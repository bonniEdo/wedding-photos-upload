(function(){
 function describe(data){
  const files=data.files||[],done=files.filter(f=>f.status==='done').length;
  const progress=`雲端已備份 ${done} / ${files.length} 張。`;
  if(!data.driveEnabled)return {type:'err',stop:true,text:progress+'目前雲端備份未啟用；照片已保存在本機，請新人檢查設定。'};
  if(files.some(f=>f.status==='missing'))return {type:'err',stop:true,text:'部分照片沒有後端儲存紀錄，請新人檢查服務。'};
  if(files.length&&done===files.length)return {type:'ok',stop:true,text:progress+' ✅ 雲端備份完成！人物相簿仍可能在分析中。'};
  if(data.connectionState==='failed')return {type:'err',stop:false,text:progress+(data.connectionError||'Google 授權失敗，請新人檢查。')+' 原圖仍保存在本機。'};
  if(data.connectionState==='authorizing')return {type:'info',stop:false,text:progress+'等待新人完成 Google 授權，原圖已保存在本機。'};
  const failed=files.find(f=>f.message);
  if(failed)return {type:'err',stop:false,text:progress+failed.message+' 原圖仍保存在本機。'};
  return {type:'info',stop:false,text:progress+'正在背景備份，不需要重新上傳。'};
 }
 window.BackupView=Object.freeze({describe});
})();
