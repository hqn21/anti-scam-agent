# Design: Web API + Dashboard + Chrome Extension

**Date:** 2026-06-16
**Branch:** `feat/web-api-extension`
**Status:** Approved (design); pending implementation

## Goal

Extend the existing Anti-Scam Agent (currently CLI-only) with:

1. An **HTTP API** (FastAPI) wrapping the existing two-agent pipeline.
2. A **React web app** with three pages: Dashboard, History, Query.
3. **SQLite persistence** so every analysis is durable and visible in History.
4. A **Chrome Extension** (MV3, native/vanilla) that checks any link via right-click.

Everything runs **locally** for demo purposes. The CLI is fully preserved.

## Hard constraints (carried from the existing project)

- **The blind-browser invariant must hold.** All new code (API, DB, web app,
  extension) is strictly *downstream* of the Browsing Agent — it never feeds into
  `_build_task_prompt`, `BrowsingResult` field descriptions, or the `read_email_inbox`
  tool description. Nothing here weakens the invariant or its guarding tests.
- **`reporting.py` stays LLM-free and pure.** The DB reads the already-produced
  `RunReport`; it does not add anything an agent sees.
- **`data/` is hand-edited by the user** — never sweep it into commits.

## Key decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Execution model | Async job + polling. `POST` returns a job id immediately; clients poll. |
| Concurrency | Serialized — one background worker runs one analysis at a time. |
| Existing `logs/` history | Not imported. DB starts fresh; `logs/` keeps being written as today. |
| Web app stack | React + Vite + TypeScript + Tailwind + Recharts (best-quality target). |
| Chrome extension UI | Native MV3, Shadow-DOM overlay, pure CSS (works injected on any site). |
| Default API port | 8000 (configurable in the extension). |

## Architecture overview

```
                       ┌─────────────────────────────────────────┐
   Chrome Extension ──▶ │  FastAPI app (uvicorn, :8000)            │
   React web app ─────▶ │   POST /api/analyze   -> enqueue job    │
   CLI (unchanged) ───▶ │   GET  /api/analyze/{id}  (poll)        │
                        │   GET  /api/analyses      (history list)│
                        │   GET  /api/analyses/{id} (full report) │
                        │   GET  /api/stats         (dashboard)   │
                        │   serves web/dist (built React)         │
                        │                                         │
                        │   async worker (concurrency=1) ─────────┼─▶ run_pipeline(url)
                        │                                         │      (existing 2 agents)
                        │   sqlite (db.py)  ◀─────────────────────┘
                        └─────────────────────────────────────────┘
```

## Component 1 — Pipeline refactor (backend, small/non-breaking)

`run_pipeline` already builds a rich `RunReport` internally but returns only the
`ScamAssessment`. Change its return type to `(ScamAssessment, RunReport)` so callers
can persist the full report.

- `src/anti_scam_agent/__main__.py` unpacks the tuple and prints the same assessment
  JSON to stdout — **CLI output and behavior unchanged**.
- No prompt, model, or agent change. `logs/` writing is untouched.

## Component 2 — Persistence (`src/anti_scam_agent/db.py`)

Stdlib `sqlite3`, no ORM. One table, one row per run. Connection-per-operation
(`sqlite3.connect(path)` each call) so it is safe across the worker thread and
FastAPI's threadpool. DB path configurable via env `ASA_DB_PATH`, defaulting to
repo-root `./anti_scam.db` — kept out of the hand-edited `data/` directory, and
gitignored.

### Table `analyses`

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | uuid4 |
| `url` | TEXT | normalized target url |
| `domain` | TEXT | extracted hostname |
| `status` | TEXT | `queued` / `running` / `done` / `error` |
| `source` | TEXT | `web` / `extension` / `cli` |
| `created_at` | TEXT | ISO8601 local |
| `finished_at` | TEXT NULL | ISO8601 local |
| `verdict` | TEXT NULL | one of the 5 `Verdict` labels |
| `is_scam` | INTEGER NULL | 0/1 |
| `scam_type` | TEXT NULL | |
| `payment_explicitly_declined` | INTEGER NULL | the canonical signal, surfaced for filtering |
| `duration_s` | REAL NULL | |
| `cost_usd` | REAL NULL | NULL when pricing unknown |
| `total_tokens` | INTEGER NULL | |
| `report_json` | TEXT NULL | full curated report blob (see Component 3 schema) |
| `error` | TEXT NULL | message when `status='error'` |

