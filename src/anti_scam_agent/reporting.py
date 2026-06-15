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
