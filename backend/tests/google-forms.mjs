import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtemp,writeFile,mkdir,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
const require=createRequire(import.meta.url);
const createSync=require('../google-forms');
const data=await mkdtemp(path.join(tmpdir(),'wedding-google-test-'));
const url='https://script.google.com/macros/s/test_deployment/exec',secret='test-only-secret-'.repeat(3);
async function seed() {
  const id=randomUUID(),dir=path.join(data,'rsvps',id);await mkdir(dir,{recursive:true});
  const bytes=Buffer.from('test image contents'),sha256=createHash('sha256').update(bytes).digest('hex');
  await writeFile(path.join(dir,'memory-1.png'),bytes);
  const record={id,guestName:'同步測試',attending:true,guestCount:2,vegetarianCount:1,dietNotes:'',message:'祝福',fingerprint:sha256,photos:[{file:'memory-1.png',originalName:'回憶.png',mimeType:'image/png',size:bytes.length,sha256}]};
  await writeFile(path.join(dir,'response.json'),JSON.stringify(record));return record;
}
try {
  assert.equal(createSync({data,url:'',secret:''}).enabled,false);
  assert.throws(()=>createSync({data,url,secret:''}));
  assert.throws(()=>createSync({data,url:'',secret}));
  for(const bad of ['http://script.google.com/macros/s/id/exec','https://attacker.invalid/macros/s/id/exec','https://script.google.com/macros/s/id/dev',url+'?foo=bar','https://user:pass@script.google.com/macros/s/id/exec'])assert.throws(()=>createSync({data,url:bad,secret}));
  const record=await seed(),calls=[];
  const sync=createSync({data,url,secret,fetchImpl:async(target,options)=>{
    assert.equal(target,url);const body=JSON.parse(options.body);calls.push(body);assert.equal(body.secret,secret);
    return {ok:true,json:async()=>body.action==='upload'?{ok:true,id:body.id,fileId:'private-photo-id'}:{ok:true,id:body.id,responseId:'google-response-id'}};
  }});
  const [one,two]=await Promise.all([sync.sync(record.id),sync.sync(record.id)]);
  assert.equal(one.synced,true);assert.equal(two.synced,true);assert.equal(calls.length,2,'concurrent requests share upload and submit');
  assert.equal(calls[0].action,'upload');assert.equal(calls[0].photo.sha256,record.photos[0].sha256);
  assert.equal(calls[1].record.photos[0].fileId,'private-photo-id');
  const state=JSON.parse(await readFile(path.join(data,'rsvps',record.id,'google-forms.json'),'utf8'));
  assert.equal(state.responseId,'google-response-id');assert(!JSON.stringify(state).includes(secret));
  await createSync({data,url,secret,fetchImpl:()=>{throw Error('should not run');}}).sync(record.id);
  const pending=await seed();let fail=true,remoteSubmissions=0;
  const remote=createSync({data,url,secret,fetchImpl:async(target,options)=>{
    const body=JSON.parse(options.body);
    if(body.action==='submit') {remoteSubmissions++;if(fail){fail=false;throw Error('lost confirmation '+secret);}}
    return {ok:true,json:async()=>({ok:true,id:body.id,fileId:'photo',responseId:'reconciled-response'})};
  }});
  await assert.rejects(remote.sync(pending.id),error=>error.status===502 && !error.message.includes(secret));
  assert.equal((await remote.sync(pending.id)).synced,true);assert.equal(remoteSubmissions,2,'retry reaches remote reconciliation');
  const malformed=await seed();
  for (const result of [{ok:false},{ok:true,id:malformed.id,fileId:'../../bad'},{ok:true,id:'wrong',fileId:'photo'}]) {
    const broken=createSync({data,url,secret,fetchImpl:async()=>({ok:true,json:async()=>result})});
    await assert.rejects(broken.sync(malformed.id),error=>error.status===502);
  }
  console.log('PASS: Google endpoint/secret validation, private per-photo delivery, concurrent sync, persisted receipts, retry reconciliation, remote errors and secret-safe messages.');
} finally {await rm(data,{recursive:true,force:true});}
