# 前端 API 規格（給男友）

前端放 `frontend/`；Node API 開發地址 `http://localhost:3000`。Python 是內部服務，前端不呼叫 5001。可用任何前端框架換畫面，只需保留以下 API 互動。

## 基本約定

- `frontend/config.js` 設定 `API_BASE`。資源 URL 回傳 `/api/...` 相對路徑，使用 API_BASE 作為來源，不能直接放在獨立前端的網域。
- 公開 API 不需登入；管理 API 需要登入驗證。
- JSON POST 用 `Content-Type: application/json`；上傳用 FormData，不自行指定 multipart boundary。
- 回傳成功通常 `ok:true`；錯誤可能 `message` 或 Python 的 `detail`，HTTP status 才是成功與否的依據。
- 429 表示忙碌／限流。上傳與自拍搜尋應加隨機等待重試，不能立即無限重送。
- 前端 origin 必須加入後端 `FRONTEND_ORIGINS`；開發預設允許 localhost:5173 與 127.0.0.1:5173。

## 公開端點

| 方法 | 路徑 | 輸入 | 回應用途 |
| --- | --- | --- | --- |
| GET | `/api/health` | 無 | 本機已存照片、接收數、Drive 備份排隊 |
| POST | `/api/photos/upload` | FormData `photos`，最多 5 個檔案 | 原圖儲存成功，背景分析／備份排隊 |
| GET | `/api/face/status` | 無 | `photos, people, pending, failed, withoutFaces` |
| GET | `/api/face/people` | 無 | 人物 `id, thumbnail, count` |
| GET | `/api/face/people/:id/photos` | 人物 ID | 該人物的照片 `id, name, url` |
| GET | `/api/face/thumbs/:filename` | 回傳的頭貼路徑 | JPEG 頭貼 |
| GET | `/api/photos/:id/image` | 照片 ID | 縮圖／預覽 JPEG |
| GET | `/api/photos/:id/original` | 照片 ID | 原始照片下載 |
| POST | `/api/face/find` | FormData `file`，單人照 | `matches: [{id,name,score,url}]` |

### 上傳

每次 UI 最多選 20 張，依序分成每批 5 張；每張最多 25 MB。支援 JPG / PNG / WebP / HEIC / HEIF。範例：

```js
const form = new FormData();
files.slice(0, 5).forEach(file => form.append('photos', file));
const response = await WeddingAPI.request('/api/photos/upload', {
  method: 'POST', body: form,
});
const data = await response.json();
```

成功回應：

```json
{"ok":true,"count":1,"files":[{"id":"sha256","duplicate":false,"localFile":"sha256.jpg"}],"analysis":"queued","backup":"queued"}
```

`count` 是本次接收檔案數。每批請產生隨機 `X-Upload-Key`（16–100 位英數、底線或連字號），同一次請求重試沿用；新的主動上傳換新 key。檔案位置與內容相同且 key 相同才算重試，回傳 `duplicate:true`。缺少 key 時每次請求皆視為新上傳。ID 為不透明 64 位 hex，不能由內容自行推算。此時 Drive 與人臉仍可能排隊，需輪詢照片狀態，不能宣稱雲端成功。

### 人物列表与相簿

```json
{"ok":true,"people":[{"id":"person-id","thumbnail":"/api/face/thumbs/abc_0.jpg","count":12}]}
```

```json
{"ok":true,"photos":[{"id":"sha256","name":"合照.jpg","url":"/api/photos/sha256/image"}]}
```

頭貼只顯示圖片，不需姓名。列表約 8 秒刷新；目前相簿可隨人物 count 變更刷新。合併後旧人物連結會導向保留人物。

### 自拍搜尋

FormData 的欄位是 `file`。預設 threshold=0.30，前端不需傳參數。結果按 score 降冪。沒有人臉或多張人臉可能 400，提示換清楚單人照。搜尋照片不會加入人物相簿。

回應新增 `searchMode`（`vectorized`／`scalar`）、`qualityLimited`、`queryPartial`。`matches[{id,name,score,url}]` 不變，每張照片取所有觀測最高 cosine，門檻以未四捨五入分數判斷，依四位小數的 `score` 穩定降冪排序，同分保持照片原順序；不按人物分組過濾，也沒有 Top-K 截斷。前端可傳 AbortSignal 取消／換照，取消 429 等待也會停止重試；Node 在轉送成功、失敗或斷線時清除暫存檔。已開始的單次 OpenCV 呼叫不保證可立即中斷。

### P0–P2 本機分析狀態（相容新增）

