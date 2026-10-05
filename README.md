# Naiz Studio

在自己的電腦上處理 PDF、圖片、影音與錄音的工具包。所有檔案都在本地處理，不會上傳。

## 功能

### PDF 工具
- **壓縮**：無損、JPEG、JPEG2000、灰階、黑白（CCITT G4）五種方式，可調整影像品質與解析度上限
- **拆分**：依比例拆分，或勾選、輸入頁碼取出頁面（加入檔案時預設全選），可輸出 PDF、PNG、JPG
- **合併**：多個 PDF 依順序合併（拖曳清單調整順序），也可以加入圖片；頁面大小可以保持原樣，或統一成 A4、第一頁的大小（等比縮放置中，直向橫向各自對齊），可選擇合併後一併壓縮

### PDF 編輯器
- **檢視**：左側頁面縮圖，右側所有頁面連續捲動，可縮放（適合寬度、整頁、50%～400%）
- **搜尋文字**：Ctrl+F 搜尋整份文件，找到的地方在頁面上標出來，Enter 跳到下一個
- **書籤**：左側欄切到「書籤」，點一下跳到那一頁；可以加入、改名、刪除、調整層級，原本的目錄也會讀進來
- **頁面管理**：拖曳排序、旋轉、刪除、複製、插入空白頁，把其他 PDF 或圖片拖進來插入，擷取選取的頁面；裁切頁面（拖曳框線或自動去白邊，內容不會被刪掉，之後可以還原）
- **標記與註解**：螢光筆、底線、刪除線（在文字上拖曳，自動對齊文字行）、文字框（直接在頁面上打字，支援注音，可以加背景色）、便利貼、直線、箭頭、方框、圓形（線條與填滿分開設定）、手繪（連續書寫不會跳出選取框），寫錯可以用橡皮擦拖過整條擦掉；工具列平常只顯示圖示，滑過時顯示名稱。顏色選單依色相排列，選好後可以接著換；右鍵點顏色或按「自訂色彩」可以自由調色（色盤、色碼或 RGB），最近使用的顏色會記住；另可調整粗細、透明度、字型與字級
- **改字**：用「選取」直接點文字就能修改整段，打字時自動重新換行，保留原本的對齊方式、縮排與行距；拖曳選字可以複製，或只改其中幾個字。清空就是刪除
- **沿用原字型**：盡量使用 PDF 裡內嵌的原字型（包含教科書常見的舊式 Type1 字型與數學符號），新字和原字長得一樣；原字型裡沒有的字，改用相近的字型補上（有襯線的字型用 Times 系列、無襯線用 Arial 系列）；文字框與改字可以設定粗體、斜體。儲存時原字會真正刪掉（不只是蓋住），別人選取、搜尋也找不到
- **塗黑個資**：在文字上拖曳，或在照片、簽名上拉出範圍；儲存時底下的字真正刪掉，圖片被蓋住的部分直接塗掉，別人移不開、也取不出原本的內容
- **插入圖片**：選一張圖片，點一下放到頁面上或拖曳出大小，改大小時維持比例
- **移動原本的圖片**：PDF 裡原本就有的圖片可以移動、縮放、刪除，儲存時只改位置，畫質不變
- **簽名**：用滑鼠或觸控筆手寫（寫錯可以復原、重做，或用橡皮擦擦掉那一筆），或拍下紙上的簽名匯入（自動去掉背景）；簽名存在本地，下次直接點選使用
- **印章**：日期章（西元或民國）、姓名章（陽刻或陰刻，可補上「之印」「印」）、文字章（已核准、機密等），可選紅、藍、黑色與印泥質感。印章是圖片，不具數位簽章的法律效力，正式文件請用自然人憑證或工商憑證簽署
- **超連結**：在頁面上拉出範圍，輸入網址或要跳到的頁碼；頁面換了順序，跳頁連結仍然指到同一頁
- **掃描檔辨識文字**：整頁都是圖片的掃描檔，在本地辨識出文字，存檔時加上看不見的文字層，外觀不變，之後就能搜尋、選字、複製
- **修改註解**：新增的註解和檔案原本就有的註解，都能移動、調整大小、改顏色或刪除；按住 Ctrl 拖曳時只往水平或垂直移動，並對齊其他物件、文字與頁面的邊和中線
- **複製、貼上**：Ctrl+C、Ctrl+V 或右鍵選單；圖片會用原本的解析度放進剪貼簿，可以直接貼到其他程式，也可以把其他程式複製的圖片、文字貼進來
- **儲存註解**：存成標準的 PDF 註解，用其他閱讀器也看得到、改得了；也可以勾選「儲存時合併註解到頁面」，合併後就不能再當成註解修改
- **字型**：可以用開源字型包、電腦上的字型，或自己加入字型檔；選的字型裡沒有的字（例如英文字型裡的中文）會自動用中文字型補上。電腦上的字型會依字型檔的授權設定判斷能不能嵌入
- **復原與重做**：每一步頁面操作、註解修改都可以復原
- **儲存**：另存新檔，不覆蓋原檔；也可以自己選擇存檔位置
- **有密碼的 PDF**：輸入密碼後開啟（儲存的新檔不會保留密碼）
- **修復損壞的 PDF**：開不了的檔案可以嘗試修復，救回還能讀取的頁面

