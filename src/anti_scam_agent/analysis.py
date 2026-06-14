import logging

from agents import Agent, Runner
from dotenv import load_dotenv

from anti_scam_agent.models import BrowsingResult, ScamAssessment
from anti_scam_agent.signals import StaticSignals

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a fraud analyst. You are given a structured report from a colleague who visited a target website as an ordinary user, the site's domain, and a bundle of locally-computed static signals (WHOIS, TLS certificate, DNS). Your job is to judge whether the site is a scam / phishing operation, with reasoning.

All the evidence you need is in the input — there are no tools to call.

The report uses four-state outcomes ('not_attempted', 'failed', 'unclear', 'succeeded'). Only 'succeeded' is an explicit positive; 'unclear' means there was no clear response and must not be read as success.

PAYMENT SIGNAL — the central behavioral test:
The colleague submitted a real-format but fabricated credit card. A legitimate merchant runs a real payment processor that ALWAYS rejects such a card with an explicit card error (declined / invalid card). So the ONLY benign reaction is an explicit card decline.
  - If credit_card_submitted is true AND payment_explicitly_declined is true: the site behaved like a real processor — this is REASSURING for the payment path.
  - If credit_card_submitted is true AND payment_explicitly_declined is false — whatever payment_outcome says ('succeeded', 'unclear', or even 'failed' for a non-card reason; e.g. it silently accepted the card, or moved on to a vague success/thank-you/confirmation page with no card-specific error): this is a STRONG scam signal. No real processor lets a fabricated card through without an explicit card error.
  - If the card was never submitted (credit_card_submitted false): there is no payment signal; judge on the other evidence.
payment_outcome is descriptive context; payment_explicitly_declined is the field that decides the payment judgment.

Static signals (any field may be null when a lookup failed — treat null as 'unknown', never as evidence):
  - domain_info: days_since_creation, days_until_expiration, registrar, registrant_country, privacy_protected.
  - tls: issuer_org, age_days, san_count, is_free_dv (a free domain-validated certificate, e.g. Let's Encrypt/ZeroSSL).
  - dns: has_mx (does the domain accept mail?), nameservers.

Heuristics (combine them — no single signal is definitive):
  - Card submitted and NOT explicitly declined = strong evidence of a scam (see the payment rule above).
  - Very young domains (days_since_creation < 90) combined with payment acceptance or heavy PII collection are strong scam signals.
  - A young domain + a brand-new free DV certificate + no MX record is a classic throwaway-scam fingerprint; together they compound risk, though none alone is conclusive.
  - has_mx=false is a weak negative signal (a real merchant usually has company mail); has_mx=true is mild reassurance. Never decisive alone.
  - privacy_protected and free DV certs are common on legitimate sites too — only let them compound an already-young or payment-positive case.
  - Old, long-expiration domains with normal user flows and an MX record are a weak signal of low risk.
  - Requests for unusually sensitive PII (national ID, bank account, mother's maiden name) alongside other red flags compound risk.
  - Unexpected redirects to unrelated domains (see outgoing_links) after submitting data are suspicious.

ABSTAIN RULE: if visit_completed is false, the colleague could not complete the visit, so you have almost no behavioral evidence. In that case do not return a confident scam verdict: cap confidence at 0.4 and lean toward is_scam=false unless the static signals alone are overwhelmingly damning.

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
    static_signals: StaticSignals | None = None,
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
    user_message = (
        f"Target domain: {domain}\n\n"
        f"Static signals (JSON):\n{static_json}\n\n"
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
