# Anti-Scam Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an end-to-end MVP that takes a URL and prints a `ScamAssessment` by running a blind browsing agent (with a Faker-generated persona) and an analysis agent over the observations.

**Architecture:** Two-agent pipeline with a clean blind/aware seam. A `BrowsingAgent` (built on `browser-use`) drives a headless browser as a normal user and emits a facts-only `BrowsingResult`. An `AnalysisAgent` (built on `openai-agents`) takes that plus domain-age signals (via the existing `get_domain_info` tool) and emits a `ScamAssessment`. `pipeline.py` is the only module that knows both halves; `__main__.py` wraps it as a CLI.

**Tech Stack:** Python 3.12, `uv`, `pydantic`, `browser-use`, `openai-agents`, `python-whois`, `faker` (new), `pytest`.

**Spec:** `docs/superpowers/specs/2026-04-13-anti-scam-agent-mvp-design.md`

---

## File map

- **Create:** `src/anti_scam_agent/persona.py` — `generate_persona() -> FakePersona`
- **Create:** `src/anti_scam_agent/browsing.py` — `async run_browsing_agent(url, persona) -> BrowsingResult`
- **Create:** `src/anti_scam_agent/analysis.py` — `async run_analysis_agent(browsing_result, domain) -> ScamAssessment`
- **Create:** `src/anti_scam_agent/pipeline.py` — `async run_pipeline(url) -> ScamAssessment`
- **Modify:** `src/anti_scam_agent/models.py` — remove `suspicious_observations`, add `unexpected_events`, neutralize field descriptions
- **Modify:** `src/anti_scam_agent/__main__.py` — argparse CLI calling `run_pipeline`
- **Modify:** `pyproject.toml` — add `faker` dependency
- **Create:** `tests/test_persona.py` — unit test for persona shape
- **Create:** `tests/test_models.py` — unit test that `BrowsingResult` field rename + neutral descriptions hold
- **Create:** `tests/test_analysis.py` — print-based end-to-end test using fixture `BrowsingResult`s

---

## Task 1: Add `faker` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add faker to dependencies**

In `pyproject.toml`, inside `[project].dependencies`, add `"faker>=30.0.0"` (any recent version is fine — pick the latest stable). Resulting block:

```toml
dependencies = [
    "browser-use>=0.11.13",
    "faker>=30.0.0",
    "openai-agents>=0.13.6",
    "pydantic>=2.12.5",
    "python-dotenv>=1.2.2",
    "python-whois>=0.9.6",
]
```

- [ ] **Step 2: Sync the environment**

Run: `uv sync`
Expected: success, `faker` appears in the resolved lockfile.

- [ ] **Step 3: Verify the import works**

Run: `uv run python -c "from faker import Faker; print(Faker().name())"`
Expected: prints a plausible human name.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add faker dependency for persona generation"
```

---

## Task 2: Refactor `models.py` — neutral `BrowsingResult`

**Files:**
- Modify: `src/anti_scam_agent/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
from anti_scam_agent.models import BrowsingResult

def test_browsing_result_has_neutral_fields():
    fields = BrowsingResult.model_fields
    # The leaky field must be gone.
    assert "suspicious_observations" not in fields
    # Its neutral replacement must exist.
    assert "unexpected_events" in fields

