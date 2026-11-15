import base64
import json
from pathlib import Path

from app.checks import load_checks
from app.config import ROOT, load_settings
from app.cost import decisions_cost_usd
from app.decisions import (
    build_decision_request,
    build_fallback_prompt,
    parse_decision_response,
    parse_fallback_payload,
    parse_fallback_usage,
)
from app.labels import SAMPLE_LABEL

FIXTURE = Path(__file__).parent / "fixtures" / "fake_decision_response.json"


def test_request_uses_one_base64_image_and_fifty_noul_questions():
    settings = load_settings()
    checks = load_checks(settings.checks_path)
    image = b"\xff\xd8\xff\xe0rack"
    request = build_decision_request(checks, image, "image/jpeg", settings)
    assert request["model"] == "gemini-decision-1"
    assert request["input"][0]["type"] == "text"
    image_part = request["input"][1]
    assert image_part["type"] == "image"
    assert image_part["mime_type"] == "image/jpeg"
    assert image_part["resolution"] == settings.image_resolution
    assert base64.b64decode(image_part["data"]) == image
    assert len(request["questions"]) == 50
    for check in checks:
        question = request["questions"][check.key]
        assert question["type"] == "noul"
        assert question["instructions"] == check.instructions
        assert question["criteria"]["true_description"] == check.true_description
        assert question["criteria"]["false_description"] == check.false_description
        assert "yes_is_good" not in question


def test_parse_fake_response_file_reads_noul_and_input_tokens():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert raw["label"] == SAMPLE_LABEL
    answers, usage = parse_decision_response(raw)
    assert answers["even_light"].p_yes == 0.91
    assert answers["even_light"].confidence == 0.9
    assert answers["red_eye"].p_yes == 0.22
    assert usage.input_tokens == 1500
    assert usage.output_tokens == 0
    assert decisions_cost_usd(usage.input_tokens) == "0.00015000"


def test_parse_attribute_objects_the_sdk_would_return():
    class Answer:
        def __init__(self, noul, confidence):
            self.noul = noul
            self.confidence = confidence
            self.type = "noul"

    class Usage:
        total_input_tokens = 2500
        total_output_tokens = 0

    class Result:
        answers = {"even_light": Answer(0.77, 0.81)}
        usage = Usage()

    answers, usage = parse_decision_response(Result())
    assert answers["even_light"].p_yes == 0.77
    assert usage.input_tokens == 2500
    assert decisions_cost_usd(2500) == "0.00025000"


def test_cost_formula():
    assert decisions_cost_usd(0) == "0.00000000"
    assert decisions_cost_usd(1) == "0.00000010"
    assert decisions_cost_usd(1_000_000) == "0.10000000"


def test_fallback_prompt_names_every_check_and_parser_accepts_a_list_or_a_map():
    checks = load_checks(ROOT / "data" / "checks.json")
    prompt = build_fallback_prompt(checks)
    assert "not a calibrated probability" in prompt
    for check in checks:
        assert check.key in prompt
        assert check.instructions in prompt
    listed = {
        "answers": [
            {"key": checks[0].key, "noul": 0.2, "confidence": 0.3},
        ]
    }
    mapped = {"answers": {checks[0].key: {"noul": 0.2, "confidence": 0.3}}}
    assert parse_fallback_payload(listed)[checks[0].key].p_yes == 0.2
    assert parse_fallback_payload(mapped)[checks[0].key].confidence == 0.3

    class Meta:
        prompt_token_count = 80
        candidates_token_count = 40
        thoughts_token_count = 0

    class Response:
        usage_metadata = Meta()

    usage = parse_fallback_usage(Response())
    assert usage.input_tokens == 80
    assert usage.output_tokens == 40
