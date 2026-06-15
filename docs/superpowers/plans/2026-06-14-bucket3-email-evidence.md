# Bucket 3 — AgentMail Email Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a high-value exoneration signal — genuine transactional email — by using an AgentMail inbox as the persona's email, then (after browsing) polling that inbox for mail attributable to the target domain and checking its authentication, feeding the result to the Analysis Agent.

**Architecture:** The pipeline picks one of N AgentMail inboxes (round-robin), sets it as `persona.email`, records a scan-start timestamp, runs the browsing flow, then polls that inbox (failure-tolerant, time-boxed, early-exit) for messages whose sender domain matches the target and whose `unauthenticated` label is absent (SPF/DKIM/DMARC passed). The result is an `EmailEvidence` object passed to `run_analysis_agent` as a 5th input. When `AGENTMAIL_API_KEY` is unset the whole step is skipped and the persona keeps its generated email — the pipeline still reaches analysis.

**Tech Stack:** Python 3.12, Pydantic v2, `agentmail` SDK (new dep), `openai-agents`, pytest, `uv`.

**Verified API surface (agentmail 0.5.5):**
- `from agentmail import AgentMail; client = AgentMail(api_key=...)`.
- `client.inboxes.messages.list(inbox_id=..., after=<datetime>, ascending=False, limit=..., include_unauthenticated=True)` → `ListMessagesResponse(count, messages: list[MessageItem])`.
- `MessageItem` fields used: `from_: str`, `timestamp: datetime`, `labels: list[str]`, `subject: str | None`.
- Auth model: AgentMail **drops** mail whose SPF/DKIM/DMARC headers are present and explicitly fail; mail with **missing** auth headers gets an `unauthenticated` label. So a received message with no `unauthenticated` label = authenticated. We pass `include_unauthenticated=True` so we can see and label both.

