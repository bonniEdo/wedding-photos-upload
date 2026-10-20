import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtemp,readFile,readdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

const require = createRequire(import.meta.url);
const express = require('express');
const invitations = require('../invitations');
const data = await mkdtemp(path.join(tmpdir(),'wedding-rsvp-test-'));
const app = express(); app.use('/api/invitations',invitations({data}));
const server = app.listen(0,'127.0.0.1');
await new Promise(resolve => server.once('listening',resolve));
const url = `http://127.0.0.1:${server.address().port}/api/invitations/rsvp`;
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1sAAAAASUVORK5CYII=','base64');
async function send(overrides={}, files=[]) {
  const fields={requestId:randomUUID(),guestName:'測試賓客',attendance:'yes',guestCount:'3',vegetarianCount:'1',dietNotes:'蛋奶素',message:'祝你們幸福！',...overrides};
  const body = new FormData();
  for (const [key,value] of Object.entries(fields)) body.append(key,value);
  for (const file of files) body.append('photos',new Blob([file.bytes],{type:file.type}),file.name);
  const response = await fetch(url,{method:'POST',body});
  return {status:response.status,body:await response.json(),fields};
}
try {
  const id = randomUUID();
  const photo = {bytes:png,type:'image/png',name:'一起的回憶.png'};
  const yes = await send({requestId:id},[photo]);
  assert.equal(yes.status,201); assert.equal(yes.body.id,id);
  const record = JSON.parse(await readFile(path.join(data,'rsvps',id,'response.json'),'utf8'));
  assert.equal(record.attending,true);assert.equal(record.guestCount,3);assert.equal(record.vegetarianCount,1);
  assert.equal(record.message,'祝你們幸福！');assert.equal(record.photos.length,1);
  assert.deepEqual(await readFile(path.join(data,'rsvps',id,record.photos[0].file)),png);
  assert.equal((await send({requestId:id},[photo])).status,201,'retry succeeds');
  assert.equal((await readdir(path.join(data,'rsvps'))).length,1,'retry creates no duplicate');
  assert.equal((await send({requestId:id,message:'changed'},[photo])).status,409,'conflicting retry rejected');
  const parallelId=randomUUID();
  const parallel=await Promise.all([send({requestId:parallelId}),send({requestId:parallelId})]);
  assert(parallel.every(r=>r.status===201),'concurrent identical retries succeed');
  const no=await send({attendance:'no',guestCount:'99',vegetarianCount:'99',dietNotes:'hidden stale value'});
  assert.equal(no.status,201);
  const declined=JSON.parse(await readFile(path.join(data,'rsvps',no.body.id,'response.json'),'utf8'));
  assert.equal(declined.guestCount,0);assert.equal(declined.vegetarianCount,0);assert.equal(declined.dietNotes,'');assert.equal(declined.message,'祝你們幸福！');
  for (const invalid of [{guestName:' '},{attendance:''},{guestCount:'0'},{guestCount:'21'},{guestCount:'1.5'},{vegetarianCount:'4'},{vegetarianCount:'-1'},{message:'x'.repeat(2001)},{requestId:'../../escape'}]) assert.equal((await send(invalid)).status,400,JSON.stringify(invalid).slice(0,60));
  assert.equal((await send({attendance:'no'},[photo])).status,400,'declining cannot upload photos');
  assert.equal((await send({},[{bytes:Buffer.from('not an image'),type:'image/png',name:'fake.png'}])).status,400,'forged MIME rejected');
  assert.equal((await send({},[{bytes:png,type:'text/plain',name:'test.txt'}])).status,400,'unsupported MIME rejected');
  assert.equal((await send({},[photo,photo,photo,photo])).status,400,'more than three photos rejected');
  assert.equal((await send({},[{bytes:Buffer.alloc(10*1024*1024+1),type:'image/png',name:'too-large.png'}])).status,400,'oversize photo rejected');
  assert((await readdir(path.join(data,'rsvps'))).every(name=>!name.startsWith('.pending-')),'no staging files left');
  assert.equal((await fetch(`${url}/${id}`)).status,404,'responses not publicly readable');
  console.log('PASS: attendance and decline records, blessings, private photo persistence, idempotent/concurrent retries, input validation, image signatures, upload limits, staging cleanup and no public read API.');
} finally {
  await new Promise(resolve=>server.close(resolve));
  await rm(data,{recursive:true,force:true});
}
