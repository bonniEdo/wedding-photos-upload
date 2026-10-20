# 喜帖直接提交 Google 表單

喜帖預設由瀏覽器原生提交到 [參加的人](https://docs.google.com/forms/d/e/1FAIpQLSdkKuTSGS_u79FWXt1khwJmPGU2kSHNyYj_vrZDTjfjGS4UZg/viewform)，沿用表單已連動的試算表。不需要 Node 後端、Apps Script、API 金鑰或額外建立試算表。

## 啟用

`frontend/config.js` 的 `INVITATION.submissionMode` 預設為 `google-form`。直接開啟 `frontend/invitation.html`，或將整個 `frontend/` 放到靜態網站即可使用；須保留 `assets/` 與 `config.js`，並連線網路。

送出時建立原生 HTML 表單，使用 `POST` 提交到 Google 的 `formResponse`，並以隱藏 iframe 作為目標。喜帖留在原頁，顯示 alert：「謝謝你的心意！回覆已送出，請以新人確認為準。」

跨來源 iframe 的 `load` 事件不代表 Google 已接受回覆；喜帖無法讀取其內容，因此提示只說已送出，不宣稱成功收件。需要 Google 已收件的可靠確認時，請改用下方的後端模式，或由新人到試算表核對。

程式先等待 iframe 的初始空白載入才送出，避免空白頁誤觸發提示；送出期間鎖住表單。收到 iframe 載入事件後保留填寫內容並維持鎖定，避免再次點擊。30 秒逾時或傳送中斷顯示不同的 alert，請新人核對後再決定是否補送；不會宣稱成功。

`frontend/assets/invitation-google-form.js` 保存實際表單網址、六個欄位 ID 與轉換邏輯；選用後端模式也共用此定義。`frontend/config.js` 的 `INVITATION.googleFormUrl` 指定目的表單及直接填寫入口。改表單時須一起更新網址與欄位對照。

| 喜帖欄位 | Google 表單欄位 |
| --- | --- |
| 姓名 | 姓名 |
| 是否出席 | 是否參加（是／否） |
| 出席人數 | 出席人數 |
| 兒童座椅數量 | 兒童座椅 |
| 素食人數大於 0 | 是否素食（是／否） |
| 給新人的話 | 給新人的話 |

原表單只有六題，素食人數與飲食備註會附在「給新人的話」最後的「用餐補充」，祝福文字完整保留。無法出席時清除隱藏的人數與飲食資料。不會修改 Google 表單題目。

## 照片

原表單沒有照片上傳題，所以純靜態模式不接收照片，會隱藏照片選擇器並說明。若日後要用 Google 收照片，須由新人新增照片上傳題，再調整整合流程；不可把本機圖片預覽當成已上傳。原有本機照片留存功能只在下面的後端選用模式提供。

## 選用後端模式

如要保留本機回覆備份與照片，才將 `INVITATION.submissionMode` 改為 `backend`，並透過 HTTP 啟動 Node 後端。此模式仍不需要 Apps Script。照片保存於 `DATA_DIR/rsvps/<UUID>/`，不會傳入 Google 表單。以下流程僅適用此選用模式：

1. 先保存本機回覆，再讀取 Google 表單並驗證六題的 ID、標題、類型與單選值。
2. 取得表單當次提交資訊，將欄位直接 POST 到表單本身的 `formResponse` 網址。
3. Google 回傳「您的回覆已記錄」等確認訊息後，才顯示「已送達 Google 表單」，並將確認紀錄寫入 `google-forms.json`。僅 HTTP 200 或返回填寫頁不算成功。
4. 相同回覆編號的已確認送出會直接沿用結果；同時點擊也只會提交一次。
5. 如果 POST 後網路中斷，無法分辨 Google 是否已收件，紀錄會標記 `uncertain`。系統不會自動重送這一筆，避免重複登記；請新人到試算表用訊息中的回覆編號核對。也不會把不確定狀態顯示為成功。

送出前無法連線或欄位檢查失敗時，可在修復後重試。`node backend/sync-invitations.js` 可補提交本機尚未確認的歷史回覆；執行前須確認目的表單與要補送的歷史資料。`sending`／`uncertain` 紀錄會停止重送，需人工核對。沒有背景排程。

兩種模式皆使用 Google 公開填寫頁的網頁提交流程。Google 修改流程或新人更換題目時，需重驗欄位對照；後端模式會先檢查題目，靜態模式無法直接確認 Google 接受或拒絕結果。

僅離線開發可在 `.env` 設 `WEDDING_GOOGLE_FORMS_ENABLED=false`，此時只存本機，正式使用請保持預設啟用。舊的 `WEDDING_GOOGLE_FORMS_URL` 與 `WEDDING_GOOGLE_FORMS_SECRET` 已不使用，可自行移除。

## 測試

```bash
node backend/tests/invitations.mjs
node backend/tests/invitation-static.mjs
node backend/tests/google-forms.mjs
node backend/tests/frontend-api.mjs
```

以上測試不會實際提交 Google 表單，涵蓋初始 iframe 不觸發完成、背景目標、逾時與無法提交等行為。實際驗收可由喜帖送一筆明確標示「串接測試」的資料，確認主頁網址不變且顯示 alert，再到試算表核對。自動化瀏覽器不允許操作 file 協定，所以雙擊開檔情境未由自動化直接驗證。

原生表單可以跨來源提交的依據：[MDN CORS 說明](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS)。

iframe 載入事件不保證成功的依據：[MDN iframe 的 load／error 行為](https://developer.mozilla.org/en-US/docs/Web/API/HTMLIFrameElement#error_and_load_event_behavior)。
