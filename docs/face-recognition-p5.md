# P5 舊照片回填：實作、備份與驗證

日期：2026-10-21。P5 已完成實作、正式資料回填與隔離測試。沿用既有本機 YuNet／SFace 權重；沒有新增決策模型、套件或改寫 `.env`。正式 Node／Python 服務原先未啟動，本輪維持停止，以離線程序處理資料。

## 本次結果

| 項目 | 結果 |
| --- | ---: |
| 已完成補分析 | **347 / 347 張照片** |
| 本機原圖來源 | 347 張 |
| 失敗／原圖不可用／版本變動／過期 | 0 張 |
| 部分偵測 | 0 張 |
| 舊人臉補穩定 ID | 298 張 |
| 高信心對應並補品質／原尺寸裁切資訊 | 296 張 |
| 新漏臉候選 | **59 張，分布於 39 張照片** |
| 不明對應紀錄 | 6 項，保留原判斷 |
| 原人物 | **85 → 85 個** |
| 正式觀測 | **298 → 298 張臉** |
| 原圖暫存殘留 | 0 檔 |

先試跑三張並核對保留欄位，再由另一個程序續跑剩餘 344 張；全部任務只嘗試一次，結果均 ready。正式佇列目前已暫停，沒有待執行任務。新候選仍保存在私有回填資料內，**尚未自動加入相簿或指派給既有人物**；需要人在管理頁確認。

296 是可安全對應的舊觀測數，不是新增人臉數；6 是不明對應紀錄數，可能涉及舊觀測或重疊新偵測，不等於六個不同人物。59 也只是候選，尚未經人工標註，不宣稱皆為真漏臉或代表辨識準確率提升。

## 備份與還原演練

正式可供維護工具驗證的完整備份：

`/Users/walkerlin/Project/wedding-photos-upload/data-backup-p5-migration-20261021/`

- `snapshot.tar.gz`：完整 DATA_DIR 與當時現存的 `backend/.env`、`frontend/config.js`。
- `manifest.json`：1,344 個檔案的大小／SHA-256、來源、目錄與索引雜湊。
- 原始資料總計 **787,026,105 bytes**；目錄權限 0700，備份檔及設定權限 0600。
- archive SHA-256：`d6b27e663d21498ed014c2470457db7fb2f53ad8cecc30d4236b290a5ac6a2ec`。
- 備份前、後完整來源清單一致；封存逐檔校驗與索引／歷史結構稽核通過。
- 已解開到全新的 `/private/tmp/wedding-p5-restore-20261021`，再次驗證 1,344 檔及索引。演練副本已清理，正式備份保留。

開始實作前另保存一份完整原始快照：

`/Users/walkerlin/Project/wedding-photos-upload/data-backup-p5-prework-20261021T060409Z/`

此份為 `data.tar` + SHA-256 清單，亦已逐檔驗證；格式與維護工具不同，正式恢復／驗證請優先使用 migration 備份。當時不存在 credentials.json／token.json，未備份不存在的憑證；未呼叫真實 Google API。

兩份備份皆被 Git 排除。不要將含 `.env` 的備份公開、提交或交給前端部署。

## 保留資料的證據

以回填前快照的完整索引為基準，僅忽略本輪新增的 face_id、backfill_metadata、維護憑證、佇列與 revision 欄位後，回填後索引與原索引完全一致。另逐項確認：

- 人物表及其順序、照片表順序、每張照片原 faces 陣列的順序不變。
- 原 embedding、bbox、person_id、樣本、頭貼及人工欄位不變。
- aliases、merge_history、admin_history、不同人約束、略過紀錄及既有重試收據不變。這份正式資料當時沒有合併／人工操作項目；有歷史的相容性另由專門 fixture 驗證。
- 索引以外原有的 **1,343 個資料／設定檔** SHA-256 全部未變，包括原圖、預覽、公開頭貼、上傳工作紀錄及設定。
- 新 metadata 不改搜尋的 embedding_revision；正式觀測仍 298 張，因此現有自拍搜尋結果不因補 metadata 改變。

聚合報告在 Git 忽略的 `face-evaluation/p5-report.json`，包含備份、數量、保留檢查、目前程式雜湊及最終索引雜湊，不包含 embedding 或身份標籤。

## 實作方式

`maintain_faces.py` 提供唯讀 audit、完整 backup、verify、restore-test 及追加式 migrate。只接受新目錄，拒絕 symlink／特殊檔、來源變動、不一致備份與不安全 tar 路徑。遷移需符合原索引雜湊的驗證備份；只加穩定 ID、版本與備份憑證，不改人物、舊幾何或歷史快照，並保留字典與陣列順序。相同備份憑證重跑不重複修改。Python worker 正在寫入時會拒絕維護；Node 須由操作人員先停止。

`backfill-originals.js`、`backfill-source-cli.mjs` 優先讀本機保留原圖，先存成穩定暫存快照並校驗；不存在才讀既有 Drive ID。雲端需有舊 SHA-256 或 MD5 以核對原照片，並核對下載前／後的 id、大小、MD5、version／modifiedTime、trashed、下載權限。缺歷史校驗時不將目前雲端版本冒充舊原圖。一次只下載一張，預設 100 MiB 上限，成功／失敗／斷線都清理暫存；不重設 originalReleased，不寫上傳或同步紀錄，不重新匯入、上傳或更改 Drive 分享權限。

`backfill_jobs.py`、`backfill_run.py` 使用獨立可續跑佇列。每張任務記照片證據、來源 SHA、模型／預處理／參數管線 key、狀態、嘗試、時間與錯誤。claim 先原子落盤；重啟可恢復 processing。圖片解碼／模型分析在狀態鎖外，使用既有模型鎖；完成時再次檢查照片證據及任務 token，避免蓋掉期間的人工校正。暫時性取圖錯誤最多重試三次；unavailable／changed／failed／stale 保留原因待重試。

