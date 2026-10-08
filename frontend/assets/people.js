const API_BASE=WeddingAPI.base;
const $=id=>document.getElementById(id);
let selectedPerson=null, queryFile=null, previewURL=null, revision='', requestSequence=0;
let mode='people', refreshBusy=false;
function errorText(data){return data.message||data.detail||'暫時無法使用，請稍後再試';}
async function get(url){const r=await WeddingAPI.request(API_BASE+url,{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error(errorText(d));return d;}
function renderPhotos(items,title,note=''){
  $('results').hidden=false;$('viewTitle').textContent=title;$('resultNote').textContent=note;$('photos').replaceChildren();
  if(!items.length){const p=document.createElement('p');p.textContent='目前還沒找到，可以換一張自拍，或等新照片分析完成再試。';$('photos').append(p);return;}
  for(const item of items){const card=document.createElement('div');card.className='photo';const view=document.createElement('a');view.href=API_BASE+item.url;view.target='_blank';view.rel='noopener';const img=document.createElement('img');img.loading='lazy';img.src=API_BASE+item.url;img.alt='婚禮照片';view.append(img);const download=document.createElement('a');download.href=API_BASE+`/api/photos/${item.id}/original`;download.textContent='下載原始照片 ↓';download.target='_blank';download.rel='noopener';card.append(view,download);$('photos').append(card);}
}
async function showAlbum(id,scroll=true){
  mode='people';selectedPerson=id;const seq=++requestSequence;
  try{const data=await get(`/api/face/people/${id}/photos`);if(seq!==requestSequence||mode!=='people')return;renderPhotos(data.photos,`人物相簿 · ${data.photos.length} 張`,'自動分類可能有誤，請確認照片中的人物；也可以用自拍搜尋其他角度的照片。');document.querySelectorAll('.person').forEach(b=>b.classList.toggle('selected',b.dataset.id===id));if(scroll)$('results').scrollIntoView({behavior:'smooth'});}
  catch(e){$('queue').textContent=e.message;}
}
async function refresh(){
  if(refreshBusy)return;refreshBusy=true;
  try{
    const [data,status]=await Promise.all([get('/api/face/people'),get('/api/face/status')]);
    $('queue').textContent=`已分析 ${status.photos} 張 · ${status.people} 個人物大頭貼`+(status.pending?` · 還有 ${status.pending} 張分析中`:'')+(status.failed?` · ${status.failed} 張分析失敗，請新人查看`:'')+(status.withoutFaces?` · ${status.withoutFaces} 張沒有偵測到人臉`:'');
    $('empty').hidden=data.people.length>0;$('empty').textContent=status.pending?'正在整理照片，大頭貼會陸續出現…':'還沒有大頭貼，先上傳婚禮照片吧 📸';
    const next=JSON.stringify(data.people);
    if(next!==revision){revision=next;$('people').replaceChildren();for(const p of data.people){const b=document.createElement('button');b.type='button';b.className='person'+(selectedPerson===p.id?' selected':'');b.dataset.id=p.id;b.setAttribute('aria-label',`查看人物相簿，${p.count} 張照片`);const img=document.createElement('img');img.src=API_BASE+p.thumbnail;img.loading='lazy';img.alt='人物大頭貼';const count=document.createElement('span');count.textContent=p.count+' 張';b.append(img,count);b.onclick=()=>showAlbum(p.id);$('people').append(b);}if(selectedPerson&&mode==='people')await showAlbum(selectedPerson,false);}
  }catch(e){$('queue').textContent=e.message;}finally{refreshBusy=false;}
}
function selectQuery(file){queryFile=file;$('search').disabled=!file;if(previewURL)URL.revokeObjectURL(previewURL);$('queryPreview').style.display=file?'block':'none';if(file){previewURL=URL.createObjectURL(file);$('queryPreview').src=previewURL;}$('searchStatus').textContent='';}
$('selfie').onchange=()=>selectQuery($('selfie').files[0]);$('camera').onchange=()=>selectQuery($('camera').files[0]);
const pause=ms=>new Promise(r=>setTimeout(r,ms));
$('search').onclick=async()=>{
  if(!queryFile)return;mode='search';requestSequence++;$('search').disabled=true;$('searchStatus').textContent='正在找照片…';
  try{let data;for(let attempt=0;attempt<30;attempt++){const form=new FormData();form.append('file',queryFile);const r=await WeddingAPI.request(API_BASE+'/api/face/find',{method:'POST',body:form});data=await r.json();if(r.status===429){$('searchStatus').textContent='搜尋的人較多，正在等待…';await pause(3000+Math.random()*3000);continue;}if(!r.ok)throw new Error(errorText(data));break;}
    if(!data?.ok)throw new Error('等待較久，請稍後再試');renderPhotos(data.matches,`找到 ${data.matches.length} 張可能有你的照片`,'結果依相似度排列，請確認是否為本人。');$('searchStatus').textContent='搜尋完成；補傳照片分析完成後，可以再搜尋一次。';$('results').scrollIntoView({behavior:'smooth'});
  }catch(e){$('searchStatus').textContent=e.message;}finally{$('search').disabled=!queryFile;}
};
$('refresh').onclick=refresh;refresh();setInterval(()=>{if(!document.hidden)refresh();},8000);
