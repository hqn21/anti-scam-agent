# Anti-Scam Agent — MVP Design

**Date:** 2026-04-13
**Scope:** End-to-end MVP. URL in → `ScamAssessment` printed to stdout. Sensible hardcoded defaults; no observability, caching, retries, or concurrency.

## Goal

Given a URL, automatically decide whether the site is a scam / phishing site by driving a headless browser with a synthetic persona and analyzing what the site does with the submitted information, combined with domain-age signals from WHOIS. No blacklist lookups.

## Core detection principle

Legitimate sites validate inputs against real backends (payment processors, email verification, identity checks). Scam sites usually don't — they just want to collect the data. So if a site "accepts" a fabricated credit card that a real payment gateway would reject, that is strong evidence the site has no real backend validation and is harvesting data.

To preserve the integrity of this signal, the component that drives the browser (the Browsing Agent) is kept **blind**: it does not know it is part of an anti-scam system and does not know its credentials are fake. It behaves like an ordinary user trying the site. The judgment — "was this suspicious?" — lives in a separate Analysis Agent that never sees the site directly, only the Browsing Agent's objective observations plus WHOIS data.

## Architecture

Two agents with a clean blind/aware seam.

```
CLI (__main__)
   │ url
   ▼
pipeline.run_pipeline(url)
   │
   ├─► persona.generate_persona()                 → FakePersona   (Faker-backed)
   │
   ├─► browsing.run_browsing_agent(url, persona)
   │       uses browser_use.Agent                 → BrowsingResult (facts only)
   │       — blind: prompt never mentions scam/phishing/fake/detection
   │
   ├─► (extract domain from url)                  → str
   │
   ├─► analysis.run_analysis_agent(result, domain)
   │       uses openai-agents Agent,
   │       has get_domain_info as a tool          → ScamAssessment
   │
   ▼
print JSON to stdout
```

Only `pipeline.py` knows about both "fake persona" and "scam detection." The two agent modules can be read and tested in isolation.

## Module layout

```
src/anti_scam_agent/
├── __init__.py
├── __main__.py          # CLI entry: anti-scam-agent <url>
├── models.py            # FakePersona, BrowsingResult, ScamAssessment
├── persona.py           # generate_persona() via Faker
├── browsing.py          # run_browsing_agent(url, persona) -> BrowsingResult
├── analysis.py          # run_analysis_agent(browsing_result, domain) -> ScamAssessment
├── pipeline.py          # run_pipeline(url) -> ScamAssessment — orchestrator
└── tools/
    ├── __init__.py
    └── handler.py       # existing get_domain_info (unchanged)
```

Each module has a single narrow purpose.

## Models (`models.py`)

### `FakePersona` — unchanged

```
name, email, password, phone, address,
credit_card_number, credit_card_expiry, credit_card_cvv
```

### `BrowsingResult` — refactored to be facts-only

`suspicious_observations` is **removed**. The Browsing Agent must not emit subjective judgments, since that would leak the meta-goal. Instead:

- `website_summary: str`
- `outgoing_links: list[str]` — external domains linked from the page
- `login_attempted: bool`
- `login_succeeded: bool`
- `credit_card_submitted: bool`
- `credit_card_accepted: bool` — defined in the field description as: the site reported success / showed a confirmation, without redirecting to a real payment processor that returned a validation error
- `form_fields_requested: list[str]` — types of personal info the site asked for
- `unexpected_events: list[str]` — neutral rename: "anything that surprised you as a user" (redirects to unrelated domains, instant approvals, error-page loops, etc.). The Analysis Agent is the one who interprets these as suspicious.

**Important:** every Pydantic `Field(description=...)` on `BrowsingResult` will be serialized into the JSON schema shown to the Browsing Agent. Rewrite all descriptions in neutral, user-framed language — no words like "fake", "scam", "suspicious", or examples like "site accepted obviously fake CC". The existing description for `credit_card_accepted` in `models.py` ("Whether the credit card information passed the site's validation") is neutral enough to keep; `suspicious_observations`'s current description ("e.g. 'site accepted obviously fake CC'") must not carry over to `unexpected_events`.

### `ScamAssessment` — unchanged

```
is_scam: bool
confidence: float (0.0–1.0)
scam_type: str | None
reasoning: str
risk_factors: list[str]
```

## Persona generation (`persona.py`)

`generate_persona() -> FakePersona` using the `faker` library (new dep), locale `en_US`:

- `name`: `faker.name()`
- `email`: derived from name (e.g., `first.last@example.com`)
- `password`: `faker.password(length=12)`
- `phone`: `faker.phone_number()`
- `address`: `faker.address()` flattened to a single line
- `credit_card_number`: `faker.credit_card_number()` — Luhn-valid, never-issued
- `credit_card_expiry`: `faker.credit_card_expire()`
- `credit_card_cvv`: random 3-digit string

