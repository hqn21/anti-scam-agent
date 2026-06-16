/* background.js — Anti-Scam Agent service worker.
 *
 * Owns ALL networking: it POSTs the analyze request and POLLS for the result.
 * Polling MUST live here (not in the content script): a content-script fetch to
 * http://localhost:8000 runs in the page's origin and gets blocked on most real
 * sites by the page CSP (connect-src) and by mixed-content rules (HTTPS page ->
 * HTTP localhost). The service worker is not subject to either and holds the
 * host_permissions, so its fetch works from any page.
 *
 * Job state is kept in chrome.storage.local so it survives SW termination and is
 * shared with every tab's content-script panel (which renders from storage).
 */
"use strict";

const DEFAULT_API = "http://localhost:8000";
const JOBS_KEY = "asa_jobs";
const POLL_INTERVAL_MS = 2000;
const POLL_DEADLINE_MS = 5 * 60 * 1000;
const ALARM_NAME = "asa-poll";

async function apiBase() {
  const { apiBase } = await chrome.storage.sync.get("apiBase");
  return apiBase || DEFAULT_API;
}

// --- job storage helpers ---------------------------------------------------

async function getJobs() {
  const data = await chrome.storage.local.get(JOBS_KEY);
  return Array.isArray(data[JOBS_KEY]) ? data[JOBS_KEY] : [];
}

async function setJobs(jobs) {
  await chrome.storage.local.set({ [JOBS_KEY]: jobs });
}

function isTerminal(status) {
  return status === "done" || status === "error";
}

function safeHostname(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

async function upsertJob(job) {
  const jobs = await getJobs();
  const i = jobs.findIndex((j) => j.id === job.id);
  if (i >= 0) jobs[i] = { ...jobs[i], ...job };
  else jobs.unshift(job); // newest first
  await setJobs(jobs);
}

async function patchJob(id, patch) {
  const jobs = await getJobs();
  const i = jobs.findIndex((j) => j.id === id);
  if (i < 0) return;
  jobs[i] = { ...jobs[i], ...patch };
  await setJobs(jobs);
}

// --- context menu ----------------------------------------------------------

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "asa-check-link",
    title: "用 Anti-Scam Agent 檢查此連結",
    contexts: ["link"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "asa-check-link") return;
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
    await upsertJob({
      id,
      url,
      hostname: safeHostname(url),
      base,
      status: "queued",
      createdAt: Date.now(),
    });
    ensurePolling();
  } catch (e) {
    // Surface the failure as a transient error item so the user sees it.
    await upsertJob({
      id: "local-" + Date.now(),
      url,
      hostname: safeHostname(url),
      base,
      status: "error",
      error: String(e && e.message ? e.message : e) + "（伺服器無法連線？）",
      createdAt: Date.now(),
    });
  }
});

// --- polling loop ----------------------------------------------------------

let pollingActive = false;

async function pollOnce() {
  const jobs = await getJobs();
  const active = jobs.filter((j) => !isTerminal(j.status) && !String(j.id).startsWith("local-"));
  if (active.length === 0) return false;

  await Promise.all(
    active.map(async (job) => {
      // Give up on jobs that have run past the deadline.
      if (Date.now() - (job.createdAt || 0) > POLL_DEADLINE_MS) {
        await patchJob(job.id, { status: "error", error: "檢查逾時" });
        return;
      }
      let data;
      try {
        const r = await fetch(`${job.base}/api/analyze/${job.id}`);
        if (!r.ok) return; // transient (e.g. 404 right after create); retry next tick
        data = await r.json();
      } catch {
        return; // server momentarily unreachable; retry next tick
      }
      if (data.status === "done") {
        const c = data.curated || {};
        await patchJob(job.id, {
          status: "done",
          verdict: c.verdict || "uncertain",
          scamType: c.scam_type || null,
          declined: Boolean(c.payment_explicitly_declined),
          reportUrl: `${job.base}/report/${job.id}`,
        });
      } else if (data.status === "error") {
        await patchJob(job.id, { status: "error", error: data.error || "分析失敗" });
      } else {
        // queued / running -> reflect the latest server status
        if (job.status !== data.status) await patchJob(job.id, { status: data.status });
      }
    })
  );
  return true;
}

async function ensurePolling() {
  // Keep an alarm as a backstop so polling resumes even if the SW was terminated
  // between ticks (alarms wake the worker).
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: 0.5 });
  if (pollingActive) return;
  pollingActive = true;
  try {
    // The repeated fetches keep the SW alive while work remains.
    while (await pollOnce()) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    }
  } finally {
    pollingActive = false;
    chrome.alarms.clear(ALARM_NAME);
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) ensurePolling();
});

// Resume polling after a SW restart if unfinished jobs remain.
chrome.runtime.onStartup.addListener(() => ensurePolling());
ensurePolling();
