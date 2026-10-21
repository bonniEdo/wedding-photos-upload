# P3–P4 人工複核、錯分校正與自拍搜尋

2026-10-21。P3–P4 工程與隔離測試完成。沿用本機 YuNet／SFace，沒有新增通用決策模型、套件或模型權重。本輪沒有改寫正式 `DATA_DIR`、`.env`、重建既有人物、啟動／重啟正式服務或呼叫雲端 API。

本文件保存 P3–P4 階段結果；後續正式備份、舊照回填及最新驗證見 [P5 實作與回填報告](face-recognition-p5.md)。

## 操作

管理頁 `admin-people.html` 登入後，在「人工複核」切換可能重複人物與待確認人臉。

- 可能重複：查看證據照片後，合併並選保留頭貼、確認不同人，或略過當次建議。同張照片出現的兩組會加提示，仍由管理員確認鏡面／拼貼等例外。
- 待確認：確認目前歸屬，或開啟照片選一張臉移轉。低品質觀測經確認後仍不自動加入可信代表庫。
- 從建議或相簿的「檢查人臉」開啟照片，框選／單選人臉，選既有人物或「拆出成新人物」，確認後只移動選定臉。同圖其他人的歸屬不變。
- 「不同人排除紀錄」可撤銷約束；「整理紀錄」依最新到最舊復原合併及人工操作。有較新的校正時，先復原它才能復原更早的合併。

建議使用每個人物最多三個可用且相異的觀測做配對，呈現最強配對的兩張裁切與原照片位置；也可從人物相簿確認其他角度。duplicate 門檻 0.50 是候選提示，未被解讀為本人機率，也不會自動合併。無品質資訊的舊臉可提供候選，已知低品質的臉不產生合併建議。略過綁定當次證據版本，有新觀測可能再出現建議。

自拍頁保留單人照條件與原結果格式，新增取消搜尋、取消等待重試及模糊／部分偵測提示。換自拍或切到人物相簿會中止舊搜尋，舊回應不覆蓋新選擇。

## 資料相容與交易

新增 `face_state.py`、`reviews.py`。舊索引無須啟動遷移：缺少 face ID 時，以照片 ID 與原觀測陣列位置推導穩定 ID，GET 不會寫回或重排資料。舊 bbox 的預覽座標與新原圖座標各自保留，API 提供 normalized_bbox 讓框能畫在預覽上。舊非公開裁切缺失時可退回既有頭貼，原照片仍可檢視。

人物、指派、不同人證據、歷史、revision 與重試收據只寫入同一份 `face_index.json`，先修改副本，再原子落盤後發布狀態。寫檔失敗不提交半套修改。新管理寫入要求 revision 與 request_id；過期頁面回 409，相同網路重試回持久化收據。收據在重啟與操作復原後仍保留，相同 ID 不會再次執行舊操作。

既有 `merge_history` 保留為可復原前綴；新合併仍寫原歷史，另以 `admin_history` 合併指標與人工校正共用復原順序。保留 aliases、合併頭貼及各組歷史樣本。單臉移轉會移除可追溯到該錯分臉的代表污染，保留無法對應觀測的舊人工樣本。原人物即使移空也保留 ID；公開列表／狀態計數隱藏空人物，舊相簿網址回空陣列，可日後再移入。

復原僅改原本涉及的觀測；操作後新增照片仍留在當時相簿。新建立人物若復原後變空也保留 ID。復原不會把已知低品質／拒收觀測升為比對樣本；legacy 配對也排除 provisional 人物，保護舊 enhanced 索引中仍存有低品質歷史向量的情況。不同人約束綁定兩張穩定觀測，按目前歸屬阻擋違反證據的合併／移轉，並不任意傳遞到所有相似臉或未來觀測。

裁切端點沿用 Node 管理登入、管理標頭與 Python 內部驗證，JPEG 使用 no-store。前端用帶標頭的 fetch 載入 Blob 圖片，以支援跨來源 Bearer；換頁、登出或關閉編輯器會釋放 object URL。API 不回傳 embedding。

## 搜尋實作與效能

新增 `search_index.py`：依 embedding_revision 建立不可寫的 NumPy 矩陣快照，從狀態鎖取得來源後在鎖外計算。新照片使快取失效；合併與校正不改 embedding，因此沿用搜尋快照。快取於 Python 重啟時重建。

預設 `FACE_SEARCH_MODE=vectorized`；`scalar` 可切回逐臉比較。兩者門檻一致，每張照片取所有臉最高 cosine，以四位小數 score 穩定降冪排序，同分保留原照片順序。向量化對門檻邊界及照片內最大分數候選以原 scalar 算式重算，避免 SIMD 捨入差異改變門檻或排序。沒有 Top-K 預篩、人物分組過濾或新搜尋門檻。

