# Anti-Scam Agent

Anti-Scam Agent detects phishing and scam websites (fake e-commerce, lottery and prize-claim,
credit-card harvesting) without blacklists. It exploits a structural weakness of scam sites:
they rarely implement real backend validation, because they have no real user base or payment
processor. A legitimate site runs a real payment processor that rejects a fabricated card with
an explicit "card declined / invalid" error. A scam site, having no processor, accepts it or
moves on without a clear card error. That absence of an explicit card decline is the canonical
detection signal.

The system is two LLM agents wired in sequence:

1. **Browsing Agent** drives a real browser (via `browser-use`) as an ordinary first-time user,
   filling forms with a synthetic persona. It stays unaware that it is part of an anti-scam
   system.
2. **Analysis Agent** consumes the browsing observation plus static signals (WHOIS domain age,
   TLS certificate, DNS) and emits a calibrated scam assessment.

Around the core pipeline, the repo adds a CLI, an HTTP API (FastAPI) that runs analyses through
a serialized background worker, a React web app, SQLite persistence, and a Chrome extension for
checking links from the right-click menu. Everything runs locally; this is a demo, not a hosted
service.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (Python >= 3.12) for the backend, CLI, and API.
- [Node.js](https://nodejs.org/) with `npm`, only to build or run the web app.
- A `.env` file in the repo root (copy `.env.example`) with:
  - `OPENAI_API_KEY`, required (both `browser-use` and `openai-agents` read it).
  - `AGENTMAIL_API_KEY`, required. The persona's email is routed through an AgentMail inbox so
    the Browsing Agent can read verification codes mid-flow. The app fails fast if it is unset.

```bash
uv sync                # install Python dependencies
cp .env.example .env   # then fill in your API keys
```

## 1. CLI

Analyze a single URL or bare domain (bare domains are normalized to `http://`):

```bash
uv run anti-scam-agent shop.example.com
uv run anti-scam-agent https://shop.example.com --verbose
```

It prints the assessment JSON to stdout and writes a per-run report folder under `logs/` (the
path goes to stderr). `--verbose` (or `ASA_LOG_VERBOSE=1`) inlines the full agent thinking into
the report.

## 2. API server

```bash
uv run anti-scam-server                          # http://127.0.0.1:8000
uv run anti-scam-server --host 0.0.0.0 --port 8000
```

Configurable via flags or env vars: `ASA_HOST`, `ASA_PORT`, and `ASA_DB_PATH` (the SQLite file,
default `./anti_scam.db`).

Each analysis drives a real browser and takes roughly 1 to 3 minutes, so the API is
asynchronous: submit a job, then poll for the result. Runs are serialized (one at a time) to
keep real-browser usage and API cost under control.

| Method & path | Purpose |
|---|---|
| `POST /api/analyze` | Body `{"url": "...", "source": "web"\|"extension"}`. Returns `{id, status}` (202). |
| `GET /api/analyze/{id}` | Poll job status; includes the curated report once `status` is `done`. |
| `GET /api/analyses` | History list (`?limit=&offset=&status=`). |
| `GET /api/analyses/{id}` | Full curated report for one run. |
| `GET /api/stats` | Dashboard aggregates. |

When a built web app exists at `web/dist/`, the server also serves it at `/`.

## 3. Web app

React, Vite, TypeScript, and Tailwind. It has a Dashboard (totals, verdict distribution, scam
types, cost and time), a History list (every past run, with click-through to its report), a
Query page (enter a URL and watch a live elapsed-time counter until the report is ready), and a
per-run Report view. Reports lead with the headline signal, whether the site ever produced an
explicit card decline, and omit the noisy step-by-step browsing transcript.

Install web dependencies once:

```bash
npm --prefix web install
```

### Option A: all-in-one demo (recommended)

Build the app and let the API server serve it:

```bash
npm --prefix web run build   # produces web/dist/
uv run anti-scam-server      # then open http://localhost:8000
```

### Option B: dev mode (hot reload)

Run the API and the Vite dev server side by side (Vite proxies `/api` to `:8000`):

```bash
uv run anti-scam-server      # terminal 1
npm --prefix web run dev     # terminal 2, then open http://localhost:5173
```

## 4. Chrome extension

Right-click any link to run a check from the context menu. A panel appears in the bottom-right
corner listing each check: a live elapsed-time counter while it runs, then a verdict badge, the
headline card-decline takeaway, and a link to the full report. Items can be dismissed
individually while other checks keep running, and a clear button removes finished ones. The
panel is driven by the extension's background worker via `chrome.storage`, so progress survives
closing an item or navigating away. Checks go through the same API, so they also appear in the
web app's History.

Load it unpacked:

1. Start the API server (`uv run anti-scam-server`) so the extension has something to call.
2. Open `chrome://extensions`.
3. Enable Developer mode (top-right).
4. Click Load unpacked and select the `extension/` folder.
5. Right-click a link and choose the menu item.

The extension defaults to `http://localhost:8000`. To change it (for example a different port),
open the extension's options (Details, then Extension options) and set the API address.

## Data & storage

- Runs from the API, web app, and extension persist to `./anti_scam.db` (SQLite, gitignored).
  Override the path with `ASA_DB_PATH`.
- The CLI also writes a detailed per-run report folder under `logs/` (gitignored).

## Tests

```bash
uv run pytest
```

Most tests are offline and fast. A few make live network calls: `tests/test_analysis.py` calls
OpenAI (needs `OPENAI_API_KEY`); `tests/test_dependencies.py` and `tests/test_tools.py` make
live WHOIS lookups (they fail offline).
