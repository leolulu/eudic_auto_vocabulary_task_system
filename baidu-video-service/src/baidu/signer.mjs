import { ServiceError, checkAbort, waitFor } from "../errors.mjs";

// Only SDK access is serialized. Independent upstream requests can overlap.
export class SerialSigner {
  #runtime;
  #tail = Promise.resolve();

  constructor(loadRuntime = () => import("./runtime.mjs")) {
    this.loadRuntime = loadRuntime;
  }

  getToken(signal) {
    const task = this.#tail.then(async () => {
      checkAbort(signal);
      try {
        if (!this.#runtime) {
          const { AcsRuntime } = await this.loadRuntime();
          checkAbort(signal);
          this.#runtime = new AcsRuntime();
        }
        return await this.#runtime.getToken(signal);
      } catch (error) {
        this.#runtime?.close();
        this.#runtime = null;
        if (error instanceof ServiceError) throw error;
        throw new ServiceError("SIGNER_ERROR", "Video signer failed", 502);
      }
    });
    this.#tail = task.catch(() => {});
    return waitFor(task, signal);
  }

  async close() {
    await this.#tail;
    this.#runtime?.close();
    this.#runtime = null;
  }
}
