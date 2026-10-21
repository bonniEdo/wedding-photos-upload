# P0–P2 本機人臉改善：操作與驗證

2026-10-21。工程已實作；正式品質驗收待人工標註。沒有啟動正式服務、重建正式索引、改寫 `.env` 或呼叫外部模型。

本文件保存 P0–P2 階段的結果與雜湊；之後的 P3–P4 程式變更及測試見 [P3–P4 實作說明](face-recognition-p3-p4.md)，舊評估報告雜湊不代表目前全部原始碼。

## 已交付

| 範圍 | 實作 |
| --- | --- |
| P0 | `evaluate_faces.py`：建立未標註清單；四組偵測／配對組合離線比較；IoU 人臉評估、分層小臉指標、誤合併／拆組、自拍搜尋指標、耗時、程序峰值記憶體、模型與程式雜湊 |
| P1 | `face_engine.py`：預覽與原尺寸分析分離、重疊分區、框／五官座標還原、保守去重、品質與管線版本、穩定 face ID、等比例頭貼及非公開裁切、原子保存產物 |
| P2 | `matching.py`／`core.py`：整圖候選快照、一對一競爭處理、保守單樣本門檻、品質／多樣性選代表、人工合併來源分組保留、低品質 provisional 人物、shadow 比較 |
| 相容 | 舊人物 ID、原觀測順序、aliases、合併歷史及鎖定頭貼保留；合併後新增照片仍按原規則在復原時留在主相簿 |

第一版沒有加入通用決策模型或新 Python 套件，也沒有變更模型權重、Turborepo 或 Node／Python 單程序部署方式。P3 單臉移轉／複核及 P5 舊照片回填不在本次交付範圍。

## 開關與門檻

兩個開關各自獨立，預設皆為 `legacy`：

| 設定 | legacy | shadow | enhanced |
| --- | --- | --- | --- |
| `FACE_PIPELINE` | 原有 1600 預覽偵測／對齊 | 正式結果沿用 legacy，另記分區偵測對照 | 原尺寸分區偵測／對齊 |
| `GROUP_MODE` | 原有最大相似度及逐臉分組 | 正式指派沿用 legacy，另記整圖策略判斷 | 整圖配對及品質代表樣本策略 |

建議先保持正式設定不變，做離線評估。需要收集新進照片對照時，再設定：

```dotenv
FACE_PIPELINE=shadow
GROUP_MODE=shadow
```

重啟 Python 生效。這不會重新分析已登記照片。偵測 shadow 額外記錄 `photo.analysis.shadow`；分組 shadow 記錄 `photo.matching_shadow` 的候選、原因、策略 fingerprint 及與 legacy 是否不同。兩種 shadow 都捕捉對照工作錯誤，保留可成功執行的 legacy 結果。

shadow 分組使用當次正式抽取的人臉及現有正式人物快照，不維護第二套長期人物庫；要比較完整管線累積效果，使用離線四組比較。自拍搜尋只使用當前正式偵測流程，不執行 shadow、不保存查詢圖。

enhanced 配對有獨立的 `GROUP_V2_*` 門檻。0.60 分數／0.08 差距、0.65 單樣本、0.70／0.10 入代表庫是保守實驗初值，**尚未校準**。代表分數為同來源子群中最佳 cosine 的 0.75，加前兩個有效支持樣本平均分數的 0.25；不足兩個支持時採最佳分數並使用較嚴門檻。自動配對不以已占用人物之外的弱第二候選補位。

活動代表每個來源子群最多 5 個，以品質與特徵多樣性更新，近乎相同樣本不重複增加支持。人工合併保留各來源子群，故合併後總活動代表可能超過 5；歷史 `samples` 與全部觀測仍保留。沒有量測品質的舊樣本可作既有候選，但新觀測缺品質時不自動當作可靠代表。

低品質新人物會標 `provisional=true`，可查看相簿、管理頁顯示「待確認」，但不招募其他新臉。後續 P3 才提供完整人工校正；現在仍能使用既有合併／復原。

分區限制與品質初值見 `backend/.env.example`。分區預算限制後續 OpenCV 偵測呼叫，無法中斷已開始的一次呼叫；超時／區塊上限會記 `analysis.partial=true`，狀態與人物頁也會提示。它不是整個影像解碼／所有對齊工作的硬即時期限。

## 離線評估與人工標註

在專案根目錄執行（模型需已在 `backend/models/`）：

```bash
uv run --project backend --locked python backend/face-service/evaluate_faces.py init --photos backend/sample_photos --manifest face-evaluation/samples-manifest.json
uv run --project backend --locked python backend/face-service/evaluate_faces.py run --manifest face-evaluation/samples-manifest.json --output face-evaluation/run-001 --split calibration
```

已存在的 manifest 不覆寫；每次 run 使用新的輸出目錄。預設比較 `legacy-legacy`、`legacy-enhanced`、`enhanced-legacy`、`enhanced-enhanced`，第一個名稱為偵測管線，第二個為分組。可用 `--pipeline`、`--group-mode` 限定組合，或以 `--order reverse` 驗證反向匯入敏感性。

`report.json` 記錄統計、模型 SHA-256、程式 SHA-256、實際設定、版本、處理順序與峰值記憶體；各 `*-predictions.json` 保存原圖座標、特徵與配對證據，**包含敏感的人臉資料**，留在被 Git 排除的 `face-evaluation/`，不要公開上傳。

