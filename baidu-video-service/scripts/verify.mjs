import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath, pathToFileURL } from "node:url";
import assert from "node:assert/strict";
import { ACS_SOURCE, ABCLITE_SOURCE } from "../src/baidu/vendor-sdk.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const hash = value => createHash("sha256").update(value).digest("hex");

export async function verify() {
  const integrity = JSON.parse(await fs.readFile(path.join(root, "docs/sdk-integrity.json"), "utf8"));
  for (const [name, value] of Object.entries({ ACS: ACS_SOURCE, ABCLITE: ABCLITE_SOURCE })) {
    assert.equal(hash(value), integrity.sdk[name].sha256, `${name} SDK checksum`);
    assert.equal(Buffer.byteLength(value), integrity.sdk[name].bytes, `${name} SDK size`);
  }
  for (const [file, expected] of Object.entries(integrity.files)) {
    const content = await fs.readFile(path.join(root, file));
    assert.equal(hash(content), expected.sha256, `${file} checksum`);
  }
  // Check the actual source dependency graph: all local imports stay in this project.
  let modules = 0;
  async function visit(folder) {
    for (const entry of await fs.readdir(folder, { withFileTypes: true })) {
      const absolute = path.join(folder, entry.name);
      if (entry.isDirectory()) { await visit(absolute); continue; }
      if (!/\.m?js$/.test(entry.name)) continue;
      modules++;
      const code = await fs.readFile(absolute, "utf8");
      if (entry.name === "vendor-sdk.mjs") continue; // frozen source strings contain SDK-internal text
      for (const match of code.matchAll(/(?:\bfrom\s*|\bimport\s*\()(["'])([^"']+)\1/g)) {
        const specifier = match[2];
        if (specifier.startsWith("node:")) continue;
        assert(specifier.startsWith("."), `Unexpected runtime dependency: ${specifier}`);
        const target = path.resolve(path.dirname(absolute), specifier);
        const relative = path.relative(root, target);
        assert(relative !== ".." && !relative.startsWith(".." + path.sep) && !path.isAbsolute(relative), `Dependency outside service: ${specifier}`);
        await fs.access(target);
      }
    }
  }
  await visit(path.join(root, "src"));
  await visit(path.join(root, "netlify"));
  assert.deepEqual((await fs.readdir(path.join(root, "public"))).sort(), ["index.html"]);
  console.log(JSON.stringify({ ok: true, check: "source-integrity-and-local-imports", modules }));
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) await verify();
