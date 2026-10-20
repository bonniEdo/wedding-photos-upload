const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

// Only the backend knows the Apps Script URL and shared secret.
module.exports = function googleForms({data,url=process.env.WEDDING_GOOGLE_FORMS_URL||'',secret=process.env.WEDDING_GOOGLE_FORMS_SECRET||'',fetchImpl=fetch}) {
  if (!url && !secret) return {enabled:false,sync:async()=>({enabled:false})};
  let endpoint;
  try {endpoint=new URL(url);} catch {throw new Error('請設定正確的 WEDDING_GOOGLE_FORMS_URL。');}
  if (endpoint.origin!=='https://script.google.com' || !/^\/macros\/s\/[a-zA-Z0-9_-]+\/exec$/.test(endpoint.pathname) || endpoint.search || endpoint.hash || endpoint.username || endpoint.password) throw new Error('Google 表單網址必須是 Apps Script 的 https://script.google.com/macros/s/…/exec 部署網址。');
  if (secret.length<32) throw new Error('請設定至少 32 字元的 WEDDING_GOOGLE_FORMS_SECRET，並與 Apps Script 保持一致。');
  const endpointHash=crypto.createHash('sha256').update(url).digest('hex');
  const inFlight=new Map();
  async function post(payload) {
    try {
      const response=await fetchImpl(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,secret}),signal:AbortSignal.timeout(25000)});
      const result=await response.json();
      if (!response.ok || !result?.ok || result.id!==payload.id) throw new Error('invalid response');
      return result;
    } catch {
      throw Object.assign(new Error('回覆已保留，但尚未確認同步到 Google 表單。請稍候再次寄出，系統會避免重複登記。'),{status:502,code:'GOOGLE_FORMS_PENDING'});
    }
  }
  async function deliver(id) {
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(id)) throw new Error('Invalid RSVP ID');
    const dir=path.join(data,'rsvps',id);
    const record=JSON.parse(await fs.readFile(path.join(dir,'response.json'),'utf8'));
    const statePath=path.join(dir,'google-forms.json');
    try {
      const state=JSON.parse(await fs.readFile(statePath,'utf8'));
      if (state.endpointHash===endpointHash && state.fingerprint===record.fingerprint && state.responseId) return {enabled:true,synced:true};
    } catch(error) {if(error.code!=='ENOENT')throw error;}
    const photos=[];
    // Upload one image per request; never put all base64 images in one large JSON body.
    for (const [index,photo] of record.photos.entries()) {
      const bytes=await fs.readFile(path.join(dir,photo.file));
      const uploaded=await post({action:'upload',id,fingerprint:record.fingerprint,index,
        photo:{name:photo.originalName,mimeType:photo.mimeType,sha256:photo.sha256,base64:bytes.toString('base64')}});
      if (typeof uploaded.fileId!=='string' || !/^[a-zA-Z0-9_-]+$/.test(uploaded.fileId)) throw Object.assign(new Error('照片已保留，但 Google 雲端照片尚未確認，請再次寄出。'),{status:502});
      photos.push({fileId:uploaded.fileId,index,sha256:photo.sha256});
    }
    const result=await post({action:'submit',id,record:{...record,photos}});
    if (typeof result.responseId!=='string' || !result.responseId) throw Object.assign(new Error('尚未確認 Google 表單回覆編號，請再次寄出。'),{status:502});
    const temporary=`${statePath}.${crypto.randomUUID()}.tmp`;
    try {
      await fs.writeFile(temporary,JSON.stringify({endpointHash,fingerprint:record.fingerprint,responseId:result.responseId,syncedAt:new Date().toISOString()},null,2),{mode:0o600});
      await fs.rename(temporary,statePath);
    } finally {await fs.rm(temporary,{force:true});}
    return {enabled:true,synced:true};
  }
  return {enabled:true,sync(id) {
    if (inFlight.has(id)) return inFlight.get(id);
    const pending=deliver(id).finally(()=>inFlight.delete(id));
    inFlight.set(id,pending);return pending;
  }};
};