`GET /api/face/status` 額外回傳 `partialPhotos`（僅完成部分分區偵測的照片數）、`facePipeline`、`groupMode`（`legacy`、`shadow` 或 `enhanced`）及 `searchMode`。`people` 計算有照片的人物；移空後保留的 ID 不列入。`photos` 表示索引已提交，可能含部分偵測；`withoutFaces` 不表示原圖一定無人臉。shadow 的新偵測只記對照，不計入正式 `partialPhotos`。

`GET /api/admin/people` 的人物新增 `provisional` 布林，表示新策略的低品質人物待確認；既有人物缺此欄位時回 false。仍可查看相簿。P3 校正端點如下。

公開人物、相簿及自拍搜尋回應保留原有結構；`score` 仍是 cosine，沒有改成人物機率。非公開人臉裁切不透過 `/thumbs` 提供。

## 管理登入

所有 `/api/admin/*` 請求都需要：

```http
X-Admin-Request: 1
```

登入：

```http
POST /api/admin/login
Content-Type: application/json
X-Admin-Request: 1

{"password":"管理密碼"}
```

成功回應：

```json
{"ok":true,"adminToken":"隨機登入token","expiresIn":28800}
```

登入後可在管理請求附加：

```http
Authorization: Bearer 隨機登入token
X-Admin-Request: 1
```

登入同時會设置 HttpOnly Cookie。`assets/api-client.js` 支援 Bearer 存於記憶體與 Cookie `credentials:include`；切勿將管理密碼、內部 Python 驗證碼放入前端設定。跨網站前端可用 Bearer；若頁面重載且沒有可用 Cookie，需要重新登入。

管理功能預設沒有密碼，未設定時是 503；密碼錯誤／登入逾時是 401；嘗試太多是 429。有效期 8 小時；Node 重啟須重登入。

## 管理端點

| 方法 | 路徑 | 輸入 | 回應 |
| --- | --- | --- | --- |
| GET | `/api/admin/session` | 驗證標頭／Cookie | `ok:true` |
| POST | `/api/admin/logout` | `{}` | 登出當前登入 |
| GET | `/api/admin/people` | 無 | `people`、`history`、`operations`、`revision` |
| POST | `/api/admin/merge` | `person_ids`, `target_id` | 合併結果 |
| POST | `/api/admin/undo` | `merge_id` | 復原結果 |
| GET | `/api/admin/face-reviews` | `kind=all/duplicate/pending`, `offset=0`, `limit=30` | 複核建議、排除紀錄、revision |
| POST | `/api/admin/face-reviews/:id/resolve` | `action`, `target_id?`, `revision`, `request_id` | 複核結果 |
| POST | `/api/admin/face-constraints/:id/revoke` | `revision`, `request_id` | 撤銷不同人約束 |
| GET | `/api/admin/photos/:id/faces` | 無 | 照片中的觀測與座標、revision |
| GET | `/api/admin/faces/:id/crop` | 驗證標頭／Cookie | JPEG，no-store |
| POST | `/api/admin/faces/reassign` | `face_ids`, `target_id?`, `revision`, `request_id` | 單臉／多臉移轉 |
| POST | `/api/admin/face-corrections/undo` | `correction_id`, `revision`, `request_id` | 復原最近人工操作 |
| GET | `/api/admin/face-backfill` | `offset=0`, `limit=30` | 回填狀態、待複核總數及任務分頁 |
| POST | `/api/admin/face-backfill/start` | `photo_ids?`, `retry?`, `revision`, `request_id` | 開始／繼續回填 |
| POST | `/api/admin/face-backfill/pause` | `revision`, `request_id` | 暫停後續回填 |
| GET | `/api/admin/face-backfill/candidates/:id` | 無 | 私有候選與位置，無 embedding |
| GET | `/api/admin/face-backfill/candidates/:id/image` | 管理驗證 | 私有照片預覽 JPEG |
| GET | `/api/admin/face-backfill/candidates/:id/faces/:face/crop` | 管理驗證 | 私有候選裁切 JPEG |
| POST | `/api/admin/face-backfill/candidates/:id/accept` | `face_ids`, `revision`, `request_id` | 確認選定漏臉，追加待確認人物 |
| GET | `/api/admin/drive/status` | 無 | 雲端同步狀態 |
| POST | `/api/admin/drive/sync` | `{}` | 202：背景同步已啟動 |

### 合併與復原

