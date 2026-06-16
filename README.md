# Anti-Scam Agent

Anti-Scam Agent detects phishing / scam websites (fake e-commerce, lottery / prize-claim,
credit-card harvesting, etc.) **without blacklists**. It exploits a structural weakness of
scam sites: they rarely implement real backend validation, because they have no real user
base or payment processor. A legitimate site runs a real payment processor that rejects a
fabricated card with an explicit *"card declined / invalid"* error; a scam site, having no
processor, accepts it or moves on without a clear card error. That **absence of an explicit
card decline** is the canonical detection signal.

The system is two LLM agents wired in sequence:

1. **Browsing Agent** drives a real browser (via `browser-use`) as an ordinary first-time
   user, filling forms with a synthetic persona. It stays *unaware* that it is part of an
   anti-scam system.
2. **Analysis Agent** consumes the browsing observation plus static signals (WHOIS domain
   age, TLS certificate, DNS) and emits a calibrated scam assessment.

On top of the core pipeline, this repo adds:

- a **CLI** (the original interface),
- an **HTTP API** (FastAPI) that runs analyses through a serialized background worker,
- a **React web app** — Dashboard, History, and a Query page,
- **SQLite persistence** so every run is durable, and
- a **Chrome extension** to right-click any link and check whether it's a scam.

Everything runs **locally** — this is a demo, not a hosted service.

---

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (Python ≥ 3.12) — backend, CLI, API.
- [Node.js](https://nodejs.org/) (with `npm`) — only needed to build/run the web app.
- A `.env` file in the repo root (copy `.env.example`) with:
  - `OPENAI_API_KEY` — required (both `browser-use` and `openai-agents` read it).
  - `AGENTMAIL_API_KEY` — **required**; the persona's email is routed through an AgentMail
    inbox so the Browsing Agent can read verification codes mid-flow. The app fails fast if
    this is unset.

```bash
uv sync          # install / sync Python dependencies
cp .env.example .env   # then fill in your API keys
```

---

## 1. CLI (original interface)

Analyze a single URL or bare domain (bare domains are normalized to `http://`):

```bash
uv run anti-scam-agent shop.example.com
uv run anti-scam-agent https://shop.example.com --verbose
```

It prints the assessment JSON to stdout and a per-run report folder under `logs/` (the
report path is printed to stderr). `--verbose` (or `ASA_LOG_VERBOSE=1`) inlines the full
agent thinking into the report.

---

## 2. API server

```bash
uv run anti-scam-server                 # http://127.0.0.1:8000
uv run anti-scam-server --host 0.0.0.0 --port 8000
```

Configurable via flags or env vars: `ASA_HOST`, `ASA_PORT`, and `ASA_DB_PATH` (the SQLite
file, default `./anti_scam.db`).

Each analysis drives a **real browser** and takes on the order of **1–3 minutes**, so the
API is asynchronous: you submit a job and poll for the result. Runs are **serialized** (one
at a time) to keep the real-browser usage and API costs under control.

| Method & path | Purpose |
|---|---|
| `POST /api/analyze` | Body `{"url": "...", "source": "web"\|"extension"}`. Returns `{id, status}` (202). |
| `GET /api/analyze/{id}` | Poll job status; includes the curated report once `status` is `done`. |
| `GET /api/analyses` | History list (`?limit=&offset=&status=`). |
| `GET /api/analyses/{id}` | Full curated report for one run. |
| `GET /api/stats` | Dashboard aggregates. |

When a built web app exists at `web/dist/`, the server also serves it at `/`.

---

## 3. Web app

The web app (React + Vite + TypeScript + Tailwind) has three pages: a **Dashboard**
(totals, verdict distribution, scam types, cost/time), **History** (every past run, click
through to its report), and **Query** (enter a URL, watch a live "分析中…Xs" counter, then
read the report). Reports highlight the headline signal — whether the site ever produced an
explicit card decline — and exclude the noisy step-by-step browsing transcript.

Install web dependencies once:

```bash
npm --prefix web install
```

### Option A — all-in-one demo (recommended)

Build the app and let the API server serve it:

```bash
npm --prefix web run build      # produces web/dist/
uv run anti-scam-server         # then open http://localhost:8000
```

### Option B — dev mode (hot reload)

Run the API and the Vite dev server side by side (Vite proxies `/api` → `:8000`):

```bash
uv run anti-scam-server         # terminal 1
npm --prefix web run dev        # terminal 2 → http://localhost:5173
```

---

## 4. Chrome extension

Right-click any link on any page → **"用 Anti-Scam Agent 檢查此連結"**. A panel appears in the
**bottom-right corner** listing each check: a live "分析中…Xs" counter while it runs, then a
verdict badge, the headline card-decline takeaway, and a link to the full report. Each item
can be dismissed individually (other checks keep running), and "清除已完成" clears finished
ones. The panel is driven by the extension's background worker via `chrome.storage`, so
progress is never lost when you close an item or navigate. Because checks go through the same
API, they also show up in the web app's **History**.

**Load it (unpacked):**

1. Start the API server (`uv run anti-scam-server`) so the extension has something to call.
2. Open `chrome://extensions`.
3. Enable **Developer mode** (top-right).
4. Click **Load unpacked** and select the `extension/` folder.
5. Right-click a link and choose the menu item.

The extension defaults to `http://localhost:8000`. To change it (e.g. a different port), open
the extension's **options** (Details → Extension options) and set the API address.

---

## Data & storage

- Runs from the API/web/extension persist to **`./anti_scam.db`** (SQLite, gitignored).
  Override the path with `ASA_DB_PATH`.
- The **CLI** additionally writes a detailed per-run report folder under `logs/` (gitignored).

---

## Tests

```bash
uv run pytest
```

Most tests are offline and fast. A few make **live network calls**: `tests/test_analysis.py`
calls OpenAI (needs `OPENAI_API_KEY`); `tests/test_dependencies.py` and `tests/test_tools.py`
make live WHOIS lookups (fail offline).
