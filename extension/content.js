/* content.js — Anti-Scam Agent bottom-right job panel (Shadow DOM, vanilla JS).
 *
 * This script does NO networking. The background service worker performs the
 * analyze POST and polls for results, writing job state into
 * chrome.storage.local under "asa_jobs". This panel simply renders that state
 * and updates live via chrome.storage.onChanged, so checks keep progressing even
 * if a card is dismissed or the page is navigated.
 */
"use strict";

const JOBS_KEY = "asa_jobs";

// --------------------------------------------------------------------------
// Module state
// --------------------------------------------------------------------------
/** @type {HTMLElement|null} */
let host = null;
/** @type {ShadowRoot|null} */
let shadow = null;
/** @type {number|null} */
let tickIntervalId = null;
/** @type {Array<object>} */
let jobs = [];

const VERDICT_LABEL = {
  scam: "詐騙",
  likely_scam: "可能詐騙",
  uncertain: "不確定",
  likely_legitimate: "可能合法",
  legitimate: "合法",
};
const VERDICT_BADGE_CLASS = {
  scam: "asa-badge--scam",
  likely_scam: "asa-badge--scam",
  uncertain: "asa-badge--uncertain",
  likely_legitimate: "asa-badge--legit",
  legitimate: "asa-badge--legit",
};

const SHADOW_STYLES = `
  :host { all: initial; }

  .asa-panel {
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 2147483647;
    width: 300px;
    max-height: 60vh;
    display: flex;
    flex-direction: column;
    background: #ffffff;
    color: #1a1a1a;
    border-radius: 12px;
    box-shadow: 0 6px 28px rgba(0,0,0,0.20), 0 1px 4px rgba(0,0,0,0.10);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
    line-height: 1.45;
    overflow: hidden;
  }

  .asa-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
    background: #f7f8fa;
    border-bottom: 1px solid #ececf0;
    font-weight: 600;
    color: #333;
    flex-shrink: 0;
  }
  .asa-head .asa-title { flex: 1; }
  .asa-head button {
    background: none;
    border: none;
    cursor: pointer;
    color: #888;
    font-size: 12px;
    padding: 2px 6px;
    border-radius: 5px;
  }
  .asa-head button:hover { color: #333; background: #ececf0; }

  .asa-list {
    list-style: none;
    margin: 0;
    padding: 0;
    overflow-y: auto;
  }

  .asa-item {
    position: relative;
    padding: 10px 30px 10px 12px;
    border-bottom: 1px solid #f1f1f4;
  }
  .asa-item:last-child { border-bottom: none; }

  .asa-host {
    font-weight: 600;
    font-size: 12.5px;
    color: #222;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-bottom: 4px;
  }

  .asa-row { display: flex; align-items: center; gap: 8px; }

  .asa-spinner {
    width: 14px; height: 14px;
    border: 2px solid #ddd;
    border-top-color: #4f8ef7;
    border-radius: 50%;
    animation: asa-spin 0.8s linear infinite;
    flex-shrink: 0;
  }
  @keyframes asa-spin { to { transform: rotate(360deg); } }

  .asa-status { color: #555; font-size: 12.5px; }
  .asa-error { color: #c0392b; font-size: 12.5px; }

  .asa-badge {
    display: inline-block;
    border-radius: 999px;
    padding: 2px 9px;
    font-weight: 600;
    font-size: 11.5px;
  }
  .asa-badge--scam { background: #fde8e8; color: #c0392b; border: 1px solid #f5b7b1; }
  .asa-badge--uncertain { background: #fef9e7; color: #b7770d; border: 1px solid #f9e79f; }
  .asa-badge--legit { background: #e9f7ef; color: #1e8449; border: 1px solid #a9dfbf; }

  .asa-take { font-size: 11.5px; color: #666; margin: 4px 0 0; }

  .asa-link {
    display: inline-block;
    margin-top: 5px;
    font-size: 12px;
    color: #4f8ef7;
    text-decoration: none;
    font-weight: 500;
  }
  .asa-link:hover { text-decoration: underline; }

  .asa-x {
    position: absolute;
    top: 8px; right: 8px;
    background: none; border: none; cursor: pointer;
    color: #aaa; font-size: 14px; line-height: 1;
    padding: 2px 4px; border-radius: 4px;
  }
  .asa-x:hover { color: #333; background: #f0f0f0; }
`;

// --------------------------------------------------------------------------
// Storage access
// --------------------------------------------------------------------------
function storageOk() {
  return typeof chrome !== "undefined" && chrome.storage && chrome.storage.local;
}

async function readJobs() {
  if (!storageOk()) return [];
  const data = await chrome.storage.local.get(JOBS_KEY);
  return Array.isArray(data[JOBS_KEY]) ? data[JOBS_KEY] : [];
}

async function writeJobs(next) {
  if (!storageOk()) return;
  await chrome.storage.local.set({ [JOBS_KEY]: next });
}

async function dismiss(id) {
  await writeJobs((await readJobs()).filter((j) => j.id !== id));
}

async function clearCompleted() {
  await writeJobs((await readJobs()).filter((j) => j.status !== "done" && j.status !== "error"));
}

