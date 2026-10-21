const express = require('express');
const multer = require('multer');
const cors = require('cors');
const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const {pipeline} = require('stream/promises');
const { google } = require('googleapis');
const { authenticate } = require('@google-cloud/local-auth');
require('dotenv').config({path:path.join(__dirname,'.env')});

const app = express();
const PORT = process.env.PORT || 3000;

const CREDENTIALS_PATH = path.join(__dirname, 'credentials.json');
const TOKEN_PATH = path.join(__dirname, 'token.json');
const SOURCE_FOLDER_ID = process.env.GOOGLE_DRIVE_FOLDER_ID;
const FOLDER_ID = process.env.GOOGLE_DRIVE_UPLOAD_FOLDER_ID || SOURCE_FOLDER_ID;

const SCOPES = ['https://www.googleapis.com/auth/drive'];

// 前端一次最多選 20 張，但會拆成每批 5 張。
// 因此後端單一 request 只接受 5 張，失敗時不會整批 20 張重來。
const MAX_FILES_PER_REQUEST = 5;
const MAX_FILE_SIZE = 25 * 1024 * 1024; // 25 MB / 張

if (process.env.DRIVE_ENABLED !== 'false' && !FOLDER_ID) {
  throw new Error('Missing GOOGLE_DRIVE_FOLDER_ID in .env');
}

if (process.env.DRIVE_ENABLED !== 'false' && !fs.existsSync(CREDENTIALS_PATH)) {
  throw new Error('Missing credentials.json');
}

async function loadSavedCredentials() {
  try {
    const content = await fsp.readFile(TOKEN_PATH, 'utf8');
    return google.auth.fromJSON(JSON.parse(content));
  } catch {
    return null;
  }
}

async function saveCredentials(client) {
  const content = await fsp.readFile(CREDENTIALS_PATH, 'utf8');
  const keys = JSON.parse(content);
  const key = keys.installed || keys.web;

  const payload = JSON.stringify(
    {
      type: 'authorized_user',
      client_id: key.client_id,
      client_secret: key.client_secret,
      refresh_token: client.credentials.refresh_token,
    },
    null,
    2
  );

  await fsp.writeFile(TOKEN_PATH, payload);
}

async function authorize() {
  let client = await loadSavedCredentials();
  if (client) {
    console.log('✅ 使用既有 Google OAuth token');
    return client;
  }

  console.log('🔐 第一次執行，將開啟 Google OAuth...');
  client = await authenticate({
    scopes: SCOPES,
    keyfilePath: CREDENTIALS_PATH,
  });

  if (client.credentials?.refresh_token) {
    await saveCredentials(client);
  }

  console.log('✅ Google OAuth 授權完成');
  return client;
}

let drive;

