from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.models import ScamAssessment
from anti_scam_agent.persona import generate_persona


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()
    browsing_result = await run_browsing_agent(url, persona)
    domain = _extract_domain(url)
    return await run_analysis_agent(browsing_result, domain)
