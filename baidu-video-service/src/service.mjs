import { SerialSigner } from "./baidu/signer.mjs";
import { queryWord } from "./baidu/client.mjs";
import { ServiceError, checkAbort, waitFor } from "./errors.mjs";

export class VideoService {
  #cache = new Map();
  #inflight = new Map();

  constructor(config, { signer = new SerialSigner(), query = queryWord, now = Date.now } = {}) {
    this.config = config;
    this.signer = signer;
    this.query = query;
    this.now = now;
  }

  async lookup(word, { signal } = {}) {
    checkAbort(signal);
    const cached = this.#cache.get(word);
    if (cached && cached.expires > this.now()) {
      this.#cache.delete(word);
      this.#cache.set(word, cached);
      return { ...cached.result, cache: "hit" };
    }
    this.#cache.delete(word);
    const shared = this.#inflight.get(word);
    if (shared) return { ...await waitFor(shared, signal), cache: "coalesced" };
    if (this.#inflight.size >= this.config.maxInflight) {
      throw new ServiceError("BUSY", "Service is busy; retry shortly", 503);
    }
    const task = this.#execute(word).then(result => {
      const ttl = result.hasVideo ? this.config.cacheTtlMs : this.config.negativeCacheTtlMs;
      if (ttl > 0 && this.config.cacheMaxEntries > 0) {
        this.#cache.set(word, { result, expires: this.now() + ttl });
        while (this.#cache.size > this.config.cacheMaxEntries) this.#cache.delete(this.#cache.keys().next().value);
      }
      return result;
    }).finally(() => this.#inflight.delete(word));
    this.#inflight.set(word, task);
    // A disconnected caller does not cancel work shared by another caller.
    return { ...await waitFor(task, signal), cache: "miss" };
  }

  async #execute(word) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new ServiceError("TIMEOUT", "Video lookup timed out", 504)), this.config.timeoutMs);
    const signal = controller.signal;
    const baiduId = crypto.randomUUID().replaceAll("-", "").toUpperCase() + ":FG=1";
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        checkAbort(signal);
        const token = await waitFor(this.signer.getToken(signal), signal);
        checkAbort(signal);
        try {
          return await waitFor(this.query(word, {
            token, baiduId, signal, timeoutMs: this.config.upstreamTimeoutMs,
          }), signal);
        } catch (error) {
          if (attempt === 0 && error instanceof ServiceError && error.code === "ACS_REJECTED") continue;
          throw error;
        }
      }
    } finally {
      clearTimeout(timer);
    }
  }

  async close() {
    await Promise.allSettled(this.#inflight.values());
    await this.signer.close?.();
    this.#cache.clear();
  }
}
