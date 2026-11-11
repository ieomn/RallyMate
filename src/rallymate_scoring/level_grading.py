"""Level-specific aggregation of measured technique scores.

The major weights and promotion gates come from the supplied audit guide.
Unspecified internal weights are a versioned engineering policy, not a claim
that the unavailable source tables were reproduced. Missing measurements affect
coverage, never become zero scores, and never prevent displaying observed work.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
import math
from pathlib import Path
import re
from typing import Literal

DOMAINS = ("GS", "FS", "SS", "RS", "NS", "DS", "OS", "TS")
BackhandStyle = Literal["two_handed", "one_handed"]
GATES = {
    "intermediate": ("non_dominant_hand_ok", "stroke_continuity_ok", "body_coordination_ok"),
    "advanced": ("contact_point_reasonable", "serve_fluency_ok", "ball_control_consistency_ok"),
}


@lru_cache(maxsize=1)
def _policy():
    return json.loads((Path(__file__).parent / "data" / "level_grading_v1.json").read_text(encoding="utf-8"))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _rounded(value, places=4):
    # Scores and weights are nonnegative. Use the same half-up rule as JS,
    # including exact ties, instead of Python's distinct bankers rounding.
    scale = 10 ** places
    return math.floor(value * scale + .5) / scale


def _code(value):
    if not isinstance(value, str) or not re.fullmatch(r"(GS|FS|SS|RS|NS|DS|OS|TS)-?\d{2}", value):
        raise ValueError(f"unknown technique code: {value!r}")
    return value.replace("-", "")


def grade_level(
    technique_scores: Mapping[str, float | None], *, target_level: int = 1,
    backhand_style: BackhandStyle = "two_handed", gates: Mapping[str, bool | None] | None = None,
    technique_weights: Mapping[str, Mapping[str, float]] | None = None,
    domain_scores: Mapping[str, float | None] | None = None,
) -> dict:
    """Aggregate scores in [0,100]; gate eligibility is separate from the score.

    A target level is a requested assessment tier, not inferred from arbitrary
    percentage cutpoints. Missing promotion evidence means pending, not failure.
    Callers may override an entire domain's internal weights for this assessment.
    """
    if type(target_level) is not int or target_level not in range(1, 6):
        raise ValueError("target_level must be an integer from 1 to 5")
    if backhand_style not in ("two_handed", "one_handed"):
        raise ValueError("backhand_style must be two_handed or one_handed")
    policy = _policy()
    level = policy["levels"][str(target_level)]
    all_codes = {code for group in level["technique_weights"].values() for code in group}
    scores = {}
    families = dict(domain_scores or {})
    if any(domain not in DOMAINS or score is not None and (not _number(score) or not 0 <= score <= 100)
           for domain, score in families.items()):
        raise ValueError("domain_scores must name known domains with finite scores in [0,100]")
    for raw_code, score in technique_scores.items():
        code = _code(raw_code)
        if code not in all_codes or code in scores:
            raise ValueError(f"unknown or duplicate technique code: {raw_code}")
        if score is not None and (not _number(score) or not 0 <= score <= 100):
            raise ValueError(f"score must be finite and in [0,100]: {raw_code}")
        scores[code] = score
    excluded = {"GS03", "RS03"} if backhand_style == "two_handed" else {"GS02", "RS02"}
    weights = {domain: dict(group) for domain, group in level["technique_weights"].items()}
    for domain, group in (technique_weights or {}).items():
        if domain not in DOMAINS or not isinstance(group, Mapping) or not group:
            raise ValueError("technique weight override must name a domain and its technique weights")
        override = {}
        for raw_code, weight in group.items():
            code = _code(raw_code)
            if code not in all_codes or not code.startswith(domain) or code in override:
                raise ValueError("technique weight belongs to wrong domain or is duplicated")
            if not _number(weight) or weight < 0:
                raise ValueError("technique weights must be finite and nonnegative")
            override[code] = float(weight)
        if not any(weight > 0 and code not in excluded for code, weight in override.items()):
            raise ValueError("technique weights need at least one active positive weight")
        weights[domain] = override
    domain_results = {}
    observed_weight = 0.0
    total = 0.0
    coverage = 0.0
    for domain in DOMAINS:
        active = {code: value for code, value in weights[domain].items() if code not in excluded and value > 0}
        denominator = sum(active.values())
        normalized = {code: value / denominator for code, value in active.items()} if denominator else {}
        observed = {code: weight for code, weight in normalized.items() if scores.get(code) is not None}
        observed_mass = sum(observed.values())
        score = sum(scores[code] * weight for code, weight in observed.items()) / observed_mass if observed_mass else families.get(domain)
        major_weight = level["module_weights"][domain]
        if score is not None and major_weight > 0:
            observed_weight += major_weight
            total += score * major_weight
        coverage += observed_mass * major_weight
        domain_results[domain] = {
            "score": None if score is None else _rounded(score, 4),
            "family_score": families.get(domain),
            "score_source": "measured_techniques" if observed_mass else "observed_family" if score is not None else "unobserved",
            "module_weight": major_weight,
            "coverage": _rounded(observed_mass, 6),
            "active_weights": normalized,
            "observed_weights": {code: weight / observed_mass for code, weight in observed.items()},
            "measured_techniques": sorted(observed),
            "missing_techniques": sorted(set(active) - set(observed)),
            "excluded_techniques": sorted(set(weights[domain]) & excluded),
        }
    gate_values = dict(gates or {})
    if set(gate_values) - {key for keys in GATES.values() for key in keys}:
        raise ValueError("unknown promotion gate")
    if any(value is not None and type(value) is not bool for value in gate_values.values()):
        raise ValueError("gate values must be true, false, or null")
    gate_results = {}
    for name, keys in GATES.items():
        states = {key: gate_values.get(key) for key in keys}
        status = "passed" if all(value is True for value in states.values()) else "failed" if False in states.values() else "pending"
        gate_results[name] = {"status": status, "checks": states,
                              "missing_evidence": [key for key, value in states.items() if value is None],
                              "failed_checks": [key for key, value in states.items() if value is False]}
    eligible = target_level
    if gate_results["intermediate"]["status"] != "passed":
        eligible = min(eligible, 2)
    elif gate_results["advanced"]["status"] != "passed":
        eligible = min(eligible, 3)
    overall = total / observed_weight if observed_weight else None
    return {
        "version": "level-assessment-v1.0.0", "policy_version": policy["version"],
        "status": "scored" if overall is not None else "no_measured_active_techniques",
        "target_level": target_level, "eligible_level": eligible if overall is not None else None,
        "backhand_style": backhand_style, "overall_score": None if overall is None else _rounded(overall, 4),
        "score_semantics": "measured_active_techniques_with_available_module_weight_normalization",
        "weighted_coverage": _rounded(coverage, 6), "observed_module_weight": _rounded(observed_weight, 6),
        "domains": domain_results, "gatekeepers": gate_results,
        "weight_source": "caller_override" if technique_weights else policy["internal_weight_source"],
        "major_weight_source": policy["major_weight_source"],
        "level_semantics": "requested_tier_with_promotion_gates_not_skill_certification",
    }
