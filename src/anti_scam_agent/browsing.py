import asyncio
import json
import logging
import re
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from browser_use import Agent as BrowserAgent, ChatOpenAI, Browser, Tools
from browser_use.browser import BrowserSession
from dotenv import load_dotenv

from anti_scam_agent.email_evidence import read_inbox_text
from anti_scam_agent.models import BrowsingResult, FakePersona, Outcome
from anti_scam_agent.reporting import (
    CallSample,
    StageReport,
    StepRecord,
    StepWindow,
    attribute_calls,
    combine_metrics,
)

if TYPE_CHECKING:
    from agentmail import AgentMail

load_dotenv()

logger = logging.getLogger(__name__)

_MAX_STEPS = 100
_TIMEOUT_SECONDS = 900  # 15 minutes
# One action per step: the DOM (and its click indices) is re-serialized before every
# action, so a click can never fire against a stale index from before the page changed.
# This is the robust guard against same-URL in-place DOM re-renders (SPA / client-rendered
# pages) that browser_use's multi_act page-change detection does not catch.
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
    cards_block = "\n".join(
        f"  - Card {i}: number {card.number}, expiry {card.expiry}, security code (CVV) {card.cvv}"
        for i, card in enumerate(persona.cards, start=1)
    )
    return f"""# Your visit to {url}

You are visiting the website {url} as an interested first-time user who wants to go all the way through whatever the site offers.

## Who you are

Use these details to fill in any forms the site asks you to complete:
  - Full name: {persona.name}
  - Email: {persona.email}
  - Password (for any account you need to create): {persona.password}
  - Phone number: {persona.phone}
  - Mailing address: {persona.address}

You have several payment cards, listed below. Use the first card by default. If the site refuses a card for a reason about the card itself being unsupported — it says it does not accept that kind of card, does not accept debit cards, or that card type is not supported — that is not a real decline: switch to the NEXT card on the list and try again. Only stop going through the cards when the site clearly declines a specific card as invalid or declined, or you have tried them all.
{cards_block}

## Your goal

Your single most important objective is to COMPLETE THE WHOLE FLOW the site offers — from start to final confirmation — rather than to enter perfectly accurate information.

Stay on the main path a buyer or claimant follows: create an account or sign in -> choose the item or claim the offer -> go to the cart or checkout -> enter delivery and payment details -> reach the final confirmation. Follow only the links and buttons that move you forward along this path; do not wander off into menus, footer links, category browsing, or unrelated pages.

## Plan and track your progress

Once you have seen what the site is, lay the flow out as a short list of steps and work through them, keeping track of which step you are on and which steps remain. You have NOT finished while there are still steps a user could take. If your steps turn out to be wrong, re-plan from the current state.

## When something goes wrong

If something is not working — an action fails, an error or pop-up alert keeps reappearing, the page stops changing, or you notice you have done the same thing two or three times — STOP and do not keep repeating it. Step back, look again at what is actually on the screen, and choose a genuinely different action or path; do not just retry the same step expecting a different result. Re-decide what to do from the current state each time rather than committing to one fixed plan. If a particular step, option, or item is truly blocked, leave it and move on to the next part of the flow. Always keep making forward progress; never loop on an action that just failed.

When the site shows a pop-up alert, its text is reported to you under "Auto-closed JavaScript dialogs". Read it — that text is the site's actual response to what you just did. If it tells you the action could not be done (for example not enough points, invalid details, or an item is unavailable), do NOT repeat that same action; pick a different option or move on.

Tell apart two kinds of "failure":
  - The SITE refused you — an alert/error said so, or nothing changed after a real action. Change approach or move on, as above.
  - Your click simply did not land — you were told the element is no longer available or the page has changed, or you clicked the wrong neighbouring element (for example a quantity +/- instead of the button you meant). A click that did not land is a targeting miss, NOT a refusal: re-read the elements currently listed on the page, find the SAME button you intended (by its visible label), and click it again. Do not abandon an important step — such as a final 'Exchange', 'Checkout', 'Confirm', or 'Pay' button — just because it took a few tries to land the click.

## Clicking buttons reliably

For the buttons that actually move the flow forward — the main call-to-action such as Continue / Next / Add to cart / Checkout / Exchange / Pay / Confirm / Submit / Place order — prefer the "Click a button by its visible text" tool from the very first attempt, passing the button's exact visible label (for example 'Exchange', 'Checkout', or 'Pay'). A wrong click on one of these is what derails the whole flow, and these buttons almost always have clear, unique wording, so finding them by their text is more reliable than by their number — it also reaches buttons drawn by the page itself that the numbering cannot target. (You can use the find_text action first to scroll the label into view.) For ordinary controls that have NO clear text, or whose text repeats across the page — form fields, dropdown options, checkboxes, radio buttons, icons, quantity +/- steppers — do NOT use that tool; keep clicking those by their number as usual. After calling the tool, read what it returned: 'clicked: ...' means it found and clicked the button — now check the page actually advanced, and if it reported clicked but nothing changed, click that same button once by its number instead; 'no clickable element found ...' means the label did not match, so look again at the button's exact visible text and call the tool again with the corrected label.

On a long page that loads more items as you scroll (the list keeps growing when you reach the bottom), first scroll all the way down and wait until the list stops growing, THEN click a final button like 'Exchange'/'Checkout'/'Pay'. Clicking while the page is still loading more content makes the button move and your click miss.

## Step by step

  1. Open the page and wait for it to fully load.
  2. If a cookie banner, pop-up, modal, overlay, or notice appears, CLOSE or dismiss it first. Do not try to click things behind it — clear the blocker, then continue.
  3. Read what the site is about.
  4. If the site invites you to register or sign in, do so using the information above. If it emails you a verification code or confirmation link, use the "Check your email inbox" tool to read it and enter the code to finish.
  5. If the site invites you to complete a purchase, claim a prize, or enter payment details, go all the way through with it using the information above. If you can choose how to pay (for example cash on delivery versus credit card), you prefer to pay by credit card, so choose credit card and enter the card details.
  6. The form may not perfectly match your details (for example your city or district may not be in a dropdown, or a field may be required that you have no value for). Do not get stuck: pick any reasonable available option, or make up a plausible value, and move on. Getting through the flow matters more than entering matching data.
  7. Follow the site's steps as a normal determined user would, until you reach the final confirmation or there is genuinely nothing further a user could do.

## Finishing and reporting

Decide whether you have actually finished by asking one question about the page you are on: is it the continuation or result of the flow you were running, or an unrelated detour?
  - You have FINISHED when you reach a clear conclusion of the same task you were doing — a confirmation, success, thank-you, order-complete, or payment-result page for the purchase or claim you were making. This counts EVEN IF it is on a different website, as long as it is clearly the continuation or result of what you were doing (for example a payment provider, the site's checkout partner, or a bank's card-verification page).
  - You have NOT finished if you land on a page that is unrelated to your task — an ad, a marketing page, a homepage, a random offer, or an error page — that does not continue the purchase or claim. That means a click went wrong: go back to the previous page and continue the flow from where you left off rather than treating the unrelated page as the result.

When you stop, produce a summary of what happened in the structured output format. Report objective facts: what the site appears to be about, what links it has to other domains, what forms it asked you to fill in, and anything during the visit that surprised you as a user.
For each step like login or payment, record the outcome honestly: choose 'succeeded' only when the site showed an explicit confirmation or success screen, 'failed' when it showed an explicit error or rejection, 'unclear' when there was no clear response either way, and 'not_attempted' when you did not try it.
If you entered card details, set payment_explicitly_declined to true only when the site clearly told you the card itself was declined or invalid; if it accepted the card, moved on, or showed no clear card error, set it to false.
Set visit_completed to true only if you reached a genuine end state of this flow (wherever it is hosted), and false if you ended up on something unrelated or could not reach the end.
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


def _card_was_entered(actions: list, card_numbers: list[str]) -> bool:
    """Whether any of the card numbers was typed into a recorded action (digits-only match)."""
    targets = [t for n in card_numbers if len(t := re.sub(r"\D", "", n or "")) >= 12]
    if not targets:
        return False
    for action in actions:
        for s in _iter_strings(action):
            digits = re.sub(r"\D", "", s)
            if any(target in digits for target in targets):
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

    card_submitted = _card_was_entered(actions, [card.number for card in persona.cards])
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


def _browsing_stage_report(agent, duration_s: float, note: str | None = None) -> StageReport:
    """Build the browsing StageReport from already-produced data: agent.history (per-step
    timing, actions, transcribed thinking/eval/goal, result errors) and
    token_cost_service.usage_history (per-call tokens, attributed to steps by timestamp).
    Never raises — telemetry must not break the pipeline."""
    model = getattr(getattr(agent, "llm", None), "model", None)
    try:
        history_items = list(agent.history.history)
    except Exception:  # noqa: BLE001
        history_items = []

    # Per-call samples from the token service.
    calls: list[CallSample] = []
    try:
        for entry in agent.token_cost_service.usage_history:
            u = entry.usage
            calls.append(
                CallSample(
                    timestamp=entry.timestamp.timestamp(),
                    model=entry.model,
                    prompt_tokens=u.prompt_tokens,
                    cached_input_tokens=u.prompt_cached_tokens or 0,
                    output_tokens=u.completion_tokens,
                )
            )
    except Exception:  # noqa: BLE001
        calls = []

    # Assemble step windows and per-step records. Wrapped whole: the contract is that this
    # function never raises, even if an upstream browser_use schema change makes a field
    # access fail mid-loop — telemetry must never sink the already-salvaged BrowsingResult.
    try:
        windows: list[StepWindow] = []
        for h in history_items:
            meta = getattr(h, "metadata", None)
            if meta is not None:
                windows.append(
                    StepWindow(step_number=meta.step_number, start=meta.step_start_time, end=meta.step_end_time)
                )

        per_step, other = attribute_calls(calls, windows)

        steps: list[StepRecord] = []
        for h in history_items:
            meta = getattr(h, "metadata", None)
            if meta is None:
                continue
            out = h.model_output
            action_types: list[str] = []
            if out is not None:
                for action in out.action:
                    dumped = action.model_dump(exclude_none=True, mode="json")
                    name = next(iter(dumped), None)
                    if name:
                        action_types.append(name)
            errors = [r.error for r in h.result if getattr(r, "error", None)]
            steps.append(
                StepRecord(
                    step_number=meta.step_number,
                    duration_s=meta.duration_seconds,
                    url=getattr(h.state, "url", None),
                    action_types=action_types,
                    thinking=getattr(out, "thinking", None) if out else None,
                    evaluation=getattr(out, "evaluation_previous_goal", None) if out else None,
                    memory=getattr(out, "memory", None) if out else None,
                    next_goal=getattr(out, "next_goal", None) if out else None,
                    result_errors=errors,
                    metrics=per_step.get(meta.step_number, combine_metrics([])),
                )
            )
    except Exception as e:  # noqa: BLE001 — telemetry must not break the pipeline
        logger.warning("could not assemble browsing telemetry: %s", e)
        return StageReport.build(
            name="browsing", model=model, duration_s=duration_s, steps=[], other_metrics=combine_metrics([]), note=note
        )

    return StageReport.build(
        name="browsing", model=model, duration_s=duration_s, steps=steps, other_metrics=other, note=note
    )


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


async def run_browsing_agent(
    url: str, persona: FakePersona, client: "AgentMail", inbox: str
) -> tuple[BrowsingResult, StageReport]:
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

    start = time.monotonic()
    note: str | None = None
    try:
        history = await asyncio.wait_for(agent.run(max_steps=_MAX_STEPS), timeout=_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("browsing agent timed out on %s", url)
        note = f"timed out after {_TIMEOUT_SECONDS}s"
        result = _salvage_result_from_history(getattr(agent, "history", None), url, persona, note)
        return result, _browsing_stage_report(agent, time.monotonic() - start, note)
    except Exception as e:
        logger.warning("browsing agent raised on %s: %s", url, e)
        note = f"salvaged: browsing raised {type(e).__name__}: {e}"
        result = _salvage_result_from_history(getattr(agent, "history", None), url, persona, note)
        return result, _browsing_stage_report(agent, time.monotonic() - start, note)

    duration_s = time.monotonic() - start

    structured = history.structured_output
    result: BrowsingResult | None = None
    if isinstance(structured, BrowsingResult):
        result = structured
    elif isinstance(structured, dict):
        try:
            result = BrowsingResult.model_validate(structured)
        except Exception as e:
            logger.warning("failed to parse structured dict on %s: %s", url, e)
            note = f"salvaged: parsing structured output failed: {e}"
            result = _salvage_result_from_history(history, url, persona, note)

    if result is not None:
        try:
            result.outgoing_links = _external_links(history.urls(), url)
        except Exception as e:
            logger.warning("could not derive outgoing_links on %s: %s", url, e)
        return result, _browsing_stage_report(agent, duration_s, note)

    logger.warning("browsing agent returned no structured output on %s; salvaging from history", url)
    note = "salvaged: browsing agent produced no structured output"
    result = _salvage_result_from_history(history, url, persona, note)
    return result, _browsing_stage_report(agent, duration_s, note)
