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
| GET | `/api/admin/people` | 無 | `people` 與 `history` |
| POST | `/api/admin/merge` | `person_ids`, `target_id` | 合併結果 |
| POST | `/api/admin/undo` | `merge_id` | 復原結果 |
| GET | `/api/admin/drive/status` | 無 | 雲端同步狀態 |
| POST | `/api/admin/drive/sync` | `{}` | 202：背景同步已啟動 |

### 合併與復原

管理人物结构同公開列表，另有 `merged` 布林。歷史：`id, created_at`（Unix 秒）, `merged_count, can_undo`。只有最近一次 `can_undo=true`，由新到舊復原。

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