const DATA = path.resolve(process.env.DATA_DIR || path.join(__dirname, 'data'));
const FACE_URL = process.env.FACE_SERVICE_URL || 'http://127.0.0.1:5001';
const DRIVE_ENABLED = process.env.DRIVE_ENABLED !== 'false';
const driveErrorMessage=require('./drive-errors');
let driveConnectionState=DRIVE_ENABLED?'authorizing':'disabled';
let driveConnectionError=null;
const MAX_ACTIVE = Number(process.env.MAX_ACTIVE_UPLOADS || 8);
const DRIVE_WORKERS = Number(process.env.DRIVE_WORKERS || 2);
const jobs = new Map();
let analyzedIds=new Set();
const cleanupRetryAt=new Map();
const committing = new Map();
let activeUploads = 0;
let driveActive = 0;
async function atomicJSON(file, value) {
  const tmp = `${file}.${crypto.randomUUID()}.tmp`;
  try {
    const handle = await fsp.open(tmp, 'w');
    try { await handle.writeFile(JSON.stringify(value)); await handle.sync(); }
    finally { await handle.close(); }
    await fsp.rename(tmp, file);
  } finally { await fsp.unlink(tmp).catch(()=>{}); }
}
async function digest(file) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}
async function persistPhoto(file, remote=null, uploadKey=null) {
  const contentHash = await digest(file.path);
  // New guest action = new photo; retries retain their action key. Drive revisions have stable IDs.
  const identity = remote ? `drive:${remote.id}:${contentHash}` : `upload:${uploadKey || crypto.randomUUID()}:${contentHash}`;
  const id = crypto.createHash('sha256').update(identity).digest('hex');
  if (committing.has(id)) {
    const result=await committing.get(id);
    return associateRemote(result,remote);
  }
  const promise = (async()=>{
    const prior=jobs.get(id);
    if(prior && (prior.originalReleased || fs.existsSync(path.join(DATA,'originals',prior.file))) && fs.existsSync(path.join(DATA,'jobs',id+'.json'))) return {id,duplicate:true,localFile:prior.file};
    const ext = ({'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/heic':'.heic','image/heif':'.heif'})[file.mimetype];
    const name = path.basename(file.originalname).slice(0,200);
    const filename = id + ext;
    const target = path.join(DATA,'originals',filename);
    const tmp = target + '.' + crypto.randomUUID() + '.tmp';
    try {
      await fsp.copyFile(file.path, tmp);
      const h = await fsp.open(tmp,'r+'); try { await h.sync(); } finally {await h.close();}
      await fsp.rename(tmp,target);
    } finally { await fsp.unlink(tmp).catch(()=>{}); }
    const job = {...(prior||{}),contentHash,id,name:prior?.name||name,file:filename,mimeType:file.mimetype,
      driveStatus:remote?'done':prior?.driveStatus||(DRIVE_ENABLED?'pending':'disabled'), attempts:prior?.attempts||0, retryAt:0, createdAt:prior?.createdAt||Date.now(),
      ...(remote?{source:prior?.source||'drive',drive:{id:remote.id,name:remote.name,webViewLink:remote.webViewLink,md5Checksum:remote.md5Checksum}}:{})};
    await atomicJSON(path.join(DATA,'jobs',id+'.json'),job);
    jobs.set(id,job);
    return {id,duplicate:false,localFile:filename};
  })();
  committing.set(id,promise);
  try { return await associateRemote(await promise,remote); } finally { committing.delete(id); }
}
async function associateRemote(result,remote) {
  const job=jobs.get(result.id);
  if(remote && job && ['pending','disabled'].includes(job.driveStatus)) {
    // Existing matching cloud bytes already provide a backup; do not re-upload them.
    job.driveStatus='done';job.drive={id:remote.id,name:remote.name,webViewLink:remote.webViewLink,md5Checksum:remote.md5Checksum};
    await atomicJSON(path.join(DATA,'jobs',job.id+'.json'),job);
  }
  return result;
}
const driveSync=require('./drive-sync')({data:DATA,folderId:SOURCE_FOLDER_ID,getDrive:()=>drive,
  persistPhoto,atomicJSON,getKnownLocal:file=>{
    const job=jobs.get(file.appProperties?.weddingPhotoId);
    return job && file.md5Checksum && job.drive?.id===file.id && job.drive?.md5Checksum===file.md5Checksum ? {id:job.id,localFile:job.file}:null;
  },enabled:DRIVE_ENABLED&&process.env.DRIVE_SYNC_ENABLED!=='false',
  subfolders:process.env.DRIVE_SYNC_SUBFOLDERS!=='false',
  intervalSeconds:Number(process.env.DRIVE_SYNC_INTERVAL_SECONDS||300),
  maxBytes:Number(process.env.DRIVE_SYNC_MAX_FILE_MB||100)*1024*1024,
  isUploadBusy:()=>activeUploads>0 || [...jobs.values()].filter(j=>j.source==='drive' && !analyzedIds.has(j.id)).length>=30});
const backfillOriginals=require('./backfill-originals')({data:DATA,getDrive:()=>drive,getJob:id=>jobs.get(id),
  maxBytes:Number(process.env.FACE_BACKFILL_MAX_BYTES||100*1024*1024),isUploadBusy:()=>activeUploads>0});
