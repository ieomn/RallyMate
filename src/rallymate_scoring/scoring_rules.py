"""Validate executable grade mathematics, independently of release governance.

Rules are supplied by the caller; this module never generates cut points from
qualitative Word text. Dataset, approval and maturity metadata may accompany a
rule, but are not prerequisites for evaluating its stated mathematics.
"""
from __future__ import annotations

import math
from collections.abc import Mapping


def finite_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def validate_scoring_rule(rule: Mapping) -> None:
    if not isinstance(rule, Mapping):
        raise ValueError("scoring rule must be an object")
    backend = rule.get("backend")
    fields = ("indicator_id",)
    if backend == "threshold_rule":
        fields += ("threshold_version", "primary_feature", "primary_feature_version", "unit")
        if rule.get("direction") not in {"higher_is_better", "lower_is_better"}:
            raise ValueError("threshold direction is invalid")
        cuts = rule.get("thresholds")
    elif backend == "ordinal_regression":
        fields += ("model_version",)
        names, coefficients = rule.get("feature_order"), rule.get("coefficients")
        if (not isinstance(names, list) or not names
                or any(not isinstance(name, str) or not name for name in names)
                or len(names) != len(set(names))):
            raise ValueError("ordinal feature_order must contain unique feature names")
        if (not isinstance(coefficients, list) or len(coefficients) != len(names)
                or not all(finite_number(value) for value in coefficients)
                or not finite_number(rule.get("intercept", 0.0))):
            raise ValueError("ordinal coefficients and intercept must be finite and match features")
        for field in ("unit_by_feature", "feature_version_by_feature"):
            mapping = rule.get(field)
            if (not isinstance(mapping, Mapping) or set(mapping) != set(names)
                    or any(not isinstance(value, str) or not value for value in mapping.values())):
                raise ValueError(f"ordinal {field} must exactly match feature_order")
        cuts = rule.get("cutpoints")
    else:
        raise ValueError(f"unsupported scoring backend: {backend!r}")
    if any(not isinstance(rule.get(field), str) or not rule[field] for field in fields):
        raise ValueError("scoring rule requires non-empty identity, version and unit fields")
    if (not isinstance(cuts, list) or len(cuts) != 4
            or not all(finite_number(value) for value in cuts)
            or any(right <= left for left, right in zip(cuts, cuts[1:]))):
        raise ValueError("A-E rules require four finite, strictly increasing cut points")
