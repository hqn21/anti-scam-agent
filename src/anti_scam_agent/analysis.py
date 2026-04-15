import logging

from agents import Agent, Runner
from dotenv import load_dotenv

from anti_scam_agent.models import BrowsingResult, ScamAssessment
from anti_scam_agent.tools import get_domain_info

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a fraud analyst. You are given a structured report from a colleague who visited a target website as an ordinary user, plus the site's domain. Your job is to judge whether the site is a scam / phishing operation, with reasoning.

Before producing your judgment, call the `get_domain_info` tool with the target domain to learn how long ago the domain was registered and when it expires.

Heuristics (combine them — no single signal is definitive):
  - A legitimate site validates payment details against a real payment processor. If the report shows `credit_card_submitted=true` and `credit_card_accepted=true` but describes an instant success without a processor redirect, treat this as strong evidence of scam: the site accepted card details that a real processor would have rejected.
  - Very young domains (days_since_creation < 90) combined with any payment acceptance or heavy PII collection are strong scam signals.
  - Old, long-expiration domains with normal user flows are a weak signal of low risk.
  - Requests for unusually sensitive PII (national ID, bank account, mother's maiden name) alongside other red flags compound risk.
  - Unexpected redirects to unrelated domains after submitting data are suspicious.

Return a ScamAssessment:
  - is_scam: your best binary judgment.
  - confidence: 0.0–1.0, calibrated — not every scam warrants 0.99.
  - scam_type: a short category like 'phishing', 'fake lottery', 'credit card harvesting', or None if not a scam.
  - reasoning: a paragraph citing specific observations from the browsing report and domain info.
  - risk_factors: the concrete items from the inputs that drove your judgment.
"""


async def run_analysis_agent(browsing_result: BrowsingResult, domain: str) -> ScamAssessment:
    agent = Agent(
        name="AnalysisAgent",
        instructions=_SYSTEM_PROMPT,
        tools=[get_domain_info],
        output_type=ScamAssessment,
        model="gpt-4.1",
    )

    user_message = (
        f"Target domain: {domain}\n\n"
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
