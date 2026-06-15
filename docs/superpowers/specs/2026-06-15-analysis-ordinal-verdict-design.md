# Analysis verdict: ordinal enum, drop confidence, markdown prompt

Date: 2026-06-15
Status: Approved (design)
Scope: `src/anti_scam_agent/models.py` (`ScamAssessment`), `src/anti_scam_agent/analysis.py` (prompt), and tests (`test_analysis.py`, `test_pipeline.py`, `test_models.py`).

## Problem / motivation

`ScamAssessment` currently returns `is_scam: bool` + `confidence: float (0–1)`. Current LLM-as-judge research (June 2026) shows that verbalized 0–1 confidence from an LLM judge is systematically overconfident, saturates into a few bins, and is only trustworthy after calibration against human-labeled data — which we do not do. So the continuous `confidence` is false precision. Meanwhile scam evidence has genuine gradations, and an explicit "uncertain / abstain" outcome (which we already approximate with an ABSTAIN rule) is a recognized best practice. We therefore replace `is_scam` + `confidence` with a single ordinal verdict, and restructure the analysis prompt into markdown for clarity (mirroring the browsing prompt).

## Decisions (locked during brainstorming)

- Verdict becomes a **single 5-level ordinal enum**; no separate confidence field.
- An explicit `uncertain` level exists; for the binary evaluation against `data/` (`website_url, is_scam`) it maps to **not-scam** (conservative — fewer false alarms).
- `confidence` is **removed** (the ordinal band carries graded certainty).
- `scam_type`, `reasoning`, `risk_factors` are **kept**.
- The analysis prompt is **restructured into markdown sections**.

## Consumers (confirmed)

No code branches on `is_scam`/`confidence`; `ScamAssessment` is only returned by `run_pipeline` to the CLI and read by an out-of-repo evaluation that compares against `data/`'s binary `is_scam` label. So the binary signal must remain available — provided as a computed property, not an LLM-output field.

## Design

### 1. `models.py`

```python
class Verdict(str, Enum):
    scam = "scam"
    likely_scam = "likely_scam"
    uncertain = "uncertain"
    likely_legitimate = "likely_legitimate"
    legitimate = "legitimate"

class ScamAssessment(BaseModel):
    verdict: Annotated[Verdict, Field(description="Ordinal scam judgment from 'scam' (most scam-like) through 'uncertain' to 'legitimate' (most legitimate).")]
    scam_type: Annotated[str | None, Field(description="Category of scam ... None if not a scam.")]
    reasoning: Annotated[str, Field(description="...")]
    risk_factors: Annotated[list[str], Field(description="...")]

    @property
    def is_scam(self) -> bool:
        """Binary collapse for evaluation. uncertain maps to not-scam (conservative)."""
        return self.verdict in {Verdict.scam, Verdict.likely_scam}
```

- `is_scam` is a `@property`, so it is not an LLM output and not in `model_fields` / `model_dump()`; the single source of truth is `verdict`. Evaluation reads `assessment.is_scam`.
- Mapping: `scam`, `likely_scam` → True; `uncertain`, `likely_legitimate`, `legitimate` → False.

### 2. `analysis.py` — restructure `_SYSTEM_PROMPT` into markdown + verdict change

Markdown sections (content preserved/merged from the current prose):

- `# Role` — fraud analyst; all evidence is in the input; no tools to call.
- `## Reading the report` — the four-state outcomes note ('succeeded' is the only positive; 'unclear' must not be read as success).
- `## The payment signal (the central test)` — fabricated-but-real-format card; the only benign reaction is an explicit card decline; `credit_card_submitted is true` AND `payment_explicitly_declined` false (including a page that stalled / hung / kept loading) is a STRONG scam signal because **no real processor** lets a fabricated card through without a card error. (Preserve the substrings the test guards: `payment_explicitly_declined`, `no real processor`, `credit_card_submitted is true`, `hung`/`stall`.)
- `## Static signals` — domain_info / tls / dns field descriptions; null means "unknown", never evidence.
- `## Heuristics` — combine signals; young domain + payment acceptance, throwaway fingerprint, MX, PII requests, unrelated redirects.
- `## When the evidence is thin` — the abstain rule, rewritten for the enum: if `visit_completed` is false AND `credit_card_submitted` is false, there is almost no behavioral evidence, so return **`uncertain`** (or a legitimate-leaning level) rather than `scam`/`likely_scam`, unless the static signals alone are overwhelming. This abstention does NOT apply when `credit_card_submitted` is true (the hung-after-submit case is the strong signal above, not missing evidence).
- `## Your verdict` — output a `ScamAssessment` with `verdict` chosen from the five levels, each anchored:
  - `scam` — multiple corroborating signals; high certainty (e.g. fabricated card accepted / not declined plus other red flags).
  - `likely_scam` — meaningful scam signals but with a gap or some doubt.
  - `uncertain` — genuinely mixed or insufficient evidence; do not commit either way.
  - `likely_legitimate` — looks legitimate with minor unknowns.
  - `legitimate` — clearly legitimate (e.g. an explicit card decline by a real processor, established domain, normal flow).
  Plus `scam_type` (None unless scam-leaning), `reasoning` (cite specific observations), `risk_factors`.

Remove all mention of `is_scam` and `confidence` (including "cap confidence at 0.4") from the prompt. Do not introduce the words `luhn_invalid`, `card tier`, or `exoneration`.

### 3. Tests

- `test_analysis.py`: drop the two `0.0 <= assessment.confidence <= 1.0` assertions; assert `isinstance(assessment.verdict, Verdict)` and `isinstance(assessment.is_scam, bool)`. Update `test_system_prompt_encodes_explicit_decline_rule` to the restructured prompt — keep its guarded substrings (`payment_explicitly_declined`, `no real processor`, `hung`/`stall`, `credit_card_submitted is true`; absent: `luhn_invalid`, `card tier`, `exoneration`) and add a check that the five verdict levels appear.
- `test_pipeline.py`: change the `_assessment()` fixture from `is_scam=False, confidence=0.1` to `verdict=Verdict.legitimate`.
- `test_models.py`: add `test_scam_assessment_is_scam_mapping` — `scam`/`likely_scam` → `is_scam` True; `uncertain`/`likely_legitimate`/`legitimate` → False.

## Non-goals

- No evaluation harness is built; no change to `data/`.
- No change to the browsing stage or `BrowsingResult`.
- No numeric/calibrated probability output (would require calibrating against the labeled set — a separate effort).

## Risk / trade-off

Categorical outputs trade calibration for consistency; we accept this because the output is a human-facing warning plus a binary eval label, neither of which needs a calibrated probability. The per-level anchored descriptions in the prompt are the cheap stabilizer. If a calibrated score is ever needed (e.g. ROC tuning on `data/`), that is a separate calibration task.
