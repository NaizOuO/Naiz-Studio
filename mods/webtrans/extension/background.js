// 和本機的 Naiz Studio 連線(網頁裡的程式不能直接連,要經過這裡,Naiz Studio 才認得是擴充功能)
const SERVICE = "http://127.0.0.1:47821";

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  const path = { translate: "/translate", save: "/save" }[message.type];
  if (!path) return false;
  fetch(SERVICE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message.data),
  })
    .then((response) => response.json())
    .then(reply)
    .catch(() => reply({ ok: false, offline: true, error: "連不上 Naiz Studio，請先開啟 Naiz Studio" }));
  return true; // 非同步回覆
});

// PDF:瀏覽器內建的檢視器擴充功能進不去,打開 PDF 時改用自己的檢視器
const VIEWER = chrome.runtime.getURL("pdfjs/web/viewer.html");
const isPdf = (url) => /^(https?|file):\/\/[^?#]*\.pdf([?#]|$)/i.test(url) || /^https?:\/\/(www\.)?arxiv\.org\/pdf\//i.test(url);
const openInViewer = (tabId, url) => chrome.tabs.update(tabId, { url: VIEWER + "?file=" + encodeURIComponent(url) });

chrome.webNavigation.onBeforeNavigate.addListener(({ tabId, frameId, url }) => {
  if (frameId === 0 && isPdf(url) && !url.includes("#naiz-original")) openInViewer(tabId, url);
});

// 工具列圖示:整頁翻譯／換回原文。擴充功能進不去的頁面(瀏覽器內建的 PDF 檢視器)就改用自己的檢視器打開
function togglePage(tab) {
  if (!tab) return;
  if ((tab.url || "").startsWith(VIEWER)) {        // 自己的 PDF 檢視器是擴充功能的頁面,要用這種方式通知
    chrome.runtime.sendMessage({ type: "toggle-page", url: tab.url }).catch(() => {});
    return;
  }
  chrome.tabs.sendMessage(tab.id, { type: "toggle-page" }).catch(() => {
    if (/^(https?|file):/.test(tab.url || "")) openInViewer(tab.id, tab.url);
  });
}
chrome.action.onClicked.addListener(togglePage);
chrome.commands.onCommand.addListener((command, tab) => {
  if (command === "toggle-page") togglePage(tab);
});
