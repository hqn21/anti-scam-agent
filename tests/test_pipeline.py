import asyncio
import contextlib

import pytest

import anti_scam_agent.pipeline as pipeline
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment, Verdict
from anti_scam_agent.reporting import LLMCallMetrics, StageReport
from anti_scam_agent.signals import StaticSignals


def _stage(name: str) -> StageReport:
    return StageReport.build(name=name, model=None, duration_s=0.0, steps=[], other_metrics=LLMCallMetrics())


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
        verdict=Verdict.legitimate, scam_type=None, reasoning="r", risk_factors=[]
    )


def _patch(monkeypatch, payment_sequence):
    """Stub browsing, analysis, static signals; capture args."""
    calls = {"browse": 0, "static": None, "persona_email": None, "browse_inbox": None}

    async def fake_browse(url, persona, client, inbox):
        calls["persona_email"] = persona.email
        calls["browse_inbox"] = inbox
        payment = payment_sequence[calls["browse"]]
        calls["browse"] += 1
        return _result(payment), _stage("browsing")

    async def fake_analyze(result, domain, static_signals):
        calls["static"] = static_signals
        return _assessment(), _stage("analysis")

    monkeypatch.setattr(pipeline, "run_browsing_agent", fake_browse)
    monkeypatch.setattr(pipeline, "run_analysis_agent", fake_analyze)
    monkeypatch.setattr(pipeline, "collect_static_signals", lambda url: StaticSignals(target_host="shop.test"))
    monkeypatch.setattr(pipeline, "make_client", lambda: object())
    monkeypatch.setattr(pipeline, "pick_inbox", lambda: "asalpha@agentmail.to")
    # Keep these orchestration tests hermetic: don't write report files or attach a
    # root-logger handler. Reporting itself is covered by tests/test_reporting.py.
    monkeypatch.setattr(pipeline, "run_debug_log", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(pipeline, "write_run_report", lambda report, logs_root, verbose=False: logs_root / "stub")
    return calls


@pytest.mark.parametrize("payment", [Outcome.succeeded, Outcome.failed, Outcome.unclear, Outcome.not_attempted])
def test_pipeline_runs_browsing_once(monkeypatch, payment):
    calls = _patch(monkeypatch, [payment])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse"] == 1


def test_static_signals_passed_to_analysis(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert isinstance(calls["static"], StaticSignals)
    assert calls["static"].target_host == "shop.test"


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


def test_browsing_receives_inbox(monkeypatch):
    # Confirms client+inbox are forwarded to browsing (the tool needs them),
    # not just that persona.email was set.
    calls = _patch(monkeypatch, [Outcome.unclear])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert calls["browse_inbox"] == "asalpha@agentmail.to"
