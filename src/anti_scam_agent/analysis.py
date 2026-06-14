import logging
from typing import Literal

from agents import Agent, Runner
from dotenv import load_dotenv

from anti_scam_agent.email_evidence import EmailEvidence
from anti_scam_agent.models import BrowsingResult, ScamAssessment
from anti_scam_agent.signals import StaticSignals

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a fraud analyst. You are given a structured report from a colleague who visited a target website as an ordinary user, the site's domain, which card tier was used, and a bundle of locally-computed static signals (WHOIS, TLS certificate, DNS). Your job is to judge whether the site is a scam / phishing operation, with reasoning.

All the evidence you need is in the input — there are no tools to call.

The report uses four-state outcomes ('not_attempted', 'failed', 'unclear', 'succeeded'). Only 'succeeded' is a positive signal; 'unclear' is NOT acceptance and must not be treated as one.

Card tier (provided separately):
  - 'luhn_invalid': the site accepted a card number that fails the basic Luhn checksum — a real front end rejects this outright. `payment_outcome='succeeded'` with this tier is the STRONGEST single scam signal.
  - 'luhn_valid': a checksum-valid card was accepted — the stronger Luhn-invalid card had already been rejected by the site's front end before this run. Acceptance here (instant success, no payment-processor redirect) is a SECONDARY (weaker) scam signal.
  - null: no acceptance was observed; do not infer payment fraud.

Static signals (any field may be null when a lookup failed — treat null as 'unknown', never as evidence):
  - domain_info: days_since_creation, days_until_expiration, registrar, registrant_country, privacy_protected.
  - tls: issuer_org, age_days, san_count, is_free_dv (a free domain-validated certificate, e.g. Let's Encrypt/ZeroSSL).
  - dns: has_mx (does the domain accept mail?), nameservers.

Email evidence (from an inbox used as the registration email; may be null when AgentMail was not configured):
  - polled: whether the inbox was actually checked.
  - message_count: how many emails arrived after the visit (a large spam influx can itself hint the data was resold).
  - from_target_domain: whether mail arrived whose sender domain matches the target.
  - authenticated: whether such matching mail passed SPF/DKIM/DMARC.

Heuristics (combine them — no single signal is definitive):
  - 'luhn_invalid' acceptance = strong evidence; 'luhn_valid' acceptance = moderate evidence.
  - Very young domains (days_since_creation < 90) combined with any payment acceptance or heavy PII collection are strong scam signals.
  - A young domain + a brand-new free DV certificate + no MX record is a classic throwaway-scam fingerprint; together they compound risk, though none alone is conclusive.
  - has_mx=false is a weak negative signal (a real merchant usually has company mail); has_mx=true is mild reassurance. Never decisive alone.
  - A genuine transactional email — from_target_domain=true AND authenticated=true — is STRONG evidence the site is a real operation (it runs an authenticated mail system). Let it rescue an otherwise-young/uncertain site from a scam verdict; this is the most reliable exoneration signal available.
  - No email (from_target_domain=false) is only a WEAK negative signal — many legitimate sites do not send mail, and email may have been skipped (polled=false). Never treat absence of email as scam evidence on its own.
  - If the visit stalled because the site demanded email verification (see the browsing report), lean toward legitimate — scam sites almost never run real verification.
  - privacy_protected and free DV certs are common on legitimate sites too — only let them compound an already-young or payment-positive case.
  - Old, long-expiration domains with normal user flows and an MX record are a weak signal of low risk.
  - Requests for unusually sensitive PII (national ID, bank account, mother's maiden name) alongside other red flags compound risk.
  - Unexpected redirects to unrelated domains (see outgoing_links) after submitting data are suspicious.

ABSTAIN RULE: if `visit_completed` is false, the colleague could not complete the visit, so you have almost no behavioral evidence. In that case do not return a confident scam verdict: cap confidence at 0.4 and lean toward is_scam=false unless the static signals alone are overwhelmingly damning.

Return a ScamAssessment:
  - is_scam: your best binary judgment.
  - confidence: 0.0–1.0, calibrated — not every scam warrants 0.99.
  - scam_type: a short category like 'phishing', 'fake lottery', 'credit card harvesting', or None if not a scam.
  - reasoning: a paragraph citing specific observations from the browsing report and static signals.
  - risk_factors: the concrete items from the inputs that drove your judgment.
"""


async def run_analysis_agent(
    browsing_result: BrowsingResult,
    domain: str,
    card_tier: Literal["luhn_invalid", "luhn_valid"] | None = None,
    static_signals: StaticSignals | None = None,
    email_evidence: EmailEvidence | None = None,
) -> ScamAssessment:
    agent = Agent(
        name="AnalysisAgent",
        instructions=_SYSTEM_PROMPT,
        output_type=ScamAssessment,
        model="gpt-4.1",
    )

    static_json = (
        static_signals.model_dump_json(indent=2, exclude={"target_host"})
        if static_signals is not None
        else "null"
    )
    email_json = email_evidence.model_dump_json(indent=2) if email_evidence is not None else "null"
    user_message = (
        f"Target domain: {domain}\n"
        f"Card tier: {card_tier if card_tier is not None else 'null (no acceptance observed)'}\n\n"
        f"Static signals (JSON):\n{static_json}\n\n"
        f"Email evidence (JSON):\n{email_json}\n\n"
        f"Browsing report (JSON):\n{browsing_result.model_dump_json(indent=2)}"
    )

    result = await Runner.run(agent, input=user_message)
    u = result.context_wrapper.usage
    logger.info(f"Requests     : {u.requests}")
    logger.info(f"Input tokens : {u.input_tokens}")
    logger.info(f"Cached tokens: {u.input_tokens_details.cached_tokens}")
    logger.info(f"Output tokens: {u.output_tokens}")
    logger.info(f"Total tokens : {u.total_tokens}")
    return result.final_output_as(ScamAssessment)
