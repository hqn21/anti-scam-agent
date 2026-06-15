# Run-level Observability Report — Design

**Date:** 2026-06-15
**Branch:** `feat/run-logging`

## Problem

Logging is currently ad-hoc: `browsing.py` and `analysis.py` each call `logger.info(...)`
with no configured handler, so usage data goes nowhere durable. There is no per-run
artifact for tracing what happened or watching cost. We want every pipeline run to emit a
complete, **data-derived** (never LLM-generated) report covering the whole interaction
flow: per-interaction tokens / cost / time and run-level totals.

## Goals

- One self-contained artifact set per run, written under `logs/`.
- Per-step detail for the browsing stage (tokens, cost, duration, action, transcribed
  agent thinking/eval/goal, result errors) and per-call detail for analysis.
- Run-level totals: tokens, cost (USD), wall time, plus per-stage breakdown.
- Report is assembled and formatted by **plain Python from existing data** — no LLM call
  is made to produce it. The only LLM-authored text in the report is the *transcription*
  of thinking/eval strings the browsing agent already emitted into its history.
- Must not weaken the blind-browser invariant: reporting is post-hoc and never touches
  agent task prompts or `BrowsingResult` field descriptions.
- Must preserve the failure-tolerant pipeline: browsing never raises, analysis always
  runs; telemetry is captured even on timeout / exception / salvage.

## Non-goals

- No cross-run aggregation/dashboard yet (the JSON output makes that easy later).
- No change to detection logic, prompts, models, or verdict shapes.

## Output layout

One folder per run:

```
logs/<ISO8601-localtime>_<domain>/
  report.log     human-readable summary (primary artifact)
  report.json    structured data (machine-readable; for later cost roll-ups)
  debug.log      full Python logging stream for that run (browser_use internals,
                 timeout/salvage warnings, exceptions) — deep-trace aid
```

- Timestamp + domain in the folder name keep runs distinct and greppable.
- `debug.log` is produced by attaching a `logging.FileHandler` to the **root logger** at
  run start and removing it at run end, so existing scattered `logger.warning(...)` calls
  and browser_use's own logs are captured without touching each call site.
- `logs/` is added to `.gitignore` so run artifacts are never committed.

## New module: `reporting.py`

Single responsibility: assemble a report from already-produced data and write the files.
It calls no LLM and does not influence agent prompts.

### Data model (Pydantic)

```
ModelPrice          input, cached_input, output         (USD per 1M tokens)

LLMCallMetrics      input_tokens, cached_input_tokens, output_tokens,
                    total_tokens, cost_usd | None

StepRecord          step_number, duration_s, url, action_types[],
                    thinking, evaluation, memory, next_goal,   (transcribed)
                    result_errors[], metrics: LLMCallMetrics

StageReport         name ("browsing" | "signals" | "analysis"),
                    model | None, duration_s, steps: list[StepRecord],
                    other_metrics: LLMCallMetrics,   (LLM calls not tied to a step)
                    totals: LLMCallMetrics, note | None  ("timed out", "salvaged", ...)

RunReport           target_domain, url, started_at, duration_s,
                    stages: list[StageReport], grand_total: LLMCallMetrics,
                    verdict, is_scam
```

### Pricing & cost formula

A pricing table maintained in-code (one line per model):

```python
_PRICING = {
    "gpt-4.1":      ModelPrice(input=2.00, cached_input=0.50, output=8.00),
    "gpt-4.1-mini": ModelPrice(input=0.40, cached_input=0.10, output=1.60),
}

cost = (input_tokens - cached_input_tokens) * input/1e6
     + cached_input_tokens                  * cached_input/1e6
     + output_tokens                        * output/1e6
```

Unknown model → `cost_usd = None`, rendered as `(pricing unknown)` in report.log. Never
guess a price. Switching a stage to `gpt-4.1-mini` is just changing the model string in
`browsing.py` / `analysis.py`; the report picks up the right price and records the model.

### Per-step token attribution (browsing)

A browser_use step can trigger several LLM calls (main reasoning + page-extraction etc.),
so attribution is by **time window, not index**: each `StepMetadata` has
`step_start_time` / `step_end_time`; every `token_cost_service.usage_history` entry whose
`timestamp` falls inside a step's window is summed into that step. Entries outside all
windows (e.g. the final structured-output call) go into the stage's `other_metrics`
bucket. This is robust to multi-call steps and keeps the numbers self-consistent:

```
grand_total == sum(stage.totals)
stage.totals == sum(step.metrics) + stage.other_metrics
```

