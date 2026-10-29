export class ServiceError extends Error {
  constructor(code, message, status = 502, details = {}) {
    super(message);
    this.name = "ServiceError";
    this.code = code;
    this.status = status;
    this.errno = details.errno ?? null;
    this.upstreamStatus = details.upstreamStatus ?? null;
  }
}

export function abortReason(signal) {
  return signal?.reason instanceof ServiceError
    ? signal.reason
    : new ServiceError("REQUEST_ABORTED", "Request was cancelled", 499);
}

export function checkAbort(signal) {
  if (signal?.aborted) throw abortReason(signal);
}

// Observe both completion paths, including operations that finish after cancellation.
export function waitFor(promise, signal) {
  if (!signal) return Promise.resolve(promise);
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(abortReason(signal));
    signal.addEventListener("abort", onAbort, { once: true });
    Promise.resolve(promise).then(resolve, reject).finally(() => {
      signal.removeEventListener("abort", onAbort);
    });
    if (signal.aborted) onAbort();
  });
}

export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const finish = (error) => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      if (error) reject(error);
      else resolve();
    };
    const onAbort = () => finish(abortReason(signal));
    const timer = setTimeout(() => finish(), ms);
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) onAbort();
  });
}
