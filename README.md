# 婚禮照片系統：前後端分離＋Google Drive 既有照片同步

版本 **4.1.0**。不用 DB；Google Drive 保存原圖，本機保存人物索引、大頭貼與預覽。賓客上傳、攝影師雲端匯入共用人物相簿。前後端分離。

## 一個指令啟動開發環境

需要 Node 22+、pnpm 11.3.0、[uv](https://docs.astral.sh/uv/getting-started/installation/) 與 Python 3.10+。在專案根目錄執行：

```bash
pnpm install
pnpm dev
```

`pnpm install` 安裝 pnpm 工作區的 Node 依賴；`pnpm dev` 先由 uv 依 `backend/uv.lock` 準備 `backend/.venv`，下載缺少的 YuNet / SFace 模型，再由 Turborepo 同時啟動三個服務：

| 服務 | 網址 |
| --- | --- |
| 前端 | <http://localhost:5173/photos.html> |
| Node API | <http://localhost:3000/api/health> |
| Python 人臉服務（僅本機） | <http://127.0.0.1:5001/health> |

人物頁：<http://localhost:5173/people.html>；管理頁：<http://localhost:5173/admin-people.html>。第一次需要網路安裝 Python 依賴與下載模型，之後沿用本機檔案。不必手動啟用虛擬環境，也不必開三個 Terminal。按 **Ctrl+C** 一起停止；任一服務啟動失敗時，Turborepo 也會停止其他服務並顯示錯誤。

電子喜帖：可直接開啟 `frontend/invitation.html`，或使用 <http://localhost:5173/invitation.html>。預設透過隱藏 iframe 原生提交 Google 表單，留在喜帖顯示 alert，沿用已連動試算表，無需後端或 Apps Script。此模式無法讀取跨來源收件結果，提示不宣稱 Google 已確認收到。目前該表單沒有照片上傳題，純靜態模式不接收照片；詳見 [Google 表單串接指南](integrations/google-forms/README.md)。

未設定 Google Drive 資料夾且未指定 `DRIVE_ENABLED` 時，Node **開發模式**先關閉雲端備份，照片存於本機；人物相簿與自拍搜尋仍可使用。要啟用 Drive，依下方說明設定 `backend/.env`、OAuth 憑證後重啟。開發啟動保留 `.env` 與外部環境變數；Node 和 Python 仍共用原本的 `DATA_DIR`。只在本機測試時，也可明確設定 `DRIVE_ENABLED=false`。

`pnpm setup:face` 可單獨準備 Python 環境與模型。下方分別啟動三個服務的方式仍可用；`backend` 的 `npm start` 維持原本的正式啟動行為。

### 本機資料夾的照片加入分析

照片複製完成後，先在執行 `pnpm dev` 的 Terminal 按 **Ctrl+C**，再於專案根目錄執行：

```bash
pnpm import:photos
pnpm dev
```

省略資料夾時，匯入工具掃描 `DATA_DIR/originals`（預設 `backend/data/originals`），為尚未登記的照片建立分析任務，沿用現有原圖，不改名、不另複製一份。支援子資料夾、JPG / PNG / WebP / HEIC / HEIF；已登記的檔案與相同內容會跳過。重新啟動後，Python 背景逐張分析，可在 <http://localhost:5173/people.html> 查看進度。

要匯入其他資料夾，執行 `pnpm import:photos /你的照片資料夾`；這些原圖會複製到系統的 `originals`。`DRIVE_ENABLED=false` 時不排入雲端備份。Node 需停止再匯入，才能在重啟時載入新任務；不要刪除既有 `face_index.json`。

## 4.1.0 更新重點

- 每次主動上傳都建立新照片，內容相同也接受；同一次上傳的網路自動重試使用固定識別碼，避免重試重複建立。
- 只有 Drive 回覆上傳完成才顯示雲端成功；失敗會顯示原因並背景退避重試。人臉分析是另一個階段。
- 雲端備份完成且人物索引寫入成功後，重新確認 Drive 檔案存在且校驗碼吻合，才清除本機暫存原圖。每 30 秒最多處理 20 張，並非瞬間全部清除。
- 攝影師照片逐張下載分析；等待分析的雲端匯入達 30 張時暫停新下載，讓分析追上。清除過的原圖不會被反覆下載。
- 人物相簿與自拍搜尋都保留，結果提醒自行確認；下載原圖直接由後端串流讀取 Drive，不重新長期存到本機。
- 上傳頁與人物頁新增「看全部照片 → Google Drive」。前端頁尾標示 v4.1.0。

### 從 4.0.x 更新

停止 Node、Python 和前端服務，先備份專案，再將此包的 backend/、frontend/ **內容合併覆蓋**至目前實際執行的專案。保留 .env、credentials.json、token.json、data/、models/ 與原路徑的 .venv；不要用空資料夾取代整個舊 backend。

在 backend 執行 `npm ci`、`node -p "require('./package.json').version"`（應為 4.1.0），再照下方啟動兩個後端。前端重啟後按 ⌘⇧R；管理頁應有「照片雲端備份」。不必清空舊人物資料。

**升級後舊照片也會適用暫存清理**，但僅限已分析且雲端副本確認存在者。若暫時想保留全部本機原圖，設定 `KEEP_LOCAL_ORIGINALS=true`。雲端已刪除的舊紀錄不會自動補傳或清除人物；重新主動上傳照片會建立新的雲端檔案。

### 看全部照片設定

預設連到 `GOOGLE_DRIVE_FOLDER_ID` 指定的資料夾；前端不用填 ID。要看父層總相簿而上傳寫進子資料夾，可設定：

```env
GOOGLE_DRIVE_FOLDER_ID=婚禮總資料夾ID
GOOGLE_DRIVE_UPLOAD_FOLDER_ID=賓客上傳子資料夾ID
GOOGLE_DRIVE_ALL_PHOTOS_URL=https://drive.google.com/drive/folders/婚禮總資料夾ID
KEEP_LOCAL_ORIGINALS=false
```

攝影師上傳到總資料夾下的另一個子資料夾，保持 `DRIVE_SYNC_SUBFOLDERS=true`。只想保留目前 Original 位置時，不用改原本的 ID。Google Drive 資料夾需由你設定賓客可檢視的分享權限，按鈕不會自行更改 Drive 權限。前端也可透過 `config.js` 的 `ALL_PHOTOS_URL` 覆寫連結。

## 目錄與分工

| 目錄／檔案 | 用途 | 由誰修改 |
| --- | --- | --- |
| `frontend/` | HTML、CSS、JavaScript 畫面 | 男友可獨立修改 |
| `frontend/config.js` | 後端 API 網址 | 前端 |
| `frontend/assets/api-client.js` | 共用 API 呼叫、管理登入 Token | 前端 |
| `backend/` | Node API、Drive 同步、人臉服務 | 後端 |
| `backend/data/` | 暫存原圖、預覽、佇列、人物索引、合併紀錄 | 保存，升級時沿用 |
| `backend/models/` | 原本的 YuNet / SFace 模型 | 沿用 |
| `API.md` | 前端串接規格 | 給男友參考 |

只把 `frontend/` 與 `API.md` 給男友即可；不需要交給他 Google 憑證、Token 或管理密碼。前端不包含 Google API 或人臉分析邏輯，使用普通 HTML/CSS/JavaScript，沒有前端套件依賴。

## 從上一版更新

**先停止舊 Node 與 Python**。這版的後端程式搬到 `backend/`；把上一版以下檔案／目錄複製到新 `backend/`，保留內容：

```text
.env
credentials.json
token.json
data/
models/
```

如果 `.env` 有自訂 `DATA_DIR` 絕對路徑，可以繼續使用該位置，不必搬動資料；Node 與 Python 必須指向同一份資料。**不要刪除 `face_index.json`**，它包含你已整理的人物與合併紀錄。

Python 虛擬環境請重新建立，不要直接搬舊 `.venv`。原本已有的模型不需要重下載。原本的 Google 授權範圍就是 Drive；一般可沿用 `token.json`，若失效會需要重新授權。

## 啟動後端（Node 22+、Python 3.10+）

### 1. 設定与安裝

```bash
cd wedding-photo-people/backend
npm ci
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r face-service/requirements.txt
```

初次使用、沒有舊設定的人：

```bash
cp .env.example .env
```

在 `.env` 設定你的資料夾與管理密碼：

```dotenv
GOOGLE_DRIVE_FOLDER_ID=你的婚禮照片資料夾ID
ADMIN_PASSWORD=自行設定至少12字元的密碼
DRIVE_ENABLED=true
DRIVE_SYNC_ENABLED=true
DRIVE_SYNC_SUBFOLDERS=true
DRIVE_SYNC_INTERVAL_SECONDS=300
DRIVE_SYNC_MAX_FILE_MB=100
FRONTEND_ORIGINS=http://localhost:5173
ADMIN_COOKIE_SECURE=false
```

上面是格式範例，請換成你自己的設定。升級既有 `.env` 時保留原值，新增缺少的設定即可。Drive 同步未填設定時預設開啟、包含子資料夾、每 5 分鐘掃描、雲端單張下載最多 100 MB。網站賓客上傳仍每張最多 25 MB。

把 Google OAuth `credentials.json` 放在 `backend/`。若沒有原本模型，在後端目錄執行：

```bash
python face-service/download_models.py
```

模型位置：

```text
backend/models/face_detection_yunet_2023mar.onnx
backend/models/face_recognition_sface_2021dec.onnx
```

### 2. 第一個 Terminal：Python

在 `backend/`：

```bash
source .venv/bin/activate
uvicorn app:app --app-dir face-service --host 127.0.0.1 --port 5001 --workers 1
```

### 3. 第二個 Terminal：Node

在 `backend/`：

```bash
npm start
```

Google 授權完成後，會自動讀取雲端既有照片。前端上傳不用等待 Drive 同步完成。

## 啟動前端（給男友改畫面）

第三個 Terminal：

```bash
cd wedding-photo-people/frontend
node dev-server.mjs
```

打開：

- 上傳：<http://localhost:5173/photos.html>
- 賓客大頭貼與自拍搜尋：<http://localhost:5173/people.html>
- 新人管理與雲端同步：<http://localhost:5173/admin-people.html>

前端 5173、Node API 3000、Python 5001。前端只連 Node API，不直接呼叫 Python。

`frontend/config.js` 的 `API_BASE` 空白時，5173 開發模式自動連同主機 3000；正式部署改成你的後端 HTTPS 網址。後端 `.env` 的 `FRONTEND_ORIGINS` 要加入前端完整來源（含協定與連接埠，多個以逗號分隔）。

同 Wi-Fi 手機測試，例如前端網址是 `http://192.168.1.23:5173`，需在 `FRONTEND_ORIGINS` 加入該網址並重啟 Node。

為方便你直接測試，Node 預設也提供 `frontend/` 靜態檔，可直接用 3000 開啟以上頁面。若只要 API，把 `.env` 設 `SERVE_FRONTEND=false`。這不影響兩個資料夾的獨立開發。

## 雲端舊照片怎麼辨識

1. Node 用原本的 Google OAuth，讀取 `GOOGLE_DRIVE_FOLDER_ID` 及其子資料夾。
2. 分頁列出支援的 JPG / PNG / WebP / HEIC / HEIF 照片；不掃描整個帳號其他資料夾。
3. 逐張串流下載到本機。已匯入的檔案跳過；網站上傳後備份且能核對本機紀錄的照片也會直接使用本機檔。
4. 原圖與分析任務先寫入磁碟，再交给 Python 同一份佇列，建立頭貼與人物相簿。
5. 登入管理頁可看已找到、已匯入、待下載、失敗等進度，也可按「立即同步雲端照片」。

「已匯入」表示原圖下載完成并已加入分析佇列，**不是全部辨識完成**。人臉分析進度在賓客人物頁顯示。

- 雲端匯入只讀取與下載，不會把舊照片重新上傳、刪除或改名。
- 同一 Drive 檔案同一內容版本只匯入一次；不同 Drive 檔案即使內容相同也各自保留。網站建立的 Drive 檔案透過檔案 ID 與校驗碼對應原本的分析工作。
- 分次往 Drive 加照片，通常在下一次 5 分鐘掃描發現；可按「立即同步」。
- 下載失敗會退避重試，進度記錄保存在 `backend/data/drive_sync.json`，重啟可繼續。
- 雲端原圖內容改變會匯入新的內容；本版是新增匯入模式，舊的本機版本與既有人物紀錄會保留。雲端刪除照片不會刪掉本機相簿。
- 雲端禁止下載或超過大小限制的照片會显示原因；快捷方式、Google 文件、影片、GIF、BMP 不會匯入。
- 既有資料夾很大時，大頭貼會陸續出現，不需要等全部照片一起完成。

## 80 人同時上傳

一次最多選 20 張，每批 5 張，後端最多同時接收 8 個上傳請求。超出時前端自動等待重試；同一次動作的重試不會重複新增。

Google Drive 備份最多同時 2 張，舊照片下載一次 1 張；有賓客正在上傳時，先暫停新的舊照片下載。Python 背景分析一次 1 張，搜尋與分析共用模型鎖。

API 接收完成代表原圖與任務已暫存；前端會持續確認 Drive，雲端完成後才顯示成功。人臉分析仍可能排隊。實際速度取決於主機、網路、照片大小和每人張數，不能只以 80 人推估完成時間。

此 JSON 版本只跑 **1 個 Node 程序、1 個 Python worker**，不要開 PM2 cluster 或多個服務共寫資料。保留與備份整個 `data/`，不只 JSON。

## 整理同一人的多個頭貼

管理頁需 `ADMIN_PASSWORD`（至少 12 字元）。勾選 2 個以上頭貼，預覽照片後合併，選擇保留的頭貼。相簿照片去重，各組既有人臉樣本保留，補傳會繼續用這些樣本比對。

「人工複核」可查看可能重複人物、確認待確認人臉、略過建議，或記錄不同人並撤銷排除。從建議或相簿按「檢查人臉」，可看原照片中的框，選一張臉移到其他人物或拆成新人物；同圖其他人保持原歸屬。

合併與人工校正共用由新到舊的復原順序。操作後新分析的照片留在當時的相簿，不會刪除，也不會自動猜測要分回哪個舊頭貼。既有合併紀錄、舊相簿連結轉向與背景索引寫入保護保留；移空的人物 ID 也保留，舊相簿網址仍可讀取空相簿。過期頁面會要求重新整理，網路重試不會重複套用操作。

自拍搜尋預設 threshold=0.30；人物自動分組預設 0.50、差距 0.06，比搜尋保守。側臉、遮擋仍可能拆成多個頭貼，或誤分；手動合併無法保證不再出現新分組。

搜尋預設 `FACE_SEARCH_MODE=vectorized`：短鎖取得快照，在鎖外精確比對全部觀測；保留每張照片最高相似度、原門檻及排序，不以人物分組過濾。`scalar` 可切回逐臉計算。賓客可取消搜尋；模糊／部分偵測會顯示換照提示，自拍不加入人物庫。操作、相容性及測試結果見 [P3–P4 實作說明](docs/face-recognition-p3-p4.md)。

### 本機人臉改善與離線評估（P0–P2）

新增原尺寸分區偵測、品質資訊、整圖候選配對及多樣代表樣本。預設 `FACE_PIPELINE=legacy`、`GROUP_MODE=legacy`，維持原有偵測與分組；舊人物 ID、相簿連結、頭貼選擇與合併歷史可繼續使用，不會自動重分析舊照。

先用獨立評估目錄比較，不必啟動服務：

```bash
uv run --project backend --locked python backend/face-service/evaluate_faces.py init --photos backend/sample_photos --manifest face-evaluation/samples-manifest.json
uv run --project backend --locked python backend/face-service/evaluate_faces.py run --manifest face-evaluation/samples-manifest.json --output face-evaluation/run-001 --split calibration
```

`face-evaluation/` 已排除 Git。第一次建立的是**未標註**清單，報告不會宣稱辨識準確率；人工確認人臉框、匿名人物標籤及照片分組後，才計算品質指標。輸出目錄必須是新的，工具會拒絕寫入正式 DATA_DIR、模型或原始照片目錄。

需要比較服務的新進照片時，可在 `backend/.env` 設 `FACE_PIPELINE=shadow`、`GROUP_MODE=shadow` 後重啟 Python；正式相簿仍使用舊策略，索引只增加對照資訊。shadow 會額外執行分區偵測，增加分析時間；自拍不執行 shadow 工作。驗收後才考慮各別切為 `enhanced`。

新分析先保存非公開 `face_crops/`、頭貼與預覽，再提交索引。超出分區限制會明確標為部分偵測；舊原圖清理條件仍沿用 Drive 已確認及索引已落盤。P0–P2 階段的操作與測試結果見 [P0–P2 實作說明](docs/face-recognition-p0-p2.md)。

### 舊照片回填（P5）

先停止 Node／Python 寫入服務，以維護工具備份完整 DATA_DIR 和設定、驗證並演練還原，再追加穩定人臉 ID。請使用新的備份與還原測試目錄；若有 credentials.json／token.json，另以 `--config` 加入現存檔案。

```bash
backend/.venv/bin/python backend/face-service/maintain_faces.py audit --data backend/data
backend/.venv/bin/python backend/face-service/maintain_faces.py backup --data backend/data --output data-backup-before-p5 --config backend/.env --config frontend/config.js
backend/.venv/bin/python backend/face-service/maintain_faces.py restore-test --backup data-backup-before-p5 --output /tmp/wedding-restore-test
backend/.venv/bin/python backend/face-service/maintain_faces.py migrate --data backend/data --backup data-backup-before-p5
backend/.venv/bin/python backend/face-service/backfill_run.py start --data backend/data
backend/.venv/bin/python backend/face-service/backfill_run.py run --data backend/data
```

離線回填預設只讀本機；加 `--allow-drive` 才讀取現有 Drive 憑證，缺憑證不開啟互動授權。線上則使用已連線 Node 的原圖來源。下載逐一進行，校驗來源內容與雲端前後版本；不改 `originalReleased`、原上傳任務或同步紀錄，也不把預覽當高解析原圖。

啟動服務後，管理頁「舊照片補分析」可開始／繼續、暫停、重試及查看進度。新偵測先存為私有候選；確認選定漏臉才追加為待確認人物，再透過 P3 校正／合併確認歸屬。舊臉保留位置、embedding、歸屬、主頭貼與歷史；補充品質／裁切放在獨立 metadata。追加人臉可從統一整理紀錄復原。

本輪已對 347 張本機原圖完成回填，85 個人物及原 298 張臉保持不變，另有 59 張漏臉候選待人工複核。備份位置、還原演練與測試結果見 [P5 實作與回填報告](docs/face-recognition-p5.md)。正式佇列已暫停，未啟動正式服務。

各步驟的 commit、獨立版本驗證、資料保留與完整測試指令，見 [P0–P5 分階段提交紀錄](docs/face-recognition-commit-log.md)。

## 登入與部署

管理 API 驗證有效登入、嘗試限流與管理請求標頭。跨來源前端可使用登入回傳的短期隨機 Bearer Token，共用 API 客戶端只放在頁面記憶體；不存 localStorage。頁面重載時，若不同網站的 Cookie 無法使用，需重新登入。

同來源登入同時使用 HttpOnly / SameSite=Strict Cookie，有效 8 小時；登出或 Node 重啟使該登入失效。正式部署使用 HTTPS，並將 `ADMIN_COOKIE_SECURE=true`。Python 管理 API 需要內部驗證，5001 僅在本機，不公開。

賓客照片和大頭貼仍按原需求公開給持有網站網址的賓客。管理權限是密碼驗證，不是綁定個人帳號。

## 驗證與限制

內附以下測試：

```bash
cd backend
python -m unittest discover -s tests -v
node tests/admin-auth.mjs
node tests/drive-sync.mjs
node tests/cloud-storage.mjs
node tests/backup-status.mjs
node tests/frontend-api.mjs
node tests/face-review-api.mjs
node tests/selfie-cancel.mjs
node tests/selfie-ui.mjs
node tests/backfill-originals.mjs
node tests/backfill-source-api.mjs
node tests/backfill-api.mjs
node tests/load-upload.mjs
```

涵蓋合併／復原／補傳與去重、管理驗證、跨來源標頭與 Bearer 登入、Drive 分頁與子資料夾、下載重試／重啟／大小限制／範圍變更，以及 80 個並行小型上傳請求。

P3–P4 另測單臉校正／不同人約束、舊合併歷史、原子提交失敗、revision／重試收據、向量搜尋等價、取消與斷線清理。`face-review-api.mjs` 使用 `backend/.venv/bin/python`、本機 ONNX 模型及 `sample_photos` 的單臉裁切，啟動暫存 Node／Python 服務；不使用正式索引或雲端帳號。

P5 另測完整備份／還原、追加式遷移、EXIF／舊預覽座標對應、續跑／暫停、原圖缺失／版本異動、人工追加與復原、私有候選及產物損毀。`backfill-api.mjs` 使用實際本機模型與暫存 Node／Python 服務；Drive 版本測試使用模擬 API。

Drive 測試使用模擬 Google API，沒有登入你的真實帳號；真實 Drive 同步、人臉模型與婚宴網路吞吐仍需在你的主機驗收。可先用少量代表性照片測試，確認「同步匯入 → 人臉分析 → 頭貼／自拍搜尋」完整流程。

進階：本機舊照片可加入佇列並備份至 Drive：

```bash
# 先停止 Node；在 backend/ 執行
source .venv/bin/activate
python face-service/import_photos.py /你的舊sample_photos
```

原本的旧 `/reindex` 已不需要手動呼叫。修改分組門檻只影響之後新增照片；請優先使用管理頁合併，刪掉索引會失去整理紀錄。

## 本機顯示收到照片，但 Drive 沒有新圖

這次已修正上傳提示：100% 只表示本機接收完成；頁面會再確認每張照片的 Drive 備份狀態，全部確實完成才顯示「雲端備份完成」。關閉 Drive、等待 Google 授權或備份失敗，會顯示原因。相同照片重新主動上傳會建立新的雲端檔案。

登入管理頁的「照片雲端備份」，可以開啟**真正使用的備份資料夾**。如果 Google Drive 目前看的是 `Guests_upload/Original`，而設定填的是外層 `Guests_upload` ID，新照片會寫在外層，不會自動選擇 Original。

需要雲端舊照片讀取外層、但新照片寫進 Original，可分開設定：

```dotenv
GOOGLE_DRIVE_FOLDER_ID=Guests_upload外層資料夾ID
GOOGLE_DRIVE_UPLOAD_FOLDER_ID=Original子資料夾ID
DRIVE_ENABLED=true
```

ID 是網址 `https://drive.google.com/drive/folders/` 後的部分，**不是資料夾名稱**。改完重啟 Node。原本已備份的檔案不會搬移到新資料夾；同內容照片重新主動上傳會建立新檔案，可以用舊照片重新測試。

後端固定讀取 `backend/.env`，避免從不同工作目錄執行時讀錯設定。

若仍沒有照片，打開 <http://localhost:3000/api/health>：

- `driveEnabled=false`：尚未啟用雲端，將 `backend/.env` 改為 `DRIVE_ENABLED=true` 並重啟。
- `driveConnectionState=authorizing`：等待 Google 授權完成。
- `driveConnectionState=failed`：Google 授權失敗，查看 `driveConnectionError`。
- `backupFailed>0`：管理頁會顯示帳號／寫入權限／資料夾不存在／容量等錯誤。
- 沒有 `service`、`version`、`driveEnabled`：可能還在啟動舊版後端，確認 3000 的程序並重啟此版 `backend/server.js`。

Google 授權失效時，先停止 Node，將 `backend/token.json` 改名保留備份，再啟動 Node、用有資料夾寫入權限的 Google 帳號重新授權。不要公開或貼出 credentials.json、token.json 的內容。
