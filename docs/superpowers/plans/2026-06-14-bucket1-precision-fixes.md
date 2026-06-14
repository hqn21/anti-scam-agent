# Bucket 1 — Precision Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the coupled false-positive path (line-45 "no error = success" × Luhn-valid card) by introducing four-state outcomes, a two-tier (Luhn-invalid then -valid) card flow, an abstain path for failed visits, and persona hardening — without leaking the meta-goal to the blind Browsing Agent.

**Architecture:** `BrowsingResult` gains a neutral 4-state `Outcome` enum (`login_outcome`/`payment_outcome`) plus `visit_completed`; `FakePersona` carries a Luhn-invalid primary card and a Luhn-valid fallback; `pipeline.py` orchestrates up to two browsing runs (the second only when the first payment is rejected) and passes an out-of-band `card_tier` to the Analysis Agent so it can weight invalid-accepted (strong) vs valid-accepted (weak). The blind invariant is preserved: `card_tier` and Luhn-validity never appear in any agent-visible text or in the `BrowsingResult` schema.

**Tech Stack:** Python 3.12, Pydantic v2, `browser-use`, `openai-agents`, Faker, pytest, `uv`.

---

## File Structure

- `src/anti_scam_agent/models.py` — add `Outcome` enum; change `BrowsingResult` (drop `login_succeeded`/`credit_card_accepted`, add `login_outcome`/`payment_outcome`/`visit_completed`); add `credit_card_number_luhn_valid` to `FakePersona`.
- `src/anti_scam_agent/persona.py` — Luhn-invalid primary + Luhn-valid fallback, Amex 4-digit CVV, strip phone extension.
- `src/anti_scam_agent/browsing.py` — fix prompt typos, delete the "no error = success" line, add neutral outcome-mapping guidance, update `_fallback_result`.
- `src/anti_scam_agent/pipeline.py` — two-tier card orchestration, pass `card_tier` to analysis.
- `src/anti_scam_agent/analysis.py` — prompt uses the enums + `card_tier`; abstain when `visit_completed=False`; new `card_tier` parameter.
- `tests/test_models.py`, `tests/test_persona.py`, `tests/test_pipeline.py` (new), `tests/test_browsing.py` (new).

**Note:** `FakePersona.email` stays Faker-generated in this bucket; the AgentMail inbox swap is Bucket 3.

---

### Task 1: `Outcome` enum and `BrowsingResult` four-state fields

**Files:**
- Modify: `src/anti_scam_agent/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
from anti_scam_agent.models import Outcome


def test_outcome_enum_values_are_neutral():
    leaky_words = {"scam", "phishing", "suspicious", "fake", "fabricated"}
    for member in Outcome:
        assert member.value not in leaky_words
    assert {m.value for m in Outcome} == {
        "not_attempted",
        "failed",
        "unclear",
        "succeeded",
    }


def test_browsing_result_uses_four_state_outcomes():
    fields = BrowsingResult.model_fields
    # Old leaky-by-coupling booleans are gone.
    assert "login_succeeded" not in fields
    assert "credit_card_accepted" not in fields
    # New four-state fields exist with the Outcome type.
    assert fields["login_outcome"].annotation is Outcome
    assert fields["payment_outcome"].annotation is Outcome
    # Abstain flag for the fallback path.
    assert "visit_completed" in fields
    # The "was it tried at all" booleans are retained.
    assert "login_attempted" in fields
    assert "credit_card_submitted" in fields
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Outcome'`.

- [ ] **Step 3: Implement the enum and field changes**

In `src/anti_scam_agent/models.py`, add at the top with the other imports:

```python
from enum import Enum
```

Add the enum above `BrowsingResult`:

```python
class Outcome(str, Enum):
    not_attempted = "not_attempted"
    failed = "failed"
    unclear = "unclear"
    succeeded = "succeeded"
```

Replace the `BrowsingResult` class body so it reads:

