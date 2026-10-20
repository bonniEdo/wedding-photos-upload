# 前端交接

整個專案開發時，在專案根目錄執行 `pnpm install`，再執行 `pnpm dev`，即可同時啟動前端、Node API 與 Python 人臉服務。第一次需先安裝 uv；詳細設定見根目錄 README。

只啟動前端時，可以從根目錄執行 `pnpm --filter @wedding/frontend dev`，或使用下方方式。

用 Node 22+ 啟動：

```bash
node dev-server.mjs
```

開啟 `http://localhost:5173`。後端需啟動 3000；可從根目錄 `API.md` 查看串接規格。

- `photos.html`：照片上傳。
- `invitation.html`：溫馨風格電子喜帖、婚宴資訊與出席回覆。
- `people.html`：太空背景的蒲公英人物球、人物列表、相簿、自拍搜尋。
- `admin-people.html`：管理登入、雲端同步、人物合併／復原。
- `assets/*.css`：各頁樣式。
- `assets/*.js`：各頁互動。
- `assets/api-client.js`：共用 API 與登入 Token（只存在頁面記憶體）。
- `config.js`：修改 API_BASE，正式部署接後端 HTTPS 網址。

可以自由換框架或重新設計 UI，維持 API 欄位與上傳每批 5 張即可；選取上限 20 張。前端不要加入 Google 憑證、管理密碼或資料庫設定。

## 電子喜帖

預設 `INVITATION.submissionMode:'google-form'`，直接開啟 `invitation.html` 或放在靜態網站上即可填寫，不需 Node 後端；須保留旁邊的 `config.js` 和 `assets/`。也可使用 `http://localhost:5173/invitation.html` 預覽。在 `config.js` 的 `INVITATION` 修改新人姓名、日期、迎賓／開席／結束時間、回覆日期、會館與地址；確認資料後將 `isSample` 改為 `false`。`mapsQuery` 填入正式地址才會顯示 Google Maps 連結。加入行事曆使用台灣時間，範例行程會標記「範例」。

選擇出席才展開出席人數（含本人，1–20 位）、素食人數、兒童座椅與飲食備註。無論是否出席，都可填寫給新人的話。Google 表單目前沒有照片上傳題，因此純靜態模式隱藏照片選擇器並顯示說明，不會讓賓客選了照片卻未保存。

送出時使用原生 HTML `form` POST 到 Google 的 `formResponse`，目標是隱藏 iframe，使用者留在喜帖。初始空白 iframe 載入後才送出，之後載入時顯示 alert：「謝謝你的心意！回覆已送出，請以新人確認為準。」不使用跨來源 `fetch`，也不把 iframe 的 load 當成已驗證收件。素食人數與飲食備註附在「給新人的話」最後。`INVITATION.googleFormUrl` 為新人提供的「參加的人」，沿用其試算表；更換表單時需更新 `assets/invitation-google-form.js` 的欄位對照。

若要使用原有照片留存功能，可明確改成 `submissionMode:'backend'`，並透過 HTTP 啟動 Node API。此選用模式才會將回覆與照片保存到 `DATA_DIR/rsvps/<UUID>/`，照片最多 3 張、每張 10 MB；詳見 [設定指南](../integrations/google-forms/README.md)。

純靜態模式受同源限制，無法讀取 Google 在 iframe 裡的收件結果，也不建立本機回覆紀錄。送出或逾時後保留內容並鎖住表單，避免重複點擊。離線或尚未 POST 的錯誤可重試；POST 後逾時則提示聯絡新人核對。若需要可靠收件確認，可選後端模式。

驗證：`node backend/tests/invitation-static.mjs`；後端模式另執行 `node backend/tests/invitations.mjs`（皆於專案根目錄執行，不會修改真實 Google 回覆）。

## 婚禮照片星球

`people.html` 直接使用 `/api/face/people` 的所有分析大頭照，以球面座標與透視呈現前後遮擋，每個人物都有一條線連到中央新人合照。大頭照依鏡頭深度呈現近大遠小，旋轉到前方會逐漸放大，轉到後方會逐漸縮小。各人物離中心的距離不同，連線長度依人物 ID 固定，不會因更新照片而隨機改變。大頭照靠近中央合照時依距離漸進偏移，越近偏移越多，離開後自然回到原本路徑；連線端點跟隨照片，點擊人物聚焦時仍會置中。新分析結果每 8 秒更新；不依臉部外觀推測親友關係。

- 滾輪／觸控板或頁面捲軸：上下旋轉球體，可連續轉過一整圈；拖曳可上下左右轉動。
- 懸停或鍵盤 Tab：放大人物；點擊：旋轉到前方並聚焦，接著可開啟相簿。
- 觸控板捏合或手機雙指手勢：縮放星球；Escape：回到全景。手機可單指拖曳。
- 球體取得焦點後可用方向鍵旋轉、加減鍵縮放；自轉與檢視切換遵循系統減少動態效果設定。
- 「人物列表」與「相遇星球」共用同一批大頭照，切換時會平順排列成列表或收回球體，連線同步淡出／淡入；途中切回會從當下位置接續。系統設定減少動態效果時直接切換。「自拍找我」展開既有自拍搜尋。

中央先顯示可更換的照片框，點選可預覽本機圖片（25 MB 以內），不會上傳或修改相簿，重新整理後還原。若要讓所有賓客看到同一張合照，把照片放在 `frontend/assets/couple.jpg`，將 `config.js` 的 `COUPLE_PHOTO_URL` 設為 `assets/couple.jpg`，也可填公開圖片網址。這個前端改動不需要新增套件。
