import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { once } from "node:events";
import { runSmoke } from "./smoke-cases.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const child = spawn(process.execPath, ["scripts/dev.mjs"], {
  cwd: root, env: { ...process.env, PORT: "0" }, stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
});
const exited = once(child, "exit");
child.stderr.on("data", chunk => process.stderr.write(chunk));
try {
  const origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Local API did not start")), 10000);
    let text = "";
    child.once("error", error => { clearTimeout(timer); reject(error); });
    child.once("exit", () => { clearTimeout(timer); reject(new Error("Local API exited before startup")); });
    child.stdout.on("data", chunk => {
      text += chunk.toString();
      const match = text.match(/Local API: (http:\/\/127\.0\.0\.1:\d+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
  });
  const cases = await runSmoke((pathname, init) => fetch(new URL(pathname, origin), { ...init, credentials: "omit", signal: AbortSignal.timeout(35000) }));
  console.log(JSON.stringify({ ok: true, target: "temporary localhost HTTP server", cases: cases.map(({ name, status, elapsedMs, body }) => ({ name, status, elapsedMs, hasVideo: body.hasVideo, cache: body.cache })) }, null, 2));
} finally {
  child.kill();
  await exited;
}
