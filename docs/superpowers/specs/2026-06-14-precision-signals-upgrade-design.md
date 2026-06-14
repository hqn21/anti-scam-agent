# Anti-Scam Agent — Precision & Signals Upgrade

**Date:** 2026-06-14
**Scope:** Architecture changes to raise detection **precision** (fewer false positives) and add new local, no-blacklist signals. Four buckets, implemented in order: (1) precision fixes, (2) cheap out-of-band signals, (3) AgentMail email evidence, (4) decision-layer restructure. Scoring/benchmark code is written **after** these changes land.

## Goal

The current pipeline has a coupled defect that routes legitimate sites into a scam verdict, plus several missed signals that are cheap and local. This upgrade closes the false-positive paths, adds exoneration evidence (genuine transactional email), and restructures the final decision so it is calibratable later against an eval set — all without introducing blacklists or reputation databases. The blind-browser invariant (the Browsing Agent must never learn it is part of an anti-scam system or that its inputs are fabricated) is preserved throughout.

## The coupled defect this fixes (motivation)

Two existing facts multiply into a false-positive engine:

1. `browsing.py:45` instructs: *"if the website did not respond an explicit error, it should be account as a success action."*
2. `persona.py:25` produces a **Luhn-valid** card (Faker default).

A legitimate small merchant that does front-end Luhn validation + asynchronous capture will pass the Luhn-valid card, show no explicit error, and therefore be reported as `credit_card_accepted=true` — which `analysis.py:18` treats as *strong* scam evidence. Fixing only one half does nothing; both are fixed together in Bucket 1.

## Design principles

- **Precision over recall.** When evidence is thin or the visit failed, **abstain** (low confidence / not-scam) rather than guess. Accepting some false negatives is the explicit trade.
- **Division of labor in the decision.** The LLM does fuzzy per-signal *interpretation*; a thin deterministic layer enforces precision-first *invariants* on the LLM's distilled signal strengths. We do **not** hard-code fuzzy reality into rules.
- **Out-of-band signals stay out of band.** WHOIS/TLS/DNS/email checks run in the pipeline around the browsing step, never inside the blind agent's prompt or output schema.
- **No blacklists.** Every new signal is computed locally from the target itself.

---

## Bucket 1 — Precision fixes (core, ~zero runtime cost)

### 1.1 Four-state outcomes (`models.py`)

Replace the `login_succeeded` and `credit_card_accepted` booleans with a shared enum:

```python
class Outcome(str, Enum):
    not_attempted = "not_attempted"
    failed = "failed"
    unclear = "unclear"
    succeeded = "succeeded"
```

`BrowsingResult` fields become `login_outcome: Outcome` and `payment_outcome: Outcome`. Keep `login_attempted` / `credit_card_submitted` booleans (they answer "was it tried at all", distinct from the outcome). Add `visit_completed: bool` (True on a real finish, False on fallback).

**Blind invariant:** enum values and all field descriptions stay neutral, user-facing language. No `scam/phishing/suspicious/fake/fabricated`; keep `unexpected_events`. `tests/test_models.py` is extended to also assert the enum values and new field descriptions are leak-free.

### 1.2 Prompt fixes (`browsing.py`)

- **Delete** the line-45 "no explicit error = success" instruction. Replace with neutral guidance: an *explicit success screen / confirmation* maps to `succeeded`; *no visible response or ambiguous state* maps to `unclear`; an *explicit error / rejection* maps to `failed`; *never attempted* maps to `not_attempted`.
- Fix typos: line 37 `when for it fully loaded` → `wait for it to fully load`; line 45 region `likes login` → `like login`.
- `_fallback_result` sets `visit_completed=False` and uses `Outcome.not_attempted`.

### 1.3 Two-tier card (orchestrated in `pipeline.py`)

The Browsing Agent stays blind and single-card-per-run; the pipeline holds Luhn-validity out of band:

- **Run 1** — persona carries a **Luhn-invalid** card. If `payment_outcome == succeeded` → record as a **strong** signal (`card_tier="luhn_invalid"`); **stop** (no Run 2).
- **Run 2** — only if Run 1's `payment_outcome == failed` (front-end Luhn caught it): re-run browsing with a **Luhn-valid** card. Its `succeeded` is the **weaker** "instant success, no processor redirect" signal (`card_tier="luhn_valid"`).
- If Run 1 payment is `not_attempted` / `unclear` → **no** Run 2 (nothing to disambiguate); use Run 1.

The pipeline emits the chosen `BrowsingResult` plus a `card_tier` annotation so Analysis can weight invalid-accepted (strong) vs valid-accepted (weaker). The agent prompt only ever shows the one active card number; Luhn-validity never appears in any agent-visible text.

### 1.4 Persona fixes (`persona.py`)

- Primary card is **Luhn-invalid**: take a Faker card and flip the last digit (`(d+1) % 10`). Keep a separate Luhn-valid card for Run 2.
- **Amex → 4-digit CVV** (others 3), keyed off the chosen `card_type`.
- Strip phone extensions (`x1234`) so forms accept the number.
- Email is an **AgentMail inbox** (see Bucket 3), replacing `@example.com`.

### 1.5 Analysis weighting (`analysis.py`)

