import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runSmoke } from "./smoke-cases.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
if (fs.existsSync(path.join(root, ".env"))) process.loadEnvFile(path.join(root, ".env"));
const baseUrl = process.argv[2] || "http://127.0.0.1:8899";
const url = new URL(baseUrl);
if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw new Error("Use a service HTTP(S) origin without URL credentials");
if (url.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) throw new Error("Use HTTPS for a remote service");
const cases = await runSmoke((pathname, init) => fetch(new URL(pathname, url.origin), { ...init, credentials: "omit", redirect: "error", signal: AbortSignal.timeout(35000) }));
console.log(JSON.stringify({ ok: true, origin: url.origin, cases }, null, 2));
