(function(){
  const configured=(window.WEDDING_CONFIG?.API_BASE||'').replace(/\/$/,'');
  const base=configured||(location.port==='5173'?`${location.protocol}//${location.hostname}:3000`:'');
  let adminToken=null;
  function url(p){return /^https?:\/\//.test(p)?p:base+p;}
  function request(p,options={}){
    const headers=new Headers(options.headers||{});
    const pathname=new URL(url(p),location.href).pathname;
    if(adminToken&&pathname.startsWith('/api/admin/'))headers.set('Authorization','Bearer '+adminToken);
    return fetch(url(p),{...options,credentials:'include',headers});
  }
  window.WeddingAPI=Object.freeze({base,url,request,setAdminToken(value){adminToken=value||null;}});
})();