管理人物結構同公開列表，另有 `merged`、`provisional` 布林。`history` 保留原合併歷史結構：`id, created_at`（Unix 秒）, `merged_count, can_undo`。`operations` 是統一整理紀錄，含 `id, kind, created_at, can_undo`，kind 為 `merge/reassign/approve/different/skip/revoke/backfill_add`。只有整體最近一次 `can_undo=true`，由新到舊復原；有較新的校正時，舊合併也不能先復原。

```json
{"person_ids":["person-a","person-b"],"target_id":"person-a"}
```

成功：

```json
{"ok":true,"merge_id":"merge-id","person_id":"person-a","count":23,"merged_count":2}
```

復原：

```json
{"merge_id":"merge-id"}
```

回傳 `restored_count` 和 `person_ids`。選擇已過期或復原順序錯誤會 409，重新整理。相簿不刪除原圖，復原不刪掉合併後新增的照片。UI 應提供頭貼勾選、照片預覽、主頭貼選擇和確認對話框。

### 人工複核與單臉校正（P3）

新寫入端點必須附讀取時的整數 `revision`，以及隨機 `request_id`（16–100 位英數、底線、連字號）。同一次網路重試沿用相同 ID；新的主動操作換新 ID。`merge/undo` 為相容舊客戶端仍接受省略這兩欄，新前端一律提供。

成功提交會同時保存人物、指派、約束、歷史、revision 與重試收據。過期 revision 或 ID 被用於不同操作回 409。相同 ID／內容先查收據再查版本，重啟後仍可回 `replayed:true` 及原操作結果，並附**目前** revision；原操作後來即使已復原，也不會再次套用。新操作請重新讀取資料。

`face-reviews` 的 `limit` 為 1–100，`offset` 非負，回應含 `reviews,total,next_offset,constraints,revision`。每項有 `id,kind,person_ids,face_ids,faces`；duplicate 另有 `score,same_photo`，pending 有 `reason`。這些是待人工確認的建議，cosine 不是同人機率。重複建議使用本機樣本、門檻 0.50；新版證據會產生新建議 ID。

resolve 的 `action`：duplicate 可 `merge/different/skip`，pending 可 `approve/skip`。merge 可附要保留的 `target_id`。different 綁定兩個穩定 face ID，阻擋這兩個現有人物的再次合併及違反證據的移轉；撤銷排除後可重提。skip 只略過該版建議，可從整理紀錄復原。

`photos/:id/faces` 回傳 `photo_id,photo_url,revision,faces`。每張臉含 `face_id,person_id,crop_url,photo_url,bbox,coordinate_space,image_size,preview_size,normalized_bbox,quality,confirmed,review_id`。bbox 為 x/y/寬/高；normalized_bbox 是相對 EXIF 校正後完整照片的比例，缺來源尺寸則 null。舊觀測透過照片 ID 與原陣列位置產生穩定 ID，讀取不改寫索引；不要重排原 faces 陣列。回應不包含 embedding。裁切必須由有管理標頭的 fetch 讀為 Blob／object URL，直接 `<img src>` 無法附 Bearer 與 `X-Admin-Request`；關閉畫面／登出後撤銷 object URL。

移轉範例：

```json
{"face_ids":["stable-face-id"],"target_id":"existing-person-id","revision":42,"request_id":"random-operation-id-0001"}
```

`target_id:null` 或省略表示拆成新人物；支援 1–100 個不重複 face ID，前端每次選一張。成功回 `operation_id,person_id,moved_count,revision`。只改選定觀測，重建相簿／樣本／張數；移空的人物 ID 留存，列表隱藏但舊相簿回空陣列。確認低品質歸屬不代表將其加入可信代表庫。復原傳 `correction_id:operation_id` 至 face-corrections/undo；合併仍使用 undo 的 `merge_id`。

### 舊照片回填（P5）

第一次開始需先停寫，執行 `maintain_faces.py backup/restore-test/migrate`，登記已驗證備份及穩定人臉 ID；未登記時 start 回 409。此服務仍只支援一個 Node 與一個 Python worker。

start 支援 `photo_ids`（省略代表所有已索引照片，最多指定 1,000 張）及 `retry:false`。同一管線與照片證據已完成的任務不重跑；retry 會重排未完成／過期任務，仍不會繞過來源校驗。暫停不丟棄佇列，已開始的一張分析可先完成；重啟後 processing 會安全重排。暫時性原圖讀取錯誤最多自動嘗試三次，再等人工重試。

狀態回應包含 `revision,paused,counts,total,pending_faces,jobs,next_offset,backup`。狀態為 `queued/processing/ready/unavailable/changed/failed/stale`；ready 代表分析及候選已保存，不代表新增觀測已加入相簿。每筆 job 有 `photo_id,photo_name,status,attempts,candidate_id,new_faces,mapped,ambiguous,partial`，視狀態另有 `message,source,finished_at`；待複核優先顯示，這不改變原照片順序。`limit` 範圍 1–100，offset 非負。

