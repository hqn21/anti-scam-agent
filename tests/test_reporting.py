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
