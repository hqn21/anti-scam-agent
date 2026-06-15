# Flow completion: tighten the "done" criterion + restructure the task prompt

Date: 2026-06-15
Status: Approved (design)
Scope: `src/anti_scam_agent/browsing.py` (`_build_task_prompt`) and `tests/test_browsing.py` only.

## Problem

The Browsing Agent sometimes ends a visit too early. The concrete failure observed
in live testing: it mis-clicked a button, the site navigated to an unrelated page on
another domain, and the agent reported *that* page as the final result instead of going
back and continuing the flow. The agent loses the global picture of "which flow am I in
and where am I in it," so an off-path detour reads to it like a conclusion.

## Key finding (why we are NOT adding a new agent)

browser-use already ships an active planning / state-tracking system, and it is enabled
in our setup (`enable_planning=True` by default; only disabled in flash mode, which uses
`ChatBrowserUse` — we use `ChatOpenAI("gpt-4.1")`). Specifically (`agent/service.py`,
`agent/views.py`):

- The agent can emit `plan_update` (a list of step texts) — it plans the flow itself.
- The plan is re-injected into context every step with status markers:
  `[x]` done, `[>]` current, `[ ]` pending, `[-]` skipped — the global state view.
- Nudges fire automatically: replan after 3 consecutive failures
  (`planning_replan_on_stall`), "make a plan or call done" after 5 plan-less steps
  (`planning_exploration_limit`), plus loop detection.

So the user's idea of "plan the flow, then track global state" already exists and runs.
Building a separate planner/verifier agent would duplicate it and would also have to
honor the blind-browser invariant. The real problem is therefore not a missing mechanism
but a too-loose "done" judgment. We lean on the built-in planning and tighten "done".

## Goal

Stop premature completion — especially treating an off-path cross-domain detour as the
result — while restructuring the task prompt into clear markdown and merging the rules
that currently repeat.

## Design

Single-file prompt change plus its tests. No new agent, no pipeline/model/settings change.
Built-in planning stays at defaults.

### 1. Restructure `_build_task_prompt` into markdown sections (merging duplicates)

Proposed sections:

- `## Who you are` — persona details + the international identity block.
- `## Your goal` — complete the whole flow; stay on the main buyer/claimant path.
- `## Plan and track your progress` — (new, reinforces built-in planning) lay the flow
  out as steps and keep noting which step you are on; you have not finished while steps a
  user could still take remain.
- `## When something goes wrong` — merge the currently-overlapping rules: anti-loop
  ("done the same thing two or three times → stop, re-decide"), reading the
  "Auto-closed JavaScript dialogs" text and obeying it, and targeting-miss vs. site-refusal.
- `## Clicking buttons reliably` — prefer the click-by-visible-text tool for the
  flow-advancing call-to-action buttons; keep numbered clicks for text-less / duplicate
  controls; scroll lazy lists to settle before clicking a final button.
- `## Step by step` — the existing ordered checklist.
- `## Finishing and reporting` — the structured-output rules plus the tightened "done"
  criterion below.

Content/behavior is preserved; only duplicated phrasings are consolidated.

### 2. Tighten "done" — relatedness-based, NOT domain-based (core fix)

The test the agent applies is content/intent: "Is this page the continuation or result
of the flow I was running, or an unrelated detour?"

- You have finished when you reach a clear conclusion of the SAME task you were doing —
  a confirmation / success / thank-you / order-complete / payment-result page for the
  purchase or claim you were making. **This counts even if it is on a different website**,
  as long as it is clearly the continuation or result of what you were doing (e.g. a
  payment provider, the site's checkout partner, or a 3-D Secure bank verification page).
- You have NOT finished — go back and continue — if you land on a page that is unrelated
  to your task (an ad, a marketing page, a homepage, a random offer, an error/parking
  page) that does not continue the purchase or claim. That is a sign a click went wrong.
- Do not report the visit complete while steps a user could still take remain in your plan.
- `visit_completed=true` only when you reached a genuine end state of this flow (wherever
  it is hosted); otherwise `false`, with a note in `unexpected_events`.

This is deliberately relatedness-based because legitimate checkouts routinely hand off to
another domain (Stripe / PayPal / local gateways / 3-D Secure). A blunt "cross-domain =
failure" rule would make the agent flee legitimate payment-processor handoffs and so
destroy the canonical detection signal (submit a fabricated card, observe whether it is
explicitly declined). It is also consistent with the Analysis Agent, which already treats
redirects to *unrelated* domains after data submission as suspicious while treating
payment handoffs as normal. Cross-domain hops are still recorded in `outgoing_links`
regardless.

### 3. Tests (`tests/test_browsing.py`)

- Re-point the existing assertions to the restructured prompt; keep the behavioral
  substrings the suite guards: `find_text`, `visible text`, `Checkout`, `Pay`,
  `targeting miss`, `did not land`, `do not abandon`, `scroll all the way down`,
  `stops growing`, `make up`, `plausible`, the neutral forbidden-word list, and the
  international names.
- Add an assertion for the relatedness-based done criterion (a different website that is
  the continuation/result still counts; an unrelated detour does not).
- Add an assertion for the plan-tracking / "not finished while steps remain" line.

## Non-goals

- No deterministic code-level done-gate (approach C, declined).
- No second planner/verifier agent.
- No change to planning settings (defaults already nudge at 5 steps / 3 failures).

## Risk / trade-off

Pure prompt-level: it relies on the model honoring the stricter, relatedness-based "done"
judgment. If premature completion still occurs, the fallback levers (not built now) are a
code-level done-gate or tuning `planning_exploration_limit`.
