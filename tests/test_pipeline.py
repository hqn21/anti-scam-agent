import asyncio

import pytest

import anti_scam_agent.pipeline as pipeline
from anti_scam_agent.models import BrowsingResult, FakePersona, Outcome, ScamAssessment


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
