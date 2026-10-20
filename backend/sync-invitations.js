// Reconcile locally saved RSVPs after a network interruption or first-time setup.
const path=require('node:path');
const fs=require('node:fs/promises');
require('dotenv').config({path:path.join(__dirname,'.env')});
async function main() {
  const data=path.resolve(process.env.DATA_DIR||path.join(__dirname,'data'));
  const googleForms=require('./google-forms')({data});
  if (!googleForms.enabled) throw new Error('Google 表單提交已停用，請確認 WEDDING_GOOGLE_FORMS_ENABLED。');
  let directories;
  try {directories=await fs.readdir(path.join(data,'rsvps'));} catch(error) {if(error.code!=='ENOENT')throw error;directories=[];}
  let done=0,failed=0;
  for (const id of directories.filter(name=>/^[a-f0-9-]{36}$/i.test(name))) {
    try {await googleForms.sync(id);done++;} catch {failed++;console.error(`待重試：${id}`);}
  }
  console.log(`Google 表單同步完成：${done} 筆已確認，${failed} 筆待重試。`);
  if(failed)process.exitCode=1;
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
