// Offline backfill original source. stdout is binary; stderr contains safe JSON metadata.
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {pipeline} from 'node:stream/promises';

const require=createRequire(import.meta.url);
const createBackfillOriginals=require('./backfill-originals');
const {BackfillOriginalError}=createBackfillOriginals;
const interrupted=new AbortController();
const onSignal=()=>interrupted.abort();
process.once('SIGINT',onSignal);process.once('SIGTERM',onSignal);
let original;
try{
  const args=process.argv.slice(2),options={};
  for(let i=0;i<args.length;i++){
    const flag=args[i];
    if(!['--data','--photo-id','--allow-drive','--local-only'].includes(flag)||Object.hasOwn(options,flag))throw new BackfillOriginalError('invalid','回填原檔指令參數不正確');
    if(['--allow-drive','--local-only'].includes(flag)){options[flag]=true;continue;}
    const value=args[++i];
    if(!value||value.startsWith('--'))throw new BackfillOriginalError('invalid','回填原檔指令缺少參數');
    options[flag]=value;
  }
  const data=options['--data'],photoId=options['--photo-id'];
  if(typeof data!=='string'||!path.isAbsolute(data)||typeof photoId!=='string'||!/^[a-f0-9]{64}$/i.test(photoId)||
      (options['--allow-drive']&&options['--local-only']))throw new BackfillOriginalError('invalid','請提供絕對資料路徑與有效照片識別；Drive 必須明確啟用');
  let drive=null;
  if(options['--allow-drive']){
    // Reuse the saved credential only. No interactive authorize, token save, or .env loading.
    try{
      const token=JSON.parse(await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)),'token.json'),'utf8'));
      const {google}=require('googleapis');
      drive=google.drive({version:'v3',auth:google.auth.fromJSON(token)});
    }catch{drive=null;}
  }
  const originals=createBackfillOriginals({data,getDrive:()=>drive,
    maxBytes:Number(process.env.FACE_BACKFILL_MAX_BYTES||100*1024*1024),
    getJob:async id=>{
      try{return JSON.parse(await readFile(path.join(data,'jobs',id+'.json'),'utf8'));}
      catch(error){if(error.code==='ENOENT')return null;throw error;}
    }});
  original=await originals.acquire(photoId);
  if(interrupted.signal.aborted)throw new BackfillOriginalError('retryable','原檔取得已取消');
  process.stderr.write(JSON.stringify({source:original.source,sha256:original.sha256,bytes:original.bytes})+'\n');
  await pipeline(createReadStream(original.path),process.stdout,{signal:interrupted.signal});
}catch(error){
  const known=error instanceof BackfillOriginalError;
  process.stderr.write(JSON.stringify({code:known?error.code:'retryable',message:known?error.message:'暫時無法取得回填原檔'})+'\n');
  process.exitCode=known&&error.code==='invalid'?2:1;
}finally{
  await original?.cleanup().catch(()=>{});
  process.off('SIGINT',onSignal);process.off('SIGTERM',onSignal);
}
