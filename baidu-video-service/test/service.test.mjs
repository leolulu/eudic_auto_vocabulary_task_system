import test from "node:test";
import assert from "node:assert/strict";
import { VideoService } from "../src/service.mjs";
import { SerialSigner } from "../src/baidu/signer.mjs";
import { readConfig } from "../src/config.mjs";
import { ServiceError } from "../src/errors.mjs";

const config = readConfig(() => undefined);
const signer = { getToken: async () => "token" };
const result = (word, hasVideo = false) => ({ ok: true, word, hasVideo, video: hasVideo ? { videoUrl: "https://example.com/video.mp4" } : null });
function gate() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

test("simultaneous requests for the same word share one upstream lookup", async () => {
  const release = gate();
  let calls = 0;
  const service = new VideoService(config, { signer, query: async word => { calls++; await release.promise; return result(word); } });
  const first = service.lookup("apricot");
  const second = service.lookup("apricot");
  release.resolve();
  const [a, b] = await Promise.all([first, second]);
  assert.equal(calls, 1);
  assert.equal(a.cache, "miss");
  assert.equal(b.cache, "coalesced");
  assert.equal((await service.lookup("apricot")).cache, "hit");
});

test("caller cancellation leaves a shared lookup usable by another caller", async () => {
  const release = gate();
  const controller = new AbortController();
  const service = new VideoService(config, { signer, query: async word => { await release.promise; return result(word); } });
  const first = service.lookup("word", { signal: controller.signal });
  const second = service.lookup("word");
  controller.abort();
  await assert.rejects(first, { code: "REQUEST_ABORTED" });
  release.resolve();
  assert.equal((await second).ok, true);
});

test("positive and negative cache lifetimes differ and LRU storage stays bounded", async () => {
  let time = 0;
  let calls = 0;
  const service = new VideoService({ ...config, cacheMaxEntries: 2, cacheTtlMs: 100, negativeCacheTtlMs: 10 }, {
    signer, now: () => time, query: async word => { calls++; return result(word, word === "evidence"); },
  });
  await service.lookup("evidence");
  await service.lookup("apricot");
  time = 11;
  assert.equal((await service.lookup("evidence")).cache, "hit");
  assert.equal((await service.lookup("apricot")).cache, "miss");
  await service.lookup("feature");
  assert.equal((await service.lookup("evidence")).cache, "miss");
  assert.equal(calls, 5);
});

test("invalid upstream responses are never cached", async () => {
  let calls = 0;
  const service = new VideoService(config, { signer, query: async word => { if (++calls === 1) throw new ServiceError("FORMAT_ERROR", "Invalid response"); return result(word); } });
  await assert.rejects(service.lookup("word"), { code: "FORMAT_ERROR" });
  assert.equal((await service.lookup("word")).cache, "miss");
  assert.equal(calls, 2);
});

test("busy limit rejects distinct work while allowing an existing lookup to be joined", async () => {
  const release = gate();
  const service = new VideoService({ ...config, maxInflight: 1 }, { signer, query: async word => { await release.promise; return result(word); } });
  const first = service.lookup("one");
  const shared = service.lookup("one");
  await assert.rejects(service.lookup("two"), { code: "BUSY", status: 503 });
  release.resolve();
  await Promise.all([first, shared]);
});

test("overall timeout covers signer wait and releases the capacity slot", async () => {
  let calls = 0;
  let signal;
  const service = new VideoService({ ...config, timeoutMs: 20, maxInflight: 1 }, {
    signer: { getToken: async value => { signal = value; if (++calls === 1) return new Promise(() => {}); return "token"; } },
    query: async word => result(word),
  });
  await assert.rejects(service.lookup("one"), { code: "TIMEOUT", status: 504 });
  assert.equal(signal.aborted, true);
  assert.equal((await service.lookup("two")).ok, true);
});

test("only a rejected signature gets one retry, retaining the same anonymous ID", async () => {
  const attempts = [];
  let tokens = 0;
  const service = new VideoService(config, { signer: { getToken: async () => `token-${++tokens}` }, query: async (word, options) => {
    attempts.push(options);
    if (attempts.length === 1) throw new ServiceError("ACS_REJECTED", "Rejected", 502);
    return result(word);
  } });
  await service.lookup("word");
  assert.equal(attempts.length, 2);
  assert.notEqual(attempts[0].token, attempts[1].token);
  assert.equal(attempts[0].baiduId, attempts[1].baiduId);
  for (const code of ["QUOTA_EXCEEDED", "NETWORK_ERROR", "FORMAT_ERROR"]) {
    let calls = 0;
    const failing = new VideoService(config, { signer, query: async () => { calls++; throw new ServiceError(code, "Failed"); } });
    await assert.rejects(failing.lookup("word"), { code });
    assert.equal(calls, 1);
  }
});

test("SDK initialization is single-flight, token calls are serial, and failure recreates runtime", async () => {
  let loads = 0;
  let created = 0;
  let active = 0;
  let peak = 0;
  let closed = 0;
  let calls = 0;
  const serial = new SerialSigner(async () => {
    loads++;
    return { AcsRuntime: class {
      constructor() { created++; }
      async getToken() {
        active++; peak = Math.max(peak, active);
        await new Promise(resolve => setTimeout(resolve, 5));
        active--;
        if (++calls === 3) throw new Error("sensitive signer internals");
        return "token";
      }
      close() { closed++; }
    } };
  });
  assert.deepEqual(await Promise.all([serial.getToken(), serial.getToken()]), ["token", "token"]);
  assert.equal(loads, 1);
  assert.equal(peak, 1);
  await assert.rejects(serial.getToken(), { code: "SIGNER_ERROR", message: "Video signer failed" });
  assert.equal(await serial.getToken(), "token");
  assert.equal(created, 2);
  await serial.close();
  assert.equal(closed, 2);
});

test("a cancelled signer queue entry does not block later work", async () => {
  const release = gate();
  let calls = 0;
  const serial = new SerialSigner(async () => ({ AcsRuntime: class {
    async getToken() { if (++calls === 1) await release.promise; return "token"; }
    close() {}
  } }));
  const first = serial.getToken();
  const controller = new AbortController();
  const second = serial.getToken(controller.signal);
  controller.abort();
  await assert.rejects(second, { code: "REQUEST_ABORTED" });
  release.resolve();
  assert.equal(await first, "token");
  assert.equal(await serial.getToken(), "token");
  assert.equal(calls, 2);
  await serial.close();
});