The CC is Luhn-valid so client-side JS accepts it, but it is not a real issued card, so a legitimate payment processor backend will reject it. Fresh persona each run, so nothing in the values looks like a test fixture that a sophisticated scam site could pattern-match.

## Browsing Agent (`browsing.py`)

```python
async def run_browsing_agent(url: str, persona: FakePersona) -> BrowsingResult
```

- Implementation: `browser_use.Agent` with `ChatOpenAI(model="gpt-4.1-mini")`, headless.
- **Structured output** via `browser_use.Agent`'s native `output_model_schema=BrowsingResult` parameter. **Fallback** if that API turns out to be unavailable or unreliable on the installed `browser-use>=0.11.13`: a small second-stage `openai-agents` extractor that takes the browsing `AgentHistoryList` and converts it into `BrowsingResult`. Verify the primary path during implementation; only fall back if needed.
- **Task prompt constraints (blind framing):** the prompt gives the persona values as "your" information, asks the agent to try the website as a curious user — explore, register or sign in if offered, and if the site invites a purchase, prize claim, or payment submission, complete it using the information above. Ends with instructions to fill in the `BrowsingResult` schema.
  - **Forbidden words in the prompt:** scam, phishing, detection, fake, bogus, fabricated, test, validate. The agent must not suspect anything.
- **Safety knobs:** `max_steps=25`, 5-minute wall-clock timeout. On timeout, return a partial `BrowsingResult` with safe defaults (`login_attempted=False`, `credit_card_submitted=False`, etc.) and whatever was observed so far in `website_summary` / `unexpected_events`.

## Analysis Agent (`analysis.py`)

```python
async def run_analysis_agent(browsing_result: BrowsingResult, domain: str) -> ScamAssessment
```

- Implementation: `openai-agents` SDK — `Agent(name="AnalysisAgent", tools=[get_domain_info], output_type=ScamAssessment, model="gpt-4.1")`.
- **System prompt** explains the heuristics:
  - Young domain (days_since_creation < ~90) + fake CC accepted → very high confidence scam.
  - Fake CC accepted alone → high confidence scam.
  - Heavy PII requests (national ID, bank account, full DOB) + young domain → medium.
  - Old domain + normal flow + no fake-CC acceptance → low.
  - Instructs the agent to call `get_domain_info(domain)` first, then reason over the combined signals.
- **User message:** JSON-serialized `BrowsingResult` + the target `domain`.
- Runs via `Runner.run(agent, input=...)`; returns `result.final_output_as(ScamAssessment)`.

## Pipeline (`pipeline.py`)

```python
async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    browsing_result = await run_browsing_agent(url, persona)
    domain = urlparse(url).hostname.removeprefix("www.")
    return await run_analysis_agent(browsing_result, domain)
```

No retries, no caching. Exceptions propagate to the CLI.

## CLI (`__main__.py`)

- `argparse` with a single positional `url`.
- If the URL lacks a scheme, prepend `http://`.
- `asyncio.run(run_pipeline(url))`, then `print(assessment.model_dump_json(indent=2))`.
- Wired via the existing `[project.scripts] anti-scam-agent = "anti_scam_agent.__main__:main"` entry point. `main()` itself becomes a thin sync wrapper around `asyncio.run`.

## Testing

- `tests/test_tools.py` and `tests/test_dependencies.py` — unchanged (existing live WHOIS smoke tests).
- **New `tests/test_persona.py`** — unit test for `generate_persona()`: asserts every field is non-empty and the CC number is 13–19 digits (Luhn-valid is implicit from Faker).
- **New `tests/test_analysis.py`** — calls `run_analysis_agent` with two crafted `BrowsingResult` fixtures (one obvious-scam, one obvious-legit) against real domains. Follows the existing project convention: prints assessments rather than asserting on LLM output. Run with `pytest -s`.
- **No automated test for the Browsing Agent itself** — it hits the live network and costs tokens. Manual validation via the CLI against known-good and known-scam URLs.

## Dependencies

Add to `pyproject.toml`:

- `faker` (runtime)

Existing deps (`browser-use`, `openai-agents`, `pydantic`, `python-dotenv`, `python-whois`) stay.

## Non-goals (explicitly deferred)

- Caching of WHOIS / browsing results
- Retries / error-recovery policies
- Concurrent assessment of multiple URLs
- Captcha handling
- Proxy / VPN rotation
- Structured logging, tracing, observability
- Configurable models/budgets (hardcoded for MVP)
- Web UI or API server

These are all reasonable next steps but are out of scope for the MVP pass.
