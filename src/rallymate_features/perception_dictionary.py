"""Versioned identifiers from the supplied scoring guide.

An identifier being reserved never claims that its value has been measured.
The guide specifies ranges, but does not define every member of those ranges;
those entries deliberately retain ``semantic_status=reserved``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


PERCEPTION_DICTIONARY_VERSION = "scoring-guide-perception-v1.0.0"
PERCEPTION_ID_RANGES = {
    "J": (1, 179),  # The guide's lower-limb section explicitly includes J179.
    "L": (10, 116), "RK": (1, 23), "BALL-": (10, 35),
    "S": (10, 89), "V": (10, 34), "K": (10, 97), "T": (1, 20),
}
CANONICAL_PERCEPTION_IDS = frozenset(
    f"{prefix}{number:03d}"
    for prefix, (first, last) in PERCEPTION_ID_RANGES.items()
    for number in range(first, last + 1)
) | {"V001", "S013_BASE_WIDTH", "S014_WEIGHT_DISTRIBUTION"}
PERCEPTION_ALIASES = {
    "BALL-P01": ("BALL-010",), "BALL-P04": ("BALL-013",),
    "BALL-M01": ("BALL-020",), "BALL-M06": ("BALL-025",),
    "BALL-M07": ("BALL-026",),
    # S013 is overloaded in the source. Expand dependencies to both, never
    # silently choose a meaning or duplicate a scalar into incompatible units.
    "S013": ("S013_BASE_WIDTH", "S014_WEIGHT_DISTRIBUTION"),
}
_DEFINED = {
    "S013_BASE_WIDTH": ("Base width", "length", ("J146", "J166")),
    "S014_WEIGHT_DISTRIBUTION": ("Body center projection on support axis", "ratio", ("J077", "J146", "J166")),
    "V001": ("Visual attention readiness", "observation", ("J002", "J004", "J008", "J009")),
    "L030": ("Shoulder width", "length", ("J033", "J034")),
    "L110": ("Body height", "length", ()),
    "RK005": ("Racket sweet-spot center", "position", ()),
    "RK013": ("Racket throat", "position", ()),
    "BALL-010": ("Ball center", "position", ()),
    "BALL-013": ("Ball height", "length", ()),
    "BALL-020": ("Ball speed", "length/time", ()),
    "BALL-025": ("Ball direction vector", "vector", ()),
    "BALL-026": ("Ball trajectory", "trajectory", ()),
    "BALL-030": ("Ball spin rate", "rotation/time", ()),
}


def resolve_perception_ids(identifier: str) -> tuple[str, ...]:
    """Resolve a source dependency; overloaded S013 resolves to two IDs."""
    if identifier in PERCEPTION_ALIASES:
        return PERCEPTION_ALIASES[identifier]
    if identifier not in CANONICAL_PERCEPTION_IDS:
        raise ValueError(f"Unknown perception identifier: {identifier}")
    return (identifier,)


def perception_definition(identifier: str) -> dict[str, Any]:
    resolved = resolve_perception_ids(identifier)
    if len(resolved) != 1:
        return {"id": identifier, "semantic_status": "ambiguous_legacy_alias", "canonical_ids": list(resolved)}
    canonical = resolved[0]
    definition = _DEFINED.get(canonical)
    return {
        "id": canonical, "version": PERCEPTION_DICTIONARY_VERSION,
        "semantic_status": "defined_by_guide" if definition else "reserved",
        "name": definition[0] if definition else None,
        "unit_family": definition[1] if definition else None,
        "dependencies": list(definition[2]) if definition else [],
        "measurement_available": False,
    }


def normalize_perception_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize legacy input without inventing values for ambiguous S013.

    S013 may be an object containing ``base_width`` and/or
    ``weight_distribution``. A legacy scalar remains available under S013
    until its caller provides the missing semantic context.
    """
    normalized: dict[str, Any] = {}
    for identifier, value in values.items():
        targets = resolve_perception_ids(identifier)
        if len(targets) > 1:
            normalized[identifier] = value
            if isinstance(value, Mapping):
                for field, target in zip(("base_width", "weight_distribution"), targets):
                    if field in value:
                        if target in normalized and normalized[target] != value[field]:
                            raise ValueError(f"Conflicting values for {target}")
                        normalized[target] = value[field]
            continue
        target = targets[0]
        if target in normalized and normalized[target] != value:
            raise ValueError(f"Conflicting values for {target}")
        normalized[target] = value
    return normalized
