import asyncio
import inspect

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment, Verdict

OBVIOUS_SCAM = BrowsingResult(
    website_summary="Site claims the user has won a lottery and asks for immediate payment of a small processing fee to release a large prize.",
    outgoing_links=["https://some-unrelated-cdn.biz"],
    login_attempted=True,
    login_outcome=Outcome.succeeded,
    credit_card_submitted=True,
    payment_outcome=Outcome.succeeded,
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
    login_outcome=Outcome.not_attempted,
    credit_card_submitted=False,
    payment_outcome=Outcome.not_attempted,
    form_fields_requested=[],
    unexpected_events=[],
)


def test_run_analysis_agent_accepts_static_signals():
    params = inspect.signature(run_analysis_agent).parameters
    assert "static_signals" in params


def _run(result: BrowsingResult, domain: str, static_signals=None):
    return asyncio.run(run_analysis_agent(result, domain, static_signals))


def test_analysis_agent_returns_assessment_for_scam_fixture():
    assessment, stage = _run(OBVIOUS_SCAM, "example.com")
    print("\n[SCAM FIXTURE]")
    print(assessment.model_dump_json(indent=2))
    assert isinstance(assessment, ScamAssessment)
    assert isinstance(assessment.verdict, Verdict)
    assert isinstance(assessment.is_scam, bool)
    assert stage.name == "analysis"
    assert stage.totals.total_tokens > 0


def test_analysis_agent_returns_assessment_for_legit_fixture():
    assessment, stage = _run(OBVIOUS_LEGIT, "example.com")
    print("\n[LEGIT FIXTURE]")
    print(assessment.model_dump_json(indent=2))
    assert isinstance(assessment, ScamAssessment)
    assert isinstance(assessment.verdict, Verdict)
    assert isinstance(assessment.is_scam, bool)
    assert stage.name == "analysis"
    assert stage.totals.total_tokens > 0


def test_system_prompt_encodes_explicit_decline_rule():
    from anti_scam_agent.analysis import _SYSTEM_PROMPT

    p = _SYSTEM_PROMPT.lower()
    # the payment rule's core reasoning is present
    assert "payment_explicitly_declined" in p
    assert "no real processor" in p
    # a hang/stall after submitting the card is treated as a signal, not abstained away
    assert "hung" in p or "stall" in p
    assert "credit_card_submitted is true" in p
    # the five ordinal verdict levels are spelled out
    for level in ("scam", "likely_scam", "uncertain", "likely_legitimate", "legitimate"):
        assert level in p
    # the dropped fields are gone, and old framings stay gone
    assert "confidence" not in p
    assert "is_scam" not in p
    assert "luhn_invalid" not in p
    assert "card tier" not in p
    assert "exoneration" not in p
