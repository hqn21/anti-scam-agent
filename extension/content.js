/* content.js — Anti-Scam Agent overlay (Shadow DOM, pure vanilla JS) */
"use strict";

// ---------------------------------------------------------------------------
// Track last right-click position so the card appears near the context menu.
// ---------------------------------------------------------------------------
let lastPos = { x: 0, y: 0 };
document.addEventListener(
  "contextmenu",
  (e) => {
    lastPos = { x: e.clientX, y: e.clientY };
  },
  true
);

// ---------------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------------
/** @type {HTMLElement|null} */
let currentHost = null;
/** @type {number|null} */
let elapsedIntervalId = null;
/** @type {number|null} */
let autoDismissTimeoutId = null;
/** Monotonically increasing counter; each asa:start bumps it so stale poll loops self-terminate. */
let runSeq = 0;

// ---------------------------------------------------------------------------
// Styles injected into the shadow root (content_scripts CSS doesn't reach it)
// ---------------------------------------------------------------------------
const SHADOW_STYLES = `
  :host {
    all: initial;
  }

  .asa-card {
    position: fixed;
    z-index: 2147483647;
    width: 260px;
    background: #ffffff;
    color: #1a1a1a;
    border-radius: 10px;
    padding: 14px 16px 12px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.18), 0 1px 4px rgba(0,0,0,0.10);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
    line-height: 1.5;
    box-sizing: border-box;
  }

  .asa-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
    font-weight: 600;
    font-size: 13px;
    color: #444;
  }

  .asa-hostname {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 11px;
    color: #666;
    margin-top: 1px;
  }

  .asa-close {
    position: absolute;
    top: 8px;
    right: 10px;
    background: none;
    border: none;
    cursor: pointer;
    color: #888;
    font-size: 16px;
    line-height: 1;
    padding: 2px 4px;
    border-radius: 4px;
    transition: color 0.15s, background 0.15s;
  }
  .asa-close:hover {
    color: #333;
    background: #f0f0f0;
  }

  /* Spinner */
  .asa-spinner-wrap {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 4px 0;
  }

  .asa-spinner {
    width: 16px;
    height: 16px;
    border: 2px solid #ddd;
    border-top-color: #4f8ef7;
    border-radius: 50%;
    animation: asa-spin 0.8s linear infinite;
    flex-shrink: 0;
  }

  @keyframes asa-spin {
    to { transform: rotate(360deg); }
  }

  .asa-status-text {
    color: #555;
    font-size: 13px;
  }

  /* Result area */
  .asa-result {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 2px 0;
  }

  /* Badge */
  .asa-badge {
    display: inline-block;
    border-radius: 999px;
    padding: 3px 10px;
    font-weight: 600;
    font-size: 12px;
    letter-spacing: 0.01em;
  }
  .asa-badge--scam {
    background: #fde8e8;
    color: #c0392b;
    border: 1px solid #f5b7b1;
  }
  .asa-badge--uncertain {
    background: #fef9e7;
    color: #b7770d;
    border: 1px solid #f9e79f;
  }
  .asa-badge--legit {
    background: #e9f7ef;
    color: #1e8449;
    border: 1px solid #a9dfbf;
  }

  .asa-meta {
    font-size: 12px;
    color: #555;
    margin: 0;
    padding: 0;
  }
  .asa-meta + .asa-meta {
    margin-top: 2px;
  }

  /* Report link */
  .asa-link {
    display: inline-block;
    margin-top: 4px;
    font-size: 12px;
    color: #4f8ef7;
    text-decoration: none;
    font-weight: 500;
    border-bottom: 1px solid transparent;
    transition: border-color 0.15s;
  }
  .asa-link:hover {
    border-bottom-color: #4f8ef7;
  }

  /* Error */
  .asa-error-text {
    color: #c0392b;
    font-size: 13px;
  }
`;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function clearTimers() {
  if (elapsedIntervalId !== null) {
    clearInterval(elapsedIntervalId);
    elapsedIntervalId = null;
  }
  if (autoDismissTimeoutId !== null) {
    clearTimeout(autoDismissTimeoutId);
    autoDismissTimeoutId = null;
  }
}

