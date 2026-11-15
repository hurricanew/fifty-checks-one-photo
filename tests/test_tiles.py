import pytest

from app.config import Thresholds, validate_thresholds
from app.tiles import AMBER, GREEN, RED, classify

CUTOFFS = Thresholds(confidence_min=0.70, uncertain_low=0.40, uncertain_high=0.60)


def test_decisive_yes_is_green_when_yes_is_good():
    assert classify(0.93, 0.91, True, CUTOFFS) == (GREEN, "Looks good")


def test_decisive_yes_is_red_when_yes_is_a_problem():
    assert classify(0.93, 0.91, False, CUTOFFS) == (RED, "Looks bad")


def test_decisive_no_is_red_when_yes_is_good():
    assert classify(0.08, 0.87, True, CUTOFFS) == (RED, "Looks bad")


def test_decisive_no_is_green_when_yes_is_a_problem():
    assert classify(0.08, 0.87, False, CUTOFFS) == (GREEN, "Looks good")


def test_low_confidence_is_amber_even_when_p_yes_is_extreme():
    status, reason = classify(0.96, 0.42, True, CUTOFFS)
    assert status == AMBER
    assert reason == "Confidence is below the cut-off"


def test_uncertain_band_is_amber_even_when_confidence_is_high():
    status, reason = classify(0.50, 0.95, True, CUTOFFS)
    assert status == AMBER
    assert reason == "P(yes) is in the uncertain band"


def test_band_edges_are_inclusive():
    assert classify(0.40, 0.80, True, CUTOFFS)[0] == AMBER
    assert classify(0.60, 0.90, True, CUTOFFS)[0] == AMBER


def test_just_outside_the_band_is_decisive():
    assert classify(0.39, 0.84, True, CUTOFFS) == (RED, "Looks bad")
    assert classify(0.61, 0.77, True, CUTOFFS) == (GREEN, "Looks good")


def test_confidence_equal_to_the_cutoff_is_not_amber():
    assert classify(0.70, 0.70, True, CUTOFFS) == (GREEN, "Looks good")


def test_confidence_just_below_the_cutoff_is_amber():
    assert classify(0.93, 0.699, True, CUTOFFS)[0] == AMBER


def test_api_ceiling_can_still_be_a_decisive_yes():
    assert classify(0.99, 0.90, True, CUTOFFS) == (GREEN, "Looks good")


def test_p_yes_of_0_99_is_amber_when_the_band_ends_at_0_99():
    ceilings = Thresholds(confidence_min=0.70, uncertain_low=0.40, uncertain_high=0.99)
    status, reason = classify(0.99, 0.90, True, ceilings)
    assert status == AMBER
    assert "uncertain" in reason


def test_values_outside_the_api_range_are_amber():
    assert classify(1.0, 0.9, True, CUTOFFS)[0] == AMBER
    assert classify(0.009, 0.9, True, CUTOFFS)[0] == AMBER
    assert classify(0.5, 1.01, True, CUTOFFS)[0] == AMBER
    assert classify(0.5, -0.01, True, CUTOFFS)[0] == AMBER
    assert classify(True, 0.9, True, CUTOFFS)[0] == AMBER
    assert classify(0.9, True, True, CUTOFFS)[0] == AMBER
    assert classify(None, 0.9, True, CUTOFFS)[1] == "No answer returned"
    assert classify(float("nan"), 0.9, True, CUTOFFS)[0] == AMBER


def test_both_low_confidence_and_uncertain_band():
    reason = classify(0.50, 0.2, True, CUTOFFS)[1]
    assert reason == "Low confidence, and P(yes) is in the uncertain band"


def test_cutoff_above_0_99_is_rejected():
    with pytest.raises(ValueError, match="0.99"):
        validate_thresholds(Thresholds(1.0, 0.40, 0.60))
    with pytest.raises(ValueError, match="0.99"):
        validate_thresholds(Thresholds(0.70, 0.40, 0.991))


def test_cutoff_of_0_99_is_allowed():
    validate_thresholds(Thresholds(0.99, 0.40, 0.99))
