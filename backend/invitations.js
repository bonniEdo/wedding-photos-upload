// RSVP records and memories are private files, separate from public photo albums.
const express = require('express');
const multer = require('multer');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

module.exports = function invitations({data,googleForms=require('./google-forms')({data})}) {
  const router = express.Router();
  const root = path.join(data,'rsvps');
  const pending = new Map();
  let active = 0;
  const upload = multer({storage:multer.memoryStorage(),limits:{files:3,fileSize:10*1024*1024,fields:10,fieldSize:10000,parts:13},
    fileFilter(req,file,cb) {
      if (['image/jpeg','image/png','image/webp'].includes(file.mimetype)) cb(null,true);
      else cb(new Error('回憶照片僅接受 JPG、PNG 或 WebP。'));
    },
  }).array('photos',3);
  const invalid = message => Object.assign(new Error(message),{status:400});
  function text(body,key,max,required=false) {
    if (body[key] !== undefined && typeof body[key] !== 'string') throw invalid('回覆欄位格式不正確。');
    const value = (body[key] || '').trim();
    if (required && !value) throw invalid('請填寫姓名。');
    if (value.length > max) throw invalid('填寫內容太長，請縮短後再試。');
    return value;
  }
  function imageExtension(file) {
    const b = file.buffer;
    if (file.mimetype==='image/jpeg' && b.length>3 && b[0]===255 && b[1]===216 && b[2]===255) return '.jpg';
    if (file.mimetype==='image/png' && b.length>8 && b.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return '.png';
    if (file.mimetype==='image/webp' && b.length>12 && b.toString('ascii',0,4)==='RIFF' && b.toString('ascii',8,12)==='WEBP') return '.webp';
    throw invalid('照片內容與格式不符，請重新選擇 JPG、PNG 或 WebP。');
  }
  async function save(req) {
    const body = req.body || {};
    const id = text(body,'requestId',36);
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(id)) throw invalid('回覆識別碼不正確，請重新整理後再試。');
    if (!['yes','no'].includes(body.attendance)) throw invalid('請選擇是否出席。');
    const attending = body.attendance==='yes';
    const count = attending ? Number(body.guestCount) : 0;
    const vegetarianCount = attending ? Number(body.vegetarianCount) : 0;
    const childSeats = attending ? Number(body.childSeats ?? 0) : 0;
    if (!Number.isInteger(count) || count < (attending?1:0) || count>20) throw invalid('出席人數須為 1 至 20 位。');
    if (!Number.isInteger(vegetarianCount) || vegetarianCount<0 || vegetarianCount>count) throw invalid('素食人數不能超過出席人數。');
    if (!Number.isInteger(childSeats) || childSeats<0 || childSeats>count) throw invalid('兒童座椅數量須介於 0 與出席人數之間。');
    const files = req.files || [];
    if (!attending && files.length) throw invalid('只有出席回覆可附上回憶照片。');
    const record = {id,guestName:text(body,'guestName',80,true),attending,guestCount:count,vegetarianCount,...(childSeats?{childSeats}:{}),
      dietNotes:attending?text(body,'dietNotes',300):'',message:text(body,'message',2000),
      photos:files.map((file,i) => ({file:`memory-${i+1}${imageExtension(file)}`,originalName:path.basename(file.originalname).slice(0,200),size:file.size,mimeType:file.mimetype,sha256:crypto.createHash('sha256').update(file.buffer).digest('hex')})),
    };
    const fingerprint = crypto.createHash('sha256').update(JSON.stringify(record)).digest('hex');
    // Same request ID is safe to retry after an interrupted response, including concurrent requests.
    if (pending.has(id)) await pending.get(id).catch(()=>{});
    const operation = (async () => {
      const target = path.join(root,id);
      try {
        const prior = JSON.parse(await fs.readFile(path.join(target,'response.json'),'utf8'));
        if (prior.fingerprint!==fingerprint) throw Object.assign(new Error('這個回覆編號已使用，請重新整理後再送出。'),{status:409});
        return {ok:true,id};
      } catch (error) {if(error.code!=='ENOENT')throw error;}
      await fs.mkdir(root,{recursive:true,mode:0o700});
      const temporary = await fs.mkdtemp(path.join(root,'.pending-'));
      try {
        for (let i=0;i<files.length;i++) await fs.writeFile(path.join(temporary,record.photos[i].file),files[i].buffer,{mode:0o600});
        await fs.writeFile(path.join(temporary,'response.json'),JSON.stringify({...record,fingerprint,createdAt:new Date().toISOString()},null,2),{mode:0o600});
        await fs.rename(temporary,target);
      } finally {await fs.rm(temporary,{recursive:true,force:true});}
      return {ok:true,id};
    })();
    pending.set(id,operation);
    try {return await operation;} finally {if(pending.get(id)===operation)pending.delete(id);}
  }
  router.post('/rsvp',(req,res) => {
    if (active>=4) return res.set('Retry-After','3').status(429).json({ok:false,message:'目前回覆人數較多，請稍候再寄出。'});
    active++;
    let released = false;
    const release = () => {if(!released){released=true;active--;}};
    upload(req,res,async error => {
      try {
        if (error) throw Object.assign(error,{status:400});
        const result = await save(req);
        const sync = await googleForms.sync(result.id);
        res.set('Cache-Control','no-store').status(201).json({...result,googleForms:sync});
      } catch (error) {
        if (!error.status) console.error('RSVP save failed:',error.message);
        const message = error instanceof multer.MulterError ? '最多 3 張照片，每張 10 MB；請確認欄位與照片大小。' : error.status ? error.message : '回覆儲存失敗，請稍後重試。';
        res.status(error.status || 500).json({ok:false,message,...(['GOOGLE_FORMS_PENDING','GOOGLE_FORMS_UNCERTAIN'].includes(error.code)?{code:error.code,localSaved:true}:{})});
      } finally {release();}
    });
  });
  return router;
};
