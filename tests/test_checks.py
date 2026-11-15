import json
import re

import pytest

from app.checks import load_checks, validate_checks
from app.config import ROOT, load_settings
from app.cost import decisions_cost_usd
from app.labels import RECORDED_LABEL, SAMPLE_LABEL

CHECKS_PATH = ROOT / "data" / "checks.json"
SAMPLE_PATH = ROOT / "data" / "sample_response.json"


def test_shipped_checklist_has_fifty_unique_complete_checks():
    checks = load_checks(CHECKS_PATH)
    assert len(checks) == 50
    assert len({check.key for check in checks}) == 50
    assert all(check.instructions.endswith("?") for check in checks)
    assert any(check.yes_is_good for check in checks)
    assert any(not check.yes_is_good for check in checks)


def test_rejects_the_wrong_count_and_duplicate_keys_and_missing_fields():
    raw = json.loads(CHECKS_PATH.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="exactly 50"):
        validate_checks(raw[:-1])
    extra = json.loads(json.dumps(raw[0]))
    extra["key"] = "extra_check_key"
    with pytest.raises(ValueError, match="exactly 50"):
        validate_checks(raw + [extra])
    duplicated = json.loads(json.dumps(raw))
    duplicated[1]["key"] = duplicated[0]["key"]
    with pytest.raises(ValueError, match="duplicate"):
        validate_checks(duplicated)
    missing = json.loads(json.dumps(raw))
    del missing[0]["yes_is_good"]
    with pytest.raises(ValueError, match="yes_is_good"):
        validate_checks(missing)
    bad_flag = json.loads(json.dumps(raw))
    bad_flag[0]["yes_is_good"] = "true"
    with pytest.raises(ValueError, match="boolean"):
        validate_checks(bad_flag)


def test_rejects_a_negated_question_and_a_bad_key():
    raw = json.loads(CHECKS_PATH.read_text(encoding="utf-8"))
    negated = json.loads(json.dumps(raw))
    negated[0]["instructions"] = "Is there no blanking panel?"
    with pytest.raises(ValueError, match="positively"):
        validate_checks(negated)
    bad_key = json.loads(json.dumps(raw))
    bad_key[0]["key"] = "has a space"
    with pytest.raises(ValueError, match="key"):
        validate_checks(bad_key)


def test_sample_file_is_a_placeholder_or_a_recorded_call():
    raw = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    checks = load_checks(CHECKS_PATH)
    assert set(raw["answers"]) == {check.key for check in checks}
    for answer in raw["answers"].values():
        assert 0.01 <= answer["noul"] <= 0.99
        assert 0.0 <= answer["confidence"] <= 1.0
    if raw["label"] == SAMPLE_LABEL:
        assert raw.get("recorded") is not True
        assert "latency_ms" not in raw
        assert "cost_usd" not in raw
        assert "usage" not in raw
    else:
        assert raw["label"] == RECORDED_LABEL
        assert raw["recorded"] is True
        tokens = raw["usage"]["total_input_tokens"]
        assert isinstance(tokens, int) and tokens >= 0
        assert isinstance(raw["latency_ms"], (int, float)) and not isinstance(raw["latency_ms"], bool)
        assert raw["latency_ms"] >= 0
        assert raw["cost_usd"] == decisions_cost_usd(tokens)
        output_tokens = raw["usage"]["total_output_tokens"]
        assert output_tokens is None or isinstance(output_tokens, int)


FORBIDDEN_PERSON_TERMS = re.compile(
    r"\b(attractive|beautiful|ugly|weight|fat|thin|skin|age|old|young|gender|race|ethnic|body)\b",
    re.IGNORECASE,
)


def test_recorded_refresh_file_keeps_measured_figures_and_no_photo_bytes(tmp_path):
    from app.sample_data import load_sample, write_recorded_sample

    photo_name = "portrait-wavy-hair.jpg"
    answers = {
        "even_light": {"type": "noul", "noul": 0.8, "confidence": 0.7},
    }
    path = tmp_path / "sample_response.json"
    write_recorded_sample(
        path,
        answers=answers,
        latency_ms=123.4,
        input_tokens=1000,
        output_tokens=0,
        photo_name=photo_name,
        model="gemini-decision-1",
        image_resolution="medium",
    )
    raw = path.read_text(encoding="utf-8")
    assert "UNIQUE" not in raw
    assert photo_name in raw
    loaded = load_sample(path)
    assert loaded.kind == "recorded"
    assert loaded.latency_ms == 123.4
    assert loaded.input_tokens == 1000
    assert loaded.cost_usd == "0.00010000"
    assert loaded.answers["even_light"].p_yes == 0.8


def test_checks_judge_the_photo_not_the_person():
    raw = json.loads(CHECKS_PATH.read_text(encoding="utf-8"))
    fields = ("key", "label", "category", "instructions", "true_description", "false_description")
    for check in raw:
        for field in fields:
            match = FORBIDDEN_PERSON_TERMS.search(str(check[field]))
            assert match is None, f"{check['key']}.{field} contains {match.group()!r}"


def test_shipped_cutoffs_are_at_most_0_99_and_the_price_is_the_documented_rate():
    settings = load_settings()
    assert settings.model == "gemini-decision-1"
    assert settings.api_version == "v1beta"
    assert settings.input_usd_per_million == 0.1
    assert settings.thresholds.confidence_min <= 0.99
    assert settings.thresholds.uncertain_low <= 0.99
    assert settings.thresholds.uncertain_high <= 0.99
    assert settings.image_resolution in {"low", "medium", "high", "ultra_high"}
