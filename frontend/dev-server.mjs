// Plain static development server. Node 22+, no frontend npm dependencies.
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png','.jpg':'image/jpeg'};
const server=http.createServer(async(req,res)=>{
 try{
  const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
  const target=path.resolve(root,'.'+(pathname==='/'?'/photos.html':pathname));
  if(!target.startsWith(root+path.sep))return res.writeHead(403).end();
  if(!(await fs.stat(target)).isFile())return res.writeHead(404).end();
  res.writeHead(200,{'Content-Type':types[path.extname(target)]||'application/octet-stream','Cache-Control':'no-store'});
  res.end(await fs.readFile(target));
 }catch{res.writeHead(404).end();}
});
server.listen(5173,'0.0.0.0',()=>console.log('Frontend: http://localhost:5173/photos.html\nPeople: http://localhost:5173/people.html\nAdmin: http://localhost:5173/admin-people.html'));
