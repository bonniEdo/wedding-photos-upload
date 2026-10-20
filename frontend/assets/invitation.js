(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const settings = window.WEDDING_CONFIG?.INVITATION || {};
  const config = {
    isSample:true, coupleNames:'子謙 & 以安', date:'2027-05-22', welcomeTime:'11:30',
    banquetTime:'12:00', endTime:'15:00', replyBy:'2027-04-30',
    venueName:'幸福花園婚宴會館', venueRoom:'花園廳 · 2F',
    venueAddress:'台北市・詳細地址待新人確認', venueNote:'交通資訊將隨正式喜帖提供。', mapsQuery:'', ...settings,
  };
  const date = new Date(`${config.date}T12:00:00+08:00`);
  const shortDate = config.date.replaceAll('-', '.');
  document.querySelectorAll('[data-couple]').forEach(el => { el.textContent = config.coupleNames; });
  document.querySelectorAll('[data-short-date]').forEach(el => { el.textContent = shortDate; });
  document.querySelectorAll('[data-weekday]').forEach(el => { el.textContent = new Intl.DateTimeFormat('zh-TW', {weekday:'long',timeZone:'Asia/Taipei'}).format(date); });
  document.querySelectorAll('[data-long-date]').forEach(el => { el.textContent = new Intl.DateTimeFormat('zh-TW', {year:'numeric',month:'long',day:'numeric',timeZone:'Asia/Taipei'}).format(date); });
  for (const [id, value] of Object.entries({'welcome-time':config.welcomeTime,'banquet-time':config.banquetTime,'venue-name':config.venueName,'venue-room':config.venueRoom,'venue-address':config.venueAddress,'venue-note':config.venueNote,'reply-by':config.replyBy.replaceAll('-','.')})) $(id).textContent = value;
  document.title = `${config.coupleNames}｜我們結婚了`;
  $('sample-label').hidden = !config.isSample;
  if (config.mapsQuery) {
    $('map-link').href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(config.mapsQuery)}`;
    $('map-link').hidden = false;
  }
  if (config.googleFormUrl) {
    try {
      const url = new URL(config.googleFormUrl);
      if (url.origin === 'https://docs.google.com' && /^\/forms\/d\/e\/[a-zA-Z0-9_-]+\/viewform$/.test(url.pathname) && !url.username && !url.password) {
        $('google-form-link').href = url.href;
        $('google-form-alternative').hidden = false;
      }
    } catch { /* Leave the optional link hidden if the configured URL is invalid. */ }
  }

  // UTC dates make the downloaded event display correctly outside Taiwan too.
  function calendarDate(time) { return new Date(`${config.date}T${time}:00+08:00`).toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,''); }
  function escapeCalendar(value) { return value.replaceAll('\\','\\\\').replaceAll('\n','\\n').replaceAll(',','\\,').replaceAll(';','\\;'); }
  function foldCalendar(line) {
    const encoder = new TextEncoder();
    let result = '', length = 0;
    for (const char of line) {
      const size = encoder.encode(char).length;
      if (length + size > 73) { result += '\r\n '; length = 1; }
      result += char; length += size;
    }
    return result;
  }
  $('calendar-button').addEventListener('click', () => {
    const lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Wedding Invitation//ZH-TW','CALSCALE:GREGORIAN','BEGIN:VEVENT',
      `UID:wedding-${config.date}-${encodeURIComponent(config.coupleNames)}@invitation`,
      `DTSTAMP:${new Date().toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,'')}`,
      `DTSTART:${calendarDate(config.welcomeTime)}`,`DTEND:${calendarDate(config.endTime)}`,
      `SUMMARY:${escapeCalendar(`${config.isSample?'【範例】':''}${config.coupleNames} 的婚禮`)}`,
      `LOCATION:${escapeCalendar(`${config.venueName} ${config.venueRoom} ${config.venueAddress}`)}`,
      `DESCRIPTION:${escapeCalendar(`${config.isSample?'婚宴資訊為範例，請以新人正式通知為準。\n':''}${config.welcomeTime} 迎賓，${config.banquetTime} 開席。期待與你分享幸福！`)}`,
      'END:VEVENT','END:VCALENDAR',''];
    const url = URL.createObjectURL(new Blob([lines.map(foldCalendar).join('\r\n')],{type:'text/calendar;charset=utf-8'}));
    const link = document.createElement('a'); link.href = url; link.download = 'wedding-invitation.ics'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  const form = $('rsvp-form');
  let photos = [], previewUrls = [], submitting = false, requestId = null;
  function syncVegetarians() {
    const previous = Number($('vegetarian-count').value);
    $('vegetarian-count').replaceChildren(...Array.from({length:Number($('guest-count').value)+1}, (_,i) => new Option(i ? `${i} 位素食` : '無素食需求', i)));
    $('vegetarian-count').value = Math.min(previous, Number($('guest-count').value));
    const seats = Number($('child-seats').value);
    $('child-seats').replaceChildren(...Array.from({length:Number($('guest-count').value)+1}, (_,i) => new Option(i ? `${i} 張` : '不需要', i)));
    $('child-seats').value = Math.min(seats, Number($('guest-count').value));
  }
  form.addEventListener('change', event => {
    requestId = null;
    if (event.target.name === 'attendance') {
      const attending = event.target.value === 'yes';
      $('attending-details').hidden = !attending;
      $('attending-details').disabled = !attending;
    }
    if (event.target.id === 'guest-count') syncVegetarians();
  });
  form.addEventListener('input', () => {
    requestId = null;
    $('message-count').textContent = `${$('guest-message').value.length} / 2000`;
  });
  function renderPhotos() {
    previewUrls.forEach(URL.revokeObjectURL); previewUrls = [];
    $('photo-previews').replaceChildren();
    photos.forEach((file,index) => {
      const figure = document.createElement('figure'); figure.className = 'photo-preview';
      const image = document.createElement('img'); image.src = URL.createObjectURL(file); image.alt = `共同回憶照片 ${index+1}`; previewUrls.push(image.src);
      const caption = document.createElement('figcaption'); caption.textContent = file.name;
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label',`移除照片 ${file.name}`);
      remove.addEventListener('click', () => {photos.splice(index,1);requestId=null;renderPhotos();$('photo-status').textContent=`已選擇 ${photos.length} 張照片。`; $('memory-photos').focus();});
      figure.append(image,remove,caption); $('photo-previews').append(figure);
    });
  }
  function addPhotos(files) {
    if (submitting || $('attending-details').disabled) return;
    const errors = [];
    for (const file of files) {
      if (!['image/jpeg','image/png','image/webp'].includes(file.type)) {errors.push(`${file.name}：請選 JPG、PNG 或 WebP。`);continue;}
      if (!file.size || file.size > 10*1024*1024) {errors.push(`${file.name}：照片須介於 1 byte 與 10 MB。`);continue;}
      if (photos.some(p => p.name===file.name && p.size===file.size && p.lastModified===file.lastModified)) continue;
      if (photos.length >= 3) {errors.push('最多只能選擇 3 張照片。');break;}
      photos.push(file);
    }
    requestId = null; renderPhotos();
    $('photo-status').textContent = errors.length ? errors.join(' ') : `已選擇 ${photos.length} 張照片，可點右上角移除。`;
    $('memory-photos').value = '';
  }
  $('memory-photos').addEventListener('change', event => addPhotos(event.target.files));
  for (const name of ['dragenter','dragover']) $('upload-area').addEventListener(name, event => {event.preventDefault();if(!submitting)$('upload-area').classList.add('is-dragging');});
  for (const name of ['dragleave','drop']) $('upload-area').addEventListener(name, event => {event.preventDefault();$('upload-area').classList.remove('is-dragging');});
  $('upload-area').addEventListener('drop', event => addPhotos(event.dataTransfer.files));
  function newId() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = bytes[6]&15|64; bytes[8] = bytes[8]&63|128;
    const hex = [...bytes].map(b => b.toString(16).padStart(2,'0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (submitting || !form.reportValidity()) return;
    if (!$('guest-name').value.trim()) {$('guest-name').setCustomValidity('請留下你的姓名。');$('guest-name').reportValidity();return;}
    const data = new FormData(form);
    const attending = data.get('attendance') === 'yes';
    requestId ||= newId(); data.set('requestId',requestId);
    data.set('guestName',$('guest-name').value.trim());
    if (attending) photos.forEach(file => data.append('photos',file));
    const controls = [...form.querySelectorAll('input,select,textarea,button')];
    const disabled = controls.map(control => control.disabled);
    controls.forEach(control => {control.disabled=true;});
    submitting = true; $('submit-button').textContent = photos.length && attending ? '正在寄出回覆與照片…' : '正在寄出你的心意…';
    $('form-status').textContent = '正在儲存回覆，請稍候。';
    try {
      const response = await window.WeddingAPI.request('/api/invitations/rsvp',{method:'POST',body:data,signal:AbortSignal.timeout(120000)});
      const result = await response.json().catch(() => null);
      if (!response.ok || !result?.ok) throw new Error(result?.message || '回覆尚未送出，請稍後重試。');
      $('success-message').textContent = attending ? `謝謝你，${data.get('guestName')}！期待在這個好日子，和你一起笑、一起慶祝。` : `謝謝你，${data.get('guestName')}。雖然這次無法相聚，你的祝福依然讓這一天更加完整。`;
      $('success-summary').textContent = attending ? `已登記 ${data.get('guestCount')} 位出席・${data.get('vegetarianCount')} 位素食${Number(data.get('childSeats'))?`・${data.get('childSeats')} 張兒童座椅`:''}${photos.length?`・${photos.length} 張共同回憶`:''}` : '已記錄無法出席，並為你收藏這份心意。';
      $('receipt').textContent = `回覆編號 ${result.id.slice(0,8)}`;
      if (result.googleForms?.synced) $('receipt').textContent += ' · 回覆已同步';
      form.hidden = true; $('success-panel').hidden = false; $('success-panel').focus();
      previewUrls.forEach(URL.revokeObjectURL); previewUrls = []; photos = [];
    } catch (error) {
      $('form-status').textContent = error instanceof TypeError || error.name==='TimeoutError' ? '目前無法連線或連線逾時，回覆尚未確認送達。請確認網路與後端服務，再按一次寄出；填寫內容與照片會保留。' : error.message;
      $('form-status').focus();
    } finally {
      submitting=false;controls.forEach((control,i) => {control.disabled=disabled[i];});
      $('submit-button').replaceChildren(document.createTextNode('寄出回覆與祝福 '));
      const arrow = document.createElement('span'); arrow.textContent='↗';arrow.setAttribute('aria-hidden','true');$('submit-button').append(arrow);
    }
  });
  $('guest-name').addEventListener('input', () => $('guest-name').setCustomValidity(''));
})();
