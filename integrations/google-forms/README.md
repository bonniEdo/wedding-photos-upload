# 喜帖串接 Google 表單

保留喜帖的填寫畫面。Node 後端先保存回覆，再透過 Google Apps Script 上傳私人照片、提交 Google 表單；Google 表單連動的試算表可供新人整理出席名單與素食人數。

## 1. 沿用已提供的表單

目前指定的表單是 [參加的人](https://docs.google.com/forms/d/e/1FAIpQLSdkKuTSGS_u79FWXt1khwJmPGU2kSHNyYj_vrZDTjfjGS4UZg/viewform)。喜帖的 `frontend/config.js` 已加入此填寫連結；喜帖送出按鈕下方可直接開啟這份 Google 表單。填寫連結並不是自動同步的 Apps Script 接收網址，不要放進 `WEDDING_GOOGLE_FORMS_URL`。

1. 用表單擁有者帳號開啟這份表單的**編輯畫面**，從右上角「⋮」開啟「指令碼編輯器／Apps Script」。必須從指定表單開啟，不能把 `/d/e/1FA…/viewform` 的公開識別碼當成編輯表單 ID。
2. 將本目錄的 `Code.gs` 完整貼入編輯器並儲存。程式已設定這份表單的填寫網址，會檢查目前表單是否相符，不會另建表單。
3. 在函式選單選 `setupWeddingForm`，按「執行」，完成 Google 要求的授權。程式會存取 Google 表單、試算表與 Drive，以建立與寫入回覆及照片；由帳號持有人檢查並完成授權。
4. 設定會沿用原本的表單與已連動試算表；尚無試算表時才會建立並連動。執行紀錄會顯示管理頁、試算表與私人照片資料夾網址。重跑會沿用同一批資源。
5. 開啟左側「專案設定 → 指令碼屬性」，找到 `WEDDING_SHARED_SECRET`。稍後將這個值複製到本機後端 `.env`，不用貼在聊天中。

程式會沿用既有六題：姓名、是否參加（是／否）、出席人數、兒童座椅、是否素食（是／否）、給新人的話；補上素食人數、飲食備註、照片連結、回覆編號與校驗碼。這些補充題會在執行設定後出現在 Google 表單中，但不會成為賓客必填項目。喜帖已補上兒童座椅數量，沒有出席時不送出座椅需求。不要刪除整合使用的題目、改變題目類型，或啟用「每人限填一次」。

## 2. 部署 Google 接收端

1. 在 Apps Script 選「部署 → 新增部署作業 → 網頁應用程式」。
2. 「執行身分」選自己；「誰可以存取」選任何人，供 Node 後端呼叫。部署的接收端會對每個請求驗證上述 Secret；不提供公開讀取、列表或下載功能，不會開放 Drive 照片的分享權限。請勿公開 Secret。
3. 由帳號持有人完成部署與授權，複製結尾為 `/exec` 的部署網址。不要使用只有編輯者能存取的 `/dev` 測試網址。

若公司或學校帳號不允許匿名網頁應用程式，請改用允許此部署方式的帳號，或由管理員決定可用方式。修改 `Code.gs` 後，需管理部署並選新版本，既有 `/exec` 才會更新。

## 3. 設定專案後端

在 `backend/.env` 加入：

```dotenv
WEDDING_GOOGLE_FORMS_URL=https://script.google.com/macros/s/你的部署ID/exec
WEDDING_GOOGLE_FORMS_SECRET=填入指令碼屬性的WEDDING_SHARED_SECRET
```

重新啟動 Node 後端。前端 `config.js` 不需要 Google 設定，Secret 不可放進前端。先用喜帖填一筆明確標示「測試」的回覆，確認 Google 表單和試算表都收到資料；再用一張小照片測試 Drive 連結可由新人帳號開啟、登出後無法存取。實際 Google 帳號、配額和部署權限必須在完成部署後驗收。

## 保存與重試

- 本機紀錄與照片維持在 `DATA_DIR/rsvps/<UUID>/`。雲端確認後另外保存 `google-forms.json`，不刪除本機資料。
- 啟用 Google 同步時，只有 Google 回傳有效回覆編號後，喜帖才顯示送出成功。若同步失敗，後端回覆 HTTP 502，前端保留內容；再次寄出沿用原編號，不重複建立本機紀錄、Drive 照片或 Google 回覆。
- 在專案根目錄執行 `node backend/sync-invitations.js` 可補同步既有與中斷的回覆，也可在網路恢復後重跑；此指令會將尚未同步的歷史回覆與照片送到目前設定的 Google 目的地，請先確認設定。已確認同步到同一部署的回覆會跳過。
- 目前採送出時同步與手動補同步，沒有背景重試排程。Google 中斷時，賓客會看到待重試訊息，本機仍保有資料。
- 所有照片每次只傳送一張，最多三張、每張 10 MB；沒有改用 Google 表單原生檔案上傳題。原圖保存在獨立私人 Drive 資料夾，表單內保存可點擊連結；不進公開婚禮相簿，也不進人臉分析。
- 同步兩個環境或更換 Google 目的地前，先確認想使用的表單。不同 Apps Script 專案會建立不同資源。
- Google Apps Script 使用帳號配額；程式提交的回覆不會觸發一般表單提交觸發器，若日後要通知郵件需另外設計。

未設定兩個環境變數時，維持原本僅本機保存。只填其中一項或部署網址格式錯誤時，後端拒絕啟動，避免誤以為 Google 同步已啟用。

## 驗證

在專案根目錄執行：

```bash
node backend/tests/invitations.mjs
node backend/tests/google-forms.mjs
node backend/tests/google-forms-script.mjs
node backend/tests/frontend-api.mjs
```

Google 整合測試使用模擬服務，不會登入或修改 Google 帳號。

## 官方依據

- [Forms API 的回覆介面](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses)目前只提供 get／list，因此使用 [Apps Script FormResponse.submit](https://developers.google.com/apps-script/reference/forms/form-response#submit()) 寫入回覆。
- [Apps Script 網頁應用程式與部署身分](https://developers.google.com/apps-script/guides/web)。
- [Form.setDestination](https://developers.google.com/apps-script/reference/forms/form#setDestination(DestinationType,String)) 與 [每人限填一次的限制](https://developers.google.com/apps-script/reference/forms/form#setLimitOneResponsePerUser(Boolean))。
- [程式操作不觸發一般可安裝觸發器](https://developers.google.com/apps-script/guides/triggers/installable#restrictions)。
