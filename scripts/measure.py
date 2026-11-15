#!/usr/bin/env python3
"""Run N live Decisions API calls and write measured latency, tokens, and cost.

Refuses to start when GEMINI_API_KEY is missing. It does not fabricate figures.
Latency is time.perf_counter() around client.decisions.create only.
Cost is input_tokens * 0.10 / 1_000_000. Output tokens are not billed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.checks import load_checks
from app.config import load_settings
from app.cost import decisions_cost_usd
from app.decisions import build_decision_request, parse_decision_response
from app.service import api_key_from_env, build_client, public_error, timed_decisions_call
from app.uploads import matches_mime, validate_image

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MEASURE_RESOLUTIONS = ("medium", "high")
REFUSAL = (
    "Refusing to run: GEMINI_API_KEY is not set. "
    "measure.py will not fabricate latency, token counts, or cost."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photos", type=Path, required=True, help="Folder of rack photos")
    parser.add_argument("--calls", type=int, required=True, help="Number of live API calls to make")
    parser.add_argument(
        "--resolution",
        choices=MEASURE_RESOLUTIONS,
        default=None,
        help="Image resolution sent to the Decisions API: medium or high. "
        "Default is image_resolution in data/config.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence" / "measurements.json",
        help="JSON path written only after real calls",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=ROOT / "evidence" / "measurements.md",
        help="Markdown summary written only after real calls",
    )
    args = parser.parse_args(argv)
    if args.calls < 1:
        print("Refusing to run: --calls must be at least 1.", file=sys.stderr)
        return 2
    if not api_key_from_env():
        print(REFUSAL, file=sys.stderr)
        return 2

    settings = load_settings()
    if args.resolution is not None:
        settings = replace(settings, image_resolution=args.resolution)
    checks = load_checks(settings.checks_path)
    photos = list_photos(args.photos)
    if not photos:
        print(f"Refusing to run: no images in {args.photos}.", file=sys.stderr)
        return 2

    usable: list[tuple[Path, bytes, str]] = []
    for path in photos:
        data = path.read_bytes()
        mime = sniff_mime(data, settings.allowed_mime_types)
        if mime is None:
            print(f"Skipping {path.name}: not a recognized image.", file=sys.stderr)
            continue
        try:
            validate_image(data, mime, settings.allowed_mime_types, settings.max_upload_bytes)
        except Exception as exc:
            print(f"Skipping {path.name}: {exc}", file=sys.stderr)
            continue
        usable.append((path, data, mime))
    if not usable:
        print("Refusing to run: no usable photos.", file=sys.stderr)
        return 2

    client = build_client(api_key_from_env())
    records = []
    for index in range(args.calls):
        path, data, mime = usable[index % len(usable)]
        request = build_decision_request(checks, data, mime, settings)
        try:
            result, latency_ms = timed_decisions_call(client, request)
            _answers, usage = parse_decision_response(result)
        except Exception as exc:
            records.append({"photo": path.name, "ok": False, "error": public_error(exc)})
            print(f"{path.name}: call failed: {public_error(exc)}", file=sys.stderr)
            continue
        record = {
            "photo": path.name,
            "ok": True,
            "latency_ms": latency_ms,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": None if usage.input_tokens is None else decisions_cost_usd(usage.input_tokens),
        }
        records.append(record)
        print(
            f"{path.name}: latency_ms={latency_ms:.1f} input_tokens={usage.input_tokens} cost_usd={record['cost_usd']}"
        )

    summary = summarize_latency([row["latency_ms"] for row in records if row.get("ok")])
    document = {
        "source": "live",
        "model": settings.model,
        "api_version": settings.api_version,
        "calls_requested": args.calls,
        "image_resolution": settings.image_resolution,
        "clock": "time.perf_counter around client.decisions.create only",
        "cost_formula": "input_tokens * 0.10 / 1000000",
        "output_billed": False,
        "calls": records,
        "latency_ms": summary,
        "note": (
            "Percentiles use linear interpolation between sorted ranks. "
            "Calls with ok=false have no latency, token, or cost fields. "
            "A null cost means the response omitted total_input_tokens."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(document), encoding="utf-8")
    print(render_human(document))
    print(f"Wrote {args.output}")
    print(f"Wrote {args.markdown}")
    return 0 if any(row.get("ok") for row in records) else 1


def list_photos(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def sniff_mime(data: bytes, allowed: tuple[str, ...]) -> str | None:
    for mime in allowed:
        if matches_mime(data, mime):
            return mime
    return None


def percentile(values: list[float], p: float) -> float:
    """Linear interpolation, matching numpy.percentile's linear method."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= p <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (p / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    weight = rank - low
    return float(ordered[low] * (1 - weight) + ordered[high] * weight)


def summarize_latency(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "n": len(values),
        "min": min(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values),
    }


def render_markdown(document: dict) -> str:
    lines = [
        "# Measured Decisions API calls",
        "",
        "These figures come from live `client.decisions.create` calls.",
        "Latency is `time.perf_counter()` around that call only.",
        "Cost = input_tokens × 0.10 / 1,000,000. Output tokens are not billed.",
        "Failed calls are listed without latency, token, or cost figures.",
        "",
        f"Model: `{document['model']}`",
        f"Resolution: `{document.get('image_resolution', 'unspecified')}`",
        "",
        "| Photo | OK | Latency (ms) | Input tokens | Output tokens | Cost (USD) |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in document["calls"]:
        if not row.get("ok"):
            lines.append(f"| {row['photo']} | no |  |  |  |  |")
            continue
        lines.append(
            "| {photo} | yes | {latency:.1f} | {tokens} | {output} | {cost} |".format(
                photo=row["photo"],
                latency=row["latency_ms"],
                tokens=_cell(row.get("input_tokens")),
                output=_cell(row.get("output_tokens")),
                cost=_cell(row.get("cost_usd")),
            )
        )
    lines.append("")
    summary = document.get("latency_ms")
    if summary is None:
        lines.append("No successful call, so latency percentiles were not computed.")
    else:
        lines.append(
            "Latency ms over {n} successful calls: min {min:.1f}, p50 {p50:.1f}, p95 {p95:.1f}, max {max:.1f}.".format(
                **summary
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_human(document: dict) -> str:
    """Short stdout summary. Same figures as the files, no extra estimates."""
    calls = document["calls"]
    ok = [row for row in calls if row.get("ok")]
    failed = len(calls) - len(ok)
    resolution = document.get("image_resolution", "unspecified")
    lines = [
        f"{len(calls)} live calls at resolution {resolution}: {len(ok)} succeeded, {failed} failed."
    ]
    summary = document.get("latency_ms")
    if not summary:
        lines.append("No successful call, so latency, tokens, and cost were not summarized.")
    else:
        lines.append(
            "Latency ms: min {min:.1f}, p50 {p50:.1f}, p95 {p95:.1f}, max {max:.1f}.".format(**summary)
        )
        tokens = [row.get("input_tokens") for row in ok]
        costs = [row.get("cost_usd") for row in ok]
        if any(token is None for token in tokens):
            lines.append("Input tokens were missing on a successful call, so they are not totaled.")
        else:
            lines.append("Input tokens: " + ", ".join(str(token) for token in tokens) + ".")
        if any(cost is None for cost in costs):
            lines.append("Cost was not computed for every successful call.")
        else:
            lines.append("Cost USD: " + ", ".join(str(cost) for cost in costs) + ".")
    lines.append("Output tokens are not billed. These figures were measured on the live calls above.")
    return "\n".join(lines)


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)


if __name__ == "__main__":
    sys.exit(main())
