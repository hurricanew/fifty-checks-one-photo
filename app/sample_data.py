"""Load the replay fixture. It is not a measured API response."""

from __future__ import annotations

import json
from pathlib import Path

from app.decisions import AnswerValue, parse_decision_response
from app.labels import SAMPLE_LABEL

FORBIDDEN_MEASUREMENT_KEYS = ("latency_ms", "cost_usd", "total_input_tokens", "input_tokens")


def load_sample(path: Path) -> dict[str, AnswerValue]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("sample response must be a JSON object")
    if raw.get("label") != SAMPLE_LABEL:
        raise ValueError(f"sample response must be labelled {SAMPLE_LABEL!r}")
    present = [key for key in FORBIDDEN_MEASUREMENT_KEYS if key in raw]
    if present:
        raise ValueError(
            "sample response must not carry latency, token, or cost fields "
            f"(found {', '.join(present)}). Those figures were not measured."
        )
    answers, usage = parse_decision_response(raw)
    if usage.input_tokens is not None or usage.output_tokens is not None:
        raise ValueError("sample response must not include usage counts")
    if not answers:
        raise ValueError("sample response has no answers")
    return answers
