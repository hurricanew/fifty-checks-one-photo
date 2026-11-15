"""Decisions API cost. Output tokens are not billed."""

from decimal import Decimal

# Paid-tier input price for gemini-decision-1, per the Gemini pricing page.
# Output tokens are 0 and are not billed.
DECISIONS_INPUT_USD_PER_MILLION = Decimal("0.10")


def decisions_cost_usd(input_tokens: int) -> str:
    """Return US dollars as a fixed 8-decimal string.

    cost = input_tokens * 0.10 / 1_000_000
    """
    if isinstance(input_tokens, bool) or not isinstance(input_tokens, int):
        raise TypeError("input_tokens must be an int")
    if input_tokens < 0:
        raise ValueError("input_tokens must be >= 0")
    amount = (Decimal(input_tokens) * DECISIONS_INPUT_USD_PER_MILLION) / Decimal(1_000_000)
    return f"{amount:.8f}"
