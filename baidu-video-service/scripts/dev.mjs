import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createApi } from "../src/api.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
if (fs.existsSync(path.join(root, ".env"))) process.loadEnvFile(path.join(root, ".env"));
const handler = createApi({ env: name => process.env[name] });
const port = Number(process.env.PORT ?? 8899);
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error("Invalid PORT");
const server = http.createServer(async (incoming, outgoing) => {
  const controller = new AbortController();
  outgoing.on("close", () => { if (!outgoing.writableEnded) controller.abort(); });
  try {
    const request = new Request(new URL(incoming.url, `http://127.0.0.1:${port}`), {
      method: incoming.method, headers: incoming.headers, signal: controller.signal,
    });
    const response = await handler(request);
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    if (!outgoing.headersSent) outgoing.writeHead(500, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    outgoing.end('{"ok":false,"failure":"INTERNAL_ERROR"}');
  }
});
server.listen(port, "127.0.0.1", () => console.log(`Local API: http://127.0.0.1:${server.address().port}`));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => {
  server.close(async () => { await handler.close(); process.exit(0); });
});
