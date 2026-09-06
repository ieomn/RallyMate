from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


INDICATOR_REQUIREMENTS_SCHEMA_VERSION = "1.0.0"
INDICATOR_REQUIREMENTS_CANONICALIZATION = "rallymate-canonical-json-v1"


PHASE_KEY_BY_EVENT_STAGE: dict[str, str] = {
    "preload": "preload_ms",
    "takeoff_proxy": "takeoff_proxy_ms",
    "landing_proxy": "landing_proxy_ms",
    "redistribution": "redistribution_ms",
    "initiation": "initiation_ms",
    "direction_conversion": "direction_conversion_ms",
    "support_extension_proxy": "support_extension_proxy_ms",
    "lead_foot_motion_onset_proxy": "lead_foot_motion_onset_proxy_ms",
    "first_step_slowdown_proxy": "first_step_slowdown_proxy_ms",
    "peak_speed": "peak_speed_ms",
    "deceleration": "deceleration_peak_ms",
    "absorption": "deceleration_peak_ms",
    "restabilization": "restabilization_onset_ms",
    "stable_control": "stable_control_onset_ms",
}


def event_code_for_indicator(indicator_id: str) -> str:
    if not isinstance(indicator_id, str) or "-M" not in indicator_id:
        raise ValueError("indicator_id must use the event-Mnn format")
    return indicator_id.split("-M", 1)[0]


def required_phase_keys(indicator: Mapping[str, Any]) -> list[str]:
    indicator_id = indicator.get("indicator_id")
    event_code = event_code_for_indicator(indicator_id)
    requirements = indicator.get("required_events")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError(f"{indicator_id}.required_events must be a non-empty list")
    result: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, str) or not requirement:
            raise ValueError("registry required_events values must be non-empty strings")
        code, separator, stage = requirement.partition(".")
        if code != event_code:
            raise ValueError(
                f"registry required_events does not match indicator: "
                f"{indicator_id}:{requirement}"
            )
        if not separator:
            continue
        phase_key = PHASE_KEY_BY_EVENT_STAGE.get(stage)
        if phase_key is None:
            raise ValueError(
                f"unsupported registry event stage for manual truth: {requirement}"
            )
        result.add(phase_key)
    return sorted(result)


