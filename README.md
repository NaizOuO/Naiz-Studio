# Naiz Studio

在自己的電腦上處理 PDF、圖片、影音與錄音的工具包。所有檔案都在本地處理，不會上傳。

## 功能

### PDF 工具
- **壓縮**：無損、JPEG、JPEG2000、灰階、黑白（CCITT G4）五種方式，可調整影像品質與解析度上限
- **拆分**：依比例拆分，或勾選、輸入頁碼取出頁面，可輸出 PDF、PNG、JPG
- **合併**：多個 PDF 依順序合併，可選擇合併後一併壓縮

### 圖片工具
- **格式轉換**：JPG、PNG、WebP、AVIF、HEIC、GIF、SVG、ICO、BMP、TIFF 互相轉換，另可合成 PDF
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
- **PDF 工具、圖片工具**：一般電腦即可，不需要額外下載任何東西
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

用到的功能缺少元件時，程式會先說明用途與大小，經過同意才下載，下載後會以 SHA-256 驗證檔案是否完整。元件放在 exe 旁邊的 `bin`、`models` 資料夾。

| 元件 | 用途 | 下載大小 | 安裝後大小 |
|---|---|---|---|
| FFmpeg | 影音轉檔，以及讀取錄音與影片中的聲音 | 約 106 MB | 約 196 MB |
| Whisper 語音辨識（CPU 版） | 把語音轉成文字 | 約 8 MB | 約 21 MB |
| Whisper 語音辨識（NVIDIA 顯示卡版） | 用顯示卡加速辨識 | 約 643 MB | 約 1.1 GB |
| 人聲偵測模型 | 跳過沒有人說話的片段 | 約 1 MB | 約 1 MB |
| 辨識模型「快速」 | 檔案最小、速度最快，錯字較多 | 約 141 MB | 約 141 MB |
| 辨識模型「推薦」 | 準確又快 | 約 547 MB | 約 547 MB |
| 辨識模型「最準確」 | 錯字最少，速度較慢 | 約 2.9 GB | 約 2.9 GB |
| 說話者分離 | 區分說話者時使用 | 約 23 MB | 約 21 MB |
| 聲音特徵模型 | 判斷是不是同一個人 | 約 28 MB | 約 28 MB |

辨識模型只需要下載用到的那一個。

## 檔案位置

以下都在 exe（或原始碼版的 `naiz_studio.py`）所在的資料夾：

| 位置 | 內容 |
|---|---|
| `output\` | PDF 工具的結果 |
| `output\images\` | 圖片工具的結果 |
| `output\media\` | 影音轉檔的結果 |
| `output\transcripts\` | 逐字稿 |
| `bin\`、`models\` | 下載的元件 |
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
| [pikepdf](https://github.com/pikepdf/pikepdf) | PDF 壓縮、拆分、合併，圖片合成 PDF | MPL-2.0 |
| [resvg](https://github.com/linebender/resvg)（[resvg-py](https://github.com/baseplate-admin/resvg-py)） | 讀取 SVG | Apache-2.0 / MIT |
| [Pillow](https://github.com/python-pillow/Pillow) | 圖片處理 | MIT-CMU |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) | HEIC 讀寫 | BSD-3-Clause（附帶 libheif、libde265 為 LGPL-3.0，x265 為 GPL-2.0 以上） |
| [vtracer](https://github.com/visioncortex/vtracer) | 圖片轉 SVG | MIT |
| [OpenCC](https://github.com/yichen0831/opencc-python) | 繁簡轉換 | Apache-2.0 |
| [FFmpeg](https://ffmpeg.org/)（[gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 版本） | 影音轉檔、讀取影音 | GPL-3.0 |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | 語音辨識 | MIT |
| [Whisper 模型](https://github.com/openai/whisper) | 語音辨識模型 | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) | 人聲偵測模型 | MIT |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 說話者分離 | Apache-2.0 |
| [3D-Speaker CAM++](https://github.com/modelscope/3D-Speaker) | 聲音特徵模型 | Apache-2.0 |