const upload = multer({
  dest:path.join(os.tmpdir(),'wedding-photo-upload'),
  limits:{fileSize:MAX_FILE_SIZE,files:MAX_FILES_PER_REQUEST},
  fileFilter:(req,file,cb)=>{
    if (['image/jpeg','image/png','image/webp','image/heic','image/heif'].includes(file.mimetype)) cb(null,true);
    else cb(new Error('不支援的圖片格式，請選 JPG、PNG、WebP 或 HEIC'));
  }
});
app.use(cors({credentials:true,origin:(origin,cb)=>{
  const allowed=['http://localhost:'+PORT,'http://localhost:5173','http://127.0.0.1:5173',process.env.GITHUB_PAGES_ORIGIN,...(process.env.FRONTEND_ORIGINS||'').split(',')].map(s=>s?.trim()).filter(Boolean);
  cb(null,!origin||allowed.includes(origin));
}}));
const backfillPassword=process.env.ADMIN_PASSWORD||'';
const backfillConfigured=Array.from(backfillPassword).length>=12;
const backfillInternalKey=crypto.createHash('sha256').update('wedding-face-admin:'+backfillPassword).digest();
app.get('/internal/face-backfill/original/:id',async(req,res)=>{
  res.set('Cache-Control','no-store');
  if(!backfillConfigured)return res.status(503).json({code:'unavailable',message:'請先設定至少 12 字元的 ADMIN_PASSWORD'});
  const supplied=req.get('X-Admin-Key')||'';
  const validKey=/^[a-f0-9]{64}$/.test(supplied);
  const key=validKey?Buffer.from(supplied,'hex'):Buffer.alloc(32);
  const sameKey=crypto.timingSafeEqual(key,backfillInternalKey);
  if(!validKey||!sameKey)return res.status(401).json({code:'unavailable',message:'原檔回填請求驗證失敗'});
  const disconnected=new AbortController();
  const onClose=()=>{if(!res.writableEnded)disconnected.abort();};
  res.once('close',onClose);
  let original;
  try{
    original=await backfillOriginals.acquire(req.params.id);
    if(res.destroyed||disconnected.signal.aborted)return;
    res.type('application/octet-stream').set({'X-Original-Sha256':original.sha256,'X-Original-Source':original.source,'Content-Length':String(original.bytes)});
    await pipeline(fs.createReadStream(original.path),res,{signal:disconnected.signal});
  }catch(error){
    if(!res.destroyed&&!res.headersSent){
      const status={invalid:400,unavailable:404,changed:409,retryable:503};
      const code=Object.hasOwn(status,error.code)?error.code:'retryable';
      res.status(status[code]).json({code,message:code===error.code?error.message:'暫時無法取得回填原檔，請稍後重試'});
    }
  }finally{
    res.off('close',onClose);
    await original?.cleanup().catch(()=>{});
  }
});
app.use('/api/invitations',require('./invitations')({data:DATA}));
require('./admin-auth')(app,{password:process.env.ADMIN_PASSWORD||'',
  secureCookie:process.env.ADMIN_COOKIE_SECURE==='true',faceURL:FACE_URL,driveSync,backupStatus});
if(process.env.SERVE_FRONTEND!=='false') app.use(express.static(path.join(__dirname,'../frontend')));
app.get('/',(req,res)=>res.redirect('/photos.html'));
app.get('/api/config',(req,res)=>res.json({allPhotosUrl:process.env.GOOGLE_DRIVE_ALL_PHOTOS_URL || (SOURCE_FOLDER_ID?'https://drive.google.com/drive/folders/'+encodeURIComponent(SOURCE_FOLDER_ID):null)}));
app.get('/api/health',(req,res)=>res.json({ok:true,service:'wedding-photo-people',version:require('./package.json').version,
  driveEnabled:DRIVE_ENABLED,driveConnectionState,driveConnectionError,backupFailed:[...jobs.values()].filter(j=>j.lastError&&j.driveStatus==='pending').length,
  activeUploads,maxActiveUploads:MAX_ACTIVE,
  storedPhotos:jobs.size,drivePending:[...jobs.values()].filter(j=>j.driveStatus==='pending').length,
  driveBackingUp:[...jobs.values()].filter(j=>j.driveStatus==='uploading').length}));
