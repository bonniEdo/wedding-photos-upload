const path = require('node:path');
require('dotenv').config({path: path.join(__dirname, '.env')});

// A fresh checkout can run locally before Google OAuth is configured.
if (process.env.DRIVE_ENABLED === undefined &&
    !process.env.GOOGLE_DRIVE_FOLDER_ID && !process.env.GOOGLE_DRIVE_UPLOAD_FOLDER_ID) {
  process.env.DRIVE_ENABLED = 'false';
  console.log('開發模式：尚未設定 Google Drive，照片先儲存在本機。設定 backend/.env 後重啟即可啟用雲端。');
}

require('./server').main().catch(error => {
  console.error(error);
  process.exit(1);
});
