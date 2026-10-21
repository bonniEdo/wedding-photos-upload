const API_BASE=WeddingAPI.base;
const $=id=>document.getElementById(id);
let selectedPerson=null, queryFile=null, previewURL=null, revision='', requestSequence=0;
let mode='people', refreshBusy=false, searchController=null;
function errorText(data){return data.message||data.detail||'暫時無法使用，請稍後再試';}
async function get(url){const r=await WeddingAPI.request(url,{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error(errorText(d));return d;}
function renderPhotos(items,title,note=''){
  $('results').hidden=false;$('viewTitle').textContent=title;$('resultNote').textContent=note;$('photos').replaceChildren();
  if(!items.length){const p=document.createElement('p');p.textContent='目前還沒找到，可以換一張自拍，或等新照片分析完成再試。';$('photos').append(p);return;}
  for(const item of items){const card=document.createElement('div');card.className='photo';const view=document.createElement('a');view.href=WeddingAPI.url(item.url);view.target='_blank';view.rel='noopener';const img=document.createElement('img');img.loading='lazy';img.src=WeddingAPI.url(item.url);img.alt='婚禮照片';view.append(img);const download=document.createElement('a');download.href=WeddingAPI.url(`/api/photos/${encodeURIComponent(item.id)}/original`);download.textContent='下載原始照片 ↓';download.target='_blank';download.rel='noopener';card.append(view,download);$('photos').append(card);}
}
async function showAlbum(id,scroll=true){
  searchController?.abort();$('cancelSearch').hidden=true;
  mode='people';selectedPerson=id;const seq=++requestSequence;
  try{const data=await get(`/api/face/people/${encodeURIComponent(id)}/photos`);if(seq!==requestSequence||mode!=='people')return;renderPhotos(data.photos,`人物相簿 · ${data.photos.length} 張`,'自動分類可能有誤，請確認照片中的人物；也可以用自拍搜尋其他角度的照片。');document.querySelectorAll('.person').forEach(b=>b.classList.toggle('selected',b.dataset.id===id));if(scroll)$('results').scrollIntoView({behavior:scrollBehavior()});}
  catch(e){if(seq===requestSequence)$('queue').textContent=e.message;}
}
async function refresh(){
  if(refreshBusy)return;refreshBusy=true;
  try{
    const [data,status]=await Promise.all([get('/api/face/people'),get('/api/face/status')]);
    $('queue').textContent=`已分析 ${status.photos} 張 · ${status.people} 個人物大頭貼`+(status.pending?` · 還有 ${status.pending} 張分析中`:'')+(status.failed?` · ${status.failed} 張分析失敗，請新人查看`:'')+(status.withoutFaces?` · ${status.withoutFaces} 張沒有偵測到人臉`:'')+(status.partialPhotos?` · ${status.partialPhotos} 張僅完成部分偵測`:'');
    $('connection').textContent='照片星球已連線';
    $('empty').hidden=data.people.length>0;
    if(!data.people.length)$('empty').firstChild.textContent=status.pending?'正在整理照片，大頭貼會陸續出現…':'星球正等待每一個你。';
    $('listEmpty').hidden=data.people.length>0;
    $('listEmpty').textContent=status.pending?'正在整理照片，大頭貼會陸續出現…':'還沒有大頭貼，先上傳婚禮照片吧。';
    const next=JSON.stringify(data.people);
    if(next!==revision){revision=next;WeddingSphere.render(data.people);document.querySelectorAll('.person, .sphere-person').forEach(b=>b.classList.toggle('selected',b.dataset.id===selectedPerson));if(selectedPerson&&mode==='people'&&data.people.some(p=>p.id===selectedPerson))await showAlbum(selectedPerson,false);}
  }catch(e){$('connection').textContent='等待照片服務';$('queue').textContent=`暫時無法讀取人物，請確認照片服務已啟動，或按右上角重新整理。${e.message==='Failed to fetch'?'':`（${e.message}）`}`;}finally{refreshBusy=false;}
}
function scrollBehavior(){return matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth';}
function selectQuery(file){searchController?.abort();requestSequence++;$('cancelSearch').hidden=true;queryFile=file;$('search').disabled=!file;if(previewURL)URL.revokeObjectURL(previewURL);$('queryPreview').style.display=file?'block':'none';if(file){previewURL=URL.createObjectURL(file);$('queryPreview').src=previewURL;}$('searchStatus').textContent='';}
$('selfie').onchange=()=>selectQuery($('selfie').files[0]);$('camera').onchange=()=>selectQuery($('camera').files[0]);
const pause=(ms,signal)=>new Promise((resolve,reject)=>{
 if(signal.aborted){reject(new DOMException('Aborted','AbortError'));return;}
 const onAbort=()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));};
 const timer=setTimeout(()=>{signal.removeEventListener('abort',onAbort);resolve();},ms);
 signal.addEventListener('abort',onAbort,{once:true});
});
$('cancelSearch').onclick=()=>searchController?.abort();
$('search').onclick=async()=>{
  if(!queryFile)return;mode='search';const seq=++requestSequence;searchController?.abort();const controller=new AbortController();searchController=controller;$('cancelSearch').hidden=false;WeddingSphere.reset();$('search').disabled=true;$('searchStatus').textContent='正在找照片…';
  try{let data;for(let attempt=0;attempt<30;attempt++){const form=new FormData();form.append('file',queryFile);const r=await WeddingAPI.request(API_BASE+'/api/face/find',{method:'POST',body:form,signal:controller.signal});data=await r.json();if(controller.signal.aborted||seq!==requestSequence||mode!=='search')return;if(r.status===429){$('searchStatus').textContent='搜尋的人較多，正在等待…';await pause(3000+Math.random()*3000,controller.signal);continue;}if(!r.ok)throw new Error(errorText(data));break;}
    if(!data?.ok)throw new Error('等待較久，請稍後再試');if(seq!==requestSequence||mode!=='search')return;renderPhotos(data.matches,`找到 ${data.matches.length} 張可能有你的照片`,'結果依相似度排列，請確認是否為本人。'+(data.qualityLimited?' 自拍較模糊或細節不足，換清楚正面照可能找到更多照片。':'')+(data.queryPartial?' 本次僅完成部分偵測，可換較小的單人照再試。':''));$('searchStatus').textContent='搜尋完成；補傳照片分析完成後，可以再搜尋一次。';$('results').scrollIntoView({behavior:scrollBehavior()});
  }catch(e){if(seq===requestSequence&&mode==='search')$('searchStatus').textContent=e.name==='AbortError'?'已取消搜尋。':e.message;}finally{if(searchController===controller){searchController=null;$('cancelSearch').hidden=true;$('search').disabled=!queryFile;}}
};
WeddingSphere.onSelect(showAlbum);
$('findMe').onclick=()=>{const open=$('selfiePanel').hidden;$('selfiePanel').hidden=!open;$('findMe').setAttribute('aria-expanded',String(open));if(open)$('selfiePanel').scrollIntoView({behavior:scrollBehavior()});};
$('refresh').onclick=refresh;refresh();setInterval(()=>{if(!document.hidden)refresh();},8000);
