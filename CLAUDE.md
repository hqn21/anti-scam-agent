# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

Anti-Scam Agent detects phishing / scam websites (e-commerce, lottery / prize-claim, credit-card harvesting, etc.) **without blacklists**, by exploiting a weakness of scam sites: they rarely implement real backend validation because they have no real user base or payment processor. A legitimate site rejects an obviously fake credit card; a scam site "accepts" it. That acceptance is the canonical detection signal.

The system is two LLM agents wired in sequence by `pipeline.py`:

1. **Browsing Agent** (`browsing.py`) — drives a real browser via `browser-use` as if it were an ordinary first-time user, filling forms with a synthetic `FakePersona`. It is intentionally kept **unaware** that it is part of an anti-scam system or that its credentials are fabricated, and returns a structured `BrowsingResult`.
2. **Analysis Agent** (`analysis.py`) — consumes the `BrowsingResult` plus `DomainInfo` (WHOIS age/expiry) and emits a `ScamAssessment` with calibrated reasoning, using the `get_domain_info` tool.

## The blind-browser invariant (most important constraint)

The detection signal collapses if the Browsing Agent realizes it is probing a scam site — it might then refuse, behave defensively, or report through an "anti-fraud" lens instead of as a naive user. So **the meta-goal must never leak into anything the Browsing Agent sees**: not its task prompt (`_build_task_prompt`), and not the field descriptions of `BrowsingResult` (which the browser LLM sees as its output schema).

This is enforced by tests: `tests/test_models.py` asserts that no `BrowsingResult` field description contains the words *scam, phishing, suspicious, fake, fabricated*, and that the neutral field name `unexpected_events` is used (not a leaky name like `suspicious_observations`). When adding or renaming `BrowsingResult` fields, keep descriptions in plain user-facing language. The Analysis Agent's prompt, by contrast, is free to name the fraud framing explicitly.

## Commands

This project uses `uv` (Python >=3.12).

- Install / sync deps: `uv sync`
- Run the CLI: `uv run anti-scam-agent <url-or-bare-domain>` (bare domains are normalized to `http://`)
- Run all tests: `uv run pytest`
- Run a single test with output: `uv run pytest tests/test_tools.py::test_get_domain_info -s`

Runtime requires `OPENAI_API_KEY` in `.env` (see `.env.example`); both `browser-use` and `openai-agents` read it via `load_dotenv()`. `browser-use` is pinned to a fork (see `[tool.uv.sources]` in `pyproject.toml`) for OpenAI-pin compatibility.

Models are hardcoded per agent: browsing uses `gpt-4.1-mini`, analysis uses `gpt-4.1`.

## Architecture notes

- **Two SDKs, deliberately.** Browsing uses `browser_use.Agent` + `ChatOpenAI`; analysis uses `openai-agents` (`agents.Agent` / `Runner`). Both coerce their LLM output into a Pydantic model (`output_model_schema` / `output_type`). Don't unify these — they serve different stages.
- **`models.py` is the contract between stages.** `FakePersona` → browsing input; `BrowsingResult` → the structured observation; `ScamAssessment` → final judgment. New pipeline stages should flow through these shapes rather than inventing parallel ones.
- **Browsing is failure-tolerant by design.** `run_browsing_agent` wraps the run in a timeout (`_TIMEOUT_SECONDS`) and step cap (`_MAX_STEPS`), and on *any* exception, timeout, or missing/unparseable structured output it returns a neutral `_fallback_result` instead of raising — so the pipeline always reaches the Analysis Agent. Preserve this: the analysis stage should never be skipped because browsing hiccuped.
- **Tools convention** (`tools/handler.py`): each tool is a `@function_tool`-decorated wrapper paired with a plain `_name` implementation (`get_domain_info` / `_get_domain_info`). Tests call the underscore version to bypass the SDK wrapper. `tools/__init__.py` re-exports both — keep that pattern when adding tools.
- **Domain date math** in `_get_domain_info` normalizes WHOIS creation/expiration dates to `Asia/Taipei` before diffing, so `days_since_creation` / `days_until_expiration` are relative to that zone. Keep new time-based signals consistent with this.

## Testing notes

- `tests/test_analysis.py` makes **live OpenAI calls** (needs `OPENAI_API_KEY`); it runs the real Analysis Agent against hand-written scam/legit `BrowsingResult` fixtures and asserts shape/range, not a specific verdict.
- `tests/test_dependencies.py` and `tests/test_tools.py` make **live WHOIS network calls** (`haoquan.me`, `example.com`) — they fail offline. `test_dependencies.py` pins the exact `python-whois` response shape the handler relies on; if upstream changes it, update both.
- `test_persona.py` and `test_models.py` are pure/offline and fast.
