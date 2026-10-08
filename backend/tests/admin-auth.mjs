import {spawn} from 'node:child_process';
import http from 'node:http';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const data=await mkdtemp(path.join(tmpdir(),'wedding-auth-'));
const password='test-only-admin-password';
const key=crypto.createHash('sha256').update('wedding-face-admin:'+password).digest('hex');
let calls=0,proc,cookie='';
const stub=http.createServer(async(req,res)=>{
 assert.equal(req.headers['x-admin-key'],key);calls++;
 let raw='';for await(const chunk of req)raw+=chunk;
 res.setHeader('Content-Type','application/json');
 if(req.url==='/admin/people')res.end(JSON.stringify({ok:true,people:[],history:[]}));
 else {assert.equal(req.headers['content-type'],'application/json');const body=JSON.parse(raw);if(req.url==='/admin/merge')assert.equal(body.person_ids.length,2);else assert.equal(body.merge_id,'test-id');res.end(JSON.stringify({ok:true}));}
});
await new Promise(r=>stub.listen(31990,'127.0.0.1',r));
const base='http://127.0.0.1:31989';
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function start(adminPassword=password,secure='false'){
 proc=spawn(process.execPath,['server.js'],{cwd:root,env:{...process.env,PORT:'31989',DATA_DIR:data,DRIVE_ENABLED:'false',ADMIN_PASSWORD:adminPassword,ADMIN_COOKIE_SECURE:secure,FACE_SERVICE_URL:'http://127.0.0.1:31990'},stdio:'ignore'});
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/health')).ok)return;}catch{}await pause(50);}throw new Error('Startup failed');
}
async function stop(){if(proc?.exitCode===null){proc.kill();await new Promise(r=>proc.once('exit',r));}}
async function request(route,body,options={}){
 const headers={'X-Admin-Request':'1',...(cookie?{cookie}:{}),...options.headers};
 if(body!==undefined)headers['Content-Type']='application/json';
 return fetch(base+'/api/admin/'+route,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});
}
try{
 await start();
 for(const route of ['people','merge','undo','session','drive/status','drive/sync'])assert.equal((await request(route,route==='merge'?{person_ids:['a','b'],target_id:'a'}:route==='undo'?{merge_id:'test-id'}:undefined)).status,401);
 assert.equal(calls,0);
 assert.equal((await request('login',{password},{headers:{'X-Admin-Request':'0'}})).status,403);
 assert.equal((await request('login',{password:'wrong'})).status,401);
 const login=await request('login',{password});assert.equal(login.status,200);const loginBody=await login.clone().json();assert.match(loginBody.adminToken,/^[a-f0-9]{64}$/);const set=login.headers.get('set-cookie');assert(set.includes('HttpOnly'));assert(set.includes('SameSite=Strict'));assert(set.includes('Path=/api/admin'));assert(!set.includes(password));cookie=set.split(';')[0];
 assert.equal((await request('session')).status,200);
 const oldCookie=cookie;cookie='';assert.equal((await request('session',undefined,{headers:{Authorization:'Bearer '+loginBody.adminToken}})).status,200);cookie=oldCookie;
 assert.equal((await request('drive/status')).status,200);assert.equal((await request('drive/sync',{})).status,503);
 const preflight=await fetch(base+'/api/admin/login',{method:'OPTIONS',headers:{Origin:'http://localhost:5173','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'Content-Type,X-Admin-Request,Authorization'}});assert.equal(preflight.headers.get('access-control-allow-origin'),'http://localhost:5173');assert.equal(preflight.headers.get('access-control-allow-credentials'),'true');
 const denied=await fetch(base+'/api/admin/login',{method:'OPTIONS',headers:{Origin:'https://untrusted.invalid','Access-Control-Request-Method':'POST'}});assert.equal(denied.headers.get('access-control-allow-origin'),null);
 assert.equal((await request('people')).status,200);
 assert.equal((await request('merge',{person_ids:['a','b'],target_id:'a'})).status,200);
 assert.equal((await request('undo',{merge_id:'test-id'})).status,200);
 assert.equal(calls,3);
 assert.equal((await request('merge',{person_ids:['a','b'],target_id:'a'},{headers:{'X-Admin-Request':'0'}})).status,403);assert.equal(calls,3);
 assert.equal((await request('logout',{})).status,200);assert.equal((await request('people')).status,401);
 // Logged-in sessions cannot survive a server restart.
 const again=await request('login',{password});assert.equal(again.status,200);cookie=again.headers.get('set-cookie').split(';')[0];
 await stop();await start();assert.equal((await request('session')).status,401);
 cookie='';for(let i=0;i<5;i++)assert.equal((await request('login',{password:'wrong'})).status,401);assert.equal((await request('login',{password})).status,429);
 await stop();await start('', 'false');assert.equal((await request('login',{password})).status,503);
 await stop();await start(password,'true');const secure=await request('login',{password});assert(secure.headers.get('set-cookie').includes('Secure'));
 console.log('PASS: unauthorized management blocked, password login, HttpOnly/SameSite cookie, authenticated forwarding, CSRF header, logout, restart expiry, login rate limit, disabled default, Secure option. Python service mocked.');
}finally{await stop();await new Promise(r=>stub.close(r));await rm(data,{recursive:true,force:true});}
