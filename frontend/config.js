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
  // 電子喜帖：正式使用時請替換以下範例，確認後將 isSample 改為 false。
  INVITATION:{
    isSample:true,
    coupleNames:'子謙 & 以安',
    date:'2027-05-22',
    welcomeTime:'11:30',
    banquetTime:'12:00',
    endTime:'15:00',
    replyBy:'2027-04-30',
    venueName:'幸福花園婚宴會館',
    venueRoom:'花園廳 · 2F',
    venueAddress:'台北市・詳細地址待新人確認',
    venueNote:'把這一天空下來，交通資訊將隨正式喜帖提供。',
    // 填入正式地址後顯示 Google Maps 交通連結。
    mapsQuery:'',
  },
};