**Design decisions (locked for v1):**
- Email evidence is a **separate 5th argument** to `run_analysis_agent` (`email_evidence`), with its own input block — `StaticSignals` stays "static" (WHOIS/TLS/DNS) and is not polluted with behavioral mail data.
- **Attribution** = sender domain equals the target host, or is a sub/parent domain of it (`endswith` either way) — avoids a public-suffix-list dependency while catching `mail.shop.com` for target `shop.com`.
- **Poll** default 120s, 10s interval, **early-exit** as soon as a domain-matching message arrives (so legitimate fast-senders don't wait the full window; only no-mail sites pay it). All tunable via env.
- **Graceful degradation:** no `AGENTMAIL_API_KEY` → skip email entirely; any AgentMail error → `EmailEvidence(polled=False)`. Never raises.
- **No verification-link click-through** (a site blocking on email verification is reported neutrally by the blind agent and is treated by analysis as *leaning legitimate*).

---

## File Structure

- `pyproject.toml` / `.env.example` — add `agentmail`; document `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOXES`, `AGENTMAIL_POLL_SECONDS`.
- `src/anti_scam_agent/email_evidence.py` (new) — `EmailEvidence` model; pure helpers (`_sender_domain`, `_domain_matches`, `_is_authenticated`, `_evidence_from_messages`); `pick_inbox()`; `collect_email_evidence(client, inbox, target_host, since, ...)`; `make_client()` (None when unconfigured).
- `src/anti_scam_agent/pipeline.py` — pick inbox + override `persona.email`, record `since`, gather static + email signals, pass `email_evidence` to analysis.
- `src/anti_scam_agent/analysis.py` — `email_evidence` param + input block + heuristics.
- `CLAUDE.md` — note the email-evidence stage.
- Tests: `tests/test_email_evidence.py` (new, offline), `tests/test_pipeline.py`, `tests/test_analysis.py`.

---

### Task 1: `email_evidence.py` — model, pure helpers, poller

**Files:**
- Modify: `pyproject.toml`, `.env.example`
- Create: `src/anti_scam_agent/email_evidence.py`
- Test: `tests/test_email_evidence.py` (create)

- [ ] **Step 1: Add the dependency and env docs**

Run: `uv add agentmail`. Verify: `uv run python -c "from agentmail import AgentMail; print('ok')"` → `ok`.

Append to `.env.example`:
```
# AgentMail (optional — enables the transactional-email signal). Without it the email step is skipped.
AGENTMAIL_API_KEY=
# Comma-separated inboxes to rotate through (defaults to the three below if unset).
AGENTMAIL_INBOXES=asalpha@agentmail.to,asbravo@agentmail.to,ascharlie@agentmail.to
# Seconds to poll an inbox after browsing before giving up (early-exits on a domain match).
AGENTMAIL_POLL_SECONDS=120
```

- [ ] **Step 2: Write the failing offline tests**

Create `tests/test_email_evidence.py`:

```python
import datetime
from types import SimpleNamespace

import anti_scam_agent.email_evidence as ev
from anti_scam_agent.email_evidence import (
    EmailEvidence,
    _domain_matches,
    _evidence_from_messages,
    _is_authenticated,
    _sender_domain,
    collect_email_evidence,
    pick_inbox,
)


def _msg(from_, labels=(), subject="Welcome", when=None):
    return SimpleNamespace(
        from_=from_,
        labels=list(labels),
        subject=subject,
        timestamp=when or datetime.datetime(2026, 6, 14, 12, 0, tzinfo=datetime.timezone.utc),
    )


def test_sender_domain_extraction():
    assert _sender_domain("No Reply <noreply@mail.Shop.com>") == "mail.shop.com"
    assert _sender_domain("plain@shop.com") == "shop.com"
    assert _sender_domain("garbage") == ""


def test_domain_matches_exact_and_subdomain():
    assert _domain_matches("shop.com", "shop.com") is True
    assert _domain_matches("mail.shop.com", "shop.com") is True  # ESP subdomain
    assert _domain_matches("shop.com", "checkout.shop.com") is True  # parent
    assert _domain_matches("evil.com", "shop.com") is False


def test_is_authenticated_uses_unauthenticated_label():
    assert _is_authenticated(["inbound"]) is True
    assert _is_authenticated(["inbound", "unauthenticated"]) is False


def test_evidence_from_messages_strong_case():
    msgs = [_msg("noreply@shop.com", labels=["inbound"])]
    e = _evidence_from_messages(msgs, "shop.com")
    assert e.polled is True
    assert e.message_count == 1
    assert e.from_target_domain is True
    assert e.authenticated is True


def test_evidence_from_messages_unauthenticated_domain_mail():
    msgs = [_msg("noreply@shop.com", labels=["inbound", "unauthenticated"])]
    e = _evidence_from_messages(msgs, "shop.com")
    assert e.from_target_domain is True
    assert e.authenticated is False


def test_evidence_from_messages_unrelated_only():
    msgs = [_msg("promo@randomcdn.biz", labels=["inbound"])]
    e = _evidence_from_messages(msgs, "shop.com")
    assert e.message_count == 1
    assert e.from_target_domain is False
    assert e.authenticated is None  # no domain-matching mail -> unknown


def test_pick_inbox_rotates(monkeypatch):
    monkeypatch.setattr(ev, "_get_inboxes", lambda: ["a@x.to", "b@x.to", "c@x.to"])
    ev._reset_inbox_rotation()
    picks = [pick_inbox() for _ in range(4)]
    assert picks == ["a@x.to", "b@x.to", "c@x.to", "a@x.to"]


def test_collect_email_evidence_early_exits_on_match():
    since = datetime.datetime(2026, 6, 14, 11, 0, tzinfo=datetime.timezone.utc)
    calls = {"n": 0}

    class FakeMessages:
        def list(self, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                return SimpleNamespace(count=1, messages=[_msg("noreply@shop.com", labels=["inbound"])])
            return SimpleNamespace(count=0, messages=[])

    client = SimpleNamespace(inboxes=SimpleNamespace(messages=FakeMessages()))
    e = collect_email_evidence(client, "in@x.to", "shop.com", since, poll_seconds=60, interval=0)
    assert e.from_target_domain is True
    assert calls["n"] == 2  # stopped as soon as the match arrived


def test_collect_email_evidence_failure_tolerant():
    class Boom:
        def list(self, **kwargs):
            raise RuntimeError("api down")

    client = SimpleNamespace(inboxes=SimpleNamespace(messages=Boom()))
    e = collect_email_evidence(client, "in@x.to", "shop.com",
                               datetime.datetime.now(datetime.timezone.utc), poll_seconds=0, interval=0)
    assert isinstance(e, EmailEvidence)
    assert e.polled is False
```

Run `uv run pytest tests/test_email_evidence.py -v` → expect FAIL (module missing).

- [ ] **Step 3: Implement `src/anti_scam_agent/email_evidence.py`**

```python
import logging
import os
import time
from datetime import datetime
from email.utils import parseaddr

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_INBOXES = ["asalpha@agentmail.to", "asbravo@agentmail.to", "ascharlie@agentmail.to"]
_inbox_index = 0


class EmailEvidence(BaseModel):
    polled: bool = False  # did we successfully query the inbox at all
    message_count: int = 0  # messages received in the post-scan window (volume signal)
    from_target_domain: bool = False  # received mail whose sender domain matches the target
    authenticated: bool | None = None  # a domain-matching message passed SPF/DKIM/DMARC; None if none matched


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
    inbox = inboxes[_inbox_index % len(inboxes)]
    _inbox_index += 1
    return inbox


def _sender_domain(from_field: str) -> str:
    addr = parseaddr(from_field)[1]
    _, _, domain = addr.partition("@")
    return domain.strip().lower()


def _domain_matches(sender_domain: str, target_host: str) -> bool:
    s = sender_domain.lower().removeprefix("www.")
    t = target_host.lower().removeprefix("www.")
    if not s or not t:
        return False
    return s == t or s.endswith("." + t) or t.endswith("." + s)


def _is_authenticated(labels: list[str]) -> bool:
    return "unauthenticated" not in labels


def _evidence_from_messages(messages, target_host: str) -> EmailEvidence:
    matched = [m for m in messages if _domain_matches(_sender_domain(m.from_), target_host)]
    authenticated: bool | None = None
    if matched:
        authenticated = any(_is_authenticated(list(m.labels)) for m in matched)
    return EmailEvidence(
        polled=True,
        message_count=len(messages),
        from_target_domain=bool(matched),
        authenticated=authenticated,
    )


def make_client():
    """Return an AgentMail client, or None when unconfigured (email step is skipped)."""
    api_key = os.getenv("AGENTMAIL_API_KEY")
    if not api_key:
        return None
    try:
        from agentmail import AgentMail

        return AgentMail(api_key=api_key)
    except Exception as e:  # noqa: BLE001
        logger.warning("AgentMail client unavailable: %s", e)
        return None


def collect_email_evidence(
    client,
    inbox: str,
    target_host: str,
    since: datetime,
    poll_seconds: int = 120,
    interval: float = 10.0,
) -> EmailEvidence:
    """Poll the inbox until a domain-matching message arrives or the window closes.

    Never raises: any failure yields EmailEvidence(polled=False).
    """
    deadline = time.monotonic() + poll_seconds
    last: EmailEvidence | None = None
    while True:
        try:
            resp = client.inboxes.messages.list(
                inbox_id=inbox,
                after=since,
                ascending=False,
                limit=50,
                include_unauthenticated=True,
            )
            last = _evidence_from_messages(list(resp.messages), target_host)
            if last.from_target_domain:
                return last  # early exit — got what we came for
        except Exception as e:  # noqa: BLE001 — email signal must never break the pipeline
            logger.warning("email evidence poll failed for %s: %s", inbox, e)
            return EmailEvidence(polled=False)
        if time.monotonic() >= deadline:
            return last or EmailEvidence(polled=True)
        time.sleep(interval)
```

- [ ] **Step 4: Run the offline tests**

Run: `uv run pytest tests/test_email_evidence.py -v`
Expected: PASS (9 tests). The `interval=0` in tests makes the poll loop spin without real delay.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .env.example src/anti_scam_agent/email_evidence.py tests/test_email_evidence.py
git commit -m "feat: AgentMail email-evidence collector with failure-tolerant polling

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire email evidence into the pipeline

**Files:**
- Modify: `src/anti_scam_agent/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Update tests**

In `tests/test_pipeline.py`, extend the `_patch` helper so `fake_analyze` takes a 5th arg and email collection is stubbed out. Replace `_patch` with:

```python
def _patch(monkeypatch, payment_sequence):
    """Stub browsing, analysis, static + email signals; capture args."""
    calls = {"browse": 0, "cards": [], "card_tier": None, "static": None, "email": None, "persona_email": None}

    async def fake_browse(url, persona):
        calls["cards"].append(persona.credit_card_number)
        calls["persona_email"] = persona.email
        payment = payment_sequence[calls["browse"]]
        calls["browse"] += 1
        return _result(payment)

    async def fake_analyze(result, domain, card_tier, static_signals, email_evidence):
        calls["card_tier"] = card_tier
        calls["static"] = static_signals
        calls["email"] = email_evidence
        return _assessment()

    monkeypatch.setattr(pipeline, "run_browsing_agent", fake_browse)
    monkeypatch.setattr(pipeline, "run_analysis_agent", fake_analyze)
    monkeypatch.setattr(pipeline, "collect_static_signals", lambda url: StaticSignals(target_host="shop.test"))
    monkeypatch.setattr(pipeline, "make_client", lambda: None)  # email disabled by default in tests
    return calls
```

Add a test that, when AgentMail is configured, the persona email is swapped to the picked inbox and evidence reaches analysis:

```python
from anti_scam_agent.email_evidence import EmailEvidence


def test_email_evidence_collected_when_configured(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])
    monkeypatch.setattr(pipeline, "make_client", lambda: object())
    monkeypatch.setattr(pipeline, "pick_inbox", lambda: "asalpha@agentmail.to")
    monkeypatch.setattr(
        pipeline, "collect_email_evidence",
        lambda client, inbox, target, since, poll_seconds: EmailEvidence(polled=True, from_target_domain=True, authenticated=True),
    )
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["persona_email"] == "asalpha@agentmail.to"  # blind agent used the inbox
    assert isinstance(calls["email"], EmailEvidence)
    assert calls["email"].from_target_domain is True


