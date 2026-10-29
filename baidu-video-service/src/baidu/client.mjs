import { ENDPOINT, USER_AGENT } from "./constants.mjs";
import { ServiceError, checkAbort, waitFor } from "../errors.mjs";

const MAX_RESPONSE_BYTES = 512 * 1024;
const failure = () => new ServiceError("FORMAT_ERROR", "Unexpected video provider response", 502);

function businessError(errno) {
  const codes = {
    995: ["ACS_REJECTED", "Video provider rejected the signature"],
    10001: ["VALIDATION_FAILED", "Video provider requires additional verification"],
    1709: ["QUOTA_EXCEEDED", "Video provider quota is exhausted"],
  };
  const [code, message] = codes[errno] || ["UPSTREAM_ERROR", "Video provider returned an error"];
  return new ServiceError(code, message, 502, { errno });
}

function httpsUrl(value, required = false) {
  if (value === undefined || value === null || value === "") {
    if (required) throw failure();
    return null;
  }
  if (typeof value !== "string" || value.length > 4096) throw failure();
  let url;
  try { url = new URL(value); } catch { throw failure(); }
  if (url.protocol !== "https:" || url.username || url.password) throw failure();
  return value;
}

function normalizeVideo(explain) {
  return {
    videoUrl: httpsUrl(explain.videoUrl, true),
    coverUrl: httpsUrl(explain.coverUrl),
    thumbUrl: httpsUrl(explain.thumbUrl),
    feedUrl: httpsUrl(explain.feedUrl),
    videoTime: Number.isFinite(explain.videoTime) && explain.videoTime >= 0 ? explain.videoTime : null,
    videoType: typeof explain.videoType === "string" || typeof explain.videoType === "number" ? explain.videoType : null,
    tag: typeof explain.tag === "string" ? explain.tag : null,
  };
}

export function parseVideoStream(text, word) {
  const payloads = [];
  for (const frame of text.replace(/^\uFEFF/, "").split(/\r?\n\r?\n/)) {
    const data = frame.split(/\r?\n/).filter(line => line.startsWith("data:"))
      .map(line => line.slice(5).replace(/^ /, "")).join("\n");
    if (!data || data === "[DONE]") continue;
    let payload;
    try { payload = JSON.parse(data); } catch { throw failure(); }
    if (!payload || typeof payload !== "object" || Array.isArray(payload) || typeof payload.errno !== "number") throw failure();
    if (payload.errno !== 0) throw businessError(payload.errno);
    if (!payload.data || typeof payload.data.event !== "string") throw failure();
    payloads.push(payload);
  }
  const events = new Set(payloads.map(payload => payload.data.event));
  // No video is a success only after a complete, recognized translation stream.
  // A truncated stream or a changed format must not enter the negative cache.
  if (!["StartTranslation", "TranslationSucceed", "Finished"].every(event => events.has(event))) throw failure();
  const explainEvent = payloads.find(payload => payload.data.event === "GetQueryExplainSucceed");
  const explain = explainEvent?.data.queryExplain;
  if (explainEvent && (explain === undefined || (explain !== null && (typeof explain !== "object" || Array.isArray(explain))))) throw failure();
  if (explain?.videoUrl !== undefined && explain.videoUrl !== null && typeof explain.videoUrl !== "string") throw failure();
  const hasVideo = Boolean(explain?.videoUrl);
  return { ok: true, word, hasVideo, video: hasVideo ? normalizeVideo(explain) : null };
}

async function readBody(response, signal) {
  const length = Number(response.headers.get("content-length"));
  if (length > MAX_RESPONSE_BYTES || !response.body) {
    response.body?.cancel().catch(() => {});
    throw failure();
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let bytes = 0;
  let text = "";
  try {
    for (;;) {
      const { done, value } = await waitFor(reader.read(), signal);
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_RESPONSE_BYTES) throw failure();
      text += decoder.decode(value, { stream: true });
    }
    return text + decoder.decode();
  } catch (error) {
    if (error instanceof TypeError && !signal.aborted) throw failure();
    throw error;
  } finally {
    reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function queryWord(word, { token, baiduId, signal, timeoutMs = 10000, fetchImpl = globalThis.fetch }) {
  checkAbort(signal);
  const controller = new AbortController();
  const relay = () => controller.abort(signal.reason);
  signal?.addEventListener("abort", relay, { once: true });
  if (signal?.aborted) relay();
  const timer = setTimeout(() => controller.abort(new ServiceError("UPSTREAM_TIMEOUT", "Video provider timed out", 504)), timeoutMs);
  const now = Date.now();
  try {
    const response = await waitFor(fetchImpl(ENDPOINT, {
      method: "POST",
      redirect: "error",
      headers: {
        "Content-Type": "application/json", "Acs-Token": token, "User-Agent": USER_AGENT,
        Origin: "https://fanyi.baidu.com", Referer: "https://fanyi.baidu.com/",
        Cookie: `BAIDUID=${baiduId}`, Accept: "text/event-stream",
      },
      body: JSON.stringify({
        needNewlineCombine: false, disableCache: false, isAi: true, sseStartTime: now,
        query: word, from: "en", to: "zh", corpusIds: [], needPhonetic: true,
        domain: "ai_advanced", detectLang: "", isIncognitoAI: true, milliTimestamp: now,
      }),
      signal: controller.signal,
    }), controller.signal);
    if (!response.ok) {
      response.body?.cancel().catch(() => {});
      throw new ServiceError("HTTP_ERROR", "Video provider returned an HTTP error", 502, { upstreamStatus: response.status });
    }
    if (!response.headers.get("content-type")?.toLowerCase().startsWith("text/event-stream")) {
      response.body?.cancel().catch(() => {});
      throw failure();
    }
    return parseVideoStream(await readBody(response, controller.signal), word);
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason;
    if (error instanceof ServiceError) throw error;
    throw new ServiceError("NETWORK_ERROR", "Could not reach video provider", 502);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", relay);
  }
}
