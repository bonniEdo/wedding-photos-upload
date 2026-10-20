const fs=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');
const defaults=require('./google-form-config');

function decodeHTML(value) {
  return value.replace(/&quot;/g,'"').replace(/&#39;|&apos;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&#x([a-f0-9]+);/gi,(_,n)=>String.fromCodePoint(parseInt(n,16))).replace(/&#(\d+);/g,(_,n)=>String.fromCodePoint(Number(n))).replace(/&amp;/g,'&');
}
function attributes(tag) {
  const result={};
  for(const match of tag.matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g))result[match[1].toLowerCase()]=decodeHTML(match[2]??match[3]??match[4]);
  return result;
}
const syncError=(message,code='GOOGLE_FORMS_PENDING')=>Object.assign(new Error(message),{status:502,code});

module.exports=function googleForms({data,config=defaults,enabled=process.env.WEDDING_GOOGLE_FORMS_ENABLED!=='false',fetchImpl=fetch}) {
  if(!enabled)return {enabled:false,sync:async()=>({enabled:false})};
  const pageURL=new URL(config.url);
  if(pageURL.origin!=='https://docs.google.com' || !/^\/forms\/d\/e\/[a-zA-Z0-9_-]+\/viewform$/.test(pageURL.pathname) || pageURL.search || pageURL.hash || pageURL.username || pageURL.password)throw new Error('請設定有效的 Google 表單公開填寫網址。');
  const submitURL=config.url.replace(/\/viewform$/,'/formResponse');
  const targetHash=crypto.createHash('sha256').update(JSON.stringify(config)).digest('hex');
  const inFlight=new Map();
  const requestOptions={headers:{'Accept-Language':'zh-TW,zh;q=0.9,en;q=0.8'},redirect:'error'};
  async function atomicState(file,state) {
    const temporary=`${file}.${crypto.randomUUID()}.tmp`;
    try {
      const handle=await fs.open(temporary,'w',0o600);
      try {await handle.writeFile(JSON.stringify(state,null,2));await handle.sync();} finally {await handle.close();}
      await fs.rename(temporary,file);
    } finally {await fs.rm(temporary,{force:true});}
  }
  async function prepareForm() {
    let response,html;
    try {response=await fetchImpl(config.url,{...requestOptions,signal:AbortSignal.timeout(15000)});html=await response.text();}
    catch {throw syncError('回覆已保留，但目前無法連線至 Google 表單，請稍後再試。');}
    if(!response.ok)throw syncError('Google 表單目前無法填寫，回覆已保留，請稍後再試。');
    const formTag=html.match(/<form\b[^>]*>/i)?.[0];
    const form=formTag?attributes(formTag):{};
    if(form.action!==submitURL || form.method?.toLowerCase()!=='post')throw syncError('Google 表單目前無法接受回覆，請通知新人確認。');
    const questions=new Map();
    for(const match of html.matchAll(/\bdata-params\s*=\s*"([^"]*)"/g)) {
      try {
        const raw=decodeHTML(match[1]);
        if(!raw.startsWith('%.@.'))continue;
        const item=JSON.parse('['+raw.slice(4))[0];
        if(Array.isArray(item?.[4]))for(const entry of item[4])questions.set(String(entry[0]),{title:item[1],type:item[3],choices:entry[1]?.map(choice=>choice[0])});
      } catch { /* Required questions are checked below. */ }
    }
    for(const field of Object.values(config.fields)) {
      const question=questions.get(field.id);
      if(!question || question.title!==field.title || question.type!==field.type || (field.type===2 && (!question.choices?.includes('是') || !question.choices?.includes('否'))))throw syncError('Google 表單欄位已變更，回覆已保留，請通知新人更新串接設定。');
    }
    const body=new URLSearchParams();
    for(const tag of html.matchAll(/<input\b[^>]*>/gi)) {
      const input=attributes(tag[0]);
      if(['fvv','partialResponse','pageHistory','fbzx','submissionTimestamp'].includes(input.name))body.set(input.name,input.value||'');
    }
    if(!body.get('fbzx') || !body.has('fvv') || !body.has('pageHistory'))throw syncError('Google 表單提交資訊不完整，請稍後再試。');
    return body;
  }
  async function deliver(id) {
    if(!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(id))throw new Error('Invalid RSVP ID');
    const dir=path.join(data,'rsvps',id),statePath=path.join(dir,'google-forms.json');
    const record=JSON.parse(await fs.readFile(path.join(dir,'response.json'),'utf8'));
    let prior;
    try {prior=JSON.parse(await fs.readFile(statePath,'utf8'));} catch(error){if(error.code!=='ENOENT')throw error;}
    if(prior?.targetHash===targetHash && prior.fingerprint===record.fingerprint) {
      if(prior.status==='confirmed')return {enabled:true,synced:true};
      if(['sending','uncertain'].includes(prior.status))throw syncError('回覆已保留，但 Google 送達狀態需要新人核對。為避免重複登記，系統不會再次送出這一筆。','GOOGLE_FORMS_UNCERTAIN');
    }
    const body=await prepareForm();
    const extra=[];
    if(record.attending) {
      extra.push(`素食人數：${record.vegetarianCount} 位`);
      if(record.dietNotes)extra.push(`飲食備註：${record.dietNotes}`);
      if(record.photos.length)extra.push(`共同回憶照片：${record.photos.length} 張（由新人另外留存）`);
    }
    extra.push(`回覆編號：${id}`);
    const message=[record.message,`【回覆補充】\n${extra.join('\n')}`].filter(Boolean).join('\n\n');
    const values={guestName:record.guestName,attending:record.attending?'是':'否',guestCount:String(record.guestCount),childSeats:String(record.childSeats||0),vegetarian:record.vegetarianCount>0?'是':'否',message};
    for(const [key,field] of Object.entries(config.fields)) {
      body.set(`entry.${field.id}`,values[key]);
      if(field.type===2)body.set(`entry.${field.id}_sentinel`,'');
    }
    const state={targetHash,fingerprint:record.fingerprint,status:'sending',attemptedAt:new Date().toISOString()};
    await atomicState(statePath,state);
    let response,html;
    try {
      response=await fetchImpl(submitURL,{...requestOptions,method:'POST',headers:{...requestOptions.headers,'Content-Type':'application/x-www-form-urlencoded'},body:body.toString(),signal:AbortSignal.timeout(30000)});
      html=await response.text();
    } catch {
      await atomicState(statePath,{...state,status:'uncertain'});
      throw syncError('回覆已保留，但連線中斷，尚無法確認 Google 是否收到。請通知新人核對回覆編號，避免重複登記。','GOOGLE_FORMS_UNCERTAIN');
    }
    // A 200 response can be the form with validation errors. Require Google's confirmation message.
    const visibleText=decodeHTML(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi,'').replace(/<[^>]*>/g,' '));
    const confirmation=/我們已經收到您回覆的表單|您的回覆已記錄|已記錄你的回覆|你的回覆已記錄|已記錄您的回覆|Your response has been recorded/i.test(visibleText);
    if(!response.ok || !confirmation || /<form\b/i.test(html)) {
      const rejected=response.status===400 || (response.ok && /<form\b/i.test(html));
      await atomicState(statePath,{...state,status:rejected?'rejected':'uncertain'});
      throw syncError(rejected?'Google 表單未接受這份回覆，資料已保留，請通知新人確認欄位設定。':'Google 尚未確認收到回覆，資料已保留，請通知新人核對。',rejected?'GOOGLE_FORMS_PENDING':'GOOGLE_FORMS_UNCERTAIN');
    }
    await atomicState(statePath,{...state,status:'confirmed',syncedAt:new Date().toISOString(),confirmationHash:crypto.createHash('sha256').update(html).digest('hex')});
    return {enabled:true,synced:true};
  }
  return {enabled:true,sync(id) {
    if(inFlight.has(id))return inFlight.get(id);
    const pending=deliver(id).finally(()=>inFlight.delete(id));
    inFlight.set(id,pending);return pending;
  }};
};
