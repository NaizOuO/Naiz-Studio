"""OBS 瀏覽器來源(v1.18.6,直播用):Naiz Studio 在本地開一個網址(只有本機連得到),
在 OBS 新增「瀏覽器來源」貼上網址,字幕就會出現在直播畫面上,背景透明。

- 只聽 127.0.0.1(同一台電腦),不對外;連接埠固定(OBS 裡設一次就好),被別的程式佔用時往後找
- 網頁用「伺服器推送」(Server-Sent Events)收字幕:有新字馬上送,不用一直問
- 兩種版面:字幕樣式(最近幾句、會淡出)、紀錄樣式(一次顯示多行,新的在下面)
- 外觀:預設跟著字幕視窗的樣式(字型、大小、顏色、外框、底色),另外可以放大縮小;
  想要完全自己設計的話,把 .css 檔放進 setting\\subtitle_obs 選用(或貼在 OBS 瀏覽器來源的「自訂 CSS」)。
  網頁只用 class 和 CSS 變數排版(不寫死在每個元素上),自己的 CSS 都蓋得過去;可以用的名稱寫在 CSS_GUIDE
- 字型:沒有安裝在電腦上的字型(例如自己加入的)由這裡提供檔案;已安裝的直接用名稱
- 送出去的字已經過敏感詞過濾(由 page 處理)
"""

import http.server
import json
import mimetypes
import threading
from pathlib import Path

from core import paths

PORT = 47831                # 預設的連接埠;被佔用時試後面幾個
TRIES = 10
KEEPALIVE = 15              # 這麼久沒新字就送一次「還在」,OBS 才不會以為斷線
LOG_LINES = 20              # 紀錄樣式最多送幾行(實際顯示幾行看設定)
LAYOUTS = [("subtitle", "字幕樣式"), ("log", "紀錄樣式")]
LAYOUT_NOTES = {"subtitle": "最近幾句，講完一段時間會淡出（句數、淡出和字幕視窗一樣）",
                "log": "一次顯示多行，新的在下面，像聊天室；適合放在畫面側邊"}
SCALES = [("80", "80%"), ("100", "100%"), ("125", "125%"), ("150", "150%")]
LOG_COUNTS = [("6", "6 行"), ("12", "12 行"), ("20", "20 行")]
CSS_FOLDER = "subtitle_obs"
DEFAULT_CSS = ""            # 「預設外觀」(跟著字幕視窗)
CSS_GUIDE = """/* Naiz Studio 即時字幕:OBS 瀏覽器來源的自訂外觀
   把這個檔案複製一份、改名,在 Naiz Studio 的「直播」→「OBS 外觀」選它就會套用(存檔後馬上更新)。
   也可以把內容貼到 OBS 瀏覽器來源的「自訂 CSS」。

   可以用的名稱:
   #wrap               全部字幕的外框(.subtitle 字幕樣式 / .log 紀錄樣式)
   .block              一句(一個底色框);.partial 是還在講的句子;.speaker-0～7 是判斷誰說話的第幾個人
                       .speaker-me 是同時聽麥克風時自己說的話
   .row                一行字;.original 原文、.translation 翻譯;.main 大字、.sub 小字
   rt                  日文讀音(振假名)
   CSS 變數(預設跟著字幕視窗的設定):
   --size 大字的大小、--size-small 小字的大小、--color 字的顏色、--dim 還在講的句子的顏色、
   --bg 底色、--outline 外框(text-shadow)、--speaker 這個人的顏色、--speaker-bg 這個人那句的底色
   (顏色調暗、透明度照底色深淺)、--font 字型
*/

/* 例:換成圓角膠囊、字變粗、底色漸層 */
.block {
  border-radius: 999px;
  padding: 8px 28px;
  background: linear-gradient(90deg, rgba(20, 20, 40, 0.85), rgba(60, 20, 80, 0.85));
}
.row { font-weight: bold; }
.row.translation { color: #ffe08a; }
/* 例:新的句子從下面滑進來 */
.block { animation: rise 0.25s ease-out; }
@keyframes rise { from { transform: translateY(12px); opacity: 0; } to { transform: none; } }
"""


def css_folder():
    return Path(paths.SETTING_DIR) / CSS_FOLDER


