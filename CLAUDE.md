# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

Anti-Scam Agent detects phishing / scam websites (e-commerce, lottery / prize-claim, credit-card harvesting, etc.) **without blacklists**, by exploiting a weakness of scam sites: they rarely implement real backend validation because they have no real user base or payment processor. A legitimate site runs a real payment processor that rejects a fabricated card with an explicit "card declined / invalid" error; a scam site, having no processor, accepts it or moves on without a clear card error. That **absence of an explicit card decline** — captured by the `payment_explicitly_declined` field of `BrowsingResult` — is the canonical detection signal.

The system is two LLM agents wired in sequence by `pipeline.py`:

1. **Browsing Agent** (`browsing.py`) — drives a real browser via `browser-use` as if it were an ordinary first-time user, filling forms with a synthetic `FakePersona`. It is intentionally kept **unaware** that it is part of an anti-scam system or that its credentials are fabricated. `run_browsing_agent` returns a tuple `(BrowsingResult, StageReport)` — the structured observation plus its token/cost/time telemetry (see **Run reporting** below).
2. **Analysis Agent** (`analysis.py`) — consumes the `BrowsingResult` and a `StaticSignals` bundle (WHOIS age/expiry, TLS certificate info, DNS) computed by `signals.collect_static_signals(url)`, and emits a `ScamAssessment` with calibrated reasoning. It reads all signals from its input; it calls no tools. `run_analysis_agent` returns a tuple `(ScamAssessment, StageReport)`.

`run_pipeline(url, verbose=False)` wires the stages, times each one, and writes a per-run report under `logs/` (see **Run reporting**).

**AgentMail is mandatory.** The pipeline routes the persona's email through one of the rotating AgentMail inboxes (`AGENTMAIL_INBOXES`) before browsing — the blind agent just sees an ordinary address — and gives the Browsing Agent a neutral `read_email_inbox` tool (backed by `email_evidence.read_inbox_text`) so it can read a verification code or confirmation link mid-flow and finish a registration or checkout that would otherwise stall. `make_client()` raises if `AGENTMAIL_API_KEY` is unset. There is no post-hoc email-evidence signal: receiving mail — even authenticated mail — does not exonerate a site, since scam sites run email verification too.

## The blind-browser invariant (most important constraint)

The detection signal collapses if the Browsing Agent realizes it is probing a scam site — it might then refuse, behave defensively, or report through an "anti-fraud" lens instead of as a naive user. So **the meta-goal must never leak into anything the Browsing Agent sees**: not its task prompt (`_build_task_prompt`), and not the field descriptions of `BrowsingResult` (which the browser LLM sees as its output schema).

This is enforced by tests: `tests/test_models.py` asserts that no `BrowsingResult` field description contains the words *scam, phishing, suspicious, fake, fabricated* (nor the implementation-leaking terms *luhn*, *card_tier*), and that the neutral field name `unexpected_events` is used (not a leaky name like `suspicious_observations`). The same neutrality is enforced for the `payment_explicitly_declined` field (it is a `BrowsingResult` field, so the same `test_models.py` loop covers it) and for the `read_email_inbox` tool description (guarded in `tests/test_browsing.py`). When adding or renaming `BrowsingResult` fields, keep descriptions in plain user-facing language. The Analysis Agent's prompt, by contrast, is free to name the fraud framing explicitly.

## Run reporting (observability)

Every run emits a **data-derived (no-LLM)** report for tracing and cost tracking, assembled and written by `reporting.py` (it calls no LLM and never touches agent prompts — only formats numbers/strings already produced). Per run it writes a folder `logs/<ISO8601-localtime>_<domain>/` containing:

- `report.log` — human-readable summary: per-step tokens/cost/time for browsing, per-call for analysis, per-stage subtotals, and run totals. Concise by default; `--verbose` (or `ASA_LOG_VERBOSE=1`) additionally inlines each step's full transcribed agent *thinking*.
- `report.json` — the same data, structured (for later cross-run roll-ups).
- `debug.log` — the run's full Python logging stream (browser-use internals, timeout/salvage warnings, tracebacks), captured by attaching a root-logger handler for the run and detaching it after.
- `prediction.yml` — the at-a-glance result: `verdict` (the five `Verdict` labels), `is_scam`, and `scam_type`.

It also appends one JSON line per run to a cross-run ledger `logs/predictions.jsonl` (timestamp, target, url, verdict, is_scam, scam_type) so many runs can be scanned at once. `logs/` is gitignored.

Implementation details to preserve: cost comes from a hand-maintained `_PRICING` table in `reporting.py` (currently `gpt-4.1` and `gpt-4.1-mini`); an unknown model yields `cost_usd=None` rendered `(pricing unknown)`, never a guessed price. Browsing per-step token attribution buckets each `token_cost_service.usage_history` entry into the step whose `[step_start_time, step_end_time]` window contains its timestamp (robust to multi-LLM-call steps); calls outside every window go to a per-stage "other" bucket, so totals stay self-consistent. YAML for `prediction.yml` is hand-rolled (no PyYAML dependency).