工具只讀取模型／來源圖，拒絕寫入預設或已設定的 `DATA_DIR`、模型目錄、來源照片目錄及其 symlink 別名，不匯入會啟動服務的 `app.py`。不建立正式相簿或雲端任務。

manifest 使用 EXIF 校正後的原圖像素座標。照片格式範例（以人工核對的內容替換）：

```json
{
  "id": "photo-0001",
  "file": "example.jpg",
  "sha256": "保持 init 產生的內容雜湊",
  "split": "calibration",
  "scene_id": "scene-01",
  "annotated": true,
  "faces": [
    {"bbox": [120, 80, 60, 75], "identity": "person-A"},
    {"bbox": [250, 90, 20, 25], "identity": null}
  ]
}
```

`annotated=true` 表示已核對此照片所有可評估人臉，空 faces 才表示已確認無臉；框可辨識、身份不明時用 identity=null。需要排除的區域可設 `ignore=true`。不可直接把模型預測抄成真值。

同一連拍／場景使用相同 scene_id，整組放在 calibration 或 test，工具拒絕跨集合場景、重複內容或內容變更。初始 `scene_id=unassigned` 必須由人工分組後再拆測試集。test 只作保留驗收，門檻調整在 calibration；`--group-threshold` 等參數可比較候選設定，工具不自動用測試集挑門檻。

自拍放在同一 root 下並加入 manifest 的 `queries`（不加入 photos）。除了 id、file、sha256、split、scene_id、annotated，另填 `relevant_photo_ids`，指定同一 split 中確定相關的照片。無臉／多臉查詢仍計零命中，不從分母移除。precision@20 採命中數／20；recall@20 採命中數／相關照片數；無相關照片時 recall 為 null。

偵測採 score 順序、一對一 IoU≥0.5；小臉定義為原圖 bbox 較短邊≤64 像素（可用 `--small-pixels` 更改）。分組指標只對成功偵測且可對應人工身份的觀測計算，必須同時看漏偵測與未辨識身份數。報告提供點估計及樣本數，尚未實作場景 bootstrap 信賴區間；不可把大量相關配對當作獨立樣本主張統計顯著改善。

## 本次測試結果

- Python：52 項測試通過（原有 14 項，加上偵測、評估、整圖配對、worker／保存邊界回歸）。
- Node：`admin-auth.mjs`、`drive-sync.mjs`、`cloud-storage.mjs`、`backup-status.mjs`、`frontend-api.mjs` 通過；Google／人臉外部 API 為模擬，資料目錄隔離。
- 前端兩份修改過的 JavaScript 語法檢查通過；尚未做瀏覽器視覺驗收。
- 額外擷取目前 Git HEAD 的原版 `load_image/extract`，與新模組 legacy 路徑對 10 張樣本做真實 YuNet／SFace 比較：24 個框、quality 及 embedding 全部完全一致；產物僅寫暫存目錄。
- 額外以真實 YuNet／SFace 跑 legacy、enhanced 兩種 worker 生命週期：從獨立暫存索引載入既有合併資料、處理新照片並落盤，確認舊觀測、aliases、合併歷史及鎖定頭貼未變，必要裁切與預覽存在。
- 實際模型離線四組試跑完成，沒有部分偵測；摘要如下。樣本只有 10 張且未標註，因此沒有準確率或召回提升的結論。

| 偵測／分組 | 人臉框數 | 人物群組數 | 分析 P50／P95（ms） |
| --- | --- | --- | --- |
| legacy／legacy | 24 | 13 | 68.6／119.2 |
| legacy／enhanced | 24 | 21 | 61.0／89.1 |
| enhanced／legacy | 28 | 17 | 159.7／199.7 |
| enhanced／enhanced | 28 | 25 | 156.8／185.7 |

分區偵測多了 4 個框，可能包含真實漏臉或誤偵測，需要人工核對。新策略人物群組增加，可能是較保守的拒配，也可能增加同人拆組；不能把它當成品質改善。因此正式預設維持 legacy。

完整原始測試報告位於本機 `face-evaluation/samples-release/report.json`，其中的程式雜湊已核對符合交付版本。耗時為單次本機試跑、未隔離冷啟動／快取，分析時間不含分組及資產落盤，分組時間另列；程序 peak RSS 約 522.6 MiB，是四組共用程序的高水位，不是各策略的獨立記憶體需求。

## 保存及回退界線

必要裁切、頭貼及預覽全部成功保存後，worker 才把照片與人物索引一起提交；產物失敗時保留原圖與可重試錯誤，不建立已完成照片。原子提交中斷前留下的未引用資產可能仍存在，重試會覆寫相同檔名，不會因此新增第二本相簿。

Node 清理仍以雲端確認及正式索引落盤為條件。部分偵測照片仍可能按此規則清理本機原圖；其未完成區域需以後續 P5 從 Drive 重取原圖補做，不會因 shadow 或 partial 自動排出第二個分析任務。

正式資料沒有自動遷移；新增 face ID 只附在新分析觀測。切回 legacy 不復原已用 enhanced 寫入的新指派。更新前仍應備份完整 DATA_DIR；若要退回不認得新欄位的舊程式，需停服務並配對還原程式與資料，不能依賴單一開關逆轉既有改動。
