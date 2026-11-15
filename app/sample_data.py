"""Load the replay file.

The shipped file is a labelled sample with no measurements. A file written by
scripts/refresh_sample.py is a recorded live call and may carry the latency
and token counts that script measured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.cost import decisions_cost_usd
from app.decisions import AnswerValue, parse_decision_response
from app.labels import RECORDED_LABEL, SAMPLE_LABEL

FORBIDDEN_MEASUREMENT_KEYS = ("latency_ms", "cost_usd", "total_input_tokens", "input_tokens")


@dataclass(frozen=True)
class ReplaySource:
    answers: dict[str, AnswerValue]
    kind: str
    label: str = SAMPLE_LABEL
    photo_name: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: str | None = None


def load_sample(path: Path) -> ReplaySource:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("sample response must be a JSON object")
    label = raw.get("label")
    if label == SAMPLE_LABEL:
        return _load_labelled_sample(raw)
    if label == RECORDED_LABEL and raw.get("recorded") is True:
        return _load_recorded(raw)
    raise ValueError(
        f"sample response label must be {SAMPLE_LABEL!r} or {RECORDED_LABEL!r}"
    )


def write_recorded_sample(
    path: Path,
    *,
    answers: dict,
    latency_ms: float,
    input_tokens: int,
    output_tokens: int | None,
    photo_name: str,
    model: str,
    image_resolution: str,
) -> None:
    """Write a real refresh result. The caller must have measured every figure."""
    cost = decisions_cost_usd(input_tokens)
    document = {
        "label": RECORDED_LABEL,
        "recorded": True,
        "note": (
            "Written by scripts/refresh_sample.py from one live Decisions API call. "
            "Latency is time.perf_counter() around that call only. "
            "Cost is input_tokens * 0.10 / 1_000_000. The photo bytes are not stored here."
        ),
        "model": model,
        "image_resolution": image_resolution,
        "photo_name": photo_name,
        "latency_ms": latency_ms,
        "cost_usd": cost,
        "usage": {
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
        },
        "answers": answers,
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _load_labelled_sample(raw: dict) -> ReplaySource:
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
    return ReplaySource(answers=answers, kind="sample")


def _load_recorded(raw: dict) -> ReplaySource:
    answers, usage = parse_decision_response(raw)
    if not answers:
        raise ValueError("recorded response has no answers")
    if usage.input_tokens is None:
        raise ValueError("recorded response is missing total_input_tokens")
    latency = raw.get("latency_ms")
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        raise ValueError("recorded response is missing latency_ms")
    cost = decisions_cost_usd(usage.input_tokens)
    stated = raw.get("cost_usd")
    if stated is not None and stated != cost:
        raise ValueError("recorded cost_usd does not match input_tokens * 0.10 / 1_000_000")
    photo_name = raw.get("photo_name")
    if not isinstance(photo_name, str) or not photo_name.strip():
        photo_name = None
    return ReplaySource(
        answers=answers,
        kind="recorded",
        label=RECORDED_LABEL,
        photo_name=photo_name,
        latency_ms=float(latency),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=cost,
    )


def replay_hint(source: ReplaySource) -> str:
    """Short description of the saved replay file. Figures come only from that file."""
    if source.kind == "recorded":
        if source.photo_name:
            opening = f"Replay shows a saved result from one real call on {source.photo_name}."
        else:
            opening = "Replay shows a saved result from one real call."
        measured = ""
        if source.latency_ms is not None and source.input_tokens is not None and source.cost_usd:
            measured = (
                f" That call measured {source.latency_ms:.1f} ms, "
                f"{source.input_tokens} input tokens, US${source.cost_usd}."
            )
        return opening + " It does not call the API or score your photo." + measured
    return (
        "Replay loads a fixed file labelled SAMPLE - not a real API result. "
        "It does not call the API, it does not score your photo, and it has no measured latency, tokens, or cost. "
        "Run scripts/refresh_sample.py later if you want that file to come from a real call."
    )
