# Run-level Observability Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit a complete, data-derived (never LLM-generated) report for every pipeline run — per-step tokens/cost/time for browsing, per-call for analysis, and run-level totals — written to `logs/<ts>_<domain>/`.

**Architecture:** A new `reporting.py` module owns pure data models, a maintained pricing table, a time-window token-attribution function, and JSON/text renderers. `run_browsing_agent` and `run_analysis_agent` each additionally return a `StageReport` extracted from already-produced data (`agent.history`, `token_cost_service.usage_history`, `result.context_wrapper.usage`). `run_pipeline` times the stages, assembles a `RunReport`, tees the run's Python logging to `debug.log`, and writes the artifacts. No LLM is called to build the report.

**Tech Stack:** Python 3.12, Pydantic v2, `uv`, `pytest`. Reads `browser_use` (`AgentHistoryList`, `TokenUsageEntry`, `ChatInvokeUsage`) and `openai-agents` (`Usage`) types but the pure core is testable without them.

---

## File Structure

- **Create** `src/anti_scam_agent/reporting.py` — pricing table, cost math, report data models, time-window attribution, JSON/text renderers, `write_run_report`, and the run-scoped `debug.log` logging handler context manager. Single responsibility: turn already-produced data into report files; no LLM, no agent-prompt influence.
- **Create** `tests/test_reporting.py` — pure/offline tests (cost math, attribution invariants, renderers). Belongs to the fast tier alongside `test_models.py`.
- **Modify** `src/anti_scam_agent/browsing.py` — `run_browsing_agent` returns `(BrowsingResult, StageReport)`; add a `_browsing_stage_report(agent, duration_s, note)` extractor; replace the existing `get_usage_summary()`/`logger.info(summary)` lines.
- **Modify** `src/anti_scam_agent/analysis.py` — `run_analysis_agent` returns `(ScamAssessment, StageReport)`; build the stage report from `result.context_wrapper.usage`; replace the existing per-field `logger.info` lines.
- **Modify** `src/anti_scam_agent/pipeline.py` — time each stage, build `RunReport`, open the run-scoped `debug.log` handler, write artifacts, still return `ScamAssessment`.
- **Modify** `src/anti_scam_agent/__main__.py` — add `--verbose` flag (env `ASA_LOG_VERBOSE=1` also honored), print the report path.
- **Modify** `.gitignore` — add `logs/`.

---

## Task 1: Pricing table, cost math, and `LLMCallMetrics`

