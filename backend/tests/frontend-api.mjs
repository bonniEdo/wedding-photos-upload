import vm from 'node:vm';
import {readFile,stat} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../frontend');
const source=await readFile(path.join(root,'assets/api-client.js'),'utf8');
const calls=[];
const context={window:{WEDDING_CONFIG:{API_BASE:''}},location:{port:'5173',protocol:'http:',hostname:'localhost',href:'http://localhost:5173/admin-people.html'},URL,Headers,fetch:async(url,opts)=>{calls.push({url,opts});return {ok:true};}};
vm.runInNewContext(source,context);
const client=context.window.WeddingAPI;
assert.equal(client.base,'http://localhost:3000');assert.equal(client.url('/api/face/people'),'http://localhost:3000/api/face/people');
client.setAdminToken('random-session');await client.request('/api/admin/people',{headers:{'X-Admin-Request':'1'},credentials:'same-origin'});
assert.equal(calls[0].opts.credentials,'include');assert.equal(calls[0].opts.headers.get('Authorization'),'Bearer random-session');assert.equal(calls[0].opts.headers.get('X-Admin-Request'),'1');
await client.request('/api/face/people');assert.equal(calls[1].opts.headers.get('Authorization'),null);
client.setAdminToken(null);await client.request('/api/admin/session');assert.equal(calls[2].opts.headers.get('Authorization'),null);
context.window.WEDDING_CONFIG={API_BASE:'https://api.example.invalid/'};vm.runInNewContext(source,context);assert.equal(context.window.WeddingAPI.base,'https://api.example.invalid');
for(const name of ['photos','people','admin-people','invitation']){
 const html=await readFile(path.join(root,name+'.html'),'utf8');
 assert(!/<script>/.test(html));
 for(const [,asset] of html.matchAll(/(?:src|href)="((?:assets\/|config\.js)[^"]*)"/g))assert((await stat(path.join(root,asset))).isFile());
}
console.log('PASS: independent API base, cross-origin credentials, memory-only Bearer on admin routes, logout clears token, explicit production API URL, HTML references separated JS/CSS assets.');
