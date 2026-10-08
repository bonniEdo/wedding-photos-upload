const crypto=require('crypto');
module.exports=function attachAdmin(app,{password,secureCookie,faceURL,driveSync,backupStatus}){
  const express=require('express');
  const configured=typeof password==='string' && Array.from(password).length>=12;
  const passwordHash=crypto.createHash('sha256').update(password||'').digest();
  const internalKey=crypto.createHash('sha256').update('wedding-face-admin:'+(password||'')).digest('hex');
  const sessions=new Map(), attempts=new Map();
  const ttl=8*60*60*1000;
  const cookieOptions={httpOnly:true,sameSite:'strict',secure:secureCookie,path:'/api/admin',maxAge:ttl};
  function token(req){
    const bearer=(req.get('Authorization')||'').match(/^Bearer ([a-f0-9]{64})$/);
    if(bearer)return bearer[1];
    const raw=(req.headers.cookie||'').split(';').map(s=>s.trim()).find(s=>s.startsWith('wedding_admin='));
    const value=raw?.slice('wedding_admin='.length);
    return /^[a-f0-9]{64}$/.test(value||'')?value:null;
  }
  app.use('/api/admin',(req,res,next)=>{res.set('Cache-Control','no-store');next();});
  function header(req,res,next){
    if(req.get('X-Admin-Request')!=='1')return res.status(403).json({ok:false,message:'管理請求驗證失敗'});
    next();
  }
  app.post('/api/admin/login',header,express.json({limit:'8kb'}),(req,res)=>{
    if(!configured)return res.status(503).json({ok:false,message:'請先在 .env 設定至少 12 字元的 ADMIN_PASSWORD，並重啟兩個服務'});
    const now=Date.now(), ip=req.ip;
    for(const [key,value] of attempts)if(value.until<=now)attempts.delete(key);
    if(attempts.size>=1000 && !attempts.has(ip))return res.status(429).json({ok:false,message:'登入人數較多，請稍後再試'});
    const record=attempts.get(ip)||{count:0,until:now+60000};
    if(record.count>=5){res.set('Retry-After',String(Math.max(1,Math.ceil((record.until-now)/1000))));return res.status(429).json({ok:false,message:'嘗試次數較多，請稍後再試'});}
    record.count++;attempts.set(ip,record);
    const input=typeof req.body?.password==='string'?req.body.password:'';
    if(!crypto.timingSafeEqual(crypto.createHash('sha256').update(input).digest(),passwordHash))return res.status(401).json({ok:false,message:'管理密碼不正確'});
    const old=token(req);if(old)sessions.delete(old);
    const id=crypto.randomBytes(32).toString('hex');sessions.set(id,now+ttl);
    res.cookie('wedding_admin',id,cookieOptions).json({ok:true,adminToken:id,expiresIn:ttl/1000});
  });
  app.use('/api/admin',header,(req,res,next)=>{
    if(!configured)return res.status(503).json({ok:false,message:'請先設定 ADMIN_PASSWORD'});
    const id=token(req), expires=sessions.get(id);
    if(!expires||expires<=Date.now()){
      if(id)sessions.delete(id);return res.status(401).json({ok:false,message:'請先登入管理頁面'});
    }
    req.adminSession=id;next();
  });
  app.get('/api/admin/backup/status',(req,res)=>res.json(backupStatus()));
  app.get('/api/admin/drive/status',(req,res)=>res.json(driveSync.status()));
  app.post('/api/admin/drive/sync',(req,res)=>{const result=driveSync.requestScan();res.status(result.ok?202:503).json(result);});
  app.get('/api/admin/session',(req,res)=>res.json({ok:true}));
  app.post('/api/admin/logout',(req,res)=>{
    sessions.delete(req.adminSession);res.clearCookie('wedding_admin',{...cookieOptions,maxAge:undefined}).json({ok:true});
  });
  async function forward(req,res,route){
    try{
      const options={method:req.method,headers:{'X-Admin-Key':internalKey},signal:AbortSignal.timeout(120000)};
      if(req.method==='POST'){options.headers['Content-Type']='application/json';options.body=JSON.stringify(req.body);}
      const upstream=await fetch(faceURL+route,options);
      res.status(upstream.status).type('json').send(await upstream.text());
    }catch(e){res.status(503).json({ok:false,message:'人臉服務暫時無法使用，請確認 Python 服務已啟動'});}
  }
  app.get('/api/admin/people',(req,res)=>forward(req,res,'/admin/people'));
  app.post('/api/admin/merge',express.json({limit:'16kb'}),(req,res)=>forward(req,res,'/admin/merge'));
  app.post('/api/admin/undo',express.json({limit:'8kb'}),(req,res)=>forward(req,res,'/admin/undo'));
  setInterval(()=>{
    const now=Date.now();for(const [id,expires] of sessions)if(expires<=now)sessions.delete(id);
  },60000).unref();
};
