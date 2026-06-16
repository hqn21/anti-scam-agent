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
  chrome.tabs.sendMessage(tab.id, { type: "asa:start", url });
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, source: "extension" }),
    });
    const { id } = await res.json();
    poll(tab.id, base, id, url);
  } catch (e) {
    chrome.tabs.sendMessage(tab.id, { type: "asa:error", url, error: String(e) });
  }
});

async function poll(tabId, base, id, url) {
  const deadline = Date.now() + 5 * 60 * 1000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    let data;
    try {
      data = await (await fetch(`${base}/api/analyze/${id}`)).json();
    } catch (e) {
      continue; // transient; keep polling
    }
    if (data.status === "done") {
      chrome.tabs.sendMessage(tabId, { type: "asa:done", url, id, curated: data.curated, reportUrl: `${base}/report/${id}` });
      return;
    }
    if (data.status === "error") {
      chrome.tabs.sendMessage(tabId, { type: "asa:error", url, error: data.error || "analysis failed" });
      return;
    }
  }
  chrome.tabs.sendMessage(tabId, { type: "asa:error", url, error: "timeout" });
}