### 文件轉檔
- **互相轉換**：Word、PowerPoint、Excel、ODF（odt、odp、ods）、RTF、純文字、網頁、CSV、PDF
- **兩種方式**：電腦上有裝 Office 就用它（排版最準），沒有就用 LibreOffice（需要時才下載）
- **PDF 取出文字**：PDF 轉純文字用程式自己的 PDF 元件，比整份重新排版乾淨，也不用下載任何東西
- **PDF 轉 Word**：程式自己重建成真正的段落與表格，文字、字型、大小、顏色、圖片（照原檔的解析度，不會變糊）、分欄與縮排都會保留，可以直接接著編輯；有框線的表格照框線重建（含合併儲存格），用線條畫出來的圖表會整塊變成圖片
- **兩種版面**：「重新排版」變成一般的段落與表格，最好編輯；「照原樣」每一行都固定在原本的位置，最像原檔
- **掃描檔文字辨識**：整頁都是圖片的 PDF 會辨識成文字（繁體中文、英文），文字以外的照片、圖表保留成圖片；也可以關閉，整頁放成圖片

### 圖片工具
- **調整**：裁切（自由或固定比例、數字輸入、四角放大細調）、擴展畫布、旋轉、翻轉、四點校正（拖曳四個角對準拍斜的文件、白板或畫面邊緣，拉正成長方形，附放大鏡對準）
- **色彩**：參考 Word，用縮圖直接點選——校正（銳利／柔化、亮度 × 對比）、色彩（飽和度、色調、重新著色：灰階、懷舊、刷淡、黑白、各色單色調）；「微調」可以用滑桿細調；滑鼠移到縮圖上，大預覽會先顯示套用後的樣子，按住「看原圖」可以比較前後
- **美術效果**：鉛筆素描、線條畫、模糊、柔光、馬賽克、海報、油畫、浮雕、卡通、底片顆粒、曝光過度、負片，可以調整強度
- **復原與重做**：每張圖的編輯各自記住，可以一步一步復原；編輯可以套用到清單裡的全部圖片
- **文件掃描**：像掃描 App 一樣，自動找出照片裡文件的四個角並拉正（依透視算出紙真正的長寬比，直式的講義不會變胖），可以再拖曳微調；濾鏡有原色、增強（去陰影、紙變白、保留顏色）、灰階、黑白；多張照片依清單順序合成一份 PDF（A4 或依圖片大小），或每張存成 JPG、PNG
- **高清**：用 AI（Real-ESRGAN）讓模糊的圖變清楚，預設大小不變，也可以放大 2～4 倍；模型有照片（用「質感」在銳利和自然之間調整）、插畫動漫、快速，以及不用下載的一般放大；「強度」可以調整 AI 處理的程度；可以先預覽，拖曳分隔線比較前後，在本地用顯示卡運算，不會上傳
- **去背**：用 AI（BiRefNet）找出照片裡的人物、動物、商品，背景換成透明、白色、其他顏色或模糊的原背景（像手機的人像模式）；方式有「一般」（快）、「精細」（頭髮、毛邊和顏色接近背景的主體更準），以及不用 AI、不用下載的「單色背景」（白底插畫、證件照，動畫也能用）。AI 沒抓好的地方用「保留」「移除」筆刷在預覽上補回或擦掉，也可以讓邊緣往內縮、變柔和，或裁到主體；去背後再旋轉、裁切也會跟著走，色彩與效果只套在主體上。背景透明時，原格式的 JPG 會自動存成 PNG。在本地運算，有顯示卡時用顯示卡加速（NVIDIA、AMD、Intel 都可以），不會上傳
- **批次改檔名**：用 `{名稱}`、`{序號}`、`{日期}`（拍攝日期）組合出新檔名，改名前先預覽，重複或不能用的檔名會標出來；預設另外輸出一份，也可以直接改原檔（改錯可以復原）
- **儲存**：另存到 `output\photo\`，不會覆蓋原圖；可以保留原格式或存成 JPG、PNG、WebP、GIF，動畫 GIF 每一格都會套用；也可以把清單裡的圖依順序合成一個 GIF 動畫（可選每格時間）

### 圖片轉檔
- **格式轉換**：JPG、PNG、WebP、AVIF、HEIC、GIF、SVG、ICO、BMP、TIFF 互相轉換，另可合成 PDF（拖曳清單調整順序）
- **壓縮**：預設保持原圖品質，也可自行調整品質、限制尺寸、PNG 減少顏色
- **簡單編輯**：轉檔前可以順便裁切、旋轉、翻轉、四點校正；更多編輯用「圖片工具」
- **動畫 GIF**：保留動畫、播放預覽，或逐格拆成多張圖片；也可以把清單裡的圖片依順序合成一個 GIF 動畫（拖曳調整順序、可選每格時間，本身是動畫的圖會保留每一格）
- **背景變透明**：單一顏色的背景（白底插畫、證件照、繪圖軟體匯出的白底圖）變透明，只去掉和圖片邊緣相連的背景，主體裡同色的地方（例如眼睛的白色）會保留；動畫每一格都會處理，原格式的 JPG 會改存 PNG
- **隱私**：可移除拍攝資訊（GPS 位置、拍攝時間等），並依拍攝方向自動轉正

### 影音轉檔
- **影片**：MP4、MOV、MKV、WebM、AVI、WMV、FLV、MPEG-1、MPEG-2、M2TS、OGV、3GP、SWF、DVD（NTSC、PAL）、GIF
- **壓縮**：選「檔案最小」「平衡」「接近原畫質」，或直接指定目標大小（例如 25 MB 以下）
- **進階設定**：編碼器（H.264、H.265、AV1、VP9 等）、解析度、畫面速率、位元速率、編碼速度、時間範圍、聲音
- **顯示卡加速**：自動偵測可用的顯示卡；轉檔前會預估輸出大小
- **影片轉 GIF**：可調整每秒格數、寬度、抖色方式；GIF、WebP 動畫也能轉成影片
- **音訊**：MP3、M4A、WAV、FLAC、OGG、Opus、WMA、AIFF、ALAC、AC3，影片可以直接取出聲音

### 錄音轉逐字稿
- 把錄音或影片轉成字幕檔（SRT）與純文字（TXT）
- 三種辨識模型可選，可輸出台灣繁體或簡體
- 可區分說話者，自動判斷人數或指定人數
- 修正錯字：自訂「辨識錯的字 → 正確的字」，之後的逐字稿會自動套用；可以存好幾個設定檔（例如一門課一個），存在 `setting\transcript_corrections` 資料夾，一個設定檔一個檔，可以直接複製給別人或放入別人給的檔案

### 即時字幕
- 把電腦正在播放的聲音（YouTube、B站、遊戲、Discord）、單一程式或麥克風，即時轉成字幕並翻譯
- 字幕浮在所有視窗最上層、半透明，滑鼠點得到後面的東西，也不會把遊戲切到背景；位置、字的大小、底色深淺都可以調
- 邊講邊出字，講完再換成完整的翻譯；說完到翻譯出來約 0.4～1 秒（RTX 5060 Ti 實測）
- 可以只顯示翻譯、原文和翻譯一起，或只顯示原文（一起顯示時原文大小可以另外調）；原文語言可以自動判斷或直接指定
- 專有名詞表：人名、招式名等固定的翻法，可以存好幾個設定檔（例如一部動畫一個），字幕進行中也能切換；設定檔存在 exe 旁邊的 `setting\subtitle_glossary` 資料夾，一個設定檔一個檔，可以直接複製給別人或放入別人給的檔案
- 字幕進行中也能改設定：換辨識模型、聲音來源時會短暫中斷，這段時間的聲音載入好後補上字幕；字幕紀錄會記下改了什麼
- 一陣子沒人說話時字幕會淡出（可以設定幾秒，或一直顯示），畫面上可以留 1～3 句
- 日文標讀音：原文是日文時，可以在漢字上方用小字標出讀音（振假名）；讀音由字典判斷，少數要看上下文的詞可能標錯
- 判斷誰說話：不同人用不同顏色（字幕視窗和字幕紀錄都會標），預設「自動分群」會自己判斷有幾個人，最近 10 秒內的顏色可能會修正；
  在字幕紀錄點一句可以複製或改那個人的顏色，TXT 紀錄會標「(藍色)」。背景有音樂或遊戲聲、多人同時講話時容易分錯；
  也可以改用門檻比對，自己調門檻（設定檔存在 `setting\subtitle_speakers`）。需要下載約 51 MB 的元件
- 收音靈敏度：電腦音量開得很小時自動放大，也可以固定放大倍數
- 字幕紀錄自動存在 `output\subtitles`，可以選存成 SRT、TXT、兩者，或不存檔
- 辨識模型四種（快速、輕量、推薦、最準確），翻譯用本地的 [Ollama](https://ollama.com)；程式會依電腦配備先選好建議的組合，所有選項都可以自己換
- 翻譯模型在「翻譯模型」視窗裡管理：每個模型都寫出說明、大小和需要的顯示卡記憶體，可以下載、選用、刪除；有給高階電腦用的大模型（需要約 20 GB 以上的顯示卡記憶體），配備不夠時下載前會先提醒
- 全部在本地運算，聲音不會上傳；字幕只能從 Naiz Studio 停止，關掉 Naiz Studio 字幕也會一起關
- 「獨佔全螢幕」的遊戲蓋不上任何視窗，請在遊戲設定改成「無邊框視窗」

### 擴充模組
- **安裝**：把下載的模組 zip 拖進首頁，確認來源與作者後就會安裝並啟用；再拖一次新版就是更新
- **自己放**：也可以把模組資料夾放進 exe 旁邊的 `mods` 資料夾（例如 `mods\circuit`），回到程式時首頁就會出現這個模組，點一下確認後才會啟用
- **移除**：在首頁的模組卡片上按右鍵選「移除模組」，資料夾會丟進資源回收筒；模組存的資料留著，重新安裝還在
- **注意安全**：模組是程式碼，會在本地直接執行，只安裝信得過的模組
- 模組需要比較新的主程式時，首頁會顯示需要的版本；模組出錯時不會讓整個程式關掉，會回到首頁，錯誤內容寫進 `error.log`

## 下載與執行

### 一般使用
1. 到 [Releases](https://github.com/NaizOuO/Naiz-Studio/releases) 下載最新版本的 zip
2. 解壓縮後執行 `Naiz Studio.exe`

注意事項：
- `setting` 資料夾要和 exe 放在一起，裡面有程式圖示與介面圖片，之後的設定也會存在這裡
- 第一次開啟時 Windows 可能出現「Windows 已保護您的電腦」，按「其他資訊」→「仍要執行」即可
- 第一次開啟時會詢問要不要在桌面建立捷徑，只會問一次；更新後捷徑照樣能用，之後也可以在設定裡重新建立

### 設定（首頁右上角的齒輪）
- **外觀**：首頁背景圖片（把圖片放進「開啟圖片資料夾」打開的資料夾就能選）、填充方式、透明度
- **檔案與空間**：輸出位置可以改到「文件\Naiz Studio」；「元件與空間」列出下載過的元件和各佔多少空間，用不到的可以刪掉，之後又用到時會再詢問下載
- **更新與關於**：開啟時要不要檢查新版本、立即檢查、建立桌面捷徑，以及說明文件、GitHub、設定資料夾
- 首頁右上角的「輸出資料夾」直接打開目前的輸出位置

### 更新
- 開啟程式時會檢查 GitHub 上有沒有新版本(只讀取公開的版本資訊，不會送出任何資料)，有的話會通知；設定裡可以關掉，或按「立即檢查」
- 通知可以選「更新」「取消」(下次開啟還會通知)或「不再顯示」；不管選哪個，首頁標題旁都會顯示「有新版本」，想更新時點它就好
- 按「更新」會下載新版並自動換好，重新開啟程式就是新版；設定、下載的元件、擴充模組與輸出都不會動到
- 通常只會下載新舊版之間的差異(補丁，約 1～2 MB)，落後好幾版就依序套用；補丁對不上或落後太多版時，會自動改下載完整版

### 從原始碼執行
```bash
pip install -r requirements.txt
python naiz_studio.py
```
也可以直接雙擊 `Naiz_Studio.bat`。開發環境為 Windows 11、Python 3.14。

## 系統需求

- 目前只在 **Windows 11 64 位元** 上測試過
- 螢幕至少能顯示 960 × 640 的視窗
- **PDF 工具、PDF 編輯器、圖片工具、圖片轉檔**：一般電腦即可，不需要額外下載任何東西（PDF 編輯器的開源字型包可以自己選擇要不要下載）
- **圖片工具的高清、AI 去背**：第一次使用時下載 AI 元件。去背實測 RTX 5060 Ti 一張約 0.2～0.4 秒；沒有顯示卡時用處理器，一張約 6～12 秒
- **文件轉檔**：有裝 Office 就直接可用；沒有的話需要下載 LibreOffice。掃描檔文字辨識需要下載 Tesseract，一頁約 3 秒
- **影音轉檔**：需要下載 FFmpeg。NVIDIA 顯示卡實測可以加速（RTX 5060 Ti 轉 30 秒 1080p 影片約 2 秒）；AMD、Intel 顯示卡程式會自動偵測，但還沒有實測
- **錄音轉逐字稿**：需要下載語音辨識元件，速度取決於顯示卡
- **即時字幕**：需要下載語音辨識元件；翻譯需要另外安裝免費的 Ollama。依電腦配備的建議：

| 電腦 | 辨識模型 | 翻譯模型 | 實測 |
|---|---|---|---|
| 沒有獨立顯示卡 | 輕量 | translategemma:4b | 辨識每次約 1 秒、翻譯每句約 2～3 秒（只翻講完的句子） |
| NVIDIA 顯示卡 6～8 GB | 推薦 | translategemma:4b | 辨識每次約 0.1 秒、翻譯每句約 0.3 秒 |
| NVIDIA 顯示卡 12 GB 以上 | 推薦 | qwen3:8b（預設） | 辨識每次約 0.1 秒、翻譯每句約 0.2～0.4 秒 |

玩遊戲時字幕會和遊戲搶顯示卡，卡頓的話換小一點的模型。

| 硬體 | 使用的版本 | 實測速度（11 分鐘中文錄音，「推薦」模型） |
|---|---|---|
| NVIDIA 顯示卡 | 顯示卡加速版 | RTX 5060 Ti 16GB 約 12 秒（約 55 倍速） |
| 其他顯示卡或沒有顯示卡 | CPU 版 | 約 259 秒（約 2.6 倍速，依 CPU 而定） |

補充說明：
- 程式會自動判斷有沒有 NVIDIA 顯示卡，選擇適合的版本
- 「最準確」模型需要約 3.3 GB 顯示記憶體；RTX 5060 Ti 跑 11 分鐘錄音約 44 秒
- RTX 50 系列顯示卡第一次使用顯示卡加速版時，需要額外約 30 秒準備，之後就不用
- 網路只有在第一次下載元件時需要

## 需要時才下載的元件

用到的功能缺少元件時，程式會先說明用途與大小，經過同意才下載，下載後會以 SHA-256 驗證檔案是否完整。元件放在 exe 旁邊的 `bin`、`models` 資料夾，字型包放在 `setting\fonts`。

| 元件 | 用途 | 下載大小 | 安裝後大小 |
|---|---|---|---|
| FFmpeg | 影音轉檔，以及讀取錄音與影片中的聲音 | 約 106 MB | 約 196 MB |
| LibreOffice | 文件轉檔（電腦上沒有 Office，或指定要用它時） | 約 357 MB | 約 1.6 GB |
| Tesseract 文字辨識 | 掃描檔辨識成文字（含繁體中文、英文語言檔） | 約 78 MB | 約 144 MB |
| Whisper 語音辨識（CPU 版） | 把語音轉成文字 | 約 8 MB | 約 21 MB |
| Whisper 語音辨識（NVIDIA 顯示卡版） | 用顯示卡加速辨識 | 約 643 MB | 約 1.1 GB |
| 人聲偵測模型 | 跳過沒有人說話的片段 | 約 1 MB | 約 1 MB |
| 辨識模型「快速」 | 檔案最小、速度最快，錯字較多 | 約 141 MB | 約 141 MB |
| 辨識模型「推薦」 | 準確又快 | 約 547 MB | 約 547 MB |
| 辨識模型「最準確」 | 錯字最少，速度較慢 | 約 2.9 GB | 約 2.9 GB |
| 說話者分離 | 區分說話者時使用 | 約 23 MB | 約 21 MB |
| 聲音特徵模型 | 判斷是不是同一個人（逐字稿區分說話者、即時字幕判斷誰說話） | 約 28 MB | 約 28 MB |
| Real-ESRGAN 圖片高清 | 圖片工具的 AI 放大（需要支援 Vulkan 的顯示卡） | 約 43 MB | 約 51 MB |
| 辨識模型「輕量」 | 即時字幕：沒有獨立顯示卡也能即時辨識 | 約 252 MB | 約 252 MB |
| 人聲偵測模型（字幕） | 即時字幕：判斷有沒有人在說話，避免冒出不存在的字幕（和 AI 去背共用執行元件） | 約 2 MB | 約 2 MB |
| 照片高清模型（自然） | 高清的「照片」模型，「質感」往自然拉時用到，保留紋理 | 約 31 MB | 約 33 MB |
| AI 去背執行元件（ONNX Runtime） | 在本地執行去背 AI，有顯示卡時用顯示卡加速 | 約 26 MB | 約 67 MB |
| 去背模型（一般） | 去背的「一般」，速度快 | 約 109 MB | 約 109 MB |
| 去背模型（精細） | 去背的「精細」，頭髮、毛邊更準 | 約 467 MB | 約 467 MB |
| 日文讀音字典（IPADIC） | 即時字幕：日文漢字上方標讀音（振假名） | 約 13 MB | 約 51 MB |

辨識模型只需要下載用到的那一個。

### PDF 編輯器的開源字型包

文字框可以使用的免費字型，每一套分開下載，在字型選單選到時才下載。Windows 內建的微軟正黑體、標楷體等可以直接使用，不一定要下載。

| 字型 | 適合 | 下載大小 |
|---|---|---|
| Noto Sans TC 黑體 | 繁體中文，簡報、螢幕閱讀 | 約 11.4 MB |
| Noto Serif TC 明體 | 繁體中文，文書、報告 | 約 16.1 MB |
| 霞鶩文楷 TC（一般、粗體） | 繁體中文楷體，風格接近標楷體 | 各約 14.5 MB |
| Noto Sans SC 黑体、Noto Serif SC 宋体 | 簡體中文 | 約 16.9 MB、24.0 MB |
| Carlito（一般、粗體、斜體、粗斜體） | 英文，字寬和 Calibri 一樣 | 每種約 0.6～0.8 MB |
| Liberation Sans、Liberation Serif（各 4 種樣式） | 英文，字寬和 Arial、Times New Roman 一樣 | 全部一起下載約 2.3 MB |
| Noto Sans Mono | 英文等寬，程式碼、數據 | 約 1.6 MB |

## 給其他程式呼叫（命令列）

可以不開視窗，直接請 Naiz Studio 處理檔案。

```bash
python naiz_cli.py images-to-pdf --output 報告.pdf 照片1.jpg 照片2.jpg
```

打包成 exe 後則是 `"Naiz Studio.exe" --cli images-to-pdf …`；exe 沒有主控台，要加 `--result 結果.json`
把結果寫成檔案。

- `--page-size keep|a4|first`：每頁維持原大小、統一成 A4、統一成第一頁的大小（等比縮放置中，直向橫向各自對齊）
- `--quality none|light|standard|strong`：壓縮等級
- `--list 清單.txt`：檔案很多時改用清單檔，每行一個路徑
- 結果是一行 JSON：`{"ok": true, "output": "…", "pages": 3, "bytes": 812345}`；失敗時 `ok` 為 `false`，
  `error` 是可以直接顯示給使用者看的中文訊息，離開碼為 1

## 自己寫擴充模組

在 `mods` 裡建一個資料夾（名稱用英文，例如 `mods\hello`），裡面放 `__init__.py`：

```python
from core.plugins import Page, Tool
from core.widgets import draw_text