def ensure_css_folder():
    """打開資料夾前:沒有範例的話放一份(說明可以用的名稱)。"""
    folder = css_folder()
    folder.mkdir(parents=True, exist_ok=True)
    example = folder / "範例.css"
    if not any(folder.glob("*.css")):
        example.write_text(CSS_GUIDE, encoding="utf-8")
    return folder


def css_files():
    """setting\\subtitle_obs 裡的 .css 檔名(不含副檔名)。"""
    folder = css_folder()
    return sorted(path.stem for path in folder.glob("*.css")) if folder.is_dir() else []


class _Server(http.server.ThreadingHTTPServer):
    # 不共用連接埠:Windows 上開了「重複使用位址」的話,兩個程式能同時佔同一個埠,OBS 會連到錯的那個
    allow_reuse_address = False
    daemon_threads = True


class WebSource:
    def __init__(self):
        self.httpd = None
        self.port = None
        self.error = ""
        self._cond = threading.Condition()
        self._version = 0
        self._state = {"style": {}, "lines": [], "log": [], "layout": "subtitle", "scale": 100, "count": 12,
                       "css": DEFAULT_CSS, "css_version": 0}
        self._stopped = False

    @property
    def alive(self):
        return self.httpd is not None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/" if self.port else ""

    def start(self, port=PORT):
        if self.httpd is not None:
            return True
        source = self

        class Handler(_Handler):
            owner = source

        for candidate in range(port, port + TRIES):
            try:
                self.httpd = _Server(("127.0.0.1", candidate), Handler)
                break
            except OSError:
                continue
        if self.httpd is None:
            self.error = "找不到可以用的連接埠"
            return False
        self.port = self.httpd.server_address[1]
        self.error = ""
        self._stopped = False
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return True

    def stop(self):
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def push(self, **changes):
        """更新要顯示的內容(style、lines、log、layout、scale、count、css),馬上推給所有開著的網頁。"""
        with self._cond:
            self._state.update(changes)
            self._version += 1
            self._cond.notify_all()

    def refresh_css(self):
        """自訂外觀的檔案改過了:叫網頁重新讀。"""
        with self._cond:
            self._state["css_version"] = self._state.get("css_version", 0) + 1
            self._version += 1
            self._cond.notify_all()

    def snapshot(self):
        with self._cond:
            return self._version, json.dumps(self._state, ensure_ascii=False)

    def wait(self, version, timeout):
        """等到內容變了(或停止、逾時);回傳 (版本, JSON) 或 None(已停止)。"""
        with self._cond:
            self._cond.wait_for(lambda: self._version != version or self._stopped, timeout)
            if self._stopped:
                return None
            return self._version, json.dumps(self._state, ensure_ascii=False)

    def font_file(self, which):
        style = self._state.get("style") or {}
        path = style.get("font_path" if which == "main" else "fallback_path") or ""
        return Path(path) if path and Path(path).is_file() else None

    def css_text(self):
        name = self._state.get("css") or ""
        if not name:
            return ""
        path = css_folder() / f"{name}.css"
        # 只讀資料夾裡的檔案(名稱不能跳到別的資料夾)
        if path.parent != css_folder() or not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return ""


class _Handler(http.server.BaseHTTPRequestHandler):
    owner = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        elif path == "/events":
            self._events()
        elif path == "/custom.css":
            self._send(200, "text/css; charset=utf-8", self.owner.css_text().encode("utf-8"))
        elif path in ("/font/main", "/font/fallback"):
            font = self.owner.font_file(path.rsplit("/", 1)[1])
            if font is None:
                self._send(404, "text/plain", b"")
            else:
                kind = mimetypes.guess_type(font.name)[0] or "font/ttf"
                self._send(200, kind, font.read_bytes())
        else:
            self._send(404, "text/plain", b"")

    def _send(self, code, kind, body):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        version, data = self.owner.snapshot()
        try:
            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                result = self.owner.wait(version, KEEPALIVE)
                if result is None:
                    return
                if result[0] == version:
                    self.wfile.write(b": ping\n\n")         # 沒有新字:告訴網頁連線還在
                else:
                    version, data = result
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except OSError:
            return                                          # OBS 關掉或重新整理了


