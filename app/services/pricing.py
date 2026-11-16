"""Model pricing: the single source of truth for USD cost estimates.

Approximate OpenAI list prices per 1M tokens; update here when prices
move - the eval scripts and the runtime usage ledger both import this
module, so an estimate can never diverge between the dashboard and the
eval reports. Unknown models return None (never a fabricated zero): a
caller decides whether None means "skip" (evals) or "record without
cost" (the usage ledger).
"""

# (input_usd_per_1m, output_usd_per_1m)
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "text-embedding-3-small": (0.02, 0.0),
    "gpt-4o-mini": (0.15, 0.60),
}


def cost_usd(model: str, tokens_in: int, tokens_out: int = 0) -> float | None:
    """Estimated USD for a call; None when the model has no known price."""
    prices = PRICES_PER_1M.get(model)
    if prices is None:
        return None
    in_price, out_price = prices
    return round(tokens_in / 1e6 * in_price + tokens_out / 1e6 * out_price, 6)