## Data sources (all existing; nothing LLM-generated)

| Report field | Source |
|---|---|
| stage `duration_s` | `time.monotonic()` around each stage in `run_pipeline` |
| step `duration_s` | `agent.history` `StepMetadata.step_end_time - step_start_time` |
| `action_types` | `agent.history` step `model_output.action` names |
| `thinking/evaluation/memory/next_goal` | transcribed from `agent.history` `model_output` |
| `result_errors` | `agent.history` step `result[].error` |
| step `url` | `agent.history` step `state.url` |
| token counts (browsing) | `agent.token_cost_service.usage_history` (per call), windowed |
| token counts (analysis) | `result.context_wrapper.usage` |
| `cost_usd` | token counts × `_PRICING` (pure arithmetic) |
| `verdict / is_scam` | final `ScamAssessment` |

## Pipeline wiring

- `run_browsing_agent` returns `(BrowsingResult, StageReport)`. Telemetry is extracted in
  **all** paths — success, timeout, exception, salvage — because the `agent` object (and
  thus `agent.history` + `token_cost_service`) exists in each. Failure paths set
  `StageReport.note` (`"timed out after Ns"`, `"salvaged: ..."`). The existing
  `logger.info(summary)` line is replaced by this structured extraction.
- `run_analysis_agent` returns `(ScamAssessment, StageReport)`.
- `collect_static_signals` stays unchanged; `run_pipeline` just times it and records a
  `StageReport(name="signals", model=None, steps=[])` with the duration (no LLM cost) so a
  slow WHOIS/TLS/DNS lookup is visible.
- `run_pipeline` owns the wall-clock timer, builds the `RunReport`, and calls
  `reporting.write_run_report(report, verbose=...)`. It still returns `ScamAssessment`
  (callers unaffected).

## CLI / config

- `__main__` gains `--verbose` (also honored via env `ASA_LOG_VERBOSE=1`). When set,
  `report.log` inlines each step's full `thinking` text; otherwise it shows concise
  eval/goal lines. `report.json` **always** contains the full thinking text regardless of
  the flag.
- After a run, `__main__` prints the report path: `📄 report: logs/.../report.log`.

## report.log shape (concise / default)

```
================ Anti-Scam Run ================
Target   : example.com  (http://example.com)
Started  : 2026-06-15 18:30:12  (Asia/Taipei)
Duration : 142.3s
Cost     : $0.0431   Tokens: 58,204  (in 54,100 / cached 12,800 / out 4,104)
Verdict  : likely_scam   (is_scam=True)

-- Stage: browsing   118.7s   $0.0402   55,900 tok   model=gpt-4.1
   step 01  1.2s  navigate                 2,104 tok  $0.0019
     eval : Starting fresh
     goal : Open the page and wait for load
   ...
   step 47  3.1s  input,click_by_text      4,980 tok  $0.0051
     eval : Card form filled
     goal : Submit payment
     ! result error: element index 8 not found
   (other LLM calls not tied to a step: 1 call, 1,200 tok, $0.0011)

-- Stage: signals    11.5s   (no LLM)

-- Stage: analysis    8.1s   $0.0029   1,980 tok   model=gpt-4.1
   call 01  8.1s   1,980 tok  $0.0029

================ Totals ================
LLM cost : $0.0431      LLM tokens: 57,880
Wall time: 142.3s       (browsing 118.7 / signals 11.5 / analysis 8.1)
===============================================
```

With `--verbose`, each step additionally prints its full `thinking` block.

## Testing

- Pure/offline tests for `reporting.py`:
  - cost formula (known model, cached-token discount, unknown model → `None`).
  - time-window attribution: synthetic usage entries + step windows bucket correctly,
    out-of-window entries land in `other_metrics`, and the self-consistency invariants
    (`grand_total == sum(stages)`, `stage.totals == sum(steps) + other`) hold.
  - renderer: `report.log` text and `report.json` are produced from a hand-built
    `RunReport` fixture (no agent, no network, no LLM) and contain expected lines/fields;
    verbose vs concise differ only by the thinking block.
- No live API needed for reporting tests (keeps them in the fast/offline tier alongside
  `test_models.py`).

## Risks / notes

- ChatInvokeUsage field names for cached tokens vary; the extractor reads them
  defensively and treats missing values as 0 (cost still computes on input+output).
- The root-logger FileHandler must be removed in a `finally` so a crashing run doesn't
  leave a dangling handler across runs in long-lived processes.