本機 arm64／NumPy 2.2.6 的可重現合成測試：3,000 張照片、每張三臉、128 維、100 個合成人物、12 個查詢、threshold=0.30、seed=42。交錯執行兩種比較，結果包含照片、score、順序完全相同。

| 項目 | scalar | vectorized（快取已建好） |
| --- | ---: | ---: |
| P50 | 35.07 ms | 0.447 ms |
| P95 | 35.47 ms | 0.483 ms |

P50 約 78.6 倍；快照建立 38.37 ms，矩陣／norm／索引陣列 4,716,000 bytes。這是單程序搜尋比較的合成微基準，**不含解碼、偵測／特徵、模型鎖等待、HTTP 或網路**；首次搜尋需建立快照。陣列大小不是 Python 全程序尖峰記憶體。不能把此結果稱為整體自拍快 78 倍，或辨識準確率提升。

重現（報告路徑需尚不存在）：

```bash
backend/.venv/bin/python backend/face-service/benchmark_search.py --output face-evaluation/search-benchmark-next.json
```

最終報告位於忽略 Git 的 `face-evaluation/search-benchmark-p3-p4-verified.json`，保存合成 fixture、原始碼雜湊、參數與環境資訊，不保存人臉向量。測試 fixture SHA-256：`2f9d81c9a43feef87ea6e9a9e9bdf2393a6e55e2317e00199d7054ab9a2b69a1`。

查詢圖不加入索引、不保存裁切或訓練資料。Node 在轉送成功／失敗／客戶端取消時刪除暫存自拍並釋放搜尋名額；客戶端斷線會 abort 上游 fetch。已經開始的單次 OpenCV 分析可能仍完成，不承諾可立即取消模型運算。

## 驗證結果

- **Python 78 項通過**：包含原 P0–P2 回歸，以及單臉移轉／拆出、同圖其他人保持原歸屬、污染樣本移除、舊人工樣本保留、空人物 ID、全域復原順序、操作後補傳、不同人約束／撤銷／復原、建議略過失效、版本／收據／重啟、原子提交失敗、舊座標與純讀取、搜尋邊界／同分／並行／無效向量／快取更新。
- **Node 8 組通過**：原有 admin-auth、drive-sync、cloud-storage、backup-status、frontend-api；新增 face-review-api、selfie-cancel、selfie-ui。雲端相關測試使用模擬 API。
- **真實 Node → Python 整合**：以獨立暫存索引模擬缺少 face ID／admin_history 的舊資料及既有合併；使用實際 ONNX 模型與 sample_photos 的單人裁切搜尋。驗證管理驗證、二進位裁切、單臉校正前後相同搜尋結果、過期版本、重試與重啟、舊 aliases 及 merge_history 完全一致。此查詢是流程測試，不是獨立準確率測試。
- **斷線與前端競爭**：取消請求會中止 Node 上游、移除暫存並釋放名額；前端取消 429 等待不洩漏 timer，換自拍後舊成功／429 回應不覆蓋畫面。
- **Chrome 實際畫面**：暫存合成頭像驗證不同人排除／復原、待確認歸屬確認、照片框與單選、拆出新人物／復原；原圖、受保護裁切及目標頭貼皆載入，console 無 error。操作使用頁內確認對話框，預設聚焦取消。畫面存於忽略 Git 的 `face-evaluation/p3-p4-review.png`。
- JavaScript 語法及 `git diff --check` 通過。

執行方式（專案根目錄）：

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
```

`face-review-api` 須已有 `.venv`、兩個 ONNX 模型與 sample_photos；`--serve` 可在通過測試後保留隔離服務供畫面驗證，結束後自動清理。其餘 Python 測試不需下載模型。全部沿用一個 Node、一個 Python worker 的 JSON 部署前提。

## 啟用與限制

部署包含前端新增資產及 Node／Python 程式後，先備份整個 DATA_DIR，再於維護時段重啟兩個服務。既有 `.env` 沒有 FACE_SEARCH_MODE 時會使用 vectorized；設定 scalar 並重啟 Python 可回復搜尋演算法，不會回復人工指派。不要用舊版程式寫入已有人工校正的新索引；完整回退須停寫並還原配套程式與資料備份。

本輪只完成程式及隔離驗證，未替正式環境啟用／整理人物。P0–P2 的 `FACE_PIPELINE=legacy`、`GROUP_MODE=legacy` 預設保持，enhanced 品質仍需婚禮人工標註驗收。P5 舊照回填尚未實作，原來漏掉的臉不會因本輪更新自動出現。

建議快取每次資料更新會重算，duplicate 建議目前對人物兩兩比較；超大型人物庫的延遲仍需實測。人工歷史與重試收據未自動裁剪，長期大量操作需留意索引大小。缺少標註的品質驗收、實際婚宴網路／多人搜尋延遲、P5 備份與回填演練仍待後續驗證，不能只靠本輪合成測試推論誤配率。