### Functions

- `init_db(path)` — create table if absent (idempotent).
- `create_job(url, source) -> id` — insert `queued` row, return id.
- `mark_running(id)`.
- `save_result(id, assessment, report, curated)` — fill result columns + `report_json`, set `status='done'`, `finished_at`.
- `mark_error(id, message)` — `status='error'`.
- `get(id) -> dict | None`.
- `list_analyses(limit, offset, status=None) -> list[dict]` — summary rows, newest first.
- `stats() -> dict` — aggregates (see Component 4).

Hermetic unit tests use a temp sqlite file.

## Component 3 — Curated report shape

A pure helper (e.g. `src/anti_scam_agent/web_report.py`, no LLM) maps
`(ScamAssessment, RunReport)` → a JSON-serializable `curated` dict that the web app
and extension render. **Excludes** the per-step browsing transcript/thinking.

Included fields:

- Identity: `url`, `domain`, `started_at`, `source`, `duration_s`.
- Headline: `verdict`, `is_scam`, `scam_type`,
  **`payment_explicitly_declined`** (highlighted as the headline detection signal).
- Reasoning: `reasoning`, `risk_factors[]`.
- Observation summary (from `BrowsingResult`, neutral fields only):
  `website_summary`, `form_fields_requested[]`, `unexpected_events[]`,
  `login_outcome`, `payment_outcome`, `credit_card_submitted`, `outgoing_links[]`.
- Static signals (from `StaticSignals`): domain age / expiry, TLS issuer + validity,
  DNS summary — rendered compactly, `None`-safe.
- Telemetry: `cost_usd`, `total_tokens`, per-stage durations.

This helper is unit-tested against hand-written fixtures (no network/LLM).

## Component 4 — API (`src/anti_scam_agent/api.py`)

FastAPI app. A module-level async job queue + a single worker task started in the
app's `lifespan` that drains the queue **one job at a time** (serialization).

### Endpoints

- `POST /api/analyze` body `{ "url": str, "source": "web"|"extension" }`
  → normalize url, `create_job`, enqueue id, return `{ "id", "status": "queued" }` (202).
- `GET /api/analyze/{id}` → `{ id, status, curated? , error? }`. The polling endpoint
  used by the query page and the extension.
- `GET /api/analyses?limit=&offset=&status=` → history summary list.
- `GET /api/analyses/{id}` → full curated report (or 404).
- `GET /api/stats` → `{ total, by_verdict{...}, scam_count, legit_count, uncertain_count,
  scam_rate, scam_types{type: n}, avg_duration_s, total_cost_usd }`.

### Worker

```
async worker():
    while True:
        id = await queue.get()
        db.mark_running(id)
        try:
            assessment, report = await run_pipeline(url)
            db.save_result(id, assessment, report, curated)
        except Exception as e:
            db.mark_error(id, str(e))
```

Because `run_pipeline` is already failure-tolerant (it salvages a neutral result
rather than raising for browsing hiccups), most runs reach `done`; `error` is for
true infrastructure failures.

### Cross-cutting

- CORS middleware `allow_origins=["*"]` (demo; lets the extension call `localhost`
  from any page).
- `init_db()` on startup.
- Static mount: if `web/dist` exists, serve it at `/` (SPA fallback to `index.html`).
- New console script `anti-scam-server` (runs uvicorn on `:8000`); add `fastapi` +
  `uvicorn` deps. Port/host configurable via env / CLI args.

Hermetic API tests use FastAPI `TestClient` with `run_pipeline` monkeypatched to a
stub (no real browser/LLM), asserting the job lifecycle and the stats/history shapes.

## Component 5 — Web app (`web/`)

React + Vite + TypeScript + Tailwind + Recharts. React Router. No auth.

