// 同一台 Mac 測試時留空。
// 若之後前端搬到 GitHub Pages，再改成公開後端 HTTPS 網址。
const API_BASE = WeddingAPI.base;

const MAX_SELECT = 20;
const BATCH_SIZE = 5;

const input = document.getElementById('photos');
const preview = document.getElementById('preview');
const uploadBtn = document.getElementById('uploadBtn');
const statusBox = document.getElementById('status');
const countBox = document.getElementById('count');
const progressWrap = document.getElementById('progressWrap');
const progressBar = document.getElementById('progressBar');
const progressText = document.getElementById('progressText');
const batchText = document.getElementById('batchText');

let selectedFiles = [];
let backupGeneration=0;

function setStatus(message, type='info'){
  statusBox.className = `status show ${type}`;
  statusBox.textContent = message;
}

function setProgress(percent, label=''){
  const p = Math.max(0, Math.min(100, Math.round(percent)));
  progressBar.style.width = `${p}%`;
  progressText.textContent = `${p}%`;
  batchText.textContent = label;
}

function splitIntoBatches(files, size){
  const batches = [];
  for(let i=0;i<files.length;i+=size){
    batches.push(files.slice(i,i+size));
  }
  return batches;
}

function uploadBatchOnce(files, batchIndex, totalBatches, uploadKey){
  return new Promise((resolve, reject)=>{
    const formData = new FormData();
    files.forEach(file => formData.append('photos', file));

    const xhr = new XMLHttpRequest();
    xhr.open('POST', WeddingAPI.url('/api/photos/upload'));
    xhr.withCredentials=true;
    xhr.setRequestHeader('X-Upload-Key',uploadKey);

    xhr.upload.onprogress = (event)=>{
      if(!event.lengthComputable) return;

      // 單一批次傳到 Mac 的進度只佔這批的前 90%，
      // 等後端確認原始照片與排隊任務寫入磁碟才算完成。
      const uploadFraction = event.loaded / event.total;
      const batchBase = batchIndex / totalBatches;
      const batchSpan = 1 / totalBatches;

      const overall =
        (batchBase + batchSpan * uploadFraction * 0.9) * 100;

      setProgress(
        overall,
        `第 ${batchIndex + 1} / ${totalBatches} 批上傳中`
      );
    };

    xhr.onload = ()=>{
      try{
        const data = JSON.parse(xhr.responseText || '{}');

        if(xhr.status >= 200 && xhr.status < 300 && data.ok){
          const completed = ((batchIndex + 1) / totalBatches) * 100;
          setProgress(
            completed,
            `第 ${batchIndex + 1} / ${totalBatches} 批完成`
          );
          resolve(data);
        }else{
          const error=new Error(data.message || `HTTP ${xhr.status}`); error.status=xhr.status; reject(error);
        }
      }catch(error){
        reject(error);
      }
    };

    xhr.onerror = ()=>{
      reject(new Error('網路連線失敗'));
    };

    xhr.ontimeout = ()=>{
      reject(new Error('上傳逾時'));
    };

    // 每批最多 5 張，給比較寬鬆的逾時時間。
    xhr.timeout = 5 * 60 * 1000;

    xhr.send(formData);
  });
}

const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function uploadBatch(files,batchIndex,totalBatches){
  const uploadKey=Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');
  const deadline=Date.now()+15*60*1000;
  let attempt=0;
  for(;;){
    try{return await uploadBatchOnce(files,batchIndex,totalBatches,uploadKey);}
    catch(error){
      if(Date.now()>deadline || (error.status && ![429,500,502,503,504].includes(error.status)))throw error;
      attempt++;
      const delay=Math.min(20000,2000*2**Math.min(attempt,4))+Math.random()*5000;
      batchText.textContent=`第 ${batchIndex+1} 批等待重試，請保持頁面開啟…`;
      await pause(delay);
    }
  }
}

