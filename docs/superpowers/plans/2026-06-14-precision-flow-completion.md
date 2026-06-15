# Precision & Flow-Completion Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refocus the agent on completing the full site flow and on the signal "a fabricated valid-format card was not explicitly declined," make AgentMail mandatory, and turn email into a mid-flow Browsing tool rather than a post-hoc analysis signal.

**Architecture:** Single browsing run with one Luhn-valid card; the Browsing Agent gets a neutral closure-captured `read_email_inbox` tool (framework `context` injection is unwired in the pinned browser-use fork — see spec); the post-hoc `EmailEvidence` subsystem is removed; analysis is rewritten around an explicit-decline payment rule.

**Tech Stack:** Python 3.12, uv, pytest (sync, `asyncio.run`), pydantic v2, browser-use 0.13.1 (pinned fork), openai-agents, agentmail SDK.

**Spec:** `docs/superpowers/specs/2026-06-14-precision-flow-completion-design.md`

**Standing constraints:**
- Do NOT read `.env` (it holds the real key). Do NOT run the full `uv run pytest` (live OpenAI/AgentMail/WHOIS). Run only the named offline test files.
- The blind-browser invariant holds: nothing the Browsing Agent sees (task prompt, tool descriptions, `BrowsingResult` field descriptions) may reference scam detection, AgentMail, card validity, or payment-probing intent.
- Commit prefixes follow conventional commits; we stay on `feat/precision-signals-upgrade` (PR only after all tasks pass).

---

## File Structure

| File | Responsibility after this plan |
|---|---|
| `src/anti_scam_agent/models.py` | `FakePersona` (single card), `BrowsingResult` (+ `payment_explicitly_declined`), `ScamAssessment` |
| `src/anti_scam_agent/persona.py` | TW persona with one Luhn-valid card |
| `src/anti_scam_agent/email_evidence.py` | Inbox rotation, mandatory `make_client()`, `read_inbox_text()` for the browsing tool — **no** post-hoc evidence |
| `src/anti_scam_agent/browsing.py` | Task prompt (flow-completion), `read_email_inbox` tool, single-run agent |
| `src/anti_scam_agent/pipeline.py` | One browsing run, mandatory AgentMail, static signals, analysis |
| `src/anti_scam_agent/analysis.py` | Explicit-decline payment rule; no card-tier / email blocks |
| `CLAUDE.md` | Updated narrative |

---

## Task 1: Add `payment_explicitly_declined` to BrowsingResult

**Files:**
- Modify: `src/anti_scam_agent/models.py` (BrowsingResult)
- Modify: `src/anti_scam_agent/browsing.py` (`_fallback_result`)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_browsing_result_has_payment_explicitly_declined():
    fields = BrowsingResult.model_fields
    assert "payment_explicitly_declined" in fields
    assert fields["payment_explicitly_declined"].annotation is bool
    # default must be the safe/neutral False
    assert fields["payment_explicitly_declined"].default is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_browsing_result_has_payment_explicitly_declined -v`
Expected: FAIL with KeyError on `"payment_explicitly_declined"`.

- [ ] **Step 3: Add the field (neutral description) to `models.py`**

In `BrowsingResult`, after the `payment_outcome` field, add:

```python
    payment_explicitly_declined: Annotated[bool, Field(default=False, description="Whether the site clearly told you the card was declined or invalid (an explicit error specifically about the card), as opposed to accepting it or moving on without a clear card error.")]
```

- [ ] **Step 4: Keep `_fallback_result` constructing a full model**

In `src/anti_scam_agent/browsing.py`, inside `_fallback_result`, add the field to the `BrowsingResult(...)` call (after `payment_outcome=Outcome.not_attempted,`):

```python
        payment_explicitly_declined=False,
```

- [ ] **Step 5: Run the model + neutrality tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (the forbidden-word loop `test_browsing_result_descriptions_are_neutral` also passes — "card", "declined", "invalid" are not in the forbidden set).

- [ ] **Step 6: Commit**

```bash
git add src/anti_scam_agent/models.py src/anti_scam_agent/browsing.py tests/test_models.py
git commit -m "feat: add neutral payment_explicitly_declined signal to BrowsingResult

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Remove post-hoc email subsystem; make AgentMail mandatory; add `read_inbox_text`

This rips out `EmailEvidence` / `collect_email_evidence` end-to-end (email_evidence + analysis + pipeline + their tests) and adds the inbox-reading helper the browsing tool will use. The two-tier card flow and `card_tier` are still present after this task (removed in Task 3).

**Files:**
- Modify: `src/anti_scam_agent/email_evidence.py`
- Modify: `src/anti_scam_agent/analysis.py` (drop `email_evidence` param + import + user-message block)
- Modify: `src/anti_scam_agent/pipeline.py` (drop email collection + the make_client-None branch)
- Rewrite: `tests/test_email_evidence.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_analysis.py` (live — call-signature only)

- [ ] **Step 1: Rewrite `tests/test_email_evidence.py` to the new surface (failing)**

Replace the entire file with:

```python
import datetime
from types import SimpleNamespace

import pytest

import anti_scam_agent.email_evidence as ev
from anti_scam_agent.email_evidence import (
    make_client,
    pick_inbox,
    read_inbox_text,
)


def test_pick_inbox_rotates(monkeypatch):
    monkeypatch.setattr(ev, "_get_inboxes", lambda: ["a@x.to", "b@x.to", "c@x.to"])
    ev._reset_inbox_rotation()
    picks = [pick_inbox() for _ in range(4)]
    assert picks == ["a@x.to", "b@x.to", "c@x.to", "a@x.to"]


def test_make_client_raises_without_key(monkeypatch):
    monkeypatch.delenv("AGENTMAIL_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        make_client()


def _msg(from_, text=None, subject="Welcome", message_id="m1"):
    return SimpleNamespace(from_=from_, subject=subject, text=text, message_id=message_id)


def test_read_inbox_text_returns_recent_message_bodies():
    msgs = [_msg("noreply@shop.com", text="Your verification code is 482913")]
    client = SimpleNamespace(
        inboxes=SimpleNamespace(
            messages=SimpleNamespace(list=lambda **kw: SimpleNamespace(messages=msgs))
        )
    )
    out = read_inbox_text(client, "in@x.to")
    assert "482913" in out
    assert "noreply@shop.com" in out


def test_read_inbox_text_empty_inbox():
    client = SimpleNamespace(
        inboxes=SimpleNamespace(
            messages=SimpleNamespace(list=lambda **kw: SimpleNamespace(messages=[]))
        )
    )
    out = read_inbox_text(client, "in@x.to")
    assert "No messages" in out


def test_read_inbox_text_is_failure_tolerant():
    def boom(**kw):
        raise RuntimeError("api down")

    client = SimpleNamespace(
        inboxes=SimpleNamespace(messages=SimpleNamespace(list=boom))
    )
    out = read_inbox_text(client, "in@x.to")
    assert isinstance(out, str) and out  # benign non-empty string, no raise


def test_read_inbox_text_requests_unauthenticated_mail():
    seen = {}

    def fake_list(**kw):
        seen.update(kw)
        return SimpleNamespace(messages=[])

    client = SimpleNamespace(
        inboxes=SimpleNamespace(messages=SimpleNamespace(list=fake_list))
    )
    read_inbox_text(client, "in@x.to")
    assert seen.get("include_unauthenticated") is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_email_evidence.py -v`
Expected: FAIL — `read_inbox_text` does not exist; `make_client` returns None instead of raising.

- [ ] **Step 3: Rewrite `email_evidence.py` to the slim surface**

Replace the entire file with:

```python
import logging
import os
import threading
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    from agentmail import AgentMail

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_INBOXES = ["asalpha@agentmail.to", "asbravo@agentmail.to", "ascharlie@agentmail.to"]
_inbox_index = 0
_inbox_lock = threading.Lock()


def _get_inboxes() -> list[str]:
    raw = os.getenv("AGENTMAIL_INBOXES", "")
    inboxes = [a.strip() for a in raw.split(",") if a.strip()]
    return inboxes or _DEFAULT_INBOXES


def _reset_inbox_rotation() -> None:
    global _inbox_index
    _inbox_index = 0


def pick_inbox() -> str:
    """Round-robin across the configured inboxes (scans run sequentially)."""
    global _inbox_index
    inboxes = _get_inboxes()
    with _inbox_lock:
        inbox = inboxes[_inbox_index % len(inboxes)]
        _inbox_index += 1
    return inbox


def make_client() -> "AgentMail":
    """Return an AgentMail client. AgentMail is mandatory: raise if unconfigured."""
    api_key = os.getenv("AGENTMAIL_API_KEY")
    if not api_key:
        raise RuntimeError(
            "AGENTMAIL_API_KEY is required: the agent routes the persona's email "
            "through an AgentMail inbox so it can read verification codes mid-flow. "
            "Set it in .env (see .env.example)."
        )
    from agentmail import AgentMail

    return AgentMail(api_key=api_key)


def _message_text(client, inbox: str, m) -> str:
    """Best-effort body text for a message, tolerant of the SDK's exact shape."""
    for attr in ("text", "preview"):
        val = getattr(m, attr, None)
        if val:
            return str(val)
    msg_id = getattr(m, "message_id", None) or getattr(m, "id", None)
    if msg_id is None:
        return ""
    try:
        full = client.inboxes.messages.get(inbox_id=inbox, message_id=msg_id)
        return str(getattr(full, "text", "") or getattr(full, "preview", "") or "")
    except Exception as e:  # noqa: BLE001 — reading mail must never break browsing
        logger.warning("read_inbox_text get failed for %s: %s", inbox, e)
        return ""


def read_inbox_text(client, inbox: str, limit: int = 5) -> str:
    """Readable text of the most recent inbox messages, for the browsing email tool.

    Failure-tolerant: any error yields a benign string (never raises). Includes
    unauthenticated mail — scam verification mail is frequently unauthenticated.
    """
    try:
        resp = client.inboxes.messages.list(
            inbox_id=inbox,
            ascending=False,
            limit=limit,
            include_unauthenticated=True,
        )
        messages = list(resp.messages)
    except Exception as e:  # noqa: BLE001
        logger.warning("read_inbox_text list failed for %s: %s", inbox, e)
        return "Could not read the inbox right now; please continue."
    if not messages:
        return "No messages in your inbox yet."
    parts = []
    for m in messages:
        subject = getattr(m, "subject", "") or ""
        body = _message_text(client, inbox, m)
        parts.append(f"From: {getattr(m, 'from_', '')}\nSubject: {subject}\n{body}".strip())
    return "\n\n---\n\n".join(parts)
```

