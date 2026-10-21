// Real Node -> Python integration. All indexes, receipts and selfies stay in a temp directory.
import {spawn, spawnSync} from 'node:child_process';
import http from 'node:http';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const data=await mkdtemp(path.join(tmpdir(),'wedding-face-review-'));
const python=path.join(root,'.venv/bin/python');
const password='fixture-only-admin-password';
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const port=async()=>{const s=http.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;};
const pyPort=await port(),nodePort=await port(),base=`http://127.0.0.1:${nodePort}`;
let py,node,token,logs='';
const env={...process.env,DATA_DIR:data,DRIVE_ENABLED:'false',ADMIN_PASSWORD:password,ADMIN_COOKIE_SECURE:'false',FACE_PIPELINE:'legacy',GROUP_MODE:'legacy',FACE_SEARCH_MODE:'vectorized'};
async function start(command,args,extra,url){
  const p=spawn(command,args,{cwd:root,env:{...env,...extra},stdio:['ignore','pipe','pipe']});
  for(const stream of [p.stdout,p.stderr])stream.on('data',b=>{logs=(logs+b).slice(-10000);});
  for(let i=0;i<200;i++){if(p.exitCode!==null)throw new Error('Service exited: '+logs);try{if((await fetch(url)).ok)return p;}catch{}await pause(50);}
  p.kill();throw new Error('Service startup timeout: '+logs);
}
async function startPython(){py=await start(python,['-m','uvicorn','app:app','--app-dir','face-service','--host','127.0.0.1','--port',String(pyPort)],{},`http://127.0.0.1:${pyPort}/health`);}
async function stop(p){if(p?.exitCode===null){p.kill();await new Promise(r=>p.once('exit',r));}}
async function request(route,body,expected=200,headers={}){
  const res=await fetch(base+'/api/admin/'+route,{method:body===undefined?'GET':'POST',headers:{'X-Admin-Request':'1',...(token?{Authorization:'Bearer '+token}:{}),...(body!==undefined?{'Content-Type':'application/json'}:{}),...headers},body:body===undefined?undefined:JSON.stringify(body)});
  const raw=await res.text();assert.equal(res.status,expected,`${route}: ${raw}`);return JSON.parse(raw);
}
const version=async()=> (await request('people')).revision;
const write=async(route,payload,expected=200)=>request(route,{revision:await version(),request_id:randomUUID(),...payload},expected);
const undo=async result=>write('face-corrections/undo',{correction_id:result.operation_id});
async function search(){const form=new FormData();form.append('file',new Blob([await readFile(path.join(data,'query.jpg'))],{type:'image/jpeg'}),'selfie.jpg');const res=await fetch(base+'/api/face/find',{method:'POST',body:form});assert.equal(res.status,200);return res.json();}
async function disk(){return JSON.parse(await readFile(path.join(data,'face_index.json'),'utf8'));}
try{
  const fixture=spawnSync(python,['tests/create_face_fixture.py',data,'--real-query'],{cwd:root,encoding:'utf8'});
  assert.equal(fixture.status,0,fixture.stderr);
  const meta=JSON.parse(await readFile(path.join(data,'fixture.json'),'utf8'));
  const original=await disk();
  await startPython();node=await start(process.execPath,['server.js'],{PORT:String(nodePort),FACE_SERVICE_URL:`http://127.0.0.1:${pyPort}`},base+'/api/health');
  for(const route of ['face-reviews','photos/'+meta.photos[0]+'/faces','faces/missing/crop'])await request(route,undefined,401);
  token=(await request('login',{password})).adminToken;
  await request('face-reviews',undefined,403,{'X-Admin-Request':'0'});
  const photo=await request('photos/'+meta.photos[0]+'/faces');
  assert.equal(photo.faces.length,2);assert.deepEqual(photo.faces[0].normalized_bbox,[20/256,30/160,70/256,90/160]);
  const crop=await fetch(base+photo.faces[0].crop_url,{headers:{'X-Admin-Request':'1',Authorization:'Bearer '+token}});
  assert.equal(crop.status,200);assert.match(crop.headers.get('content-type'),/image\/jpeg/);assert.equal(crop.headers.get('cache-control'),'no-store');assert.equal(Buffer.from(await crop.arrayBuffer()).readUInt16BE(),0xffd8);
  const catalog=await request('face-reviews?kind=duplicate&limit=1');assert.equal(catalog.total,1);assert.equal(catalog.next_offset,null);
  assert.deepEqual(await disk(),original,'GET endpoints must not migrate/rewrite legacy state');
  const beforeSearch=await search();assert.equal(beforeSearch.searchMode,'vectorized');assert(beforeSearch.matches.some(p=>p.id===meta.photos[0]));
  assert.deepEqual(await disk(),original,'Query must not persist selfie or change people');
  const different=await write(`face-reviews/${catalog.reviews[0].id}/resolve`,{action:'different'});
  assert.equal((await request('face-reviews?kind=duplicate')).total,0);
  await write('merge',{person_ids:meta.people.slice(0,2),target_id:meta.people[0]},409);
  const revoked=await write(`face-constraints/${different.constraint_id}/revoke`,{});
  assert.equal((await request('face-reviews?kind=duplicate')).total,1);
  await undo(revoked);assert.equal((await request('face-reviews?kind=duplicate')).total,0);await undo(different);
  const skipped=await write(`face-reviews/${catalog.reviews[0].id}/resolve`,{action:'skip'});
  assert.equal((await request('face-reviews?kind=duplicate')).total,0);await undo(skipped);
  const pending=(await request('face-reviews?kind=pending')).reviews[0];
  const approved=await write(`face-reviews/${pending.id}/resolve`,{action:'approve'});
  assert.equal((await request('face-reviews?kind=pending')).total,0);await undo(approved);
  const body={face_ids:[photo.faces[0].face_id],target_id:meta.people[1],revision:await version(),request_id:randomUUID()};
  const moved=await request('faces/reassign',body);
  assert.equal((await request('people')).people.some(p=>p.id===meta.people[0]),false);
  assert.deepEqual((await fetch(base+`/api/face/people/${meta.people[0]}/photos`).then(r=>r.json())).photos,[]);
  const updated=await request('photos/'+meta.photos[0]+'/faces');assert.equal(updated.faces[0].person_id,meta.people[1]);assert.equal(updated.faces[1].person_id,photo.faces[1].person_id);
  assert.deepEqual((await search()).matches,beforeSearch.matches,'Correcting groups must not filter selfie results');
  const retried=await request('faces/reassign',body);assert.equal(retried.operation_id,moved.operation_id);assert.equal(retried.replayed,true);
  await request('faces/reassign',{...body,target_id:null},409);
  await request('faces/reassign',{...body,request_id:randomUUID()},409);
  await write('undo',{merge_id:meta.old_merge_id},409);
  await undo(moved);assert.deepEqual((await request('photos/'+meta.photos[0]+'/faces')).faces,photo.faces);
  const merged=await write(`face-reviews/${catalog.reviews[0].id}/resolve`,{action:'merge',target_id:meta.people[0]});
  assert.equal((await request('people')).operations.at(-1).kind,'merge');
  await write('undo',{merge_id:merged.merge_id});
  assert.deepEqual((await disk()).merge_history,meta.old_merge_history);
  assert.deepEqual((await disk()).aliases,original.aliases);
  await stop(py);await startPython();
  assert.equal((await request('faces/reassign',body)).replayed,true,'Retry receipt survives restart and later undo');
  assert.deepEqual((await request('photos/'+meta.photos[0]+'/faces')).faces,photo.faces);
  assert.deepEqual((await disk()).merge_history,meta.old_merge_history);
  assert.deepEqual((await search()).matches,beforeSearch.matches);
  console.log('PASS: real Node/Python auth, protected JPEG, read-only legacy IDs, review decisions, exclusion/revoke, one-face correction, global undo, revisions, idempotency/restart, old merge history/aliases, real selfie search unchanged after correction.');
  if(process.argv.includes('--serve')){
    console.log(JSON.stringify({fixtureURL:base+'/admin-people.html',password,data}));
    await new Promise(resolve=>{process.once('SIGINT',resolve);process.once('SIGTERM',resolve);});
  }
}catch(error){console.error(logs);throw error;}
finally{await stop(node);await stop(py);await rm(data,{recursive:true,force:true});}
