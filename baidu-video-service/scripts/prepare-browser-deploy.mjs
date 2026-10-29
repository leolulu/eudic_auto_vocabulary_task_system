import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { version } = JSON.parse(await fs.readFile(path.join(root, "package.json"), "utf8"));
const siteId = process.argv[2];
if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(siteId || "")) {
  throw new Error("Pass the explicitly selected Netlify site UUID. This command only prepares an upload script.");
}
const manifest = JSON.parse(await fs.readFile(path.join(root, ".netlify/edge-functions/manifest.json"), "utf8"));
const assets = [];
async function asset(file, deployPath) {
  const data = await fs.readFile(file);
  assets.push({ path: deployPath, sha1: crypto.createHash("sha1").update(data).digest("hex"), sha256: crypto.createHash("sha256").update(data).digest("hex"), content: data.toString("base64") });
}
await asset(path.join(root, "public/index.html"), "/index.html");
await asset(path.join(root, ".netlify/edge-functions/manifest.json"), "/.netlify/internal/edge-functions/manifest.json");
for (const bundle of manifest.bundles) await asset(path.join(root, ".netlify/edge-functions", bundle.asset), "/.netlify/internal/edge-functions/" + bundle.asset);
const edgeFunctions = Object.fromEntries(manifest.bundles.map(bundle => [bundle.format, assets.find(file => file.path.endsWith("/" + bundle.asset)).sha256]));
const script = `() => {
  const siteId = ${JSON.stringify(siteId)};
  const assets = ${JSON.stringify(assets)};
  const edgeFunctions = ${JSON.stringify(edgeFunctions)};
  const state = globalThis.__baiduServiceDeploy = {siteId,phase:'creating',uploads:[],startedAt:new Date().toISOString()};
  async function api(route,init={}) {
    const response=await fetch('/access-control/bb-api/api/v1'+route,{credentials:'same-origin',...init});
    const text=await response.text();let data;try{data=JSON.parse(text)}catch{data={message:'Non-JSON API response'}};
    if(!response.ok) throw new Error(response.status+' '+route+': '+String(data.message||'API error').slice(0,300));
    return data;
  }
  const decode=file=>Uint8Array.from(atob(file.content),value=>value.charCodeAt(0));
  (async()=>{
    const deploy=await api('/sites/'+siteId+'/deploys?title='+encodeURIComponent(${JSON.stringify("Baidu word video public API " + version)}),{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files:Object.fromEntries(assets.map(file=>[file.path,file.sha1])),edge_functions:edgeFunctions,draft:true,async:false})
    });
    state.deployId=deploy.id;state.url=deploy.deploy_ssl_url||deploy.deploy_url;state.phase='uploading';
    for(const file of assets.filter(file=>(deploy.required||[]).includes(file.sha1))){
      await api('/deploys/'+deploy.id+'/files'+file.path,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:decode(file)});
      state.uploads.push(file.path);
    }
    for(const sha of deploy.required_edge_functions||[]){
      const file=assets.find(file=>file.sha256===sha);if(!file) throw new Error('Required edge asset missing');
      await api('/deploys/'+deploy.id+'/edge_functions/'+sha,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:decode(file)});
      state.uploads.push('edge:'+sha);
    }
    state.phase='processing';
  })().catch(error=>{state.phase='error';state.error=error.message;});
  return {started:true,siteId,assets:assets.length,draft:true};
}`;
const output = path.join(root, ".cache", "browser-deploy");
await fs.mkdir(output, { recursive: true });
await fs.writeFile(path.join(output, "deploy.browser.js"), script);
await fs.writeFile(path.join(output, "input.json"), JSON.stringify({ siteId, manifest, files: assets.map(({ content, ...file }) => file) }, null, 2) + "\n");
console.log(JSON.stringify({ prepared: true, siteId, script: ".cache/browser-deploy/deploy.browser.js", assets: assets.length, remoteChanges: false }));
