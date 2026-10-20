import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {randomUUID,createHash} from 'node:crypto';
const properties=new Map(),forms=new Map(),folders=new Map(),files=new Map();let sheets=0,sequence=0,activeForm=null;
const iterator=array=>{let index=0;return {hasNext:()=>index<array.length,next:()=>array[index++]};};
function makeFolder(name,parent=null) {
  const id='folder-'+sequence++,folder={getId:()=>id,getFoldersByName:n=>iterator([...folders.values()].filter(f=>f.name===n && f.parent===id)),createFolder:n=>makeFolder(n,id),getFilesByName:n=>iterator([...files.values()].filter(f=>f.name===n && f.parent===id)),createFile:blob=>{
    const fileId='file-'+sequence++,file={name:blob.name,parent:id,getId:()=>fileId,getName:()=>blob.name,getParents:()=>iterator([folder]),getUrl:()=>`https://drive.google.com/file/d/${fileId}/view`};files.set(fileId,file);return file;
  },name,parent};folders.set(id,folder);return folder;
}
function makeForm() {
  const id='form-'+sequence++,items=[],responses=[];let destination=null;
  function addItem(type) {const itemId=sequence++,item={getId:()=>itemId,getTitle:()=>item.title,getType:()=>type,setTitle:title=>(item.title=title,item),setChoiceValues:values=>(item.choices=values,item),getChoices:()=>item.choices.map(value=>({getValue:()=>value})),asTextItem:()=>item,asMultipleChoiceItem:()=>item,asParagraphTextItem:()=>item,createResponse:value=>({item,value})};items.push(item);return item;}
  const form={getId:()=>id,getPublishedUrl:()=> 'https://docs.google.com/forms/d/e/1FAIpQLSdkKuTSGS_u79FWXt1khwJmPGU2kSHNyYj_vrZDTjfjGS4UZg/viewform',getItems:()=>items,addTextItem:()=>addItem('TEXT'),addParagraphTextItem:()=>addItem('PARAGRAPH_TEXT'),addMultipleChoiceItem:()=>addItem('MULTIPLE_CHOICE'),getDestinationId:()=>destination,setDestination:(type,sheet)=>{destination=sheet;},getEditUrl:()=>`https://docs.google.com/forms/d/${id}/edit`,getItemById:id=>items.find(item=>item.getId()===id),getResponses:()=>responses,createResponse:()=>{
    const answers=new Map(),responseId='response-'+sequence++;
    const response={withItemResponse:answer=>(answers.set(answer.item.getId(),answer.value),response),submit:()=>{responses.push(response);return response;},getId:()=>responseId,getResponseForItem:item=>answers.has(item.getId())?{getResponse:()=>answers.get(item.getId())}:null};return response;
  }};forms.set(id,form);return form;
}
const context={console:{log(){}},FormApp:{create:()=>{throw Error('must not create a new form');},getActiveForm:()=>activeForm,openById:id=>forms.get(id),DestinationType:{SPREADSHEET:'sheet'}},SpreadsheetApp:{create:()=>({getId:()=>`sheet-${++sheets}`})},DriveApp:{createFolder:makeFolder,getFolderById:id=>folders.get(id),getFileById:id=>files.get(id)},PropertiesService:{getScriptProperties:()=>({getProperty:key=>properties.get(key)||null,setProperty:(key,value)=>properties.set(key,value)})},LockService:{getScriptLock:()=>{let held=false;return {waitLock(){held=true;},hasLock:()=>held,releaseLock(){held=false;}};}},Utilities:{getUuid:randomUUID,DigestAlgorithm:{SHA_256:'sha256'},computeDigest:(algorithm,bytes)=>[...createHash('sha256').update(Buffer.from(bytes)).digest()],base64Decode:value=>[...Buffer.from(value,'base64')],newBlob:(bytes,mimeType,name)=>({bytes,mimeType,name})},ContentService:{MimeType:{JSON:'JSON'},createTextOutput:text=>({setMimeType:()=>text})}};
vm.createContext(context);vm.runInContext(await readFile(new URL('../../integrations/google-forms/Code.gs',import.meta.url),'utf8'),context);
assert.throws(()=>context.setupWeddingForm(),/Apps Script/,'requires the supplied form instead of silently creating a new one');
activeForm=makeForm();
activeForm.addTextItem().setTitle('姓名');
activeForm.addMultipleChoiceItem().setTitle('是否參加').setChoiceValues(['是','否']);
activeForm.addTextItem().setTitle('出席人數');
activeForm.addTextItem().setTitle('兒童座椅');
activeForm.addMultipleChoiceItem().setTitle('是否素食').setChoiceValues(['否','是']);
activeForm.addParagraphTextItem().setTitle('給新人的話');
activeForm.setDestination('sheet','existing-sheet');
context.setupWeddingForm();context.setupWeddingForm();
assert.equal(forms.size,1);assert.equal(sheets,0,'keeps the existing linked spreadsheet');assert.equal(folders.size,1);assert.equal(activeForm.getItems().length,11,'setup reuses six existing questions and adds five supplemental fields only once');
const secret=properties.get('WEDDING_SHARED_SECRET');assert(secret.length>=32);
const post=payload=>JSON.parse(context.doPost({postData:{contents:JSON.stringify({...payload,secret})}}));
assert.equal(JSON.parse(context.doPost({postData:{contents:JSON.stringify({secret:'wrong'})}})).code,'AUTH');
const id=randomUUID(),bytes=Buffer.from('test'),sha256=createHash('sha256').update(bytes).digest('hex');
const payload={action:'upload',id,index:0,photo:{name:'回憶.png',mimeType:'image/png',sha256,base64:bytes.toString('base64')}};
const first=post(payload),again=post(payload);assert(first.ok);assert.equal(first.fileId,again.fileId);assert.equal(files.size,1,'upload retry does not duplicate a file');
assert.equal(post({...payload,photo:{...payload.photo,sha256:'f'.repeat(64)}}).code,'INVALID_PHOTO');
const record={id,guestName:'賓客',attending:true,guestCount:2,childSeats:1,vegetarianCount:1,dietNotes:'蛋奶素',message:'祝福你們',fingerprint:sha256,photos:[{index:0,fileId:first.fileId,sha256}]};
const submitted=post({action:'submit',id,record});assert(submitted.ok);
const fields=JSON.parse(properties.get('WEDDING_FORM_ITEMS'));
const stored=activeForm.getResponses()[0];
assert.equal(stored.getResponseForItem(activeForm.getItemById(fields.attending.id)).getResponse(),'是');
assert.equal(stored.getResponseForItem(activeForm.getItemById(fields.vegetarian.id)).getResponse(),'是');
assert.equal(stored.getResponseForItem(activeForm.getItemById(fields.childSeats.id)).getResponse(),'1');
assert.equal(post({action:'submit',id,record}).responseId,submitted.responseId,'lost acknowledgement reconciles through Forms');
assert.equal([...forms.values()][0].getResponses().length,1,'no duplicate response');
assert.equal(post({action:'submit',id,record:{...record,fingerprint:'a'.repeat(64)}}).code,'CONFLICT');
const otherId=randomUUID();
assert.equal(post({action:'submit',id:otherId,record:{...record,id:otherId}}).code,'INVALID_PHOTO','cannot reference another reply folder');
const noId=randomUUID();assert(post({action:'submit',id:noId,record:{...record,id:noId,attending:false,guestCount:0,childSeats:0,vegetarianCount:0,dietNotes:'',photos:[]}}).ok);
assert.equal(post({action:'submit',id:randomUUID(),record}).code,'INVALID_RECORD');
console.log('PASS: Apps Script setup, shared-secret gate, idempotent private uploads, digest validation, submission, response reconciliation, conflict detection and per-reply photo ownership (mock Google services).');