// --------------------------------------------------------------------------
// Rendering
// --------------------------------------------------------------------------
function ensurePanel() {
  if (host) return;
  host = document.createElement("div");
  host.setAttribute("data-asa-host", "1");
  (document.documentElement || document.body).appendChild(host);
  shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = SHADOW_STYLES;
  shadow.appendChild(style);
}

function removePanel() {
  if (host) {
    host.remove();
    host = null;
    shadow = null;
  }
}

function elapsedSeconds(job) {
  return Math.max(0, Math.floor((Date.now() - (job.createdAt || Date.now())) / 1000));
}

function renderItem(job) {
  const li = document.createElement("li");
  li.className = "asa-item";

  const hostEl = document.createElement("div");
  hostEl.className = "asa-host";
  hostEl.textContent = job.hostname || job.url || "—";
  hostEl.title = job.url || "";
  li.appendChild(hostEl);

  const row = document.createElement("div");
  row.className = "asa-row";

  if (job.status === "queued" || job.status === "running") {
    const sp = document.createElement("div");
    sp.className = "asa-spinner";
    const txt = document.createElement("span");
    txt.className = "asa-status";
    const label = job.status === "queued" ? "排隊中" : "分析中";
    txt.textContent = `${label}… ${elapsedSeconds(job)}s`;
    row.appendChild(sp);
    row.appendChild(txt);
    li.appendChild(row);
  } else if (job.status === "done") {
    const badge = document.createElement("span");
    const verdict = job.verdict || "uncertain";
    badge.className = `asa-badge ${VERDICT_BADGE_CLASS[verdict] || "asa-badge--uncertain"}`;
    badge.textContent = VERDICT_LABEL[verdict] || verdict;
    row.appendChild(badge);
    if (job.scamType) {
      const t = document.createElement("span");
      t.className = "asa-status";
      t.textContent = job.scamType;
      row.appendChild(t);
    }
    li.appendChild(row);

    const take = document.createElement("p");
    take.className = "asa-take";
    // The card-decline signal only applies when a card was actually submitted.
    if (!job.cardSubmitted) {
      take.textContent = "未送出信用卡資料，此訊號不適用";
    } else if (job.declined) {
      take.textContent = "出現明確刷卡失敗（合法跡象）";
    } else {
      take.textContent = "收下偽造卡號卻未出現刷卡失敗（詐騙常見特徵）";
    }
    li.appendChild(take);

    if (job.reportUrl) {
      const a = document.createElement("a");
      a.className = "asa-link";
      a.href = job.reportUrl;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "看完整報告 →";
      li.appendChild(a);
    }
  } else if (job.status === "error") {
    const err = document.createElement("span");
    err.className = "asa-error";
    err.textContent = `失敗：${job.error || "未知錯誤"}`;
    row.appendChild(err);
    li.appendChild(row);
  }

  const x = document.createElement("button");
  x.className = "asa-x";
  x.textContent = "✕";
  x.title = "移除";
  x.setAttribute("aria-label", "移除");
  x.addEventListener("click", () => dismiss(job.id));
  li.appendChild(x);

  return li;
}

function render() {
  if (!jobs || jobs.length === 0) {
    removePanel();
    stopTicker();
    return;
  }
  ensurePanel();

  // Rebuild contents (keep the <style>, replace the rest).
  const existing = shadow.querySelector(".asa-panel");
  if (existing) existing.remove();

  const panel = document.createElement("div");
  panel.className = "asa-panel";

  const head = document.createElement("div");
  head.className = "asa-head";
  const title = document.createElement("span");
  title.className = "asa-title";
  title.textContent = "🛡️ Anti-Scam 檢查";
  head.appendChild(title);

  const hasCompleted = jobs.some((j) => j.status === "done" || j.status === "error");
  if (hasCompleted) {
    const clearBtn = document.createElement("button");
    clearBtn.textContent = "清除已完成";
    clearBtn.addEventListener("click", clearCompleted);
    head.appendChild(clearBtn);
  }

  panel.appendChild(head);

  const list = document.createElement("ul");
  list.className = "asa-list";
  const sorted = [...jobs].sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
  for (const job of sorted) list.appendChild(renderItem(job));
  panel.appendChild(list);

  shadow.appendChild(panel);

  manageTicker();
}

// A 1s ticker re-renders while any job is still running, to advance the elapsed
// counter. It stops once everything is terminal.
function manageTicker() {
  const anyActive = jobs.some((j) => j.status === "queued" || j.status === "running");
  if (anyActive && tickIntervalId === null) {
    tickIntervalId = setInterval(render, 1000);
  } else if (!anyActive) {
    stopTicker();
  }
}

function stopTicker() {
  if (tickIntervalId !== null) {
    clearInterval(tickIntervalId);
    tickIntervalId = null;
  }
}

// --------------------------------------------------------------------------
// Wire up
// --------------------------------------------------------------------------
async function init() {
  jobs = await readJobs();
  render();
}

if (storageOk()) {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes[JOBS_KEY]) {
      jobs = Array.isArray(changes[JOBS_KEY].newValue) ? changes[JOBS_KEY].newValue : [];
      render();
    }
  });
  init();
}
