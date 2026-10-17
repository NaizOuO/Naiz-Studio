// 整頁翻譯:按工具列圖示(或 Alt+T)把原文換成譯文,再按一次換回來。
// 一般網頁:一段一段直接換掉文字;PDF 檢視器:字是疊在固定位置上的,改成在原本那段的位置蓋一塊譯文。
// 看得到的先翻,捲到哪翻到哪;一次只問一段,不會把 Naiz Studio 塞爆。
(() => {
  if (window.__naizPage) return;
  window.__naizPage = true;

  const pdfApp = () => window.PDFViewerApplication;
  const cache = new Map(); // 原文 → 譯文(換回原文再翻一次、PDF 縮放重畫都不用重問)
  const queue = [];
  let on = false;
  let running = false;

  // ------------------------------------------------------------ 共用:排隊翻譯、右下角的進度
  const pill = document.createElement("div");
  pill.style.cssText = "all: initial; position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; display: none;"
    + "background: #1f232a; color: #e6edf3; border-radius: 16px; padding: 6px 14px; font: 13px 'Microsoft JhengHei UI', sans-serif;"
    + "box-shadow: 0 4px 14px rgba(0,0,0,.25);";

  function status(text, hold) {
    if (!pill.isConnected) document.documentElement.appendChild(pill);
    pill.textContent = text;
    pill.style.display = text ? "block" : "none";
    clearTimeout(status.timer);
    if (hold) status.timer = setTimeout(() => status(""), hold);
  }

  function ask(text) {
    if (cache.has(text)) return Promise.resolve({ ok: true, translation: cache.get(text) });
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "translate", data: { text, page: true } }, (result) => {
        if (result && result.ok) cache.set(text, result.translation);
        resolve(result || { ok: false, error: "沒有回應" });
      });
    });
  }

  function enqueue(text, done, alive) {
    if (cache.has(text)) return done(cache.get(text));
    queue.push({ text, done, alive });
    pump();
  }

  async function pump() {
    if (running) return;
    running = true;
    while (on && queue.length) {
      status(`整頁翻譯中…（還有 ${queue.length} 段）`);
      const job = queue.shift();
      if (!job.alive()) continue;
      const result = await ask(job.text);
      if (!on) break;
      if (result.ok) job.done(result.translation);
      else if (result.offline) { status(result.error, 5000); stop(); break; }
    }
    running = false;
    if (on && !queue.length) status("");
  }

  const worth = (text) => /\p{L}{2}/u.test(text) && !/^[\p{Script=Han}\p{P}\p{N}\s]+$/u.test(text);

  // ------------------------------------------------------------ 一般網頁
  const BLOCKS = "p,li,h1,h2,h3,h4,h5,h6,td,th,dt,dd,blockquote,figcaption,caption,summary,div";
  const SKIP = "script,style,noscript,code,pre,textarea,input,select,button,svg,math,[contenteditable='true']";
  const originals = new Map(); // 元素 → 原本的 HTML
  let watcher = null;

  function candidates() {
    return [...document.body.querySelectorAll(BLOCKS)].filter((element) => {
      if (element.closest(SKIP) || element.querySelector(BLOCKS)) return false;
      if (element.tagName === "DIV" && ![...element.childNodes].some((n) => n.nodeType === 3 && n.data.trim().length > 15))
        return false;
      const text = element.innerText.trim();
      return text.length > 1 && text.length < 4000 && worth(text);
    });
  }

  function startWeb() {
    watcher = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const element = entry.target;
        watcher.unobserve(element);
        const text = element.innerText.trim();
        enqueue(text, (translation) => {
          if (!on || !element.isConnected) return;
          originals.set(element, element.innerHTML);
          element.textContent = translation;
        }, () => element.isConnected);
      }
    }, { rootMargin: "600px 0px" });
    candidates().forEach((element) => watcher.observe(element));
  }

  function stopWeb() {
    if (watcher) watcher.disconnect();
    for (const [element, html] of originals) if (element.isConnected) element.innerHTML = html;
    originals.clear();
  }

  // ------------------------------------------------------------ PDF 檢視器
  const STYLE = `
    .naiz-layer { position: absolute; inset: 0; z-index: 3; pointer-events: none; }
    .naiz-para { position: absolute; background: #fff; color: #111; overflow: hidden; white-space: pre-wrap;
                 line-height: 1.3; pointer-events: auto; font-family: "Microsoft JhengHei", "Segoe UI", sans-serif;
                 display: none; }`;

  // 找段落:規則照 Naiz Studio PDF 編輯器的 plugins/editor/paragraphs.py(改字時找整段用的)
  // 字塊照 PDF 裡原本的順序接成行,再依行距、字級、縮排、句尾、項目編號判斷哪幾行是同一段。
  const LIST_START = /^\s*([一二三四五六七八九十百]+[、.．]|[(（][一二三四五六七八九十\d]+[)）]|\d+[.、．)）]\s*\S|[①-⑳]|[•●○◆■▪\-–—]\s)/;
  const ENDINGS = "。！？：；.!?:;」』）)";

  function readLines(pageDiv) {
    const base = pageDiv.getBoundingClientRect();
    const lines = [];
    for (const span of pageDiv.querySelectorAll(".textLayer span")) {
      if (span.classList.contains("markedContent") || !span.textContent.trim()) continue;
      const r = span.getBoundingClientRect();
      if (!r.height) continue;
      const item = { x: r.left - base.left, r: r.right - base.left, y: r.top - base.top, b: r.bottom - base.top,
                     size: r.height, text: span.textContent };
      const line = lines[lines.length - 1];
      const size = line ? Math.max(item.size, line.size) : 0;
      // 底部差不多高、往右走、中間沒有空很大一段(分欄、表格)就是同一行
      // 上標、下標(字比較小、落在這一行的範圍內)也算同一行
      const script = line && item.size < line.size * 0.8 && item.y > line.y - line.size * 0.4 && item.b < line.b + line.size * 0.4;
      if (line && (Math.abs(item.b - line.b) < size * 0.35 || script) && item.x >= line.lastX && item.x - line.r < size * 2) {
        const space = /\s$/.test(line.text) || /^\s/.test(item.text) || item.x - line.r < item.size * 0.15 ? "" : " ";
        Object.assign(line, { text: line.text + space + item.text, r: Math.max(line.r, item.r), lastX: item.x });
        if (!script) Object.assign(line, { y: Math.min(line.y, item.y), b: Math.max(line.b, item.b) });
      } else {
        lines.push({ ...item, lastX: item.x });
      }
    }
    return lines;
  }

  // 每一行所在那一欄的右邊界:左緣差不多的行裡,90% 的位置
  function columnRight(lines, line) {
    const rights = lines.filter((l) => Math.abs(l.x - line.x) <= line.size * 3).map((l) => l.r).sort((a, b) => a - b);
    return rights[Math.floor(rights.length * 0.9)] || line.r;
  }

  function sameParagraph(group, line, column) {
    const last = group[group.length - 1];
    const size = last.size;
    if (LIST_START.test(line.text)) return false;
    if (last.r < column - size * 4) return false;                                           // 沒寫滿:作者自己換行
    if (last.r < column - size * 2 && ENDINGS.includes(last.text.trim().slice(-1))) return false;
    if (last.r < line.r - size * 2) return false;                                           // 明顯比下一行短
    if (Math.abs(line.size - size) > size * 0.15) return false;
    const drop = line.b - last.b;
    if (drop < size * 0.6 || drop > size * 1.8) return false;
    if (group.length >= 2 && Math.abs(drop - (last.b - group[group.length - 2].b)) > size * 0.25) return false;
    const left = Math.min(...group.map((l) => l.x)), right = Math.max(...group.map((l) => l.r));
    if (line.x > right - size || line.r < left + size) return false;                         // 另一欄
    if (group.length >= 2 && Math.abs(line.x - last.x) > size * 0.5 && Math.abs(line.x - group[1].x) > size * 0.5)
      return false;
    return true;
  }

  function paragraphs(pageDiv) {
    const lines = readLines(pageDiv);
    const groups = [];
    for (const line of lines) {
      const group = groups[groups.length - 1];
      if (group && sameParagraph(group, line, columnRight(lines, group[group.length - 1]))) group.push(line);
      else groups.push([line]);
    }
    return groups.map((group) => {
      const sizes = group.map((l) => l.size).sort((a, b) => a - b);
      let text = "";
      for (const line of group) {   // 行和行之間:中文直接接,英文補空白,斷字的連字號接回去
        const piece = line.text.trim();
        if (text && /-$/.test(text) && /^[a-z]/.test(piece)) text += piece;
        else text += (text && !/[　-鿿]$/.test(text) && !/^[　-鿿]/.test(piece) ? " " : "") + piece;
      }
      return { x: Math.min(...group.map((l) => l.x)), y: Math.min(...group.map((l) => l.y)),
               r: Math.max(...group.map((l) => l.r)), b: Math.max(...group.map((l) => l.b)),
               h: sizes[Math.floor(sizes.length / 2)], text };
    }).filter((p) => worth(p.text));
  }

  function fill(box, translation, size) {
    box.textContent = translation;
    box.style.display = "block";
    let font = size;
    box.style.fontSize = font + "px";
    while (box.scrollHeight > box.clientHeight + 1 && font > 5) {
      font -= 0.5;
      box.style.fontSize = font + "px";
    }
  }

  function coverPage(pageDiv) {
    pageDiv.querySelector(".naiz-layer")?.remove();
    if (!on || !pageDiv.querySelector(".textLayer span")) return;
    const layer = document.createElement("div");
    layer.className = "naiz-layer";
    pageDiv.appendChild(layer);
    for (const p of paragraphs(pageDiv)) {
      const box = document.createElement("div");
      box.className = "naiz-para";
      const pad = Math.max(2, p.h * 0.3);
      Object.assign(box.style, { left: p.x - pad + "px", top: p.y - pad + "px",
                                 width: p.r - p.x + pad * 2 + "px", height: p.b - p.y + pad * 2 + "px" });
      layer.appendChild(box);
      const size = Math.max(6, p.h * 0.82);
      enqueue(p.text, (translation) => box.isConnected && fill(box, translation, size), () => box.isConnected);
    }
  }

  function onRendered(event) {
    const pageDiv = document.querySelector(`.page[data-page-number="${event.pageNumber}"]`);
    if (pageDiv) coverPage(pageDiv);
  }

  function startPdf() {
    if (!document.getElementById("naiz-style")) {
      const style = document.createElement("style");
      style.id = "naiz-style";
      style.textContent = STYLE;
      document.head.appendChild(style);
    }
    pdfApp().eventBus.on("textlayerrendered", onRendered);
    document.querySelectorAll(".page").forEach(coverPage);
  }

  function stopPdf() {
    pdfApp().eventBus.off("textlayerrendered", onRendered);
    document.querySelectorAll(".naiz-layer").forEach((layer) => layer.remove());
  }

  // ------------------------------------------------------------ 開關
  function start() {
    on = true;
    status("整頁翻譯中…");
    pdfApp() ? startPdf() : startWeb();
    pump();
  }

  function stop() {
    on = false;
    queue.length = 0;
    pdfApp() ? stopPdf() : stopWeb();
    if (button) button.textContent = "翻譯整頁";
  }

  function toggle() {
    on ? (stop(), status("已換回原文", 1500)) : start();
    if (button) button.textContent = on ? "顯示原文" : "翻譯整頁";
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === "toggle-page" && (!message.url || message.url === location.href)) toggle();
  });

  // PDF 檢視器的工具列上也放一個按鈕(工具列圖示可能被收在拼圖選單裡)
  let button = null;
  const right = pdfApp() && document.getElementById("toolbarViewerRight");
  if (right) {
    button = document.createElement("button");
    button.textContent = "翻譯整頁";
    button.title = "整頁翻譯（Alt+T）";
    button.style.cssText = "margin: 4px 6px; padding: 0 10px; height: 24px; border-radius: 4px; cursor: pointer;"
      + "border: 1px solid var(--field-border-color, #888); background: transparent; color: var(--main-color, inherit);"
      + "font: 13px 'Microsoft JhengHei UI', sans-serif;";
    button.addEventListener("click", toggle);
    right.prepend(button);
  }
})();
