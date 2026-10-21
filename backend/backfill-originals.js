// Read-only original retrieval for face backfill. Never changes import or job state.
const fs=require('fs');
const fsp=require('fs/promises');
const path=require('path');
const crypto=require('crypto');
const {Readable,Transform}=require('stream');
const {pipeline}=require('stream/promises');

class BackfillOriginalError extends Error {
  constructor(code,message){super(message);this.name='BackfillOriginalError';this.code=code;}
}
const fail=(code,message)=>new BackfillOriginalError(code,message);
const SHA256=/^[a-f0-9]{64}$/i,MD5=/^[a-f0-9]{32}$/i;
const FIELDS='id,trashed,size,md5Checksum,version,modifiedTime,capabilities(canDownload)';

function checksum(value,pattern,label){
  if(value===undefined||value===null||value==='')return null;
  if(typeof value!=='string'||!pattern.test(value))throw fail('invalid',`${label} 格式不正確，未讀取原檔`);
  return value.toLowerCase();
}
function safeFilename(value){
  return typeof value==='string'&&value.length>0&&value!=='.'&&value!=='..'&&
    path.basename(value)===value&&!/[\\/\0]/.test(value);
}
function apiError(error){
  if(error instanceof BackfillOriginalError)return error;
  if(['EACCES','EPERM'].includes(error?.code))return fail('unavailable','目前沒有讀取或暫存原檔的權限');
  const status=Number(error?.response?.status||error?.status||error?.code);
  if([401,403,404,410].includes(status))return fail('unavailable','Google Drive 原檔不存在，或目前沒有下載權限');
  return fail('retryable','暫時無法取得 Google Drive 原檔，請稍後重試');
}

