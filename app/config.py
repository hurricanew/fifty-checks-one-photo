"""Cut-offs and runtime settings.

Every probability cut-off is required to be at most 0.99 because a Decisions
API yes/no answer never reports P(yes) above 0.99.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

MAX_CUTOFF = 0.99
ALLOWED_RESOLUTIONS = ("low", "medium", "high", "ultra_high")


@dataclass(frozen=True)
class Thresholds:
    confidence_min: float
    uncertain_low: float
    uncertain_high: float


@dataclass(frozen=True)
class Settings:
    model: str
    api_version: str
    image_resolution: str
    max_upload_bytes: int
    allowed_mime_types: tuple[str, ...]
    input_usd_per_million: float
    thresholds: Thresholds
    fallback_enabled: bool
    fallback_model: str
    checks_path: Path
    sample_path: Path


def validate_thresholds(thresholds: Thresholds) -> None:
    values = {
        "confidence_min": thresholds.confidence_min,
        "uncertain_low": thresholds.uncertain_low,
        "uncertain_high": thresholds.uncertain_high,
    }
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number")
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{name} must be a finite number")
        if float(value) > MAX_CUTOFF:
            raise ValueError(
                f"{name} is {value}, which is above {MAX_CUTOFF}. "
                "Every cut-off must be at most 0.99 because P(yes) never goes above 0.99."
            )
    if thresholds.confidence_min < 0:
        raise ValueError("confidence_min must be >= 0")
    if thresholds.uncertain_low < 0:
        raise ValueError("uncertain_low must be >= 0")
    if not thresholds.uncertain_low < thresholds.uncertain_high:
        raise ValueError("uncertain_low must be below uncertain_high")


def load_settings(
    config_path: Path | None = None,
    checks_path: Path | None = None,
    sample_path: Path | None = None,
) -> Settings:
    path = config_path or (DATA_DIR / "config.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    thresholds = Thresholds(
        confidence_min=raw["thresholds"]["confidence_min"],
        uncertain_low=raw["thresholds"]["uncertain_low"],
        uncertain_high=raw["thresholds"]["uncertain_high"],
    )
    validate_thresholds(thresholds)
    resolution = raw["image_resolution"]
    if resolution not in ALLOWED_RESOLUTIONS:
        raise ValueError(f"image_resolution must be one of {ALLOWED_RESOLUTIONS}")
    price = raw["input_usd_per_million"]
    if price != 0.1:
        raise ValueError(
            "input_usd_per_million must be 0.10, the Decisions API paid-tier input price"
        )
    max_upload = raw["max_upload_bytes"]
    if isinstance(max_upload, bool) or not isinstance(max_upload, int) or max_upload < 1:
        raise ValueError("max_upload_bytes must be a positive int")
    mimes = tuple(raw["allowed_mime_types"])
    if not mimes:
        raise ValueError("allowed_mime_types must not be empty")
    return Settings(
        model=raw["model"],
        api_version=raw["api_version"],
        image_resolution=resolution,
        max_upload_bytes=max_upload,
        allowed_mime_types=mimes,
        input_usd_per_million=price,
        thresholds=thresholds,
        fallback_enabled=bool(raw["fallback_enabled"]),
        fallback_model=raw["fallback_model"],
        checks_path=checks_path or (DATA_DIR / "checks.json"),
        sample_path=sample_path or (DATA_DIR / "sample_response.json"),
    )
