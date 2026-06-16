const DEFAULT_API = "http://localhost:8000";

async function apiBase() {
  const { apiBase } = await chrome.storage.sync.get("apiBase");
  return apiBase || DEFAULT_API;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "asa-check-link",
    title: "用 Anti-Scam Agent 檢查此連結",
    contexts: ["link"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "asa-check-link" || !tab?.id) return;
  const url = info.linkUrl;
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, source: "extension" }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const { id } = await res.json();
    chrome.tabs.sendMessage(tab.id, { type: "asa:start", url, id, base }).catch(() => {});
  } catch (e) {
    chrome.tabs.sendMessage(tab.id, { type: "asa:error", url, error: String(e) }).catch(() => {});
  }
});
