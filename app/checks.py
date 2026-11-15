"""Load and validate the 50 rack checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REQUIRED_FIELDS = (
    "key",
    "label",
    "category",
    "instructions",
    "true_description",
    "false_description",
    "yes_is_good",
)
CATEGORIES = (
    "Cables",
    "Airflow",
    "Labels",
    "Doors and mounting",
    "Indicators",
    "Surroundings",
)
NEGATED_RE = re.compile(r"\b(no|not)\b|n't", re.IGNORECASE)


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    category: str
    instructions: str
    true_description: str
    false_description: str
    yes_is_good: bool


def validate_checks(raw: object) -> list[Check]:
    if not isinstance(raw, list):
        raise ValueError("checklist must be a list")
    if len(raw) != 50:
        raise ValueError(f"checklist must contain exactly 50 checks, found {len(raw)}")
    checks: list[Check] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"check {index} must be an object")
        missing = [field for field in REQUIRED_FIELDS if field not in item]
        if missing:
            raise ValueError(f"check {index} is missing {', '.join(missing)}")
        key = item["key"]
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise ValueError(
                f"check {index} key {key!r} must match [A-Za-z0-9_-] and be 1 to 64 characters"
            )
        if key in seen:
            raise ValueError(f"duplicate check key {key}")
        seen.add(key)
        category = item["category"]
        if category not in CATEGORIES:
            raise ValueError(f"check {key} has unknown category {category!r}")
        instructions = _text(item, "instructions", key)
        if not instructions.endswith("?"):
            raise ValueError(f"check {key} instructions must be a question")
        if NEGATED_RE.search(instructions):
            raise ValueError(f"check {key} instructions must be worded positively")
        true_description = _text(item, "true_description", key)
        false_description = _text(item, "false_description", key)
        if true_description == false_description:
            raise ValueError(f"check {key} true and false descriptions must differ")
        label = _text(item, "label", key)
        yes_is_good = item["yes_is_good"]
        if not isinstance(yes_is_good, bool):
            raise ValueError(f"check {key} yes_is_good must be a boolean")
        checks.append(
            Check(
                key=key,
                label=label,
                category=category,
                instructions=instructions,
                true_description=true_description,
                false_description=false_description,
                yes_is_good=yes_is_good,
            )
        )
    return checks


def load_checks(path: Path) -> list[Check]:
    return validate_checks(json.loads(path.read_text(encoding="utf-8")))


def _text(item: dict, field: str, key: str) -> str:
    value = item[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"check {key} field {field} must be a non-empty string")
    return value.strip()
