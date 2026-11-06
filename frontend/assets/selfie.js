const $ = id => document.getElementById(id);
let queryFile = null;
let previewURL = null;
let searchController = null;

function selectQuery(file) {
  searchController?.abort();
  searchController = null;
  queryFile = file;
  $('search').disabled = !file;
  $('cancelSearch').hidden = true;
  $('results').hidden = true;
  $('searchStatus').textContent = '';
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = file ? URL.createObjectURL(file) : null;
  const preview = $('queryPreview');
  preview.hidden = !file;
  if (file) preview.src = previewURL;
  else preview.removeAttribute('src');
}

function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

async function search() {
  if (!queryFile) return;
  searchController?.abort();
  const controller = new AbortController();
  const { signal } = controller;
  searchController = controller;
  const form = new FormData();
  form.append('file', queryFile);
  $('cancelSearch').hidden = false;
  $('search').disabled = true;
  $('searchStatus').textContent = '正在找照片…';

  try {
    for (let attempt = 0; attempt < 30; attempt++) {
      const response = await WeddingAPI.request('/api/face/find', { method: 'POST', body: form, signal });
      const data = await response.json();
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (response.status === 429) {
        $('searchStatus').textContent = '搜尋的人較多，正在等待…';
        await pause(3000 + Math.random() * 3000, signal);
        continue;
      }
      if (!response.ok) throw new Error(data.message || data.detail || '暫時無法使用，請稍後再試');
      if (!data.ok) break;
      const quality = data.qualityLimited ? ' 自拍較模糊或細節不足，換清楚正面照可能找到更多照片。' : '';
      const partial = data.queryPartial ? ' 本次僅完成部分偵測，可換較小的單人照再試。' : '';
      renderPhotos(data.matches, `找到 ${data.matches.length} 張可能有你的照片`, '結果依相似度排列，請確認是否為本人。' + quality + partial);
      $('searchStatus').textContent = '搜尋完成；補傳照片分析完成後，可以再搜尋一次。';
      $('results').scrollIntoView({ behavior: scrollBehavior() });
      return;
    }
    throw new Error('等待較久，請稍後再試');
  } catch (error) {
    if (searchController === controller) {
      $('searchStatus').textContent = signal.aborted ? '已取消搜尋。' : error.message;
    }
  } finally {
    if (searchController === controller) {
      searchController = null;
      $('cancelSearch').hidden = true;
      $('search').disabled = !queryFile;
    }
  }
}

$('selfie').onchange = () => selectQuery($('selfie').files[0]);
$('camera').onchange = () => selectQuery($('camera').files[0]);
$('cancelSearch').onclick = () => searchController?.abort();
$('search').onclick = search;
window.addEventListener('pagehide', () => searchController?.abort());
