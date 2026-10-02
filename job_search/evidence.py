from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_SECTIONS = (
    "professional_responsibilities",
    "tools",
    "domains",
    "leadership",
    "education",
    "practical_eligibility",
    "learning_only",
    "unsupported_ownership",
)


def load_evidence(path: Path) -> dict[str, Any]:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    missing = [section for section in REQUIRED_SECTIONS if section not in evidence]
    if missing:
        raise ValueError("Evidence file is missing required sections: " + ", ".join(missing))
    required_item_fields = {
        "id", "capability", "evidence_type", "strength", "employer", "role",
        "work_periods", "context", "limitations", "actual_hiring_fit", "ats_similarity",
    }
    for item in evidence_items(
        evidence, "professional_responsibilities", "tools", "domains", "learning_only", "unsupported_ownership"
    ):
        absent = required_item_fields.difference(item)
        if absent:
            raise ValueError(f"Evidence item {item.get('id', '<unknown>')} is missing provenance fields: {', '.join(sorted(absent))}")
    return evidence


def evidence_items(evidence: dict[str, Any], *sections: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for section in sections:
        value = evidence.get(section, [])
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def verified_evidence_terms(evidence: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for item in evidence_items(evidence, "professional_responsibilities", "tools", "domains"):
        if item.get("evidence_type", "direct") != "direct" or not item.get("actual_hiring_fit", True):
            continue
        terms.update(str(term).lower() for term in item.get("terms", []))
    return terms


def evidence_index(evidence: dict[str, Any], *sections: str) -> dict[str, dict[str, Any]]:
    return {
        str(item["id"]): item
        for item in evidence_items(evidence, *sections)
        if item.get("id")
    }
