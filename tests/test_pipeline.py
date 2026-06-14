import asyncio

import pytest

import anti_scam_agent.pipeline as pipeline
from anti_scam_agent.email_evidence import EmailEvidence
from anti_scam_agent.models import BrowsingResult, FakePersona, Outcome, ScamAssessment
from anti_scam_agent.signals import StaticSignals


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


def test_invalid_card_accepted_stops_after_one_run(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.succeeded])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse"] == 1
    assert calls["card_tier"] == "luhn_invalid"


def test_invalid_rejected_then_valid_accepted_runs_twice(monkeypatch):
    known = FakePersona(
        name="Test User",
        email="t@x.com",
        password="password1234",
        phone="555-0000",
        address="1 Main St",
        credit_card_number="4111111111111112",  # Luhn-invalid primary
        credit_card_number_luhn_valid="4111111111111111",  # Luhn-valid fallback
        credit_card_expiry="12/30",
        credit_card_cvv="123",
    )
    monkeypatch.setattr(pipeline, "generate_persona", lambda: known)
    calls = _patch(monkeypatch, [Outcome.failed, Outcome.succeeded])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse"] == 2
    # Run 1 used the invalid primary; Run 2 used the specific Luhn-valid fallback.
    assert calls["cards"][0] == known.credit_card_number
    assert calls["cards"][1] == known.credit_card_number_luhn_valid
    assert calls["card_tier"] == "luhn_valid"


@pytest.mark.parametrize("payment", [Outcome.unclear, Outcome.not_attempted])
def test_non_failure_payment_does_not_trigger_second_run(monkeypatch, payment):
    calls = _patch(monkeypatch, [payment])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse"] == 1
    assert calls["card_tier"] is None


@pytest.mark.parametrize("second", [Outcome.failed, Outcome.unclear, Outcome.not_attempted])
def test_second_run_without_success_leaves_card_tier_none(monkeypatch, second):
    calls = _patch(monkeypatch, [Outcome.failed, second])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse"] == 2
    assert calls["card_tier"] is None


def test_static_signals_passed_to_analysis(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert isinstance(calls["static"], StaticSignals)
    assert calls["static"].target_host == "shop.test"


def test_email_evidence_collected_when_configured(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])
    monkeypatch.setattr(pipeline, "make_client", lambda: object())
    monkeypatch.setattr(pipeline, "pick_inbox", lambda: "asalpha@agentmail.to")
    monkeypatch.setattr(
        pipeline, "collect_email_evidence",
        lambda client, inbox, target, since, poll_seconds: EmailEvidence(polled=True, from_target_domain=True, authenticated=True),
    )
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["persona_email"] == "asalpha@agentmail.to"
    assert isinstance(calls["email"], EmailEvidence)
    assert calls["email"].from_target_domain is True


def test_email_skipped_when_unconfigured(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["email"] is None


def test_poll_seconds_defaults_on_bad_env(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_POLL_SECONDS", "not-a-number")
    assert pipeline._poll_seconds() == 120


def test_poll_seconds_reads_valid_env(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_POLL_SECONDS", "30")
    assert pipeline._poll_seconds() == 30