class HelloPage(Page):
    def draw(self, rect, mouse_pos):
        draw_text(self.screen, "你好", rect.center, 24, center=True)

    def handle_event(self, event, mouse_pos):
        pass


class HelloTool(Tool):
    id = "hello"                # 不能和其他工具重複
    name = "打招呼"
    description = "首頁卡片上的說明"
    accent = (120, 200, 255)    # 主題色
    version = "0.1.0"           # 模組版本,顯示在首頁卡片
    author = "你的名字"
    min_app = "1.15.0"          # 需要的主程式版本

    def create_page(self, app):
        return HelloPage(app, self)


TOOLS = [HelloTool]
```

- 一個資料夾可以有好幾個 .py 檔，彼此用 `from . import xxx` 引用
- 分享時把模組資料夾壓成 zip(例如 `hello-v0.1.0.zip`，裡面是 `hello\__init__.py`)，別人拖進首頁就能安裝

**可以用的東西**

| 功能 | 用法 |
|---|---|
| 畫面元件 | `core.widgets`(按鈕、輸入框、滑桿、選項列)、`core.scroll`(可捲動清單)、`core.contextmenu`(右鍵選單)、`core.dialog`(確認視窗) |
| 檔案 | `core.winfile`(開啟、另存新檔視窗)、`core.files`(不覆蓋舊檔的命名、寫到一半不會留下壞檔) |
| 存自己的資料 | `self.tool.data_dir()`：`mods_data\工具代號` 資料夾，移除模組也不會刪掉 |
| 下載大型元件 | 在工具填 `requires`(`core.deps.Dependency`)，開啟前會先說明並詢問使用者 |
| 網路 | 直接用 Python 內建的 `urllib`，HTTPS 可以用 |
| 套件 | 主程式已經帶著 Pillow、numpy、pygame、pikepdf、pypdfium2 與常用的 Python 內建模組(含 sqlite3、csv、json、xml、zipfile 等)；其他純 Python 套件可以放在模組的 `lib` 資料夾，會自動加入搜尋路徑 |

**注意**

- 耗時的工作(下載、轉檔、大量計算)要放到背景執行緒(`threading`)，在 `update()` 裡檢查進度；直接在 `draw()`、`handle_event()` 裡做會讓整個視窗卡住
- `lib` 裡的套件名稱不要和主程式已有的套件重複(主程式的版本優先)；需要編譯的套件要用 Python 3.14、Windows 64 位元版
- 工具出錯時主程式會關掉這個畫面並回到首頁，錯誤寫進 `error.log`，可以從那裡找原因

## 檔案位置

以下都在 exe（或原始碼版的 `naiz_studio.py`）所在的資料夾；設定裡打開「輸出到文件」時，`output` 底下的結果改存在「文件\Naiz Studio」：

| 位置 | 內容 |
|---|---|
| `output\pdf\` | PDF 工具的結果 |
| `output\editor\` | PDF 編輯器儲存、擷取、修復的檔案 |
| `output\images\` | 圖片轉檔的結果 |
| `output\photo\` | 圖片工具儲存的圖片、另外輸出的改名檔案 |
| `output\documents\` | 文件轉檔的結果 |
| `output\media\` | 影音轉檔的結果 |
| `output\transcripts\` | 逐字稿 |
| `bin\`、`models\` | 下載的元件 |
| `mods\` | 擴充模組，一個模組一個資料夾 |
| `mods_data\` | 擴充模組自己存的設定與資料 |
| `setting\config.json` | 設定（背景、各功能的選項等） |
| `setting\images\` | 介面圖片；也可以放自己的圖片當作背景 |
| `setting\fonts\downloads\` | 下載的開源字型包 |
| `setting\fonts\custom\` | 自己加入的字型（也可以直接把字型檔放進來） |
| `setting\signatures\` | 存起來的簽名（只存在本地，不會上傳） |
| `setting\transcript_corrections\` | 逐字稿修正錯字的設定檔，一個設定檔一個 `.json` |
| `setting\subtitle_glossary\` | 即時字幕專有名詞的設定檔，一個設定檔一個 `.json` |
| `setting\subtitle_speakers\` | 即時字幕判斷誰說話的區分方式設定檔，一個設定檔一個 `.json` |
| `error.log` | 發生錯誤時的紀錄 |

輸出時不會覆蓋原檔，同名時會自動加上編號。

## 授權

- **原始碼**：以 [MIT 授權](LICENSE) 釋出。可以自由使用、修改、再散佈（包含商業用途），只需要保留版權聲明與授權文字。
- **Releases 提供的 exe**：內含 GPL 授權的元件（pillow-heif 附帶的 x265），因此 exe 整體依 [GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html) 散佈，對應的原始碼就是本專案。
- **需要時才下載的元件**（FFmpeg、Whisper 等）：是獨立的程式與模型，各自依照原本的授權使用，詳見下方清單。

## 使用的開源專案

| 專案 | 用途 | 授權 |
|---|---|---|
| [pygame-ce](https://github.com/pygame-community/pygame-ce) | 介面 | LGPL-2.1 |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)（[PDFium](https://pdfium.googlesource.com/pdfium/)） | PDF 轉圖片、頁面縮圖 | Apache-2.0 / BSD-3-Clause |
| [pikepdf](https://github.com/pikepdf/pikepdf) | PDF 壓縮、拆分、合併，圖片合成 PDF，儲存註解 | MPL-2.0 |
| [fontTools](https://github.com/fonttools/fonttools) | 讀取字型、嵌入字型子集 | MIT |
| [python-docx](https://github.com/python-openxml/python-docx) | 產生 Word 檔（PDF 轉 Word） | MIT |
| [resvg](https://github.com/linebender/resvg)（[resvg-py](https://github.com/baseplate-admin/resvg-py)） | 讀取 SVG | Apache-2.0 / MIT |
| [Pillow](https://github.com/python-pillow/Pillow) | 圖片處理 | MIT-CMU |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) | HEIC 讀寫 | BSD-3-Clause（附帶 libheif、libde265 為 LGPL-3.0，x265 為 GPL-2.0 以上） |
| [vtracer](https://github.com/visioncortex/vtracer) | 圖片轉 SVG | MIT |
| [OpenCC](https://github.com/yichen0831/opencc-python) | 繁簡轉換 | Apache-2.0 |
| [FFmpeg](https://ffmpeg.org/)（[gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 版本） | 影音轉檔、讀取影音 | GPL-3.0 |
| [LibreOffice](https://www.libreoffice.org/) | 文件轉檔 | MPL-2.0 |
| [Tesseract](https://github.com/tesseract-ocr/tesseract)（[UB Mannheim](https://github.com/UB-Mannheim/tesseract) Windows 版本） | 掃描檔文字辨識 | Apache-2.0 |
| [tessdata_best](https://github.com/tesseract-ocr/tessdata_best) | 文字辨識的語言資料 | Apache-2.0 |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | 語音辨識 | MIT |
| [Whisper 模型](https://github.com/openai/whisper) | 語音辨識模型 | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) | 人聲偵測模型 | MIT |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 說話者分離、即時字幕判斷誰說話 | Apache-2.0 |
| [3D-Speaker CAM++](https://github.com/modelscope/3D-Speaker) | 聲音特徵模型 | Apache-2.0 |
| [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)（[ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan) 版） | 圖片高清（AI 放大）與模型 | BSD-3-Clause / MIT |
| [4xNomosWebPhoto_esrgan](https://github.com/Phhofm/models/releases/tag/4xNomosWebPhoto_esrgan)（Philip Hofmann） | 高清的「照片（自然）」模型（轉成 ncnn 格式，放在本專案的[下載元件](https://github.com/NaizOuO/Naiz-Studio/releases/tag/components) Release） | CC BY 4.0 |
| [BiRefNet](https://github.com/ZhengPeng7/BiRefNet)（[ONNX 版](https://huggingface.co/onnx-community/BiRefNet-ONNX)） | 去背模型（一般、精細） | MIT |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime)（DirectML 版） | 執行去背 AI | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) | 即時字幕的人聲偵測 | MIT |
| [comtypes](https://github.com/enthought/comtypes) | 即時字幕擷取電腦播放的聲音 | MIT |
| [fugashi](https://github.com/polm/fugashi)（[MeCab](https://taku910.github.io/mecab/)） | 即時字幕的日文斷詞（振假名） | MIT / BSD-3-Clause |
| [IPADIC](https://pypi.org/project/ipadic/)（奈良先端科學技術大學院大學） | 即時字幕振假名用的日文讀音字典 | IPADIC 授權（允許自由使用與散布，須保留版權聲明） |
| [Ollama](https://github.com/ollama/ollama)（使用者自行安裝） | 即時字幕的本地翻譯；翻譯模型由 Ollama 下載，各自依原本的授權（Qwen3 為 Apache-2.0，TranslateGemma 為 Gemma 使用條款） | MIT |
| [Noto 字型](https://github.com/notofonts/noto-cjk)（[Google Fonts](https://github.com/google/fonts)） | 字型包：Noto Sans / Serif TC、SC，Noto Sans Mono | OFL-1.1 |
| [霞鶩文楷 TC](https://github.com/lxgw/LxgwWenkaiTC) | 字型包：楷體 | OFL-1.1 |
| [Carlito](https://github.com/googlefonts/carlito) | 字型包：英文（Calibri 字寬） | OFL-1.1 |
| [Liberation Fonts](https://github.com/liberationfonts/liberation-fonts) | 字型包：英文（Arial、Times New Roman 字寬） | OFL-1.1 |