PAGE = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>Naiz Studio 字幕</title>
<style id="fonts"></style>
<style>
  :root { --size: 34px; --size-small: 20px; --color: #fff; --dim: #d1d1d1; --bg: rgba(12, 14, 18, 0.6);
          --outline: none; --font: "Microsoft JhengHei", sans-serif; --align: center; --justify: center; }
  html, body { margin: 0; padding: 0; background: transparent; overflow: hidden; height: 100%; }
  #wrap { position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: flex-end;
          align-items: var(--justify); padding: 16px 24px; box-sizing: border-box; gap: 10px;
          font-family: var(--font); }
  .block { border-radius: 12px; padding: 10px 20px; max-width: 100%; box-sizing: border-box;
           background: var(--bg); text-align: var(--align); overflow-wrap: anywhere; }
  .block.speaker { background: var(--speaker-bg); }
  .row { line-height: 1.35; white-space: pre-wrap; color: var(--color); text-shadow: var(--outline); }
  .row.main { font-size: var(--size); }
  .row.sub { font-size: var(--size-small); color: var(--dim); }
  .block.partial .row.main { color: var(--dim); }
  rt { font-size: 0.5em; }
</style>
<style id="custom"></style>
</head>
<body><div id="wrap"></div>
<script>
let state = {style: {}, lines: [], log: [], layout: "subtitle"}, received = performance.now();
let fontKey = "", cssKey = "";
const FADE_TIME = 0.8;
const root = document.documentElement.style;
let bgAlpha = 0.6;
const SPEAKER_SHADE = 0.55;    // 說話者那句的底色:那個人的顏色調暗(和字幕視窗一樣),透明度照底色深淺
function paintSpeaker(block) {
  const c = block.speakerColor;
  const value = c ? rgb(c.map(v => Math.round(v * SPEAKER_SHADE)), bgAlpha) : "";
  if (block.style.getPropertyValue("--speaker-bg") !== value) block.style.setProperty("--speaker-bg", value);
}
function rgb(c, a) { c = c || [255, 255, 255]; return a === undefined ? `rgb(${c[0]},${c[1]},${c[2]})`
                                                                      : `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
function outline(width, color) {
  if (!width) return "none";
  const shadows = [];
  for (let k = 0; k < 16; k++) {
    const a = k * Math.PI / 8;
    shadows.push(`${(Math.cos(a) * width).toFixed(2)}px ${(Math.sin(a) * width).toFixed(2)}px 0 ${rgb(color)}`);
  }
  return shadows.join(",");
}
function applyStyle() {
  const style = state.style || {}, scale = (state.scale || 100) / 100;
  const key = (style.font_path || "") + "|" + (style.fallback_path || "");
  if (key !== fontKey) {
    fontKey = key;
    let css = "";
    if (style.font_path) css += `@font-face { font-family: "NaizMain"; src: url("/font/main?${encodeURIComponent(key)}"); }\n`;
    if (style.fallback_path) css += `@font-face { font-family: "NaizBackup"; src: url("/font/fallback?${encodeURIComponent(key)}"); }\n`;
    document.getElementById("fonts").textContent = css;
  }
  const names = [];
  if (style.font_path) names.push('"NaizMain"');
  if (style.font_name) names.push(JSON.stringify(style.font_name));
  if (style.fallback_path) names.push('"NaizBackup"');
  names.push('"Microsoft JhengHei"', '"Yu Gothic"', '"Malgun Gothic"', "sans-serif");
  const big = Math.max(10, style.size || 34) * scale;
  const color = style.color || [255, 255, 255];
  root.setProperty("--font", names.join(","));
  root.setProperty("--size", big + "px");
  root.setProperty("--size-small", Math.max(10, style.size_original || big * 0.6) * scale + "px");
  root.setProperty("--color", rgb(color));
  root.setProperty("--dim", rgb(color.map(c => Math.round(c * 0.82))));
  bgAlpha = (style.opacity === undefined ? 60 : style.opacity) / 100;
  root.setProperty("--bg", rgb(style.bg || [12, 14, 18], bgAlpha));
  for (const block of shown.values()) paintSpeaker(block);         // 底色深淺改了:說話者的底色透明度跟著改
  root.setProperty("--outline", outline((style.outline === undefined ? 2 : style.outline) * scale, style.outline_color || [0, 0, 0]));
  root.setProperty("--align", style.align || "center");
  root.setProperty("--justify", {left: "flex-start", right: "flex-end"}[style.align] || "center");
  const css = (state.css || "") + "|" + (state.css_version || 0);
  if (css !== cssKey) {
    cssKey = css;
    if (!state.css) document.getElementById("custom").textContent = "";
    else fetch("/custom.css?" + encodeURIComponent(css)).then(r => r.text())
      .then(text => { document.getElementById("custom").textContent = text; });
  }
}
function textNode(item, key) {
  const span = document.createElement("span");
  if (key === "o" && item.r && item.r.length) {
    for (const [part, reading] of item.r) {
      if (reading) {
        const ruby = document.createElement("ruby");
        ruby.append(part);
        const rt = document.createElement("rt");
        rt.textContent = reading;
        ruby.append(rt);
        span.append(ruby);
      } else span.append(part);
    }
  } else span.textContent = item[key] || "";
  return span;
}
// 每一句一個框,用句子的編號(id)對應:已經在畫面上的只更新內容和透明度,不重建
// (重建會讓自訂外觀的動畫一直重播;新的句子才會播一次進場動畫)
const shown = new Map();
function render() {
  const style = state.style || {}, layout = state.layout || "subtitle";
  const wrap = document.getElementById("wrap");
  if (wrap.className !== layout) wrap.className = layout;
  const mode = style.mode || "both", fade = Number(style.fade || 0);
  const elapsed = (performance.now() - received) / 1000;
  const items = layout === "log" ? (state.log || []).slice(-Math.max(1, state.count || 12))
                                 : (state.lines || []).slice(-Math.max(1, style.count || 2));
  const keep = new Set();
  let previous = null;
  for (const item of items) {
    let alpha = 1;
    if (layout !== "log" && fade > 0 && item.i !== null && item.i !== undefined) {
      const over = item.i + elapsed - fade;
      if (over > 0) alpha = Math.max(0, 1 - over / FADE_TIME);
    }
    if (alpha <= 0) continue;
    const rows = [];
    if (mode === "original" && item.o) rows.push(["o", "original main"]);
    if (mode === "both" && item.o) rows.push(["o", "original sub"]);
    if ((mode === "both" || mode === "translation") && item.t) rows.push(["t", "translation main"]);
    if (mode === "translation" && !item.t && item.o) rows.push(["o", "original sub"]);
    if (!rows.length) continue;
    const id = String(item.id === undefined ? JSON.stringify([item.o, item.t]) : item.id);
    keep.add(id);
    let block = shown.get(id);
    if (!block) {
      block = document.createElement("div");
      shown.set(id, block);
      // 放在上一句的後面(新的句子在最下面;已經在畫面上的不搬動,動畫才不會重播)
      wrap.insertBefore(block, previous ? previous.nextSibling : wrap.firstChild);
    }
    previous = block;
    const kind = "block" + (item.f === false ? " partial" : "") +
                 (item.c ? ` speaker speaker-${item.s === undefined ? 0 : item.s}` : "");
    if (block.className !== kind) block.className = kind;
    const speaker = item.c ? rgb(item.c) : "";
    if (block.style.getPropertyValue("--speaker") !== speaker) block.style.setProperty("--speaker", speaker);
    block.speakerColor = item.c || null;
    paintSpeaker(block);
    const opacity = alpha < 1 ? String(alpha) : "";
    if (block.style.opacity !== opacity) block.style.opacity = opacity;
    const content = JSON.stringify([rows, item.o, item.t, item.r || null]);
    if (block.dataset.content !== content) {
      block.dataset.content = content;
      block.textContent = "";
      for (const [key, kind] of rows) {
        const row = document.createElement("div");
        row.className = "row " + kind;
        row.append(textNode(item, key));
        block.append(row);
      }
    }
  }
  for (const [id, block] of shown) {
    if (!keep.has(id)) { block.remove(); shown.delete(id); }
  }
}
function connect() {
  const source = new EventSource("/events");
  source.onmessage = (event) => {
    state = JSON.parse(event.data);
    received = performance.now();
    applyStyle();
    render();
  };
  source.onerror = () => { source.close(); setTimeout(connect, 2000); };   // Naiz Studio 關掉再開:自動重連
}
connect();
setInterval(render, 100);       // 淡出
</script></body></html>
"""