def requirement_snapshot(
    indicator: Mapping[str, Any],
    *,
    required_semantic_keys: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Return the authoritative, deterministic independent-test requirements."""

    features = indicator.get("required_features")
    if (
        not isinstance(features, list)
        or not features
        or any(not isinstance(name, str) or not name for name in features)
        or len(features) != len(set(features))
    ):
        raise ValueError("indicator.required_features must be unique non-empty strings")
    semantics = list(required_semantic_keys)
    if any(not isinstance(name, str) or not name for name in semantics):
        raise ValueError("required_semantic_keys must contain non-empty strings")
    return {
        "indicator_id": indicator["indicator_id"],
        "required_feature_names": list(features),
        "required_phase_keys": required_phase_keys(indicator),
        "required_semantic_keys": sorted(set(semantics)),
    }


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_indicator_requirements_snapshot(
    *,
    registry: Mapping[str, Any],
    semantic_requirements: Mapping[str, list[str] | tuple[str, ...]],
    requirements_version: str,
    semantic_contract_version: str,
    artifact_scope: str = "indicator_scoring_requirements",
) -> dict[str, Any]:
    if artifact_scope not in {
        "indicator_scoring_requirements",
        "synthetic_test_only_indicator_scoring_requirements",
    }:
        raise ValueError("invalid indicator requirements artifact_scope")
    if not isinstance(requirements_version, str) or not requirements_version:
        raise ValueError("requirements_version must be non-empty")
    if not isinstance(semantic_contract_version, str) or not semantic_contract_version:
        raise ValueError("semantic_contract_version must be non-empty")
    indicators = registry.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("registry indicators must be non-empty")
    indicator_ids = [item.get("indicator_id") for item in indicators]
    if set(indicator_ids) != set(semantic_requirements):
        raise ValueError(
            "semantic requirements must exactly cover registry indicator ids"
        )
    records = []
    for indicator in indicators:
        record = requirement_snapshot(
            indicator,
            required_semantic_keys=semantic_requirements[indicator["indicator_id"]],
        )
        record["registry_indicator_sha256"] = canonical_sha256(indicator)
        records.append(record)
    semantic_payload = {
        indicator_id: sorted(set(values))
        for indicator_id, values in semantic_requirements.items()
    }
    return {
        "schema_version": INDICATOR_REQUIREMENTS_SCHEMA_VERSION,
        "artifact_scope": artifact_scope,
        "requirements_version": requirements_version,
        "source": {
            "kind": (
                "synthetic_test_fixture"
                if artifact_scope.startswith("synthetic_test_only")
                else "versioned_registry_and_manual_truth_contract"
            ),
            "registry_version": registry.get("registry_version"),
            "registry_content_sha256": canonical_sha256(registry),
            "semantic_contract_version": semantic_contract_version,
            "semantic_contract_sha256": canonical_sha256(semantic_payload),
            "canonicalization": INDICATOR_REQUIREMENTS_CANONICALIZATION,
        },
        "indicators": records,
    }


def validate_indicator_requirements_snapshot(
    payload: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ValueError("indicator requirements snapshot must be an object")
    if payload.get("schema_version") != INDICATOR_REQUIREMENTS_SCHEMA_VERSION:
        raise ValueError("unsupported indicator requirements schema_version")
    scope = payload.get("artifact_scope")
    if scope not in {
        "indicator_scoring_requirements",
        "synthetic_test_only_indicator_scoring_requirements",
    }:
        raise ValueError("invalid indicator requirements artifact_scope")
    if not isinstance(payload.get("requirements_version"), str) or not payload[
        "requirements_version"
    ]:
        raise ValueError("indicator requirements version must be non-empty")
    source = payload.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("indicator requirements source must be an object")
    expected_kind = (
        "synthetic_test_fixture"
        if str(scope).startswith("synthetic_test_only")
        else "versioned_registry_and_manual_truth_contract"
    )
    if source.get("kind") != expected_kind:
        raise ValueError("indicator requirements source.kind is invalid")
    for field in (
        "registry_version",
        "semantic_contract_version",
        "canonicalization",
    ):
        if not isinstance(source.get(field), str) or not source[field]:
            raise ValueError(f"indicator requirements source.{field} must be non-empty")
    if source["canonicalization"] != INDICATOR_REQUIREMENTS_CANONICALIZATION:
        raise ValueError("indicator requirements canonicalization is invalid")
    for field in ("registry_content_sha256", "semantic_contract_sha256"):
        value = source.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in value)
        ):
            raise ValueError(f"indicator requirements source.{field} must be SHA-256")
    indicators = payload.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("indicator requirements indicators must be non-empty")
    result: dict[str, dict[str, Any]] = {}
    expected_fields = {
        "indicator_id",
        "required_feature_names",
        "required_phase_keys",
        "required_semantic_keys",
        "registry_indicator_sha256",
    }
    for record in indicators:
        if not isinstance(record, dict) or set(record) != expected_fields:
            raise ValueError("indicator requirements record fields are invalid")
        indicator_id = record.get("indicator_id")
        if not isinstance(indicator_id, str) or not indicator_id:
            raise ValueError("indicator requirements indicator_id must be non-empty")
        if indicator_id in result:
            raise ValueError(f"duplicate indicator requirements: {indicator_id}")
        for field in (
            "required_feature_names",
            "required_phase_keys",
            "required_semantic_keys",
        ):
            values = record.get(field)
            if (
                not isinstance(values, list)
                or (field == "required_feature_names" and not values)
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))
            ):
                raise ValueError(f"{indicator_id}.{field} must be a unique string list")
        registry_hash = record.get("registry_indicator_sha256")
        if (
            not isinstance(registry_hash, str)
            or len(registry_hash) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in registry_hash)
        ):
            raise ValueError(f"{indicator_id}.registry_indicator_sha256 must be SHA-256")
        result[indicator_id] = record
    return result
