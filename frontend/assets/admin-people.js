const $=id=>document.getElementById(id);let people=[],selected=new Set(),busy=false,loggedIn=false,albumSequence=0,peopleRevision=0,mergeRevision=0;
const writeRetries=new Map();
function requestId(){const bytes=crypto.getRandomValues(new Uint8Array(16));return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');}
function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error);}
function confirmAction(message){
 const dialog=$('actionConfirm');if(dialog.open)return Promise.resolve(false);
 $('actionConfirmText').textContent=message;dialog.returnValue='';
 return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.showModal();});
}
$('actionCancel').onclick=()=>$('actionConfirm').close('cancel');
$('actionAccept').onclick=()=>$('actionConfirm').close('confirm');
function showLogin(){faceTools.reset();backfillTools.reset();writeRetries.clear();WeddingAPI.setAdminToken(null);loggedIn=false;$('login').hidden=false;$('manager').hidden=true;$('logout').hidden=true;selected.clear();people=[];$('people').replaceChildren();$('albumPhotos').replaceChildren();$('album').hidden=true;albumSequence++;if($('confirm').open)$('confirm').close();if($('actionConfirm').open)$('actionConfirm').close('cancel');}
async function api(route,body){
 const options={method:body===undefined?'GET':'POST',credentials:'same-origin',headers:{'X-Admin-Request':'1'}};
 let retryKey=null;
 if(body!==undefined){
  const payload={...body};
  if(!['login','logout','drive/sync'].includes(route)){
   retryKey=route+JSON.stringify(body);payload.request_id=writeRetries.get(retryKey)||requestId();writeRetries.set(retryKey,payload.request_id);
  }
  options.headers['Content-Type']='application/json';options.body=JSON.stringify(payload);
 }
 const r=await WeddingAPI.request('/api/admin/'+route,options);const data=await r.json();
 if(!r.ok){if(retryKey&&r.status<500)writeRetries.delete(retryKey);if(r.status===401)showLogin();const error=new Error(data.message||data.detail||'操作失敗，請稍後再試');error.httpStatus=r.status;throw error;}
 if(retryKey)writeRetries.delete(retryKey);
 if(route==='login'&&data.adminToken)WeddingAPI.setAdminToken(data.adminToken);if(route==='logout')WeddingAPI.setAdminToken(null);return data;
}
function selectCount(){ $('selectionCount').textContent=`已選 ${selected.size} 個`;$('merge').disabled=busy||selected.size<2;document.querySelectorAll('.person').forEach(p=>p.classList.toggle('selected',selected.has(p.dataset.id))); }
function controls(){for(const id of ['clear','refresh','logout','confirmMerge','cancel'])$(id).disabled=busy;document.querySelectorAll('.person input,.person button').forEach(e=>e.disabled=busy);selectCount();faceTools.syncBusy();backfillTools.syncBusy();}
async function preview(person){const seq=++albumSequence;try{const r=await WeddingAPI.request(`/api/face/people/${person.id}/photos`);const d=await r.json();if(!r.ok)throw new Error(d.message||d.detail||'無法讀取相簿');if(seq!==albumSequence||!loggedIn)return;$('album').hidden=false;$('albumTitle').textContent=`相簿預覽 · ${d.photos.length} 張`;$('albumPhotos').replaceChildren();for(const p of d.photos){const link=document.createElement('a');link.href=WeddingAPI.url(p.url);link.target='_blank';link.rel='noopener';const img=document.createElement('img');img.src=WeddingAPI.url(p.url);img.alt='婚禮照片';img.loading='lazy';link.append(img);const card=document.createElement('div');const inspect=document.createElement('button');inspect.textContent='檢查人臉';inspect.onclick=()=>faceTools.openPhoto(p.id);card.append(link,inspect);$('albumPhotos').append(card);}$('album').scrollIntoView({behavior:'smooth'});}catch(e){status(e.message,true);}}
async function refresh(){const d=await api('people');people=d.people;peopleRevision=d.revision??0;selected=new Set([...selected].filter(id=>people.some(p=>p.id===id)));$('people').replaceChildren();$('empty').hidden=people.length>0;
 for(const [i,p] of people.entries()){const tile=document.createElement('div');tile.className='person';tile.dataset.id=p.id;const label=document.createElement('label');const check=document.createElement('input');check.type='checkbox';check.checked=selected.has(p.id);check.setAttribute('aria-label',`選擇第 ${i+1} 個大頭貼，${p.count} 張照片`);check.onchange=()=>{if(check.checked)selected.add(p.id);else selected.delete(p.id);selectCount();};const img=document.createElement('img');img.src=WeddingAPI.url(p.thumbnail);img.alt='人物大頭貼';img.loading='lazy';const count=document.createElement('div');count.className='count';count.textContent=p.count+' 張'+(p.merged?' · 已合併':'')+(p.provisional?' · 待確認':'');label.append(check,img,count);const view=document.createElement('button');view.className='small';view.textContent='查看照片';view.onclick=()=>preview(p);tile.append(label,view);$('people').append(tile);}
 $('history').replaceChildren();const operations=d.operations||d.history.map(h=>({...h,kind:'merge'}));
 if(!operations.length)$('history').textContent='尚未整理人物。';
 const labels={merge:'合併人物',reassign:'移轉人臉',approve:'確認歸屬',different:'確認不同人',skip:'略過建議',revoke:'撤銷排除',backfill_add:'加入漏臉'};
 for(const h of [...operations].reverse()){
  const row=document.createElement('div');row.className='history actions';const title=document.createElement('span');title.textContent=`${new Date(h.created_at*1000).toLocaleString('zh-TW')} · ${labels[h.kind]||h.kind}`;
  const undo=document.createElement('button');undo.className='small';undo.textContent=h.can_undo?'復原這次操作':'先復原較新的操作';undo.disabled=!h.can_undo;undo.dataset.undo=h.id;undo.onclick=()=>undoOperation(h);row.append(title,undo);$('history').append(row);
 }
 await faceTools.reload().catch(error=>status(error.message,true));await backfillTools.reload().catch(error=>status(error.message,true));controls();}

