import asyncio
import logging
from typing import Literal
from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.email_evidence import make_client, pick_inbox
from anti_scam_agent.models import Outcome, ScamAssessment
from anti_scam_agent.persona import generate_persona
from anti_scam_agent.signals import collect_static_signals

logger = logging.getLogger(__name__)


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    domain = _extract_domain(url)

    # AgentMail is mandatory; route the persona's email through a real inbox.
    make_client()  # raises if unconfigured
    persona = persona.model_copy(update={"email": pick_inbox()})

    # Run 1: a Luhn-invalid card. Acceptance here is the strongest signal.
    result = await run_browsing_agent(url, persona)
    card_tier: Literal["luhn_invalid", "luhn_valid"] | None = None

    if result.payment_outcome is Outcome.succeeded:
        card_tier = "luhn_invalid"
    elif result.payment_outcome is Outcome.failed:
        persona_valid = persona.model_copy(
            update={"credit_card_number": persona.credit_card_number_luhn_valid}
        )
        result = await run_browsing_agent(url, persona_valid)
        if result.payment_outcome is Outcome.succeeded:
            card_tier = "luhn_valid"

    static_signals = await asyncio.to_thread(collect_static_signals, url)
    return await run_analysis_agent(result, domain, card_tier, static_signals)
