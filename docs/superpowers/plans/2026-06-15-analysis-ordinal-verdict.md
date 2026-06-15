# Analysis ordinal verdict + markdown prompt — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `ScamAssessment`'s `is_scam: bool` + `confidence: float` with a single 5-level `Verdict` enum (plus an `is_scam` computed property for binary eval), and restructure the analysis prompt into markdown.

**Architecture:** One cohesive contract change across `models.py` (the Verdict enum + ScamAssessment) and `analysis.py` (the prompt), with the two consuming test files updated in the same commit so the offline suite stays green. The single source of truth is `verdict`; `is_scam` is derived (`scam`/`likely_scam` → True, the rest → False, so `uncertain` maps to not-scam).

**Tech Stack:** Python 3.12, pydantic v2, openai-agents, pytest (offline, sync).

Spec: `docs/superpowers/specs/2026-06-15-analysis-ordinal-verdict-design.md`

---

## Constraints the executor MUST respect

- **Do NOT run the two live `test_analysis.py` tests** (`test_analysis_agent_returns_assessment_for_scam_fixture`, `test_analysis_agent_returns_assessment_for_legit_fixture`) — they make paid live OpenAI calls. Run only the offline node ids named in Step 5. The user runs the live ones.
- **Do NOT touch `data/`.** Commit with an explicit pathspec only.
- Working directory is the repo root `/Users/haoquan/Documents/Project/anti-scam-agent`. If a shell `cd`'d elsewhere, `cd` back first.
- The prompt feeds the Analysis Agent (not the blind Browsing Agent), so naming the fraud framing is fine here. But do not introduce the strings `luhn_invalid`, `card tier`, or `exoneration` (a test asserts their absence).

---

## Task 1: Ordinal Verdict enum, drop confidence, markdown analysis prompt

**Files:**
- Modify: `src/anti_scam_agent/models.py` (`ScamAssessment`, lines 38–43; add a `Verdict` enum)
- Modify: `src/anti_scam_agent/analysis.py` (`_SYSTEM_PROMPT`, lines 13–49)
- Test: `tests/test_models.py` (add a mapping test)
- Test: `tests/test_analysis.py` (swap confidence asserts; extend the prompt test)
- Test: `tests/test_pipeline.py` (fixture)

- [ ] **Step 1: Update the tests first**

(a) In `tests/test_models.py`, change the import line and add a mapping test at the end:

Change line 1 from:
```python
from anti_scam_agent.models import BrowsingResult, Outcome
```
to:
```python
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment, Verdict
```

Append:
```python
def test_scam_assessment_is_scam_mapping():
    def mk(v: Verdict) -> ScamAssessment:
        return ScamAssessment(verdict=v, scam_type=None, reasoning="r", risk_factors=[])

    assert mk(Verdict.scam).is_scam is True
    assert mk(Verdict.likely_scam).is_scam is True
    assert mk(Verdict.uncertain).is_scam is False
    assert mk(Verdict.likely_legitimate).is_scam is False
    assert mk(Verdict.legitimate).is_scam is False
```

(b) In `tests/test_analysis.py`, update the import and three tests.

Change line 5 from:
```python
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment
```
to:
```python
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment, Verdict
```

In `test_analysis_agent_returns_assessment_for_scam_fixture`, replace:
```python
    assert isinstance(assessment, ScamAssessment)
    assert 0.0 <= assessment.confidence <= 1.0
```
with:
```python
    assert isinstance(assessment, ScamAssessment)
    assert isinstance(assessment.verdict, Verdict)
    assert isinstance(assessment.is_scam, bool)
```

In `test_analysis_agent_returns_assessment_for_legit_fixture`, replace the identical two lines with the same three lines as above.

Replace the whole `test_system_prompt_encodes_explicit_decline_rule` body with:
```python
def test_system_prompt_encodes_explicit_decline_rule():
    from anti_scam_agent.analysis import _SYSTEM_PROMPT

    p = _SYSTEM_PROMPT.lower()
    # the payment rule's core reasoning is present
    assert "payment_explicitly_declined" in p
    assert "no real processor" in p
    # a hang/stall after submitting the card is treated as a signal, not abstained away
    assert "hung" in p or "stall" in p
    assert "credit_card_submitted is true" in p
    # the five ordinal verdict levels are spelled out
    for level in ("scam", "likely_scam", "uncertain", "likely_legitimate", "legitimate"):
        assert level in p
    # the dropped fields are gone, and old framings stay gone
    assert "confidence" not in p
    assert "is_scam" not in p
    assert "luhn_invalid" not in p
    assert "card tier" not in p
    assert "exoneration" not in p
```

(c) In `tests/test_pipeline.py`, update the import and fixture.

Change line 6 from:
```python
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment
```
to:
```python
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment, Verdict
```

