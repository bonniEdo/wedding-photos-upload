(() => {
  function create({api,status,isLoggedIn,isBusy,setBusy,refresh,confirmAction}) {
    const el=id=>document.getElementById(id), urls=new Set();
    let generation=0, loadSequence=0, editorSequence=0, data=null, candidate=null, nextOffset=null, loading=false, displayed=0;
    const labels={queued:'等待分析',processing:'分析中',ready:'已補分析',unavailable:'原圖不可用',changed:'原圖版本變動',failed:'分析失敗',stale:'資料已更新'};
    function revoke(){for(const url of urls)URL.revokeObjectURL(url);urls.clear();}
    function button(label,action){const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=action;return b;}
    async function image(img,route,sequence) {
      try {
        const response=await WeddingAPI.request(route,{headers:{'X-Admin-Request':'1'}});
        if(!response.ok)throw new Error('影像無法載入');
        const blob=await response.blob();
        if(!isLoggedIn()||sequence!==editorSequence)return;
        const url=URL.createObjectURL(blob);urls.add(url);img.src=url;
      } catch {if(sequence===editorSequence)img.alt='影像無法載入，請關閉後重試';}
    }
    function syncBusy() {
      document.querySelectorAll('#backfillPanel button,#backfillEditor button,#backfillEditor input').forEach(node=>node.disabled=isBusy());
      el('startBackfill').disabled=isBusy()||!data?.backup?.archive_sha256;
      el('retryBackfill').disabled=isBusy()||!data?.backup?.archive_sha256;
      el('pauseBackfill').disabled=isBusy()||!data||data.paused;
      el('moreBackfill').disabled=isBusy()||loading;
      document.querySelectorAll('#backfillChoices input[data-accepted]').forEach(node=>node.disabled=true);
      el('acceptBackfill').disabled=isBusy()||!candidate||candidate.stale||!document.querySelector('#backfillChoices input:checked:not([data-accepted])');
    }
    function renderJob(job) {
      const row=document.createElement('div');row.className='backfill-job';
      const text=document.createElement('span');
      text.textContent=`${job.photo_name||'照片'} · ${labels[job.status]||job.status} · 對應 ${job.mapped} 張既有人臉 · 新候選 ${job.new_faces} 張`+(job.partial?' · 部分偵測':'')+(job.ambiguous?` · ${job.ambiguous} 項需保留原判斷`:'');
      row.append(text);
      if(job.message){const note=document.createElement('p');note.className='note';note.textContent=job.message;row.append(note);}
      if(job.candidate_id&&job.new_faces)row.append(button(`複核新增人臉（${job.new_faces}）`,()=>open(job.candidate_id)));
      el('backfillJobs').append(row);
    }
    async function reload(append=false) {
      if(!isLoggedIn()||(loading&&append))return;
      const offset=append?nextOffset:0;if(offset===null)return;
      loading=true;const sequence=++loadSequence, epoch=generation;
      try {
        const result=await api(`face-backfill?offset=${offset}&limit=20`);
        if(!isLoggedIn()||sequence!==loadSequence||epoch!==generation)return;
        if(append&&data&&data.revision!==result.revision){loading=false;return reload(false);}
        data=result;nextOffset=result.next_offset;
        if(!append){el('backfillJobs').replaceChildren();displayed=0;}
        const counts=Object.entries(result.counts).map(([key,n])=>`${labels[key]||key} ${n}`).join(' · ');
        el('backfillStatus').textContent=(result.backup.archive_sha256?'已登記驗證備份。':'尚未登記驗證備份，請先依維護說明完成備份。')+
          ` ${result.paused?'已暫停':result.total&&!result.counts.queued&&!result.counts.processing?'本輪分析已完成':'回填已開啟'}。`+(counts?' '+counts:' 尚無回填任務。')+` · 待複核候選 ${result.pending_faces||0} 張`;
        result.jobs.forEach(renderJob);displayed+=result.jobs.length;el('moreBackfill').hidden=nextOffset===null;
      } finally {if(sequence===loadSequence){loading=false;syncBusy();}}
    }
    function close() {editorSequence++;candidate=null;revoke();if(el('backfillEditor').open)el('backfillEditor').close();}
    async function open(id) {
      if(isBusy())return;
      close();const sequence=++editorSequence;
      try {
        const result=await api(`face-backfill/candidates/${encodeURIComponent(id)}`);
        if(!isLoggedIn()||sequence!==editorSequence)return;
        candidate=result;el('backfillStage').replaceChildren();el('backfillChoices').replaceChildren();el('backfillError').textContent='';
        el('backfillCandidateNote').textContent=`已對應 ${result.mapped_count} 張既有人臉，保留 ${result.ambiguous_count} 項不明對應。`+
          (result.partial?'本次只完成部分偵測。':'')+(result.stale?' 資料已更新，請重新回填後再確認。':' 確認漏臉後會建立待確認人物，可再移轉或合併。');
        const full=document.createElement('img');full.alt='補分析照片預覽';image(full,result.photo_url,sequence);el('backfillStage').append(full);
        result.faces.forEach((face,index)=>{
          const label=document.createElement('label');label.className='face-choice';
          const input=document.createElement('input');input.type='checkbox';input.value=face.face_id;
          input.setAttribute('aria-label',`選擇第 ${index+1} 張漏臉候選`);
          if(face.accepted)input.dataset.accepted='1';
          input.onchange=syncBusy;
          const crop=document.createElement('img');crop.alt=`第 ${index+1} 張漏臉候選`;crop.className='review-crop';image(crop,face.crop_url,sequence);
          const note=document.createElement('span');note.textContent=`候選 ${index+1}${face.accepted?' · 已加入':face.qualityLimited?' · 請確認模糊影像':''}`;
          label.append(input,crop,note);el('backfillChoices').append(label);
          if(face.normalized_bbox){
            const [x,y,w,h]=face.normalized_bbox,box=document.createElement('span');box.className='face-box';box.textContent=String(index+1);
            box.style.left=`${Math.max(0,x)*100}%`;box.style.top=`${Math.max(0,y)*100}%`;
            box.style.width=`${Math.max(0,Math.min(1,x+w)-Math.max(0,x))*100}%`;box.style.height=`${Math.max(0,Math.min(1,y+h)-Math.max(0,y))*100}%`;
            el('backfillStage').append(box);
          }
        });
        syncBusy();el('backfillEditor').showModal();
      } catch(error){status(error.message,true);}
    }
    async function act(route,body,message) {
      if(isBusy())return;setBusy(true);
      try {await api(route,body);close();await refresh();status(message);}
      catch(error){status(error.message,true);el('backfillError').textContent=error.message;if(error.httpStatus===409){close();await refresh().catch(()=>{});}}
      finally {setBusy(false);syncBusy();}
    }
    el('startBackfill').onclick=()=>data&&act('face-backfill/start',{revision:data.revision},'已開始／繼續回填，新增人臉會先留待複核。');
    el('retryBackfill').onclick=()=>data&&act('face-backfill/start',{revision:data.revision,retry:true},'已重排未完成任務，原圖版本異常仍會保留提示。');
    el('pauseBackfill').onclick=()=>data&&act('face-backfill/pause',{revision:data.revision},'已暫停後續回填，目前分析中的照片會先完成。');
    el('refreshBackfill').onclick=()=>reload().catch(error=>status(error.message,true));
    el('moreBackfill').onclick=()=>reload(true).catch(error=>status(error.message,true));
    el('closeBackfill').onclick=close;
    el('backfillEditor').addEventListener('cancel',event=>{if(isBusy())event.preventDefault();else close();});
    el('acceptBackfill').onclick=async()=>{
      const current=candidate;if(!current||current.stale||isBusy())return;
      const ids=[...document.querySelectorAll('#backfillChoices input:checked:not([data-accepted])')].map(node=>node.value);
      if(!ids.length||!await confirmAction(`確認選定的 ${ids.length} 張是原本漏掉的人臉，加入為待確認人物？既有人臉的歸屬會保留。`))return;
      if(candidate!==current||!isLoggedIn()||isBusy())return;
      act(`face-backfill/candidates/${current.candidate_id}/accept`,{face_ids:ids,revision:current.revision},'已追加選定漏臉，請至待確認人臉檢查歸屬；可從整理紀錄復原。');
    };
    function reset(){generation++;loadSequence++;data=null;nextOffset=null;loading=false;displayed=0;close();el('backfillJobs').replaceChildren();}
    setInterval(()=>{if(isLoggedIn()&&!document.hidden&&!isBusy()&&!loading&&displayed<=20&&!el('backfillEditor').open)reload().catch(()=>{});},8000);
    return {reload,reset,syncBusy,close};
  }
  window.WeddingBackfill={create};
})();
