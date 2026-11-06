function renderPhotos(items, title, note) {
  const results = document.getElementById('results');
  const photos = document.getElementById('photos');
  results.hidden = false;
  document.getElementById('viewTitle').textContent = title;
  document.getElementById('resultNote').textContent = note;
  photos.replaceChildren();

  if (!items.length) {
    const message = document.createElement('p');
    message.textContent = '目前還沒找到，可以換一張自拍，或等新照片分析完成再試。';
    photos.append(message);
    return;
  }

  for (const item of items) {
    const card = document.createElement('div');
    card.className = 'photo';
    const view = document.createElement('a');
    view.href = WeddingAPI.url(item.url);
    view.target = '_blank';
    view.rel = 'noopener';
    const image = document.createElement('img');
    image.loading = 'lazy';
    image.src = WeddingAPI.url(item.url);
    image.alt = '婚禮照片';
    view.append(image);

    const download = document.createElement('a');
    download.href = WeddingAPI.url(`/api/photos/${encodeURIComponent(item.id)}/original`);
    download.textContent = '下載原始照片 ↓';
    download.target = '_blank';
    download.rel = 'noopener';
    card.append(view, download);
    photos.append(card);
  }
}

function scrollBehavior() {
  return matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
}
