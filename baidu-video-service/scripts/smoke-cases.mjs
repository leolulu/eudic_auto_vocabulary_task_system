import assert from "node:assert/strict";

export async function runSmoke(send) {
  const cases = [];
  const check = async (name, pathname, init, status) => {
    const started = performance.now();
    const response = await send(pathname, init);
    const text = await response.text();
    let body;
    try { body = JSON.parse(text); } catch { throw new Error(`${name}: expected JSON; received HTTP ${response.status} (check Netlify access protection)`); }
    cases.push({ name, status: response.status, elapsedMs: Math.round(performance.now() - started), body });
    assert.equal(response.status, status, `${name}: ${body.failure || "unexpected status"}`);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert(response.headers.get("x-request-id"));
    return body;
  };
  await check("health", "/healthz", {}, 200);
  await check("missing-word", "/v1/video", {}, 400);
  await check("wrong-method", "/v1/video?word=evidence", { method: "POST" }, 405);
  const [evidence, shared, apricot, feature] = await Promise.all([
    check("evidence", "/v1/video?word=evidence", {}, 200),
    check("same-word-concurrent", "/v1/video?word=evidence", {}, 200),
    check("apricot", "/v1/video?word=apricot", {}, 200),
    check("feature", "/v1/video?word=feature", {}, 200),
  ]);
  assert.equal(evidence.ok, true);
  assert.equal(evidence.hasVideo, true, "The reference word no longer has a video; inspect the provider result");
  assert(new URL(evidence.video.videoUrl).protocol === "https:");
  assert.equal(shared.video.videoUrl, evidence.video.videoUrl);
  for (const item of [apricot, feature]) { assert.equal(item.ok, true); assert.equal(typeof item.hasVideo, "boolean"); }
  const cors = await send("/v1/video?word=evidence", { headers: { Origin: "https://public-client.example" } });
  assert.equal(cors.status, 200);
  assert.equal(cors.headers.get("access-control-allow-origin"), "*");
  cases.push({ name: "public-cors", status: cors.status, body: await cors.json() });
  const cached = await check("cache-hit", "/v1/video?word=evidence", {}, 200);
  assert.equal(cached.cache, "hit", "Smoke expects the default in-memory cache to be enabled");
  return cases;
}
