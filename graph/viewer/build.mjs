import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root=path.dirname(fileURLToPath(import.meta.url));
const bundle=async(name)=>(await build({entryPoints:[path.join(root,name)],bundle:true,write:false,
  platform:'browser',format:'iife',target:['chrome110','edge110'],minify:true,legalComments:'inline'})).outputFiles[0].text;
const [template,css,app,worker,streamLicense,dagreLicense,graphlibLicense,pythonLicense,commonLicense,lrLicense,highlightLicense]=await Promise.all([
  readFile(path.join(root,'template.html'),'utf8'),readFile(path.join(root,'style.css'),'utf8'),
  bundle('app.js'),bundle('worker.js'),
  readFile(path.join(root,'node_modules/@streamparser/json/LICENSE'),'utf8'),
  readFile(path.join(root,'node_modules/@dagrejs/dagre/LICENSE'),'utf8'),
  readFile(path.join(root,'node_modules/@dagrejs/graphlib/LICENSE'),'utf8'),
  readFile(path.join(root,'node_modules/@lezer/python/LICENSE'),'utf8'),
  readFile(path.join(root,'node_modules/@lezer/common/LICENSE'),'utf8'),
  readFile(path.join(root,'node_modules/@lezer/lr/LICENSE'),'utf8'),
  readFile(path.join(root,'node_modules/@lezer/highlight/LICENSE'),'utf8')
]);
const safeScript=s=>s.replace(/<\/script/gi,'<\\/script');
const licenses=['@streamparser/json 0.0.26',streamLicense,'@dagrejs/dagre 3.1.1',dagreLicense,
  '@dagrejs/graphlib (see package-lock.json)',graphlibLicense,'@lezer/python 1.1.19',pythonLicense,
  '@lezer/common (see package-lock.json)',commonLicense,'@lezer/lr (see package-lock.json)',lrLicense,
  '@lezer/highlight (see package-lock.json)',highlightLicense].join('\n\n');
const html=template.replace('/*__STYLE__*/',css).replace('/*__WORKER__*/',()=>safeScript(worker))
  .replace('/*__APP__*/',()=>safeScript(app))
  .replace('</body>',()=>`<script type="text/plain" id="third-party-licenses">${safeScript(licenses)}</script>\n</body>`);
await writeFile(path.join(root,'../story_graph_viewer.html'),html,'utf8');
await writeFile(path.join(root,'THIRD_PARTY_LICENSES.txt'),licenses,'utf8');
console.log(`Offline HTML: ${Buffer.byteLength(html).toLocaleString()} bytes`);
