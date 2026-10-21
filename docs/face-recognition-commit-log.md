# P0–P5 分階段提交與驗證紀錄

日期：2026-10-21。基底為 `9628541`，提交於原有 `main`。本次將已完成的改善工作依依賴順序拆分，沒有重寫既有 commit，也沒有推送遠端。

| 順序 | Commit | 範圍 | 本次獨立版本驗證 |
| --- | --- | --- | --- |
| 1 | `3777fb9` | P0–P2：離線評估、本機偵測與保守配對 | 52 項 Python 通過 |
| 2 | `d113a47` | P3：人工複核、單臉校正與統一復原 | 71 項 Python、2 組 Node 通過 |
| 3 | `e2f766c` | P4：精確向量搜尋、取消與查詢提示 | 78 項 Python、8 組 Node 通過 |
| 4 | `4551bc6` | P5：驗證備份、可續跑回填與候選確認 | 150 項 Python、11 組 Node 通過 |
| 5 | `docs(face): record rollout, preservation checks and P5 results` | 計畫、API、操作與驗證紀錄 | 文件連結及差異格式檢查通過 |

各列 Python 數量為該版本的累計測試數，不應加總。每筆功能提交都有詳細本文，記錄功能、相容性、資料保留與驗證方式；可用 `git show --format=fuller <commit>` 查閱。

## 1. P0–P2：評估、偵測與配對

新增 `evaluate_faces.py`、`face_engine.py`、`matching.py` 與 `face_state.py`，將影像分析與配對政策從服務入口分離，保留相同 YuNet／SFace 權重及 embedding 空間。

- P0：離線標註格式、管線／策略指紋、可重現評估及輸出目錄保護。未標註照片只報觀測結果，不換算為準確率。
- P1：EXIF／原尺寸座標、重疊分區、框去重、品質量測、私有裁切、像素／分區／時間預算及 partial 標記。shadow 只保存比較資訊，失敗不阻擋原分析。
- P2：整張照片指派、候選差距、單樣本嚴格門檻、樣本來源與代表集、低品質 provisional 人物。worker 先完成影像產物，再以副本原子提交索引。

此提交也加入後續人工整理需要的穩定觀測 ID、revision 與操作順序基礎。舊合併與復原仍可運作，原人物、樣本、頭貼鎖定及 aliases 保留；復原保留舊快照錨點與後來新增的照片，避免招募已知不合格觀測。

詳見 [P0–P2 實作與評估](face-recognition-p0-p2.md)。

## 2. P3：人工複核與錯分校正

新增重複人物／待確認人臉清單、不同人約束、略過及確認歸屬。管理頁可查看照片框與私有裁切，將單張人臉移到既有人物或拆為新人物，同照片的其他臉不跟著移動。

校正會處理可追溯的污染樣本、相簿、頭貼與張數，保留無法追溯的舊錨點及移空人物 ID。合併與人工校正共用由新到舊的復原順序。

管理寫入以 revision 檢查過期狀態，以 request_id 保存重試收據；回應、資料及操作歷史在同一次原子提交中保存。過期頁面回 409，程序重啟後重試不會重複操作。Node 沿用登入、管理標頭及 CSRF 防護代理私有 JSON／JPEG。

此版本保留原 scalar 搜尋；管理交易測試可在尚未加入 P4 模組時獨立執行。

## 3. P4：自拍搜尋改善

新增不可變矩陣快照及 embedding_revision 快取，在狀態鎖外完成精確比較。維持每張照片的最高 cosine、門檻、分數精度及同分順序，另保留 scalar 回退模式。

前端支援取消搜尋、停止 429 退避等待、換照後忽略舊結果，以及品質／部分偵測提示。Node 在瀏覽器斷線時取消上游 fetch、釋放搜尋名額並清除自拍暫存；單次 OpenCV 推論呼叫仍不能保證立即中斷。

新增實際本機 ONNX 的 Node／Python 整合測試，驗證人工校正前後搜尋結果、舊合併歷史、私有裁切、冪等交易及重啟。P3 與 P4 的操作及合成效能基準見 [P3–P4 實作說明](face-recognition-p3-p4.md)；比較階段的速度不等於端到端速度或準確率。

## 4. P5：備份與舊照片回填