Prompt updated so: `payment_outcome == succeeded` with `card_tier="luhn_invalid"` is the strongest single signal; with `card_tier="luhn_valid"` it is secondary; `visit_completed == False` forces abstention (see Bucket 4 guardrails).

---

## Bucket 2 — Cheap out-of-band signals (local compute, no blacklist)

Computed in code and **attached to the Analysis input** (not all exposed as LLM-callable tools, to avoid round-trips), following the existing `tools/handler.py` convention (`_name` impl + `@function_tool` wrapper, re-exported in `tools/__init__.py`).

- **TLS certificate** (one ssl socket): issuer, cert age (days), SAN count. Scam sites are near-uniformly freshly-issued DV (Let's Encrypt/ZeroSSL).
- **DNS**: presence of MX record (real merchants almost always have one), nameservers, hosting ASN.
- **WHOIS expansion** (already hitting WHOIS): registrar, privacy-protection flag, registrant country — added to `DomainInfo`.
- **Outgoing links from history**: `run_browsing_agent` populates `outgoing_links` **programmatically from the browser-use `history`** URL trail (verify exact accessor against the pinned `browser-use` version during implementation), not from LLM self-report.

New shapes carry these into Analysis (e.g. extend `DomainInfo` and add a `StaticSignals` model). All time math stays consistent with the existing `Asia/Taipei` normalization in `_get_domain_info`.

---

## Bucket 3 — AgentMail email evidence (new module + API key)

A new module (e.g. `email_evidence.py`) plus `AGENTMAIL_API_KEY` in `.env` / `.env.example`.

- **Inboxes:** `asalpha@agentmail.to`, `asbravo@agentmail.to`, `ascharlie@agentmail.to`, **round-robin per scan**; the chosen inbox is the persona email.
- **Collection:** after browsing, poll the AgentMail API for ~2 minutes. **Attribute** messages by *sender-domain == target domain* within the *post-scan-start time window* — immune to leftover mail from prior scans across the shared inboxes.
- **`EmailEvidence` output:** `received_transactional_email` (verification/welcome), `sender_domain_matches`, `spf_pass`, `dkim_pass`, `message_count` (spam volume is itself a weak data-resale signal). SPF/DKIM are verified locally from received headers — no blacklist.
- **Directionality (key for precision):** a genuine transactional email (real ESP, SPF/DKIM pass, sender matches) is **strong exoneration** — it rescues "young domain but actually legitimate" sites. *Absence* of email is only a **weak** signal (some legit sites don't send) and never alone drives a scam verdict.
- **Blind invariant:** the Browsing Agent receives only an ordinary-looking email address; it does not know it is an API inbox. The inbox check is out-of-band, after browsing.
- **v1 verification-link handling:** do **not** auto-click verification links. If a site blocks progress pending email verification, the agent reports it neutrally (via `unexpected_events`), and Analysis treats "stuck at verification" as **leaning legitimate** (consistent with the abstain logic) — never as suspicious.

The pipeline gains a step between browsing and analysis: browse → poll AgentMail → build `EmailEvidence` → analyze. `EmailEvidence` is added to the Analysis input.

---

## Bucket 4 — Decision-layer restructure

**Hybrid, per current best practice** (LLM for judgment, deterministic layer for auditable combination; 5-level risk beats forced binary):

- **`ScamAssessment` gains structured per-signal output:** for each signal family (payment acceptance, domain age, email evidence, TLS/DNS, PII collection, redirects), a strength/risk label rather than only a single binary. Keep `is_scam`, `confidence`, `scam_type`, `reasoning`, `risk_factors`.
- **Low temperature** on the Analysis model for stability/reproducibility.
- **Deterministic guardrails (this round, safe + untunable only):**
  - `visit_completed == False` → cap confidence (e.g. ≤0.4) / emit "insufficient evidence", never a confident scam verdict.
  - Genuine transactional email present → raise the bar for a scam verdict (require an independent strong signal).
  - Default-innocent: thin evidence → `is_scam=false`, low confidence.
- **Deferred to post-benchmark:** the *tunable* combination threshold (e.g. "≥1 strong + ≥1 independent corroborator", with a tuned cutoff). Tuning a threshold without an eval set is the vibes trap; the eval set is built right after this round, then the threshold is calibrated against it.

---

## Out of scope (this round)

- Scoring/benchmark harness (next, against the user's existing dataset).
- Tunable decision threshold (waits for the benchmark).
- Two-stage cost gating (static pre-check to skip deep-browsing big legit sites) and early-stop — cost optimizations, not accuracy; revisit once the benchmark can measure the accuracy/cost trade.
- Browser-side verification-link click-through (a future neutral "check your inbox" custom tool).
- Web UI / extension / service layer.

## Testing

- `tests/test_models.py` (offline): extended to cover the `Outcome` enum values, `visit_completed`, and any new `BrowsingResult` field descriptions under the leak-free invariant.
- `tests/test_persona.py` (offline): Luhn-invalid primary card (fails Luhn), Luhn-valid secondary (passes), Amex→4-digit CVV, no phone extension, email is an AgentMail address.
- New offline tests for TLS/DNS parsing and email-attribution logic (pure functions; mock the network).
- `tests/test_analysis.py` (live OpenAI) and WHOIS-based tests follow existing conventions; new live AgentMail interaction is exercised by a separate, network-gated test.
