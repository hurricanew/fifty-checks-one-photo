"""Replay, one live Decisions call, and the optional uncalibrated fallback."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable

from app.checks import Check
from app.config import Settings
from app.cost import decisions_cost_usd
from app.decisions import (
    FALLBACK_SCHEMA,
    ParsedUsage,
    build_decision_request,
    build_fallback_prompt,
    parse_decision_response,
    parse_fallback_payload,
    parse_fallback_usage,
)
from app.labels import FALLBACK_LABEL, SAMPLE_LABEL
from app.tiles import classify

MISSING_KEY_MESSAGE = (
    "GEMINI_API_KEY is not set. Live mode did not call the API. "
    "Set GEMINI_API_KEY to use the Decisions API, or switch to replay mode. "
    "This app will not invent latency, tokens, or cost."
)


class MissingApiKey(Exception):
    def __init__(self) -> None:
        super().__init__(MISSING_KEY_MESSAGE)


class CallFailed(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def api_key_from_env() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def build_client(api_key: str):
    """Construct the SDK client. Imported lazily so replay tests stay offline."""
    from google import genai

    return genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})


def evaluate(
    *,
    mode: str,
    image: bytes,
    mime_type: str,
    checks: list[Check],
    settings: Settings,
    sample_answers: dict,
    fallback_requested: bool,
    client_factory: Callable | None = None,
) -> dict:
    if mode == "replay":
        return _replay(image, mime_type, checks, settings, sample_answers)
    if mode != "live":
        raise CallFailed("mode must be replay or live.", status_code=400)
    if not api_key_from_env():
        raise MissingApiKey()
    factory = client_factory or build_client
    client = factory(api_key_from_env())
    request = build_decision_request(checks, image, mime_type, settings)
    try:
        result, latency_ms = timed_decisions_call(client, request)
    except Exception as exc:
        if _fallback_allowed(exc, settings, fallback_requested):
            try:
                return _fallback(client, checks, image, mime_type, settings, exc)
            except CallFailed:
                raise
            except Exception as fallback_exc:
                raise CallFailed(
                    "The Decisions API is unavailable "
                    f"({public_error(exc)}). The fallback also failed "
                    f"({public_error(fallback_exc)}). No result was invented."
                ) from fallback_exc
        raise CallFailed(_live_error_message(exc)) from exc
    answers, usage = parse_decision_response(result)
    return _payload(
        source="decisions",
        image=image,
        mime_type=mime_type,
        checks=checks,
        settings=settings,
        answers=answers,
        usage=usage,
        latency_ms=latency_ms,
        sample_label=None,
        fallback_label=None,
        sent_to_model=True,
        timing_note="time.perf_counter() around client.decisions.create only.",
        usage_note=_decisions_usage_note(usage),
        decisions_error=None,
    )


def _replay(image, mime_type, checks, settings, sample_answers) -> dict:
    missing = [check.key for check in checks if check.key not in sample_answers]
    if missing:
        raise CallFailed("The sample file is missing answers for: " + ", ".join(missing[:5]))
    return _payload(
        source="replay",
        image=image,
        mime_type=mime_type,
        checks=checks,
        settings=settings,
        answers=sample_answers,
        usage=ParsedUsage(None, None),
        latency_ms=None,
        sample_label=SAMPLE_LABEL,
        fallback_label=None,
        sent_to_model=False,
        timing_note="Not measured. Replay does not call the API.",
        usage_note=(
            "Not measured. The sample file has no token counts, and this app will not invent them."
        ),
        decisions_error=None,
    )


def _fallback(client, checks, image, mime_type, settings, decisions_exc) -> dict:
    from google.genai import types

    prompt = build_fallback_prompt(checks)
    start = time.perf_counter()
    response = client.models.generate_content(
        model=settings.fallback_model,
        contents=[
            prompt,
            types.Part.from_bytes(data=image, mime_type=mime_type),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=FALLBACK_SCHEMA,
            temperature=0,
        ),
    )
    latency_ms = (time.perf_counter() - start) * 1000.0
    text = getattr(response, "text", None)
    if not text:
        raise CallFailed("The fallback model returned no JSON text.")
    try:
        payload = json.loads(text)
        answers = parse_fallback_payload(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CallFailed(f"The fallback model returned JSON this app could not read: {exc}") from exc
    usage = parse_fallback_usage(response)
    note = (
        "Token counts are from the fallback response usage, when it included them. "
        "Output tokens are billed. The Decisions formula "
        "(input_tokens × 0.10 / 1,000,000, output not billed) was not applied, so no US$ cost is shown."
    )
    if usage.input_tokens is None:
        note = (
            "The fallback response did not include input token usage, so tokens and cost were not invented. "
            "Output tokens on this path are billed. Confidence is not calibrated."
        )
    return _payload(
        source="fallback",
        image=image,
        mime_type=mime_type,
        checks=checks,
        settings=settings,
        answers=answers,
        usage=usage,
        latency_ms=latency_ms,
        sample_label=None,
        fallback_label=FALLBACK_LABEL,
        sent_to_model=True,
        timing_note=(
            "time.perf_counter() around the fallback generate_content call only. "
            "The failed Decisions call was not given a latency."
        ),
        usage_note=note,
        decisions_error=(
            "Decisions API unavailable (" + public_error(decisions_exc) + "). "
            "Fell back to " + settings.fallback_model + "."
        ),
    )


def timed_decisions_call(client, request: dict):
    start = time.perf_counter()
    result = client.decisions.create(
        model=request["model"],
        input=request["input"],
        questions=request["questions"],
    )
    latency_ms = (time.perf_counter() - start) * 1000.0
    return result, latency_ms


def _payload(
    *,
    source,
    image,
    mime_type,
    checks,
    settings,
    answers,
    usage: ParsedUsage,
    latency_ms,
    sample_label,
    fallback_label,
    sent_to_model,
    timing_note,
    usage_note,
    decisions_error,
) -> dict:
    tiles = []
    counts = {"green": 0, "red": 0, "amber": 0}
    for check in checks:
        answer = answers.get(check.key)
        p_yes = None if answer is None else answer.p_yes
        confidence = None if answer is None else answer.confidence
        status, reason = classify(p_yes, confidence, check.yes_is_good, settings.thresholds)
        counts[status] += 1
        tiles.append(
            {
                "key": check.key,
                "label": check.label,
                "category": check.category,
                "instructions": check.instructions,
                "yes_is_good": check.yes_is_good,
                "p_yes": p_yes,
                "confidence": confidence,
                "status": status,
                "reason": reason,
            }
        )
    cost = None
    usage_measured = False
    if source == "decisions" and usage.input_tokens is not None:
        usage_measured = True
        cost = decisions_cost_usd(usage.input_tokens)
    elif source == "fallback" and (
        usage.input_tokens is not None or usage.output_tokens is not None
    ):
        usage_measured = True
    return {
        "source": source,
        "model": settings.fallback_model if source == "fallback" else settings.model,
        "sample_label": sample_label,
        "fallback_label": fallback_label,
        "decisions_error": decisions_error,
        "photo": {
            "mime_type": mime_type,
            "byte_size": len(image),
            "sent_to_model": sent_to_model,
        },
        "thresholds": {
            "confidence_min": settings.thresholds.confidence_min,
            "uncertain_low": settings.thresholds.uncertain_low,
            "uncertain_high": settings.thresholds.uncertain_high,
        },
        "timing": {
            "measured": latency_ms is not None,
            "latency_ms": latency_ms,
            "note": timing_note,
        },
        "usage": {
            "measured": usage_measured,
            "input_tokens": usage.input_tokens if usage_measured else None,
            "output_tokens": usage.output_tokens if usage_measured else None,
            "cost_usd": cost,
            "note": usage_note,
        },
        "summary": counts,
        "tiles": tiles,
    }


def _decisions_usage_note(usage: ParsedUsage) -> str:
    if usage.input_tokens is None:
        return (
            "The Decisions response did not include total_input_tokens, "
            "so tokens and cost were not invented."
        )
    output = "not included in the response" if usage.output_tokens is None else str(usage.output_tokens)
    return (
        "Cost = input_tokens × 0.10 / 1,000,000. Output tokens are not billed. "
        f"total_output_tokens: {output}."
    )


def _fallback_allowed(exc: BaseException, settings: Settings, requested: bool) -> bool:
    return bool(settings.fallback_enabled and requested and decisions_unavailable(exc))


def decisions_unavailable(exc: BaseException) -> bool:
    """True only when the Decisions API itself looks unavailable.

    Auth failures, quota, and safety blocks are not treated as unavailable,
    so the app does not hide them behind the uncalibrated fallback.
    """
    code = getattr(exc, "code", None)
    try:
        code_int = int(code) if code is not None and not isinstance(code, bool) else None
    except (TypeError, ValueError):
        code_int = None
    status = str(getattr(exc, "status", "") or "").upper()
    message = " ".join(str(getattr(exc, "message", None) or exc).lower().split())
    if code_int in {404, 410, 501}:
        return True
    if status in {"NOT_FOUND", "UNIMPLEMENTED"}:
        return True
    if "not found" in message and ("decision" in message or "model" in message):
        return True
    if "not implemented" in message or "unimplemented" in message:
        return True
    if code_int == 503 and "decision" in message:
        return True
    return False


def _live_error_message(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    prefix = "The Decisions API call failed"
    if code in {401, 403}:
        prefix = "The Decisions API rejected the credentials"
    return (
        f"{prefix} ({public_error(exc)}). "
        "Fallback was not used. No latency, tokens, or cost were recorded for this failure."
    )


def public_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    key = api_key_from_env()
    if key:
        text = text.replace(key, "[redacted]")
    if len(text) > 400:
        text = text[:400] + "…"
    return text or exc.__class__.__name__