**Files:**
- Create: `src/anti_scam_agent/reporting.py`
- Test: `tests/test_reporting.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reporting.py
from anti_scam_agent.reporting import LLMCallMetrics, cost_usd, combine_metrics


def test_cost_known_model_with_cache_discount():
    # gpt-4.1: input 2.00 / cached 0.50 / output 8.00 per 1M tokens.
    # cost_usd's input_tokens is the NON-cached prompt tokens (see docstring): 800 here.
    c = cost_usd("gpt-4.1", input_tokens=800, cached_input_tokens=200, output_tokens=500)
    expected = (800 * 2.00 + 200 * 0.50 + 500 * 8.00) / 1_000_000
    assert c == expected


def test_cost_mini_model():
    c = cost_usd("gpt-4.1-mini", input_tokens=1000, cached_input_tokens=0, output_tokens=1000)
    assert c == (1000 * 0.40 + 1000 * 1.60) / 1_000_000


def test_cost_unknown_model_is_none():
    assert cost_usd("some-future-model", input_tokens=10, cached_input_tokens=0, output_tokens=10) is None


def test_metrics_from_counts_sets_cost_and_totals():
    m = LLMCallMetrics.from_counts("gpt-4.1", prompt_tokens=1000, cached_input_tokens=200, output_tokens=500)
    assert m.input_tokens == 800        # prompt minus cached
    assert m.cached_input_tokens == 200
    assert m.output_tokens == 500
    assert m.total_tokens == 1500       # non-cached input + cached + output
    assert m.cost_usd == (800 * 2.00 + 200 * 0.50 + 500 * 8.00) / 1_000_000


def test_combine_sums_tokens_and_costs():
    a = LLMCallMetrics.from_counts("gpt-4.1", prompt_tokens=1000, cached_input_tokens=0, output_tokens=100)
    b = LLMCallMetrics.from_counts("gpt-4.1", prompt_tokens=2000, cached_input_tokens=0, output_tokens=200)
    total = combine_metrics([a, b])
    assert total.input_tokens == 3000
    assert total.output_tokens == 300
    assert total.total_tokens == 3300
    assert total.cost_usd == a.cost_usd + b.cost_usd


def test_combine_unknown_cost_poisons_total():
    known = LLMCallMetrics.from_counts("gpt-4.1", prompt_tokens=1000, cached_input_tokens=0, output_tokens=100)
    unknown = LLMCallMetrics.from_counts("mystery", prompt_tokens=1000, cached_input_tokens=0, output_tokens=100)
    total = combine_metrics([known, unknown])
    assert total.input_tokens == 2000      # tokens still sum
    assert total.cost_usd is None          # any unknown cost => total cost unknown


def test_combine_empty_is_zero():
    z = combine_metrics([])
    assert z.total_tokens == 0
    assert z.cost_usd == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'anti_scam_agent.reporting'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/anti_scam_agent/reporting.py
"""Data-derived run report. No LLM is called here; this module only assembles and
formats numbers and strings produced elsewhere (agent history, token-usage records,
the static-signal stage timing, and the final assessment)."""

from __future__ import annotations

from pydantic import BaseModel


class ModelPrice(BaseModel):
    """USD per 1,000,000 tokens."""

    input: float
    cached_input: float
    output: float


# Maintained by hand. Add a line per model you run. gpt-4.1 / gpt-4.1-mini current pricing.
_PRICING: dict[str, ModelPrice] = {
    "gpt-4.1": ModelPrice(input=2.00, cached_input=0.50, output=8.00),
    "gpt-4.1-mini": ModelPrice(input=0.40, cached_input=0.10, output=1.60),
}


def cost_usd(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float | None:
    """USD cost for a call. input_tokens here is the NON-cached prompt tokens. Unknown
    model -> None (never guess a price)."""
    price = _PRICING.get(model)
    if price is None:
        return None
    return (
        input_tokens * price.input
        + cached_input_tokens * price.cached_input
        + output_tokens * price.output
    ) / 1_000_000


class LLMCallMetrics(BaseModel):
    input_tokens: int = 0           # non-cached prompt tokens
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = 0.0

    @classmethod
    def from_counts(
        cls, model: str, prompt_tokens: int, cached_input_tokens: int, output_tokens: int
    ) -> "LLMCallMetrics":
        """prompt_tokens follows the browser_use convention: it INCLUDES cached tokens."""
        cached = cached_input_tokens or 0
        non_cached_input = max(prompt_tokens - cached, 0)
        return cls(
            input_tokens=non_cached_input,
            cached_input_tokens=cached,
            output_tokens=output_tokens,
            total_tokens=non_cached_input + cached + output_tokens,
            cost_usd=cost_usd(model, non_cached_input, cached, output_tokens),
        )


def combine_metrics(parts: list[LLMCallMetrics]) -> LLMCallMetrics:
    """Sum metrics. Tokens always sum. Cost sums only when every part has a known cost;
    if any part's cost is None (unknown model), the combined cost is None — honest rather
    than silently under-counting."""
    costs = [p.cost_usd for p in parts]
    combined_cost: float | None
    if any(c is None for c in costs):
        combined_cost = None
    else:
        combined_cost = sum(c or 0.0 for c in costs)
    return LLMCallMetrics(
        input_tokens=sum(p.input_tokens for p in parts),
        cached_input_tokens=sum(p.cached_input_tokens for p in parts),
        output_tokens=sum(p.output_tokens for p in parts),
        total_tokens=sum(p.total_tokens for p in parts),
        cost_usd=combined_cost,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/reporting.py tests/test_reporting.py
git commit -m "feat: pricing table and LLMCallMetrics for run reporting"
```

---

## Task 2: Report data models (`StepRecord`, `StageReport`, `RunReport`)

