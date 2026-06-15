import pytest

from anti_scam_agent.reporting import LLMCallMetrics, cost_usd, combine_metrics


def test_cost_known_model_with_cache_discount():
    # cost_usd's input_tokens is the NON-cached prompt tokens (see its docstring).
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
    assert m.input_tokens == 800
    assert m.cached_input_tokens == 200
    assert m.output_tokens == 500
    assert m.total_tokens == 1500  # non-cached input + cached + output
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
    assert total.input_tokens == 2000
    assert total.cost_usd is None


def test_combine_empty_is_zero():
    z = combine_metrics([])
    assert z.total_tokens == 0
    assert z.cost_usd == 0.0


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


from anti_scam_agent.reporting import CallSample, StepWindow, attribute_calls


def test_attribute_calls_buckets_by_window_and_collects_leftovers():
    windows = [StepWindow(step_number=1, start=0.0, end=10.0), StepWindow(step_number=2, start=10.0, end=20.0)]
    calls = [
        CallSample(timestamp=1.0, model="gpt-4.1", prompt_tokens=1000, cached_input_tokens=0, output_tokens=100),
        CallSample(timestamp=5.0, model="gpt-4.1", prompt_tokens=2000, cached_input_tokens=0, output_tokens=200),
        CallSample(timestamp=12.0, model="gpt-4.1", prompt_tokens=3000, cached_input_tokens=0, output_tokens=300),
        CallSample(timestamp=25.0, model="gpt-4.1", prompt_tokens=4000, cached_input_tokens=0, output_tokens=400),
    ]
    per_step, other = attribute_calls(calls, windows)
    assert per_step[1].input_tokens == 3000
    assert per_step[2].input_tokens == 3000
    assert other.input_tokens == 4000
    grand = combine_metrics(list(per_step.values()) + [other])
    each = combine_metrics([LLMCallMetrics.from_counts(c.model, c.prompt_tokens, c.cached_input_tokens, c.output_tokens) for c in calls])
    assert grand.total_tokens == each.total_tokens
    # float addition is non-associative; the two groupings differ only in last-bit rounding.
    assert grand.cost_usd == pytest.approx(each.cost_usd)


def test_attribute_calls_no_windows_all_other():
    calls = [CallSample(timestamp=1.0, model="gpt-4.1", prompt_tokens=100, cached_input_tokens=0, output_tokens=10)]
    per_step, other = attribute_calls(calls, [])
    assert per_step == {}
    assert other.input_tokens == 100


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
    assert parsed["stages"][0]["steps"][0]["thinking"] == "thinking text"


def test_render_log_concise_omits_full_thinking_but_keeps_eval_goal():
    text = render_log(_sample_run(), verbose=False)
    assert "Anti-Scam Run" in text
    assert "example.com" in text
    assert "Stage: browsing" in text
    assert "Stage: signals" in text and "no LLM" in text
    assert "likely_scam" in text
    assert "goal text" in text
    assert "thinking text" not in text


def test_render_log_verbose_includes_full_thinking():
    text = render_log(_sample_run(), verbose=True)
    assert "thinking text" in text


def test_render_log_marks_unknown_pricing():
    steps = [_step(1, model="mystery")]
    stage = StageReport.build("browsing", "mystery", 1.0, steps, LLMCallMetrics())
    run = RunReport.build("e.com", "http://e.com", "2026-06-15T18:30:12+08:00", 1.0, [stage], "uncertain", False)
    text = render_log(run, verbose=False)
    assert "pricing unknown" in text
