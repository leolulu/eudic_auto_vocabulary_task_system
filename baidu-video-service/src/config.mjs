import { ServiceError } from "./errors.mjs";

function integer(env, name, fallback, min, max) {
  const raw = env(name);
  if (raw === undefined || raw === "") return fallback;
  if (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)) || Number(raw) < min || Number(raw) > max) {
    throw new ServiceError("CONFIGURATION_ERROR", "Service configuration is invalid", 503);
  }
  return Number(raw);
}

export function readConfig(env) {
  const origins = (env("BAIDU_VIDEO_ALLOWED_ORIGINS") || "*").split(",").map(value => value.trim()).filter(Boolean);
  for (const origin of origins) {
    if (origin === "*" && origins.length === 1) continue;
    let parsed;
    try { parsed = new URL(origin); } catch { /* handled below */ }
    if (!parsed || !["https:", "http:"].includes(parsed.protocol) || parsed.origin !== origin) {
      throw new ServiceError("CONFIGURATION_ERROR", "Service configuration is invalid", 503);
    }
  }
  return {
    allowedOrigins: new Set(origins),
    timeoutMs: integer(env, "BAIDU_VIDEO_TIMEOUT_MS", 20000, 1000, 30000),
    upstreamTimeoutMs: integer(env, "BAIDU_VIDEO_UPSTREAM_TIMEOUT_MS", 10000, 500, 15000),
    cacheTtlMs: integer(env, "BAIDU_VIDEO_CACHE_TTL_SECONDS", 3600, 0, 86400) * 1000,
    negativeCacheTtlMs: integer(env, "BAIDU_VIDEO_NEGATIVE_CACHE_TTL_SECONDS", 300, 0, 3600) * 1000,
    cacheMaxEntries: integer(env, "BAIDU_VIDEO_CACHE_MAX_ENTRIES", 256, 0, 2048),
    maxInflight: integer(env, "BAIDU_VIDEO_MAX_INFLIGHT", 4, 1, 16),
  };
}

export function normalizeWord(value) {
  if (typeof value !== "string" || value.length > 160) {
    throw new ServiceError("INVALID_WORD", "Provide one English word or short phrase (1-80 characters)", 400);
  }
  const word = value.trim().toLowerCase().replace(/\s+/g, " ");
  if (word.length > 80 || !/^[a-z][a-z0-9]*(?:[ '-][a-z0-9]+)*$/.test(word)) {
    throw new ServiceError("INVALID_WORD", "Provide one English word or short phrase (1-80 characters)", 400);
  }
  return word;
}
