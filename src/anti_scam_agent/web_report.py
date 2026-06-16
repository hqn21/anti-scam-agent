"""Pure (LLM-free) mapping from pipeline outputs to a curated, JSON-serializable report
for the web app and extension. Excludes the per-step browsing transcript."""

from __future__ import annotations

from anti_scam_agent.models import BrowsingResult, ScamAssessment
from anti_scam_agent.reporting import RunReport
from anti_scam_agent.signals import StaticSignals


def build_curated_report(
    assessment: ScamAssessment,
    report: RunReport,
    signals: StaticSignals,
    observation: BrowsingResult,
) -> dict:
    di = signals.domain_info
    tls = signals.tls
    dns = signals.dns
    return {
        "url": report.url,
        "domain": report.target_domain,
        "started_at": report.started_at,
        "verdict": assessment.verdict.value,
        "is_scam": assessment.is_scam,
        "scam_type": assessment.scam_type,
        "payment_explicitly_declined": observation.payment_explicitly_declined,
        "reasoning": assessment.reasoning,
        "risk_factors": list(assessment.risk_factors),
        "observation": {
            "website_summary": observation.website_summary,
            "form_fields_requested": list(observation.form_fields_requested),
            "unexpected_events": list(observation.unexpected_events),
            "login_attempted": observation.login_attempted,
            "login_outcome": observation.login_outcome.value,
            "credit_card_submitted": observation.credit_card_submitted,
            "payment_outcome": observation.payment_outcome.value,
            "outgoing_links": list(observation.outgoing_links),
            "visit_completed": observation.visit_completed,
        },
        "signals": {
            "domain_age_days": di.days_since_creation if di else None,
            "domain_days_until_expiration": di.days_until_expiration if di else None,
            "registrar": di.registrar if di else None,
            "registrant_country": di.registrant_country if di else None,
            "privacy_protected": di.privacy_protected if di else None,
            "tls_issuer": tls.issuer_org if tls else None,
            "tls_age_days": tls.age_days if tls else None,
            "tls_is_free_dv": tls.is_free_dv if tls else None,
            "dns_has_mx": dns.has_mx if dns else None,
            "dns_nameservers": list(dns.nameservers) if dns else [],
        },
        "telemetry": {
            "duration_s": report.duration_s,
            "cost_usd": report.grand_total.cost_usd,
            "total_tokens": report.grand_total.total_tokens,
            "stages": [
                {"name": s.name, "duration_s": s.duration_s,
                 "total_tokens": s.totals.total_tokens, "cost_usd": s.totals.cost_usd}
                for s in report.stages
            ],
        },
    }
