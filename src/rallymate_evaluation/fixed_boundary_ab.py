from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from rallymate_features import (
    FEATURE_LIBRARY_VERSION,
    EventInterval,
    PoseSequence,
    clear_feature_cache,
    compute_event_features,
    pose_sequence_from_records,
)
from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_scoring.feasibility import (
    load_feasibility_registry,
    measurement_feature_names,
)
from rallymate_tracking import diagnose_primary_timeline


FIXED_BOUNDARY_AB_VERSION = "fixed-boundary-pose-ab-v1.0.0"
_HASH_LENGTH = 64
_CIRCULAR_DIRECTION_FEATURES = frozenset(
    {
        "hip_center_motion_direction_deg",
        "launch_direction_deg",
    }
)
_PROHIBITED_SCORING_KEYS = frozenset(
    {
        "grade",
        "threshold",
        "thresholds",
        "threshold_version",
    }
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _finite_scalar(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _comparison_kind(feature_name: str, unit: str) -> str:
    if unit == "code":
        return "categorical"
    if feature_name in _CIRCULAR_DIRECTION_FEATURES:
        return "circular_angle"
    return "continuous"


def _wrapped_angle_difference_deg(first: float, second: float) -> float:
    difference = (first - second + 180.0) % 360.0 - 180.0
    # Keep the endpoint deterministic for exactly opposite directions.
    return 180.0 if difference == -180.0 and first - second > 0 else difference


def _validity_state(first_valid: bool, second_valid: bool) -> str:
    if first_valid and second_valid:
        return "both_valid"
    if first_valid:
        return "model_a_only"
    if second_valid:
        return "model_b_only"
    return "neither_valid"


def _semantic_signature(feature_name: str) -> str:
    definition = FEATURE_DEFINITIONS[feature_name]
    return _canonical_sha256(
        {
            "feature_name": feature_name,
            "unit": definition["unit"],
            "definition": definition["definition"],
            "aggregation": definition["aggregation"],
        }
    )


def _boundary_provenance(event: Mapping[str, Any]) -> dict[str, Any]:
    """Keep source identity/version fields, not model-specific rule internals.

    Candidate segmentation envelopes may contain fields named ``threshold``.
    They are not A-E scoring thresholds, but copying them into this comparison
    would make the no-scoring-threshold contract needlessly ambiguous.  The
    immutable source-record hash still traces the complete original record.
    """

    provenance = event.get("provenance")
    if not isinstance(provenance, Mapping):
        return {}
    allowed = (
        "detector_version",
        "source_id",
        "pose_topology",
        "primary_player_id",
        "rule_family",
        "threshold_semantics",
        "ground_truth_status",
        "phase_candidate_version",
        "phase_candidate_semantics",
    )
    return {name: provenance[name] for name in allowed if name in provenance}


def _feature_payload(result: Any, feature_name: str) -> dict[str, Any]:
    computed_value = result.value
    numeric = _finite_scalar(computed_value)
    valid_numeric = bool(result.valid and numeric is not None)
    return {
        "valid": bool(result.valid),
        "value": numeric if valid_numeric else None,
        "computed_value": computed_value,
        "unit": result.unit,
        "confidence": round(float(result.confidence), 6),
        "reason": result.reason,
        "feature_version": result.feature_version,
        "semantic_signature_sha256": _semantic_signature(feature_name),
        "source_frames": [int(value) for value in result.source_frames],
        "provenance": result.provenance,
    }


def _compare_feature_pair(
    feature_name: str,
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    model_order: tuple[str, str],
) -> dict[str, Any]:
    first_valid = bool(first["valid"])
    second_valid = bool(second["valid"])
    state = _validity_state(first_valid, second_valid)
    unit_match = first.get("unit") == second.get("unit")
    version_match = first.get("feature_version") == second.get("feature_version")
    semantic_match = (
        first.get("semantic_signature_sha256")
        == second.get("semantic_signature_sha256")
    )
    kind = _comparison_kind(feature_name, str(first.get("unit")))
    response = {
        "comparison_kind": kind,
        "eligible": False,
        "reason": None,
        "validity_state": state,
        "missing_inconsistency": first_valid != second_valid,
        "unit_match": unit_match,
        "feature_version_match": version_match,
        "semantic_match": semantic_match,
        "signed_direction": f"{model_order[0]}_minus_{model_order[1]}",
        "signed_cross_model_disagreement": None,
        "absolute_cross_model_disagreement": None,
        "category_agreement": None,
    }
    if not first_valid or not second_valid:
        response["reason"] = "one_or_both_features_invalid"
        return response
    if not unit_match:
        response["reason"] = "unit_mismatch"
        return response
    if not version_match:
        response["reason"] = "feature_version_mismatch"
        return response
    if not semantic_match:
        response["reason"] = "feature_semantics_mismatch"
        return response
    first_value = _finite_scalar(first.get("value"))
    second_value = _finite_scalar(second.get("value"))
    if first_value is None or second_value is None:
        response["reason"] = "non_scalar_or_non_finite_feature_value"
        return response
    response["eligible"] = True
    response["reason"] = "paired_same_boundary_same_unit_same_semantics"
    if kind == "categorical":
        response["category_agreement"] = first_value == second_value
        return response
    difference = (
        _wrapped_angle_difference_deg(first_value, second_value)
        if kind == "circular_angle"
        else first_value - second_value
    )
    response["signed_cross_model_disagreement"] = round(float(difference), 8)
    response["absolute_cross_model_disagreement"] = round(abs(float(difference)), 8)
    return response


def _paired_validity(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(item["comparison"]["validity_state"] for item in records)
    total = len(records)
    mismatch = counts["model_a_only"] + counts["model_b_only"]
    return {
        "pair_count": total,
        "both_valid_count": counts["both_valid"],
        "model_a_only_count": counts["model_a_only"],
        "model_b_only_count": counts["model_b_only"],
        "neither_valid_count": counts["neither_valid"],
        "both_valid_rate": round(counts["both_valid"] / max(total, 1), 8),
        "missing_inconsistency_count": mismatch,
        "missing_inconsistency_rate": round(mismatch / max(total, 1), 8),
    }


def _aggregate_feature_pairs(
    records: list[dict[str, Any]],
    *,
    model_order: tuple[str, str],
) -> dict[str, Any]:
    if not records:
        raise ValueError("feature aggregation requires at least one record")
    feature_name = records[0]["feature_name"]
    kinds = {item["comparison"]["comparison_kind"] for item in records}
    units = {
        item["models"][model_order[0]]["unit"]
        for item in records
        if item["models"][model_order[0]].get("unit") is not None
    }
    kind = next(iter(kinds)) if len(kinds) == 1 else "incompatible"
    comparisons = [
        item["comparison"] for item in records if item["comparison"]["eligible"]
    ]
    reasons = Counter(item["comparison"]["reason"] for item in records)
    metric: dict[str, Any] = {
        "feature_name": feature_name,
        "unit": next(iter(units)) if len(units) == 1 else None,
        "comparison_kind": kind,
        "paired_validity": _paired_validity(records),
        "comparison_eligibility": {
            "eligible_count": len(comparisons),
            "pair_count": len(records),
            "eligible_rate": round(len(comparisons) / max(len(records), 1), 8),
            "reason_counts": dict(sorted(reasons.items())),
        },
        "cross_model_disagreement": {
            "status": "unavailable",
            "accuracy_claim": False,
            "semantics": (
                "descriptive paired cross-model disagreement on identical event "
                "boundaries; neither model is ground truth"
            ),
        },
    }
    disagreement = metric["cross_model_disagreement"]
    if kind == "categorical":
        agreements = [bool(item["category_agreement"]) for item in comparisons]
        if agreements:
            disagreement.update(
                {
                    "status": "evaluated",
                    "agreement_count": sum(agreements),
                    "disagreement_count": len(agreements) - sum(agreements),
                    "agreement_rate": round(sum(agreements) / len(agreements), 8),
                    "signed_cross_model_bias": None,
                    "mean_absolute_cross_model_disagreement": None,
                    "p95_absolute_cross_model_disagreement": None,
                }
            )
        else:
            disagreement.update(
                {
                    "agreement_count": 0,
                    "disagreement_count": 0,
                    "agreement_rate": None,
                    "signed_cross_model_bias": None,
                    "mean_absolute_cross_model_disagreement": None,
                    "p95_absolute_cross_model_disagreement": None,
                }
            )
        return metric
    signed = [
        float(item["signed_cross_model_disagreement"])
        for item in comparisons
        if item["signed_cross_model_disagreement"] is not None
    ]
    absolute = [abs(value) for value in signed]
    if signed:
        disagreement.update(
            {
                "status": "evaluated",
                "signed_direction": f"{model_order[0]}_minus_{model_order[1]}",
                "signed_cross_model_bias": round(float(np.mean(signed)), 8),
                "mean_absolute_cross_model_disagreement": round(
                    float(np.mean(absolute)), 8
                ),
                "p95_absolute_cross_model_disagreement": round(
                    float(np.percentile(absolute, 95)), 8
                ),
                "wrapped_angular_difference": kind == "circular_angle",
            }
        )
    else:
        disagreement.update(
            {
                "signed_direction": f"{model_order[0]}_minus_{model_order[1]}",
                "signed_cross_model_bias": None,
                "mean_absolute_cross_model_disagreement": None,
                "p95_absolute_cross_model_disagreement": None,
                "wrapped_angular_difference": kind == "circular_angle",
            }
        )
    return metric


def _indicator_validity_state(
    model_order: tuple[str, str],
    feature_records: list[dict[str, Any]],
) -> str:
    valid = {
        model: bool(feature_records)
        and all(item["models"][model]["valid"] for item in feature_records)
        for model in model_order
    }
    return _validity_state(valid[model_order[0]], valid[model_order[1]])


def _validate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        raise ValueError("common events must not be empty")
    required = {"event_id", "person_track_id", "event_code", "start_ms", "end_ms"}
    seen: set[str] = set()
    normalized = []
    for event in events:
        missing = sorted(required - set(event))
        if missing:
            raise ValueError(f"common event missing fields: {missing}")
        event_id = str(event["event_id"])
        if event_id in seen:
            raise ValueError(f"duplicate common event_id: {event_id}")
        seen.add(event_id)
        start_ms, end_ms = int(event["start_ms"]), int(event["end_ms"])
        if end_ms <= start_ms:
            raise ValueError(f"invalid common event interval: {event_id}")
        normalized.append(event)
    return sorted(normalized, key=lambda item: (int(item["start_ms"]), str(item["event_id"])))


def _registry_index(registry: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    indicators = registry.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("feasibility registry must contain indicators")
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for indicator in indicators:
        indicator_id = str(indicator["indicator_id"])
        if indicator_id in seen:
            raise ValueError(f"duplicate indicator_id: {indicator_id}")
        seen.add(indicator_id)
        required_features = measurement_feature_names(indicator)
        unknown = sorted(set(required_features) - set(FEATURE_DEFINITIONS))
        if unknown:
            raise ValueError(f"unregistered required features for {indicator_id}: {unknown}")
        # Fixed-boundary Pose A/B evaluates only model-observable measurement
        # features.  Context features (for example an independently annotated
        # tactical target direction) remain mandatory for scoring, but are not
        # outputs of either Pose model and must not make a Pose comparison
        # impossible to construct.
        measurement_indicator = dict(indicator)
        measurement_indicator["required_features"] = required_features
        by_event[indicator_id.split("-", 1)[0]].append(measurement_indicator)
    return by_event


def _model_source_for_report(source: Mapping[str, Any]) -> dict[str, Any]:
    required = ("label", "model_sha256", "frames", "primary_timeline")
    missing = [name for name in required if name not in source]
    if missing:
        raise ValueError(f"model source missing fields: {missing}")
    model_sha = str(source["model_sha256"]).upper()
    if len(model_sha) != _HASH_LENGTH or any(c not in "0123456789ABCDEF" for c in model_sha):
        raise ValueError("model_sha256 must be a 64-character hexadecimal digest")
    return {
        **source,
        "model_sha256": model_sha,
        "feature_library_version": FEATURE_LIBRARY_VERSION,
    }


def compare_fixed_boundary_pose_features(
    *,
    common_events: list[dict[str, Any]],
    registry: Mapping[str, Any],
    model_sequences: Mapping[str, PoseSequence],
    model_sources: Mapping[str, Mapping[str, Any]],
    diagnostics_by_model_event: Mapping[str, Mapping[str, dict[str, Any]]],
    boundary_source: Mapping[str, Any],
    registry_source: Mapping[str, Any],
    source_id: str,
) -> dict[str, Any]:
    """Compare two pose models on immutable public event boundaries.

    This is deliberately not a truth evaluator.  It reports paired coverage
    and cross-model disagreement only; no model is selected as the reference,
    and no threshold or grade is produced.
    """

    events = _validate_events(common_events)
    by_event = _registry_index(registry)
    if len(model_sequences) != 2:
        raise ValueError("fixed-boundary comparison requires exactly two models")
    model_order = tuple(model_sequences)
    if set(model_sources) != set(model_order):
        raise ValueError("model_sources keys must match model_sequences")
    if set(diagnostics_by_model_event) != set(model_order):
        raise ValueError("diagnostic keys must match model_sequences")
    first_sequence, second_sequence = (model_sequences[name] for name in model_order)
    if not np.array_equal(first_sequence.timestamp_ms, second_sequence.timestamp_ms):
        raise ValueError("model inputs must use the same timestamp_ms grid")
    if not np.array_equal(first_sequence.source_frames, second_sequence.source_frames):
        raise ValueError("model inputs must use the same source-frame grid")
    model_report_sources = {
        name: _model_source_for_report(model_sources[name]) for name in model_order
    }

    feature_versions: dict[str, str] = {}
    feature_pair_details: list[dict[str, Any]] = []
    event_details: list[dict[str, Any]] = []
    indicator_event_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unique_feature_records: dict[tuple[str, str], dict[str, Any]] = {}
    model_feature_results: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
        name: {} for name in model_order
    }

    for event in events:
        event_code = str(event["event_code"])
        indicators = by_event.get(event_code, [])
        names = sorted(
            {
                name
                for indicator in indicators
                for name in indicator["required_features"]
            }
        )
        interval = EventInterval(
            event_id=str(event["event_id"]),
            event_code=event_code,
            start_ms=int(event["start_ms"]),
            end_ms=int(event["end_ms"]),
            person_track_id=int(event["person_track_id"]),
            key_phases=event.get("key_phases_ms"),
        )
        for model_name in model_order:
            results = compute_event_features(model_sequences[model_name], interval, names)
            for result in results:
                payload = _feature_payload(result, result.feature_name)
                model_feature_results[model_name][
                    (interval.event_id, result.feature_name)
                ] = payload
                previous = feature_versions.setdefault(
                    result.feature_name, result.feature_version
                )
                if previous != result.feature_version:
                    raise ValueError(
                        f"inconsistent feature version for {result.feature_name}"
                    )

        event_indicator_details = []
        for indicator in indicators:
            indicator_id = str(indicator["indicator_id"])
            required_features = [str(name) for name in indicator["required_features"]]
            pairs = []
            for feature_name in required_features:
                key = (interval.event_id, feature_name)
                models = {
                    model_name: model_feature_results[model_name][key]
                    for model_name in model_order
                }
                comparison = _compare_feature_pair(
                    feature_name,
                    models[model_order[0]],
                    models[model_order[1]],
                    model_order=model_order,
                )
                unique = unique_feature_records.get(key)
                if unique is None:
                    unique = {
                        "event_id": interval.event_id,
                        "event_code": interval.event_code,
                        "person_track_id": interval.person_track_id,
                        "start_ms": interval.start_ms,
                        "end_ms": interval.end_ms,
                        "feature_name": feature_name,
                        "indicator_ids": [],
                        "models": models,
                        "comparison": comparison,
                    }
                    unique_feature_records[key] = unique
                    feature_pair_details.append(unique)
                unique["indicator_ids"].append(indicator_id)
                pairs.append(unique)
            indicator_state = _indicator_validity_state(model_order, pairs)
            model_statuses = {}
            for model_name in model_order:
                valid_count = sum(item["models"][model_name]["valid"] for item in pairs)
                confidences = [
                    float(item["models"][model_name]["confidence"])
                    for item in pairs
                    if item["models"][model_name]["valid"]
                ]
                model_statuses[model_name] = {
                    "measurement_status": (
                        "measured" if valid_count == len(pairs) else "unavailable"
                    ),
                    "required_feature_count": len(pairs),
                    "valid_required_feature_count": valid_count,
                    "mean_valid_feature_confidence": (
                        round(float(np.mean(confidences)), 8) if confidences else None
                    ),
                    "track_diagnostics": diagnostics_by_model_event[model_name].get(
                        interval.event_id
                    ),
                    "event_quality_gate": {
                        "status": "not_evaluated",
                        "measurement_allowed": None,
                        "scoring_allowed": None,
                        "reason": (
                            "model-specific event semantics and event quality flags were "
                            "not regenerated on the externally fixed boundary"
                        ),
                        "boundary_source_quality_flags_reused": False,
                    },
                }
            record = {
                "event_id": interval.event_id,
                "indicator_id": indicator_id,
                "feasibility_level": indicator.get("feasibility_level"),
                "required_features": required_features,
                "models": model_statuses,
                "paired_validity": {
                    "state": indicator_state,
                    "both_measured": indicator_state == "both_valid",
                    "missing_inconsistency": indicator_state
                    in {"model_a_only", "model_b_only"},
                },
            }
            indicator_event_records[indicator_id].append(record)
            event_indicator_details.append(record)
        event_details.append(
            {
                "boundary": {
                    "event_id": interval.event_id,
                    "event_code": interval.event_code,
                    "person_track_id": interval.person_track_id,
                    "start_ms": interval.start_ms,
                    "end_ms": interval.end_ms,
                    "key_phases_ms": event.get("key_phases_ms"),
                },
                "boundary_source_annotations": {
                    "source_record_sha256": _canonical_sha256(event),
                    "confidence": event.get("confidence"),
                    "boundary_uncertainty_ms": event.get("boundary_uncertainty_ms"),
                    "quality_flags": list(event.get("quality_flags", [])),
                    "provenance": _boundary_provenance(event),
                    "used_as_model_quality_flags": False,
                },
                "indicators": event_indicator_details,
            }
        )

    feature_pair_details.sort(key=lambda item: (item["event_id"], item["feature_name"]))
    for item in feature_pair_details:
        item["indicator_ids"] = sorted(set(item["indicator_ids"]))
    feature_metric_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in feature_pair_details:
        feature_metric_groups[record["feature_name"]].append(record)
    feature_metrics = {
        name: _aggregate_feature_pairs(records, model_order=model_order)
        for name, records in sorted(feature_metric_groups.items())
    }
    indicator_metrics = {}
    for indicator_id, records in sorted(indicator_event_records.items()):
        state_counts = Counter(item["paired_validity"]["state"] for item in records)
        total = len(records)
        indicator_feature_names = records[0]["required_features"] if records else []
        per_feature = {}
        for feature_name in indicator_feature_names:
            selected = [
                item
                for item in feature_pair_details
                if indicator_id in item["indicator_ids"]
                and item["feature_name"] == feature_name
            ]
            per_feature[feature_name] = _aggregate_feature_pairs(
                selected, model_order=model_order
            )
        indicator_metrics[indicator_id] = {
            "event_pair_count": total,
            "required_features": indicator_feature_names,
            "paired_validity": {
                "both_measured_count": state_counts["both_valid"],
                "model_a_only_count": state_counts["model_a_only"],
                "model_b_only_count": state_counts["model_b_only"],
                "neither_measured_count": state_counts["neither_valid"],
                "both_measured_rate": round(
                    state_counts["both_valid"] / max(total, 1), 8
                ),
                "missing_inconsistency_count": (
                    state_counts["model_a_only"] + state_counts["model_b_only"]
                ),
            },
            "event_quality_gate": {
                "status": "not_evaluated",
                "event_pair_count": total,
                "reason": "fixed boundaries do not carry symmetric model-specific event quality",
            },
            "feature_metrics": per_feature,
        }

    all_indicator_records = [
        item for records in indicator_event_records.values() for item in records
    ]
    model_summaries = {}
    for model_name in model_order:
        measured = sum(
            item["models"][model_name]["measurement_status"] == "measured"
            for item in all_indicator_records
        )
        model_summaries[model_name] = {
            **model_report_sources[model_name],
            "indicator_event_pair_count": len(all_indicator_records),
            "measured_indicator_event_count": measured,
            "unavailable_indicator_event_count": len(all_indicator_records) - measured,
            "event_quality_gate_status": "not_evaluated",
        }

    boundary_kind = str(boundary_source.get("kind", "external"))
    boundary_truth_status = (
        "candidate_source_not_truth"
        if boundary_kind == "candidate"
        else "manual_annotation_supplied_but_not_pose_model_truth"
        if boundary_kind == "manual"
        else "external_boundary_source_not_assumed_truth"
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": FIXED_BOUNDARY_AB_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "evaluated_fixed_boundary_cross_model_disagreement",
        "source_id": source_id,
        "comparison_semantics": {
            "accuracy_claim": False,
            "ground_truth_model": None,
            "event_boundary_truth_status": boundary_truth_status,
            "numeric_metric_semantics": (
                "cross_model_disagreement_not_truth_error_or_accuracy"
            ),
            "categorical_metric_semantics": "paired_agreement_rate_only",
            "grade_generated": False,
            "scoring_threshold_generated": False,
        },
        "boundary_source": {
            **boundary_source,
            "kind": boundary_kind,
            "truth_status": boundary_truth_status,
            "event_count": len(events),
            "event_ids": [str(item["event_id"]) for item in events],
            "immutable_fields": [
                "event_id",
                "event_code",
                "person_track_id",
                "start_ms",
                "end_ms",
                "key_phases_ms",
            ],
        },
        "registry_source": {
            **registry_source,
            "registry_version": registry.get("registry_version"),
            "indicator_count": len(registry.get("indicators", [])),
            "indicator_ids": sorted(
                str(item["indicator_id"]) for item in registry.get("indicators", [])
            ),
        },
        "model_order": list(model_order),
        "models": model_summaries,
        "input_alignment": {
            "same_timestamp_grid": True,
            "same_source_frame_grid": True,
            "same_common_event_records": True,
            "event_person_track_id_is_shared_semantic_primary_id": True,
        },
        "feature_versions": dict(sorted(feature_versions.items())),
        "indicator_metrics": indicator_metrics,
        "feature_metrics": feature_metrics,
        "events": event_details,
        "feature_pairs": feature_pair_details,
        "assertions": {
            "accuracy_claim": False,
            "neither_model_used_as_truth": True,
            "same_boundaries_for_both_models": True,
            "only_both_valid_same_unit_same_semantics_pairs_enter_numeric_disagreement": all(
                (
                    not item["comparison"]["eligible"]
                    or (
                        item["comparison"]["validity_state"] == "both_valid"
                        and item["comparison"]["unit_match"]
                        and item["comparison"]["feature_version_match"]
                        and item["comparison"]["semantic_match"]
                    )
                )
                for item in feature_pair_details
            ),
            "event_quality_gate_status": "not_evaluated",
            "boundary_source_quality_flags_reused": False,
            "grade_generated": False,
            "scoring_threshold_generated": False,
        },
    }
    for sequence in model_sequences.values():
        clear_feature_cache(sequence)
    validate_fixed_boundary_ab_report(report)
    return report


def _timeline_event_alignment(
    timeline: list[dict[str, Any]], event: Mapping[str, Any]
) -> bool:
    primary_ids = {
        int(item["primary_player_id"])
        for item in timeline
        if int(event["start_ms"]) <= int(item["timestamp_ms"]) <= int(event["end_ms"])
        and item.get("primary_player_id") is not None
    }
    return primary_ids == {int(event["person_track_id"])}


def _observed_pose_formats(records: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(pose["keypoint_format"])
            for record in records
            for pose in record.get("poses", [])
            if pose.get("keypoint_format")
        }
    )


def evaluate_fixed_boundary_pose_ab_files(
    *,
    events_path: str | Path,
    registry_path: str | Path,
    model_inputs: Mapping[str, Mapping[str, Any]],
    boundary_source_kind: str,
    boundary_source_label: str,
    source_id: str,
) -> dict[str, Any]:
    events_path = Path(events_path).resolve()
    registry_path = Path(registry_path).resolve()
    events = _validate_events(_load_jsonl(events_path))
    registry = load_feasibility_registry(registry_path)
    if len(model_inputs) != 2:
        raise ValueError("model_inputs must contain exactly two models")
    sequences: dict[str, PoseSequence] = {}
    sources: dict[str, dict[str, Any]] = {}
    diagnostics: dict[str, dict[str, dict[str, Any]]] = {}
    for model_name, raw_input in model_inputs.items():
        frames_path = Path(raw_input["frames_path"]).resolve()
        timeline_path = Path(raw_input["primary_timeline_path"]).resolve()
        records = _load_jsonl(frames_path)
        timeline = _load_jsonl(timeline_path)
        for event in events:
            if not _timeline_event_alignment(timeline, event):
                raise ValueError(
                    f"{model_name} timeline primary_player_id does not match "
                    f"common event person_track_id for {event['event_id']}"
                )
        sequences[model_name] = pose_sequence_from_records(records, timeline)
        diagnostics[model_name] = {
            str(event["event_id"]): diagnose_primary_timeline(
                records,
                timeline,
                start_ms=int(event["start_ms"]),
                end_ms=int(event["end_ms"]),
            )
            for event in events
        }
        sources[model_name] = {
            "label": model_name,
            "model_sha256": str(raw_input["model_sha256"]).upper(),
            "pose_backend": raw_input.get("pose_backend", "unknown"),
            "pose_profile": raw_input.get("pose_profile", model_name),
            "native_keypoint_formats_observed": _observed_pose_formats(records),
            "frames": {
                "path": str(frames_path),
                "sha256": _sha256(frames_path),
            },
            "primary_timeline": {
                "path": str(timeline_path),
                "sha256": _sha256(timeline_path),
            },
        }
    return compare_fixed_boundary_pose_features(
        common_events=events,
        registry=registry,
        model_sequences=sequences,
        model_sources=sources,
        diagnostics_by_model_event=diagnostics,
        boundary_source={
            "kind": boundary_source_kind,
            "label": boundary_source_label,
            "path": str(events_path),
            "sha256": _sha256(events_path),
        },
        registry_source={
            "path": str(registry_path),
            "sha256": _sha256(registry_path),
        },
        source_id=source_id,
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HASH_LENGTH
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _walk_prohibited_scoring_keys(
    value: Any,
    ancestors: tuple[str, ...] = (),
):
    """Find scoring outputs while allowing auditable algorithm provenance.

    Feature and event algorithms legitimately record adaptive values named
    ``threshold`` inside provenance.  Those are measurement/candidate
    diagnostics, not A-E calibration thresholds.  Formal grades and
    ``threshold_version`` remain forbidden everywhere; bare threshold assets
    are only allowed beneath an explicitly named provenance object.
    """

    if isinstance(value, dict):
        for key, nested in value.items():
            name = str(key)
            if name in {"grade", "threshold_version"}:
                yield name
            elif (
                name in {"threshold", "thresholds"}
                and "provenance" not in ancestors
            ):
                yield name
            yield from _walk_prohibited_scoring_keys(
                nested,
                ancestors + (name,),
            )
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_prohibited_scoring_keys(nested, ancestors)


def validate_fixed_boundary_ab_report(report: Mapping[str, Any]) -> None:
    """Strict invariant validator used in addition to the JSON Schema."""

    if report.get("schema_version") != "1.0.0":
        raise ValueError("unsupported fixed-boundary A/B schema_version")
    if report.get("comparison_semantics", {}).get("accuracy_claim") is not False:
        raise ValueError("fixed-boundary A/B must set accuracy_claim=false")
    boundary = report.get("boundary_source", {})
    if not boundary.get("path") or not _is_sha256(boundary.get("sha256")):
        raise ValueError("boundary source path and sha256 are required")
    registry = report.get("registry_source", {})
    if not registry.get("path") or not _is_sha256(registry.get("sha256")):
        raise ValueError("registry source path and sha256 are required")
    model_order = report.get("model_order")
    models = report.get("models")
    if not isinstance(model_order, list) or len(model_order) != 2:
        raise ValueError("model_order must contain exactly two labels")
    if not isinstance(models, dict) or set(models) != set(model_order):
        raise ValueError("models must match model_order")
    for model_name in model_order:
        model = models[model_name]
        if not _is_sha256(model.get("model_sha256")):
            raise ValueError(f"{model_name} model_sha256 is required")
        for source_name in ("frames", "primary_timeline"):
            source = model.get(source_name, {})
            if not source.get("path") or not _is_sha256(source.get("sha256")):
                raise ValueError(f"{model_name} {source_name} path/sha256 is required")
    feature_versions = report.get("feature_versions")
    if not isinstance(feature_versions, dict) or not feature_versions:
        raise ValueError("feature_versions must be a non-empty mapping")
    if any(not isinstance(value, str) or not value for value in feature_versions.values()):
        raise ValueError("every feature version must be a non-empty string")
    indicator_metrics = report.get("indicator_metrics")
    feature_metrics = report.get("feature_metrics")
    if not isinstance(indicator_metrics, dict) or not indicator_metrics:
        raise ValueError("indicator_metrics must be non-empty")
    if not isinstance(feature_metrics, dict) or not feature_metrics:
        raise ValueError("feature_metrics must be non-empty")
    for metric in indicator_metrics.values():
        if "paired_validity" not in metric:
            raise ValueError("indicator metric missing paired_validity")
    for metric in feature_metrics.values():
        if "paired_validity" not in metric:
            raise ValueError("feature metric missing paired_validity")
        if metric.get("cross_model_disagreement", {}).get("accuracy_claim") is not False:
            raise ValueError("feature disagreement must set accuracy_claim=false")
    event_boundaries = {
        item["boundary"]["event_id"]: item["boundary"]
        for item in report.get("events", [])
    }
    if set(event_boundaries) != set(boundary.get("event_ids", [])):
        raise ValueError("boundary event IDs do not match event details")
    for pair in report.get("feature_pairs", []):
        if pair.get("event_id") not in event_boundaries:
            raise ValueError("feature pair references an unknown common event")
        pair_models = pair.get("models")
        if not isinstance(pair_models, dict) or set(pair_models) != set(model_order):
            raise ValueError("feature pair models must match model_order")
        for model_name in model_order:
            payload = pair_models[model_name]
            required = {
                "valid",
                "confidence",
                "provenance",
                "source_frames",
                "feature_version",
                "unit",
                "semantic_signature_sha256",
            }
            if not required.issubset(payload):
                raise ValueError("feature model payload lacks validity/version/evidence")
            if not _is_sha256(payload["semantic_signature_sha256"]):
                raise ValueError("invalid feature semantic signature")
        comparison = pair.get("comparison", {})
        if comparison.get("eligible"):
            if comparison.get("validity_state") != "both_valid":
                raise ValueError("eligible disagreement must have both features valid")
            if not all(
                comparison.get(name)
                for name in ("unit_match", "feature_version_match", "semantic_match")
            ):
                raise ValueError("eligible disagreement requires matching semantics")
            if comparison.get("comparison_kind") == "categorical":
                if comparison.get("category_agreement") is None:
                    raise ValueError("categorical pair must report agreement")
                if comparison.get("signed_cross_model_disagreement") is not None:
                    raise ValueError("categorical pair must not report continuous bias")
            else:
                absolute = comparison.get("absolute_cross_model_disagreement")
                if absolute is None:
                    raise ValueError("eligible numeric pair lacks absolute disagreement")
                if (
                    comparison.get("comparison_kind") == "circular_angle"
                    and float(absolute) > 180.0
                ):
                    raise ValueError("wrapped angular disagreement exceeds 180 degrees")
    prohibited = sorted(set(_walk_prohibited_scoring_keys(report)))
    if prohibited:
        raise ValueError(f"fixed-boundary A/B contains prohibited scoring keys: {prohibited}")
    assertions = report.get("assertions", {})
    if assertions.get("accuracy_claim") is not False:
        raise ValueError("report assertion accuracy_claim must be false")
    if assertions.get("grade_generated") is not False:
        raise ValueError("fixed-boundary A/B must not generate grades")
    if assertions.get("scoring_threshold_generated") is not False:
        raise ValueError("fixed-boundary A/B must not generate scoring thresholds")


__all__ = [
    "FIXED_BOUNDARY_AB_VERSION",
    "compare_fixed_boundary_pose_features",
    "evaluate_fixed_boundary_pose_ab_files",
    "validate_fixed_boundary_ab_report",
]
