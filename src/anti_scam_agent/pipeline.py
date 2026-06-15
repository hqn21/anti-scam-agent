import asyncio
from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.email_evidence import make_client, pick_inbox
from anti_scam_agent.models import ScamAssessment
from anti_scam_agent.persona import generate_persona
from anti_scam_agent.signals import collect_static_signals


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    domain = _extract_domain(url)

    # AgentMail is mandatory; route the persona's email through a real inbox so the
    # Browsing Agent can read verification codes mid-flow.
    client = make_client()  # raises if unconfigured
    inbox = pick_inbox()
    persona = persona.model_copy(update={"email": inbox})

    result = await run_browsing_agent(url, persona, client, inbox)
    static_signals = await asyncio.to_thread(collect_static_signals, url)
    return await run_analysis_agent(result, domain, static_signals)
