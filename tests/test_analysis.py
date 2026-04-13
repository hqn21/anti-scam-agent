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