新增完整備份、逐檔 SHA-256 驗證、還原演練及追加式遷移工具。回填使用獨立持久化佇列，支援 claim、暫停、重試及重啟恢復；取圖及分析在狀態鎖外，提交前重新核對照片證據與管線，避免覆蓋人工整理。

原圖來源優先使用本機穩定快照；Drive 路徑核對歷史 checksum 與下載前後版本／權限，限制單張大小、序列下載並清理暫存。這些流程不重設 originalReleased、不重新匯入或上傳，也不改同步紀錄及分享權限。

保守幾何對應只補 metadata，新增偵測先留在私有候選；管理者明確確認後，才追加 provisional 人物與觀測。追加及復原都更新搜尋快取版本，並納入同一套全域整理紀錄。

本次正式資料操作結果：

| 項目 | 數量 |
| --- | ---: |
| 完成補分析 | 347 / 347 張 |
| 原人物 | 85 → 85 個 |
| 正式觀測 | 298 → 298 張臉 |
| 舊觀測補穩定 ID | 298 張 |
| 高信心對應補資訊 | 296 張 |
| 待人工確認的新候選 | 59 張，分布於 39 張照片 |
| 不明對應紀錄 | 6 項，保留原判斷 |
| 失敗／部分偵測／原圖暫存殘留 | 0 |

新候選尚未自動加入相簿，數量不能視為已確認的漏臉或準確率提升。正式佇列已暫停，沒有待執行任務。

正式備份位置：

```text
/Users/walkerlin/Project/wedding-photos-upload/data-backup-p5-migration-20261021/
```

包含 1,344 個原始資料／設定檔、共 787,026,105 bytes；封存 SHA-256：

```text
d6b27e663d21498ed014c2470457db7fb2f53ad8cecc30d4236b290a5ac6a2ec
```

另有實作前快照：

```text
/Users/walkerlin/Project/wedding-photos-upload/data-backup-p5-prework-20261021T060409Z/
```

完整還原演練、原欄位及 1,343 個其他原始檔案不變的證據，見 [P5 實作與回填報告](face-recognition-p5.md)。正式資料原本沒有人工／合併項目；含既有人工校正與合併歷史的保留及復原，另以非空 fixture 測試。

## 本次提交前的重驗方式

P0–P2、P3、P4 分別從原基底建立暫存原始碼快照，僅加入該階段檔案；沒有把後續模組放進較早快照。快照共用既有執行環境、權重與測試照片，測試資料寫入獨立暫存目錄。

各階段與最終工作目錄執行：

```bash
rtk proxy backend/.venv/bin/python -m unittest discover -s backend/tests -q
```

P3 另跑 admin-auth 與 frontend-api；P4 跑下列前八組；P5 最終版本跑全部十一組：

```bash
rtk proxy node backend/tests/admin-auth.mjs
rtk proxy node backend/tests/drive-sync.mjs
rtk proxy node backend/tests/cloud-storage.mjs
rtk proxy node backend/tests/backup-status.mjs
rtk proxy node backend/tests/frontend-api.mjs
rtk proxy node backend/tests/face-review-api.mjs
rtk proxy node backend/tests/selfie-cancel.mjs
rtk proxy node backend/tests/selfie-ui.mjs
rtk proxy node backend/tests/backfill-originals.mjs
rtk proxy node backend/tests/backfill-source-api.mjs
rtk proxy node backend/tests/backfill-api.mjs
```

以上全部通過。Google API 行為以模擬測試覆蓋，本次重驗未呼叫真實 Drive 帳號。P5 報告另記錄先前完成的 Chrome 操作驗證；本次拆分提交沒有重做或重複計算該次瀏覽器驗證。

## 資料與設定界線

本次拆分提交前後再次核對正式 face_index.json、實際 .env、frontend/config.js 及兩份備份 manifest 的 SHA-256，皆保持一致。所有原有工作檔案內容也保持一致；只新增本紀錄及 README 導覽連結。

Git 僅收錄程式、測試、範例設定與文件。正式索引、照片、embedding、私有候選、憑證、模型權重、實際設定、備份及評估影像保持忽略。未啟動正式服務，也未再次回填資料。

目前預設 FACE_PIPELINE／GROUP_MODE 仍為 legacy，搜尋預設 vectorized；沒有加入 Jev 或其他通用決策模型。原尺寸偵測及保守配對策略的正式品質驗收仍需人工標註，詳見 [改善計畫](face-recognition-improvement-plan.md)。
