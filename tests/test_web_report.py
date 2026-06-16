from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment, Verdict
from anti_scam_agent.reporting import LLMCallMetrics, RunReport, StageReport
from anti_scam_agent.signals import DnsInfo, StaticSignals, TlsInfo
from anti_scam_agent.tools.handler import DomainInfo
from anti_scam_agent.web_report import build_curated_report


def _browsing(scammy: bool) -> BrowsingResult:
    return BrowsingResult(
        website_summary="An online shop.",
        outgoing_links=["pay.example.net"] if scammy else [],
        login_attempted=True,
        login_outcome=Outcome.succeeded,
        credit_card_submitted=True,
        payment_outcome=Outcome.succeeded if scammy else Outcome.failed,
        payment_explicitly_declined=not scammy,
        form_fields_requested=["full name", "credit card"],
        unexpected_events=["payment confirmed instantly"] if scammy else [],
        visit_completed=True,
    )


def _report() -> RunReport:
    stage = StageReport.build(
        name="browsing", model="gpt-4.1", duration_s=12.0, steps=[],
        other_metrics=LLMCallMetrics(total_tokens=1000, cost_usd=0.02),
    )
    return RunReport.build(
        target_domain="shop.test", url="http://shop.test",
        started_at="2026-06-16T10:00:00+08:00", duration_s=30.0, stages=[stage],
        verdict="scam", is_scam=True, scam_type="phishing",
    )


def _signals() -> StaticSignals:
    return StaticSignals(
        target_host="shop.test",
        domain_info=DomainInfo(domain="shop.test", days_since_creation=5,
                               days_until_expiration=360, registrar="NameCheap",
                               registrant_country="US", privacy_protected=True),
        tls=TlsInfo(issuer_org="Let's Encrypt", age_days=3, san_count=1, is_free_dv=True),
        dns=DnsInfo(has_mx=False, nameservers=["ns1.example.com"]),
    )


def test_curated_has_headline_and_signal():
    a = ScamAssessment(verdict=Verdict.scam, scam_type="phishing",
                       reasoning="No real card decline.", risk_factors=["instant confirm"])
    c = build_curated_report(a, _report(), _signals(), _browsing(scammy=True))
    assert c["verdict"] == "scam"
    assert c["is_scam"] is True
    assert c["payment_explicitly_declined"] is False
    assert c["reasoning"] == "No real card decline."
    assert c["risk_factors"] == ["instant confirm"]
    assert c["url"] == "http://shop.test"
    assert c["domain"] == "shop.test"


def test_curated_includes_observation_and_signals():
    a = ScamAssessment(verdict=Verdict.scam, scam_type="phishing", reasoning="r", risk_factors=[])
    c = build_curated_report(a, _report(), _signals(), _browsing(scammy=True))
    assert c["observation"]["website_summary"] == "An online shop."
    assert "credit card" in c["observation"]["form_fields_requested"]
    assert c["observation"]["outgoing_links"] == ["pay.example.net"]
    assert c["signals"]["domain_age_days"] == 5
    assert c["signals"]["tls_issuer"] == "Let's Encrypt"
    assert c["signals"]["tls_is_free_dv"] is True
    assert c["signals"]["dns_has_mx"] is False
    assert c["telemetry"]["duration_s"] == 30.0
    assert c["telemetry"]["total_tokens"] == 1000


def test_curated_is_json_serializable():
    import json
    a = ScamAssessment(verdict=Verdict.legitimate, scam_type=None, reasoning="r", risk_factors=[])
    c = build_curated_report(a, _report(), StaticSignals(target_host="x"), _browsing(scammy=False))
    json.dumps(c)  # must not raise
    assert c["signals"]["domain_age_days"] is None
