module.exports=function driveErrorMessage(error){
  const status=Number(error.response?.status||error.code||0);
  const reasons=(error.response?.data?.error?.errors||[]).map(e=>e.reason);
  const text=String(error.response?.data?.error?.message||error.message||'');
  if(/invalid_grant|invalid credentials|token.*expired/i.test(text)||status===401)return 'Google 授權已失效，請新人重新授權後重啟服務';
  if(reasons.includes('storageQuotaExceeded'))return 'Google Drive 儲存空間不足，請新人檢查容量';
  if(status===429||reasons.some(r=>/rateLimit|quotaExceeded/i.test(r)))return 'Google Drive 請求較多，正在等待重試';
  if(status===404)return '找不到 Google Drive 資料夾或帳號無權存取，請新人確認資料夾設定';
  if(status===403)return 'Google 權限不足，請新人確認目前授權帳號與資料夾寫入權限';
  return 'Google Drive 備份暫時失敗，會自動重試';
};
