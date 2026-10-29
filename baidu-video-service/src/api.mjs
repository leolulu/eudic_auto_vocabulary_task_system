import { readConfig, normalizeWord } from "./config.mjs";
import { ServiceError } from "./errors.mjs";
import { VideoService } from "./service.mjs";

export const VERSION = "1.1.0";

export function createApi({ env, createService = config => new VideoService(config), logger = event => console.log(JSON.stringify(event)) }) {
  let config;
  let service;

  const handler = async (request, context = {}) => {
    const started = performance.now();
    const requestId = context.requestId || crypto.randomUUID();
    let word = null;
    const headers = new Headers({
      "Cache-Control": "no-store", "CDN-Cache-Control": "no-store",
      "Netlify-CDN-Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
      "X-Request-Id": requestId, Vary: "Origin",
    });
    let result;
    let status = 200;
    try {
      const url = new URL(request.url);
      if (!["/healthz", "/v1/video"].includes(url.pathname)) throw new ServiceError("NOT_FOUND", "Endpoint not found", 404);
      config ||= readConfig(env);
      const origin = request.headers.get("origin");
      const publicCors = config.allowedOrigins.has("*");
      if (origin && !publicCors && origin !== url.origin && !config.allowedOrigins.has(origin)) {
        throw new ServiceError("ORIGIN_NOT_ALLOWED", "Browser origin is not allowed", 403);
      }
      if (publicCors || origin) {
        headers.set("Access-Control-Allow-Origin", publicCors ? "*" : origin);
        headers.set("Access-Control-Expose-Headers", "X-Request-Id");
      }
      if (request.method === "OPTIONS") {
        if (!origin || request.headers.get("access-control-request-method") !== "GET") {
          throw new ServiceError("INVALID_PREFLIGHT", "Unsupported preflight request", 400);
        }
        headers.set("Access-Control-Allow-Methods", "GET");
        headers.set("Access-Control-Allow-Headers", "*, Authorization");
        headers.set("Access-Control-Max-Age", "600");
        return new Response(null, { status: 204, headers });
      }
      if (request.method !== "GET") {
        headers.set("Allow", "GET, OPTIONS");
        throw new ServiceError("METHOD_NOT_ALLOWED", "Use GET for this endpoint", 405);
      }
      if (url.pathname === "/healthz") {
        result = { ok: true, service: "baidu-word-video", version: VERSION, configured: true, requestId };
      } else {
        if (url.searchParams.getAll("word").length !== 1 || [...url.searchParams.keys()].some(name => name !== "word")) {
          throw new ServiceError("INVALID_WORD", "Provide exactly one word parameter", 400);
        }
        word = normalizeWord(url.searchParams.get("word"));
        service ||= createService(config);
        result = { ...await service.lookup(word, { signal: request.signal }), failure: null, errno: null, errmsg: null, requestId };
      }
    } catch (error) {
      const failure = error instanceof ServiceError ? error : new ServiceError("INTERNAL_ERROR", "Video service failed", 500);
      status = failure.status;
      if (failure.code === "BUSY") headers.set("Retry-After", "1");
      if (failure.code === "QUOTA_EXCEEDED") headers.set("Retry-After", "60");
      result = {
        ok: false, word, hasVideo: false, video: null, failure: failure.code,
        errno: failure.errno, errmsg: failure.message, requestId,
      };
      if (failure.upstreamStatus !== null) result.upstreamStatus = failure.upstreamStatus;
    }
    logger({
      event: "baidu_word_video", requestId, status, failure: result.failure || null,
      cache: result.cache || null, elapsedMs: Math.round(performance.now() - started),
    });
    return Response.json(result, { status, headers });
  };
  handler.close = async () => service?.close?.();
  return handler;
}
