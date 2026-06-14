# Precision & Flow-Completion Upgrade — Design

Date: 2026-06-14
Branch: `feat/precision-signals-upgrade`
Status: approved (brainstorming)

## Motivation

Real-world testing of the current build surfaced four problems that block the
next step:

1. **The Luhn-invalid thesis is too narrow.** The current "canonical signal" is a
   site accepting a card that fails the Luhn checksum. In practice the stronger,
   more general signal is: a *fabricated but Luhn-valid* card is **not explicitly
   rejected**. A real payment processor declines a fabricated valid-checksum card
   outright ("card declined / invalid card"); a scam site without a real processor
   either "succeeds" or lands on a vague success/thank-you page. Anything other than
   an explicit decline is the signal.

2. **Email was overweighted.** "An authenticated transactional email = strong
   exoneration" is wrong. SPF/DKIM/DMARC are not required to send mail; scam sites
   routinely run email verification (including unauthenticated mail) to look real.
   Receiving mail — even authenticated mail — does not clear a site.

3. **The Browsing Agent gets stuck and cannot complete flows.** Observed failure
   modes: it gets blocked behind dismissible prompts/overlays (and operates on
   elements behind them instead of closing them); it stalls when a form cannot be
   filled with the exact persona (e.g. an address with no matching region option);
   it does not aggressively exercise the payment path.

4. **AgentMail is now essential, not optional.** Email verification is part of many
   flows, so the email capability must always be present.

## Goals

- Make **completing the full site flow** the Browsing Agent's primary objective —
  filling *correct* information is explicitly not the goal.
- Redefine the payment signal around **"the fabricated card was not explicitly
  rejected."**
- Turn email into a **neutral, general-purpose tool the Browsing Agent can use
  mid-flow** (verification codes, confirmation links, anything), not a post-hoc
  analysis signal.
- Require AgentMail; fail fast if it is unconfigured.

## Non-goals

- Bucket 4 (decision-layer restructure) — deferred.
- Scoring/benchmark harness — deferred (comes after these fixes).
- The `data/` evaluation set — the user is editing it; untouched here.
- Tunable decision thresholds — still deferred until an eval set exists.

## The blind-browser invariant (unchanged, still paramount)

Nothing about scam detection, AgentMail, card validity, or payment-probing intent
may leak into anything the Browsing Agent sees — task prompt, tool descriptions, or
`BrowsingResult` field descriptions. All new prompt language and the new
`read_email_inbox` tool description must read as ordinary first-time-user behavior.
`tests/test_models.py` continues to enforce the forbidden-word list on
`BrowsingResult` descriptions; the new `payment_explicitly_declined` field must use
plain user-facing language and add no leaky terms.

---

## Workstreams

### WS1 — Require AgentMail; single Luhn-valid card

- `email_evidence.make_client()` (or its replacement): **raise** a clear
  `RuntimeError` when `AGENTMAIL_API_KEY` is unset, instead of returning `None`.
- `pipeline.run_pipeline` loses the "client is None → skip email" branch entirely;
  the persona email is **always** an AgentMail inbox.
- `persona.py`: drop `_break_luhn`; the persona carries a **single Luhn-valid**
  `credit_card_number`. Remove `credit_card_number_luhn_valid`.
- `models.FakePersona`: remove the `credit_card_number_luhn_valid` field.
- `pipeline`: remove the two-tier card flow and the `card_tier` concept — one
  browsing run only.

### WS2 — Redefine the payment signal

- Add a neutral field to `BrowsingResult`:
  `payment_explicitly_declined: bool` (default `False`), described in plain
  user-facing language, e.g. *"Whether the site clearly told you the card was
  declined or invalid (an explicit error specifically about the card)."*
- Semantics for analysis: the **only** "normal merchant" reaction to the fabricated
  card is `payment_explicitly_declined == True`. A submitted card that was *not*
  explicitly declined — `credit_card_submitted == True` and
  `payment_explicitly_declined == False` (whether `payment_outcome` is `succeeded`
  or `unclear`) — is a strong scam signal. `payment_outcome` is retained for
  description but is no longer the sole basis of the payment judgment.

### WS3 — Browsing flow-completion robustness (prompt only, stays blind)

Strengthen `_build_task_prompt` while remaining strictly user-framed:

- **Dismiss blockers first:** explicitly instruct the agent to close cookie banners,
  pop-ups, modals, and overlays before interacting with anything behind them.
- **Completion over correctness:** the persona is a *starting point*. When a form
  has no option matching the persona (e.g. address region not listed), the agent
  should pick any reasonable available option or **make up a plausible value** to
  proceed — never stall. State plainly that getting through the flow matters more
  than entering matching data.
- **Exercise payment:** when offered a choice of payment method (e.g. cash-on-
  delivery vs. credit card), always choose **credit card** — framed as a persona
  preference ("you prefer to pay by credit card"), no anti-scam framing.
