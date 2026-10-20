import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtemp,writeFile,mkdir,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
const require=createRequire(import.meta.url),createSync=require('../google-forms'),config=require('../google-form-config');
const data=await mkdtemp(path.join(tmpdir(),'wedding-google-test-'));
function formHTML(fields=config.fields) {
  const questions=Object.values(fields).map(field=>{
    const item=[123,field.title,null,field.type,[[Number(field.id),field.type===2?[['是'],['否']]:null,false]]];
    const metadata='%.@.'+JSON.stringify([item,'i1','i2']).slice(1);
    return `<div data-params="${metadata.replaceAll('"','&quot;')}"></div>`;
  }).join('');
  return `<form method="POST" action="${config.url.replace('/viewform','/formResponse')}">${questions}<input name="fvv" value="1"><input name="pageHistory" value="0"><input name="fbzx" value="test-token"></form>`;
}
const response=(html,status=200)=>({ok:status>=200&&status<300,status,text:async()=>html});
const confirmation='<html><div class="vHW8K">我們已經收到您回覆的表單。</div></html>';
async function seed(overrides={}) {
  const id=randomUUID(),dir=path.join(data,'rsvps',id);await mkdir(dir,{recursive:true});
  const record={id,guestName:'同步測試',attending:true,guestCount:3,childSeats:1,vegetarianCount:2,dietNotes:'蛋奶素',message:'祝福',photos:[{file:'memory-1.png'}],...overrides};
  record.fingerprint=createHash('sha256').update(JSON.stringify(record)).digest('hex');
  await writeFile(path.join(dir,'response.json'),JSON.stringify(record));return record;
}
try {
  assert.equal(createSync({data,enabled:false}).enabled,false);
  for(const url of ['http://docs.google.com/forms/d/e/id/viewform','https://attacker.invalid/forms/d/e/id/viewform','https://user:pass@docs.google.com/forms/d/e/id/viewform',config.url+'?foo=bar'])assert.throws(()=>createSync({data,config:{...config,url}}));
  const record=await seed(),calls=[];
  const sync=createSync({data,fetchImpl:async(url,options)=>{calls.push({url,options});return response(options.method==='POST'?confirmation:formHTML());}});
  const [one,two]=await Promise.all([sync.sync(record.id),sync.sync(record.id)]);
  assert(one.synced && two.synced);assert.equal(calls.length,2,'concurrent requests share one GET and POST');
  const submitted=new URLSearchParams(calls[1].options.body);
  for(const [key,value] of Object.entries({guestName:'同步測試',attending:'是',guestCount:'3',childSeats:'1',vegetarian:'是'}))assert.equal(submitted.get('entry.'+config.fields[key].id),value);
  const message=submitted.get('entry.'+config.fields.message.id);
  assert(message.includes('祝福'));assert(message.includes('素食人數：2 位'));assert(message.includes('蛋奶素'));assert(message.includes(record.id));assert(message.includes('1 張'));
  assert.equal(submitted.get('fbzx'),'test-token');
  const state=JSON.parse(await readFile(path.join(data,'rsvps',record.id,'google-forms.json'),'utf8'));
  assert.equal(state.status,'confirmed');assert(!state.responseId,'does not invent a Google response ID');
  await createSync({data,fetchImpl:()=>{throw Error('should not run');}}).sync(record.id);
  const declined=await seed({attending:false,guestCount:0,childSeats:0,vegetarianCount:0,dietNotes:'',photos:[]});
  await createSync({data,fetchImpl:async(url,options)=>{
    if(options.method==='POST'){const body=new URLSearchParams(options.body);assert.equal(body.get('entry.'+config.fields.attending.id),'否');assert.equal(body.get('entry.'+config.fields.vegetarian.id),'否');assert.equal(body.get('entry.'+config.fields.guestCount.id),'0');}
    return response(options.method==='POST'?confirmation:formHTML());
  }}).sync(declined.id);
  const pending=await seed();let posts=0;
  const uncertain=createSync({data,fetchImpl:async(url,options)=>{if(options.method==='POST'){posts++;throw Error('connection lost');}return response(formHTML());}});
  await assert.rejects(uncertain.sync(pending.id),error=>error.code==='GOOGLE_FORMS_UNCERTAIN');
  await assert.rejects(uncertain.sync(pending.id),error=>error.code==='GOOGLE_FORMS_UNCERTAIN');assert.equal(posts,1,'unknown delivery must not blindly resubmit');
  const invalid=await seed();
  const changed={...config.fields,guestName:{...config.fields.guestName,id:'999'}};
  await assert.rejects(createSync({data,fetchImpl:async(url,options)=>{assert.notEqual(options.method,'POST');return response(formHTML(changed));}}).sync(invalid.id),error=>error.code==='GOOGLE_FORMS_PENDING');
  const rejected=await seed();
  await assert.rejects(createSync({data,fetchImpl:async(url,options)=>response(options.method==='POST'?'<form>error</form>':formHTML())}).sync(rejected.id),error=>error.code==='GOOGLE_FORMS_PENDING');
  const generic=await seed();
  await assert.rejects(createSync({data,fetchImpl:async(url,options)=>response(options.method==='POST'?'<html>OK</html>':formHTML())}).sync(generic.id),error=>error.code==='GOOGLE_FORMS_UNCERTAIN');
  const scriptOnly=await seed();
  await assert.rejects(createSync({data,fetchImpl:async(url,options)=>response(options.method==='POST'?'<script>"我們已經收到您回覆的表單"</script><p>error</p>':formHTML())}).sync(scriptOnly.id),error=>error.code==='GOOGLE_FORMS_UNCERTAIN');
  console.log('PASS: direct Google form fields and tokens, attendance/decline, dietary notes, concurrent/repeated submissions, durable confirmation, changed schema, validation rejection and no duplicate POST after uncertain delivery.');
} finally {await rm(data,{recursive:true,force:true});}