async function monitorBackup(ids,received,duplicates,generation){
  const unique=[...new Set(ids)];
  const prefix=`已收到 ${received} 張照片。`+(duplicates?`其中 ${duplicates} 張已在本次重試前收到。`:'');
  if(!unique.length){setStatus(prefix+'目前無法確認雲端狀態，請新人確認後端版本。','info');return;}
  const deadline=Date.now()+10*60*1000;
  while(Date.now()<deadline && generation===backupGeneration){
    try{
      const response=await WeddingAPI.request('/api/photos/status?ids='+encodeURIComponent(unique.join(',')));
      if(response.status===404){setStatus(prefix+'目前後端版本尚未提供雲端狀態，請新人更新後端。','info');return;}
      const data=await response.json();
      if(!response.ok)throw new Error(data.message||'無法讀取雲端狀態');
      if(generation!==backupGeneration)return;
      const view=BackupView.describe(data);setStatus(prefix+view.text,view.type);
      if(view.stop){if(view.type==='ok')setProgress(100,'雲端備份完成');return;}
    }catch(error){if(generation!==backupGeneration)return;setStatus(prefix+'暫時無法確認雲端進度；已收到的照片不需要重傳。','info');}
    await pause(5000);
  }
  if(generation===backupGeneration)setStatus(prefix+'雲端備份等待較久，請新人在管理頁查看進度；原圖仍保存在本機。','info');
}

input.addEventListener('change', ()=>{
  backupGeneration++;
  let files = [...input.files];

  if(files.length > MAX_SELECT){
    files = files.slice(0, MAX_SELECT);
    setStatus(`一次最多 20 張，已先取前 20 張。`, 'info');
  }else{
    statusBox.className = 'status';
  }

  selectedFiles = files;

  preview.innerHTML = '';
  files.slice(0, 12).forEach(file=>{
    const img = document.createElement('img');
    img.alt = file.name;
    img.src = URL.createObjectURL(file);
    preview.appendChild(img);
  });

  countBox.style.display = files.length ? 'block' : 'none';
  countBox.textContent =
    files.length > 12
      ? `已選 ${files.length} 張（下方先預覽前 12 張）`
      : `已選 ${files.length} 張`;

  uploadBtn.disabled = files.length === 0;

  progressWrap.style.display = 'none';
  setProgress(0);
});

uploadBtn.addEventListener('click', async ()=>{
  if(!selectedFiles.length) return;

  const batches = splitIntoBatches(selectedFiles, BATCH_SIZE);

  uploadBtn.disabled = true;
  uploadBtn.textContent = '上傳中…';
  input.disabled=true;

  progressWrap.style.display = 'block';
  setProgress(0, `準備上傳 ${selectedFiles.length} 張照片`);
  setStatus(
    `正在分 ${batches.length} 批上傳，請先不要關閉頁面。`,
    'info'
  );

  let uploadedCount = 0;
  let duplicateCount=0;const uploadedIds=[];const generation=++backupGeneration;

  try{
    for(let i=0;i<batches.length;i++){
      const result = await uploadBatch(
        batches[i],
        i,
        batches.length
      );
      uploadedCount += result.count || batches[i].length;
      for(const file of result.files||[]){if(/^[a-f0-9]{64}$/.test(file.id))uploadedIds.push(file.id);if(file.duplicate)duplicateCount++;}
      selectedFiles=selectedFiles.slice(batches[i].length);
    }

    setProgress(100, '本機接收完成，確認雲端備份中');
    setStatus(
      `已收到 ${uploadedCount} 張照片，正在確認雲端備份進度…`,
      'info'
    );

    input.value = '';
    selectedFiles = [];
    preview.innerHTML = '';
    countBox.style.display = 'none';
    monitorBackup(uploadedIds,uploadedCount,duplicateCount,generation);
  }catch(error){
    setStatus(
      `❌ 上傳中斷：${error.message}。已完成的批次不會重傳，可以重新選剩下的照片再試。`,
      'err'
    );
  }finally{
    input.disabled=false;
    uploadBtn.disabled = selectedFiles.length === 0;
    uploadBtn.textContent = '上傳給新郎新娘 💛';
  }
});
