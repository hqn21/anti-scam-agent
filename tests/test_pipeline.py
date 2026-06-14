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
