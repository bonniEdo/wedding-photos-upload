import {spawn} from 'node:child_process';
import http from 'node:http';
import {mkdtemp,readdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const data=await mkdtemp(path.join(tmpdir(),'wedding-selfie-cancel-'));
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const port=async()=>{const s=http.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;};
let calls=0,disconnected=false,proc;
const upstream=http.createServer(async(req,res)=>{
  for await(const _ of req){} // Receive the forwarded multipart body before canceling.
  calls++;
  if(calls===1)res.once('close',()=>{disconnected=true;});
  else {res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true,matches:[]}));}
});
await new Promise(r=>upstream.listen(0,'127.0.0.1',r));
const base=`http://127.0.0.1:${await port()}`;
async function until(test,message){for(let i=0;i<100;i++){if(await test())return;await pause(30);}throw new Error(message);}
function query(signal){const form=new FormData();form.append('file',new Blob(['fixture-only-selfie'],{type:'image/jpeg'}),'query.jpg');return fetch(base+'/api/face/find',{method:'POST',body:form,signal});}
try{
  proc=spawn(process.execPath,['server.js'],{cwd:root,env:{...process.env,PORT:new URL(base).port,DATA_DIR:data,TMPDIR:data,DRIVE_ENABLED:'false',FACE_SERVICE_URL:`http://127.0.0.1:${upstream.address().port}`},stdio:'ignore'});
  await until(async()=>{try{return (await fetch(base+'/api/health')).ok;}catch{return false;}},'Node startup timed out');
  const controller=new AbortController();const pending=query(controller.signal);const rejected=assert.rejects(pending,{name:'AbortError'});
  await until(()=>calls===1,'Selfie was not forwarded');
  assert.equal((await readdir(path.join(data,'wedding-selfie'))).length,1);
  controller.abort();await rejected;
  await until(()=>disconnected,'Cancel did not abort the upstream request');
  await until(async()=>!(await readdir(path.join(data,'wedding-selfie'))).length,'Canceled selfie temp file leaked');
  assert.equal((await query()).status,200,'Search admission must be released after cancel');
  await until(async()=>!(await readdir(path.join(data,'wedding-selfie'))).length,'Successful selfie temp file leaked');
  console.log('PASS: browser disconnect aborts upstream fetch, releases admission, and removes canceled/successful selfie temp files. Python model inference mocked.');
}finally{
  if(proc?.exitCode===null){proc.kill();await new Promise(r=>proc.once('exit',r));}
  upstream.closeAllConnections();await new Promise(r=>upstream.close(r));await rm(data,{recursive:true,force:true});
}
