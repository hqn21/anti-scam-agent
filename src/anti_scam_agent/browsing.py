import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from browser_use import Agent as BrowserAgent, ChatOpenAI, Browser, Tools
from browser_use.browser import BrowserSession
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

# JavaScript backing the click_by_visible_text tool. It walks every shadow root (so it
# also finds buttons in client-rendered / web-component pages that the framework's DOM
# numbering misses or mis-targets), then clicks the clickable element whose visible text
# matches the label — exact match first, else the shortest element that contains it.
# __LABEL__ is replaced with a json.dumps-encoded string at call time, so any label works
# and the value is safely quoted; the label is never hardcoded.
_CLICK_BY_TEXT_JS = """(() => {
  const label = __LABEL__;
  const nodes = [];
  const walk = (root) => {
    root.querySelectorAll('*').forEach((el) => {
      nodes.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    });
  };
  walk(document);
  const clickable = (el) =>
    el.tagName === 'BUTTON' || el.type === 'submit' || el.getAttribute('role') === 'button' || !!el.onclick;
  const text = (el) => (el.innerText || el.textContent || '').trim();
  const exact = nodes.filter((el) => clickable(el) && text(el) === label);
  const partial = nodes
    .filter((el) => clickable(el) && text(el).includes(label))
    .sort((a, b) => text(a).length - text(b).length);
  const hit = exact[0] || partial[0];
  if (hit) { hit.scrollIntoView({ block: 'center' }); hit.click(); return 'clicked: ' + label; }
  return 'no clickable element found with text: ' + label;
})()"""


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

When the site shows a pop-up alert, its text is reported to you under "Auto-closed JavaScript dialogs". Read it — that text is the site's actual response to what you just did. If it tells you the action could not be done (for example not enough points, invalid details, or an item is unavailable), do NOT repeat that same action; pick a different option or move on.

Tell apart two different kinds of "failure". (a) The SITE refused you — an alert/error said so, or nothing changed after a real action: change approach or move on, as above. (b) Your click simply did not land — you were told the element is no longer available or the page changed, or you clicked the wrong neighbouring element (for example a quantity +/- instead of the button you meant). A click that did not land is a targeting miss, NOT a refusal: re-read the elements currently listed on the page, find the SAME button you intended (by its visible label), and click it again. Do not abandon an important step — such as a final 'Exchange', 'Checkout', 'Confirm', or 'Pay' button — just because it took a few tries to land the click; keep re-locating and clicking that intended button until it actually registers or the site itself gives you a clear response.

If clicking that button by its number keeps landing on the wrong element (for example a nearby +/-) more than twice, the numbering for it is unreliable — STOP clicking it by number. Instead use the "Click a button by its visible text" tool, passing the exact visible label of the button you want (for example 'Exchange', 'Checkout', 'Pay', or 'Confirm'). It finds the button by its text — including buttons drawn by the page itself that the numbering cannot target — and clicks it directly. (You can use the find_text action first to scroll the label into view.) After calling the tool, read what it returned: 'clicked: ...' means it worked, so check whether the page advanced; 'no clickable element found ...' means the label text did not match, so look again at the button's exact visible text and try the tool again with the corrected label.

On a long page that loads more items as you scroll (the list keeps growing when you reach the bottom), first scroll all the way down and wait until the list stops growing, THEN click a final button like 'Exchange'/'Checkout'/'Pay'. Clicking while the page is still loading more content makes the button move and your click miss.

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


def _build_tools(client: "AgentMail", inbox: str) -> Tools:
    """The custom tools handed to the Browsing Agent. Descriptions stay neutral and any
    privileged values (client + inbox) are closure-captured so they never appear in the
    LLM-facing action schema (preserving the blind invariant)."""
    tools = Tools()

    @tools.action(
        "Check your email inbox and read your most recent messages — useful when a "
        "site says it has emailed you a code or a confirmation link."
    )
    async def read_email_inbox() -> str:
        return await asyncio.to_thread(read_inbox_text, client, inbox)

    @tools.action(
        "Click a button or control by its exact visible text instead of by its number. "
        "Use this when clicking by number keeps landing on the wrong element. Pass the "
        "label shown on the button you want, e.g. 'Exchange', 'Checkout', 'Pay'. Returns "
        "'clicked: <label>' if it found and clicked it, or 'no clickable element found ...' "
        "if nothing on the page has that exact visible text."
    )
    async def click_by_visible_text(text: str, browser_session: BrowserSession) -> str:
        code = _CLICK_BY_TEXT_JS.replace("__LABEL__", json.dumps(text))
        cdp_session = await browser_session.get_or_create_cdp_session()
        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={"expression": code, "returnByValue": True, "awaitPromise": True},
            session_id=cdp_session.session_id,
        )
        value = result.get("result", {}).get("value")
        return str(value) if value is not None else "click attempt returned no result"

    return tools


async def run_browsing_agent(url: str, persona: FakePersona, client: "AgentMail", inbox: str) -> BrowsingResult:
    llm = ChatOpenAI(model="gpt-4.1")
    task = _build_task_prompt(url, persona)

    browser = Browser(
        # Moderate settle margin (defaults are 0.25 / 0.5 / 0.1). We keep ~2-4x the default
        # so late-rendering pages have time to paint, but no more: the lazy-load / late-render
        # case that originally motivated much longer waits is now handled directly by the
        # click_by_visible_text tool and scroll-to-settle, not by stalling on every load.
        # wait_for_network_idle especially: many sites' trackers never go idle, so a large
        # value just burns the full timeout on every navigation.
        minimum_wait_page_load_time=1.0,
        wait_for_network_idle_page_load_time=2.0,
        wait_between_actions=0.5,
        headless=False,
        disable_security=True,
        cross_origin_iframes=True,
        # paint_order_filtering disabled: the (experimental) occlusion filter also drops
        # real, visible buttons, leaving the agent unable to find them. The stale-index
        # misclick it was meant to help with is already handled by max_actions_per_step=1.
        paint_order_filtering=False,
    )

    agent = BrowserAgent(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=True,
        output_model_schema=BrowsingResult,
        tools=_build_tools(client, inbox),
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
