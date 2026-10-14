// 男友改畫面時只需修改 frontend；後端地址在這裡設定。
// 正式部署可填 'https://你的後端網域'，尾端不要加斜線。
// 空白時：5173 開發模式指向同主機 3000，其餘使用同源後端。
window.WEDDING_CONFIG={
  API_BASE:'',
  // 選填：全部照片的 Google Drive 資料夾網址；空白由後端設定提供。
  ALL_PHOTOS_URL:'',
  // 選填：所有賓客共用的中央新人合照，填圖片網址或前端相對路徑。
  // 例：'assets/couple.jpg'。頁面點選更換僅提供當次瀏覽器預覽。
  COUPLE_PHOTO_URL:'',
};
