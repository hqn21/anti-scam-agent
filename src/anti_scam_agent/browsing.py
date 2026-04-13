import asyncio
import logging

from browser_use import Agent as BrowserAgent, ChatOpenAI, Browser
from dotenv import load_dotenv

from anti_scam_agent.models import BrowsingResult, FakePersona

load_dotenv()

logger = logging.getLogger(__name__)

_MAX_STEPS = 25
_TIMEOUT_SECONDS = 300  # 5 minutes


def _build_task_prompt(url: str, persona: FakePersona) -> str:
    """Build a strictly user-framed task prompt.

    The prompt must avoid anti-scam-analysis framing and any reviewer-forbidden
    terms that could reveal the true purpose of the visit.
    The agent must not suspect it is being used for anti-scam analysis.
    """
    return f"""You are visiting the website {url} as an interested first-time user.

Here is the information about you, which you may use to fill in any forms the site asks you to complete:
  - Full name: {persona.name}
  - Email: {persona.email}
  - Password (for any account you need to create): {persona.password}
  - Phone number: {persona.phone}
  - Mailing address: {persona.address}
  - Credit card number: {persona.credit_card_number}
  - Credit card expiry: {persona.credit_card_expiry}
  - Credit card security code (CVV): {persona.credit_card_cvv}

What to do, in order:
  1. Open the page and read what it is about.
  2. If the site invites you to register or sign in, do so using the information above.
  3. If the site invites you to complete a purchase, claim a prize, or enter payment details, go through with it using the information above.
  4. Follow the site's instructions as a normal curious user would. Do not skip steps the site asks you to complete.
  5. Stop when you have either completed the main flow the site offers, or there is nothing further a user would reasonably do.

When you stop, produce a summary of what happened in the structured output format. Report objective facts: what the site appears to be about, what links it has to other domains, what forms it asked you to fill in, whether registration / login / payment appeared to succeed, and anything during the visit that surprised you as a user.
"""


def _fallback_result(url: str, note: str) -> BrowsingResult:
    return BrowsingResult(
        website_summary=f"Unable to complete visit to {url}.",
        outgoing_links=[],
        login_attempted=False,
        login_succeeded=False,
        credit_card_submitted=False,
        credit_card_accepted=False,
        form_fields_requested=[],
        unexpected_events=[note],
    )


async def run_browsing_agent(url: str, persona: FakePersona) -> BrowsingResult:
    llm = ChatOpenAI(model="gpt-4.1-mini")
    task = _build_task_prompt(url, persona)

    browser = Browser(
        minimum_wait_page_load_time=2.0,
        wait_for_network_idle_page_load_time=3.0,
        wait_between_actions=1.0,
    )

    agent = BrowserAgent(
        task=task,
        llm=llm,
        use_vision=True,
        output_model_schema=BrowsingResult,
    )

    try:
        history = await asyncio.wait_for(
            agent.run(max_steps=_MAX_STEPS),
            timeout=_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("browsing agent timed out on %s", url)
        return _fallback_result(url, f"browsing timed out after {_TIMEOUT_SECONDS}s")
    except Exception as e:
        logger.warning("browsing agent raised on %s: %s", url, e)
        return _fallback_result(url, f"browsing raised {type(e).__name__}: {e}")

    structured = history.structured_output
    if isinstance(structured, BrowsingResult):
        return structured
    if isinstance(structured, dict):
        try:
            return BrowsingResult.model_validate(structured)
        except Exception as e:
            logger.warning("failed to parse structured dict on %s: %s", url, e)
            return _fallback_result(url, f"parsing structured output failed: {e}")

    logger.warning("browsing agent returned no structured output on %s; using fallback", url)
    return _fallback_result(url, "browsing agent produced no structured output")
