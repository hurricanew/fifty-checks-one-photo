"""Turn one yes/no answer into a green, red, or amber tile."""

from __future__ import annotations

from app.config import Thresholds

GREEN = "green"
RED = "red"
AMBER = "amber"


def classify(
    p_yes: float | None,
    confidence: float | None,
    yes_is_good: bool,
    thresholds: Thresholds,
) -> tuple[str, str]:
    """Return (status, reason).

    Amber when confidence is below the cut-off, or when P(yes) sits inside the
    uncertain band (inclusive). Otherwise green or red from P(yes) and yes_is_good.
    Values outside the Decisions API ranges stay amber so a fallback or a bad
    payload cannot paint a hard color.
    """
    if p_yes is None or confidence is None:
        return AMBER, "No answer returned"
    if isinstance(p_yes, bool) or isinstance(confidence, bool):
        return AMBER, "Value is outside the Decisions API range"
    if not isinstance(p_yes, (int, float)) or not isinstance(confidence, (int, float)):
        return AMBER, "Value is outside the Decisions API range"
    p_value = float(p_yes)
    c_value = float(confidence)
    if (
        p_value != p_value
        or c_value != c_value
        or not (0.01 <= p_value <= 0.99)
        or not (0.0 <= c_value <= 1.0)
    ):
        return AMBER, "Value is outside the Decisions API range"

    low_confidence = c_value < thresholds.confidence_min
    uncertain = thresholds.uncertain_low <= p_value <= thresholds.uncertain_high
    if low_confidence and uncertain:
        return AMBER, "Low confidence, and P(yes) is in the uncertain band"
    if low_confidence:
        return AMBER, "Confidence is below the cut-off"
    if uncertain:
        return AMBER, "P(yes) is in the uncertain band"

    says_yes = p_value > thresholds.uncertain_high
    looks_good = says_yes if yes_is_good else not says_yes
    if looks_good:
        return GREEN, "Looks good"
    return RED, "Looks bad"