function createBackfillOriginals({data,getDrive,getJob,maxBytes=100*1024*1024,isUploadBusy=()=>false}){
  if(typeof data!=='string'||typeof getDrive!=='function'||typeof getJob!=='function')throw new TypeError('data, getDrive and getJob are required');
  if(!Number.isSafeInteger(maxBytes)||maxBytes<=0)throw new TypeError('maxBytes must be a positive safe integer');
  let queue=Promise.resolve();

  async function directory(parent,name,create=false){
    const target=path.join(parent,name);
    if(create)await fsp.mkdir(target,{mode:0o700}).catch(error=>{if(error.code!=='EEXIST')throw error;});
    const stat=await fsp.lstat(target);
    if(!stat.isDirectory()||stat.isSymbolicLink())throw fail('invalid','原檔資料路徑不安全，未讀取原檔');
    return target;
  }
  async function tempFile(root){
    const base=await directory(root,'face-backfill',true),tmp=await directory(base,'tmp',true);
    const target=path.join(tmp,crypto.randomUUID()+'.part');
    // wx plus a non-symlink parent ensures an existing path is never overwritten.
    const handle=await fsp.open(target,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_NOFOLLOW,0o600);
    return {target,handle};
  }
  async function stage(root,input,expected){
    let target,handle;
    try{
      ({target,handle}=await tempFile(root));
      let bytes=0;
      const sha=crypto.createHash('sha256'),md5=crypto.createHash('md5');
      const limiter=new Transform({transform(chunk,encoding,done){
        bytes+=chunk.length;
        if(bytes>maxBytes){done(fail('unavailable','原檔超過回填下載大小限制，未使用此檔案'));return;}
        sha.update(chunk);md5.update(chunk);done(null,chunk);
      }});
      await pipeline(input,limiter,handle.createWriteStream({autoClose:true}));
      handle=null;
      const sha256=sha.digest('hex'),md5sum=md5.digest('hex');
      if(expected.size!==undefined&&bytes!==expected.size)throw fail('changed','原檔大小與檔案紀錄不一致，未使用此檔案');
      if(expected.sha256&&sha256!==expected.sha256)throw fail('changed','原檔內容與原照片紀錄不一致，未使用此檔案');
      if(expected.md5&&md5sum!==expected.md5)throw fail('changed','原檔內容與 Google Drive 校驗不一致，未使用此檔案');
      let cleaned=false;
      return {path:target,sha256,bytes,md5:md5sum,cleanup:async()=>{
        if(cleaned)return;await fsp.unlink(target).catch(error=>{if(error.code!=='ENOENT')throw error;});cleaned=true;
      }};
    }catch(error){
      input.destroy?.();
      if(handle)await handle.close().catch(()=>{});
      if(target)await fsp.unlink(target).catch(()=>{});
      throw error;
    }
  }
  async function local(root,job,expected){
    let originals;
    try{originals=await directory(root,'originals');}catch(error){if(error.code==='ENOENT')return null;throw error;}
    const filename=path.join(originals,job.file);
    let handle;
    try{
      // Refuse symlinks even if their target would currently be within originals.
      handle=await fsp.open(filename,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);
    }catch(error){
      if(error.code==='ENOENT')return null;
      if(error.code==='ELOOP')throw fail('invalid','原檔路徑包含符號連結，未讀取原檔');
      throw error;
    }
    let result;
    try{
      const before=await handle.stat({bigint:true});
      if(!before.isFile())throw fail('invalid','原檔路徑不是一般檔案，未讀取原檔');
      if(before.size>BigInt(maxBytes))throw fail('unavailable','原檔超過回填下載大小限制，未使用此檔案');
      const chunks=async function*(){
        const buffer=Buffer.alloc(64*1024);let position=0;
        while(true){
          const {bytesRead}=await handle.read(buffer,0,buffer.length,position);
          if(!bytesRead)return;
          position+=bytesRead;yield Buffer.from(buffer.subarray(0,bytesRead));
        }
      };
      result=await stage(root,Readable.from(chunks()),{...expected,size:Number(before.size)});
      const after=await handle.stat({bigint:true});
      if(before.size!==after.size||before.mtimeNs!==after.mtimeNs||before.ctimeNs!==after.ctimeNs)throw fail('changed','讀取期間原檔已變更，未使用此檔案');
      return {...result,source:'local'};
    }catch(error){await result?.cleanup();throw error;}
    finally{await handle.close().catch(()=>{});}
  }
  function metadata(file,id,after=false){
    if(!file||file.id!==id)throw fail('changed','Google Drive 原檔識別已變更，未使用此檔案');
    if(file.trashed)throw fail(after?'changed':'unavailable','Google Drive 原檔已移至垃圾桶，未使用此檔案');
    if(file.capabilities?.canDownload===false)throw fail('unavailable','Google Drive 未允許下載此原檔');
    const size=Number(file.size);
    const validSize=typeof file.size==='number'||(typeof file.size==='string'&&/^\d+$/.test(file.size));
    if(typeof file.trashed!=='boolean'||!validSize||!Number.isSafeInteger(size)||size<0||
        typeof file.md5Checksum!=='string'||!MD5.test(file.md5Checksum)||
        (!file.version&&!file.modifiedTime))throw fail('unavailable','Google Drive 缺少原檔校驗或版本資訊，無法安全回填');
    if(size>maxBytes)throw fail('unavailable','原檔超過回填下載大小限制，未使用此檔案');
    return {id:file.id,trashed:Boolean(file.trashed),size,md5:file.md5Checksum.toLowerCase(),version:String(file.version||''),modifiedTime:String(file.modifiedTime||'')};
  }
  async function cloud(root,job,expected){
    const id=job.drive?.id;
    if(typeof id!=='string'||!id.trim())throw fail('unavailable','本機沒有保留原檔，且照片沒有 Google Drive 原檔紀錄');
    if(!expected.sha256&&!expected.md5)throw fail('unavailable','舊紀錄缺少原檔內容校驗，無法確認雲端檔案仍是原照片');
    const drive=getDrive();
    if(!drive?.files?.get)throw fail('unavailable','本機沒有保留原檔，且 Google Drive 尚未連線');
    if(await isUploadBusy())throw fail('retryable','照片上傳或同步正在進行，請稍後重試原檔回填');
    let result;
    try{
      const getMetadata=async()=>((await drive.files.get({fileId:id,fields:FIELDS,supportsAllDrives:true},{timeout:30000})).data);
      const before=metadata(await getMetadata(),id);
      if(expected.md5&&expected.md5!==before.md5)throw fail('changed','Google Drive 原檔內容已與照片紀錄不同，未下載');
      const response=await drive.files.get({fileId:id,alt:'media',supportsAllDrives:true},{responseType:'stream',timeout:120000});
      if(!response?.data||typeof response.data.pipe!=='function')throw fail('retryable','Google Drive 暫時未提供可讀取的原檔');
      result=await stage(root,response.data,{...expected,size:before.size,md5:before.md5});
      const after=metadata(await getMetadata(),id,true);
      if(JSON.stringify(before)!==JSON.stringify(after))throw fail('changed','下載期間 Google Drive 原檔版本已變更，未使用此檔案');
      return {...result,source:'drive'};
    }catch(error){await result?.cleanup();throw apiError(error);}
  }
  async function acquireOne(photoId){
    if(typeof photoId!=='string'||!SHA256.test(photoId))throw fail('invalid','照片識別格式不正確');
    let job;
    try{job=await getJob(photoId);}catch{throw fail('retryable','暫時無法讀取照片紀錄，請稍後重試');}
    if(!job)throw fail('unavailable','找不到照片的原檔紀錄');
    if((job.id!==undefined&&job.id!==photoId)||!safeFilename(job.file))throw fail('invalid','照片原檔紀錄格式不正確，未讀取原檔');
    // Snapshot only the fields we need; the live job and release/import flags are untouched.
    job={id:job.id,file:job.file,contentHash:job.contentHash,drive:job.drive?{id:job.drive.id,md5Checksum:job.drive.md5Checksum}:null};
    const expected={sha256:checksum(job.contentHash,SHA256,'原檔 SHA-256'),md5:checksum(job.drive?.md5Checksum,MD5,'原檔 MD5')};
    try{
      const root=await fsp.realpath(data);
      const result=await local(root,job,expected)||await cloud(root,job,expected);
      // Internal MD5 is used only for validation, not part of the public contract.
      const {md5,...publicResult}=result;return publicResult;
    }catch(error){
      if(error instanceof BackfillOriginalError)throw error;
      if(['EACCES','EPERM'].includes(error.code))throw fail('unavailable','目前沒有讀取或暫存原檔的權限');
      throw fail('retryable','暫時無法讀取或暫存原檔，請稍後重試');
    }
  }
  function acquire(photoId){
    const operation=queue.then(()=>acquireOne(photoId));
    queue=operation.then(()=>{},()=>{});
    return operation;
  }
  return {acquire};
}
module.exports=createBackfillOriginals;
module.exports.createBackfillOriginals=createBackfillOriginals;
module.exports.BackfillOriginalError=BackfillOriginalError;
