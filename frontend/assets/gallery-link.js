(async function(){
  const links=[...document.querySelectorAll('[data-all-photos]')];
  try{
    let url=window.WEDDING_CONFIG?.ALL_PHOTOS_URL;
    if(!url){const r=await WeddingAPI.request('/api/config',{cache:'no-store'});if(!r.ok)throw new Error();url=(await r.json()).allPhotosUrl;}
    const parsed=new URL(url);
    if(parsed.protocol!=='https:' || parsed.hostname!=='drive.google.com')throw new Error();
    for(const link of links){link.href=parsed.href;link.removeAttribute('aria-disabled');link.title='開啟 Google Drive 查看全部照片';}
  }catch{for(const link of links){link.title='全部照片連結尚未設定或服務暫時無法連線';link.addEventListener('click',event=>{event.preventDefault();alert(link.title);});}}
})();
