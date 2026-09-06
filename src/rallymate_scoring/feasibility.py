from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


FEASIBILITY_LEVELS = ("F0", "F1", "F2", "F3", "F4")
# Backwards-compatible name for the original minimum scoring-loop wave.  Code
# that imports TARGET_INDICATORS must continue to see exactly these six IDs.
TARGET_INDICATORS = {
    "FS01-M02",
    "FS01-M05",
    "FS02-M02",
    "FS09-M03",
    "FS09-M04",
    "FS09-M05",
}
POSE_WAVE_V2_INDICATORS = TARGET_INDICATORS | {
    "FS01-M03",
    "FS01-M04",
    "FS02-M03",
    "FS02-M04",
    "FS02-M05",
    "FS09-M01",
    "FS09-M02",
}
SUPPORTED_REGISTRY_SCHEMA_VERSIONS = {"1.0.0", "2.0.0"}
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def assess_feasibility_promotion(
    indicator: dict[str, Any],
    *,
    target_level: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Assess one maturity step without mutating the registry.

    Numerical acceptance thresholds stay in an external pre-registered protocol;
    this function only verifies the required evidence and review decisions.
    """

    current_level = indicator.get("feasibility_level")
    if current_level not in FEASIBILITY_LEVELS or target_level not in FEASIBILITY_LEVELS:
        raise ValueError("invalid feasibility level")
    current_index = FEASIBILITY_LEVELS.index(current_level)
    target_index = FEASIBILITY_LEVELS.index(target_level)
    if target_index != current_index + 1:
        return {
            "allowed": False,
            "current_level": current_level,
            "target_level": target_level,
            "blockers": ["promotion_must_advance_exactly_one_level"],
        }
    blockers = []
    if not _non_empty(evidence.get("evidence_version")):
        blockers.append("evidence_version_missing")
    if target_level == "F1":
        event_layer = evidence.get("event_layer", {})
        if event_layer.get("status") != "verified":
            blockers.append("event_layer_not_verified")
        required_codes = {
            value.split(".")[0] for value in indicator.get("required_events", [])
        }
        if not required_codes.issubset(set(event_layer.get("event_codes", []))):
            blockers.append("required_event_codes_missing")
        if not _non_empty(event_layer.get("contract_version")):
            blockers.append("event_contract_version_missing")
    elif target_level == "F2":
        feature_layer = evidence.get("feature_layer", {})
        if feature_layer.get("status") != "verified":
            blockers.append("feature_layer_not_verified")
        if not set(indicator.get("required_features", [])).issubset(
            set(feature_layer.get("feature_names", []))
        ):
            blockers.append("required_features_missing")
        if feature_layer.get("timestamp_ms_verified") is not True:
            blockers.append("timestamp_ms_not_verified")
        if feature_layer.get("missing_value_policy_verified") is not True:
            blockers.append("missing_value_policy_not_verified")
        if not _non_empty(feature_layer.get("library_version")):
            blockers.append("feature_library_version_missing")
    elif target_level == "F3":
        for field, status in (
            ("event_evaluation", "evaluated"),
            ("feature_evaluation", "evaluated"),
            ("grade_separation", "passed"),
            ("coach_agreement", "evaluated"),
            ("internal_calibration", "validated"),
        ):
            value = evidence.get(field, {})
            if value.get("status") != status:
                blockers.append(f"{field}_{status}_required")
            if not _non_empty(value.get("report_version")):
                blockers.append(f"{field}_report_version_missing")
        review = evidence.get("acceptance_review", {})
        if review.get("decision") != "passed":
            blockers.append("F3_acceptance_review_not_passed")
        if not _non_empty(review.get("protocol_version")):
            blockers.append("F3_acceptance_protocol_missing")
        if not _non_empty(review.get("reviewer_id")):
            blockers.append("F3_reviewer_missing")
    elif target_level == "F4":
        independent = evidence.get("independent_test", {})
        if independent.get("status") != "passed":
            blockers.append("independent_test_not_passed")
        if independent.get("approved_for_scoring") is not True:
            blockers.append("independent_test_scoring_approval_missing")
        for field in (
            "dataset_version",
            "report_version",
            "acceptance_protocol_version",
            "evaluated_at",
        ):
            if not _non_empty(independent.get(field)):
                blockers.append(f"independent_test_{field}_missing")
        report_sha = independent.get("report_sha256")
        if not isinstance(report_sha, str) or not _SHA256_PATTERN.fullmatch(report_sha):
            blockers.append("independent_test_report_sha256_invalid")
        if not _non_empty(evidence.get("calibration_version")):
            blockers.append("calibration_version_missing")
        if not _non_empty(evidence.get("test_split_manifest_version")):
            blockers.append("independent_test_split_manifest_missing")
    return {
        "allowed": not blockers,
        "current_level": current_level,
        "target_level": target_level,
        "blockers": blockers,
        "semantics": "evidence_gate_only_no_numeric_acceptance_thresholds_embedded",
    }


def _validate_registry_header(payload: dict[str, Any]) -> str:
    schema_version = payload.get("schema_version")
    if schema_version not in SUPPORTED_REGISTRY_SCHEMA_VERSIONS:
        raise ValueError("unsupported metric feasibility schema_version")
    if not _non_empty(payload.get("registry_version")):
        raise ValueError("metric feasibility registry_version must be non-empty")
    if not _non_empty(payload.get("updated_at")):
        raise ValueError("metric feasibility updated_at must be non-empty")
    if not isinstance(payload.get("scope"), dict):
        raise ValueError("metric feasibility scope must be an object")
    definitions = payload.get("level_definitions")
    if not isinstance(definitions, dict) or not all(
        _non_empty(definitions.get(level)) for level in FEASIBILITY_LEVELS
    ):
        raise ValueError("metric feasibility level_definitions must define F0 through F4")
    return schema_version


def _validate_indicator(indicator: Any) -> str:
    if not isinstance(indicator, dict):
        raise ValueError("metric feasibility indicator must be an object")
    indicator_id = indicator.get("indicator_id")
    if not _non_empty(indicator_id):
        raise ValueError("metric feasibility indicator_id must be a non-empty string")
    if indicator.get("feasibility_level") not in FEASIBILITY_LEVELS:
        raise ValueError(f"{indicator_id} has invalid feasibility_level")
    for field in (
        "required_events",
        "required_features",
        "view_constraints",
        "ground_truth_requirements",
    ):
        values = indicator.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not _non_empty(value) for value in values)
        ):
            raise ValueError(f"{indicator_id}.{field} must be a non-empty string list")
    measurement_features = indicator.get("measurement_features")
    if measurement_features is not None:
        if (
            not isinstance(measurement_features, list)
            or not measurement_features
            or any(not _non_empty(value) for value in measurement_features)
            or len(measurement_features) != len(set(measurement_features))
        ):
            raise ValueError(
                f"{indicator_id}.measurement_features must be a unique non-empty string list"
            )
        if not set(measurement_features).issubset(set(indicator["required_features"])):
            raise ValueError(
                f"{indicator_id}.measurement_features must be a subset of required_features"
            )
    blockers = indicator.get("current_blockers")
    if not isinstance(blockers, list) or any(
        not _non_empty(value) for value in blockers
    ):
        raise ValueError(f"{indicator_id}.current_blockers must be a string list")
    metrics = indicator.get("acceptance_metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError(f"{indicator_id}.acceptance_metrics must be an object")
    if any(
        value is not None
        and (isinstance(value, bool) or not isinstance(value, (int, float)))
        for value in metrics.values()
    ):
        raise ValueError(f"{indicator_id} acceptance metrics must be numeric or null")
    versions = indicator.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise ValueError(f"{indicator_id}.versions must be a non-empty object")
    availability = indicator.get("feature_availability")
    if availability is not None:
        if not isinstance(availability, dict):
            raise ValueError(
                f"{indicator_id}.feature_availability must be an object"
            )
        implemented = availability.get("implemented")
        missing = availability.get("missing")
        if not isinstance(implemented, list) or any(
            not _non_empty(value) for value in implemented
        ):
            raise ValueError(
                f"{indicator_id}.feature_availability.implemented must be a string list"
            )
        if not isinstance(missing, list) or any(
            not _non_empty(value) for value in missing
        ):
            raise ValueError(
                f"{indicator_id}.feature_availability.missing must be a string list"
            )
        if set(implemented) & set(missing):
            raise ValueError(
                f"{indicator_id}.feature_availability sets must be disjoint"
            )
        if set(implemented) | set(missing) != set(indicator["required_features"]):
            raise ValueError(
                f"{indicator_id}.feature_availability must partition required_features"
            )
        if not _non_empty(availability.get("assessment_basis")):
            raise ValueError(
                f"{indicator_id}.feature_availability.assessment_basis must be non-empty"
            )
    return indicator_id


def measurement_feature_names(indicator: dict[str, Any]) -> list[str]:
    """Return features measurable from the declared sensor/pose layer alone.

    ``required_features`` remains the complete ordered vector consumed by a
    scoring calibration.  An indicator may declare ``measurement_features``
    when its complete scoring vector also contains a separately adjudicated
    context feature, such as FS02-M02 target-direction alignment.
    """

    names = indicator.get("measurement_features", indicator.get("required_features"))
    if (
        not isinstance(names, list)
        or not names
        or any(not _non_empty(value) for value in names)
        or len(names) != len(set(names))
    ):
        raise ValueError("indicator measurement feature contract is invalid")
    required = indicator.get("required_features")
    if not isinstance(required, list) or not set(names).issubset(set(required)):
        raise ValueError("measurement features must be a subset of required_features")
    return list(names)


def validate_feasibility_registry(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("metric feasibility registry must be an object")
    schema_version = _validate_registry_header(payload)
    indicators = payload.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("metric feasibility indicators must be a non-empty list")
    ids = [_validate_indicator(item) for item in indicators]
    if len(ids) != len(set(ids)):
        raise ValueError("metric feasibility indicator_id values must be unique")
    if schema_version == "1.0.0" and (
        set(ids) != TARGET_INDICATORS or len(ids) != len(TARGET_INDICATORS)
    ):
        raise ValueError("metric feasibility registry must contain the six target indicators once")
    if schema_version == "2.0.0":
        scope = payload["scope"]
        if scope.get("indicator_count") != len(ids):
            raise ValueError(
                "metric feasibility scope.indicator_count must match indicators"
            )
        events = scope.get("events")
        if (
            not isinstance(events, list)
            or not events
            or any(not _non_empty(value) for value in events)
        ):
            raise ValueError(
                "metric feasibility scope.events must be a non-empty string list"
            )


def load_feasibility_registry(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_feasibility_registry(payload)
    return payload


def indicator_ids_at_or_above(
    registry: dict[str, Any],
    minimum_level: str,
) -> set[str]:
    """Return indicators whose validated maturity is at least ``minimum_level``.

    Consumers use this helper instead of maintaining a second hard-coded list
    of feature-measurable indicators.  F3/F4 indicators remain feature
    measurable, so they are intentionally included when the minimum is F2.
    """

    validate_feasibility_registry(registry)
    if minimum_level not in FEASIBILITY_LEVELS:
        raise ValueError("invalid minimum feasibility level")
    minimum_index = FEASIBILITY_LEVELS.index(minimum_level)
    return {
        str(indicator["indicator_id"])
        for indicator in registry["indicators"]
        if FEASIBILITY_LEVELS.index(str(indicator["feasibility_level"]))
        >= minimum_index
    }


def feasibility_event_codes(registry: dict[str, Any]) -> set[str]:
    """Return the base event codes declared by a validated registry scope."""

    validate_feasibility_registry(registry)
    values = registry["scope"].get("events")
    if (
        not isinstance(values, list)
        or not values
        or any(not _non_empty(value) for value in values)
    ):
        raise ValueError("metric feasibility scope.events must be a non-empty string list")
    return {str(value).split(".", 1)[0] for value in values}
