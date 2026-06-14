import asyncio
import logging
from urllib.parse import urlparse

from browser_use import Agent as BrowserAgent, ChatOpenAI, Browser
from dotenv import load_dotenv

from anti_scam_agent.models import BrowsingResult, FakePersona, Outcome

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
  1. Open the page and wait for it to fully load.
  2. Read what it is about.
  3. If the site invites you to register or sign in, do so using the information above.
  4. If the site invites you to complete a purchase, claim a prize, or enter payment details, go through with it using the information above.
  5. Follow the site's instructions as a normal curious user would. Do not skip steps the site asks you to complete.
  6. Stop when you have either completed the main flow the site offers, or there is nothing further a user would reasonably do.

When you stop, produce a summary of what happened in the structured output format. Report objective facts: what the site appears to be about, what links it has to other domains, what forms it asked you to fill in, and anything during the visit that surprised you as a user.
For each step like login or payment, record the outcome honestly: choose 'succeeded' only when the site showed an explicit confirmation or success screen, 'failed' when it showed an explicit error or rejection, 'unclear' when there was no clear response either way, and 'not_attempted' when you did not try it.
"""


def _fallback_result(url: str, note: str) -> BrowsingResult:
    return BrowsingResult(
        website_summary=f"Unable to complete visit to {url}.",
        outgoing_links=[],
        login_attempted=False,
        login_outcome=Outcome.not_attempted,
        credit_card_submitted=False,
        payment_outcome=Outcome.not_attempted,
        payment_explicitly_declined=False,
        form_fields_requested=[],
        unexpected_events=[note],
        visit_completed=False,
    )


def _external_links(urls: list[str | None], target_url: str) -> list[str]:
    """Distinct hosts visited during the run that differ from the target host.

    Subdomains of the target (e.g. pay.shop.test vs shop.test) count as external;
    only the bare www. prefix is normalised away.
    """
    target = (urlparse(target_url).hostname or "").removeprefix("www.")
    seen: set[str] = set()
    links: list[str] = []
    for url in urls:
        if not url:
            continue
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if host and host != target and host not in seen:
            seen.add(host)
            links.append(host)
    return links


async def run_browsing_agent(url: str, persona: FakePersona) -> BrowsingResult:
    llm = ChatOpenAI(model="gpt-4.1-mini")
    task = _build_task_prompt(url, persona)

    browser = Browser(
        minimum_wait_page_load_time=2.0,
        wait_for_network_idle_page_load_time=3.0,
        wait_between_actions=1.0,
        headless=False,
        disable_security=True,
        cross_origin_iframes=True,
        paint_order_filtering=False,
    )

    agent = BrowserAgent(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=True,
        output_model_schema=BrowsingResult,
    )

    try:
        history = await asyncio.wait_for(
            agent.run(max_steps=_MAX_STEPS),
            timeout=_TIMEOUT_SECONDS,
        )
        summary = await agent.token_cost_service.get_usage_summary()
        logger.info(summary)
    except asyncio.TimeoutError:
        logger.warning("browsing agent timed out on %s", url)
        return _fallback_result(url, f"browsing timed out after {_TIMEOUT_SECONDS}s")
    except Exception as e:
        logger.warning("browsing agent raised on %s: %s", url, e)
        return _fallback_result(url, f"browsing raised {type(e).__name__}: {e}")

    structured = history.structured_output
    result: BrowsingResult | None = None
    if isinstance(structured, BrowsingResult):
        result = structured
    elif isinstance(structured, dict):
        try:
            result = BrowsingResult.model_validate(structured)
        except Exception as e:
            logger.warning("failed to parse structured dict on %s: %s", url, e)
            return _fallback_result(url, f"parsing structured output failed: {e}")

    if result is not None:
        try:
            result.outgoing_links = _external_links(history.urls(), url)
        except Exception as e:  # never let history parsing break the result
            logger.warning("could not derive outgoing_links on %s: %s", url, e)
        return result

    logger.warning("browsing agent returned no structured output on %s; using fallback", url)
    return _fallback_result(url, "browsing agent produced no structured output")