function removeHost() {
  clearTimers();
  if (currentHost) {
    currentHost.remove();
    currentHost = null;
  }
}

/**
 * Returns the hostname from a URL string, or the raw string if parsing fails.
 * @param {string} url
 */
function safeHostname(url) {
  try {
    return new URL(url).hostname;
  } catch (_) {
    return url;
  }
}

/**
 * Clamp `value` between [min, max].
 */
function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

/**
 * Build (or rebuild) the shadow-DOM host and return `{ shadow, card }`.
 * The card is positioned near `lastPos`, clamped to the viewport.
 * @param {string} url  The URL being checked (used for hostname display).
 */
function ensureHost(url) {
  removeHost(); // start fresh

  const CARD_W = 260;
  const CARD_H_APPROX = 200; // approximate height for clamping
  const MARGIN = 12;

  const host = document.createElement("div");
  // Keep the host element itself invisible/unstyled; all layout is in the shadow
  host.setAttribute("data-asa-host", "1");
  (document.documentElement || document.body).appendChild(host);
  currentHost = host;

  const shadow = host.attachShadow({ mode: "open" });

  // Inject styles
  const styleEl = document.createElement("style");
  styleEl.textContent = SHADOW_STYLES;
  shadow.appendChild(styleEl);

  // Card element
  const card = document.createElement("div");
  card.className = "asa-card";

  // Position: near cursor, clamped so nothing overflows
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let left = lastPos.x + MARGIN;
  let top = lastPos.y + MARGIN;
  if (left + CARD_W > vw - MARGIN) left = lastPos.x - CARD_W - MARGIN;
  left = clamp(left, MARGIN, vw - CARD_W - MARGIN);
  if (top + CARD_H_APPROX > vh - MARGIN) top = lastPos.y - CARD_H_APPROX - MARGIN;
  top = clamp(top, MARGIN, vh - CARD_H_APPROX - MARGIN);
  card.style.left = `${left}px`;
  card.style.top = `${top}px`;

  // Close button (always present)
  const closeBtn = document.createElement("button");
  closeBtn.className = "asa-close";
  closeBtn.textContent = "✕";
  closeBtn.title = "關閉";
  closeBtn.setAttribute("aria-label", "關閉");
  closeBtn.addEventListener("click", removeHost);
  card.appendChild(closeBtn);

  // Optional hostname row
  if (url) {
    const hostnameEl = document.createElement("div");
    hostnameEl.className = "asa-hostname";
    hostnameEl.textContent = safeHostname(url);
    card.appendChild(hostnameEl);
  }

  shadow.appendChild(card);
  return { shadow, card };
}

// ---------------------------------------------------------------------------
// Verdict mapping
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------

/**
 * Render the result card with verdict, scam type, payment signal, and report link.
 * Calls clearTimers() first, then rebuilds the overlay card.
 * Auto-dismisses after 30 s.
 * @param {{ url: string, id?: string, curated: object, reportUrl: string }} opts
 */
