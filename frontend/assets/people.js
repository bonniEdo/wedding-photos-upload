const $=id=>document.getElementById(id);
let selectedPerson=null, revision='', requestSequence=0;
let refreshBusy=false;
function errorText(data){return data.message||data.detail||'暫時無法使用，請稍後再試';}
async function get(url){const r=await WeddingAPI.request(url,{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error(errorText(d));return d;}
async function showAlbum(id,scroll=true){
  selectedPerson=id;const seq=++requestSequence;
  try{const data=await get(`/api/face/people/${encodeURIComponent(id)}/photos`);if(seq!==requestSequence)return;renderPhotos(data.photos,`人物相簿 · ${data.photos.length} 張`,'自動分類可能有誤，請確認照片中的人物；也可以用自拍搜尋其他角度的照片。');document.querySelectorAll('.person').forEach(b=>b.classList.toggle('selected',b.dataset.id===id));if(scroll)$('results').scrollIntoView({behavior:scrollBehavior()});}
  catch(e){if(seq===requestSequence)$('queue').textContent=e.message;}
}
async function refresh(){
  if(refreshBusy)return;refreshBusy=true;
  try{
    const [data,status]=await Promise.all([get('/api/face/people'),get('/api/face/status')]);
    $('queue').textContent=`已分析 ${status.photos} 張 · ${status.people} 個人物大頭貼`+(status.pending?` · 還有 ${status.pending} 張分析中`:'')+(status.failed?` · ${status.failed} 張分析失敗，請新人查看`:'')+(status.withoutFaces?` · ${status.withoutFaces} 張沒有偵測到人臉`:'')+(status.partialPhotos?` · ${status.partialPhotos} 張僅完成部分偵測`:'');
    $('connection').textContent='照片星球已連線';
    $('empty').hidden=data.people.length>0;
    if(!data.people.length)$('empty').textContent=status.pending?'正在整理照片，大頭貼會陸續出現…':'星球正等待每一個你。';
    $('listEmpty').hidden=data.people.length>0;
    $('listEmpty').textContent=status.pending?'正在整理照片，大頭貼會陸續出現…':'還沒有大頭貼，先上傳婚禮照片吧。';
    const next=JSON.stringify(data.people);
    if(next!==revision){revision=next;WeddingSphere.render(data.people);document.querySelectorAll('.person, .sphere-person').forEach(b=>b.classList.toggle('selected',b.dataset.id===selectedPerson));if(selectedPerson&&data.people.some(p=>p.id===selectedPerson))await showAlbum(selectedPerson,false);}
  }catch(e){
    $('connection').textContent='等待照片服務';
    $('queue').textContent=`暫時無法讀取人物，請確認照片服務已啟動，或按右上角重新整理。${e.message==='Failed to fetch'?'':`（${e.message}）`}`;
    if(!$('listEmpty').hidden)$('listEmpty').textContent='暫時無法讀取人物，請按右上角重新整理。';
  }finally{refreshBusy=false;}
}
WeddingSphere.onSelect(showAlbum);
$('refresh').onclick=refresh;refresh();setInterval(()=>{if(!document.hidden)refresh();},8000);