def test_email_skipped_when_unconfigured(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])  # make_client -> None
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["email"] is None  # no email step
```

Run `uv run pytest tests/test_pipeline.py -v` → expect FAIL (pipeline doesn't pass email_evidence yet).

- [ ] **Step 2: Implement the wiring**

Replace the contents of `src/anti_scam_agent/pipeline.py` with:

```python
import asyncio
import os
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.email_evidence import (
    EmailEvidence,
    collect_email_evidence,
    make_client,
    pick_inbox,
)
from anti_scam_agent.models import Outcome, ScamAssessment
from anti_scam_agent.persona import generate_persona
from anti_scam_agent.signals import collect_static_signals


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    domain = _extract_domain(url)

    # If AgentMail is configured, route the persona's email through a real inbox so we
    # can later check whether the site sent genuine transactional mail.
    client = make_client()
    since = datetime.now(timezone.utc)
    if client is not None:
        persona = persona.model_copy(update={"email": pick_inbox()})
    inbox = persona.email

    # Run 1: a Luhn-invalid card. Acceptance here is the strongest signal.
    result = await run_browsing_agent(url, persona)
    card_tier: Literal["luhn_invalid", "luhn_valid"] | None = None

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

    # Out-of-band signals, off the event loop. Both are failure-tolerant.
    static_signals = await asyncio.to_thread(collect_static_signals, url)

    email_evidence: EmailEvidence | None = None
    if client is not None:
        poll_seconds = int(os.getenv("AGENTMAIL_POLL_SECONDS", "120"))
        email_evidence = await asyncio.to_thread(
            collect_email_evidence, client, inbox, domain, since, poll_seconds
        )

    return await run_analysis_agent(result, domain, card_tier, static_signals, email_evidence)
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS (all pipeline tests, including the two new email tests).