function backupStatus(){
  const records=[...jobs.values()];
  return {ok:true,enabled:DRIVE_ENABLED,connectionState:driveConnectionState,connectionError:driveConnectionError,
    folderUrl:FOLDER_ID?'https://drive.google.com/drive/folders/'+encodeURIComponent(FOLDER_ID):null,
    total:records.length,done:records.filter(j=>j.driveStatus==='done').length,
    pending:records.filter(j=>j.driveStatus==='pending').length,uploading:records.filter(j=>j.driveStatus==='uploading').length,
    disabled:records.filter(j=>j.driveStatus==='disabled').length,
    problems:records.filter(j=>j.lastError&&j.driveStatus==='pending').slice(0,20).map(j=>({name:j.name,message:j.lastError,attempts:j.attempts}))};
}
app.get('/api/photos/status',(req,res)=>{
  const raw=typeof req.query.ids==='string'?req.query.ids:'';
  const ids=[...new Set(raw.split(',').filter(Boolean))];
  if(!ids.length||ids.length>20||ids.some(id=>!/^[a-f0-9]{64}$/.test(id)))return res.status(400).json({ok:false,message:'請提供 1 到 20 個有效照片 ID'});
  res.set('Cache-Control','no-store').json({ok:true,driveEnabled:DRIVE_ENABLED,connectionState:driveConnectionState,
    connectionError:driveConnectionError,files:ids.map(id=>{
      const j=jobs.get(id);return {id,status:j?.driveStatus||'missing',message:j?.lastError||null};
    })});
});
function admission(req,res,next) {
  if(activeUploads>=MAX_ACTIVE) {
    res.set('Retry-After','3');return res.status(429).json({ok:false,message:'目前上傳人數較多，正在等待重試'});
  }
  activeUploads++;
  let released=false;
  const release=()=>{if(!released){released=true;activeUploads--;}};
  res.once('finish',release);res.once('close',release);next();
}
app.post('/api/photos/upload',admission,upload.array('photos',MAX_FILES_PER_REQUEST),async(req,res)=>{
  const files=req.files||[];const accepted=[];
  try {
    if(!files.length) return res.status(400).json({ok:false,message:'沒有收到照片'});
    const key=req.get('X-Upload-Key') || crypto.randomUUID();
    if(!/^[a-zA-Z0-9_-]{16,100}$/.test(key))return res.status(400).json({ok:false,message:'上傳識別碼格式錯誤'});
    for (const [index,file] of files.entries()) accepted.push(await persistPhoto(file,null,key+':'+index));
    res.json({ok:true,count:accepted.length,files:accepted,analysis:'queued',backup:DRIVE_ENABLED?'queued':'disabled'});
  } catch(e) {
    console.error(e);
    res.status(500).json({ok:false,message:'儲存失敗，請稍後重試',accepted});
  } finally {
    await Promise.allSettled(files.map(file=>fsp.unlink(file.path)));
  }
});
async function backup(job,client=drive) {
  job.driveStatus='uploading';driveActive++;
  try {
    // Look for a previous successful create before retrying after a disconnect/crash.
    const existing=await client.files.list({q:`'${FOLDER_ID}' in parents and trashed=false and appProperties has { key='weddingPhotoId' and value='${job.id}' }`,fields:'files(id,name,webViewLink,md5Checksum)',pageSize:1,supportsAllDrives:true,includeItemsFromAllDrives:true},{timeout:60000});
    let remote=existing.data.files?.[0];
    if(!remote) {
      const response=await client.files.create({requestBody:{name:job.name,parents:[FOLDER_ID],appProperties:{weddingPhotoId:job.id}},
        media:{mimeType:job.mimeType,body:fs.createReadStream(path.join(DATA,'originals',job.file))},fields:'id,name,webViewLink,md5Checksum',supportsAllDrives:true},{timeout:180000});
      remote=response.data;
    }
    job.drive=remote;job.driveStatus='done';delete job.lastError;
  } catch(e) {
    job.driveStatus='pending';job.attempts++;
    job.retryAt=Date.now()+Math.min(300000,5000*2**Math.min(job.attempts,6));
    job.lastError=driveErrorMessage(e);console.error('Drive backup failed:',e.message);
  } finally {
    driveActive--;
    await atomicJSON(path.join(DATA,'jobs',job.id+'.json'),job);
  }
}
function scheduleBackups() {
  if(!drive) return;
  for(const job of jobs.values()) {
    if(driveActive>=DRIVE_WORKERS) break;
    if(job.driveStatus==='pending' && job.retryAt<=Date.now()) backup(job).catch(console.error);
  }
}
const queryUpload=multer({dest:path.join(os.tmpdir(),'wedding-selfie'),limits:{files:1,fileSize:MAX_FILE_SIZE}});
let searches=0;
function searchAdmission(req,res,next) {
  if(searches>=4){res.set('Retry-After','3');return res.status(429).json({ok:false,message:'搜尋人數較多，請稍後再試'});}
  searches++;let released=false;
  const release=()=>{if(!released){released=true;searches--;}};
  res.once('finish',release);res.once('close',release);next();
}
app.post('/api/face/find',searchAdmission,queryUpload.single('file'),async(req,res)=>{
  const disconnected=new AbortController();
  const onClose=()=>{if(!res.writableEnded)disconnected.abort();};
  res.once('close',onClose);
  try {
    if(!req.file) return res.status(400).json({ok:false,message:'請選擇自拍照'});
    const form=new FormData();
    form.append('file',new Blob([await fsp.readFile(req.file.path)],{type:req.file.mimetype}),req.file.originalname);
    const upstream=await fetch(FACE_URL+'/find',{method:'POST',body:form,signal:AbortSignal.any([AbortSignal.timeout(120000),disconnected.signal])});
    res.status(upstream.status).type('json').send(await upstream.text());
  } catch(e) {if(!res.destroyed)res.status(503).json({ok:false,message:'分析服務暫時無法使用，請稍後再試'});}
  finally {res.off('close',onClose);if(req.file) await fsp.unlink(req.file.path).catch(()=>{});}
});
async function proxy(req,res,route) {
  try {
    const upstream=await fetch(FACE_URL+route,{signal:AbortSignal.timeout(30000)});
    res.status(upstream.status).type(upstream.headers.get('content-type')||'application/json');
    if(route.includes('/thumbs/') || route.endsWith('/image')) res.set('Cache-Control','public, max-age=86400');
    const {Readable}=require('stream');Readable.fromWeb(upstream.body).pipe(res);
  } catch(e) {res.status(503).json({ok:false,message:'分析服務尚未啟動，請稍後再試'});}
}
app.get('/api/face/status',(req,res)=>proxy(req,res,'/health'));
app.get('/api/face/people',(req,res)=>proxy(req,res,'/people'));
app.get('/api/face/people/:id/photos',(req,res)=>proxy(req,res,'/people/'+encodeURIComponent(req.params.id)+'/photos'));
app.get('/api/face/thumbs/:filename',(req,res)=>proxy(req,res,'/thumbs/'+encodeURIComponent(req.params.filename)));
app.get('/api/photos/:id/image',(req,res)=>proxy(req,res,'/photo/'+encodeURIComponent(req.params.id)+'/image'));
let activeDownloads=0;
async function streamOriginal(req,res,client=drive){
  const job=jobs.get(req.params.id);if(!job)return res.status(404).send('找不到照片');
  if(activeDownloads>=8)return res.status(429).set('Retry-After','5').send('下載人數較多，請稍後再試');
  if(job.driveStatus!=='done'||!job.drive?.id||!client)return res.status(409).send('原圖尚在上傳雲端，請稍後下載');
  activeDownloads++;
  try {
    const remote=await client.files.get({fileId:job.drive.id,alt:'media',supportsAllDrives:true},{responseType:'stream',timeout:180000});
    res.attachment(job.name);res.type(job.mimeType);
    await require('stream/promises').pipeline(remote.data,res);
  }catch(e){if(!res.headersSent)res.status(e.code===404?404:503).send('目前無法從雲端下載原圖，照片可能已移除，請聯絡新人。');}
  finally{activeDownloads--;}
}
app.get('/api/photos/:id/original',(req,res)=>streamOriginal(req,res));
// Node alone writes manifests. Face service owns its index. Cleanup requires BOTH durable index and verified cloud copy.
let cleanupBusy=false;
async function cleanupOriginals(client=drive){
  if(cleanupBusy||!client)return;
  cleanupBusy=true;
  try{
    let index;try{index=JSON.parse(await fsp.readFile(path.join(DATA,'face_index.json'),'utf8'));}catch{return;}
    analyzedIds=new Set(Object.keys(index.photos||{}));
    if(process.env.KEEP_LOCAL_ORIGINALS==='true')return;
    let checked=0;
    for(const job of jobs.values()){
      if(job.originalReleased){await fsp.unlink(path.join(DATA,'originals',job.file)).catch(()=>{});continue;}
      if((cleanupRetryAt.get(job.id)||0)>Date.now())continue;
      if(job.driveStatus!=='done'||!job.drive?.id||!index.photos?.[job.id])continue;
      if(++checked>20)break;
      cleanupRetryAt.set(job.id,Date.now()+300000);
      try{
        const reply=await client.files.get({fileId:job.drive.id,fields:'id,trashed,md5Checksum',supportsAllDrives:true},{timeout:30000});
        if(reply.data.trashed || (job.drive.md5Checksum && reply.data.md5Checksum!==job.drive.md5Checksum))continue;
        // Mark first: on a crash, repeat unlink on next startup; importer must not redownload released originals.
        job.originalReleased=true;
        await atomicJSON(path.join(DATA,'jobs',job.id+'.json'),job);
        await fsp.unlink(path.join(DATA,'originals',job.file)).catch(e=>{if(e.code!=='ENOENT')throw e;});
      }catch(e){console.error('暫存清理保留原圖：',job.id, e.message);}
    }
  }finally{cleanupBusy=false;}
}
app.use(async(err,req,res,next)=>{
  await Promise.allSettled((req.files||[req.file].filter(Boolean)).map(f=>fsp.unlink(f.path)));
  if(res.headersSent) return next(err);
  res.status(400).json({ok:false,message:err instanceof multer.MulterError?'每批最多 5 張、每張最多 25 MB':err.message});
});
async function main() {
  if(Number(process.versions.node.split('.')[0])<22)throw new Error('Please use Node.js 22 or newer');
  for(const d of ['jobs','originals','thumbs','previews'])await fsp.mkdir(path.join(DATA,d),{recursive:true});
  for(const name of await fsp.readdir(path.join(DATA,'jobs'))) {
    if(!name.endsWith('.json'))continue;
    const job=JSON.parse(await fsp.readFile(path.join(DATA,'jobs',name),'utf8'));
    if(job.driveStatus==='uploading')job.driveStatus='pending';
    if(DRIVE_ENABLED && job.driveStatus==='disabled')job.driveStatus='pending';
    jobs.set(job.id,job);
    if(job.originalReleased)await fsp.unlink(path.join(DATA,'originals',job.file)).catch(()=>{});
  }
  await driveSync.init();
  // Local uploads and face albums remain usable even while Google OAuth starts.
  const server=app.listen(PORT,()=>console.log(`💛 上傳 http://localhost:${PORT}/photos.html\n🔎 人物相簿 http://localhost:${PORT}/people.html\n🛠 整理人物 http://localhost:${PORT}/admin-people.html`));
  server.requestTimeout=15*60*1000;
  if(DRIVE_ENABLED) authorize().then(auth=>{
    drive=google.drive({version:'v3',auth});driveConnectionState='ready';driveConnectionError=null;driveSync.start();scheduleBackups();
    setInterval(scheduleBackups,3000).unref();
    setInterval(()=>cleanupOriginals().catch(console.error),30000).unref();
  }).catch(e=>{driveConnectionState='failed';driveConnectionError=driveErrorMessage(e);console.error('Google 授權失敗，照片已保存在本機。修正授權後重啟即可繼續備份：',e.message);});
}
if(require.main===module)main().catch(e=>{console.error(e);process.exit(1);});
module.exports={app,main,persistPhoto,driveSync,cleanupOriginals,streamOriginal,backupPhoto:(id,client)=>backup(jobs.get(id),client)};
