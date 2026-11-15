"""Build one Decisions API request and read a response.

Image parts follow the Decisions API content blocks: base64 `data`,
`mime_type`, and `resolution` (`low`, `medium`, `high`, or `ultra_high`).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from app.checks import Check
from app.config import Settings

INPUT_TEXT = (
    "Phone photo of a server rack. Judge each question only from what is visible in this photo."
)

FALLBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "noul": {"type": "number"},
                    "confidence": {"type": "number"},
                },
                "required": ["key", "noul", "confidence"],
            },
        }
    },
    "required": ["answers"],
}


@dataclass(frozen=True)
class AnswerValue:
    p_yes: float | None
    confidence: float | None


@dataclass(frozen=True)
class ParsedUsage:
    input_tokens: int | None
    output_tokens: int | None


def build_decision_request(checks: list[Check], image: bytes, mime_type: str, settings: Settings) -> dict:
    encoded = base64.b64encode(image).decode("ascii")
    return {
        "model": settings.model,
        "input": [
            {"type": "text", "text": INPUT_TEXT},
            {
                "type": "image",
                "data": encoded,
                "mime_type": mime_type,
                "resolution": settings.image_resolution,
            },
        ],
        "questions": {
            check.key: {
                "type": "noul",
                "instructions": check.instructions,
                "criteria": {
                    "true_description": check.true_description,
                    "false_description": check.false_description,
                },
            }
            for check in checks
        },
    }


def build_fallback_prompt(checks: list[Check]) -> str:
    lines = [
        "You are scoring one phone photo of a server rack.",
        "For every key, return noul as your probability that the answer is yes, from 0.01 to 0.99,",
        "and confidence as your own uncalibrated self-rating from 0 to 1.",
        "This confidence is not a calibrated probability.",
        "Judge only what is visible. If the feature is out of frame, keep noul near 0.5 and confidence low.",
        "",
    ]
    for check in checks:
        lines.append(
            "\n".join(
                [
                    f"key: {check.key}",
                    f"Question: {check.instructions}",
                    f"Yes means: {check.true_description}",
                    f"No means: {check.false_description}",
                ]
            )
        )
        lines.append("")
    return "\n".join(lines)


def parse_decision_response(result: object) -> tuple[dict[str, AnswerValue], ParsedUsage]:
    answers_raw = _get(result, "answers") or {}
    if not isinstance(answers_raw, dict):
        raise ValueError("Decisions response answers must be an object keyed by check")
    answers: dict[str, AnswerValue] = {}
    for key, answer in answers_raw.items():
        answers[str(key)] = AnswerValue(
            p_yes=_number(_get(answer, "noul")),
            confidence=_number(_get(answer, "confidence")),
        )
    usage = _get(result, "usage")
    if usage is None:
        return answers, ParsedUsage(None, None)
    return answers, ParsedUsage(
        input_tokens=_int(_get(usage, "total_input_tokens")),
        output_tokens=_int(_get(usage, "total_output_tokens")),
    )


def parse_fallback_payload(payload: object) -> dict[str, AnswerValue]:
    if not isinstance(payload, dict):
        raise ValueError("Fallback response was not a JSON object")
    raw = payload.get("answers")
    answers: dict[str, AnswerValue] = {}
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = []
        for item in raw:
            if not isinstance(item, dict) or "key" not in item:
                raise ValueError("Fallback answer is missing key")
            items.append((item["key"], item))
    else:
        raise ValueError("Fallback response is missing answers")
    for key, item in items:
        answers[str(key)] = AnswerValue(
            p_yes=_number(_get(item, "noul")),
            confidence=_number(_get(item, "confidence")),
        )
    return answers


def parse_fallback_usage(response: object) -> ParsedUsage:
    meta = _get(response, "usage_metadata")
    if meta is None:
        return ParsedUsage(None, None)
    prompt = _int(_get(meta, "prompt_token_count"))
    candidates = _int(_get(meta, "candidates_token_count"))
    thoughts = _int(_get(meta, "thoughts_token_count"))
    output = None
    if candidates is not None or thoughts is not None:
        output = (candidates or 0) + (thoughts or 0)
    return ParsedUsage(prompt, output)


def _get(obj: object, key: str):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None
