"""Versioned, source-preserving contracts for the 298 Word scoring cards.

Having a contract is deliberately distinct from having a verified extractor.
Numeric values in this catalogue are engineering proxy checks, not calibrated
tennis grades or numbers attributed to the source documents.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json

VERSION = "card-rules-v1.0.0"
GRADE_POLICY = "condition_completion_v1"
GRADE_MAPPING = {"A": 100, "B": 80, "C": 60, "D": 40, "E": 20}


@lru_cache(maxsize=1)
def _catalog() -> dict:
    data = json.loads(files("rallymate_scoring").joinpath("data/card_rules_v1.json").read_text(encoding="utf-8"))
    if data.get("version") != VERSION:
        raise ValueError("unsupported card catalogue version")
    cards = data.get("cards", [])
    ids = [card["indicator_id"] for card in cards]
    if len(ids) != 298 or len(set(ids)) != 298:
        raise ValueError("card catalogue must contain exactly 298 unique source cards")
    return data


def load_card_rules() -> dict:
    """Return an isolated catalogue; callers cannot mutate the cached rules."""
    return deepcopy(_catalog())


def get_card_rule(indicator_id: str) -> dict:
    for card in _catalog()["cards"]:
        if card["indicator_id"] == indicator_id:
            return deepcopy(card)
    raise KeyError(indicator_id)


def get_stage_rule(stage_code: str) -> dict:
    for stage in _catalog()["stages"]:
        if stage["stage_code"] == stage_code:
            return deepcopy(stage)
    raise KeyError(stage_code)


def catalogue_coverage() -> dict:
    """Report specification counts, never advertise these as tested coverage."""
    from collections import Counter
    cards = _catalog()["cards"]
    return {
        "rule_version": VERSION,
        "source_cards": len(cards),
        "specified_by_kind": dict(Counter(card["evaluation"]["kind"] for card in cards)),
        "runtime_validation": "read_execution_evidence_not_catalogue_counts",
        "automatic_source_grade_claims": 0,
        "source_revisions": len(_catalog()["revisions"]),
    }
