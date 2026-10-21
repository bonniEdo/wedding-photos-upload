import {createRequire} from 'node:module';
import {mkdtemp,mkdir,readFile,writeFile,rm,readdir,symlink,realpath} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';import crypto from 'node:crypto';
import {Readable} from 'node:stream';import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const createBackfillOriginals=require('../backfill-originals');
const {BackfillOriginalError}=createBackfillOriginals;
const id='a'.repeat(64),other='b'.repeat(64),bytes=Buffer.from('retained original bytes');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const md5=value=>crypto.createHash('md5').update(value).digest('hex');
const data=await realpath(await mkdtemp(path.join(os.tmpdir(),'wedding-backfill-originals-')));
const original=path.join(data,'originals');
await mkdir(original);
const originalJob={id,file:id+'.jpg',contentHash:sha(bytes),originalReleased:true,driveStatus:'done',drive:{id:'remote-a',md5Checksum:md5(bytes)}};
const clone=value=>JSON.parse(JSON.stringify(value));
let job=clone(originalJob),drive=null,busy=false,media=0,meta=0;
const baseMeta=()=>({id:'remote-a',trashed:false,size:String(bytes.length),md5Checksum:md5(bytes),version:'12',modifiedTime:'2026-10-21T00:00:00Z',capabilities:{canDownload:true}});
const mockDrive=({before=baseMeta(),after=before,body=()=>Readable.from([bytes]),beforeError=null,mediaError=null}={})=>({files:{async get(params,options){
  assert.equal(params.fileId,'remote-a');assert.equal(params.supportsAllDrives,true);
  if(params.alt==='media'){
    media++;assert.equal(options.responseType,'stream');assert.equal(options.timeout,120000);
    if(mediaError)throw mediaError;return {data:body()};
  }
  meta++;assert(params.fields.includes('version'));assert(params.fields.includes('md5Checksum'));assert.equal(options.timeout,30000);
  if(beforeError)throw beforeError;return {data:clone(meta%2===1?before:after)};
}}});
const options={data,getDrive:()=>drive,getJob:photoId=>photoId===id?job:null,isUploadBusy:()=>busy};
const originals=createBackfillOriginals(options);
const temps=async()=>{try{return await readdir(path.join(data,'face-backfill','tmp'));}catch(error){if(error.code==='ENOENT')return [];throw error;}};
async function rejects(code,run){await assert.rejects(run,error=>error instanceof BackfillOriginalError&&error.code===code);assert.deepEqual(await temps(),[]);}
function reset(){job=clone(originalJob);drive=null;busy=false;media=0;meta=0;}

