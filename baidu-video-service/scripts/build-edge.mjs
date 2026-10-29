import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cache = path.join(root, ".cache");
await fs.mkdir(path.join(cache, "tmp"), { recursive: true });
for (const name of ["TMP", "TEMP", "TMPDIR"]) process.env[name] = path.join(cache, "tmp");
const envDirs = {
  DENO_DIR: "deno-dir", APPDATA: "appdata", LOCALAPPDATA: "localappdata",
  XDG_CACHE_HOME: "xdg", XDG_CONFIG_HOME: "config", XDG_DATA_HOME: "data", XDG_STATE_HOME: "state",
  npm_config_cache: "npm-cache", COREPACK_HOME: "corepack", PNPM_HOME: "pnpm-home",
};
for (const [name, folder] of Object.entries(envDirs)) process.env[name] = path.join(cache, folder);
process.env.DENO_NO_UPDATE_CHECK = "1";
process.env.PATH = [path.join(cache, "deno-cli"), process.env.PATH].join(path.delimiter);

const { verify } = await import("./verify.mjs");
await verify();
const { bundle } = await import("@netlify/edge-bundler");
const dist = path.join(root, ".netlify", "edge-functions");
await fs.mkdir(dist, { recursive: true });
const result = await bundle([path.join(root, "netlify", "edge-functions")], dist, [], {
  basePath: root, rootPath: root, cacheDirectory: path.join(cache, "deno-cli"),
  systemLogger: (...args) => console.error(...args),
  userLogger: (...args) => console.error(...args),
});
console.log(JSON.stringify({ manifest: result.manifest }, null, 2));