- **Dashboard** (`/`): stat cards (total runs, scam / legit / uncertain counts, scam
  rate, avg duration, total cost) + a verdict-distribution chart and a scam-type
  breakdown chart (Recharts). Fetches `/api/stats`.
- **History** (`/history`): sortable table (time, domain, verdict badge, source);
  row click → report route. Fetches `/api/analyses`.
- **Query** (`/query`): URL input → `POST /api/analyze` → poll `/api/analyze/{id}`
  every ~2s, showing a live "分析中 Xs" elapsed counter → on `done`, render result and
  a link to the full report route.
- **Report** (`/report/:id`): the curated report renderer, reused by History and
  Query. `payment_explicitly_declined` shown as the headline signal with explanatory
  copy. Fetches `/api/analyses/{id}`.

Component design follows `vercel-react-best-practices` and `vercel-composition-patterns`;
the result is reviewed against `web-design-guidelines` before completion.

**Serving:** dev via `npm run dev` (Vite proxies `/api` → `:8000`); demo via
`npm run build` → FastAPI serves `web/dist`.

## Component 6 — Chrome Extension (`extension/`, MV3)

Native/vanilla, no framework. Files:

- `manifest.json` — MV3; `permissions: ["contextMenus"]`;
  `host_permissions: ["http://localhost:8000/*"]`; background service worker +
  content script registered for `<all_urls>`.
- `background.js` (service worker) — registers a context menu item
  "用 Anti-Scam Agent 檢查此連結" with `contexts: ["link"]`. On click: read
  `info.linkUrl`, tell the content script (via `tabs.sendMessage`) to show the
  overlay, then `POST /api/analyze` (`source=extension`) and poll
  `/api/analyze/{id}`, relaying status/result to the content script. (Fetch runs in
  the service worker, which holds the `host_permissions`.)
- `content.js` — listens for `contextmenu` to remember the last right-clicked element
  + coordinates; on a "start" message, mounts a small overlay in a **Shadow DOM**
  (so page CSS can't clobber it) anchored near those coordinates: a spinner + live
  elapsed-seconds counter ("檢查中… Xs"). On result: swap to a compact verdict badge
  (scam / likely / uncertain / legit) with a "看完整報告" link that opens
  `http://localhost:8000/report/{id}` in a new tab. Auto-dismiss / close button.
- `content.css` — pure CSS, scoped inside the Shadow DOM.
- Optional `options.html` — to change the API base URL (default `http://localhost:8000`),
  stored in `chrome.storage`.

Because extension runs go through the same API, their results persist in SQLite and
appear in History automatically.

## Component 7 — README + packaging

Rewrite `README.md` to document:

- Prereqs (`uv`, Node for the web build), env vars (`OPENAI_API_KEY`,
  `AGENTMAIL_API_KEY`).
- **CLI** (unchanged): `uv run anti-scam-agent <url>`.
- **Server:** `uv run anti-scam-server` (→ `http://localhost:8000`).
- **Web app:** dev (`npm run dev` in `web/`) vs build-and-serve
  (`npm run build`, then the FastAPI server serves it).
- **Extension:** `chrome://extensions` → Developer mode → Load unpacked →
  select `extension/`. Note the default port 8000 and how to change it.
- Where data lives (`./anti_scam.db`, gitignored) and that the CLI still writes `logs/`.

## Testing

- `tests/test_db.py` — hermetic, temp sqlite: job lifecycle, list/stats shapes.
- `tests/test_web_report.py` — curated mapping against scam/legit fixtures (no LLM).
- `tests/test_api.py` — FastAPI `TestClient`, `run_pipeline` monkeypatched: endpoint
  contracts, job state transitions, stats/history.
- Existing tests stay green; `test_pipeline.py` updated for the new
  `(ScamAssessment, RunReport)` return tuple. Blind-browser guard tests untouched and
  still passing.
- Web app + extension: manual/demo verification documented in README (no automated
  JS test harness for this demo scope — YAGNI).

## Out of scope (YAGNI)

- Auth / multi-user.
- Importing existing `logs/` history into the DB.
- Parallel analyses / distributed queue.
- Deployment / hosting (local only).
- Automated front-end / extension test suites.
```