候選回應包含 `candidate_id,photo_id,photo_url,revision,faces,mapped_count,ambiguous_count,partial,stale`。faces 的 `face_id` 是候選 ID，另有 `crop_url,normalized_bbox,accepted,qualityLimited`。圖片需帶登入及管理標頭 fetch 為 Blob；回應為 no-store，不透過公開人物或照片 API 提供未確認裁切。

確認範例：

```json
{"face_ids":["candidate-face-id"],"revision":42,"request_id":"random-backfill-accept-0001"}
```

只接受該候選中尚未加入的 1–100 個不重複 ID；過期版本、舊人臉已校正、管線不同或候選內容不符回 409。成功回 `operation_id,added_count,face_ids,person_ids,revision`，只在原 faces 陣列尾端追加。新人物 `provisional=true`，不招募其他照片，需再確認歸屬。復原將 `operation_id` 作為 `correction_id` 傳既有 face-corrections/undo；較新的人工操作必須先復原，非尾端觀測不會被刪除，後來加入的其他照片保留。原子提交與持久化 request_id 收據語意沿用 P3。

舊觀測僅增加穩定 ID 與 `backfill_metadata`；舊 embedding、bbox、person_id、原陣列順序、主頭貼及歷史保留。來源不明或幾何重疊時保留原判斷，不自動取代。確認追加或復原會使搜尋快取失效；只補 metadata 不影響搜尋結果。

Python 向 Node 的內部原圖端點為 `GET /internal/face-backfill/original/:id`，需要內部 `X-Admin-Key`，不可放到前端。回傳二進位及 `X-Original-Sha256/X-Original-Source/Content-Length`，no-store；來源錯誤分為 invalid400、unavailable404、changed409、retryable503。只讀既有 manifest、本機或已授權 Drive 原檔，限制單次 100 MiB（可設定），逐一下載到專用暫存，不修改原圖釋出／匯入／同步狀態。

### 雲端同步狀態

```json
{
  "ok":true,"enabled":true,"ready":true,"scanning":false,
  "downloading":true,"currentName":"合照.jpg","subfolders":true,
  "intervalSeconds":300,"discovered":300,"imported":120,
  "pending":178,"failed":1,"skipped":1,
  "lastScanAt":1791374400000,"lastScanError":null,
  "problems":[{"name":"大圖.jpg","message":"照片超過 100 MB，未下載"}]
}
```

時間欄位 `lastScanAt` 為 Unix 毫秒。`imported` 包含已匯入或核對已有本機照片；不等於已分析。`pending` 含下載中，不包含失敗重試項。`failed` 會重試；`skipped` 是大小／權限問題。每 5 秒更新即可，不要每秒查所有人物或反覆觸發掃描。

按「立即同步」使用 POST，202 不等待掃描完成；已有掃描時不新增第二個並行掃描。未啟用或 Google 尚未授權時 503。Google OAuth 憑證只放後端，不需要賓客登入 Google。

## 後端管理設定（不放進前端）

`GOOGLE_DRIVE_FOLDER_ID`、`ADMIN_PASSWORD`、Google `credentials.json`／`token.json`、資料存放路徑、同步間隔与下載上限全由後端設定。前端只需要 `API_BASE`。

Google Drive 匯入依官方 files.list 分頁及 files.get `alt=media` 下載介面實作：
[列出檔案](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list)、[下載檔案](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get)。

## 雲端備份確認（本次修正）

公開 `GET /api/photos/status?ids=sha256a,sha256b`：最多 20 個不同照片 ID。回應：

```json
{"ok":true,"driveEnabled":true,"connectionState":"ready","connectionError":null,"files":[{"id":"sha256","status":"pending","message":null}]}
```

`status` 可能是 `pending`、`uploading`、`done`、`disabled`、`missing`。只有全數 `done` 才顯示雲端成功；每 5 秒確認即可。`connectionState=ready` 表示 Google 客戶端已初始化，**不等於照片已傳到 Drive**。備份失敗時個別 `message` 說明原因；上傳原图已保留，避免要求賓客重傳。

管理端 `GET /api/admin/backup/status`：需管理驗證。回傳 `enabled, connectionState, connectionError, folderUrl, total, done, pending, uploading, disabled, problems`。`folderUrl` 是新照片的實際備份資料夾，可放管理頁的「開啟備份資料夾」連結。匯入 Drive 舊照片的來源可能與備份目的地不同。

