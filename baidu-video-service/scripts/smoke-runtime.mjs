import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createApi } from "../src/api.mjs";
import { runSmoke } from "./smoke-cases.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const events = [];
const handler = createApi({ env: () => undefined, logger: event => events.push(event) });
const runtime = globalThis.Deno ? `deno-${Deno.version.deno}` : `node-${process.versions.node}`;
try {
  const cases = await runSmoke((pathname, init) => handler(new Request(`http://localhost${pathname}`, init)));
  const report = { ok: true, capturedAt: new Date().toISOString(), runtime, target: "local production handler with real Baidu requests", cases, events };
  await fs.mkdir(path.join(root, ".cache"), { recursive: true });
  await fs.writeFile(path.join(root, ".cache", `smoke-runtime-${runtime}.json`), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ ok: true, runtime, cases: cases.map(({ name, status, elapsedMs, body }) => ({ name, status, elapsedMs, hasVideo: body.hasVideo, cache: body.cache })) }, null, 2));
} finally {
  await handler.close();
}