try{
  // Retained originals work without Drive and produce a stable, verified temporary snapshot.
  await writeFile(path.join(original,job.file),bytes);
  let result=await originals.acquire(id);
  assert.equal(result.source,'local');assert.equal(result.sha256,sha(bytes));assert.equal(result.bytes,bytes.length);
  assert.notEqual(result.path,path.join(original,job.file));assert.equal(path.dirname(result.path),path.join(data,'face-backfill','tmp'));
  assert.deepEqual(await readFile(result.path),bytes);assert.equal(media,0);assert.equal(meta,0);
  assert.deepEqual(job,originalJob);await result.cleanup();await result.cleanup();assert.deepEqual(await temps(),[]);
  job.drive.md5Checksum=md5('wrong historical md5');await rejects('changed',()=>originals.acquire(id));
  job.contentHash=undefined;job.drive.md5Checksum=undefined;
  result=await originals.acquire(id);assert.equal(result.sha256,sha(bytes));await result.cleanup();reset();
  await writeFile(path.join(original,job.file),Buffer.from('wrong retained file'));
  drive=mockDrive();await rejects('changed',()=>originals.acquire(id));assert.equal(media,0); // no silent remote substitution
  await rm(path.join(original,job.file));

  // Released originals may be fetched read-only without undoing the release or import bookkeeping.
  reset();drive=mockDrive();result=await originals.acquire(id);
  assert.equal(result.source,'drive');assert.equal(result.sha256,sha(bytes));assert.equal(result.bytes,bytes.length);
  assert.equal(media,1);assert.equal(meta,2);assert.deepEqual(await readFile(result.path),bytes);assert.deepEqual(job,originalJob);
  assert.deepEqual(await readdir(original),[]);await result.cleanup();assert.deepEqual(await temps(),[]);

  reset();job.contentHash=undefined;job.drive.md5Checksum=undefined;drive=mockDrive();
  await rejects('unavailable',()=>originals.acquire(id));assert.equal(meta,0);assert.equal(media,0);

  reset();await rejects('unavailable',()=>originals.acquire(id));
  job.drive=null;await rejects('unavailable',()=>originals.acquire(id));
  await rejects('unavailable',()=>originals.acquire(other));
  await rejects('invalid',()=>originals.acquire('../'+id));
  await rejects('invalid',()=>originals.acquire(id+'.jpg'));
  reset();job.id=other;await rejects('invalid',()=>originals.acquire(id));
  reset();job.contentHash='not-a-sha';await rejects('invalid',()=>originals.acquire(id));
  reset();job.drive.md5Checksum='not-md5';await rejects('invalid',()=>originals.acquire(id));
  for(const unsafe of ['../escape.jpg','sub/escape.jpg','sub\\escape.jpg','..','.','file\0.jpg','/etc/passwd']){
    reset();job.file=unsafe;await rejects('invalid',()=>originals.acquire(id));
  }
  reset();await symlink(path.join(data,'outside.jpg'),path.join(original,job.file));
  await writeFile(path.join(data,'outside.jpg'),bytes);await rejects('invalid',()=>originals.acquire(id));
  await rm(path.join(original,job.file));
  await rm(original,{recursive:true});await symlink(data,original);await rejects('invalid',()=>originals.acquire(id));
  await rm(original);await mkdir(original);

  // Missing files, permission loss and trashed or unverifiable files are reported safely.
  for(const status of [401,403,404,410]){
    reset();drive=mockDrive({beforeError:Object.assign(new Error('private token should never leak'),{response:{status}})});
    await assert.rejects(()=>originals.acquire(id),error=>error.code==='unavailable'&&!error.message.includes('private'));
    assert.deepEqual(await temps(),[]);
  }
  for(const patch of [{trashed:true},{trashed:undefined},{capabilities:{canDownload:false}},{md5Checksum:undefined},{version:undefined,modifiedTime:undefined},{size:'broken'},{size:undefined},{size:true},{size:' '}]){
    reset();drive=mockDrive({before:{...baseMeta(),...patch}});await rejects('unavailable',()=>originals.acquire(id));assert.equal(media,0);
  }
  reset();drive=mockDrive({before:{...baseMeta(),id:'wrong-id'}});await rejects('changed',()=>originals.acquire(id));
  reset();drive=mockDrive({beforeError:Object.assign(new Error('outage'),{code:503})});await rejects('retryable',()=>originals.acquire(id));
  reset();busy=true;drive=mockDrive();await rejects('retryable',()=>originals.acquire(id));assert.equal(meta,0);assert.equal(media,0);
  busy=false;result=await originals.acquire(id);await result.cleanup(); // the serialized queue recovers after rejection

  // The historical checksum and both metadata snapshots must agree with the downloaded bytes.
  reset();drive=mockDrive({before:{...baseMeta(),md5Checksum:md5('new version')}});await rejects('changed',()=>originals.acquire(id));assert.equal(media,0);
  for(const patch of [{version:'13'},{modifiedTime:'2026-10-22T00:00:00Z'},{md5Checksum:md5('new version')},{size:String(bytes.length+1)},{trashed:true},{id:'other-remote'}]){
    reset();drive=mockDrive({after:{...baseMeta(),...patch}});await rejects('changed',()=>originals.acquire(id));assert.equal(media,1);
  }
  reset();job.contentHash=sha('different historical original');drive=mockDrive();await rejects('changed',()=>originals.acquire(id));
  reset();drive=mockDrive({body:()=>Readable.from([Buffer.from('bad')])});await rejects('changed',()=>originals.acquire(id));
  reset();drive=mockDrive({body:()=>Readable.from([Buffer.alloc(bytes.length,9)])});await rejects('changed',()=>originals.acquire(id));
  reset();drive=mockDrive({mediaError:new Error('private download URL')});await rejects('retryable',()=>originals.acquire(id));
  reset();drive=mockDrive({body:()=>Readable.from((async function*(){yield bytes.subarray(0,5);throw new Error('stream lost');})())});await rejects('retryable',()=>originals.acquire(id));

  // Byte limits apply before local copies/cloud fetches and while streaming dishonest size metadata.
  reset();drive=mockDrive();const limited=createBackfillOriginals({...options,maxBytes:4});
  await rejects('unavailable',()=>limited.acquire(id));assert.equal(media,0);
  await writeFile(path.join(original,job.file),bytes);await rejects('unavailable',()=>limited.acquire(id));
  await rm(path.join(original,job.file));
  reset();job.drive.md5Checksum=md5('abc');job.contentHash=undefined;
  drive=mockDrive({before:{...baseMeta(),size:'3',md5Checksum:md5('abc')},body:()=>Readable.from([Buffer.from('abc'),Buffer.alloc(10)])});
  await rejects('unavailable',()=>limited.acquire(id));

  // A symlink in the dedicated staging directory cannot redirect file writes.
  reset();drive=mockDrive();await rm(path.join(data,'face-backfill','tmp'),{recursive:true});
  await symlink(original,path.join(data,'face-backfill','tmp'));
  await rejects('invalid',()=>originals.acquire(id));assert.deepEqual(await readdir(original),[]);
  await rm(path.join(data,'face-backfill','tmp'));await mkdir(path.join(data,'face-backfill','tmp'));

  // Concurrent callers share one serialized acquisition queue, with no overlapping downloads.
  reset();let active=0,peak=0;
  drive=mockDrive({body:()=>Readable.from((async function*(){active++;peak=Math.max(peak,active);try{
    yield bytes.subarray(0,3);await new Promise(resolve=>setTimeout(resolve,20));yield bytes.subarray(3);
  }finally{active--;}})())});
  const results=await Promise.all([originals.acquire(id),originals.acquire(id),originals.acquire(id)]);
  assert.equal(media,3);assert.equal(meta,6);assert.equal(peak,1);assert.equal(new Set(results.map(item=>item.path)).size,3);
  for(const item of results)await item.cleanup();assert.deepEqual(await temps(),[]);assert.deepEqual(job,originalJob);
  console.log('PASS: local snapshots, read-only released Drive originals, before/after revision and checksum verification, safe errors, byte limits, path/symlink rejection, serialized downloads, failure cleanup, and unchanged job/release state. Google Drive API mocked.');
}finally{await rm(data,{recursive:true,force:true});}