function renderResult({ url, id, curated, reportUrl }) {
  clearTimers();
  const { card } = ensureHost(url);

  const curatedData = curated || {};
  const verdict = curatedData.verdict || "uncertain";
  const scamType = curatedData.scam_type || null;
  const declined = Boolean(curatedData.payment_explicitly_declined);
  const reportLink = reportUrl || "";

  const result = document.createElement("div");
  result.className = "asa-result";

  // Badge
  const badge = document.createElement("span");
  badge.className = `asa-badge ${VERDICT_BADGE_CLASS[verdict] || "asa-badge--uncertain"}`;
  badge.textContent = VERDICT_LABEL[verdict] || verdict;
  result.appendChild(badge);

  // Scam type (optional)
  if (scamType) {
    const typeEl = document.createElement("p");
    typeEl.className = "asa-meta";
    typeEl.textContent = `類型：${scamType}`;
    result.appendChild(typeEl);
  }

  // Payment signal takeaway
  const payEl = document.createElement("p");
  payEl.className = "asa-meta";
  payEl.textContent = declined
    ? "出現明確刷卡失敗（合法跡象）"
    : "未出現明確刷卡失敗（詐騙常見特徵）";
  result.appendChild(payEl);

  // Report link
  if (reportLink) {
    const link = document.createElement("a");
    link.className = "asa-link";
    link.href = reportLink;
    link.textContent = "看完整報告";
    link.target = "_blank";
    link.rel = "noopener";
    link.addEventListener("click", () => {
      // Allow the link to open, then close the card shortly after
      setTimeout(removeHost, 300);
    });
    result.appendChild(link);
  }

  card.appendChild(result);

  // Auto-dismiss after 30 s
  autoDismissTimeoutId = setTimeout(removeHost, 30_000);
}

/**
 * Render an error card. Does NOT auto-dismiss (errors should stay until read).
 * Calls clearTimers() first, then rebuilds the overlay card.
 * "timeout" in the error string is rendered as "檢查逾時".
 * @param {{ url: string, error: string }} opts
 */
function renderError({ url, error }) {
  clearTimers();
  const { card } = ensureHost(url);

  const rawError = String(error || "");
  const friendlyError =
    rawError.toLowerCase().includes("timeout")
      ? "檢查逾時"
      : rawError || "未知錯誤";

  const errEl = document.createElement("p");
  errEl.className = "asa-error-text";
  errEl.textContent = `檢查失敗：${friendlyError}`;
  card.appendChild(errEl);
}

// ---------------------------------------------------------------------------
// Message handler
// ---------------------------------------------------------------------------
if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || !msg.type) return;

    if (msg.type === "asa:start") {
      const myRun = ++runSeq;
      const { card } = ensureHost(msg.url);

      // Spinner + elapsed counter
      const spinnerWrap = document.createElement("div");
      spinnerWrap.className = "asa-spinner-wrap";

      const spinner = document.createElement("div");
      spinner.className = "asa-spinner";

      const statusText = document.createElement("span");
      statusText.className = "asa-status-text";
      let elapsed = 0;
      statusText.textContent = `檢查中… ${elapsed}s`;

      spinnerWrap.appendChild(spinner);
      spinnerWrap.appendChild(statusText);
      card.appendChild(spinnerWrap);

      elapsedIntervalId = setInterval(() => {
        elapsed += 1;
        statusText.textContent = `檢查中… ${elapsed}s`;
      }, 1000);

      // Poll from the content script so MV3 SW termination can't stall the result.
      if (msg.id && msg.base) {
        const id = msg.id, base = msg.base, url = msg.url;
        const deadline = Date.now() + 5 * 60 * 1000;
        (async () => {
          while (Date.now() < deadline) {
            await new Promise((r) => setTimeout(r, 2000));
            // Stop if this run was superseded or the overlay was manually closed.
            if (myRun !== runSeq || currentHost === null) return;
            let data;
            try {
              data = await (await fetch(`${base}/api/analyze/${id}`)).json();
            } catch {
              continue;
            }
            // Re-check staleness after the async fetch.
            if (myRun !== runSeq || currentHost === null) return;
            if (data.status === "done") {
              renderResult({ url, id, curated: data.curated, reportUrl: `${base}/report/${id}` });
              return;
            }
            if (data.status === "error") {
              renderError({ url, error: data.error || "analysis failed" });
              return;
            }
          }
          if (myRun !== runSeq || currentHost === null) return;
          renderError({ url, error: "timeout" });
        })();
      }
      return;
    }

    if (msg.type === "asa:done") {
      renderResult(msg);
      return;
    }

    if (msg.type === "asa:error") {
      renderError(msg);
    }
  });
}
