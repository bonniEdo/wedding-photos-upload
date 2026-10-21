// Exercise observable cancellation/stale-response behavior without loading the 3D canvas.
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const nodes=new Map();
function element(){return {hidden:false,disabled:false,style:{},textContent:'',firstChild:{textContent:''},classList:{toggle(){}},replaceChildren(){this.replaced=true;},append(){},scrollIntoView(){},setAttribute(){}};}
const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
const timers=new Map();let timerId=0,handler;
const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
const context=vm.createContext({
  document:{getElementById:get,querySelectorAll:()=>[],hidden:false,createElement:element},
  WeddingSphere:{render(){},reset(){},onSelect(){}},
  WeddingAPI:{base:'',url:x=>x,request:async(route,options)=>{
    if(route.endsWith('/find'))return handler(options);
    if(route.endsWith('/status'))return response({photos:0,people:0});
    return response({people:[],photos:[]});
  }},
  AbortController,DOMException,FormData,Blob,URL,console,
  matchMedia:()=>({matches:true}),setInterval(){},
  setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),
});
vm.runInContext(await readFile(new URL('../../frontend/assets/people.js',import.meta.url),'utf8'),context);
const flush=()=>new Promise(r=>setImmediate(r));await flush();
function choose(){get('selfie').files=[new Blob(['fixture'],{type:'image/jpeg'})];get('selfie').onchange();}
choose();
handler=({signal})=>new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}));
let search=get('search').onclick();assert.equal(get('cancelSearch').hidden,false);get('cancelSearch').onclick();await search;
assert.equal(get('searchStatus').textContent,'已取消搜尋。');assert.equal(get('search').disabled,false);assert.equal(get('cancelSearch').hidden,true);
handler=async()=>response({ok:false},429);search=get('search').onclick();await flush();assert.equal(timers.size,1);
get('cancelSearch').onclick();await search;assert.equal(timers.size,0);assert.equal(get('searchStatus').textContent,'已取消搜尋。');
let finish;handler=()=>new Promise(resolve=>{finish=resolve;});search=get('search').onclick();await flush();choose();
finish(response({ok:true,matches:[]}));await search;
assert.equal(get('searchStatus').textContent,'','An old response must not replace the newly selected selfie status');assert.equal(get('photos').replaced,undefined);
assert.equal(get('search').disabled,false);
search=get('search').onclick();await flush();choose();finish(response({ok:false},429));await search;
assert.equal(timers.size,0);assert.equal(get('searchStatus').textContent,'','Stale 429 must not change status or schedule retries');
console.log('PASS: cancel active selfie, cancel 429 backoff without leaking timer, and ignore old response after choosing another selfie.');
