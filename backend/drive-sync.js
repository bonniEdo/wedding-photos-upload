// Read-only Google Drive importer. Uses the existing local photo/analysis queue.
const fs=require('fs');
const fsp=require('fs/promises');
const path=require('path');
const crypto=require('crypto');
const {Transform}=require('stream');
const {pipeline}=require('stream/promises');
const TYPES={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp','.heic':'image/heic','.heif':'image/heif'};
function mimeFor(file){return Object.values(TYPES).includes(file.mimeType)?file.mimeType:TYPES[path.extname(file.name||'').toLowerCase()];}
function fingerprint(file){return file.md5Checksum||`${file.modifiedTime||''}:${file.size||''}`;}
function quote(value){return String(value).replace(/\\/g,'\\\\').replace(/'/g,"\\'");}
module.exports=function createDriveSync({data,folderId,getDrive,persistPhoto,atomicJSON,
  getKnownLocal=()=>null,enabled=true,subfolders=true,intervalSeconds=300,maxBytes=100*1024*1024,isUploadBusy=()=>false,logger=console}){
  const stateFile=path.join(data,'drive_sync.json'),tmpDir=path.join(data,'drive-tmp');
  let state={version:1,scopeKey:JSON.stringify([folderId,subfolders]),files:{},lastScanAt:null,lastScanError:null};
  let scanPromise=null,pumpPromise=null,saveChain=Promise.resolve(),timers=[],stopped=false;
  let runtime={scanning:false,downloading:false,currentName:null};
  function save(){saveChain=saveChain.catch(()=>{}).then(()=>atomicJSON(stateFile,state));return saveChain;}
  async function init(){
    await fsp.mkdir(tmpDir,{recursive:true});
    try{state=JSON.parse(await fsp.readFile(stateFile,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
    const scopeKey=JSON.stringify([folderId,subfolders]);
    if(state.scopeKey!==scopeKey)state={version:1,scopeKey,files:{},lastScanAt:null,lastScanError:null};
    for(const f of Object.values(state.files))if(f.status==='downloading')f.status='pending';
    for(const name of await fsp.readdir(tmpDir))if(name.endsWith('.part'))await fsp.unlink(path.join(tmpDir,name));
  }
  function status(){
    const records=Object.values(state.files);
    return {ok:true,enabled,folderUrl:folderId?'https://drive.google.com/drive/folders/'+encodeURIComponent(folderId):null,ready:Boolean(getDrive()),...runtime,subfolders,intervalSeconds,
      discovered:records.length,imported:records.filter(r=>r.status==='done').length,
      pending:records.filter(r=>['pending','downloading'].includes(r.status)).length,
      failed:records.filter(r=>r.status==='failed').length,skipped:records.filter(r=>r.status==='skipped').length,
      lastScanAt:state.lastScanAt,lastScanError:state.lastScanError,
      problems:records.filter(r=>['failed','skipped'].includes(r.status)).slice(0,20).map(r=>({name:r.file.name,message:r.message}))};
  }
  function localReady(id,file){
    try {const job=JSON.parse(fs.readFileSync(path.join(data,'jobs',id+'.json'),'utf8'));
      return job.originalReleased || Boolean(file && fs.existsSync(path.join(data,'originals',file)));
    }catch{return false;}
  }
  function scan(){
    if(scanPromise)return scanPromise;
    if(!enabled||!getDrive())return Promise.reject(new Error('Google Drive 尚未連線，請先完成授權並啟用 Drive'));
    runtime.scanning=true;
    scanPromise=(async()=>{
      try{
        const folders=[folderId],visited=new Set();
        while(folders.length&&!stopped){
          const folder=folders.shift();if(visited.has(folder))continue;visited.add(folder);
          let pageToken;
          do{
            const reply=await getDrive().files.list({q:`'${quote(folder)}' in parents and trashed=false`,
              fields:'nextPageToken,incompleteSearch,files(id,name,mimeType,size,md5Checksum,modifiedTime,webViewLink,appProperties,capabilities(canDownload))',
              pageSize:1000,pageToken,supportsAllDrives:true,includeItemsFromAllDrives:true},{timeout:60000});
            if(reply.data.incompleteSearch)throw new Error('Drive 回傳不完整清單，稍後將重新掃描');
            for(const file of reply.data.files||[]){
              if(file.mimeType==='application/vnd.google-apps.folder'){if(subfolders)folders.push(file.id);continue;}
              if(!mimeFor(file))continue;
              const prior=state.files[file.id];
              // A completed local import may be skipped only while its manifest and original exist.
              const complete=prior?.status==='done'&&prior.localPhotoId&&localReady(prior.localPhotoId,prior.localFile);
              const sig=fingerprint(file);
              const tooLarge=Number(file.size||0)>maxBytes;
              const noPermission=file.capabilities?.canDownload===false;
              if(tooLarge||noPermission){state.files[file.id]={file,fingerprint:sig,status:'skipped',message:tooLarge?`照片超過 ${Math.round(maxBytes/1024/1024)} MB，未下載`:'Google Drive 未允許下載這張照片'};continue;}
              if(prior?.fingerprint===sig&&complete)continue;
              const known=getKnownLocal(file);
              if(known && localReady(known.id,known.localFile)){state.files[file.id]={file,fingerprint:sig,status:'done',localPhotoId:known.id,localFile:known.localFile,duplicate:true,importedAt:Date.now()};continue;}
              if(prior?.fingerprint===sig&&['pending','downloading','failed'].includes(prior.status)){prior.file=file;continue;}
              state.files[file.id]={file,fingerprint:sig,status:'pending',attempts:0,retryAt:0};
            }
            await save();pageToken=reply.data.nextPageToken;
          }while(pageToken&&!stopped);
        }
        if(!stopped){state.lastScanAt=Date.now();state.lastScanError=null;await save();}
      }catch(e){state.lastScanError='讀取 Drive 清單失敗：'+e.message;await save();throw e;}
      finally{runtime.scanning=false;scanPromise=null;}
    })();
    return scanPromise;
  }
  async function download(record){
    const file=record.file,sig=record.fingerprint;
    const temp=path.join(tmpDir,crypto.randomUUID()+'.part');
    record.status='downloading';runtime.downloading=true;runtime.currentName=file.name;
    try{
      await save();
      const reply=await getDrive().files.get({fileId:file.id,alt:'media',supportsAllDrives:true},{responseType:'stream',timeout:120000});
      let bytes=0;const md5=crypto.createHash('md5');
      const limiter=new Transform({transform(chunk,encoding,done){bytes+=chunk.length;if(bytes>maxBytes){const e=new Error('實際檔案大小超過下載限制');e.code='PHOTO_TOO_LARGE';done(e);return;}md5.update(chunk);done(null,chunk);}});
      await pipeline(reply.data,limiter,fs.createWriteStream(temp,{flags:'wx'}));
      if(file.md5Checksum&&md5.digest('hex')!==file.md5Checksum)throw new Error('下載檔案與 Drive 校驗不一致，將重試');
      const result=await persistPhoto({path:temp,originalname:file.name,mimetype:mimeFor(file)},file);
      // Do not overwrite a newer Drive revision discovered while downloading.
      if(state.files[file.id]!==record||record.fingerprint!==sig)return;
      record.status='done';record.localPhotoId=result.id;record.localFile=result.localFile;
      record.duplicate=Boolean(result.duplicate);record.importedAt=Date.now();delete record.message;
      await save();
    }catch(e){
      if(state.files[file.id]===record){
        record.attempts=(record.attempts||0)+1;record.status=e.code==='PHOTO_TOO_LARGE'?'skipped':'failed';
        record.retryAt=Date.now()+Math.min(300000,10000*2**Math.min(record.attempts,5));
        record.message='下載失敗：'+e.message;await save();
      }
      logger.error('Drive import failed:',file.name,e.message);
    }finally{await fsp.unlink(temp).catch(()=>{});runtime.downloading=false;runtime.currentName=null;}
  }
  function pump(){
    if(pumpPromise)return pumpPromise;
    if(stopped||!enabled||!getDrive()||isUploadBusy())return Promise.resolve(false);
    const record=Object.values(state.files).find(r=>['pending','failed'].includes(r.status)&&(!r.retryAt||r.retryAt<=Date.now()));
    if(!record)return Promise.resolve(false);
    pumpPromise=download(record).then(()=>true).finally(()=>{pumpPromise=null;});return pumpPromise;
  }
  function requestScan(){
    if(!enabled||!getDrive())return {ok:false,message:'Google Drive 尚未連線，請先完成授權；DRIVE_ENABLED 與 DRIVE_SYNC_ENABLED 需啟用'};
    scan().catch(e=>logger.error('Drive scan failed:',e.message));return {ok:true,message:'已開始背景同步',status:status()};
  }
  function start(){if(!enabled||timers.length)return;stopped=false;requestScan();
    timers.push(setInterval(()=>requestScan(),Math.max(10,intervalSeconds)*1000));
    timers.push(setInterval(()=>pump().catch(e=>logger.error('Drive import queue:',e.message)),1000));
    for(const t of timers)t.unref();
  }
  async function stop(){stopped=true;timers.forEach(clearInterval);timers=[];await Promise.allSettled([scanPromise,pumpPromise].filter(Boolean));}
  return {init,status,scan,pump,requestScan,start,stop};
};
