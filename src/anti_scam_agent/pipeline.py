import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.email_evidence import (
    EmailEvidence,
    collect_email_evidence,
    make_client,
    pick_inbox,
)
from anti_scam_agent.models import Outcome, ScamAssessment
from anti_scam_agent.persona import generate_persona
from anti_scam_agent.signals import collect_static_signals

logger = logging.getLogger(__name__)


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


def _poll_seconds() -> int:
    try:
        return int(os.getenv("AGENTMAIL_POLL_SECONDS", "120"))
    except ValueError:
        logger.warning("AGENTMAIL_POLL_SECONDS is not a valid integer; using default 120")
        return 120


async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    domain = _extract_domain(url)

    # If AgentMail is configured, route the persona's email through a real inbox so we
    # can later check whether the site sent genuine transactional mail.
    client = make_client()
    since = datetime.now(timezone.utc)
    inbox: str | None = None
    if client is not None:
        persona = persona.model_copy(update={"email": pick_inbox()})
        inbox = persona.email

    # Run 1: a Luhn-invalid card. Acceptance here is the strongest signal.
    result = await run_browsing_agent(url, persona)
    card_tier: Literal["luhn_invalid", "luhn_valid"] | None = None

    if result.payment_outcome is Outcome.succeeded:
        card_tier = "luhn_invalid"
    elif result.payment_outcome is Outcome.failed:
        # The site's front end caught the bad card. Retry with a valid one;
        # acceptance now (instant success, no processor) is a weaker signal.
        persona_valid = persona.model_copy(
            update={"credit_card_number": persona.credit_card_number_luhn_valid}
        )
        result = await run_browsing_agent(url, persona_valid)
        if result.payment_outcome is Outcome.succeeded:
            card_tier = "luhn_valid"

    # Out-of-band signals, collected concurrently off the event loop. Both are
    # failure-tolerant, so neither can break the run.
    static_task = asyncio.to_thread(collect_static_signals, url)
    email_evidence: EmailEvidence | None = None
    if client is not None:
        static_signals, email_evidence = await asyncio.gather(
            static_task,
            asyncio.to_thread(collect_email_evidence, client, inbox, domain, since, _poll_seconds()),
        )
    else:
        static_signals = await static_task

    return await run_analysis_agent(result, domain, card_tier, static_signals, email_evidence)