## Commands

This project uses `uv` (Python >=3.12).

- Install / sync deps: `uv sync`
- Run the CLI: `uv run anti-scam-agent <url-or-bare-domain>` (bare domains are normalized to `http://`); add `--verbose` (or set `ASA_LOG_VERBOSE=1`) to inline full agent thinking into `report.log`. The CLI prints the assessment JSON to stdout and the report path to stderr; each run also writes a report folder under `logs/` (see **Run reporting**).
- Run all tests: `uv run pytest`
- Run a single test with output: `uv run pytest tests/test_tools.py::test_get_domain_info -s`

Runtime requires `OPENAI_API_KEY` **and** `AGENTMAIL_API_KEY` in `.env` (see `.env.example`); both `browser-use` and `openai-agents` read the OpenAI key via `load_dotenv()`, and `make_client()` raises if the AgentMail key is missing. `browser-use` is pinned to a fork (see `[tool.uv.sources]` in `pyproject.toml`) for OpenAI-pin compatibility.

Models are hardcoded per agent: browsing uses `gpt-4.1`, analysis uses `gpt-4.1`.

## Architecture notes

- **Two SDKs, deliberately.** Browsing uses `browser_use.Agent` + `ChatOpenAI`; analysis uses `openai-agents` (`agents.Agent` / `Runner`). Both coerce their LLM output into a Pydantic model (`output_model_schema` / `output_type`). Don't unify these — they serve different stages.
- **`models.py` is the contract between stages.** `FakePersona` → browsing input; `BrowsingResult` → the structured observation; `ScamAssessment` → final judgment. New pipeline stages should flow through these shapes rather than inventing parallel ones.
- **Browsing is failure-tolerant by design.** `run_browsing_agent` wraps the run in a timeout (`_TIMEOUT_SECONDS`) and step cap (`_MAX_STEPS`), and on *any* exception, timeout, or missing/unparseable structured output it returns `_salvage_result_from_history(...)` (which reconstructs a neutral `BrowsingResult` from whatever the agent managed to do) instead of raising — so the pipeline always reaches the Analysis Agent. Every path still returns the `(BrowsingResult, StageReport)` tuple: telemetry is extracted from the agent on success *and* failure paths, and `_browsing_stage_report` is itself exception-proof so reporting can never sink the salvaged result. Preserve this: the analysis stage should never be skipped because browsing hiccuped.
- **`reporting.py` is pure and standalone.** It defines the report shapes (`LLMCallMetrics`, `StepRecord`, `StageReport`, `RunReport`) and all rendering/writing. It must stay LLM-free and must never feed into anything an agent sees (it only consumes already-produced data) — so it cannot weaken the blind-browser invariant. New run-level metrics belong here, flowing through `StageReport`/`RunReport` rather than parallel shapes.
- **Static-signal collectors are plain functions, not agent tools.** `signals.collect_static_signals(url)` calls `_get_domain_info` (in `tools/handler.py`), `_get_tls_info`, and `_get_dns_info` (both in `signals.py`), each routed through `_safe(...)` so a failed lookup yields `None` rather than raising, and returns a `StaticSignals` bundle. Tests call these underscore functions directly. The Analysis Agent consumes the bundle as input — it has no tools. `tools/__init__.py` re-exports `DomainInfo`, `_domain_info_from_whois`, and `_get_domain_info`.
- **Browsing custom actions** (`browsing.py`): the Browsing Agent's extra tools are registered with browser-use's `Tools().action(...)` — `read_email_inbox` (mid-flow inbox reads) and `click_by_visible_text` (a shadow-DOM-aware CDP click for late-rendered / unindexable buttons). Keep their descriptions in neutral, user-facing language (the blind-browser invariant; the `read_email_inbox` description is guarded by `tests/test_browsing.py`).
- **Domain date math** in `_get_domain_info` normalizes WHOIS creation/expiration dates to `Asia/Taipei` before diffing, so `days_since_creation` / `days_until_expiration` are relative to that zone. Keep new time-based signals consistent with this.

## Testing notes

- `tests/test_analysis.py` makes **live OpenAI calls** (needs `OPENAI_API_KEY`); it runs the real Analysis Agent against hand-written scam/legit `BrowsingResult` fixtures and asserts shape (a `Verdict` enum member and a boolean `is_scam`), not a specific verdict. It unpacks the `(ScamAssessment, StageReport)` tuple and also asserts the analysis stage recorded usage.
- `tests/test_dependencies.py` and `tests/test_tools.py` make **live WHOIS network calls** (`haoquan.me`, `example.com`) — they fail offline. `test_dependencies.py` pins the exact `python-whois` response shape the handler relies on; if upstream changes it, update both.
- The rest are pure/offline and fast: `test_models.py`, `test_persona.py`, `test_browsing.py` (guards the neutral tool description), `test_signals.py`, `test_email_evidence.py`, `test_pipeline.py` (monkeypatches the agents/AgentMail and stubs the report writes so it stays hermetic), and `test_reporting.py` (cost math, time-window attribution invariants, renderers, and the prediction YAML/ledger).
