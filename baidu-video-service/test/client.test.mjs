import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { parseVideoStream, queryWord } from "../src/baidu/client.mjs";
import { ServiceError } from "../src/errors.mjs";

const video = await fs.readFile(new URL("fixtures/video.sse", import.meta.url), "utf8");
const noVideo = await fs.readFile(new URL("fixtures/no-video.sse", import.meta.url), "utf8");
const options = { token: "private-token", baiduId: "anonymous:FG=1" };
const response = (body, init = {}) => new Response(body, { headers: { "Content-Type": "text/event-stream" }, ...init });

test("complete provider streams distinguish video and no-video", () => {
  assert.equal(parseVideoStream(video, "evidence").video.videoTime, 77);
  assert.deepEqual(parseVideoStream(noVideo, "apricot"), { ok: true, word: "apricot", hasVideo: false, video: null });
});

test("SSE parser accepts CRLF, comments and multi-line data", () => {
  const multi = noVideo.replace('data: {"errno":0,"data":{"event":"Finished"}}', 'data: {"errno":0,\ndata: "data":{"event":"Finished"}}');
  assert.equal(parseVideoStream(multi.replaceAll("\n", "\r\n"), "word").ok, true);
});

test("malformed, truncated, unknown and invalid metadata responses are errors", () => {
  for (const text of ["<html>blocked</html>", "data: invalid\n\n", noVideo.replace('data: {"errno":0,"data":{"event":"Finished"}}', ""), 'data: {"errno":0,"data":{"event":"NewFormat"}}\n\n', video.replace('"videoUrl":"https:', '"videoUrl":"http:'), video.replace('"videoUrl":"https://fanyiapp.cdn.bcebos.com/app/video/query/E204-evidence.mp4"', '"videoUrl":123')]) {
    assert.throws(() => parseVideoStream(text, "word"), { code: "FORMAT_ERROR" });
  }
});

test("provider business errors are classified without returning raw messages", () => {
  for (const [errno, code] of [[995, "ACS_REJECTED"], [10001, "VALIDATION_FAILED"], [1709, "QUOTA_EXCEEDED"], [12345, "UPSTREAM_ERROR"]]) {
    assert.throws(() => parseVideoStream(`data: {"errno":${errno},"errmsg":"private upstream message"}\n\n`, "word"), error => error.code === code && error.errno === errno && !error.message.includes("private"));
  }
});

test("client calls only the fixed provider endpoint and sends an abort signal", async () => {
  const result = await queryWord("evidence", { ...options, fetchImpl: async (url, init) => {
    assert.equal(url, "https://fanyi.baidu.com/ait/text/translateIncognitoAi");
    assert.equal(init.redirect, "error");
    assert.equal(init.headers["Acs-Token"], options.token);
    assert.equal(JSON.parse(init.body).query, "evidence");
    assert(init.signal instanceof AbortSignal);
    return response(video);
  } });
  assert.equal(result.hasVideo, true);
});

test("HTTP and content-type failures do not become no-video", async () => {
  await assert.rejects(queryWord("word", { ...options, fetchImpl: async () => response("blocked", { status: 403 }) }), { code: "HTTP_ERROR", upstreamStatus: 403 });
  await assert.rejects(queryWord("word", { ...options, fetchImpl: async () => new Response(noVideo) }), { code: "FORMAT_ERROR" });
});

test("stream byte limit cancels an oversized response", async () => {
  let cancelled = false;
  const stream = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(512 * 1024 + 1)); }, cancel() { cancelled = true; } });
  await assert.rejects(queryWord("word", { ...options, fetchImpl: async () => response(stream) }), { code: "FORMAT_ERROR" });
  assert.equal(cancelled, true);
});

test("upstream timeout covers a response body that never finishes", async () => {
  let cancelled = false;
  const stream = new ReadableStream({ cancel() { cancelled = true; } });
  await assert.rejects(queryWord("word", { ...options, timeoutMs: 20, fetchImpl: async () => response(stream) }), { code: "UPSTREAM_TIMEOUT", status: 504 });
  assert.equal(cancelled, true);
});

test("overall cancellation reaches fetch and retains its error code", async () => {
  const controller = new AbortController();
  let observedSignal;
  const pending = queryWord("word", { ...options, signal: controller.signal, fetchImpl: async (_url, init) => {
    observedSignal = init.signal;
    return new Promise(() => {});
  } });
  controller.abort(new ServiceError("TIMEOUT", "Lookup timed out", 504));
  await assert.rejects(pending, { code: "TIMEOUT" });
  assert.equal(observedSignal.aborted, true);
});