- [ ] **Step 4: Run the email_evidence tests**

Run: `uv run pytest tests/test_email_evidence.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Drop email from `analysis.py` (import, param, user-message block)**

In `src/anti_scam_agent/analysis.py`:
- Remove the import line `from anti_scam_agent.email_evidence import EmailEvidence`.
- Change the signature to drop the `email_evidence` parameter:

```python
async def run_analysis_agent(
    browsing_result: BrowsingResult,
    domain: str,
    card_tier: Literal["luhn_invalid", "luhn_valid"] | None = None,
    static_signals: StaticSignals | None = None,
) -> ScamAssessment:
```

- Remove the `email_json = ...` line and the `Email evidence (JSON):` block from `user_message`, leaving:

```python
    user_message = (
        f"Target domain: {domain}\n"
        f"Card tier: {card_tier if card_tier is not None else 'null (no acceptance observed)'}\n\n"
        f"Static signals (JSON):\n{static_json}\n\n"
        f"Browsing report (JSON):\n{browsing_result.model_dump_json(indent=2)}"
    )
```

(The `_SYSTEM_PROMPT` email paragraph is a harmless string for now; it is fully rewritten in Task 6.)

- [ ] **Step 6: Drop email collection from `pipeline.py`**

In `src/anti_scam_agent/pipeline.py`:
- Update the import to drop `EmailEvidence` and `collect_email_evidence`:

```python
from anti_scam_agent.email_evidence import make_client, pick_inbox
```

- Remove `_poll_seconds` and the `from datetime import ...` / `timezone` imports if now unused (keep `asyncio`, `os` only if still used — `os` is no longer needed; remove it).
- Replace the body of `run_pipeline` from the `client = make_client()` line through the analysis call with (two-tier card flow retained for this task):

```python
async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    domain = _extract_domain(url)

    # AgentMail is mandatory; route the persona's email through a real inbox.
    make_client()  # raises if unconfigured
    persona = persona.model_copy(update={"email": pick_inbox()})

    # Run 1: a Luhn-invalid card. Acceptance here is the strongest signal.
    result = await run_browsing_agent(url, persona)
    card_tier: Literal["luhn_invalid", "luhn_valid"] | None = None

    if result.payment_outcome is Outcome.succeeded:
        card_tier = "luhn_invalid"
    elif result.payment_outcome is Outcome.failed:
        persona_valid = persona.model_copy(
            update={"credit_card_number": persona.credit_card_number_luhn_valid}
        )
        result = await run_browsing_agent(url, persona_valid)
        if result.payment_outcome is Outcome.succeeded:
            card_tier = "luhn_valid"

    static_signals = await asyncio.to_thread(collect_static_signals, url)
    return await run_analysis_agent(result, domain, card_tier, static_signals)
```

- [ ] **Step 7: Update `tests/test_pipeline.py` for the new wiring**

In `tests/test_pipeline.py`:
- Change the import to drop `EmailEvidence`:

```python
from anti_scam_agent.models import BrowsingResult, FakePersona, Outcome, ScamAssessment
```

- In `_patch`, drop the `email` key and the `email_evidence` arg, and patch `make_client`/`pick_inbox` for mandatory AgentMail:

```python
def _patch(monkeypatch, payment_sequence):
    """Stub browsing, analysis, static signals; capture args."""
    calls = {"browse": 0, "cards": [], "card_tier": None, "static": None, "persona_email": None}

    async def fake_browse(url, persona):
        calls["cards"].append(persona.credit_card_number)
        calls["persona_email"] = persona.email
        payment = payment_sequence[calls["browse"]]
        calls["browse"] += 1
        return _result(payment)

    async def fake_analyze(result, domain, card_tier, static_signals):
        calls["card_tier"] = card_tier
        calls["static"] = static_signals
        return _assessment()

    monkeypatch.setattr(pipeline, "run_browsing_agent", fake_browse)
    monkeypatch.setattr(pipeline, "run_analysis_agent", fake_analyze)
    monkeypatch.setattr(pipeline, "collect_static_signals", lambda url: StaticSignals(target_host="shop.test"))
    monkeypatch.setattr(pipeline, "make_client", lambda: object())
    monkeypatch.setattr(pipeline, "pick_inbox", lambda: "asalpha@agentmail.to")
    return calls
