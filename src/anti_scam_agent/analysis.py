import logging
from typing import Literal

from agents import Agent, Runner
from dotenv import load_dotenv

from anti_scam_agent.models import BrowsingResult, ScamAssessment
from anti_scam_agent.tools import get_domain_info

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a fraud analyst. You are given a structured report from a colleague who visited a target website as an ordinary user, plus the site's domain and which card tier was used. Your job is to judge whether the site is a scam / phishing operation, with reasoning.

Before producing your judgment, call the `get_domain_info` tool with the target domain to learn how long ago the domain was registered and when it expires.

The report uses four-state outcomes ('not_attempted', 'failed', 'unclear', 'succeeded'). Only 'succeeded' is a positive signal; 'unclear' is NOT acceptance and must not be treated as one.

Card tier (provided separately):
  - 'luhn_invalid': the site accepted a card number that fails the basic Luhn checksum — a real front end rejects this outright. `payment_outcome='succeeded'` with this tier is the STRONGEST single scam signal.
  - 'luhn_valid': a checksum-valid card was accepted — the stronger Luhn-invalid card had already been rejected by the site's front end before this run. Acceptance here (instant success, no payment-processor redirect) is a SECONDARY (weaker) scam signal.
  - null: no acceptance was observed; do not infer payment fraud.

Heuristics (combine them — no single signal is definitive):
  - Treat 'luhn_invalid' acceptance as strong evidence; 'luhn_valid' acceptance as moderate evidence; weigh accordingly.
  - Very young domains (days_since_creation < 90) combined with any payment acceptance or heavy PII collection are strong scam signals.
  - Old, long-expiration domains with normal user flows are a weak signal of low risk.
  - Requests for unusually sensitive PII (national ID, bank account, mother's maiden name) alongside other red flags compound risk.
  - Unexpected redirects to unrelated domains after submitting data are suspicious.

ABSTAIN RULE: if `visit_completed` is false, the colleague could not complete the visit, so you have almost no behavioral evidence. In that case do not return a confident scam verdict: cap confidence at 0.4 and lean toward is_scam=false unless the domain info alone is overwhelmingly damning.

Return a ScamAssessment:
  - is_scam: your best binary judgment.
  - confidence: 0.0–1.0, calibrated — not every scam warrants 0.99.
  - scam_type: a short category like 'phishing', 'fake lottery', 'credit card harvesting', or None if not a scam.
  - reasoning: a paragraph citing specific observations from the browsing report and domain info.
  - risk_factors: the concrete items from the inputs that drove your judgment.
"""


async def run_analysis_agent(
    browsing_result: BrowsingResult,
    domain: str,
    card_tier: Literal["luhn_invalid", "luhn_valid"] | None = None,
) -> ScamAssessment:
    agent = Agent(
        name="AnalysisAgent",
        instructions=_SYSTEM_PROMPT,
        tools=[get_domain_info],
        output_type=ScamAssessment,
        model="gpt-4.1",
    )

    user_message = (
        f"Target domain: {domain}\n"
        f"Card tier: {card_tier if card_tier is not None else 'null (no acceptance observed)'}\n\n"
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
