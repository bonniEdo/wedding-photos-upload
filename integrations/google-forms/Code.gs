// Open Apps Script from the existing Google Form's editor and paste this file.
// Run setupWeddingForm once, then deploy as a web app. See README.md.
const WEDDING_FORM_URL = 'https://docs.google.com/forms/d/e/1FAIpQLSdkKuTSGS_u79FWXt1khwJmPGU2kSHNyYj_vrZDTjfjGS4UZg/viewform';
const WEDDING_FIELDS = [
  ['id','回覆編號','text'],['guestName','姓名','text'],['attending','是否參加','choice',['是否出席']],
  ['guestCount','出席人數','text',['出席人數（含本人）']],['childSeats','兒童座椅','text'],
  ['vegetarian','是否素食','choice'],['vegetarianCount','素食人數','text'],
  ['dietNotes','飲食備註','paragraph'],['message','給新人的話','paragraph',['給新人的一段話']],
  ['photos','共同回憶照片連結','paragraph'],['fingerprint','資料校驗碼','text'],
];

function setupWeddingForm() {
  const lock=LockService.getScriptLock();lock.waitLock(30000);
  try {
    const props=PropertiesService.getScriptProperties();
    const savedId=props.getProperty('WEDDING_FORM_ID');
    const form=FormApp.getActiveForm() || (savedId?FormApp.openById(savedId):null);
    if(!form)throw new Error('請從「參加的人」Google 表單編輯頁的 Apps Script 開啟本程式，再執行設定。填寫網址不能當成編輯表單 ID。');
    if(form.getPublishedUrl().split(/[?#]/)[0]!==WEDDING_FORM_URL)throw new Error('目前開啟的表單與喜帖指定的 Google 表單不同，設定已停止。');
    if(savedId && savedId!==form.getId())throw new Error('這個指令碼已綁定其他表單，請改用指定表單的 Apps Script 專案。');
    props.setProperty('WEDDING_FORM_ID',form.getId());
    const items={};
    WEDDING_FIELDS.forEach(function(field){
      const key=field[0],title=field[1],type=field[2];
      const titles=[title].concat(field[3] || []);
      const matches=form.getItems().filter(function(existing){return titles.indexOf(existing.getTitle())>=0;});
      if(matches.length>1)throw new Error('表單有重複題目「'+title+'」，請先確認要保留的欄位。');
      let item=matches[0];
      if(!item){
        item=type==='choice'?form.addMultipleChoiceItem().setChoiceValues(['是','否']):type==='paragraph'?form.addParagraphTextItem():form.addTextItem();
        item.setTitle(title);
      }
      const expected={text:'TEXT',paragraph:'PARAGRAPH_TEXT',choice:'MULTIPLE_CHOICE'}[type];
      if(String(item.getType())!==expected)throw new Error('題目「'+title+'」的類型須為 '+expected+'，請確認後再執行。');
      items[key]={id:item.getId(),type:type};
      if(type==='choice'){
        const choices=item.asMultipleChoiceItem().getChoices().map(function(choice){return choice.getValue();});
        const yes=choices.find(function(value){return ['是','會出席','我會出席'].indexOf(value)>=0;});
        const no=choices.find(function(value){return ['否','無法出席','這次無法到場'].indexOf(value)>=0;});
        if(!yes || !no)throw new Error('題目「'+title+'」須有「是／否」或「會出席／無法出席」選項。');
        items[key].yes=yes;items[key].no=no;
      }
    });
    props.setProperty('WEDDING_FORM_ITEMS',JSON.stringify(items));
    let sheetId=form.getDestinationId() || props.getProperty('WEDDING_SHEET_ID');
    if(!sheetId){sheetId=SpreadsheetApp.create('婚禮賓客回覆整理').getId();props.setProperty('WEDDING_SHEET_ID',sheetId);}
    if(form.getDestinationId()!==sheetId)form.setDestination(FormApp.DestinationType.SPREADSHEET,sheetId);
    props.setProperty('WEDDING_SHEET_ID',sheetId);
    let folderId=props.getProperty('WEDDING_PHOTO_FOLDER_ID');
    if(!folderId){folderId=DriveApp.createFolder('婚禮・賓客共同回憶（私人）').getId();props.setProperty('WEDDING_PHOTO_FOLDER_ID',folderId);}
    if(!props.getProperty('WEDDING_SHARED_SECRET'))props.setProperty('WEDDING_SHARED_SECRET',Utilities.getUuid()+Utilities.getUuid());
    console.log('Google 表單管理：'+form.getEditUrl());
    console.log('回覆試算表：https://docs.google.com/spreadsheets/d/'+sheetId+'/edit');
    console.log('私人照片資料夾：https://drive.google.com/drive/folders/'+folderId);
    console.log('設定完成。請到「專案設定 → 指令碼屬性」複製 WEDDING_SHARED_SECRET，勿公開分享。');
  } finally {lock.releaseLock();}
}

function weddingJSON_(value){return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);}
function weddingAssert_(condition,code){if(!condition)throw new Error(code);}
function weddingFolder_(id,props){
  const parent=DriveApp.getFolderById(props.getProperty('WEDDING_PHOTO_FOLDER_ID'));
  const found=parent.getFoldersByName(id);
  return found.hasNext()?found.next():parent.createFolder(id);
}
function weddingDigest_(bytes){return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,bytes).map(function(b){return ('0'+((b+256)%256).toString(16)).slice(-2);}).join('');}

function doPost(event){
  let lock;
  try {
    const props=PropertiesService.getScriptProperties();
    const payload=JSON.parse(event.postData.contents);
    const secret=props.getProperty('WEDDING_SHARED_SECRET');
    weddingAssert_(secret && typeof payload.secret==='string' && payload.secret===secret,'AUTH');
    weddingAssert_(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(payload.id),'INVALID_ID');
    weddingAssert_(props.getProperty('WEDDING_FORM_ITEMS') && props.getProperty('WEDDING_PHOTO_FOLDER_ID'),'NOT_CONFIGURED');
    lock=LockService.getScriptLock();lock.waitLock(10000);
    if(payload.action==='upload')return weddingJSON_(weddingUpload_(payload,props));
    if(payload.action==='submit')return weddingJSON_(weddingSubmit_(payload,props));
    throw new Error('UNKNOWN_ACTION');
  } catch(error){
    const known=['AUTH','INVALID_ID','NOT_CONFIGURED','INVALID_PHOTO','INVALID_RECORD','CONFLICT','UNKNOWN_ACTION'];
    return weddingJSON_({ok:false,code:known.indexOf(error.message)>=0?error.message:'RETRY'});
  } finally {if(lock && lock.hasLock())lock.releaseLock();}
}

function weddingUpload_(payload,props){
  const photo=payload.photo || {};
  const extensions={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'};
  weddingAssert_(extensions[photo.mimeType] && Number.isInteger(payload.index) && payload.index>=0 && payload.index<3 && /^[a-f0-9]{64}$/.test(photo.sha256) && typeof photo.base64==='string' && photo.base64.length<=13981016,'INVALID_PHOTO');
  const bytes=Utilities.base64Decode(photo.base64);
  weddingAssert_(bytes.length>0 && bytes.length<=10*1024*1024 && weddingDigest_(bytes)===photo.sha256,'INVALID_PHOTO');
  const folder=weddingFolder_(payload.id,props);
  const filename=(payload.index+1)+'-'+photo.sha256+extensions[photo.mimeType];
  const found=folder.getFilesByName(filename);
  // The stable filename makes a retry safe even if the first HTTP response was lost.
  const file=found.hasNext()?found.next():folder.createFile(Utilities.newBlob(bytes,photo.mimeType,filename));
  return {ok:true,id:payload.id,fileId:file.getId()};
}

function weddingSubmit_(payload,props){
  const record=payload.record || {};
  const childSeats=record.childSeats===undefined?0:record.childSeats;
  weddingAssert_(record.id===payload.id && typeof record.guestName==='string' && record.guestName.trim().length>0 && record.guestName.length<=80 && typeof record.attending==='boolean' && /^[a-f0-9]{64}$/.test(record.fingerprint),'INVALID_RECORD');
  weddingAssert_(Number.isInteger(record.guestCount) && record.guestCount>=(record.attending?1:0) && record.guestCount<=20 && Number.isInteger(record.vegetarianCount) && record.vegetarianCount>=0 && record.vegetarianCount<=record.guestCount,'INVALID_RECORD');
  weddingAssert_(Number.isInteger(childSeats) && childSeats>=0 && childSeats<=record.guestCount,'INVALID_RECORD');
  weddingAssert_(typeof record.message==='string' && record.message.length<=2000 && typeof record.dietNotes==='string' && record.dietNotes.length<=300 && Array.isArray(record.photos) && record.photos.length<=3 && (record.attending || (record.guestCount===0 && record.vegetarianCount===0 && record.photos.length===0)),'INVALID_RECORD');
  const form=FormApp.openById(props.getProperty('WEDDING_FORM_ID'));
  const fields=JSON.parse(props.getProperty('WEDDING_FORM_ITEMS'));
  const idItem=form.getItemById(fields.id.id);
  // Look up the ID in Forms itself, so an interruption after submit cannot duplicate a response.
  const existing=form.getResponses().find(function(response){const item=response.getResponseForItem(idItem);return item && item.getResponse()===record.id;});
  if(existing){
    const prior=existing.getResponseForItem(form.getItemById(fields.fingerprint.id));
    weddingAssert_(prior && prior.getResponse()===record.fingerprint,'CONFLICT');
    return {ok:true,id:record.id,responseId:existing.getId()};
  }
  const links=[];
  if(record.photos.length){
    const folder=weddingFolder_(record.id,props);
    record.photos.forEach(function(photo,index){
      weddingAssert_(photo.index===index && /^[a-zA-Z0-9_-]+$/.test(photo.fileId) && /^[a-f0-9]{64}$/.test(photo.sha256),'INVALID_PHOTO');
      const file=DriveApp.getFileById(photo.fileId);
      const parents=file.getParents();let belongs=false;
      while(parents.hasNext())if(parents.next().getId()===folder.getId())belongs=true;
      weddingAssert_(belongs && file.getName().indexOf((index+1)+'-'+photo.sha256)===0,'INVALID_PHOTO');
      links.push(file.getUrl());
    });
  }
  const values={id:record.id,guestName:record.guestName,attending:record.attending?fields.attending.yes:fields.attending.no,guestCount:String(record.guestCount),childSeats:String(childSeats),vegetarian:record.vegetarianCount>0?fields.vegetarian.yes:fields.vegetarian.no,vegetarianCount:String(record.vegetarianCount),dietNotes:record.dietNotes,message:record.message,photos:links.join('\n'),fingerprint:record.fingerprint};
  let response=form.createResponse();
  WEDDING_FIELDS.forEach(function(field){
    const info=fields[field[0]],item=form.getItemById(info.id);
    const typed=info.type==='choice'?item.asMultipleChoiceItem():info.type==='paragraph'?item.asParagraphTextItem():item.asTextItem();
    response=response.withItemResponse(typed.createResponse(values[field[0]]));
  });
  response=response.submit();
  return {ok:true,id:record.id,responseId:response.getId()};
}