def test_browsing_result_descriptions_are_neutral():
    leaky_words = {"scam", "phishing", "suspicious", "fake", "fabricated"}
    for name, field in BrowsingResult.model_fields.items():
        desc = (field.description or "").lower()
        for word in leaky_words:
            assert word not in desc, (
                f"Field {name!r} description leaks meta-goal via {word!r}: {desc!r}"
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `suspicious_observations` still present / description contains `"suspicious"` or `"fake"`.

- [ ] **Step 3: Update `models.py`**

Replace the `BrowsingResult` class in `src/anti_scam_agent/models.py`:

```python
class BrowsingResult(BaseModel):
    website_summary: Annotated[str, Field(description="A concise summary of the website's apparent purpose and content.")]
    outgoing_links: Annotated[list[str], Field(description="External links (different domain) discovered on the site.")]
    login_attempted: Annotated[bool, Field(description="Whether a login or registration flow was attempted.")]
    login_succeeded: Annotated[bool, Field(description="Whether the login or registration appeared to succeed.")]
    credit_card_submitted: Annotated[bool, Field(description="Whether credit card information was submitted to the site.")]
    credit_card_accepted: Annotated[bool, Field(description="Whether the site reported the payment as successful, without redirecting to a payment processor that returned an error.")]
    form_fields_requested: Annotated[list[str], Field(description="Types of personal information the site requested (e.g. 'full name', 'ID number', 'credit card').")]
    unexpected_events: Annotated[list[str], Field(description="Anything that happened during the visit that an ordinary user would find surprising (e.g. 'redirected to an unrelated domain', 'payment confirmation page appeared instantly without a processor redirect').")]
```

Leave `FakePersona` and `ScamAssessment` untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/models.py tests/test_models.py
git commit -m "refactor(models): replace suspicious_observations with neutral unexpected_events

The Browsing Agent is fed the BrowsingResult JSON schema when producing
structured output, so any description referencing scam/phishing/fake would
leak the meta-goal and compromise the blind-user framing."
```

---

## Task 3: Persona generation (TDD)

**Files:**
- Create: `src/anti_scam_agent/persona.py`
- Test: `tests/test_persona.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_persona.py`:

```python
import re

from anti_scam_agent.models import FakePersona
from anti_scam_agent.persona import generate_persona


def test_generate_persona_returns_fake_persona():
    persona = generate_persona()
    assert isinstance(persona, FakePersona)


def test_generate_persona_fields_are_non_empty():
    persona = generate_persona()
    for field_name in FakePersona.model_fields:
        value = getattr(persona, field_name)
        assert value, f"{field_name} was empty: {value!r}"


def test_generate_persona_credit_card_shape():
    persona = generate_persona()
    digits = re.sub(r"\D", "", persona.credit_card_number)
    assert 13 <= len(digits) <= 19, f"unexpected CC length: {digits!r}"
    assert re.fullmatch(r"\d{2}/\d{2,4}", persona.credit_card_expiry), persona.credit_card_expiry
    assert re.fullmatch(r"\d{3,4}", persona.credit_card_cvv), persona.credit_card_cvv


def test_generate_persona_is_not_constant():
    # Fresh persona each call — guards against accidental module-level caching.
    a = generate_persona()
    b = generate_persona()
    assert (a.name, a.email, a.credit_card_number) != (b.name, b.email, b.credit_card_number)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_persona.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'anti_scam_agent.persona'`.

- [ ] **Step 3: Implement `persona.py`**

Create `src/anti_scam_agent/persona.py`:

```python
import random

from faker import Faker

from anti_scam_agent.models import FakePersona

_faker = Faker("en_US")


def _email_from_name(name: str) -> str:
    parts = [p.lower() for p in name.split() if p.isalpha()]
    if len(parts) < 2:
        parts = ["user", str(random.randint(1000, 9999))]
    return f"{parts[0]}.{parts[-1]}{random.randint(10, 99)}@example.com"


def generate_persona() -> FakePersona:
    name = _faker.name()
    return FakePersona(
        name=name,
        email=_email_from_name(name),
        password=_faker.password(length=12),
        phone=_faker.phone_number(),
        address=_faker.address().replace("\n", ", "),
        credit_card_number=_faker.credit_card_number(),
        credit_card_expiry=_faker.credit_card_expire(),
        credit_card_cvv=f"{random.randint(0, 999):03d}",
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_persona.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/persona.py tests/test_persona.py
git commit -m "feat: add Faker-backed persona generator"
```

---

## Task 4: Browsing Agent (implement + manual verify)

The Browsing Agent hits the live network and burns tokens, so per the spec we do not add an automated test for it. TDD here would be theater. We implement it, verify via a small manual smoke run, and rely on pipeline-level manual validation at the end.

**Files:**
- Create: `src/anti_scam_agent/browsing.py`

- [ ] **Step 1: Implement `browsing.py` using browser-use's structured output**

Create `src/anti_scam_agent/browsing.py`:

```python
import asyncio
import logging

from browser_use import Agent as BrowserAgent, ChatOpenAI
from dotenv import load_dotenv

from anti_scam_agent.models import BrowsingResult, FakePersona

load_dotenv()

logger = logging.getLogger(__name__)

_MAX_STEPS = 25
_TIMEOUT_SECONDS = 300  # 5 minutes


def _build_task_prompt(url: str, persona: FakePersona) -> str:
    """Build a strictly user-framed task prompt.

    Forbidden words: scam, phishing, detection, fake, bogus, fabricated, test.
    The agent must not suspect it is being used for anti-scam analysis.
    """
    return f"""You are visiting the website {url} as an interested first-time user.

Here is the information about you, which you may use to fill in any forms the site asks you to complete:
  - Full name: {persona.name}
  - Email: {persona.email}
  - Password (for any account you need to create): {persona.password}
  - Phone number: {persona.phone}
  - Mailing address: {persona.address}
  - Credit card number: {persona.credit_card_number}
  - Credit card expiry: {persona.credit_card_expiry}
  - Credit card security code (CVV): {persona.credit_card_cvv}

What to do, in order:
  1. Open the page and read what it is about.
  2. If the site invites you to register or sign in, do so using the information above.
  3. If the site invites you to complete a purchase, claim a prize, or enter payment details, go through with it using the information above.
  4. Follow the site's instructions as a normal curious user would. Do not skip steps the site asks you to complete.
  5. Stop when you have either completed the main flow the site offers, or there is nothing further a user would reasonably do.

When you stop, produce a summary of what happened in the structured output format. Report objective facts: what the site appears to be about, what links it has to other domains, what forms it asked you to fill in, whether registration / login / payment appeared to succeed, and anything during the visit that surprised you as a user.
"""


def _fallback_result(url: str, note: str) -> BrowsingResult:
    return BrowsingResult(
        website_summary=f"Unable to complete visit to {url}.",
        outgoing_links=[],
        login_attempted=False,
        login_succeeded=False,
        credit_card_submitted=False,
        credit_card_accepted=False,
        form_fields_requested=[],
        unexpected_events=[note],
    )


async def run_browsing_agent(url: str, persona: FakePersona) -> BrowsingResult:
    llm = ChatOpenAI(model="gpt-4.1-mini")
    task = _build_task_prompt(url, persona)

    agent = BrowserAgent(
        task=task,
        llm=llm,
        output_model_schema=BrowsingResult,
    )

    try:
        history = await asyncio.wait_for(
            agent.run(max_steps=_MAX_STEPS),
            timeout=_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("browsing agent timed out on %s", url)
        return _fallback_result(url, f"browsing timed out after {_TIMEOUT_SECONDS}s")

    structured = history.structured_output
    if isinstance(structured, BrowsingResult):
        return structured
    if isinstance(structured, dict):
        return BrowsingResult.model_validate(structured)

    logger.warning("browsing agent returned no structured output; using fallback")
    return _fallback_result(url, "browsing agent produced no structured output")
```

> **Note on browser-use API:** the `output_model_schema=` kwarg and `history.structured_output` attribute reflect the current `browser-use>=0.11.13` API. If these names differ on the installed version, resolve them by consulting `uv run python -c "import browser_use; help(browser_use.Agent)"` and `help(browser_use.agent.views.AgentHistoryList)` — adjust the two call sites only (constructor kwarg + result accessor). Do not weaken the blind framing in the prompt to work around SDK differences.

- [ ] **Step 2: Verify the module imports cleanly**

Run: `uv run python -c "from anti_scam_agent.browsing import run_browsing_agent; print(run_browsing_agent)"`
Expected: prints a function object, no import error.

- [ ] **Step 3: Smoke-run the agent against a harmless known-good page**

Run (requires `OPENAI_API_KEY` in `.env`, uses tokens):

```bash
uv run python -c "
import asyncio
from anti_scam_agent.persona import generate_persona
from anti_scam_agent.browsing import run_browsing_agent

async def main():
    persona = generate_persona()
    result = await run_browsing_agent('https://example.com', persona)
    print(result.model_dump_json(indent=2))

asyncio.run(main())
"
```

Expected: prints a populated `BrowsingResult`. `login_attempted`, `credit_card_submitted` should both be `False` for `example.com`; `website_summary` should describe it as a placeholder/example page.

- [ ] **Step 4: Commit**

```bash
git add src/anti_scam_agent/browsing.py
git commit -m "feat: add blind browsing agent over browser-use

Drives a headless browser with a persona as if a normal user were trying
the site. Prompt is strictly user-framed — no mention of scam/phishing/fake —
so the LLM cannot pattern its behavior around the detection goal."
```

---

## Task 5: Analysis Agent (TDD with fixtures)

The analysis agent calls OpenAI, so the test prints rather than asserts on the judgment (matches existing project convention in `tests/test_tools.py`). TDD discipline is preserved by having the test drive module creation.

**Files:**
- Create: `src/anti_scam_agent/analysis.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_analysis.py`:

```python
import asyncio

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.models import BrowsingResult, ScamAssessment

OBVIOUS_SCAM = BrowsingResult(
    website_summary="Site claims the user has won a lottery and asks for immediate payment of a small processing fee to release a large prize.",
    outgoing_links=["https://some-unrelated-cdn.biz"],
    login_attempted=True,
    login_succeeded=True,
    credit_card_submitted=True,
    credit_card_accepted=True,
    form_fields_requested=[
        "full name", "national ID", "bank account number",
        "credit card number", "credit card CVV", "mother's maiden name",
    ],
    unexpected_events=[
        "payment confirmation page appeared instantly without redirecting to a payment processor",
        "after login, the site asked for additional personal information unrelated to the prize claim",
    ],
)

OBVIOUS_LEGIT = BrowsingResult(
    website_summary="Simple placeholder domain with a short description and no interactive forms.",
    outgoing_links=["https://www.iana.org/domains/example"],
    login_attempted=False,
    login_succeeded=False,
    credit_card_submitted=False,
    credit_card_accepted=False,
    form_fields_requested=[],
    unexpected_events=[],
)


def _run(result: BrowsingResult, domain: str) -> ScamAssessment:
    return asyncio.run(run_analysis_agent(result, domain))


def test_analysis_agent_returns_assessment_for_scam_fixture():
    assessment = _run(OBVIOUS_SCAM, "example.com")
    print("\n[SCAM FIXTURE]")
    print(assessment.model_dump_json(indent=2))
    assert isinstance(assessment, ScamAssessment)
    assert 0.0 <= assessment.confidence <= 1.0


def test_analysis_agent_returns_assessment_for_legit_fixture():
    assessment = _run(OBVIOUS_LEGIT, "example.com")
    print("\n[LEGIT FIXTURE]")
    print(assessment.model_dump_json(indent=2))
    assert isinstance(assessment, ScamAssessment)
    assert 0.0 <= assessment.confidence <= 1.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analysis.py -v -s`
Expected: FAIL — `ModuleNotFoundError: No module named 'anti_scam_agent.analysis'`.

- [ ] **Step 3: Implement `analysis.py`**

Create `src/anti_scam_agent/analysis.py`:

```python
from agents import Agent, Runner
from dotenv import load_dotenv

from anti_scam_agent.models import BrowsingResult, ScamAssessment
from anti_scam_agent.tools import get_domain_info

load_dotenv()

_SYSTEM_PROMPT = """You are a fraud analyst. You are given a structured report from a colleague who visited a target website as an ordinary user, plus the site's domain. Your job is to judge whether the site is a scam / phishing operation, with reasoning.

Before producing your judgment, call the `get_domain_info` tool with the target domain to learn how long ago the domain was registered and when it expires.

Heuristics (combine them — no single signal is definitive):
  - A legitimate site validates payment details against a real payment processor. If the report shows `credit_card_submitted=true` and `credit_card_accepted=true` but describes an instant success without a processor redirect, treat this as strong evidence of scam: the site accepted card details that a real processor would have rejected.
  - Very young domains (days_since_creation < 90) combined with any payment acceptance or heavy PII collection are strong scam signals.
  - Old, long-expiration domains with normal user flows are low-risk.
  - Requests for unusually sensitive PII (national ID, bank account, mother's maiden name) alongside other red flags compound risk.
  - Unexpected redirects to unrelated domains after submitting data are suspicious.

Return a ScamAssessment:
  - is_scam: your best binary judgment.
  - confidence: 0.0–1.0, calibrated — not every scam warrants 0.99.
  - scam_type: a short category like 'phishing', 'fake lottery', 'credit card harvesting', or None if not a scam.
  - reasoning: a paragraph citing specific observations from the browsing report and domain info.
  - risk_factors: the concrete items from the inputs that drove your judgment.
"""


async def run_analysis_agent(browsing_result: BrowsingResult, domain: str) -> ScamAssessment:
    agent = Agent(
        name="AnalysisAgent",
        instructions=_SYSTEM_PROMPT,
        tools=[get_domain_info],
        output_type=ScamAssessment,
        model="gpt-4.1",
    )

    user_message = (
        f"Target domain: {domain}\n\n"
        f"Browsing report (JSON):\n{browsing_result.model_dump_json(indent=2)}"
    )

    result = await Runner.run(agent, input=user_message)
    return result.final_output_as(ScamAssessment)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_analysis.py -v -s`
Expected: PASS (both tests). The printed `SCAM FIXTURE` assessment should have `is_scam=true` with high confidence; the `LEGIT FIXTURE` should be the opposite. If the LLM gets it wrong, revisit the prompt — do not weaken the assertions.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/analysis.py tests/test_analysis.py
git commit -m "feat: add analysis agent that judges browsing observations"
```

---

## Task 6: Pipeline orchestrator

**Files:**
- Create: `src/anti_scam_agent/pipeline.py`

This module has no automated test — it is pure wiring and is covered by end-to-end manual validation in Task 8.

- [ ] **Step 1: Implement `pipeline.py`**

Create `src/anti_scam_agent/pipeline.py`:

```python
from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.models import ScamAssessment
from anti_scam_agent.persona import generate_persona


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    browsing_result = await run_browsing_agent(url, persona)
    domain = _extract_domain(url)
    return await run_analysis_agent(browsing_result, domain)
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `uv run python -c "from anti_scam_agent.pipeline import run_pipeline; print(run_pipeline)"`
Expected: prints a function object, no import error.

- [ ] **Step 3: Commit**

```bash
git add src/anti_scam_agent/pipeline.py
git commit -m "feat: add pipeline orchestrator"
```

---

## Task 7: CLI

**Files:**
- Modify: `src/anti_scam_agent/__main__.py`

- [ ] **Step 1: Replace `__main__.py`**

Overwrite `src/anti_scam_agent/__main__.py`:

```python
import argparse
import asyncio
import sys

from anti_scam_agent.pipeline import run_pipeline


def _normalize_url(raw: str) -> str:
    if "://" in raw:
        return raw
    return f"http://{raw}"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anti-scam-agent",
        description="Assess whether a website is a scam / phishing site.",
    )
    parser.add_argument("url", help="Target URL or bare domain.")
    args = parser.parse_args()

    url = _normalize_url(args.url)
    assessment = asyncio.run(run_pipeline(url))
    print(assessment.model_dump_json(indent=2))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify the CLI parses arguments**

Run: `uv run anti-scam-agent --help`
Expected: prints the argparse help text, exits 0.

- [ ] **Step 3: Commit**

```bash
git add src/anti_scam_agent/__main__.py
git commit -m "feat: wire CLI entry point to pipeline"
```

---

## Task 8: End-to-end manual validation

No code changes. Validate the built system against two real URLs.

- [ ] **Step 1: Run against a known-legitimate site**

Run: `uv run anti-scam-agent https://example.com`
Expected: a `ScamAssessment` JSON with `is_scam=false` and low confidence. Watch for clean completion with no exceptions and no "fallback" markers in `unexpected_events`.

- [ ] **Step 2: Run against a site you consider plausibly scammy**

Pick a site from a current scam-reporter feed (e.g. a freshly reported phishing URL from `urlscan.io` or similar) and run:

```bash
uv run anti-scam-agent <suspect-url>
```

Expected: a well-reasoned `ScamAssessment`. If the site is clearly scam-shaped, `is_scam=true` with high confidence and `reasoning` citing specific concrete observations from the browsing report plus the domain-age signals.

- [ ] **Step 3: Inspect for blind-framing leaks in the browsing output**

Re-read the `BrowsingResult` portion of both runs (visible in logs, or add a temporary print in `pipeline.py` if needed). Confirm none of the fields contain words like "scam", "phishing", "fake", "fraudulent", or "suspicious". If any appear, the task prompt or field descriptions leaked the meta-goal and need tightening.

- [ ] **Step 4: No commit needed**

This is a validation step only. If any issues are found, file them as follow-up tasks — do not silently patch.

---

## Done when

- `uv run pytest` passes all of `test_models.py`, `test_persona.py`, `test_analysis.py` (plus the pre-existing tests).
- `uv run anti-scam-agent <url>` prints a populated `ScamAssessment` for arbitrary URLs.
- Task 8 validation confirms sensible behavior on known-legit and known-scam inputs, and the Browsing Agent's output shows no meta-goal leakage.
