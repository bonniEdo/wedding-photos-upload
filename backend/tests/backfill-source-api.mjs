import {spawn,spawnSync} from 'node:child_process';
import http from 'node:http';
import {mkdtemp,mkdir,writeFile,readFile,readdir,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const data=await realpath(await mkdtemp(path.join(tmpdir(),'wedding-backfill-source-api-')));
const password='backfill-source-test-password';
const key=crypto.createHash('sha256').update('wedding-face-admin:'+password).digest('hex');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const id='1'.repeat(64),missing='2'.repeat(64),corrupt='3'.repeat(64),large='4'.repeat(64),unsafe='5'.repeat(64);
const bytes=Buffer.from('test original binary\x00\xff','latin1'),largeBytes=Buffer.alloc(8*1024*1024,123);
for(const name of ['jobs','originals'])await mkdir(path.join(data,name));
const jobs={
  [id]:{id,file:id+'.jpg',contentHash:sha(bytes),originalReleased:false,driveStatus:'disabled'},
  [missing]:{id:missing,file:missing+'.jpg',contentHash:sha(bytes),originalReleased:true,driveStatus:'done',drive:{id:'unavailable-remote'}},
  [corrupt]:{id:corrupt,file:corrupt+'.jpg',contentHash:sha('different historical contents'),driveStatus:'disabled'},
  [large]:{id:large,file:large+'.jpg',contentHash:sha(largeBytes),driveStatus:'disabled'},
  [unsafe]:{id:unsafe,file:'../outside.jpg',contentHash:sha(bytes),driveStatus:'disabled'}
};
for(const job of Object.values(jobs))await writeFile(path.join(data,'jobs',job.id+'.json'),JSON.stringify(job));
await writeFile(path.join(data,'originals',jobs[id].file),bytes);
await writeFile(path.join(data,'originals',jobs[corrupt].file),bytes);
await writeFile(path.join(data,'originals',jobs[large].file),largeBytes);
let proc,base,output='';
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function port(){const server=http.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const value=server.address().port;await new Promise(resolve=>server.close(resolve));return value;}
async function start(adminPassword=password){
  const value=await port();base='http://127.0.0.1:'+value;output='';
  proc=spawn(process.execPath,['server.js'],{cwd:root,env:{...process.env,PORT:String(value),DATA_DIR:data,DRIVE_ENABLED:'false',ADMIN_PASSWORD:adminPassword,SERVE_FRONTEND:'false'},stdio:['ignore','pipe','pipe']});
  proc.stdout.on('data',chunk=>{output+=chunk;});proc.stderr.on('data',chunk=>{output+=chunk;});
  for(let i=0;i<150;i++){
    if(proc.exitCode!==null)throw new Error('Test server exited: '+output);
    try{if((await fetch(base+'/api/health')).ok)return;}catch{}
    await pause(30);
  }
  throw new Error('Test server startup timed out: '+output);
}
async function stop(){if(proc?.exitCode===null){proc.kill();await new Promise(resolve=>proc.once('exit',resolve));}}
function request(photoId,headers={'X-Admin-Key':key}){return fetch(base+'/internal/face-backfill/original/'+photoId,{headers});}
async function temps(){try{return await readdir(path.join(data,'face-backfill','tmp'));}catch(error){if(error.code==='ENOENT')return [];throw error;}}
async function waitClean(){for(let i=0;i<100;i++){if(!(await temps()).length)return;await pause(20);}assert.deepEqual(await temps(),[]);}
function cli(args){return spawnSync(process.execPath,[path.join(root,'backfill-source-cli.mjs'),...args],{cwd:root,encoding:null,maxBuffer:16*1024*1024,env:{...process.env,FACE_BACKFILL_MAX_BYTES:'104857600'}});}
try{
  await start();
  for(const headers of [{},{'X-Admin-Key':'wrong'},{'X-Admin-Key':'0'.repeat(64)},{'X-Admin-Request':'1',Authorization:'Bearer '+key}]){
    const response=await request(id,headers);assert.equal(response.status,401);assert.equal(response.headers.get('cache-control'),'no-store');
    const error=await response.json();assert.equal(error.code,'unavailable');assert(!JSON.stringify(error).includes(data));
  }
  assert.deepEqual(await temps(),[]);
  const response=await request(id);
  assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'application/octet-stream');
  assert.equal(response.headers.get('x-original-sha256'),sha(bytes));assert.equal(response.headers.get('x-original-source'),'local');
  assert.equal(response.headers.get('content-length'),String(bytes.length));assert.equal(response.headers.get('cache-control'),'no-store');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()),bytes);await waitClean();
  assert.deepEqual(await readFile(path.join(data,'originals',jobs[id].file)),bytes);
  const cors=await request(id,{'X-Admin-Key':key,Origin:'https://untrusted.invalid'});
  assert.equal(cors.headers.get('access-control-allow-origin'),null);await cors.arrayBuffer();await waitClean();
  for(const [photoId,status,code] of [[missing,404,'unavailable'],['f'.repeat(64),404,'unavailable'],['not-a-photo',400,'invalid'],[corrupt,409,'changed'],[unsafe,400,'invalid']]){
    const result=await request(photoId);assert.equal(result.status,status);assert.equal(result.headers.get('cache-control'),'no-store');
    const error=await result.json();assert.equal(error.code,code);assert(!JSON.stringify(error).includes(data));await waitClean();
  }
  // Abandon a large streaming response after its first chunk; its staged snapshot is still removed.
  await new Promise((resolve,reject)=>{
    const req=http.get(base+'/internal/face-backfill/original/'+large,{headers:{'X-Admin-Key':key}},res=>{
      assert.equal(res.statusCode,200);
      res.once('data',()=>{res.destroy();req.destroy();resolve();});res.once('error',error=>{if(error.code!=='ECONNRESET')reject(error);});
    });req.once('error',error=>{if(error.code!=='ECONNRESET')reject(error);});
  });await waitClean();
  for(const [photoId,job] of Object.entries(jobs))assert.deepEqual(JSON.parse(await readFile(path.join(data,'jobs',photoId+'.json'),'utf8')),job);

  // Offline helper uses binary stdout, safe stderr metadata and an explicit opt-in for any Drive access.
  for(const flags of [[],['--local-only']]){
    const result=cli(['--data',data,'--photo-id',id,...flags]);assert.equal(result.status,0,result.stderr.toString());assert.deepEqual(result.stdout,bytes);
    assert.deepEqual(JSON.parse(result.stderr.toString()),{source:'local',sha256:sha(bytes),bytes:bytes.length});await waitClean();
  }
  for(const [args,code] of [
    [['--data',data,'--photo-id',missing],'unavailable'],
    [['--data',data,'--photo-id',corrupt],'changed'],
    [['--data','relative','--photo-id',id],'invalid'],
    [['--data',data,'--photo-id','../bad'],'invalid'],
    [['--data',data,'--photo-id',id,'--local-only','--allow-drive'],'invalid'],
    [['--data',data,'--photo-id',id,'--unknown'],'invalid']
  ]){
    const result=cli(args);assert.notEqual(result.status,0);assert.equal(result.stdout.length,0);
    const error=JSON.parse(result.stderr.toString());assert.equal(error.code,code);assert(!JSON.stringify(error).includes(data));await waitClean();
  }
  await stop();await start('');
  const disabled=await request(id);assert.equal(disabled.status,503);assert.equal((await disabled.json()).code,'unavailable');
  console.log('PASS: internal key authentication, disabled default, binary source/hash/length headers, safe error status mapping, unchanged CORS and manifests, disconnect cleanup, local-only CLI and explicit Drive opt-in. Isolated local Node server; no cloud calls.');
}finally{await stop();await rm(data,{recursive:true,force:true});}