```python
class BrowsingResult(BaseModel):
    website_summary: Annotated[str, Field(description="A concise summary of the website's apparent purpose and content.")]
    outgoing_links: Annotated[list[str], Field(description="External links (different domain) discovered on the site.")]
    login_attempted: Annotated[bool, Field(description="Whether a login or registration flow was attempted.")]
    login_outcome: Annotated[Outcome, Field(default=Outcome.not_attempted, description="The result of the login or registration: 'succeeded' only if an explicit confirmation appeared, 'failed' if it was explicitly rejected, 'unclear' if there was no clear response, 'not_attempted' if it was never tried.")]
    credit_card_submitted: Annotated[bool, Field(description="Whether credit card information was submitted to the site.")]
    payment_outcome: Annotated[Outcome, Field(default=Outcome.not_attempted, description="The result of the payment: 'succeeded' only if an explicit confirmation appeared, 'failed' if it was explicitly rejected or declined, 'unclear' if there was no clear response, 'not_attempted' if no payment was made.")]
    form_fields_requested: Annotated[list[str], Field(description="Types of personal information the site requested (e.g. 'full name', 'ID number', 'credit card').")]
    unexpected_events: Annotated[list[str], Field(description="Anything that happened during the visit that an ordinary user would find surprising (e.g. 'redirected to an unrelated domain', 'payment confirmation page appeared instantly without a processor redirect').")]
    visit_completed: Annotated[bool, Field(default=True, description="Whether the visit ran to a normal conclusion rather than being cut short.")]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (including the pre-existing `test_browsing_result_descriptions_are_neutral`, which now also scans the new descriptions).

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/models.py tests/test_models.py
git commit -m "feat: replace browsing booleans with four-state Outcome enum

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Two-card `FakePersona` and persona hardening

**Files:**
- Modify: `src/anti_scam_agent/models.py` (add field to `FakePersona`)
- Modify: `src/anti_scam_agent/persona.py`
- Test: `tests/test_persona.py`

- [ ] **Step 1: Write the failing tests**

Add a Luhn helper and tests to `tests/test_persona.py`:

```python
def _luhn_ok(number: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", number)]
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def test_primary_card_is_luhn_invalid():
    persona = generate_persona()
    assert not _luhn_ok(persona.credit_card_number), persona.credit_card_number


def test_fallback_card_is_luhn_valid():
    persona = generate_persona()
    assert _luhn_ok(persona.credit_card_number_luhn_valid), persona.credit_card_number_luhn_valid


def test_phone_has_no_extension():
    persona = generate_persona()
    assert "x" not in persona.phone.lower(), persona.phone


def test_cvv_length_matches_card_type():
    # Amex cards (start with 34 or 37) use a 4-digit CVV; others use 3.
    for _ in range(40):
        persona = generate_persona()
        valid_digits = re.sub(r"\D", "", persona.credit_card_number_luhn_valid)
        is_amex = valid_digits[:2] in {"34", "37"}
        expected = 4 if is_amex else 3
        assert len(persona.credit_card_cvv) == expected, (
            f"{valid_digits[:2]} -> cvv {persona.credit_card_cvv!r}"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_persona.py -v`
Expected: FAIL — `credit_card_number_luhn_valid` does not exist / primary card is currently Luhn-valid.

- [ ] **Step 3: Add the model field**

In `src/anti_scam_agent/models.py`, add to `FakePersona` (after `credit_card_number`):

```python
    credit_card_number_luhn_valid: str
```

- [ ] **Step 4: Implement persona hardening**

Replace `src/anti_scam_agent/persona.py` contents with:

```python
import random
import re

from faker import Faker

from anti_scam_agent.models import FakePersona

_faker = Faker("en_US")


def _email_from_name(name: str) -> str:
    parts = [p.lower() for p in name.split() if p.isalpha()]
    if len(parts) < 2:
        parts = ["user", str(random.randint(1000, 9999))]
    return f"{parts[0]}.{parts[-1]}{random.randint(10, 99)}@example.com"


def _break_luhn(number: str) -> str:
    """Flip the last (check) digit so the number fails Luhn validation."""
    last = int(number[-1])
    return number[:-1] + str((last + 1) % 10)


def generate_persona() -> FakePersona:
    name = _faker.name()
    card_type = random.choice(["visa", "mastercard", "amex", "discover"])
    valid_card = _faker.credit_card_number(card_type=card_type)
    cvv_len = 4 if card_type == "amex" else 3
    phone = _faker.phone_number().split("x")[0].strip()
    return FakePersona(
        name=name,
        email=_email_from_name(name),
        password=_faker.password(length=12),
        phone=phone,
        address=_faker.address().replace("\n", ", "),
        credit_card_number=_break_luhn(valid_card),
        credit_card_number_luhn_valid=valid_card,
        credit_card_expiry=_faker.credit_card_expire(),
        credit_card_cvv=f"{random.randint(0, 10**cvv_len - 1):0{cvv_len}d}",
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_persona.py -v`
Expected: PASS (existing shape/non-empty/non-constant tests still pass; `credit_card_number` is still 13–19 digits because only the last digit changed).

- [ ] **Step 6: Commit**

```bash
git add src/anti_scam_agent/models.py src/anti_scam_agent/persona.py tests/test_persona.py
git commit -m "feat: two-tier card persona with Luhn-invalid primary and hardening

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Browsing prompt fixes and fallback

**Files:**
- Modify: `src/anti_scam_agent/browsing.py`
- Test: `tests/test_browsing.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_browsing.py`:

```python
from anti_scam_agent.browsing import _build_task_prompt, _fallback_result
from anti_scam_agent.models import FakePersona, Outcome


def _persona() -> FakePersona:
    return FakePersona(
        name="Jane Doe",
        email="jane.doe11@example.com",
        password="hunter2hunter2",
        phone="555-123-4567",
        address="1 Main St, Springfield",
        credit_card_number="4111111111111112",
        credit_card_number_luhn_valid="4111111111111111",
        credit_card_expiry="08/30",
        credit_card_cvv="123",
    )


def test_prompt_drops_the_no_error_equals_success_instruction():
    prompt = _build_task_prompt("http://example.com", _persona())
    assert "did not respond an explicit error" not in prompt
    assert "account as a success" not in prompt


def test_prompt_typos_are_fixed():
    prompt = _build_task_prompt("http://example.com", _persona())
    assert "when for it fully loaded" not in prompt
    assert "wait for it to fully load" in prompt


def test_prompt_does_not_leak_card_tier_or_luhn():
    prompt = _build_task_prompt("http://example.com", _persona())
    lowered = prompt.lower()
    assert "luhn" not in lowered
    assert "card_tier" not in lowered
    # Only the active (primary) card number appears, never the fallback.
    assert "4111111111111111" not in prompt


def test_fallback_marks_visit_incomplete():
    result = _fallback_result("http://example.com", "boom")
    assert result.visit_completed is False
    assert result.login_outcome is Outcome.not_attempted
    assert result.payment_outcome is Outcome.not_attempted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_browsing.py -v`
Expected: FAIL — prompt still contains the old line / `_fallback_result` uses removed boolean fields.

- [ ] **Step 3: Update the prompt builder**

In `src/anti_scam_agent/browsing.py`, replace the `_build_task_prompt` return block's numbered steps and trailing instruction. The full new `_build_task_prompt` body:

```python
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
  1. Open the page and wait for it to fully load.
  2. Read what it is about.
  3. If the site invites you to register or sign in, do so using the information above.
  4. If the site invites you to complete a purchase, claim a prize, or enter payment details, go through with it using the information above.
  5. Follow the site's instructions as a normal curious user would. Do not skip steps the site asks you to complete.
  6. Stop when you have either completed the main flow the site offers, or there is nothing further a user would reasonably do.

When you stop, produce a summary of what happened in the structured output format. Report objective facts: what the site appears to be about, what links it has to other domains, what forms it asked you to fill in, and anything during the visit that surprised you as a user.
For each step like login or payment, record the outcome honestly: choose 'succeeded' only when the site showed an explicit confirmation or success screen, 'failed' when it showed an explicit error or rejection, 'unclear' when there was no clear response either way, and 'not_attempted' when you did not try it.
"""
```

- [ ] **Step 4: Update the fallback**

In `src/anti_scam_agent/browsing.py`, ensure `Outcome` is imported:

```python
from anti_scam_agent.models import BrowsingResult, FakePersona, Outcome
```

Replace `_fallback_result` with:

```python
def _fallback_result(url: str, note: str) -> BrowsingResult:
    return BrowsingResult(
        website_summary=f"Unable to complete visit to {url}.",
        outgoing_links=[],
        login_attempted=False,
        login_outcome=Outcome.not_attempted,
        credit_card_submitted=False,
        payment_outcome=Outcome.not_attempted,
        form_fields_requested=[],
        unexpected_events=[note],
        visit_completed=False,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_browsing.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/anti_scam_agent/browsing.py tests/test_browsing.py
git commit -m "fix: neutral outcome mapping in browsing prompt, mark failed visits incomplete

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Two-tier card orchestration in the pipeline

**Files:**
- Modify: `src/anti_scam_agent/pipeline.py`
- Test: `tests/test_pipeline.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`. These are offline — they monkeypatch the browsing and analysis calls so no browser or network is used. The project has no `pytest-asyncio`; follow the existing convention in `tests/test_analysis.py` and drive coroutines with `asyncio.run(...)` inside plain sync test functions.

```python
import asyncio

import anti_scam_agent.pipeline as pipeline
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment


def _result(payment: Outcome) -> BrowsingResult:
    return BrowsingResult(
        website_summary="x",
        outgoing_links=[],
        login_attempted=True,
        login_outcome=Outcome.succeeded,
        credit_card_submitted=True,
        payment_outcome=payment,
        form_fields_requested=[],
        unexpected_events=[],
        visit_completed=True,
    )


def _assessment() -> ScamAssessment:
    return ScamAssessment(
        is_scam=False, confidence=0.1, scam_type=None, reasoning="r", risk_factors=[]
    )


def _patch(monkeypatch, payment_sequence):
    """Make run_browsing_agent return the given outcomes in order; capture analysis args."""
    calls = {"browse": 0, "cards": [], "card_tier": None}

    async def fake_browse(url, persona):
        calls["cards"].append(persona.credit_card_number)
        payment = payment_sequence[calls["browse"]]
        calls["browse"] += 1
        return _result(payment)

    async def fake_analyze(result, domain, card_tier):
        calls["card_tier"] = card_tier
        return _assessment()

    monkeypatch.setattr(pipeline, "run_browsing_agent", fake_browse)
    monkeypatch.setattr(pipeline, "run_analysis_agent", fake_analyze)
    return calls


def test_invalid_card_accepted_stops_after_one_run(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.succeeded])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse"] == 1
    assert calls["card_tier"] == "luhn_invalid"


def test_invalid_rejected_then_valid_accepted_runs_twice(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.failed, Outcome.succeeded])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse"] == 2
    # Second run used the Luhn-valid fallback card, different from the first.
    assert calls["cards"][0] != calls["cards"][1]
    assert calls["card_tier"] == "luhn_valid"


def test_unclear_payment_does_not_trigger_second_run(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse"] == 1
    assert calls["card_tier"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `run_analysis_agent` does not accept `card_tier`; no two-run logic.

- [ ] **Step 3: Implement the orchestration**

Replace `src/anti_scam_agent/pipeline.py` contents with:

```python
from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.models import Outcome, ScamAssessment
from anti_scam_agent.persona import generate_persona


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()

    # Run 1: a Luhn-invalid card. Acceptance here is the strongest signal.
    result = await run_browsing_agent(url, persona)
    card_tier: str | None = None

    if result.payment_outcome is Outcome.succeeded:
        card_tier = "luhn_invalid"
    elif result.payment_outcome is Outcome.failed:
        # The site's front end caught the bad card. Retry with a valid one;
        # acceptance now (instant success, no processor) is a weaker signal.
        persona_valid = persona.model_copy(
            update={"credit_card_number": persona.credit_card_number_luhn_valid}
        )
        result = await run_browsing_agent(url, persona_valid)
        if result.payment_outcome is Outcome.succeeded:
            card_tier = "luhn_valid"

    domain = _extract_domain(url)
    return await run_analysis_agent(result, domain, card_tier)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/pipeline.py tests/test_pipeline.py
git commit -m "feat: two-tier card orchestration in pipeline

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Analysis prompt + `card_tier` parameter and abstain rule

**Files:**
- Modify: `src/anti_scam_agent/analysis.py`
- Test: `tests/test_analysis.py` (live OpenAI; add one offline signature guard)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_analysis.py` an offline guard that does not call OpenAI (it only inspects the function signature):

```python
import inspect

from anti_scam_agent.analysis import run_analysis_agent


def test_run_analysis_agent_accepts_card_tier():
    params = inspect.signature(run_analysis_agent).parameters
    assert "card_tier" in params
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analysis.py::test_run_analysis_agent_accepts_card_tier -v`
Expected: FAIL — `card_tier` not in signature.

- [ ] **Step 3: Update the analysis agent**

In `src/anti_scam_agent/analysis.py`, replace the heuristics paragraph of `_SYSTEM_PROMPT` (the bullet list under "Heuristics") with:

```python
_SYSTEM_PROMPT = """You are a fraud analyst. You are given a structured report from a colleague who visited a target website as an ordinary user, plus the site's domain and which card tier was used. Your job is to judge whether the site is a scam / phishing operation, with reasoning.

Before producing your judgment, call the `get_domain_info` tool with the target domain to learn how long ago the domain was registered and when it expires.

The report uses four-state outcomes ('not_attempted', 'failed', 'unclear', 'succeeded'). Only 'succeeded' is a positive signal; 'unclear' is NOT acceptance and must not be treated as one.

Card tier (provided separately):
  - 'luhn_invalid': the site accepted a card number that fails the basic Luhn checksum — a real front end rejects this outright. `payment_outcome='succeeded'` with this tier is the STRONGEST single scam signal.
  - 'luhn_valid': the bad card was rejected, but a checksum-valid card was then accepted with instant success and no payment-processor redirect — a SECONDARY (weaker) scam signal.
  - null: no acceptance was observed; do not infer payment fraud.

Heuristics (combine them — no single signal is definitive):
  - Treat 'luhn_invalid' acceptance as strong evidence; 'luhn_valid' acceptance as moderate evidence; weigh accordingly.
  - Very young domains (days_since_creation < 90) combined with any payment acceptance or heavy PII collection are strong scam signals.
  - Old, long-expiration domains with normal user flows are a weak signal of low risk.
  - Requests for unusually sensitive PII (national ID, bank account, mother's maiden name) alongside other red flags compound risk.
  - Unexpected redirects to unrelated domains after submitting data are suspicious.

ABSTAIN RULE: if `visit_completed` is false, the colleague could not complete the visit, so you have almost no behavioral evidence. In that case do not return a confident scam verdict: cap confidence at 0.4 and lean toward is_scam=false unless the domain info alone is overwhelmingly damning.

Return a ScamAssessment:
  - is_scam: your best binary judgment.
  - confidence: 0.0–1.0, calibrated — not every scam warrants 0.99.
  - scam_type: a short category like 'phishing', 'fake lottery', 'credit card harvesting', or None if not a scam.
  - reasoning: a paragraph citing specific observations from the browsing report and domain info.
  - risk_factors: the concrete items from the inputs that drove your judgment.
"""
```

Change the function signature and the user message in `run_analysis_agent`:

```python
async def run_analysis_agent(
    browsing_result: BrowsingResult, domain: str, card_tier: str | None = None
) -> ScamAssessment:
```

and replace the `user_message` assignment with:

```python
    user_message = (
        f"Target domain: {domain}\n"
        f"Card tier: {card_tier if card_tier is not None else 'null (no acceptance observed)'}\n\n"
        f"Browsing report (JSON):\n{browsing_result.model_dump_json(indent=2)}"
    )
```

- [ ] **Step 4: Run the offline guard to verify it passes**

Run: `uv run pytest tests/test_analysis.py::test_run_analysis_agent_accepts_card_tier -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/analysis.py tests/test_analysis.py
git commit -m "feat: analysis weights card tier and abstains on incomplete visits

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the offline suite**

Run: `uv run pytest tests/test_models.py tests/test_persona.py tests/test_browsing.py tests/test_pipeline.py -v`
Expected: PASS — all offline tests green.

- [ ] **Step 2: Run the full suite (network/live tests included)**

Run: `uv run pytest -v`
Expected: offline tests PASS; live OpenAI/WHOIS tests PASS when `OPENAI_API_KEY` is set and network is available (they fail only offline, per project convention — note any such skips, do not "fix" them).

- [ ] **Step 3: Confirm no leak regressions**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS — the neutral-field and enum-value invariants hold.

---

## Self-Review notes

- **Spec coverage:** 1.1 four-state + `visit_completed` (Task 1); 1.2 prompt fixes + fallback (Task 3); 1.3 two-tier orchestration + `card_tier` out-of-band (Task 4); 1.4 persona Luhn-invalid/valid + Amex CVV + phone strip (Task 2); 1.5 analysis weighting + abstain (Task 5). Email→AgentMail is explicitly deferred to Bucket 3.
- **Blind invariant:** `card_tier`/Luhn never enter `BrowsingResult` (Task 1) or the prompt (Task 3 guard test); they live only on the aware analysis side (Tasks 4–5).
- **Type consistency:** `Outcome` members `not_attempted/failed/unclear/succeeded`; `BrowsingResult` fields `login_outcome`/`payment_outcome`/`visit_completed`; `FakePersona.credit_card_number_luhn_valid`; `run_analysis_agent(browsing_result, domain, card_tier=None)`; `card_tier` values `"luhn_invalid"`/`"luhn_valid"`/`None` — used identically across Tasks 1, 3, 4, 5.