`backfill_mapping.py` 將新原尺寸座標與舊預覽／EXIF 座標對齊，以保守的一對一 IoU 及差距規則對應。舊觀測只加 backfill_metadata，新的品質／原框／裁切獨立保存；未對上、幾何缺失或附近重疊都不覆蓋舊判斷。新臉以內容衍生 ID 存成候選，所有未確認影像僅透過管理驗證的 no-store 路由提供。

正式權威仍是同一份 face_index.json；候選及產物先寫檔，之後原子提交狀態。候選 JSON 另存 face-backfill/candidates 作查核副本，API 以已提交索引中的候選為準，不靠掃描殘留檔判斷成功。

確認漏臉後只往原 faces 尾端追加，建立 provisional 人物、暫不招募樣本，保留舊人物 ID／頭貼／人工操作。影像先驗證 JPEG，再以不覆蓋既有檔案的方式保存；同名不同內容、壞檔或磁碟錯誤不提交人物與收據。新 `backfill_add` 與 P3 操作共用由新到舊的復原順序，只能移除該次尾端觀測；後來加入其他照片的相簿保留。追加及復原均更新 embedding_revision，自拍搜尋快取隨之重建。

## 後續操作

目前已完成這一批回填，無需再次遷移或清空人物。啟動更新後的 Node／Python 服務，登入 admin-people.html，進入「舊照片補分析」。待複核任務優先排列，選「複核新增人臉」，查看整張照片的位置與裁切；勾選確定是漏臉的候選，確認後加入待確認人物，再用 P3 的確認／移轉／合併功能整理歸屬。尚無法確認的候選可保持原狀。

線上開始／暫停／重試及確認均沿用 revision／request_id；過期頁面回 409 後刷新。背景回填會更新 revision，整理人物時可先暫停，以免持續遇到重新整理提示。暫停讓當前一張完成，後續不再取圖。已完成且證據／管線相同的任務不重跑。更多任務使用分頁；展開多頁時暫停自動刷新清單，以免打斷選擇。

離線維護範例（專案根目錄；先停止 Node 及 Python）：

```bash
backend/.venv/bin/python backend/face-service/maintain_faces.py verify --backup data-backup-p5-migration-20261021
backend/.venv/bin/python backend/face-service/maintain_faces.py audit --data backend/data
backend/.venv/bin/python backend/face-service/backfill_run.py status --data backend/data
backend/.venv/bin/python backend/face-service/backfill_run.py start --data backend/data --retry
backend/.venv/bin/python backend/face-service/backfill_run.py run --data backend/data --limit 10
backend/.venv/bin/python backend/face-service/backfill_run.py pause --data backend/data
```

離線 run 預設只讀本機，只有明確加 `--allow-drive` 才使用現有 token.json；不開互動授權、不寫 token。若有重試等待時間，run 會處理目前可跑的任務後結束，待到期再跑即可。線上 worker 則持續輪詢。

完整回退須停寫，使用 restore-test 解到另一個新目錄、驗證後切換對應 DATA_DIR 與相符程式／設定；工具不會直接覆蓋正式資料。回退到本輪之前會失去之後才新增的工作，需先另備份現狀。不要用舊程式寫入新索引或直接手改歷史。

## 測試結果與限制

**150 項 Python 測試、11 組 Node 測試全部通過**，另完成 Chrome 實際操作驗證。

- Python：原 P0–P4 回歸，以及 23 項幾何對應、15 項備份／遷移、24 項佇列／追加／復原、10 項 P5 管理交易測試。涵蓋來源變動、過期人工校正、磁碟中斷、重試、重啟、私有輸出、JPEG 損毀、名稱衝突及舊歷史復原。
- Node：原 8 組人臉／管理／搜尋／Drive 回歸，加 backfill-originals、backfill-source-api、backfill-api。雲端異動、權限與 checksum 使用模擬 Drive API；本輪未使用真實雲端帳號。
- 完整整合：實際本機 ONNX、Node 私有原圖串流、Python 背景分析、管理候選 JPEG、人工追加、搜尋新增／復原、重啟收據、舊人工校正及合併紀錄保持一致。
- Chrome：進度、候選框／裁切、選擇／確認追加、統一紀錄復原、開始及暫停皆可操作；console 無 error。截圖 `face-evaluation/p5-backfill.png` 是隔離測試資料的介面示例，數量不是正式回填統計。

```bash
backend/.venv/bin/python -m unittest discover -s backend/tests -q
node backend/tests/admin-auth.mjs
node backend/tests/drive-sync.mjs
node backend/tests/cloud-storage.mjs
node backend/tests/backup-status.mjs
node backend/tests/frontend-api.mjs
node backend/tests/face-review-api.mjs
node backend/tests/selfie-cancel.mjs
node backend/tests/selfie-ui.mjs
node backend/tests/backfill-originals.mjs
node backend/tests/backfill-source-api.mjs
node backend/tests/backfill-api.mjs
```

維護 CLI 使用 macOS／Linux flock；仍以單一 Node、單一 Python worker 的 JSON 部署為前提。P1–P2 的正式偵測／分組預設保持 legacy；回填的 enhanced 分析只補資訊及候選。私有候選／歷史產物不自動刪除，避免破壞復原與查核，長期資料量需按備份策略管理。尚待人工標註確認漏臉與錯配品質；本輪測試不能推論辨識準確率或未來雲端權限仍可用。