Replace `_assessment`:
```python
def _assessment() -> ScamAssessment:
    return ScamAssessment(
        is_scam=False, confidence=0.1, scam_type=None, reasoning="r", risk_factors=[]
    )
```
with:
```python
def _assessment() -> ScamAssessment:
    return ScamAssessment(
        verdict=Verdict.legitimate, scam_type=None, reasoning="r", risk_factors=[]
    )
```

- [ ] **Step 2: Run the mapping test to verify it fails**

Run: `uv run pytest "tests/test_models.py::test_scam_assessment_is_scam_mapping" -q`
Expected: FAIL with an `ImportError` (cannot import `Verdict`) — the enum does not exist yet.

- [ ] **Step 3: Implement the model change in `src/anti_scam_agent/models.py`**

Replace the `ScamAssessment` class (lines 38–43) with a `Verdict` enum followed by the new `ScamAssessment`:
```python
class Verdict(str, Enum):
    scam = "scam"
    likely_scam = "likely_scam"
    uncertain = "uncertain"
    likely_legitimate = "likely_legitimate"
    legitimate = "legitimate"

class ScamAssessment(BaseModel):
    verdict: Annotated[Verdict, Field(description="Ordinal scam judgment from 'scam' (most scam-like) through 'uncertain' to 'legitimate' (most legitimate).")]
    scam_type: Annotated[str | None, Field(description="Category of scam, e.g. 'phishing', 'fake lottery', 'credit card harvesting'. None if not scam-leaning.")]
    reasoning: Annotated[str, Field(description="Detailed explanation of the assessment, citing specific evidence.")]
    risk_factors: Annotated[list[str], Field(description="Specific observations that contributed to the judgment.")]

    @property
    def is_scam(self) -> bool:
        """Binary collapse for evaluation; 'uncertain' maps to not-scam (conservative)."""
        return self.verdict in {Verdict.scam, Verdict.likely_scam}
```
(`Enum`, `BaseModel`, `Field`, `Annotated` are already imported at the top of the file.)

- [ ] **Step 4: Rewrite `_SYSTEM_PROMPT` in `src/anti_scam_agent/analysis.py`**

Replace the entire `_SYSTEM_PROMPT = """..."""` assignment (lines 13–49) with:
```python
_SYSTEM_PROMPT = """# Role

You are a fraud analyst. You are given a structured report from a colleague who visited a target website as an ordinary user, the site's domain, and a bundle of locally-computed static signals (WHOIS, TLS certificate, DNS). Your job is to judge whether the site is a scam / phishing operation, with reasoning. All the evidence you need is in the input — there are no tools to call.

## Reading the report

The report uses four-state outcomes ('not_attempted', 'failed', 'unclear', 'succeeded'). Only 'succeeded' is an explicit positive; 'unclear' means there was no clear response and must not be read as success.

## The payment signal (the central test)

The colleague submitted a real-format but fabricated credit card. A legitimate merchant runs a real payment processor that ALWAYS rejects such a card with an explicit card error (declined / invalid card). So the ONLY benign reaction is an explicit card decline.
  - If credit_card_submitted is true AND payment_explicitly_declined is true: the site behaved like a real processor — this is REASSURING for the payment path.
  - If credit_card_submitted is true AND payment_explicitly_declined is false — whatever payment_outcome says ('succeeded', 'unclear', or even 'failed' for a non-card reason; e.g. it silently accepted the card, moved on to a vague success/thank-you/confirmation page, OR the page stalled / hung / kept loading forever after submission, all with no card-specific error): this is a STRONG scam signal. No real processor lets a fabricated card through without an explicit card error, and it does not hang instead of declining.
  - If the card was never submitted (credit_card_submitted false): there is no payment signal; judge on the other evidence.
payment_outcome is descriptive context; payment_explicitly_declined is the field that decides the payment judgment.

## Static signals

Any field may be null when a lookup failed — treat null as 'unknown', never as evidence.
  - domain_info: days_since_creation, days_until_expiration, registrar, registrant_country, privacy_protected.
  - tls: issuer_org, age_days, san_count, is_free_dv (a free domain-validated certificate, e.g. Let's Encrypt/ZeroSSL).
  - dns: has_mx (does the domain accept mail?), nameservers.

## Heuristics

Combine them — no single signal is definitive:
  - Card submitted and NOT explicitly declined = strong evidence of a scam (see the payment signal above).
  - Very young domains (days_since_creation < 90) combined with payment acceptance or heavy PII collection are strong scam signals.
  - A young domain + a brand-new free DV certificate + no MX record is a classic throwaway-scam fingerprint; together they compound risk, though none alone is conclusive.
  - has_mx=false is a weak negative signal (a real merchant usually has company mail); has_mx=true is mild reassurance. Never decisive alone.
  - privacy_protected and free DV certs are common on legitimate sites too — only let them compound an already-young or payment-positive case.
  - Old, long-expiration domains with normal user flows and an MX record are a weak signal of low risk.
  - Requests for unusually sensitive PII (national ID, bank account, mother's maiden name) alongside other red flags compound risk.
  - Unexpected redirects to unrelated domains (see outgoing_links) after submitting data are suspicious.

## When the evidence is thin

If visit_completed is false AND credit_card_submitted is false, the colleague gathered almost no behavioral evidence, so do not return a scam-leaning verdict: choose 'uncertain' (or a legitimate-leaning level) unless the static signals alone are overwhelmingly damning. This abstention does NOT apply when credit_card_submitted is true: a submitted card that met no explicit decline — including a page that stalled, hung, or kept loading after submission instead of returning a clear card error — is the STRONG scam signal above even though visit_completed is false (the visit ended because the SITE hung, not because evidence was missing). Weigh the payment signal fully in that case.

## Your verdict

Return a ScamAssessment:
  - verdict: choose the single level that best fits the weight of evidence:
      - 'scam' — multiple corroborating signals; you are confident it is a scam (for example a fabricated card accepted or not explicitly declined, alongside other red flags).
      - 'likely_scam' — meaningful scam signals, but with a gap or some doubt.
      - 'uncertain' — the evidence is genuinely mixed or insufficient; do not commit either way.
      - 'likely_legitimate' — it looks legitimate, with only minor unknowns.
      - 'legitimate' — clearly legitimate behavior (for example an explicit card decline by a real processor, an established domain, and a normal user flow).
  - scam_type: a short category like 'phishing', 'fake lottery', 'credit card harvesting', or None when the verdict is not scam-leaning.
  - reasoning: a paragraph citing specific observations from the browsing report and static signals.
  - risk_factors: the concrete items from the inputs that drove your judgment.
"""
```