let driveStatusBusy=false;
async function refreshDrive(){
 if(!loggedIn||driveStatusBusy)return;driveStatusBusy=true;
 try{const [d,b]=await Promise.all([api('drive/status'),api('backup/status')]);
 $('backupStatus').textContent=`已備份 ${b.done} / ${b.total} 張 · 等待備份 ${b.pending} 張 · 備份中 ${b.uploading} 張`+(!b.enabled?'\n雲端備份已關閉，請確認 DRIVE_ENABLED=true。':b.connectionState==='authorizing'?'\n正在等待 Google 授權。':b.connectionError?'\n'+b.connectionError:'');
 $('backupFolder').hidden=!b.folderUrl;if(b.folderUrl)$('backupFolder').href=b.folderUrl;
 $('backupProblems').textContent=b.problems.map(p=>p.name+'：'+p.message).join('\n');
 $('driveStatus').textContent=!d.enabled?'雲端同步未啟用，請確認 DRIVE_ENABLED 與 DRIVE_SYNC_ENABLED。':!d.ready?'等待 Google 授權完成…':`已找到 ${d.discovered} 張 · 已匯入 ${d.imported} 張 · 等待下載 ${d.pending} 張`+(d.scanning?' · 正在讀取清單':'')+(d.downloading?` · 正在下載 ${d.currentName}`:'')+(d.failed?` · ${d.failed} 張失敗，會重試`:'')+(d.skipped?` · ${d.skipped} 張未下載`:'')+(d.lastScanAt?`\n上次掃描：${new Date(d.lastScanAt).toLocaleString('zh-TW')}`:'')+'\n匯入後仍需人臉分析；大頭貼會陸續出現，可按下方「重新整理」。';
 $('driveProblems').textContent=[d.lastScanError,...(d.problems||[]).map(p=>p.name+'：'+p.message)].filter(Boolean).join('\n');
 $('syncDrive').disabled=!d.enabled||!d.ready||d.scanning;
 }catch(e){$('driveStatus').textContent=e.message;}finally{driveStatusBusy=false;}
}
$('syncDrive').onclick=async()=>{$('syncDrive').disabled=true;try{await api('drive/sync',{});status('已開始背景同步，照片會依序匯入。');await refreshDrive();}catch(e){status(e.message,true);$('syncDrive').disabled=false;}};
setInterval(()=>{if(loggedIn&&!document.hidden)refreshDrive();},5000);
$('loginForm').onsubmit=async event=>{event.preventDefault();$('loginBtn').disabled=true;try{await api('login',{password:$('password').value});$('password').value='';loggedIn=true;$('login').hidden=true;$('manager').hidden=false;$('logout').hidden=false;await refresh();await refreshDrive();status('登入成功，請選擇要合併的大頭貼。');}catch(e){status(e.message,true);}finally{$('loginBtn').disabled=false;}};
$('logout').onclick=async()=>{try{await api('logout',{});showLogin();status('已登出。');}catch(e){status(e.message,true);}};
$('clear').onclick=()=>{selected.clear();document.querySelectorAll('.person input').forEach(c=>c.checked=false);selectCount();};
$('refresh').onclick=async()=>{if(busy)return;busy=true;controls();try{await refresh();status('人物列表已更新。');}catch(e){status(e.message,true);}finally{busy=false;controls();}};
function openMerge(ids,revision=peopleRevision){if(ids.some(id=>!people.some(p=>p.id===id))){status('人物已更新，請重新整理。',true);return;}selected=new Set(ids);mergeRevision=revision;const chosen=people.filter(p=>selected.has(p.id));$('choices').replaceChildren();$('mergeError').textContent='';for(const [i,p] of chosen.entries()){const label=document.createElement('label');label.className='choice';const radio=document.createElement('input');radio.type='radio';radio.name='target';radio.value=p.id;radio.checked=i===0;radio.setAttribute('aria-label','保留這張頭貼');const img=document.createElement('img');img.src=WeddingAPI.url(p.thumbnail);img.alt='待合併的大頭貼';const count=document.createElement('div');count.textContent=p.count+' 張';label.append(radio,img,count);$('choices').append(label);}$('confirm').showModal();}
$('merge').onclick=()=>openMerge([...selected],peopleRevision);
$('cancel').onclick=()=>{if(!busy)$('confirm').close();};$('confirm').addEventListener('cancel',e=>{if(busy)e.preventDefault();});
$('confirmMerge').onclick=async()=>{if(busy)return;const ids=[...selected],target=$('choices').querySelector('input:checked')?.value;if(!target)return;busy=true;controls();try{const d=await api('merge',{person_ids:ids,target_id:target,revision:mergeRevision});$('confirm').close();selected.clear();$('album').hidden=true;albumSequence++;await refresh();status(`已合併 ${d.merged_count} 個頭貼，相簿共有 ${d.count} 張照片。賓客頁面會自動更新。`);}catch(e){if($('confirm').open)$('mergeError').textContent=e.message;else status(e.message,true);if(e.httpStatus===409){$('confirm').close();await refresh().catch(()=>{});}}finally{busy=false;controls();}};
async function undoOperation(operation){
 if(busy||!await confirmAction('復原最近一次整理操作？操作後新增的照片會留在當時的相簿。')||busy||!loggedIn)return;
 busy=true;controls();document.querySelectorAll('[data-undo]').forEach(b=>b.disabled=true);
 try{
  if(operation.kind==='merge')await api('undo',{merge_id:operation.id,revision:peopleRevision});
  else await api('face-corrections/undo',{correction_id:operation.id,revision:peopleRevision});
  selected.clear();$('album').hidden=true;albumSequence++;faceTools.closeEditor();await refresh();status('已復原操作，新分析照片仍保留。');
 }catch(e){status(e.message,true);}finally{busy=false;controls();await refresh().catch(()=>{});}
}
const faceTools=WeddingFaceTools.create({api,status,getPeople:()=>people,isLoggedIn:()=>loggedIn,isBusy:()=>busy,setBusy:value=>{busy=value;controls();},refresh,beginMerge:openMerge,confirmAction});

const backfillTools=WeddingBackfill.create({api,status,isLoggedIn:()=>loggedIn,isBusy:()=>busy,setBusy:value=>{busy=value;controls();},refresh,confirmAction});

(async()=>{try{await api('session');loggedIn=true;$('login').hidden=true;$('manager').hidden=false;$('logout').hidden=false;await refresh();await refreshDrive();}catch(e){showLogin();if(!e.message.includes('請先登入'))status(e.message,true);}})();
