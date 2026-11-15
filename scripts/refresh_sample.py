#!/usr/bin/env python3
"""Replace data/sample_response.json with one real Decisions API call.

Refuses to start when GEMINI_API_KEY is missing. It does not invent latency,
token counts, or cost. The photo bytes are not written into the output file.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.checks import load_checks
from app.config import load_settings
from app.decisions import build_decision_request, parse_decision_response
from app.sample_data import write_recorded_sample
from app.service import api_key_from_env, build_client, public_error, timed_decisions_call
from app.uploads import matches_mime, validate_image

REFUSAL = (
    "Refusing to run: GEMINI_API_KEY is not set. "
    "refresh_sample.py will not fabricate a model result, latency, tokens, or cost."
)
RESOLUTIONS = ("medium", "high")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photo", type=Path, help="One photo of a person or a couple")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "sample_response.json",
        help="Where to write the recorded response",
    )
    parser.add_argument("--resolution", choices=RESOLUTIONS, default=None)
    args = parser.parse_args(argv)
    if not api_key_from_env():
        print(REFUSAL, file=sys.stderr)
        return 2
    if not args.photo.is_file():
        print(f"Refusing to run: {args.photo} is not a file.", file=sys.stderr)
        return 2

    settings = load_settings()
    if args.resolution is not None:
        settings = replace(settings, image_resolution=args.resolution)
    checks = load_checks(settings.checks_path)
    data = args.photo.read_bytes()
    mime = None
    for candidate in settings.allowed_mime_types:
        if matches_mime(data, candidate):
            mime = candidate
            break
    if mime is None:
        print(f"Refusing to run: {args.photo.name} is not a recognized image.", file=sys.stderr)
        return 2
    validate_image(data, mime, settings.allowed_mime_types, settings.max_upload_bytes)

    client = build_client(api_key_from_env())
    request = build_decision_request(checks, data, mime, settings)
    try:
        result, latency_ms = timed_decisions_call(client, request)
        parsed, usage = parse_decision_response(result)
    except Exception as exc:
        print(f"Call failed: {public_error(exc)}", file=sys.stderr)
        print("The output file was not changed.", file=sys.stderr)
        return 1
    if usage.input_tokens is None:
        print("The response omitted total_input_tokens. The output file was not changed.", file=sys.stderr)
        return 1
    missing = [check.key for check in checks if check.key not in parsed]
    if missing:
        print("The response missed check keys. The output file was not changed.", file=sys.stderr)
        return 1

    answers = {
        key: {"type": "noul", "noul": value.p_yes, "confidence": value.confidence}
        for key, value in parsed.items()
    }
    write_recorded_sample(
        args.output,
        answers=answers,
        latency_ms=latency_ms,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        photo_name=args.photo.name,
        model=settings.model,
        image_resolution=settings.image_resolution,
    )
    print(
        f"Recorded 1 live call on {args.photo.name} at resolution {settings.image_resolution}. "
        f"Latency {latency_ms:.1f} ms, input tokens {usage.input_tokens}, "
        f"output tokens {usage.output_tokens}."
    )
    print(f"Wrote {args.output}")
    print("Photo bytes were not written into that file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