**Files:**
- Modify: `src/anti_scam_agent/reporting.py`
- Test: `tests/test_reporting.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_reporting.py
from anti_scam_agent.reporting import StepRecord, StageReport, RunReport


def _step(n: int, model: str = "gpt-4.1") -> StepRecord:
    return StepRecord(
        step_number=n,
        duration_s=1.0,
        url="http://example.com",
        action_types=["click"],
        thinking="thinking text",
        evaluation="eval text",
        memory="mem",
        next_goal="goal text",
        result_errors=[],
        metrics=LLMCallMetrics.from_counts(model, prompt_tokens=1000, cached_input_tokens=0, output_tokens=100),
    )


def test_stage_report_totals_equal_steps_plus_other():
    steps = [_step(1), _step(2)]
    other = LLMCallMetrics.from_counts("gpt-4.1", prompt_tokens=500, cached_input_tokens=0, output_tokens=50)
    stage = StageReport.build(name="browsing", model="gpt-4.1", duration_s=10.0, steps=steps, other_metrics=other)
    expected = combine_metrics([s.metrics for s in steps] + [other])
    assert stage.totals.total_tokens == expected.total_tokens
    assert stage.totals.cost_usd == expected.cost_usd


def test_run_report_grand_total_equals_sum_of_stages():
    s1 = StageReport.build("browsing", "gpt-4.1", 10.0, [_step(1)], LLMCallMetrics())
    s2 = StageReport.build("analysis", "gpt-4.1", 2.0, [_step(2)], LLMCallMetrics())
    run = RunReport.build(
        target_domain="example.com",
        url="http://example.com",
        started_at="2026-06-15T18:30:12+08:00",
        duration_s=12.0,
        stages=[s1, s2],
        verdict="likely_scam",
        is_scam=True,
    )
    expected = combine_metrics([s1.totals, s2.totals])
    assert run.grand_total.total_tokens == expected.total_tokens
    assert run.grand_total.cost_usd == expected.cost_usd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: FAIL with `ImportError: cannot import name 'StepRecord'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/anti_scam_agent/reporting.py

class StepRecord(BaseModel):
    step_number: int
    duration_s: float
    url: str | None = None
    action_types: list[str] = []
    thinking: str | None = None         # transcribed from agent output, not regenerated
    evaluation: str | None = None
    memory: str | None = None
    next_goal: str | None = None
    result_errors: list[str] = []
    metrics: LLMCallMetrics = LLMCallMetrics()


class StageReport(BaseModel):
    name: str                           # "browsing" | "signals" | "analysis"
    model: str | None = None
    duration_s: float
    steps: list[StepRecord] = []
    other_metrics: LLMCallMetrics = LLMCallMetrics()   # LLM calls not tied to a step
    totals: LLMCallMetrics = LLMCallMetrics()
    note: str | None = None             # "timed out after Ns", "salvaged: ...", etc.

    @classmethod
    def build(
        cls,
        name: str,
        model: str | None,
        duration_s: float,
        steps: list[StepRecord],
        other_metrics: LLMCallMetrics,
        note: str | None = None,
    ) -> "StageReport":
        totals = combine_metrics([s.metrics for s in steps] + [other_metrics])
        return cls(
            name=name,
            model=model,
            duration_s=duration_s,
            steps=steps,
            other_metrics=other_metrics,
            totals=totals,
            note=note,
        )


