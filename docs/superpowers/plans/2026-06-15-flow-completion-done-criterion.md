# Flow-completion done-criterion + prompt restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Browsing Agent from declaring a visit finished too early (especially treating an off-path cross-domain detour as the result) by restructuring its task prompt into markdown, merging duplicated rules, and tightening "done" to a relatedness-based criterion.

**Architecture:** Pure task-prompt change in `_build_task_prompt` (browsing.py) plus its assertions in `tests/test_browsing.py`. No new agent, no pipeline/model/settings change; browser-use's built-in planning stays at defaults. The new "done" rule is content/intent based: a cross-domain page that is the continuation/result of the flow still counts as finished (so legitimate payment-processor handoffs are not abandoned), while an unrelated detour does not.

**Tech Stack:** Python 3.12, pytest (offline, sync), browser-use (pinned fork), pydantic v2.

Spec: `docs/superpowers/specs/2026-06-15-flow-completion-done-criterion-design.md`

---

## Constraints the executor MUST respect

- **Do NOT run the full suite** (`uv run pytest`) — it makes live paid OpenAI / AgentMail / WHOIS calls. Only run `tests/test_browsing.py`, which is offline.
- **Do NOT touch `data/`.** Commit with explicit pathspecs only: `git commit ... -- <file> <file>`.
- **Blind-browser invariant:** the prompt must never contain the words `scam, phishing, suspicious, fake, fabricated, fraud, anti-, luhn, card_tier`. `test_prompt_stays_neutral` and `test_prompt_does_not_leak_card_tier_or_luhn` guard this — keep them green.
- Working directory is the repo root `/Users/haoquan/Documents/Project/anti-scam-agent`. If a shell `cd`'d elsewhere earlier, `cd` back first.

---

## Task 1: Restructure the task prompt and tighten the done-criterion

**Files:**
- Modify: `src/anti_scam_agent/browsing.py` (`_build_task_prompt`, currently the `return f"""..."""` body)
- Test: `tests/test_browsing.py` (add two tests; all existing prompt tests must stay green)

This is a single TDD unit: add the two new failing assertions first, watch them fail, then rewrite the prompt so every test (old and new) passes. Every commit stays green because the existing behavioral substrings are preserved verbatim in the rewrite.

- [ ] **Step 1: Add the two new prompt tests**

In `tests/test_browsing.py`, add these two tests (place them next to the other `test_prompt_*` functions, e.g. right after `test_prompt_waits_for_lazy_lists_to_settle`):

```python
def test_prompt_done_criterion_is_relatedness_based():
    # "Finished" is judged by whether the page continues/results from the flow, not by
    # domain: a different website that is the continuation still counts; an unrelated
    # detour does not. (A blunt cross-domain=failure rule would make the agent flee
    # legitimate payment-processor handoffs.)
    low = _build_task_prompt("http://example.com", _persona()).lower()
    assert "continuation or result" in low
    assert "even if it is on a different website" in low
    assert "unrelated to your task" in low


def test_prompt_plans_and_tracks_progress():
    # Reinforces browser-use's built-in planning: lay the flow out as steps, track which
    # step you're on, and don't declare done while steps remain.
    low = _build_task_prompt("http://example.com", _persona()).lower()
    assert "lay the flow out" in low
    assert "which step you are on" in low
    assert "have not finished while there are still steps" in low
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest "tests/test_browsing.py::test_prompt_done_criterion_is_relatedness_based" "tests/test_browsing.py::test_prompt_plans_and_tracks_progress" -v`
Expected: both FAIL with `AssertionError` (the current prompt has none of these strings).

- [ ] **Step 3: Rewrite `_build_task_prompt` body into markdown sections**

In `src/anti_scam_agent/browsing.py`, replace the entire `return f"""..."""` statement inside `_build_task_prompt` with exactly this (keep the function signature, docstring, and the `f` prefix; do not change anything above the `return`):

```python
    return f"""# Your visit to {url}

You are visiting the website {url} as an interested first-time user who wants to go all the way through whatever the site offers.

## Who you are

Use these details to fill in any forms the site asks you to complete:
  - Full name: {persona.name}
  - Email: {persona.email}
  - Password (for any account you need to create): {persona.password}
  - Phone number: {persona.phone}
  - Mailing address: {persona.address}
  - Credit card number: {persona.credit_card_number}
  - Credit card expiry: {persona.credit_card_expiry}
  - Credit card security code (CVV): {persona.credit_card_cvv}

If a form is in English or will not accept Chinese characters, give the same details in their international form instead:
  - Full name (international): {persona.name_international}
  - Phone number (international): {persona.phone_international}
  - Mailing address (international): {persona.address_international}

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
```

Note for the executor: the arrows are written as ASCII `->` on purpose (not a Unicode arrow) to avoid any encoding surprises; everything else is plain prose. Do not reintroduce a literal `{` or `}` anywhere in the string — there are none, and the only `{...}` are the persona/url interpolations.

- [ ] **Step 4: Run the browsing tests to verify ALL pass**

Run: `uv run pytest tests/test_browsing.py -v`
Expected: PASS — all of them, including the two new tests from Step 1 and every existing `test_prompt_*` (their guarded substrings are preserved verbatim in the rewrite). The count should be 28 passed (26 previous + 2 new).

If any existing `test_prompt_*` fails, the rewrite dropped a guarded substring. Compare the failing assertion's expected string against the new prompt and restore that exact wording — do NOT weaken the test.

- [ ] **Step 5: Commit**

```bash
cd /Users/haoquan/Documents/Project/anti-scam-agent
git add -- src/anti_scam_agent/browsing.py tests/test_browsing.py
git commit -m "feat: restructure task prompt into markdown and tighten the done-criterion

Lean on browser-use's already-active built-in planning (plan_update / per-step plan
re-injection) by reinforcing 'plan the flow and track which step you're on; not done
while steps remain'. Restructure _build_task_prompt into markdown sections and merge
the duplicated anti-loop / dialog / targeting-miss rules. Tighten 'done' to a
relatedness-based test: a cross-domain page that continues/results from the flow still
counts (so legitimate payment-processor handoffs aren't abandoned), an unrelated detour
does not — directly fixing the case where a stray redirect was reported as the result.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 6: Confirm `data/` was not swept in**

Run: `git show --stat HEAD`
Expected: only `src/anti_scam_agent/browsing.py` and `tests/test_browsing.py` listed — no `data/` paths.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Restructure into markdown sections (merging duplicates) → Step 3 (full new prompt with the 7 named sections; anti-loop/dialog/targeting-miss merged under "When something goes wrong"). ✓
- Plan-and-track reinforcement of built-in planning → Step 3 "## Plan and track your progress" + `test_prompt_plans_and_tracks_progress` (Step 1). ✓
- Relatedness-based done-criterion (cross-domain continuation counts; unrelated detour doesn't; visit_completed wording) → Step 3 "## Finishing and reporting" + `test_prompt_done_criterion_is_relatedness_based` (Step 1). ✓
- Preserve guarded behavioral substrings + neutrality → Step 4 verifies all existing `test_prompt_*` stay green; substrings preserved verbatim in Step 3. ✓
- Tests updated → Step 1 adds the two required new assertions. ✓

**Placeholder scan:** none — the full prompt text and both tests are inline. ✓

**Type/string consistency:** the three new-test assertion strings each appear verbatim (lowercased) in the Step 3 prompt: "continuation or result", "even if it is on a different website", "unrelated to your task", "lay the flow out", "which step you are on", "have not finished while there are still steps". ✓
