// Full P5 lifecycle using real local models, isolated data and a real Node -> Python source stream.
import {spawn,spawnSync} from 'node:child_process';
import http from 'node:http';
import {mkdtemp,readFile,rm,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const home=await mkdtemp(path.join(tmpdir(),'wedding-backfill-api-')),data=path.join(home,'data');
const python=path.join(root,'.venv/bin/python'),password='fixture-only-backfill-password',photoId='4'.repeat(64);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const port=async()=>{const server=http.createServer();await new Promise(r=>server.listen(0,'127.0.0.1',r));const n=server.address().port;await new Promise(r=>server.close(r));return n;};
const pyPort=await port(),nodePort=await port(),base=`http://127.0.0.1:${nodePort}`;
let py,node,token,logs='';
const env={...process.env,DATA_DIR:data,DRIVE_ENABLED:'false',ADMIN_PASSWORD:password,ADMIN_COOKIE_SECURE:'false',FACE_PIPELINE:'legacy',GROUP_MODE:'legacy',FACE_SEARCH_MODE:'vectorized',FACE_BACKFILL_SOURCE_URL:base};
function run(args){const result=spawnSync(python,args,{cwd:root,env,encoding:'utf8'});assert.equal(result.status,0,result.stderr+'\n'+result.stdout);return result.stdout;}
async function start(command,args,extra,url){
  const child=spawn(command,args,{cwd:root,env:{...env,...extra},stdio:['ignore','pipe','pipe']});
  for(const stream of [child.stdout,child.stderr])stream.on('data',b=>{logs=(logs+b).slice(-12000);});
  for(let i=0;i<200;i++){if(child.exitCode!==null)throw new Error('Service exited: '+logs);try{if((await fetch(url)).ok)return child;}catch{}await sleep(50);}
  child.kill();throw new Error('Service startup timeout: '+logs);
}
async function stop(child){if(child?.exitCode===null){child.kill();await new Promise(r=>child.once('exit',r));}}
async function startPython(){py=await start(python,['-m','uvicorn','app:app','--app-dir','face-service','--host','127.0.0.1','--port',String(pyPort)],{},`http://127.0.0.1:${pyPort}/health`);}
async function request(route,body,expected=200,headers={}){
  const res=await fetch(base+'/api/admin/'+route,{method:body===undefined?'GET':'POST',headers:{'X-Admin-Request':'1',...(token?{Authorization:'Bearer '+token}:{}),...(body!==undefined?{'Content-Type':'application/json'}:{}),...headers},body:body===undefined?undefined:JSON.stringify(body)});
  const text=await res.text();assert.equal(res.status,expected,`${route}: ${text}`);return JSON.parse(text);
}
const version=async()=>(await request('people')).revision;
const write=async(route,body,expected=200)=>request(route,{revision:await version(),request_id:randomUUID(),...body},expected);
const disk=async()=>JSON.parse(await readFile(path.join(data,'face_index.json'),'utf8'));
async function search(){const form=new FormData();form.append('file',new Blob([await readFile(path.join(data,'query.jpg'))],{type:'image/jpeg'}),'selfie.jpg');const response=await fetch(base+'/api/face/find',{method:'POST',body:form});assert.equal(response.status,200);return response.json();}
async function jpeg(route){
  const response=await fetch(base+route,{headers:{'X-Admin-Request':'1',Authorization:'Bearer '+token}});
  assert.equal(response.status,200);assert.match(response.headers.get('content-type'),/image\/jpeg/);assert.equal(response.headers.get('cache-control'),'no-store');assert.equal(Buffer.from(await response.arrayBuffer()).readUInt16BE(),0xffd8);
}
try {
  run(['tests/create_face_fixture.py',data,'--real-query']);
  run(['-c',`import sys,json,hashlib,shutil
from pathlib import Path
sys.path.insert(0,'face-service')
from core import add_photo,atomic_json
from face_state import face_map
from reviews import reassign_faces
r=Path(sys.argv[1]);p='4'*64;s=json.loads((r/'face_index.json').read_text())
reassign_faces(s,[next(iter(face_map(s)))])
raw=(r/'query.jpg').read_bytes();shutil.copyfile(r/'query.jpg',r/'originals'/f'{p}.jpg')
add_photo(s,p,'漏臉測試照片',[]);atomic_json(r/'face_index.json',s)
atomic_json(r/'jobs'/f'{p}.json',{'id':p,'file':p+'.jpg','name':'漏臉測試照片','contentHash':hashlib.sha256(raw).hexdigest(),'driveStatus':'disabled','originalReleased':False})
`,data]);
  const backup=path.join(home,'backup');
  run(['face-service/maintain_faces.py','backup','--data',data,'--output',backup]);
  run(['face-service/maintain_faces.py','migrate','--data',data,'--backup',backup]);
  const original=await disk(),originalJob=await readFile(path.join(data,'jobs',photoId+'.json'),'utf8');
  await startPython();node=await start(process.execPath,['server.js'],{PORT:String(nodePort),FACE_SERVICE_URL:`http://127.0.0.1:${pyPort}`},base+'/api/health');
  await request('face-backfill',undefined,401);
  token=(await request('login',{password})).adminToken;
  await request('face-backfill',undefined,403,{'X-Admin-Request':'0'});
  assert.equal((await fetch(base+'/internal/face-backfill/original/'+photoId)).status,401);
  assert.equal((await search()).matches.some(row=>row.id===photoId),false);
  const started=await write('face-backfill/start',{photo_ids:[photoId]});assert.equal(started.queued,1);
  let job;
  for(let i=0;i<200;i++){
    job=(await request('face-backfill')).jobs[0];
    if(job.status==='ready')break;
    assert(['queued','processing'].includes(job.status),JSON.stringify(job));await sleep(100);
  }
  assert.equal(job.status,'ready');assert(job.new_faces>=1);assert.equal((await disk()).photos[photoId].faces.length,0);
  const candidate=await request('face-backfill/candidates/'+job.candidate_id);
  assert.equal(candidate.stale,false);assert(!JSON.stringify(candidate).includes('embedding'));
  assert.equal((await fetch(base+candidate.photo_url)).status,403);
  await jpeg(candidate.photo_url);await jpeg(candidate.faces[0].crop_url);
  const payload={revision:await version(),request_id:randomUUID(),face_ids:[candidate.faces[0].face_id]};
  const accepted=await request(`face-backfill/candidates/${job.candidate_id}/accept`,payload);
  assert.equal(accepted.added_count,1);
  let after=await disk();assert.equal(after.photos[photoId].faces.length,1);assert.equal(after.people[accepted.person_ids[0]].provisional,true);
  assert.deepEqual(after.people[accepted.person_ids[0]].representatives,[]);assert((await search()).matches.some(row=>row.id===photoId));
  assert.equal((await request(`face-backfill/candidates/${job.candidate_id}/accept`,payload)).replayed,true);
  await request(`face-backfill/candidates/${job.candidate_id}/accept`,{...payload,request_id:randomUUID()},409);
  for(const [pid,person] of Object.entries(original.people))assert.deepEqual(after.people[pid],person);
  assert.deepEqual(after.merge_history,original.merge_history);assert.deepEqual(after.aliases,original.aliases);
  assert.deepEqual(after.admin_history.slice(0,-1),original.admin_history);
  for(const pid of Object.keys(original.photos).filter(pid=>pid!==photoId))assert.deepEqual(after.photos[pid],original.photos[pid]);
  await write('face-corrections/undo',{correction_id:accepted.operation_id});
  after=await disk();assert.deepEqual(after.photos[photoId].faces,[]);assert.deepEqual(after.admin_history,original.admin_history);
  assert.equal((await search()).matches.some(row=>row.id===photoId),false);
  await stop(py);await startPython();
  assert.equal((await request(`face-backfill/candidates/${job.candidate_id}/accept`,payload)).replayed,true);
  assert.equal((await disk()).photos[photoId].faces.length,0);
  assert.equal((await request('face-backfill')).jobs[0].attempts,1);
  assert.equal((await write('face-backfill/start',{photo_ids:[photoId]})).queued,0);
  await write('face-backfill/pause',{});assert.equal((await request('face-backfill')).paused,true);
  assert.equal(await readFile(path.join(data,'jobs',photoId+'.json'),'utf8'),originalJob);
  assert.equal((await readdir(path.join(data,'face-backfill/tmp'))).length,0);
  console.log('PASS: verified backup/migration, real Node/Python backfill source+models, private candidate JPEG/no vectors, explicit append/search invalidation, global undo, retry/restart/pause, original manifests and old people/corrections/merge/aliases preserved.');
  if(process.argv.includes('--serve')){
    console.log(JSON.stringify({fixtureURL:base+'/admin-people.html',password,data}));
    await new Promise(resolve=>{process.once('SIGINT',resolve);process.once('SIGTERM',resolve);});
  }
} catch(error){console.error(logs);throw error;}
finally {await stop(node);await stop(py);await rm(home,{recursive:true,force:true});}