class RunReport(BaseModel):
    target_domain: str
    url: str
    started_at: str                     # ISO 8601 local time
    duration_s: float
    stages: list[StageReport] = []
    grand_total: LLMCallMetrics = LLMCallMetrics()
    verdict: str | None = None
    is_scam: bool | None = None

    @classmethod
    def build(
        cls,
        target_domain: str,
        url: str,
        started_at: str,
        duration_s: float,
        stages: list[StageReport],
        verdict: str | None,
        is_scam: bool | None,
    ) -> "RunReport":
        grand_total = combine_metrics([s.totals for s in stages])
        return cls(
            target_domain=target_domain,
            url=url,
            started_at=started_at,
            duration_s=duration_s,
            stages=stages,
            grand_total=grand_total,
            verdict=verdict,
            is_scam=is_scam,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/reporting.py tests/test_reporting.py
git commit -m "feat: StepRecord/StageReport/RunReport models with self-consistent totals"
```

---

## Task 3: Time-window token attribution

**Files:**
- Modify: `src/anti_scam_agent/reporting.py`
- Test: `tests/test_reporting.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_reporting.py
from anti_scam_agent.reporting import CallSample, StepWindow, attribute_calls


def test_attribute_calls_buckets_by_window_and_collects_leftovers():
    windows = [StepWindow(step_number=1, start=0.0, end=10.0), StepWindow(step_number=2, start=10.0, end=20.0)]
    calls = [
        CallSample(timestamp=1.0, model="gpt-4.1", prompt_tokens=1000, cached_input_tokens=0, output_tokens=100),
        CallSample(timestamp=5.0, model="gpt-4.1", prompt_tokens=2000, cached_input_tokens=0, output_tokens=200),  # step 1 (2 calls)
        CallSample(timestamp=12.0, model="gpt-4.1", prompt_tokens=3000, cached_input_tokens=0, output_tokens=300),  # step 2
        CallSample(timestamp=25.0, model="gpt-4.1", prompt_tokens=4000, cached_input_tokens=0, output_tokens=400),  # leftover
    ]
    per_step, other = attribute_calls(calls, windows)

    assert per_step[1].input_tokens == 3000   # 1000 + 2000
    assert per_step[2].input_tokens == 3000
    assert other.input_tokens == 4000         # the 25.0 call, outside every window

    # invariant: per-step + other == sum of all calls
    grand = combine_metrics(list(per_step.values()) + [other])
    each = combine_metrics([LLMCallMetrics.from_counts(c.model, c.prompt_tokens, c.cached_input_tokens, c.output_tokens) for c in calls])
    assert grand.total_tokens == each.total_tokens
    # float addition is non-associative; the groupings differ only in last-bit rounding.
    assert grand.cost_usd == pytest.approx(each.cost_usd)  # requires `import pytest` at top


def test_attribute_calls_no_windows_all_other():
    calls = [CallSample(timestamp=1.0, model="gpt-4.1", prompt_tokens=100, cached_input_tokens=0, output_tokens=10)]
    per_step, other = attribute_calls(calls, [])
    assert per_step == {}
    assert other.input_tokens == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: FAIL with `ImportError: cannot import name 'CallSample'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/anti_scam_agent/reporting.py

class CallSample(BaseModel):
    """One LLM call's raw counts plus the epoch time it was recorded. Decoupled from
    browser_use types so attribution is unit-testable without an agent."""

    timestamp: float                    # epoch seconds
    model: str
    prompt_tokens: int                  # includes cached tokens
    cached_input_tokens: int = 0
    output_tokens: int = 0


class StepWindow(BaseModel):
    step_number: int
    start: float                        # epoch seconds
    end: float


def attribute_calls(
    calls: list[CallSample], windows: list[StepWindow]
) -> tuple[dict[int, LLMCallMetrics], LLMCallMetrics]:
    """Assign each call to the first step window whose [start, end] contains its timestamp;
    calls outside every window go to the 'other' bucket. Returns (per_step, other)."""
    per_step_calls: dict[int, list[LLMCallMetrics]] = {w.step_number: [] for w in windows}
    other_calls: list[LLMCallMetrics] = []
    for c in calls:
        m = LLMCallMetrics.from_counts(c.model, c.prompt_tokens, c.cached_input_tokens, c.output_tokens)
        window = next((w for w in windows if w.start <= c.timestamp <= w.end), None)
        if window is None:
            other_calls.append(m)
        else:
            per_step_calls[window.step_number].append(m)
    per_step = {n: combine_metrics(ms) for n, ms in per_step_calls.items()}
    return per_step, combine_metrics(other_calls)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/reporting.py tests/test_reporting.py
git commit -m "feat: time-window token attribution for browsing steps"
```

---

## Task 4: JSON and text renderers

**Files:**
- Modify: `src/anti_scam_agent/reporting.py`
- Test: `tests/test_reporting.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_reporting.py
from anti_scam_agent.reporting import render_json, render_log


def _sample_run() -> RunReport:
    steps = [_step(1), _step(2)]
    browsing = StageReport.build("browsing", "gpt-4.1", 10.0, steps, LLMCallMetrics())
    analysis = StageReport.build("analysis", "gpt-4.1", 2.0, [_step(3)], LLMCallMetrics())
    signals = StageReport.build("signals", None, 5.0, [], LLMCallMetrics())
    return RunReport.build(
        target_domain="example.com", url="http://example.com",
        started_at="2026-06-15T18:30:12+08:00", duration_s=17.0,
        stages=[browsing, signals, analysis], verdict="likely_scam", is_scam=True,
    )


def test_render_json_roundtrips():
    import json
    run = _sample_run()
    parsed = json.loads(render_json(run))
    assert parsed["target_domain"] == "example.com"
    assert parsed["grand_total"]["total_tokens"] == run.grand_total.total_tokens
    # full thinking text is always present in JSON
    assert parsed["stages"][0]["steps"][0]["thinking"] == "thinking text"


def test_render_log_concise_omits_full_thinking_but_keeps_eval_goal():
    text = render_log(_sample_run(), verbose=False)
    assert "Anti-Scam Run" in text
    assert "example.com" in text
    assert "Stage: browsing" in text
    assert "Stage: signals" in text and "no LLM" in text
    assert "likely_scam" in text
    assert "goal text" in text          # concise still shows the next-goal line
    assert "thinking text" not in text  # full thinking only with verbose


def test_render_log_verbose_includes_full_thinking():
    text = render_log(_sample_run(), verbose=True)
    assert "thinking text" in text


def test_render_log_marks_unknown_pricing():
    steps = [_step(1, model="mystery")]
    stage = StageReport.build("browsing", "mystery", 1.0, steps, LLMCallMetrics())
    run = RunReport.build("e.com", "http://e.com", "2026-06-15T18:30:12+08:00", 1.0, [stage], "uncertain", False)
    text = render_log(run, verbose=False)
    assert "pricing unknown" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_json'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/anti_scam_agent/reporting.py

def render_json(run: RunReport) -> str:
    return run.model_dump_json(indent=2)


def _fmt_cost(cost: float | None) -> str:
    return "(pricing unknown)" if cost is None else f"${cost:.4f}"


def _fmt_metrics(m: LLMCallMetrics) -> str:
    return (
        f"{_fmt_cost(m.cost_usd)}   {m.total_tokens:,} tok "
        f"(in {m.input_tokens:,} / cached {m.cached_input_tokens:,} / out {m.output_tokens:,})"
    )


def render_log(run: RunReport, verbose: bool = False) -> str:
    lines: list[str] = []
    lines.append("================ Anti-Scam Run ================")
    lines.append(f"Target   : {run.target_domain}  ({run.url})")
    lines.append(f"Started  : {run.started_at}")
    lines.append(f"Duration : {run.duration_s:.1f}s")
    lines.append(f"Cost     : {_fmt_metrics(run.grand_total)}")
    lines.append(f"Verdict  : {run.verdict}   (is_scam={run.is_scam})")
    lines.append("")

    for stage in run.stages:
        if stage.model is None and not stage.steps:
            lines.append(f"-- Stage: {stage.name:<10} {stage.duration_s:.1f}s   (no LLM)")
        else:
            lines.append(
                f"-- Stage: {stage.name:<10} {stage.duration_s:.1f}s   "
                f"{_fmt_cost(stage.totals.cost_usd)}   {stage.totals.total_tokens:,} tok   model={stage.model}"
            )
        if stage.note:
            lines.append(f"   note: {stage.note}")
        for step in stage.steps:
            actions = ",".join(step.action_types) or "-"
            lines.append(
                f"   step {step.step_number:02d}  {step.duration_s:.1f}s  {actions:<24} "
                f"{step.metrics.total_tokens:,} tok  {_fmt_cost(step.metrics.cost_usd)}"
            )
            if step.evaluation:
                lines.append(f"     eval : {step.evaluation}")
            if step.next_goal:
                lines.append(f"     goal : {step.next_goal}")
            for err in step.result_errors:
                lines.append(f"     ! result error: {err}")
            if verbose and step.thinking:
                for tline in step.thinking.splitlines():
                    lines.append(f"     | {tline}")
        if stage.other_metrics.total_tokens:
            lines.append(
                f"   (other LLM calls not tied to a step: {stage.other_metrics.total_tokens:,} tok, "
                f"{_fmt_cost(stage.other_metrics.cost_usd)})"
            )
        lines.append("")

    per_stage = " / ".join(f"{s.name} {s.duration_s:.1f}" for s in run.stages)
    lines.append("================ Totals ================")
    lines.append(f"LLM cost : {_fmt_cost(run.grand_total.cost_usd)}      LLM tokens: {run.grand_total.total_tokens:,}")
    lines.append(f"Wall time: {run.duration_s:.1f}s       ({per_stage})")
    lines.append("===============================================")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/reporting.py tests/test_reporting.py
git commit -m "feat: JSON and human-readable renderers for run report"
```

---

## Task 5: `write_run_report` and the run-scoped debug-log handler

**Files:**
- Modify: `src/anti_scam_agent/reporting.py`
- Test: `tests/test_reporting.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_reporting.py
import logging
from anti_scam_agent.reporting import write_run_report, run_debug_log


def test_write_run_report_creates_folder_and_files(tmp_path):
    run = _sample_run()
    folder = write_run_report(run, logs_root=tmp_path, verbose=False)
    assert folder.parent == tmp_path
    assert (folder / "report.json").exists()
    assert (folder / "report.log").exists()
    # folder name carries timestamp + domain
    assert "example.com" in folder.name
    log_text = (folder / "report.log").read_text()
    assert "Anti-Scam Run" in log_text


def test_run_debug_log_captures_root_logging_then_detaches(tmp_path):
    debug_file = tmp_path / "debug.log"
    root = logging.getLogger()
    before = len(root.handlers)
    with run_debug_log(debug_file):
        logging.getLogger("anti_scam_agent.something").warning("captured line")
    assert "captured line" in debug_file.read_text()
    # handler removed on exit (no leak across runs)
    assert len(root.handlers) == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: FAIL with `ImportError: cannot import name 'write_run_report'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/anti_scam_agent/reporting.py — add these imports at the top of the file:
#   import logging
#   from contextlib import contextmanager
#   from pathlib import Path
#   from collections.abc import Iterator


def write_run_report(run: RunReport, logs_root: Path, verbose: bool = False) -> Path:
    """Create logs_root/<started_at-compact>_<domain>/ and write report.json + report.log.
    Returns the run folder. (debug.log is written separately via run_debug_log.)"""
    stamp = run.started_at.replace(":", "-")
    folder = logs_root / f"{stamp}_{run.target_domain}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(render_json(run))
    (folder / "report.log").write_text(render_log(run, verbose=verbose))
    return folder


@contextmanager
def run_debug_log(debug_file: Path) -> Iterator[None]:
    """Tee all Python logging emitted during the run into debug_file, then detach.
    Captures browser_use internals and our own logger.warning calls without touching
    individual call sites."""
    debug_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(debug_file, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous_level)
```

Note: `write_run_report` writes report.json/report.log into the run folder; `debug.log`
is created by `run_debug_log` and must point at the same folder (the pipeline wires this
in Task 8 by reusing the folder name).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_reporting.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/reporting.py tests/test_reporting.py
git commit -m "feat: write_run_report and run-scoped debug-log handler"
```

---

## Task 6: Browsing stage extractor + return `(BrowsingResult, StageReport)`

**Files:**
- Modify: `src/anti_scam_agent/browsing.py`
- Test: covered by the existing live test contract; the extractor's pure logic is already covered by Task 3. No new offline test (it reads live agent objects).

- [ ] **Step 1: Add the extractor helper**

Add to `src/anti_scam_agent/browsing.py` (near the other helpers, after `_external_links`).
Add `from anti_scam_agent.reporting import CallSample, StepWindow, StageReport, StepRecord, attribute_calls, combine_metrics` to the imports.

```python
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

    # Step windows from history metadata.
    windows: list[StepWindow] = []
    for h in history_items:
        meta = getattr(h, "metadata", None)
        if meta is not None:
            windows.append(StepWindow(step_number=meta.step_number, start=meta.step_start_time, end=meta.step_end_time))

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
                # action dict is {action_name: {...params}}; take the first key
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

    return StageReport.build(
        name="browsing", model=model, duration_s=duration_s, steps=steps, other_metrics=other, note=note
    )
```

- [ ] **Step 2: Change `run_browsing_agent` to time itself and return the report in every path**

Replace the body of `run_browsing_agent` from the `try:` block onward. Add `import time` at the top of the file if not present. The function signature becomes:

```python
async def run_browsing_agent(
    url: str, persona: FakePersona, client: "AgentMail", inbox: str
) -> tuple[BrowsingResult, StageReport]:
```

Replace the run/except/parse block with:

```python
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
```

Delete the old lines that called `await agent.token_cost_service.get_usage_summary()` and `logger.info(summary)` — that information is now captured by `_browsing_stage_report`.

- [ ] **Step 3: Run the full suite to ensure nothing offline broke**

Run: `uv run pytest tests/test_reporting.py tests/test_models.py tests/test_browsing.py -v`
Expected: PASS (these are offline; `test_browsing.py` still guards the neutral tool description).

- [ ] **Step 4: Commit**

```bash
git add src/anti_scam_agent/browsing.py
git commit -m "feat: browsing stage telemetry; run_browsing_agent returns StageReport"
```

---

## Task 7: Analysis stage report + return `(ScamAssessment, StageReport)`

**Files:**
- Modify: `src/anti_scam_agent/analysis.py`

- [ ] **Step 1: Change the return type and build the stage report**

Add `import time` and `from anti_scam_agent.reporting import StageReport, StepRecord, LLMCallMetrics` to imports. Change the signature:

```python
async def run_analysis_agent(
    browsing_result: BrowsingResult,
    domain: str,
    static_signals: StaticSignals | None = None,
) -> tuple[ScamAssessment, StageReport]:
```

Replace the run/usage-logging tail (from `result = await Runner.run(...)` onward) with:

```python
    start = time.monotonic()
    result = await Runner.run(agent, input=user_message)
    duration_s = time.monotonic() - start

    u = result.context_wrapper.usage
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    metrics = LLMCallMetrics.from_counts(
        "gpt-4.1",
        prompt_tokens=u.input_tokens,
        cached_input_tokens=cached,
        output_tokens=u.output_tokens,
    )
    step = StepRecord(step_number=1, duration_s=duration_s, action_types=["analyze"], metrics=metrics)
    stage = StageReport.build(
        name="analysis", model="gpt-4.1", duration_s=duration_s, steps=[step], other_metrics=LLMCallMetrics()
    )
    return result.final_output_as(ScamAssessment), stage
```

Note: the model string `"gpt-4.1"` matches the hardcoded `model=` on the `Agent(...)` above; if that changes, change both (the plan keeps them adjacent in this file).

- [ ] **Step 2: Run the live analysis test**

Run: `uv run pytest tests/test_analysis.py -v` (needs `OPENAI_API_KEY`)
Expected: PASS — but it will FAIL first because the test unpacks a single return value. Fix the test call sites to unpack the tuple (e.g. `assessment, _stage = await run_analysis_agent(...)`).

- [ ] **Step 3: Update `tests/test_analysis.py` call sites**

For each `await run_analysis_agent(...)` call in `tests/test_analysis.py`, change it to unpack:

```python
assessment, stage = await run_analysis_agent(result, domain, static_signals)
```

and keep the existing assertions on `assessment`. Add one assertion that the stage was recorded:

```python
assert stage.name == "analysis"
assert stage.totals.total_tokens > 0
```

- [ ] **Step 4: Run the live analysis test again**

Run: `uv run pytest tests/test_analysis.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/analysis.py tests/test_analysis.py
git commit -m "feat: analysis stage telemetry; run_analysis_agent returns StageReport"
```

---

## Task 8: Wire `run_pipeline` to assemble and write the report

**Files:**
- Modify: `src/anti_scam_agent/pipeline.py`

- [ ] **Step 1: Rewrite `run_pipeline` to time stages, build the RunReport, tee debug.log, and write artifacts**

```python
# src/anti_scam_agent/pipeline.py
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.email_evidence import make_client, pick_inbox
from anti_scam_agent.models import ScamAssessment
from anti_scam_agent.persona import generate_persona
from anti_scam_agent.reporting import (
    LLMCallMetrics,
    RunReport,
    StageReport,
    render_log,
    run_debug_log,
    write_run_report,
)
from anti_scam_agent.signals import collect_static_signals

_LOGS_ROOT = Path("logs")


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


async def run_pipeline(url: str, verbose: bool = False) -> ScamAssessment:
    domain = _extract_domain(url)
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    # Pre-compute the run folder so debug.log lands beside report.log/report.json.
    run_folder = _LOGS_ROOT / f"{started_at.replace(':', '-')}_{domain}"

    with run_debug_log(run_folder / "debug.log"):
        run_start = time.monotonic()
        persona = generate_persona()

        client = make_client()  # raises if unconfigured
        inbox = pick_inbox()
        persona = persona.model_copy(update={"email": inbox})

        result, browsing_stage = await run_browsing_agent(url, persona, client, inbox)

        sig_start = time.monotonic()
        static_signals = await asyncio.to_thread(collect_static_signals, url)
        signals_stage = StageReport.build(
            name="signals", model=None, duration_s=time.monotonic() - sig_start, steps=[], other_metrics=LLMCallMetrics()
        )

        assessment, analysis_stage = await run_analysis_agent(result, domain, static_signals)

        run_duration = time.monotonic() - run_start
        report = RunReport.build(
            target_domain=domain,
            url=url,
            started_at=started_at,
            duration_s=run_duration,
            stages=[browsing_stage, signals_stage, analysis_stage],
            verdict=assessment.verdict.value,
            is_scam=assessment.is_scam,
        )
        folder = write_run_report(report, logs_root=_LOGS_ROOT, verbose=verbose)

    # stderr so stdout stays the assessment-JSON contract.
    print(f"📄 report: {folder / 'report.log'}", file=sys.stderr)
    return assessment
```

Note: `write_run_report` derives the same folder name from `started_at` + domain, so it
writes `report.json`/`report.log` into the same `run_folder` that `run_debug_log` used.

- [ ] **Step 2: Run offline suite**

Run: `uv run pytest tests/test_reporting.py tests/test_models.py -v`
Expected: PASS (pipeline isn't exercised offline, but imports must resolve).

- [ ] **Step 3: Verify the module imports cleanly**

Run: `uv run python -c "import anti_scam_agent.pipeline"`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add src/anti_scam_agent/pipeline.py
git commit -m "feat: assemble and write per-run report in run_pipeline"
```

---

## Task 9: CLI `--verbose` flag and report-path print

**Files:**
- Modify: `src/anti_scam_agent/__main__.py`

- [ ] **Step 1: Add the flag (env-overridable) and pass it through**

```python
# src/anti_scam_agent/__main__.py
import argparse
import asyncio
import os
import sys

from anti_scam_agent.pipeline import run_pipeline


def _normalize_url(raw: str) -> str:
    if "://" in raw:
        return raw
    return f"http://{raw}"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anti-scam-agent",
        description="Assess whether a website is a scam / phishing site.",
    )
    parser.add_argument("url", help="Target URL or bare domain.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=os.environ.get("ASA_LOG_VERBOSE", "") not in ("", "0", "false", "False"),
        help="Include full agent thinking in report.log (report.json always has it). "
        "Also enabled via ASA_LOG_VERBOSE=1.",
    )
    args = parser.parse_args()

    url = _normalize_url(args.url)
    assessment = asyncio.run(run_pipeline(url, verbose=args.verbose))
    print(assessment.model_dump_json(indent=2))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify the CLI parses**

Run: `uv run anti-scam-agent --help`
Expected: help text including the `--verbose` option.

- [ ] **Step 3: Commit**

```bash
git add src/anti_scam_agent/__main__.py
git commit -m "feat: --verbose flag (ASA_LOG_VERBOSE) for report.log thinking"
```

Note: the report path is printed to **stderr** by `run_pipeline` (Task 8), so stdout
stays the assessment-JSON contract.

---

## Task 10: Ignore `logs/` in git

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Append the ignore rule**

Add this line to `.gitignore`:

```
logs/
```

- [ ] **Step 2: Verify it is ignored**

Run: `mkdir -p logs && touch logs/probe && git status --porcelain logs/`
Expected: no output (logs/ is ignored). Then `rm logs/probe`.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore logs/ run artifacts"
```

---

## Task 11: End-to-end smoke run (manual verification)

**Files:** none (verification only).

- [ ] **Step 1: Run the pipeline against a real URL and inspect the artifacts**

Run (needs `OPENAI_API_KEY` + `AGENTMAIL_API_KEY`): `uv run anti-scam-agent example.com`
Then:

```bash
ls -R logs/ | tail -20
cat "$(ls -dt logs/*/ | head -1)/report.log"
```

Expected: a run folder containing `report.log`, `report.json`, `debug.log`; `report.log`
shows the header, per-stage breakdown with per-step token/cost/time, the signals stage as
`(no LLM)`, the analysis call, and self-consistent totals. Confirm `grand_total` equals the
sum of stage totals by eye.

- [ ] **Step 2: Confirm verbose mode inlines thinking**

Run: `uv run anti-scam-agent example.com --verbose`
Expected: `report.log` now includes the `|`-prefixed full thinking lines per step; `report.json` is unchanged in that it always had them.

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -v`
Expected: offline tests PASS; live tests PASS when keys/network are available.

---

## Self-Review Notes

- **Spec coverage:** output layout (Tasks 5/8/10), reporting.py + data model (Tasks 1–2), pricing incl. gpt-4.1-mini + cost formula (Task 1), time-window attribution + invariants (Task 3), data sources/transcription (Tasks 6–7), pipeline wiring incl. signals stage + debug.log (Task 8), `--verbose`/`ASA_LOG_VERBOSE` (Task 9), `.gitignore logs/` (Task 10), tests (Tasks 1–5 offline; 7/11 live), failure-path telemetry (Task 6). All spec sections map to a task.
- **Blind-browser invariant:** untouched — reporting reads post-hoc data and never edits task prompts or `BrowsingResult` field descriptions; `test_models.py`/`test_browsing.py` still run in Task 6.
- **Type consistency:** `LLMCallMetrics.from_counts(model, prompt_tokens, cached_input_tokens, output_tokens)`, `StageReport.build(name, model, duration_s, steps, other_metrics, note=None)`, `RunReport.build(...)`, `attribute_calls(calls, windows) -> (dict[int, LLMCallMetrics], LLMCallMetrics)`, `write_run_report(run, logs_root, verbose) -> Path`, `run_debug_log(debug_file)` are used consistently across tasks.