- [ ] **Step 5: Run the offline tests to verify they pass**

Run (offline node ids only — never the two live fixture tests):
```bash
uv run pytest tests/test_models.py tests/test_pipeline.py \
  "tests/test_analysis.py::test_run_analysis_agent_accepts_static_signals" \
  "tests/test_analysis.py::test_system_prompt_encodes_explicit_decline_rule" -q
```
Expected: PASS — all collected tests pass, including `test_scam_assessment_is_scam_mapping` and the rewritten prompt test.

If `test_system_prompt_encodes_explicit_decline_rule` fails on a guarded substring, the prompt rewrite dropped it — compare the failing string against the Step 4 text and restore that exact wording.

- [ ] **Step 6: Commit**

```bash
cd /Users/haoquan/Documents/Project/anti-scam-agent
git add -- src/anti_scam_agent/models.py src/anti_scam_agent/analysis.py \
  tests/test_models.py tests/test_analysis.py tests/test_pipeline.py
git commit -m "feat: ordinal Verdict enum for analysis (drop confidence) + markdown prompt

Replace ScamAssessment is_scam+confidence with a single 5-level Verdict enum
(scam/likely_scam/uncertain/likely_legitimate/legitimate) plus an is_scam computed
property for binary eval (uncertain -> not-scam, conservative). Drop verbalized
confidence as uncalibrated false precision. Restructure the analysis prompt into
markdown sections with anchored per-level descriptions; the ABSTAIN rule now resolves
to the 'uncertain' level instead of capping a confidence score.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 7: Confirm `data/` was not swept in**

Run: `git show --stat HEAD`
Expected: only the five files above are listed — no `data/` paths.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Verdict 5-level enum → Step 3. ✓
- `is_scam` computed property + mapping (uncertain→not-scam) → Step 3 property + `test_scam_assessment_is_scam_mapping` (Step 1a). ✓
- Drop `confidence` → Step 3 (field removed) + Step 1b prompt test asserts `"confidence" not in p`. ✓
- Markdown prompt restructure with anchored levels → Step 4 (7 sections, per-level descriptions). ✓
- ABSTAIN rule resolves to `uncertain` → Step 4 "## When the evidence is thin". ✓
- Tests updated (test_analysis confidence→verdict/is_scam; test_pipeline fixture; test_models mapping) → Step 1a/1b/1c. ✓
- Consumers: no code branches on is_scam/confidence (confirmed in spec); eval reads the property. No src consumer changes needed. ✓

**Placeholder scan:** none — full prompt text, full model code, and all test edits are inline. ✓

**Type/name consistency:** `Verdict` (enum) and `ScamAssessment.verdict` / `.is_scam` are used identically across Steps 1, 3, and the tests. The five level names match between Step 3 (enum values), Step 4 (prompt), and the Step 1b prompt-test loop. ✓
