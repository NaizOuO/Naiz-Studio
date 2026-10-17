// 網頁上的選字翻譯:選取文字後旁邊出現小按鈕,按了才翻譯(不會一選字就跳出來干擾閱讀)。
// 介面放在 Shadow DOM 裡,網頁本身的樣式影響不到。
(() => {
  if (window.__naizTranslate) return;
  window.__naizTranslate = true;

  const host = document.createElement("div");
  host.style.cssText = "all: initial; position: absolute; top: 0; left: 0; z-index: 2147483647;";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
  <style>
    :host { --bg: #ffffff; --fg: #1f2328; --dim: #656d76; --line: #d8dee4; --accent: #2f81f7; --soft: #eef4ff; }
    @media (prefers-color-scheme: dark) {
      :host { --bg: #1f232a; --fg: #e6edf3; --dim: #9aa4b2; --line: #363c45; --accent: #58a6ff; --soft: #24324a; }
    }
    * { box-sizing: border-box; font-family: "Microsoft JhengHei UI", "Segoe UI", system-ui, sans-serif; }
    .dot { position: absolute; width: 26px; height: 26px; border-radius: 13px; border: 1px solid var(--line);
           background: var(--bg); color: var(--accent); font-size: 13px; font-weight: 700; cursor: pointer;
           box-shadow: 0 2px 8px rgba(0,0,0,.18); display: none; padding: 0; line-height: 24px; text-align: center; }
    .dot:hover { background: var(--soft); }
    .card { position: absolute; width: 340px; max-width: calc(100vw - 24px); background: var(--bg); color: var(--fg);
            border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.22);
            padding: 12px 14px; font-size: 14px; line-height: 1.55; display: none; }
    .source { color: var(--dim); font-size: 12.5px; max-height: 3.2em; overflow: hidden; margin-bottom: 6px; }
    .main { font-size: 15px; white-space: pre-wrap; word-break: break-word; max-height: 40vh; overflow: auto; }
    .meta { color: var(--dim); font-size: 12.5px; margin-top: 4px; }
    .foot { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; gap: 8px; }
    .note { color: var(--dim); font-size: 12px; }
    button.save { border: 1px solid var(--accent); background: transparent; color: var(--accent); border-radius: 6px;
                  padding: 3px 12px; font-size: 13px; cursor: pointer; }
    button.save:hover { background: var(--soft); }
    button.save:disabled { border-color: var(--line); color: var(--dim); cursor: default; background: transparent; }
    .error { color: #d1242f; }
  </style>
  <button class="dot" title="翻譯（Naiz Studio）">譯</button>
  <div class="card"><div class="source"></div><div class="main"></div><div class="meta"></div>
    <div class="foot"><span class="note"></span><button class="save">記下來</button></div></div>`;
  const dot = root.querySelector(".dot");
  const card = root.querySelector(".card");
  const $ = (name) => card.querySelector(name);
  let current = null; // {text, context, result}
  let picked = null;  // 選取範圍在畫面上的位置(卡片放不下時改放上面)

  function attach() {
    if (!host.isConnected) document.documentElement.appendChild(host);
  }

  function hide() {
    dot.style.display = "none";
    card.style.display = "none";
    current = null;
  }

  // 選到的字所在的那句話(記下來時當例句,單字翻譯時讓模型看前後文)
  function sentenceAround(selection, text) {
    const node = selection.anchorNode;
    const block = node && (node.nodeType === 1 ? node : node.parentElement);
    const whole = (block && block.closest("p,li,td,h1,h2,h3,h4,blockquote,div,span") || block || {}).innerText || "";
    const at = whole.indexOf(text);
    if (at < 0) return text;
    const ends = /[.!?。！？;；\n]/;
    let start = at;
    while (start > 0 && !ends.test(whole[start - 1]) && at - start < 300) start--;
    let end = at + text.length;
    while (end < whole.length && !ends.test(whole[end]) && end - at < 400) end++;
    return whole.slice(start, Math.min(end + 1, whole.length)).trim();
  }

  function place(element, x, y) {
    const width = element.offsetWidth || 340;
    const left = Math.min(Math.max(8, x), window.scrollX + document.documentElement.clientWidth - width - 8);
    element.style.left = left + "px";
    element.style.top = y + "px";
  }

  document.addEventListener("mouseup", (event) => {
    if (event.composedPath().includes(host)) return;
    setTimeout(() => {
      const selection = window.getSelection();
      const text = (selection && selection.toString() || "").trim();
      if (!text || text.length > 4000 || !selection.rangeCount) {
        if (card.style.display !== "block") hide();
        return;
      }
      const rect = selection.getRangeAt(0).getBoundingClientRect();
      picked = { top: rect.top + window.scrollY, bottom: rect.bottom + window.scrollY };
      attach();
      card.style.display = "none";
      current = { text, context: sentenceAround(selection, text) };
      dot.style.display = "block";
      place(dot, rect.right + window.scrollX + 4, rect.bottom + window.scrollY + 4);
    }, 0);
  });

  document.addEventListener("mousedown", (event) => {
    if (!event.composedPath().includes(host)) hide();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hide();
  });

  // 卡片整個留在畫面裡:下面放不下就放到選取範圍上面,上面也不夠就貼著畫面底部
  function fit() {
    if (!picked) return;
    const height = card.offsetHeight, top = window.scrollY + 8, bottom = window.scrollY + window.innerHeight - 8;
    let y = picked.bottom + 8;
    if (y + height > bottom) y = picked.top - height - 8 >= top ? picked.top - height - 8 : Math.max(top, bottom - height);
    card.style.top = y + "px";
  }

  function show(result) {
    const main = $(".main"), meta = $(".meta"), note = $(".note"), save = $(".save");
    main.classList.toggle("error", !result.ok);
    if (!result.ok) {
      main.textContent = result.error || "翻譯失敗";
      meta.textContent = "";
      note.textContent = "";
      save.style.display = "none";
      fit();
      return;
    }
    current.result = result;
    if (result.kind === "word") {
      main.textContent = result.meaning || result.translation || "";
      meta.textContent = [result.pos, result.reading].filter((v) => v && v !== "-").join("・");
    } else {
      main.textContent = result.translation || "";
      meta.textContent = "";
    }
    save.style.display = "";
    save.disabled = !!result.saved;
    save.textContent = result.saved ? "已記錄" : "記下來";
    note.textContent = result.saved ? `查過 ${result.count} 次` : "";
    fit();
  }

  dot.addEventListener("click", () => {
    if (!current) return;
    const { text, context } = current;
    dot.style.display = "none";
    $(".source").textContent = text;
    $(".main").classList.remove("error");
    $(".main").textContent = "翻譯中…";
    $(".meta").textContent = "";
    $(".note").textContent = "";
    $(".save").style.display = "none";
    card.style.display = "block";
    place(card, parseFloat(dot.style.left) - 150, parseFloat(dot.style.top) + 4);
    fit();
    chrome.runtime.sendMessage({ type: "translate", data: { text, context } }, (result) => {
      if (current && current.text === text) show(result || { ok: false, error: "沒有回應" });
    });
  });

  $(".save").addEventListener("click", () => {
    if (!current || !current.result) return;
    const r = current.result;
    const data = { text: current.text, kind: r.kind, translation: r.translation || "", meaning: r.meaning || "",
                   pos: r.pos || "", reading: r.reading || "", context: current.context,
                   url: location.href, title: document.title };
    chrome.runtime.sendMessage({ type: "save", data }, (result) => {
      if (result && result.ok) {
        current.result = { ...r, saved: true, count: result.count };
        show(current.result);
      } else {
        $(".note").textContent = (result && result.error) || "沒有存到";
      }
    });
  });
})();