- [ ] **Step 4: Commit**

```bash
git add src/anti_scam_agent/pipeline.py tests/test_pipeline.py
git commit -m "feat: route persona email through AgentMail inbox and collect evidence in pipeline

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Analysis consumes email evidence

**Files:**
- Modify: `src/anti_scam_agent/analysis.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Add the failing offline guard**

Add to `tests/test_analysis.py`:

```python
def test_run_analysis_agent_accepts_email_evidence():
    params = inspect.signature(run_analysis_agent).parameters
    assert "email_evidence" in params
```

Run `uv run pytest tests/test_analysis.py::test_run_analysis_agent_accepts_email_evidence -v` → expect FAIL.

- [ ] **Step 2: Update `analysis.py`**

Add the import near the top:

```python
from anti_scam_agent.email_evidence import EmailEvidence
```

Insert this block into `_SYSTEM_PROMPT`, immediately AFTER the "Static signals (...)" bullet list and BEFORE the "Heuristics" line:

```
Email evidence (from an inbox used as the registration email; may be null when AgentMail was not configured):
  - polled: whether the inbox was actually checked.
  - message_count: how many emails arrived after the visit (a large spam influx can itself hint the data was resold).
  - from_target_domain: whether mail arrived whose sender domain matches the target.
  - authenticated: whether such matching mail passed SPF/DKIM/DMARC.
```

