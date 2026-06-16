"use strict";

const DEFAULT_API_BASE = "http://localhost:8000";

document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("apiBase");
  const saveBtn = document.getElementById("save");
  const statusEl = document.getElementById("status");

  // Load stored value on open.
  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.sync) {
    chrome.storage.sync.get("apiBase", (result) => {
      input.value = result.apiBase || DEFAULT_API_BASE;
    });
  } else {
    input.value = DEFAULT_API_BASE;
  }

  // Save on button click.
  saveBtn.addEventListener("click", () => {
    let value = (input.value || "").trim().replace(/\/+$/, "");
    if (!value) {
      value = DEFAULT_API_BASE;
      input.value = value;
    }

    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.sync) {
      chrome.storage.sync.set({ apiBase: value }, () => {
        showStatus();
      });
    } else {
      // Fallback: nothing to persist, but still acknowledge.
      showStatus();
    }
  });

  function showStatus() {
    statusEl.textContent = "已儲存 ✓";
    setTimeout(() => {
      statusEl.textContent = "";
    }, 1500);
  }
});
