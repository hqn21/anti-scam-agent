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