```

- Delete `test_email_evidence_collected_when_configured`, `test_email_skipped_when_unconfigured`, `test_poll_seconds_defaults_on_bad_env`, and `test_poll_seconds_reads_valid_env`.
- Add a test that AgentMail is mandatory:

```python
def test_pipeline_requires_agentmail(monkeypatch):
    _patch(monkeypatch, [Outcome.unclear])

    def boom():
        raise RuntimeError("AGENTMAIL_API_KEY is required")

    monkeypatch.setattr(pipeline, "make_client", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(pipeline.run_pipeline("http://shop.test"))


def test_persona_email_routed_through_inbox(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["persona_email"] == "asalpha@agentmail.to"
```

(The two-tier tests `test_invalid_card_accepted_stops_after_one_run`, `test_invalid_rejected_then_valid_accepted_runs_twice`, etc. still pass — the persona still has the fallback field until Task 3.)

- [ ] **Step 8: Update the live `tests/test_analysis.py` call signature**

In `tests/test_analysis.py`, find every `run_analysis_agent(...)` call and remove the email-evidence argument so calls match the new signature `(browsing_result, domain, card_tier=None, static_signals=None)`. (Do not run this file — it is live.)

- [ ] **Step 9: Run the offline suite for this task**

Run: `uv run pytest tests/test_email_evidence.py tests/test_pipeline.py tests/test_models.py -v`
Expected: PASS. Also verify the chain imports:
Run: `uv run python -c "import anti_scam_agent.pipeline, anti_scam_agent.analysis, anti_scam_agent.email_evidence"`
Expected: no output, exit 0.

- [ ] **Step 10: Commit**

```bash
git add src/anti_scam_agent/email_evidence.py src/anti_scam_agent/analysis.py src/anti_scam_agent/pipeline.py tests/test_email_evidence.py tests/test_pipeline.py tests/test_analysis.py
git commit -m "refactor: remove post-hoc email evidence; require AgentMail; add read_inbox_text

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Single Luhn-valid card; single browsing run; drop `card_tier`

**Files:**
- Modify: `src/anti_scam_agent/models.py` (remove `credit_card_number_luhn_valid`)
- Modify: `src/anti_scam_agent/persona.py`
- Modify: `src/anti_scam_agent/analysis.py` (drop `card_tier` param + user-message line)
- Modify: `src/anti_scam_agent/pipeline.py` (single run)
- Modify: `tests/test_persona.py`, `tests/test_browsing.py`, `tests/test_pipeline.py`, `tests/test_analysis.py`

- [ ] **Step 1: Update `tests/test_persona.py` (failing)**

- Replace `test_primary_card_is_luhn_invalid` with:

```python
def test_card_is_luhn_valid():
    persona = generate_persona()
    assert _luhn_ok(persona.credit_card_number), persona.credit_card_number
```

- Delete `test_fallback_card_is_luhn_valid`.
- In `test_cvv_length_matches_card_type`, change `persona.credit_card_number_luhn_valid` → `persona.credit_card_number`.
- In `test_card_type_mix_is_localized`, change `generate_persona().credit_card_number_luhn_valid` → `generate_persona().credit_card_number`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_persona.py -v`
Expected: FAIL — `test_card_is_luhn_valid` fails (current primary card is Luhn-invalid) and `credit_card_number_luhn_valid` references error.

- [ ] **Step 3: Remove the field from `models.py`**

In `FakePersona`, delete the line `credit_card_number_luhn_valid: str`.

- [ ] **Step 4: Single card in `persona.py`**

Replace the file body from `_break_luhn` through `generate_persona` with:

```python
def generate_persona() -> FakePersona:
    name = _faker.name()
    card_type = random.choice(_CARD_TYPES)
    card_number = _faker.credit_card_number(card_type=card_type)
    cvv_len = 4 if card_type == "amex" else 3
    return FakePersona(
        name=name,
        # The Chinese name can't be an email local-part, so use a romanized ASCII
        # handle. The pipeline replaces this with an AgentMail inbox address.
        email=f"{_faker.user_name()}@example.com",
        password=_faker.password(length=12),
        phone=_taiwan_mobile(),
        address=_faker.address().replace("\n", ", "),
        credit_card_number=card_number,
        credit_card_expiry=_faker.credit_card_expire(),
        credit_card_cvv=f"{random.randint(0, 10**cvv_len - 1):0{cvv_len}d}",
    )
```

(Delete the `_break_luhn` function entirely.)

- [ ] **Step 5: Run persona tests**

Run: `uv run pytest tests/test_persona.py -v`
Expected: PASS.

- [ ] **Step 6: Drop `card_tier` from `analysis.py`**

In `run_analysis_agent`, remove the `card_tier` parameter (and the `Literal` import if now unused) so the signature is:

```python
async def run_analysis_agent(
    browsing_result: BrowsingResult,
    domain: str,
    static_signals: StaticSignals | None = None,
) -> ScamAssessment:
```

Remove the `Card tier: ...` line from `user_message`:

```python
    user_message = (
        f"Target domain: {domain}\n\n"
        f"Static signals (JSON):\n{static_json}\n\n"
        f"Browsing report (JSON):\n{browsing_result.model_dump_json(indent=2)}"
    )
```

- [ ] **Step 7: Single browsing run in `pipeline.py`**

Replace `run_pipeline` body (drop the two-tier flow and `card_tier`):

```python
async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    domain = _extract_domain(url)

    # AgentMail is mandatory; route the persona's email through a real inbox.
    make_client()  # raises if unconfigured
    persona = persona.model_copy(update={"email": pick_inbox()})

    result = await run_browsing_agent(url, persona)
    static_signals = await asyncio.to_thread(collect_static_signals, url)
    return await run_analysis_agent(result, domain, static_signals)
```

Remove the now-unused `from typing import Literal` and `from anti_scam_agent.models import Outcome` if `Outcome` is no longer referenced (keep `ScamAssessment`).

- [ ] **Step 8: Update `tests/test_pipeline.py` for single run**

- `fake_analyze` becomes `async def fake_analyze(result, domain, static_signals):` and stores only `static`. Drop `card_tier` from `calls`.
- Delete the two-tier tests: `test_invalid_card_accepted_stops_after_one_run`, `test_invalid_rejected_then_valid_accepted_runs_twice`, `test_non_failure_payment_does_not_trigger_second_run`, `test_second_run_without_success_leaves_card_tier_none`.
- Replace with single-run coverage:

```python
@pytest.mark.parametrize("payment", [Outcome.succeeded, Outcome.failed, Outcome.unclear, Outcome.not_attempted])
def test_pipeline_runs_browsing_once(monkeypatch, payment):
    calls = _patch(monkeypatch, [payment])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse"] == 1
```

- The `FakePersona(...)` literal in any remaining test (none should reference the removed field now; the two-tier `known` literal is deleted with its test) — confirm no `credit_card_number_luhn_valid=` remains in the file.
- `test_static_signals_passed_to_analysis` and `test_persona_email_routed_through_inbox` and `test_pipeline_requires_agentmail` remain valid.

- [ ] **Step 9: Update `tests/test_browsing.py` persona + leak test**

- In `_persona()`, remove the `credit_card_number_luhn_valid="4111111111111111",` line and set `credit_card_number="4111111111111111"` (a Luhn-valid number).
- Replace `test_prompt_does_not_leak_card_tier_or_luhn` with:

```python
def test_prompt_does_not_leak_card_tier_or_luhn():
    prompt = _build_task_prompt("http://example.com", _persona())
    lowered = prompt.lower()
    assert "luhn" not in lowered
    assert "card_tier" not in lowered
    assert "4111111111111111" in prompt  # the card the agent is given
```

- [ ] **Step 10: Update the live `tests/test_analysis.py` call signature**

Remove the `card_tier` positional/keyword argument from every `run_analysis_agent(...)` call so calls match `(browsing_result, domain, static_signals=None)`. (Do not run; live.)

- [ ] **Step 11: Run the offline suite**

Run: `uv run pytest tests/test_persona.py tests/test_pipeline.py tests/test_browsing.py tests/test_models.py -v`
Expected: PASS. Confirm no stray references:
Run: `grep -rn "credit_card_number_luhn_valid\|card_tier" src/ tests/`
Expected: only `tests/test_models.py` (forbidden-word list) and `tests/test_browsing.py` (leak assertions) mention `card_tier`; no `credit_card_number_luhn_valid` anywhere.

- [ ] **Step 12: Commit**

```bash
git add src/anti_scam_agent/models.py src/anti_scam_agent/persona.py src/anti_scam_agent/analysis.py src/anti_scam_agent/pipeline.py tests/test_persona.py tests/test_pipeline.py tests/test_browsing.py tests/test_analysis.py
git commit -m "refactor: single Luhn-valid card and single browsing run; drop card_tier

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Browsing `read_email_inbox` tool (closure capture) + signature

**Files:**
- Modify: `src/anti_scam_agent/browsing.py`
- Modify: `src/anti_scam_agent/pipeline.py` (pass client + inbox)
- Modify: `tests/test_browsing.py`, `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tool test**

Add to `tests/test_browsing.py`:

```python
import asyncio
from types import SimpleNamespace

from anti_scam_agent.browsing import _build_email_tools


def _fake_client(code_text):
    msgs = [SimpleNamespace(from_="noreply@shop.com", subject="Code", text=code_text, message_id="m1")]
    return SimpleNamespace(
        inboxes=SimpleNamespace(
            messages=SimpleNamespace(list=lambda **kw: SimpleNamespace(messages=msgs))
        )
    )


def test_email_tool_registers_without_leaking_client_or_inbox():
    tools = _build_email_tools(_fake_client("code 123456"), "in@x.to")
    action = tools.registry.registry.actions["read_email_inbox"]
    # The LLM-facing schema must expose NO client/inbox params (blind invariant).
    assert list(action.param_model.model_fields.keys()) == []
    desc = action.description.lower()
    for word in ("scam", "phishing", "agentmail", "luhn", "fabricated"):
        assert word not in desc


def test_email_tool_reads_inbox_contents():
    tools = _build_email_tools(_fake_client("Your code is 123456"), "in@x.to")
    action = tools.registry.registry.actions["read_email_inbox"]
    out = asyncio.run(action.function(params=action.param_model()))
    assert "123456" in str(out)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_browsing.py::test_email_tool_registers_without_leaking_client_or_inbox -v`
Expected: FAIL — `_build_email_tools` does not exist.

- [ ] **Step 3: Implement `_build_email_tools` and thread it into the agent**

In `src/anti_scam_agent/browsing.py`:
- Update the import line to add `Tools`:

```python
from browser_use import Agent as BrowserAgent, ChatOpenAI, Browser, Tools
```

- Add the import: `from anti_scam_agent.email_evidence import read_inbox_text`
- Add the factory (closure-captured — the pinned fork's `context` injection is unwired, see spec):

```python
def _build_email_tools(client, inbox: str) -> Tools:
    """A neutral 'read your inbox' tool. client + inbox are closure-captured so they
    never appear in the LLM-facing action schema (preserving the blind invariant)."""
    tools = Tools()

    @tools.action(
        "Check your email inbox and read your most recent messages — useful when a "
        "site says it has emailed you a code or a confirmation link."
    )
    async def read_email_inbox() -> str:
        return await asyncio.to_thread(read_inbox_text, client, inbox)

    return tools
```

- Change `run_browsing_agent` signature and pass the tools:

```python
async def run_browsing_agent(url: str, persona: FakePersona, client, inbox: str) -> BrowsingResult:
    llm = ChatOpenAI(model="gpt-4.1-mini")
    task = _build_task_prompt(url, persona)

    browser = Browser(
        minimum_wait_page_load_time=2.0,
        wait_for_network_idle_page_load_time=3.0,
        wait_between_actions=1.0,
        headless=False,
        disable_security=True,
        cross_origin_iframes=True,
        paint_order_filtering=False,
    )

    agent = BrowserAgent(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=True,
        output_model_schema=BrowsingResult,
        tools=_build_email_tools(client, inbox),
    )
```

(The rest of `run_browsing_agent` — try/except, structured-output handling, `_external_links` — is unchanged.)

- [ ] **Step 4: Run tool tests**

Run: `uv run pytest tests/test_browsing.py -v`
Expected: PASS.

- [ ] **Step 5: Pass client + inbox from `pipeline.py`**

In `run_pipeline`, capture the client and inbox and pass them to browsing:

```python
async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    domain = _extract_domain(url)

    # AgentMail is mandatory; route the persona's email through a real inbox so the
    # Browsing Agent can read verification codes mid-flow.
    client = make_client()  # raises if unconfigured
    inbox = pick_inbox()
    persona = persona.model_copy(update={"email": inbox})

    result = await run_browsing_agent(url, persona, client, inbox)
    static_signals = await asyncio.to_thread(collect_static_signals, url)
    return await run_analysis_agent(result, domain, static_signals)
```

- [ ] **Step 6: Update `tests/test_pipeline.py` browse stub**

`fake_browse` gains the new params and asserts they arrive:

```python
    async def fake_browse(url, persona, client, inbox):
        calls["cards"].append(persona.credit_card_number)
        calls["persona_email"] = persona.email
        calls["browse_inbox"] = inbox
        payment = payment_sequence[calls["browse"]]
        calls["browse"] += 1
        return _result(payment)
```

Add `"browse_inbox": None` to the `calls` dict initialization, and add:

```python
def test_browsing_receives_inbox(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse_inbox"] == "asalpha@agentmail.to"
```

- [ ] **Step 7: Run the offline suite**

Run: `uv run pytest tests/test_browsing.py tests/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/anti_scam_agent/browsing.py src/anti_scam_agent/pipeline.py tests/test_browsing.py tests/test_pipeline.py
git commit -m "feat: give the Browsing Agent a neutral mid-flow read_email_inbox tool

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Strengthen the browsing task prompt + raise step cap

**Files:**
- Modify: `src/anti_scam_agent/browsing.py` (`_build_task_prompt`, `_MAX_STEPS`)
- Modify: `tests/test_browsing.py`

- [ ] **Step 1: Write failing prompt tests**

Add to `tests/test_browsing.py`:

```python
def test_prompt_instructs_dismissing_blockers():
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    assert "pop-up" in prompt or "popup" in prompt or "overlay" in prompt
    assert "close" in prompt or "dismiss" in prompt


def test_prompt_prioritizes_completing_the_flow_over_exact_data():
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    # improvise when the form doesn't fit the persona
    assert "make up" in prompt or "any" in prompt
    assert "do not get stuck" in prompt or "don't get stuck" in prompt


def test_prompt_prefers_credit_card_payment():
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    assert "credit card" in prompt


def test_prompt_stays_neutral():
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    for word in ("scam", "phishing", "suspicious", "fake", "fabricated", "fraud", "anti-"):
        assert word not in prompt
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_browsing.py -k prompt -v`
Expected: FAIL on the new assertions.

- [ ] **Step 3: Rewrite `_build_task_prompt` (stays strictly user-framed)**

Replace the returned string in `_build_task_prompt` with:

```python
    return f"""You are visiting the website {url} as an interested first-time user who wants to go all the way through whatever the site offers.

Here is the information about you, which you may use to fill in any forms the site asks you to complete:
  - Full name: {persona.name}
  - Email: {persona.email}
  - Password (for any account you need to create): {persona.password}
  - Phone number: {persona.phone}
  - Mailing address: {persona.address}
  - Credit card number: {persona.credit_card_number}
  - Credit card expiry: {persona.credit_card_expiry}
  - Credit card security code (CVV): {persona.credit_card_cvv}

Your single most important objective is to COMPLETE THE WHOLE FLOW the site offers — from start to final confirmation — rather than to enter perfectly accurate information.

What to do, in order:
  1. Open the page and wait for it to fully load.
  2. If a cookie banner, pop-up, modal, overlay, or notice appears, CLOSE or dismiss it first. Do not try to click things behind it — clear the blocker, then continue.
  3. Read what the site is about.
  4. If the site invites you to register or sign in, do so using the information above. If it emails you a verification code or confirmation link, use the "Check your email inbox" tool to read it and enter the code to finish.
  5. If the site invites you to complete a purchase, claim a prize, or enter payment details, go all the way through with it using the information above. If you can choose how to pay (for example cash on delivery versus credit card), you prefer to pay by credit card, so choose credit card and enter the card details.
  6. The form may not perfectly match your details (for example your city or district may not be in a dropdown, or a field may be required that you have no value for). Do not get stuck: pick any reasonable available option, or make up a plausible value, and move on. Getting through the flow matters more than entering matching data.
  7. Follow the site's steps as a normal determined user would, until you reach the final confirmation or there is genuinely nothing further a user could do.

When you stop, produce a summary of what happened in the structured output format. Report objective facts: what the site appears to be about, what links it has to other domains, what forms it asked you to fill in, and anything during the visit that surprised you as a user.
For each step like login or payment, record the outcome honestly: choose 'succeeded' only when the site showed an explicit confirmation or success screen, 'failed' when it showed an explicit error or rejection, 'unclear' when there was no clear response either way, and 'not_attempted' when you did not try it.
If you entered card details, set payment_explicitly_declined to true only when the site clearly told you the card itself was declined or invalid; if it accepted the card, moved on, or showed no clear card error, set it to false.
"""
```

- [ ] **Step 4: Raise the step cap**

Change `_MAX_STEPS = 25` to:

```python
_MAX_STEPS = 40
```

- [ ] **Step 5: Run prompt tests (note the two existing assertions still hold)**

Run: `uv run pytest tests/test_browsing.py -v`
Expected: PASS — including the pre-existing `test_prompt_typos_are_fixed` ("wait for it to fully load", "not_attempted") and `test_prompt_drops_the_no_error_equals_success_instruction`.

- [ ] **Step 6: Commit**

```bash
git add src/anti_scam_agent/browsing.py tests/test_browsing.py
git commit -m "feat: prompt the Browsing Agent to dismiss blockers, improvise, and pay by card

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Rewrite the analysis prompt around explicit-decline; update CLAUDE.md

**Files:**
- Modify: `src/anti_scam_agent/analysis.py` (`_SYSTEM_PROMPT`)
- Modify: `CLAUDE.md`
- Test: `tests/test_analysis.py` (add one offline prompt-content test)

- [ ] **Step 1: Write the offline prompt-content test (failing)**

Add to `tests/test_analysis.py` (this assertion is offline — it inspects the string, makes no API call):

```python
def test_system_prompt_encodes_explicit_decline_rule():
    from anti_scam_agent.analysis import _SYSTEM_PROMPT

    p = _SYSTEM_PROMPT.lower()
    assert "payment_explicitly_declined" in p
    # the old framings are gone
    assert "luhn_invalid" not in p
    assert "card tier" not in p
    assert "exoneration" not in p
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_analysis.py::test_system_prompt_encodes_explicit_decline_rule -v`
Expected: FAIL (`luhn_invalid` / `card tier` still present, `payment_explicitly_declined` absent).

- [ ] **Step 3: Replace `_SYSTEM_PROMPT` in `analysis.py`**

```python
_SYSTEM_PROMPT = """You are a fraud analyst. You are given a structured report from a colleague who visited a target website as an ordinary user, the site's domain, and a bundle of locally-computed static signals (WHOIS, TLS certificate, DNS). Your job is to judge whether the site is a scam / phishing operation, with reasoning.

All the evidence you need is in the input — there are no tools to call.

The report uses four-state outcomes ('not_attempted', 'failed', 'unclear', 'succeeded'). Only 'succeeded' is an explicit positive; 'unclear' means there was no clear response and must not be read as success.

PAYMENT SIGNAL — the central behavioral test:
The colleague submitted a real-format but fabricated credit card. A legitimate merchant runs a real payment processor that ALWAYS rejects such a card with an explicit card error (declined / invalid card). So the ONLY benign reaction is an explicit card decline.
  - If credit_card_submitted is true AND payment_explicitly_declined is true: the site behaved like a real processor — this is REASSURING for the payment path.
  - If credit_card_submitted is true AND payment_explicitly_declined is false — whether payment_outcome is 'succeeded' or 'unclear' (it silently accepted the card, or moved on to a vague success/thank-you/confirmation page with no card-specific error): this is a STRONG scam signal. No real processor lets a fabricated card through.
  - If the card was never submitted (credit_card_submitted false): there is no payment signal; judge on the other evidence.
payment_outcome is descriptive context; payment_explicitly_declined is the field that decides the payment judgment.

Static signals (any field may be null when a lookup failed — treat null as 'unknown', never as evidence):
  - domain_info: days_since_creation, days_until_expiration, registrar, registrant_country, privacy_protected.
  - tls: issuer_org, age_days, san_count, is_free_dv (a free domain-validated certificate, e.g. Let's Encrypt/ZeroSSL).
  - dns: has_mx (does the domain accept mail?), nameservers.

Heuristics (combine them — no single signal is definitive):
  - Card submitted and NOT explicitly declined = strong evidence of a scam (see the payment rule above).
  - Very young domains (days_since_creation < 90) combined with payment acceptance or heavy PII collection are strong scam signals.
  - A young domain + a brand-new free DV certificate + no MX record is a classic throwaway-scam fingerprint; together they compound risk, though none alone is conclusive.
  - has_mx=false is a weak negative signal (a real merchant usually has company mail); has_mx=true is mild reassurance. Never decisive alone.
  - privacy_protected and free DV certs are common on legitimate sites too — only let them compound an already-young or payment-positive case.
  - Old, long-expiration domains with normal user flows and an MX record are a weak signal of low risk.
  - Requests for unusually sensitive PII (national ID, bank account, mother's maiden name) alongside other red flags compound risk.
  - Unexpected redirects to unrelated domains (see outgoing_links) after submitting data are suspicious.

ABSTAIN RULE: if visit_completed is false, the colleague could not complete the visit, so you have almost no behavioral evidence. In that case do not return a confident scam verdict: cap confidence at 0.4 and lean toward is_scam=false unless the static signals alone are overwhelmingly damning.

Return a ScamAssessment:
  - is_scam: your best binary judgment.
  - confidence: 0.0–1.0, calibrated — not every scam warrants 0.99.
  - scam_type: a short category like 'phishing', 'fake lottery', 'credit card harvesting', or None if not a scam.
  - reasoning: a paragraph citing specific observations from the browsing report and static signals.
  - risk_factors: the concrete items from the inputs that drove your judgment.
"""
```

- [ ] **Step 4: Run the offline prompt test**

Run: `uv run pytest tests/test_analysis.py::test_system_prompt_encodes_explicit_decline_rule -v`
Expected: PASS.

- [ ] **Step 5: Update `CLAUDE.md`**

Make these edits to reflect the new architecture:

- In **Project purpose**, replace the sentence describing the canonical signal. New text:

> A legitimate site runs a real payment processor that rejects a fabricated card with an explicit "card declined / invalid" error; a scam site, having no processor, accepts it or moves on without a clear card error. That **absence of an explicit card decline** — captured by `payment_explicitly_declined` — is the canonical detection signal.

- Replace numbered item 2 (Analysis Agent inputs) so it no longer lists `EmailEvidence`:

> 2. **Analysis Agent** (`analysis.py`) — consumes the `BrowsingResult` and a `StaticSignals` bundle (WHOIS age/expiry, TLS certificate info, DNS) computed by `signals.collect_static_signals(url)`, and emits a `ScamAssessment` with calibrated reasoning. It reads all signals from its input; it calls no tools.

- Replace the AgentMail paragraph with:

> **AgentMail is mandatory.** The pipeline routes the persona's email through one of the rotating AgentMail inboxes (`AGENTMAIL_INBOXES`) before browsing — the blind agent just sees an ordinary address — and gives the Browsing Agent a neutral `read_email_inbox` tool (`email_evidence.read_inbox_text`) so it can read a verification code or confirmation link mid-flow and finish a registration or checkout that would otherwise stall. `make_client()` raises if `AGENTMAIL_API_KEY` is unset. There is no post-hoc email-evidence signal: receiving mail (even authenticated mail) does not clear a site, since scam sites run email verification too.

- In **The blind-browser invariant** section, leave the forbidden-word list as-is. Add a sentence noting the `read_email_inbox` tool description and the `payment_explicitly_declined` field description are also user-framed and covered by the invariant.

- In **Tools convention**, the note already says WHOIS/TLS/DNS moved to `signals`. No change needed there.

- [ ] **Step 6: Run the full offline suite**

Run: `uv run pytest tests/test_models.py tests/test_persona.py tests/test_browsing.py tests/test_pipeline.py tests/test_email_evidence.py tests/test_signals.py -v`
Expected: PASS. Then confirm the chain imports:
Run: `uv run python -c "import anti_scam_agent.pipeline"`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/anti_scam_agent/analysis.py CLAUDE.md tests/test_analysis.py
git commit -m "feat: analysis judges on explicit-decline payment rule; update CLAUDE.md

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] Run the offline suite end to end:

Run: `uv run pytest tests/test_models.py tests/test_persona.py tests/test_browsing.py tests/test_pipeline.py tests/test_email_evidence.py tests/test_signals.py -v`
Expected: all PASS.

- [ ] Confirm no leftover references to removed concepts:

Run: `grep -rn "EmailEvidence\|collect_email_evidence\|credit_card_number_luhn_valid\|_poll_seconds\|card_tier" src/`
Expected: no matches in `src/`.

- [ ] Live tests (`test_analysis.py`, `test_tools.py`, `test_dependencies.py`) and a real end-to-end run are the user's call — do not run them automatically (cost + live keys).

- [ ] Report completion and propose opening the PR (per the user's "PR only after the buckets are done"), and surface that a manual live smoke run is advisable before merge.
