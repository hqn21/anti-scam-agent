# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

Anti-Scam Agent detects phishing / scam websites (e-commerce, lottery / invoice-prize claim, etc.) without blacklists, by exploiting a key weakness of scam sites: they rarely implement real backend validation because they have no real user base. The system is split into two LLM agents:

1. **Browsing Agent** — drives a headless browser as if it were an ordinary user, using a pre-built `FakePersona`. It is intentionally kept *unaware* that it is part of an anti-scam system or that its credentials are fabricated; it should behave like a real user following the site's instructions (register, log in, submit payment, etc.) and record observations into a `BrowsingResult`.
2. **Analysis Agent** — consumes the `BrowsingResult` plus domain-level signals (e.g. `DomainInfo` from WHOIS) and emits a `ScamAssessment` with reasoning.

The canonical detection signal: if clearly fake input (e.g. a fabricated credit card) is "accepted" by the site, that is strong evidence of phishing, since a legitimate site would have rejected it. Preserving this blind-user framing in the Browsing Agent's prompts and tools matters — leaking the meta-goal to the browsing LLM would compromise the signal.

## Commands

This project uses `uv` for dependency and environment management (Python >=3.12).

- Install / sync deps: `uv sync`
- Run the CLI entry point: `uv run anti-scam-agent` (wired via `pyproject.toml` to `anti_scam_agent.__main__:main`)
- Run all tests: `uv run pytest`
- Run a single test: `uv run pytest tests/test_tools.py::test_get_domain_info -s` (use `-s` to see the `print` output these tests rely on)

Runtime requires `OPENAI_API_KEY` in `.env` (see `.env.example`). Both `browser-use` (via `ChatOpenAI`) and `openai-agents` consume it.

## Architecture

- `src/anti_scam_agent/models.py` — Pydantic contracts for the agent pipeline. `FakePersona` is the synthetic identity fed to the browsing agent; `BrowsingResult` is the structured observation returned from a browsing session (form fields requested, whether fake CC was accepted, etc.); `ScamAssessment` is the final judgment (`is_scam`, `confidence`, `scam_type`, `reasoning`, `risk_factors`). New pipeline stages should plug into these shapes rather than inventing parallel ones.
- `src/anti_scam_agent/tools/handler.py` — Tools exposed to the LLM agents. Public tools are wrapped with `@function_tool(defer_loading=True)` from the `agents` SDK (openai-agents); the module convention is to pair each tool with a plain `_name` helper (e.g. `get_domain_info` / `_get_domain_info`) so tests can call the underlying logic without the function-tool wrapper. `tools/__init__.py` re-exports both the decorated tool and the underscore helper — preserve that pattern when adding tools.
- Domain date math in `_get_domain_info` normalizes to `Asia/Taipei` before diffing, so `days_since_creation` / `days_until_expiration` are relative to that zone. Keep new time-based signals consistent.
- `_browse_website` is the browser-automation entry point (async, uses `browser_use.Agent` + `ChatOpenAI`) and is the intended home for the impersonation/probing logic — it is currently a stub.

## Testing notes

- `tests/test_dependencies.py` is a live smoke test that hits the real WHOIS network for `haoquan.me` — expect it to fail offline. It asserts the `python-whois` response shape the handler relies on, so if upstream changes it, update both.
- `tests/test_tools.py` also makes real WHOIS calls and prints rather than asserts; run with `-s` when debugging.
