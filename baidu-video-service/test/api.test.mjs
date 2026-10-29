import test from "node:test";
import assert from "node:assert/strict";
import { createApi } from "../src/api.mjs";
import { readConfig, normalizeWord } from "../src/config.mjs";
import { ServiceError } from "../src/errors.mjs";

const request = (path, init) => new Request(`https://service.example${path}`, init);
function api(overrides = {}) {
  let created = 0;
  const logs = [];
  const handler = createApi({
    env: () => undefined,
    logger: event => logs.push(event),
    createService: () => { created++; return { lookup: async word => ({ ok: true, word, hasVideo: false, video: null, cache: "miss" }) }; },
    ...overrides,
  });
  return { handler, logs, created: () => created };
}

test("health checks work without environment settings and do not initialize the signer", async () => {
  const instance = api();
  const response = await instance.handler(request("/healthz"));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).configured, true);
  assert.equal(instance.created(), 0);
});

test("public queries require no credentials and ignore obsolete authorization headers", async () => {
  const instance = api({ env: name => { assert.notEqual(name, "BAIDU_VIDEO_API_KEY"); return undefined; } });
  for (const headers of [{}, { Authorization: "Bearer obsolete-value" }, { Authorization: "Basic ignored" }]) {
    const response = await instance.handler(request("/v1/video?word=evidence", { headers }));
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("www-authenticate"), null);
    assert.equal((await response.json()).ok, true);
  }
  assert.equal(instance.created(), 1);
});

test("invalid optional configuration is still rejected", async () => {
  const instance = api({ env: name => name === "BAIDU_VIDEO_TIMEOUT_MS" ? "40000" : undefined });
  assert.equal((await instance.handler(request("/healthz"))).status, 503);
  for (const [name, value] of [["BAIDU_VIDEO_TIMEOUT_MS", "40000"], ["BAIDU_VIDEO_MAX_INFLIGHT", "0"], ["BAIDU_VIDEO_ALLOWED_ORIGINS", "*,https://client.example"], ["BAIDU_VIDEO_ALLOWED_ORIGINS", "invalid"], ["BAIDU_VIDEO_CACHE_TTL_SECONDS", "NaN"]]) {
    assert.throws(() => readConfig(field => field === name ? value : undefined), { code: "CONFIGURATION_ERROR" });
  }
});

test("parameter validation and normalization are consistent", async () => {
  const instance = api();
  for (const path of ["/v1/video", "/v1/video?word=one&word=two", "/v1/video?word=one&url=https://example.com", "/v1/video?word=%E4%BD%A0%E5%A5%BD", "/v1/video?word=https://example.com"]) {
    assert.equal((await instance.handler(request(path))).status, 400);
  }
  const response = await instance.handler(request("/v1/video?word=%20Evidence%20"));
  assert.equal((await response.json()).word, "evidence");
  assert.equal(normalizeWord("  Ice   cream "), "ice cream");
  assert.equal(normalizeWord("can't"), "can't");
  assert.equal(normalizeWord("covid-19"), "covid-19");
  assert.throws(() => normalizeWord("a".repeat(81)), { code: "INVALID_WORD" });
});

test("methods and unknown API routes have explicit responses", async () => {
  const instance = api();
  const response = await instance.handler(request("/v1/video?word=one", { method: "POST" }));
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("allow"), "GET, OPTIONS");
  assert.equal((await instance.handler(request("/v1/unknown"))).status, 404);
});

test("public responses prohibit CDN storage and preserve request IDs", async () => {
  const instance = api();
  for (let i = 0; i < 2; i++) {
    const response = await instance.handler(request("/v1/video?word=word"), { requestId: "provider-request-id" });
    assert.equal(response.status, 200);
    for (const header of ["Cache-Control", "CDN-Cache-Control", "Netlify-CDN-Cache-Control"]) assert.equal(response.headers.get(header), "no-store");
    assert.equal(response.headers.get("X-Request-Id"), "provider-request-id");
  }
  assert.equal(instance.created(), 1);
  assert(!JSON.stringify(instance.logs).includes('"word"'));
});

test("public CORS allows other websites and GET preflights without credentials", async () => {
  const instance = api();
  const response = await instance.handler(request("/v1/video?word=word", { headers: { Origin: "https://public.example" } }));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), "*");
  assert.equal(response.headers.get("Access-Control-Allow-Credentials"), null);
  const preflight = await instance.handler(request("/v1/video", { method: "OPTIONS", headers: {
    Origin: "https://public.example", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-client",
  } }));
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get("Access-Control-Allow-Origin"), "*");
  assert(preflight.headers.get("Access-Control-Allow-Headers").includes("*"));
  assert.equal((await instance.handler(request("/v1/video", { method: "OPTIONS", headers: { Origin: "https://public.example", "Access-Control-Request-Method": "POST" } }))).status, 400);
});

test("an explicitly configured CORS allowlist is still respected", async () => {
  const instance = api({ env: name => name === "BAIDU_VIDEO_ALLOWED_ORIGINS" ? "https://client.example" : undefined });
  const response = await instance.handler(request("/v1/video?word=word", { headers: { Origin: "https://client.example" } }));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), "https://client.example");
  assert.equal((await instance.handler(request("/v1/video?word=word", { headers: { Origin: "https://untrusted.example" } }))).status, 403);
});

test("provider failures remain different from successful no-video responses", async () => {
  const instance = api({ createService: () => ({ lookup: async () => { throw new ServiceError("QUOTA_EXCEEDED", "Video provider quota is exhausted", 502, { errno: 1709 }); } }) });
  const response = await instance.handler(request("/v1/video?word=word"));
  const data = await response.json();
  assert.equal(response.status, 502);
  assert.equal(data.ok, false);
  assert.equal(data.errno, 1709);
  assert.equal(response.headers.get("Retry-After"), "60");
});

test("unexpected exceptions do not leak implementation details", async () => {
  const instance = api({ createService: () => ({ lookup: async () => { throw new Error("private internals and stack"); } }) });
  const response = await instance.handler(request("/v1/video?word=word"));
  const text = await response.text();
  assert.equal(response.status, 500);
  assert(!text.includes("private"));
  assert(!JSON.stringify(instance.logs).includes("private"));
});
