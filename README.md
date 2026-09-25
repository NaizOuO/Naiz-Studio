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
- **標記與註解**：螢光筆、底線、刪除線（在文字上拖曳，自動對齊文字行）、文字框（直接在頁面上打字，支援注音，可以加背景色）、便利貼、直線、箭頭、方框、圓形（線條與填滿分開設定）、手繪；工具列平常只顯示圖示，滑過時顯示名稱。顏色選單依色相排列，選好後可以接著換；右鍵點顏色或按「自訂色彩」可以自由調色（色盤、色碼或 RGB），最近使用的顏色會記住；另可調整粗細、透明度、字型與字級
- **改字**：用「選取」直接點文字就能修改整段，打字時自動重新換行，保留原本的對齊方式、縮排與行距；拖曳選字可以複製，或只改其中幾個字。清空就是刪除
- **沿用原字型**：盡量使用 PDF 裡內嵌的原字型（包含教科書常見的舊式 Type1 字型與數學符號），新字和原字長得一樣；原字型裡沒有的字，改用相近的字型補上（有襯線的字型用 Times 系列、無襯線用 Arial 系列）；文字框與改字可以設定粗體、斜體。儲存時原字會真正刪掉（不只是蓋住），別人選取、搜尋也找不到
- **塗黑個資**：在文字上拖曳，或在照片、簽名上拉出範圍；儲存時底下的字真正刪掉，圖片被蓋住的部分直接塗掉，別人移不開、也取不出原本的內容
- **插入圖片**：選一張圖片，點一下放到頁面上或拖曳出大小，改大小時維持比例
- **移動原本的圖片**：PDF 裡原本就有的圖片可以移動、縮放、刪除，儲存時只改位置，畫質不變
- **簽名**：用滑鼠或觸控筆手寫，或拍下紙上的簽名匯入（自動去掉背景）；簽名存在本地，下次直接點選使用
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
- **格式轉換**：JPG、PNG、WebP、AVIF、HEIC、GIF、SVG、ICO、BMP、TIFF 互相轉換，另可合成 PDF（拖曳清單調整順序）
- **壓縮**：預設保持原圖品質，也可自行調整品質、限制尺寸、PNG 減少顏色
- **編輯**：裁切、擴展畫布、旋轉、翻轉，每張圖可分開設定，也可套用到全部
- **動畫 GIF**：保留動畫、播放預覽，或逐格拆成多張圖片
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
- 修正錯字：自訂「辨識錯的字 → 正確的字」，之後的逐字稿會自動套用

## 下載與執行

### 一般使用
1. 到 [Releases](https://github.com/NaizOuO/Naiz-Studio/releases) 下載最新版本的 zip
2. 解壓縮後執行 `Naiz Studio.exe`

注意事項：
- `images` 資料夾要和 exe 放在一起，裡面是程式圖示與介面圖片
- 第一次開啟時 Windows 可能出現「Windows 已保護您的電腦」，按「其他資訊」→「仍要執行」即可

### 從原始碼執行
```bash
pip install -r requirements.txt
python naiz_studio.py
```
也可以直接雙擊 `Naiz_Studio.bat`。開發環境為 Windows 11、Python 3.14。

## 系統需求

- 目前只在 **Windows 11 64 位元** 上測試過
- 螢幕至少能顯示 960 × 640 的視窗
- **PDF 工具、PDF 編輯器、圖片工具**：一般電腦即可，不需要額外下載任何東西（PDF 編輯器的開源字型包可以自己選擇要不要下載）
- **文件轉檔**：有裝 Office 就直接可用；沒有的話需要下載 LibreOffice。掃描檔文字辨識需要下載 Tesseract，一頁約 3 秒
- **影音轉檔**：需要下載 FFmpeg。NVIDIA 顯示卡實測可以加速（RTX 5060 Ti 轉 30 秒 1080p 影片約 2 秒）；AMD、Intel 顯示卡程式會自動偵測，但還沒有實測
- **錄音轉逐字稿**：需要下載語音辨識元件，速度取決於顯示卡

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

用到的功能缺少元件時，程式會先說明用途與大小，經過同意才下載，下載後會以 SHA-256 驗證檔案是否完整。元件放在 exe 旁邊的 `bin`、`models`、`fonts` 資料夾。

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
| 聲音特徵模型 | 判斷是不是同一個人 | 約 28 MB | 約 28 MB |

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

## 檔案位置

以下都在 exe（或原始碼版的 `naiz_studio.py`）所在的資料夾：

| 位置 | 內容 |
|---|---|
| `output\pdf\` | PDF 工具的結果 |
| `output\editor\` | PDF 編輯器儲存、擷取、修復的檔案 |
| `output\images\` | 圖片工具的結果 |
| `output\documents\` | 文件轉檔的結果 |
| `output\media\` | 影音轉檔的結果 |
| `output\transcripts\` | 逐字稿 |
| `bin\`、`models\` | 下載的元件 |
| `fonts\downloads\` | 下載的開源字型包 |
| `fonts\custom\` | 自己加入的字型（也可以直接把字型檔放進來） |
| `signatures\` | 存起來的簽名（只存在本地，不會上傳） |
| `images\` | 介面圖片；也可以放自己的圖片當作背景 |
| `config.json` | 設定（背景、修正錯字規則等） |
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
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 說話者分離 | Apache-2.0 |
| [3D-Speaker CAM++](https://github.com/modelscope/3D-Speaker) | 聲音特徵模型 | Apache-2.0 |
| [Noto 字型](https://github.com/notofonts/noto-cjk)（[Google Fonts](https://github.com/google/fonts)） | 字型包：Noto Sans / Serif TC、SC，Noto Sans Mono | OFL-1.1 |
| [霞鶩文楷 TC](https://github.com/lxgw/LxgwWenkaiTC) | 字型包：楷體 | OFL-1.1 |
| [Carlito](https://github.com/googlefonts/carlito) | 字型包：英文（Calibri 字寬） | OFL-1.1 |
| [Liberation Fonts](https://github.com/liberationfonts/liberation-fonts) | 字型包：英文（Arial、Times New Roman 字寬） | OFL-1.1 |
