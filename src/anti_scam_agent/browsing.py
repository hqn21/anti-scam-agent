import asyncio
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from browser_use import Agent as BrowserAgent, ChatOpenAI, Browser, Tools
from dotenv import load_dotenv

from anti_scam_agent.email_evidence import read_inbox_text
from anti_scam_agent.models import BrowsingResult, FakePersona, Outcome

if TYPE_CHECKING:
    from agentmail import AgentMail

load_dotenv()

logger = logging.getLogger(__name__)

_MAX_STEPS = 40
_TIMEOUT_SECONDS = 300  # 5 minutes


def _build_task_prompt(url: str, persona: FakePersona) -> str:
    """Build a strictly user-framed task prompt.

    The prompt must avoid anti-scam-analysis framing and any reviewer-forbidden
    terms that could reveal the true purpose of the visit.
    The agent must not suspect it is being used for anti-scam analysis.
    """
    return f"""You are visiting the website {url} as an interested first-time user who wants to go all the way through whatever the site offers.

Here is the information about you, which you may use to fill in any forms the site asks you to complete:
  - Full name: {persona.name}
  - Email: {persona.email}
  - Password (for any account you need to create): {persona.password}
  - Phone number: {persona.phone}
  - Mailing address: {persona.address}
  - Credit card number: {persona.credit_card_number}
  - Credit card expiry: {persona.credit_card_expiry}
  - Credit card security code (CVV): {persona.credit_card_cvv}

Your single most important objective is to COMPLETE THE WHOLE FLOW the site offers — from start to final confirmation — rather than to enter perfectly accurate information.

What to do, in order:
  1. Open the page and wait for it to fully load.
  2. If a cookie banner, pop-up, modal, overlay, or notice appears, CLOSE or dismiss it first. Do not try to click things behind it — clear the blocker, then continue.
  3. Read what the site is about.
  4. If the site invites you to register or sign in, do so using the information above. If it emails you a verification code or confirmation link, use the "Check your email inbox" tool to read it and enter the code to finish.
  5. If the site invites you to complete a purchase, claim a prize, or enter payment details, go all the way through with it using the information above. If you can choose how to pay (for example cash on delivery versus credit card), you prefer to pay by credit card, so choose credit card and enter the card details.
  6. The form may not perfectly match your details (for example your city or district may not be in a dropdown, or a field may be required that you have no value for). Do not get stuck: pick any reasonable available option, or make up a plausible value, and move on. Getting through the flow matters more than entering matching data.
  7. Follow the site's steps as a normal determined user would, until you reach the final confirmation or there is genuinely nothing further a user could do.

When you stop, produce a summary of what happened in the structured output format. Report objective facts: what the site appears to be about, what links it has to other domains, what forms it asked you to fill in, and anything during the visit that surprised you as a user.
For each step like login or payment, record the outcome honestly: choose 'succeeded' only when the site showed an explicit confirmation or success screen, 'failed' when it showed an explicit error or rejection, 'unclear' when there was no clear response either way, and 'not_attempted' when you did not try it.
If you entered card details, set payment_explicitly_declined to true only when the site clearly told you the card itself was declined or invalid; if it accepted the card, moved on, or showed no clear card error, set it to false.
Set visit_completed to true if you ran the flow to a normal conclusion, and false if you could not reach the end of it.
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


def _build_email_tools(client: "AgentMail", inbox: str) -> Tools:
    """A neutral 'read your inbox' tool. client + inbox are closure-captured so they
    never appear in the LLM-facing action schema (preserving the blind invariant)."""
    tools = Tools()

    @tools.action(
        "Check your email inbox and read your most recent messages — useful when a "
        "site says it has emailed you a code or a confirmation link."
    )
    async def read_email_inbox() -> str:
        return await asyncio.to_thread(read_inbox_text, client, inbox)

    return tools


async def run_browsing_agent(url: str, persona: FakePersona, client: "AgentMail", inbox: str) -> BrowsingResult:
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
        tools=_build_email_tools(client, inbox),
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