- Raise `_MAX_STEPS` from 25 to **40** to accommodate longer register-plus-checkout
  flows that include an email-verification step.

### WS4 — Email as a mid-flow Browsing tool

- Add a neutral custom browser-use action, `read_email_inbox`, that returns the text
  of the most recent messages in the persona's inbox so the agent can read a
  verification code, confirmation link, or anything else the site emailed it. It
  must pass `include_unauthenticated=True` (scam verification mail is frequently
  unauthenticated).
- Tool description stays neutral: *"Check your email inbox and read your most recent
  messages — useful when a site says it has emailed you a code or link."*
- `run_browsing_agent` signature gains the inbox address and an AgentMail client (or
  a thin read-callable) so the action can reach the inbox. Wiring must not surface
  any anti-scam intent to the LLM.
- The action is failure-tolerant: any AgentMail error returns a benign "no new
  messages" string rather than raising, so browsing never breaks.

### WS5 — Remove the post-hoc email-evidence path

- Delete `collect_email_evidence` and the `EmailEvidence` model — nothing consumes
  them once email is a mid-flow tool only.
- `pipeline`: remove the concurrent post-hoc poll; only `collect_static_signals`
  runs after browsing.
- `analysis.run_analysis_agent`: remove the `email_evidence` parameter and the email
  block from the prompt and user message.
- Retire `tests/test_email_evidence.py` (or reduce it to whatever inbox-rotation /
  read-tool helpers survive).

### WS6 — Analysis, docs, and test alignment

- Rewrite `analysis._SYSTEM_PROMPT`: remove the entire card-tier section; remove the
  email-evidence section; replace the payment heuristics with the new rule
  (explicit decline = the only normal reaction; submitted-and-not-declined = strong
  scam signal). Keep the static-signal heuristics and the ABSTAIN rule.
- `run_analysis_agent`: drop `card_tier` and `email_evidence` params.
- `pipeline`: pass the reduced argument set to `run_analysis_agent`.
- `CLAUDE.md`: rewrite the project-purpose narrative — the canonical signal is now
  "a fabricated (Luhn-valid) card is **not explicitly rejected**," not "a
  Luhn-invalid card is accepted." Update the AgentMail section to describe the
  mid-flow `read_email_inbox` tool and the now-mandatory key. Remove the
  StaticSignals-era mention of email evidence as an exoneration signal.
- `tests/test_models.py`: **keep** `luhn` / `card_tier` on the forbidden-word list
  (the guard only gets stricter as those concepts leave the schema); ensure the new
  `payment_explicitly_declined` description passes the forbidden-word assertion.
- `tests/test_persona.py`: assert the single Luhn-valid card; drop the broken-card
  assertions.
- `tests/test_pipeline.py`, `tests/test_analysis.py`: align with the reduced
  signatures and the mandatory-AgentMail behavior.

---

## Data flow (after)

```
URL
 │
 ├─ generate_persona()                      # TW-localized, single Luhn-valid card
 ├─ make_client()                           # RAISES if AGENTMAIL_API_KEY unset
 ├─ persona.email = pick_inbox()            # always an AgentMail inbox
 │
 ├─ run_browsing_agent(url, persona, client, inbox)
 │     • dismiss blockers, complete the flow (improvise values)
 │     • always choose credit-card payment
 │     • read_email_inbox tool ── reads verification codes mid-flow
 │     └─> BrowsingResult (+ payment_explicitly_declined)
 │
 ├─ collect_static_signals(url)             # WHOIS / TLS / DNS (unchanged)
 │
 └─ run_analysis_agent(result, domain, static_signals)
       └─> ScamAssessment
```

## Error handling

- AgentMail unconfigured → fail fast at pipeline start (the one intentional
  hard-fail; everything else stays tolerant).
- `read_email_inbox` failures → benign "no messages" string; never breaks browsing.
- Browsing still wrapped in timeout + step cap, still returns `_fallback_result` on
  any failure so analysis always runs.
- `collect_static_signals` stays failure-tolerant (`_safe`), never raises.

## Testing

- Offline only for the dev loop (live OpenAI/AgentMail/WHOIS keys present; do not run
  the full live suite).
- `test_models`, `test_persona` updated and offline.
- New unit coverage for `payment_explicitly_declined` defaulting and for the
  `read_email_inbox` action's failure-tolerance (with a fake client).
- `test_pipeline` updated for mandatory AgentMail and reduced analysis args.

## Open risks

- browser-use custom-action API on the pinned fork must support an async action with
  injected dependencies (client + inbox). Verify against the installed version
  before finalizing WS4; fall back to a closure-captured read-callable if the
  decorator injection differs.
- Raising on missing AgentMail key means every offline test that constructs the
  pipeline must provide a dummy key or stub `make_client`.
