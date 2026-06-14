import asyncio
import logging
import re
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

_MAX_STEPS = 70
_TIMEOUT_SECONDS = 480  # 8 minutes
# One action per step: the DOM (and its click indices) is re-serialized before every
# action, so a click can never fire against a stale index from before the page changed.
_MAX_ACTIONS_PER_STEP = 1


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

If a form is in English or will not accept Chinese characters, you can give the same details in their international form instead:
  - Full name (international): {persona.name_international}
  - Phone number (international): {persona.phone_international}
  - Mailing address (international): {persona.address_international}

Your single most important objective is to COMPLETE THE WHOLE FLOW the site offers — from start to final confirmation — rather than to enter perfectly accurate information.

If something is not working — an action fails, an error or pop-up alert keeps reappearing, the page stops changing, or you notice you have done the same thing two or three times — STOP and do not keep repeating it. Step back, look again at what is actually on the screen, and choose a genuinely different action or path; do not just retry the same step expecting a different result. Re-decide what to do from the current state each time rather than committing to one fixed plan. If a particular step, option, or item is truly blocked, leave it and move on to the next part of the flow (or finish the visit). Always keep making forward progress; never loop on an action that just failed.

What to do, in order:
  1. Open the page and wait for it to fully load.
  2. If a cookie banner, pop-up, modal, overlay, or notice appears, CLOSE or dismiss it first. Do not try to click things behind it — clear the blocker, then continue.
  3. Read what the site is about.
  4. If the site invites you to register or sign in, do so using the information above. If it emails you a verification code or confirmation link, use the "Check your email inbox" tool to read it and enter the code to finish.
  5. If the site invites you to complete a purchase, claim a prize, or enter payment details, go all the way through with it using the information above. If you can choose how to pay (for example cash on delivery versus credit card), you prefer to pay by credit card, so choose credit card and enter the card details.
  6. The form may not perfectly match your details (for example your city or district may not be in a dropdown, or a field may be required that you have no value for). Do not get stuck: pick any reasonable available option, or make up a plausible value, and move on. Getting through the flow matters more than entering matching data.
  7. If the site unexpectedly sends you to a different, unrelated website before you have finished, go back to the previous page and continue the flow from where you left off rather than abandoning it.
  8. Follow the site's steps as a normal determined user would, until you reach the final confirmation or there is genuinely nothing further a user could do.

When you stop, produce a summary of what happened in the structured output format. Report objective facts: what the site appears to be about, what links it has to other domains, what forms it asked you to fill in, and anything during the visit that surprised you as a user.
For each step like login or payment, record the outcome honestly: choose 'succeeded' only when the site showed an explicit confirmation or success screen, 'failed' when it showed an explicit error or rejection, 'unclear' when there was no clear response either way, and 'not_attempted' when you did not try it.
If you entered card details, set payment_explicitly_declined to true only when the site clearly told you the card itself was declined or invalid; if it accepted the card, moved on, or showed no clear card error, set it to false.
Set visit_completed to true if you ran the flow to a normal conclusion, and false if you could not reach the end of it.
"""


def _iter_strings(obj) -> "list[str]":
    """Flatten all string leaves out of a nested dict/list (e.g. a recorded action)."""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_iter_strings(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_iter_strings(v))
    return out


def _card_was_entered(actions: list, card_number: str) -> bool:
    """Whether the card number was typed into any recorded action (digits-only match)."""
    target = re.sub(r"\D", "", card_number or "")
    if len(target) < 12:
        return False
    for action in actions:
        for s in _iter_strings(action):
            if target in re.sub(r"\D", "", s):
                return True
    return False


def _salvage_result_from_history(history, url: str, persona: FakePersona, note: str) -> BrowsingResult:
    """Build a BrowsingResult from whatever the agent managed to do before it stalled
    or failed, instead of discarding everything.

    Critical for hang-after-submit scams: if the card was entered and the page then
    never resolved, that must be reported as a submitted card with no explicit decline
    (a strong signal) — not as 'no payment attempted'.
    """
    actions: list = []
    urls: list = []
    try:
        actions = history.model_actions() if history is not None else []
    except Exception as e:  # noqa: BLE001 — salvage must never raise
        logger.warning("could not read history actions on %s: %s", url, e)
    try:
        urls = history.urls() if history is not None else []
    except Exception as e:  # noqa: BLE001
        logger.warning("could not read history urls on %s: %s", url, e)

    card_submitted = _card_was_entered(actions, persona.credit_card_number)
    events = [note]
    payment_outcome = Outcome.not_attempted
    if card_submitted:
        payment_outcome = Outcome.unclear
        events.append(
            "Payment details were submitted, but the page never showed a clear result — "
            "it appeared to stall or keep loading rather than confirming or rejecting the card."
        )

    try:
        outgoing = _external_links(urls, url)
    except Exception:  # noqa: BLE001
        outgoing = []

    return BrowsingResult(
        website_summary=f"The visit to {url} could not be completed normally.",
        outgoing_links=outgoing,
        login_attempted=False,
        login_outcome=Outcome.not_attempted,
        credit_card_submitted=card_submitted,
        payment_outcome=payment_outcome,
        payment_explicitly_declined=False,
        form_fields_requested=[],
        unexpected_events=events,
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
        # paint_order_filtering left at its default (True): drop visually-occluded
        # elements so a covered/duplicate button with the same label isn't indexed
        # and mis-clicked.
    )

    agent = BrowserAgent(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=True,
        output_model_schema=BrowsingResult,
        tools=_build_email_tools(client, inbox),
        max_actions_per_step=_MAX_ACTIONS_PER_STEP,
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
        return _salvage_result_from_history(
            getattr(agent, "history", None), url, persona, f"browsing timed out after {_TIMEOUT_SECONDS}s"
        )
    except Exception as e:
        logger.warning("browsing agent raised on %s: %s", url, e)
        return _salvage_result_from_history(
            getattr(agent, "history", None), url, persona, f"browsing raised {type(e).__name__}: {e}"
        )

    structured = history.structured_output
    result: BrowsingResult | None = None
    if isinstance(structured, BrowsingResult):
        result = structured
    elif isinstance(structured, dict):
        try:
            result = BrowsingResult.model_validate(structured)
        except Exception as e:
            logger.warning("failed to parse structured dict on %s: %s", url, e)
            return _salvage_result_from_history(history, url, persona, f"parsing structured output failed: {e}")

    if result is not None:
        try:
            result.outgoing_links = _external_links(history.urls(), url)
        except Exception as e:  # never let history parsing break the result
            logger.warning("could not derive outgoing_links on %s: %s", url, e)
        return result

    logger.warning("browsing agent returned no structured output on %s; salvaging from history", url)
    return _salvage_result_from_history(history, url, persona, "browsing agent produced no structured output")
