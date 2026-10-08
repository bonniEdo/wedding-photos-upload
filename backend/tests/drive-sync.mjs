import {createRequire} from 'node:module';
import {mkdtemp,mkdir,readFile,writeFile,rm,readdir} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';import crypto from 'node:crypto';
import {Readable} from 'node:stream';import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const createSync=require('../drive-sync');
const data=await mkdtemp(path.join(os.tmpdir(),'wedding-drive-'));
process.env.DATA_DIR=data;process.env.DRIVE_ENABLED='false';
for(const d of ['jobs','originals'])await mkdir(path.join(data,d));
const {persistPhoto}=require('../server'); // actual photo persistence and SHA-256 deduplication
const bytes={a:Buffer.from('photo-a'),b:Buffer.from('photo-b'),c:Buffer.from('photo-a')};
const file=(id,name=id+'.jpg')=>({id,name,mimeType:'image/jpeg',size:String(bytes[id].length),modifiedTime:'2026-10-07T00:00:00Z',md5Checksum:crypto.createHash('md5').update(bytes[id]).digest('hex'),capabilities:{canDownload:true}});
let media=0,list=0,failB=true,busy=false;
const options=[];
const drive={files:{
 async list(p){list++;options.push(p);assert.equal(p.supportsAllDrives,true);assert.equal(p.includeItemsFromAllDrives,true);
  if(p.q.includes("'root'"))return {data:p.pageToken?{files:[file('c'),{id:'huge',name:'huge.jpg',mimeType:'image/jpeg',size:'200000000'},{id:'blocked',name:'blocked.jpg',mimeType:'image/jpeg',capabilities:{canDownload:false}},{id:'text',name:'notes.txt',mimeType:'text/plain'}]}:{files:[file('a'),{id:'sub',mimeType:'application/vnd.google-apps.folder'}],nextPageToken:'second'}};
  assert(p.q.includes("'sub'"));return {data:{files:[file('b')]}};
 },
 async get(p,o){assert.equal(p.alt,'media');assert.equal(o.responseType,'stream');media++;if(p.fileId==='b'&&failB){failB=false;throw new Error('temporary outage');}return {data:Readable.from([bytes[p.fileId]])};}
}};
async function atomicJSON(p,v){const tmp=p+'.tmp';await writeFile(tmp,JSON.stringify(v));await (await import('node:fs/promises')).rename(tmp,p);}
const opts={data,folderId:'root',getDrive:()=>drive,persistPhoto,atomicJSON,isUploadBusy:()=>busy,logger:{error(){}}};
let sync=createSync(opts);
try{
 await sync.init();await Promise.all([sync.scan(),sync.scan()]);assert.equal(list,3);assert.equal(sync.status().discovered,5);assert.equal(sync.status().skipped,2);
 busy=true;assert.equal(await sync.pump(),false);assert.equal(media,0);busy=false;
 await sync.pump();await sync.pump();await sync.pump();
 assert.equal(sync.status().imported,2);assert.equal(sync.status().failed,1);assert.equal((await readdir(path.join(data,'jobs'))).length,2); // distinct Drive files remain distinct even with identical bytes
 let stored=JSON.parse(await readFile(path.join(data,'drive_sync.json'),'utf8'));stored.files.b.retryAt=0;await atomicJSON(path.join(data,'drive_sync.json'),stored);
 await sync.stop();sync=createSync(opts);await sync.init();await sync.pump();assert.equal(sync.status().imported,3);assert.equal(sync.status().failed,0);
 const count=media;await sync.scan();await sync.pump();assert.equal(media,count); // completed files aren't downloaded again
 const jobs=await readdir(path.join(data,'jobs'));assert.equal(jobs.length,3);
 for(const name of jobs){const j=JSON.parse(await readFile(path.join(data,'jobs',name),'utf8'));assert.equal(j.driveStatus,'done');assert.equal(j.source,'drive');assert(j.drive.id);}
 // A missing local original must be downloaded again instead of falsely reporting success.
 stored=JSON.parse(await readFile(path.join(data,'drive_sync.json'),'utf8'));await rm(path.join(data,'originals',stored.files.b.localFile));
 await sync.scan();await sync.pump();assert.equal(media,count+1);assert((await readFile(path.join(data,'originals',stored.files.b.localFile))).length>0);
 bytes.b=Buffer.from('photo-b-updated');await sync.scan();await sync.pump();assert.equal((await readdir(path.join(data,'jobs'))).length,4);
 // Known website backup metadata can skip downloading existing local bytes entirely.
 await sync.stop();stored=JSON.parse(await readFile(path.join(data,'drive_sync.json'),'utf8'));stored.files={};await atomicJSON(path.join(data,'drive_sync.json'),stored);
 const aHash=crypto.createHash('sha256').update('drive:a:'+crypto.createHash('sha256').update(bytes.a).digest('hex')).digest('hex');
 const known=createSync({...opts,getKnownLocal:f=>f.id==='a'?{id:aHash,localFile:aHash+'.jpg'}:null});await known.init();const beforeKnown=media;await known.scan();assert.equal(known.status().imported,1);assert.equal(media,beforeKnown);await known.stop();
 // Changing the configured import folder resets only import bookkeeping, not existing local photos.
 await sync.stop();const other=createSync({...opts,folderId:'another',getDrive:()=>undefined});await other.init();assert.equal(other.status().discovered,0);await other.stop();
 console.log('PASS: paginated and recursive listing, single scan, streaming download, busy-upload pause, durable queue/retry/restart, distinct remote photos and stable revision IDs, imported photos never re-uploaded, completed imports skipped, scope reset, size/permission handling. Google Drive API mocked.');
}finally{await sync.stop();await rm(data,{recursive:true,force:true});}