後端選填 `GOOGLE_DRIVE_UPLOAD_FOLDER_ID`，指定新照片備份目的地；空白時沿用 `GOOGLE_DRIVE_FOLDER_ID`。前端不能覆蓋此設定。

`GET /api/health` 新增 `service, version, driveEnabled, driveConnectionState, driveConnectionError, backupFailed`，供診斷連到哪一版後端。

## 4.1.0 雲端原圖與相簿入口

`GET /api/config` 公開回傳 `{ "allPhotosUrl": "https://drive.google.com/drive/folders/..." }`，未設定時為 null。前端可用 config.js 的 ALL_PHOTOS_URL 覆寫；只接受 https://drive.google.com 連結。

`GET /api/photos/:id/original` 由後端串流讀取 Google Drive 原圖，Content-Disposition 為下載，不將原圖重新落盤。不存在 404、尚未備份 409、下載忙碌 429（Retry-After）、雲端暫不可用 503。下載連結可用新視窗以免取代相簿。

本機 originals 為暫存：雲端成功 + face_index 持久化 + Drive 原圖確認後清除；保留縮圖、預覽與人物索引。KEEP_LOCAL_ORIGINALS=true 可關閉清理。雲端刪除目前不自動刪除人物索引，下載可能失敗，重新主動上傳會建立新的照片。

## 電子喜帖回覆

喜帖預設 `submissionMode:'google-form'`，由瀏覽器原生表單提交至 Google，**不呼叫以下 API**。以下為選用 `submissionMode:'backend'` 時保留的回覆與照片留存介面。兩種模式共用 `frontend/assets/invitation-google-form.js` 中的 Google 欄位定義。

`POST /api/invitations/rsvp`，使用 `multipart/form-data`；沿用 `frontend/assets/api-client.js` 的後端網址與 CORS 設定。

| 欄位 | 說明 |
| --- | --- |
| `requestId` | UUID v4，同一次送出重試保留原值；修改內容後使用新值 |
| `guestName` | 必填，最多 80 字元 |
| `attendance` | 必填，`yes` 或 `no` |
| `guestCount` | 出席才需要，1–20 整數，包含本人 |
| `vegetarianCount` | 出席才需要，0–出席人數的整數 |
| `childSeats` | 選填，0–出席人數的整數；預設 0，無法出席時歸零 |
| `dietNotes` | 選填，最多 300 字元 |
| `message` | 兩種出席狀態都可填，最多 2000 字元 |
| `photos` | 出席者選填，最多 3 張、每張 10 MB，JPG／PNG／WebP；檢查檔案標頭與 MIME |

成功為 HTTP 201：`{"ok":true,"id":"回覆 UUID","googleForms":{"enabled":true,"synced":true}}`，表示 Google 表單已回傳收件確認。只有離線測試明確停用 Google 時才回傳 `googleForms:{"enabled":false}`。無法出席時後端將人數、素食與兒童座椅歸零、清除飲食備註，若攜帶照片則拒絕。驗證失敗為 400、相同識別碼但不同內容為 409、同時接收超過 4 筆為 429、儲存失敗為 500。

紀錄與照片以一個資料夾提交，保存於 `DATA_DIR/rsvps/<UUID>/`，回覆為 `response.json`，不提供公開讀取端點。此流程不會加入公開相簿或人臉分析。沿用本專案單一 Node 程序的部署方式。

預設直接提交 `backend/google-form-config.js` 指定的公開 Google 表單，不使用 Apps Script 或 Secret。後端先讀取表單、核對六題並取得當次隱藏欄位，再 POST `application/x-www-form-urlencoded` 到實際 `formResponse` 網址。素食人數、飲食備註與照片張數附在「給新人的話」中；照片本身只存本機。Google 回傳明確收件確認後才將 `google-forms.json` 標記為 `confirmed`，不捏造 Google 回覆 ID。

送出前無法連線或 Google 拒絕時為 HTTP 502、`code:GOOGLE_FORMS_PENDING`、`localSaved:true`。POST 後連線中斷或回應不明時為 HTTP 502、`code:GOOGLE_FORMS_UNCERTAIN`、`localSaved:true`，必須到 Google 試算表用回覆編號核對，系統不會自動重送這筆。已確認的同一回覆編號不重送；Google 本身沒有此自訂編號的去重保證。可執行 `node backend/sync-invitations.js` 補送尚未提交的歷史紀錄；它同樣不會重送不確定紀錄，目前沒有背景排程。見 `integrations/google-forms/README.md`。
