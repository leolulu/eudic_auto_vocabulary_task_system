import vm from "node:vm";
import { ACS_SOURCE, ABCLITE_SOURCE } from "./vendor-sdk.mjs";
import { createSandbox } from "./sandbox.mjs";
import { USER_AGENT } from "./constants.mjs";
import { ServiceError, abortReason, checkAbort, sleep } from "../errors.mjs";

export class AcsRuntime {
  #environment;
  #sdk;
  #ready;

  async init(signal) {
    checkAbort(signal);
    if (!this.#ready) {
      this.#ready = this.#load(signal).catch(error => {
        this.close();
        throw error;
      });
    }
    return this.#ready;
  }

  async #load(signal) {
    this.#environment = createSandbox({ userAgent: USER_AGENT });
    const { sandbox } = this.#environment;
    vm.createContext(sandbox);
    vm.runInContext(ABCLITE_SOURCE, sandbox, { filename: "abclite-2060-s.js" });
    vm.runInContext(ACS_SOURCE, sandbox, { filename: "acs-2060.js" });
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      checkAbort(signal);
      for (const key of Object.getOwnPropertyNames(sandbox)) {
        const value = sandbox[key];
        if (value && typeof value === "object" && typeof value.gs === "function") {
          this.#sdk = value;
          return;
        }
      }
      await sleep(25, signal);
    }
    throw new ServiceError("SIGNER_ERROR", "Video signer could not initialize", 502);
  }

  async getToken(signal) {
    await this.init(signal);
    checkAbort(signal);
    return new Promise((resolve, reject) => {
      let settled = false;
      const done = (error, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
        if (error) reject(error);
        else if (typeof value === "string" && value.length > 0) resolve(value);
        else reject(new ServiceError("SIGNER_ERROR", "Video signer returned an invalid token", 502));
      };
      const onAbort = () => done(abortReason(signal));
      const timer = setTimeout(() => done(new ServiceError("SIGNER_TIMEOUT", "Video signer timed out", 504)), 5000);
      signal?.addEventListener("abort", onAbort, { once: true });
      if (signal?.aborted) return onAbort();
      try {
        this.#sdk.gs((first, second) => done(null, second === undefined ? first : second));
      } catch {
        done(new ServiceError("SIGNER_ERROR", "Video signer failed", 502));
      }
    });
  }

  close() {
    this.#environment?.close();
    this.#environment = null;
    this.#sdk = null;
    this.#ready = null;
  }
}
