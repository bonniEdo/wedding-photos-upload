// Real HTTP ingestion test, with Drive disabled; not a wedding-network capacity benchmark.
import {spawn} from 'node:child_process';
import {mkdtemp,readdir,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const data=await mkdtemp(path.join(tmpdir(),'wedding-load-'));
const port=Number(process.env.TEST_PORT||31987),base=`http://127.0.0.1:${port}`;
let child;let throttled=0;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function start(){
  child=spawn(process.execPath,['server.js'],{cwd:root,env:{...process.env,PORT:String(port),DATA_DIR:data,DRIVE_ENABLED:'false',MAX_ACTIVE_UPLOADS:'8'},stdio:['ignore','pipe','pipe']});
  let output='';child.stderr.on('data',b=>output+=b);
  for(let n=0;n<100;n++){try{const r=await fetch(base+'/api/health');if(r.ok)return;}catch{}await delay(100);}
  throw new Error('Server failed to start: '+output);
}
async function stop(){if(child?.exitCode===null){child.kill();await new Promise(r=>child.once('exit',r));}}
async function upload(n){
  const bytes=Buffer.alloc(128*1024,n);bytes.write(String(n));
  for(let attempt=0;attempt<100;attempt++){
    const form=new FormData();form.append('photos',new Blob([bytes],{type:'image/jpeg'}),`photo-${n}.jpg`);
    const r=await fetch(base+'/api/photos/upload',{method:'POST',headers:{'X-Upload-Key':'loadtest-request-'+n},body:form});const d=await r.json();
    if(r.status===429){throttled++;await delay(100+Math.random()*200);continue;}
    assert.equal(r.status,200,JSON.stringify(d));return d;
  }
  throw new Error('Retries exhausted');
}
try {
  await start();const begin=Date.now();
  const replies=await Promise.all(Array.from({length:80},(_,n)=>upload(n)));
  assert.equal(replies.reduce((n,r)=>n+r.count,0),80);
  assert.equal((await readdir(path.join(data,'jobs'))).filter(n=>n.endsWith('.json')).length,80);
  const ids=new Set(replies.map(r=>r.files[0].id));assert.equal(ids.size,80);
  const duplicates=await Promise.all(Array.from({length:10},()=>upload(0)));
  assert(duplicates.every(r=>r.files[0].duplicate));
  const tooMany=new FormData();for(let n=0;n<6;n++)tooMany.append('photos',new Blob([Buffer.from('test')],{type:'image/jpeg'}),`extra-${n}.jpg`);
  const rejected=await fetch(base+'/api/photos/upload',{method:'POST',body:tooMany});assert.equal(rejected.status,400);
  await stop();await start();const repeated=await upload(0);assert(repeated.files[0].duplicate);
  assert.equal((await readdir(path.join(data,'originals'))).length,80);
  for(const name of await readdir(path.join(data,'jobs'))){const j=JSON.parse(await readFile(path.join(data,'jobs',name),'utf8'));assert(j.id);assert.equal(j.driveStatus,'disabled');}
  console.log(JSON.stringify({ok:true,simultaneousClients:80,payloadKB:128,photosStored:80,throttledRequests:throttled,duplicateRequests:10,restartDeduplication:true,rejectSixPhotoBatch:true,elapsedMs:Date.now()-begin}));
}finally{await stop();await rm(data,{recursive:true,force:true});}