Add these lines to the Heuristics list (after the existing has_mx bullet):

```
  - A genuine transactional email — from_target_domain=true AND authenticated=true — is STRONG evidence the site is a real operation (it runs an authenticated mail system). Let it rescue an otherwise-young/uncertain site from a scam verdict; this is the most reliable exoneration signal available.
  - No email (from_target_domain=false) is only a WEAK negative signal — many legitimate sites do not send mail, and email may have been skipped (polled=false). Never treat absence of email as scam evidence on its own.
  - If the visit stalled because the site demanded email verification (see the browsing report), lean toward legitimate — scam sites almost never run real verification.
```

Change the function signature to add the 5th parameter:

```python
async def run_analysis_agent(
    browsing_result: BrowsingResult,
    domain: str,
    card_tier: Literal["luhn_invalid", "luhn_valid"] | None = None,
    static_signals: StaticSignals | None = None,
    email_evidence: EmailEvidence | None = None,
) -> ScamAssessment:
```

In the body, build an email JSON block and add it to the user message. After the `static_json = ...` line add:

```python
    email_json = email_evidence.model_dump_json(indent=2) if email_evidence is not None else "null"
```

and change `user_message` to include the email block right after the static-signals block:

```python
    user_message = (
        f"Target domain: {domain}\n"
        f"Card tier: {card_tier if card_tier is not None else 'null (no acceptance observed)'}\n\n"
        f"Static signals (JSON):\n{static_json}\n\n"
        f"Email evidence (JSON):\n{email_json}\n\n"
        f"Browsing report (JSON):\n{browsing_result.model_dump_json(indent=2)}"
    )
```

- [ ] **Step 3: Update the live-test helper**

In `tests/test_analysis.py`, extend `_run` to forward email evidence:

```python
def _run(result: BrowsingResult, domain: str, card_tier: str | None = None, static_signals=None, email_evidence=None) -> ScamAssessment:
    return asyncio.run(run_analysis_agent(result, domain, card_tier, static_signals, email_evidence))
```

- [ ] **Step 4: Verify (offline only — do NOT run live OpenAI)**

Run: `uv run pytest tests/test_analysis.py -k "card_tier or static_signals or email_evidence" -v` → PASS.
Run: `uv run pytest tests/test_analysis.py --collect-only -q` → collects cleanly.
Run: `PYTHONPATH=src uv run python -c "import anti_scam_agent.pipeline, anti_scam_agent.analysis; print('ok')"` → `ok`.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/analysis.py tests/test_analysis.py
git commit -m "feat: analysis weighs transactional-email evidence as strong exoneration

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Verification + CLAUDE.md + final review

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

In the architecture section, add a sentence to the Analysis Agent description (or the architecture notes) explaining that, when `AGENTMAIL_API_KEY` is configured, the pipeline routes the persona's email through one of the rotating AgentMail inboxes, polls it after browsing for transactional mail attributable to the target domain, and passes the resulting `EmailEvidence` to the Analysis Agent as input. Note that absence of `AGENTMAIL_API_KEY` cleanly skips the step. Keep the edit faithful and minimal — read the relevant lines first.

- [ ] **Step 2: Run the offline suite**

Run: `uv run pytest tests/test_models.py tests/test_persona.py tests/test_browsing.py tests/test_pipeline.py tests/test_signals.py tests/test_email_evidence.py -q` and `uv run pytest tests/test_analysis.py -k "card_tier or static_signals or email_evidence" tests/test_tools.py -k builder -q`.
Expected: all PASS.

- [ ] **Step 3: Confirm full collection + chain import**

Run: `uv run pytest --collect-only -q` (clean) and `PYTHONPATH=src uv run python -c "import anti_scam_agent.pipeline; print('ok')"`.

- [ ] **Step 4: Commit the doc**

```bash
git add CLAUDE.md
git commit -m "docs: describe AgentMail email-evidence stage in CLAUDE.md

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: Live smoke test (optional, network/paid — ask first)**

A real end-to-end email check needs `AGENTMAIL_API_KEY` set and a real send to one of the inboxes; the live OpenAI analysis test also costs money. Run only with the user's go-ahead.

---

## Self-Review notes

- **Spec coverage:** AgentMail inbox as persona email (Task 2), 3-inbox round-robin (Task 1 `pick_inbox`), post-browse polling with sender-domain + time-window attribution (Task 1), SPF/DKIM via the `unauthenticated` label → `authenticated` (Task 1), spam volume via `message_count` (Task 1), evidence to analysis as input + strong-exoneration heuristic (Task 3), verification-stall leans-legit (Task 3 prompt), graceful skip without a key (Task 2). No verification-link click-through (deferred, documented).
- **Failure tolerance:** `make_client` returns None on any error; `collect_email_evidence` returns `EmailEvidence(polled=False)` on any API failure; the pipeline only adds the email step when a client exists; analysis treats null/`polled=false`/`from_target_domain=false` as non-evidence.
- **Blind invariant:** the browsing agent only ever sees an ordinary email address (the inbox), set via `persona.email`; nothing about AgentMail, polling, or fraud framing reaches its prompt or `BrowsingResult` schema. Email evidence is computed entirely out-of-band after the visit.
- **Type consistency:** `EmailEvidence(polled, message_count, from_target_domain, authenticated)`; `collect_email_evidence(client, inbox, target_host, since, poll_seconds=120, interval=10.0)`; `pick_inbox() -> str`; `make_client() -> AgentMail | None`; `run_analysis_agent(..., email_evidence: EmailEvidence | None = None)` — consistent across Tasks 1–3.
