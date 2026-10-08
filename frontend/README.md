# 前端交接

用 Node 22+ 啟動：

```bash
node dev-server.mjs
```

開啟 `http://localhost:5173`。後端需啟動 3000；可從根目錄 `API.md` 查看串接規格。

- `photos.html`：照片上傳。
- `people.html`：人物頭貼、相簿、自拍搜尋。
- `admin-people.html`：管理登入、雲端同步、人物合併／復原。
- `assets/*.css`：各頁樣式。
- `assets/*.js`：各頁互動。
- `assets/api-client.js`：共用 API 與登入 Token（只存在頁面記憶體）。
- `config.js`：修改 API_BASE，正式部署接後端 HTTPS 網址。

可以自由換框架或重新設計 UI，維持 API 欄位與上傳每批 5 張即可；選取上限 20 張。前端不要加入 Google 憑證、管理密碼或資料庫設定。
