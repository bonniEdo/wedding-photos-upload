import {createRequire} from 'node:module';
import {mkdtemp,mkdir,writeFile,readFile,rm,access} from 'node:fs/promises';
import {Readable} from 'node:stream';
import os from 'node:os';import path from 'node:path';import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const data=await mkdtemp(path.join(os.tmpdir(),'wedding-cloud-'));
process.env.DATA_DIR=data;process.env.DRIVE_ENABLED='false';process.env.GOOGLE_DRIVE_FOLDER_ID='root';
for(const d of ['jobs','originals'])await mkdir(path.join(data,d));
const {app,persistPhoto,backupPhoto,cleanupOriginals,streamOriginal}=require('../server');
const server=app.listen(0);await new Promise(r=>server.once('listening',r));const base='http://127.0.0.1:'+server.address().port;
const photo=path.join(data,'input.jpg');await writeFile(photo,'same-photo');
const file={path:photo,originalname:'same.jpg',mimetype:'image/jpeg'};
let created=0,deleted=false;const remotes=new Map();
const cloud={files:{
 async list(p){const id=p.q.match(/value='([^']+)'/)[1];return {data:{files:remotes.has(id)?[remotes.get(id)]:[]}};},
 async create(p){for await(const b of p.media.body){};created++;const remote={id:'cloud-'+created,md5Checksum:'checksum'};remotes.set(p.requestBody.appProperties.weddingPhotoId,remote);return {data:remote};},
 async get(p){if(deleted){const e=new Error('gone');e.code=404;throw e;}if(p.alt==='media')return {data:Readable.from(['original-from-cloud'])};return {data:{trashed:false,md5Checksum:'checksum'}};}
}};
app.get('/test-original/:id',(req,res)=>streamOriginal(req,res,cloud));
try{
 const [a,retry]=await Promise.all([persistPhoto(file,null,'same-action'),persistPhoto(file,null,'same-action')]);assert.equal(a.id,retry.id);
 const b=await persistPhoto(file,null,'new-action');assert.notEqual(a.id,b.id);
 await backupPhoto(a.id,cloud);await backupPhoto(a.id,cloud);await backupPhoto(b.id,cloud);assert.equal(created,2);
 await cleanupOriginals(cloud);await access(path.join(data,'originals',a.localFile)); // analysis not complete
 await writeFile(path.join(data,'face_index.json'),JSON.stringify({photos:{[a.id]:{},[b.id]:{}}}));
 deleted=true;await cleanupOriginals(cloud);await access(path.join(data,'originals',a.localFile)); // missing cloud copy: retain
 deleted=false;const realNow=Date.now;Date.now=()=>realNow()+300001;await cleanupOriginals(cloud);Date.now=realNow;await assert.rejects(access(path.join(data,'originals',a.localFile)));
 assert.equal(JSON.parse(await readFile(path.join(data,'jobs',a.id+'.json'))).originalReleased,true);
 const downloaded=await fetch(base+'/test-original/'+a.id);assert.equal(downloaded.status,200);assert.match(downloaded.headers.get('content-disposition'),/attachment/);assert.equal(await downloaded.text(),'original-from-cloud');await assert.rejects(access(path.join(data,'originals',a.localFile)));
 deleted=true;assert.equal((await fetch(base+'/test-original/'+a.id)).status,404);deleted=false;
 const retryAfterCleanup=await persistPhoto(file,null,'same-action');assert.equal(retryAfterCleanup.id,a.id);assert(retryAfterCleanup.duplicate);await assert.rejects(access(path.join(data,'originals',a.localFile)));
 const c=await persistPhoto(file,null,'third-action');assert.notEqual(c.id,a.id);await access(path.join(data,'originals',c.localFile));
 const config=await(await fetch(base+'/api/config')).json();assert.equal(config.allPhotosUrl,'https://drive.google.com/drive/folders/root');
 // Cleanup markers prevent the photographer importer from repeatedly downloading completed photos.
 const createSync=require('../drive-sync');let downloads=0;
 const sync=createSync({data,folderId:'root',getDrive:()=>({files:{list:async()=>({data:{files:[{id:'cloud-1',name:'same.jpg',mimeType:'image/jpeg',md5Checksum:'checksum'}]}}),get:async()=>{downloads++;throw Error('should not download');}}}),persistPhoto,atomicJSON:async(p,v)=>writeFile(p,JSON.stringify(v)),getKnownLocal:()=>({id:a.id,localFile:a.localFile})});
 await sync.init();await sync.scan();assert.equal(sync.status().imported,1);await sync.pump();assert.equal(downloads,0);await sync.scan();await sync.pump();assert.equal(downloads,0);await sync.stop();
 console.log('PASS: distinct uploads of same bytes; concurrent idempotent retry; cloud retry no duplicate; cleanup requires analysis and verified cloud copy; missing cloud retains original; released imports never redownload; all-photos config. Cloud API mocked.');
}finally{await new Promise(r=>server.close(r));await rm(data,{recursive:true,force:true});}
