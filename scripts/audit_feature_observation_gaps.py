#!/usr/bin/env python3
"""Audit unavailable required features without changing measurement or scoring gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rallymate_scoring.feasibility import measurement_feature_names
from rallymate_scoring.quality_policy import (
    evaluate_indicator_event_quality,
    indicator_event_quality_flags,
)


SCHEMA_VERSION = "1.1.0"
REPORT_VERSION = "feature-observation-gap-audit-v1.1.0"

RECOVERY_POLICY = {
    "valid_fraction_below_quality_gate": {
        "class": "pose_observation_coverage_review",
        "required_action": "review_or_manually_correct_event_pose_observations_then_recompute",
    },
    "bilateral_rise_proxy_not_observable": {
        "class": "bilateral_foot_observation_or_boundary_review",
        "required_action": "review_bilateral_foot_visibility_and_manual_event_boundary_then_recompute",
    },
    "insufficient_variability_samples": {
        "class": "event_phase_and_temporal_observation_review",
        "required_action": "review_manual_event_phase_and_temporal_pose_samples_then_recompute",
    },
    "insufficient_valid_speed_and_angular_velocity_samples": {
        "class": "event_phase_and_temporal_observation_review",
        "required_action": "review_manual_event_phase_and_temporal_pose_samples_then_recompute",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_jsonl(path: Path, *, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{label} row {line_number} is not an object")
        rows.append(value)
    if not rows:
        raise ValueError(f"{label} input is empty")
    return rows


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _recovery(reason: str) -> dict[str, str]:
    return RECOVERY_POLICY.get(
        reason,
        {
            "class": "manual_feature_evidence_review",
            "required_action": "review_feature_evidence_and_contract_then_recompute",
        },
    )


def _hard_fail_recovery(flag: str) -> dict[str, str]:
    if flag.startswith("required_phase_missing:") or flag == "restabilization_not_observed":
        return {
            "class": "manual_event_phase_truth_required",
            "required_action": "annotate_and_validate_the_required_event_phase_then_recompute",
        }
    if flag == "primary_track_coverage_low":
        return {
            "class": "primary_identity_and_track_truth_required",
            "required_action": "review_primary_identity_continuity_and_track_coverage_then_recompute",
        }
    return {
        "class": "measurement_quality_truth_required",
        "required_action": "review_the_measurement_quality_evidence_then_recompute",
    }


def _raw_observation_counts(value: Any) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if "timestamp_ms" in item and "value" in item:
                samples.append(item)
            else:
                for child in item.values():
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    finite = 0
    null = 0
    frames: set[int] = set()
    timestamps: list[int] = []
    for sample in samples:
        raw = sample.get("value")
        values = raw if isinstance(raw, list) else [raw]
        if raw is None or not any(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) for v in values):
            null += 1
        else:
            finite += 1
        if isinstance(sample.get("source_frame"), int):
            frames.add(int(sample["source_frame"]))
        if isinstance(sample.get("timestamp_ms"), int):
            timestamps.append(int(sample["timestamp_ms"]))
    return {
        "series_sample_count": len(samples),
        "finite_sample_count": finite,
        "null_sample_count": null,
        "observed_source_frame_count": len(frames),
        "start_ms": min(timestamps) if timestamps else None,
        "end_ms": max(timestamps) if timestamps else None,
    }


def _selected_diagnostics(feature: dict[str, Any]) -> dict[str, Any]:
    provenance = feature.get("provenance") if isinstance(feature.get("provenance"), dict) else {}
    smoothed = feature.get("smoothed_value")
    summary = smoothed.get("summary", {}) if isinstance(smoothed, dict) else {}
    wanted = (
        "duration_observation_status",
        "complete_bilateral_observation",
        "temporal_coverage_complete",
        "max_observed_gap_ms",
        "allowed_max_gap_ms",
        "fallback_status",
    )
    return {
        "valid_fraction": provenance.get("valid_fraction"),
        "required_joints_used": sorted(str(item) for item in provenance.get("required_joints_used", []) if isinstance(item, str)),
        "measurement_status": provenance.get("measurement_status"),
        "raw_observation_counts": _raw_observation_counts(feature.get("raw_value")),
        "feature_summary": {key: summary[key] for key in wanted if key in summary},
    }


def _registry_indicators(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indicators = registry.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("feasibility registry has no indicators")
    result: dict[str, dict[str, Any]] = {}
    for item in indicators:
        indicator_id = str(item.get("indicator_id", ""))
        if not indicator_id or indicator_id in result:
            raise ValueError("feasibility registry indicator ids are empty or duplicated")
        result[indicator_id] = item
    return result


def _comparison_maps(
    paths: Iterable[Path],
    *,
    current_profile: str,
    events_sha256: str,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, str]]]:
    sources: list[dict[str, Any]] = []
    statuses: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    seen_profiles: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("comparison_semantics", {}).get("accuracy_claim") is not False:
            raise ValueError("profile comparison must not claim accuracy")
        if str(payload.get("boundary_source", {}).get("sha256", "")).upper() != events_sha256.upper():
            raise ValueError("profile comparison does not use the audited event boundaries")
        order = [str(item) for item in payload.get("model_order", [])]
        if len(order) != 2 or order[0] != current_profile:
            raise ValueError("profile comparison must place the audited profile first")
        alternative = order[1]
        if alternative in seen_profiles:
            raise ValueError(f"duplicate alternative profile: {alternative}")
        seen_profiles.add(alternative)
        for event in payload.get("events", []):
            event_id = str(event.get("boundary", {}).get("event_id", ""))
            for indicator in event.get("indicators", []):
                indicator_id = str(indicator.get("indicator_id", ""))
                models = indicator.get("models", {})
                if current_profile not in models or alternative not in models:
                    raise ValueError("profile comparison indicator is missing a model")
                statuses[(event_id, indicator_id)][alternative] = str(models[alternative].get("measurement_status"))
        sources.append(
            {
                **_source(path),
                "current_profile": current_profile,
                "alternative_profile": alternative,
                "boundary_source_sha256": events_sha256,
                "semantics": "same_candidate_boundary_observability_only_not_accuracy_or_truth",
            }
        )
    return sources, statuses


def audit_feature_observation_gaps(
    *,
    indicator_features_path: Path,
    features_path: Path,
    events_path: Path,
    summary_path: Path,
    registry_path: Path,
    comparison_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    indicator_rows = _read_jsonl(indicator_features_path, label="indicator features")
    feature_rows = _read_jsonl(features_path, label="features")
    event_rows = _read_jsonl(events_path, label="events")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict) or not isinstance(registry, dict):
        raise ValueError("summary and registry must be objects")
    registry_by_id = _registry_indicators(registry)
    registry_version = str(registry.get("registry_version", registry.get("version", "")))
    summary_registry = str(summary.get("model_versions", {}).get("feasibility_registry", ""))
    if registry_version != summary_registry:
        raise ValueError("summary and registry versions do not match")
    video_id = str(summary.get("video_id", ""))
    current_profile = str(summary.get("model_versions", {}).get("pose_profile", ""))
    if not video_id or not current_profile:
        raise ValueError("summary lacks video or pose profile provenance")

    event_index: dict[str, dict[str, Any]] = {}
    for event in event_rows:
        event_id = str(event.get("event_id", ""))
        if not event_id or event_id in event_index:
            raise ValueError("events contain an empty or duplicate event_id")
        event_index[event_id] = event

    feature_index: dict[tuple[str, str], dict[str, Any]] = {}
    for feature in feature_rows:
        key = (str(feature.get("event_id", "")), str(feature.get("feature_name", "")))
        if not all(key) or key in feature_index:
            raise ValueError(f"features contain an empty or duplicate key: {key}")
        feature_index[key] = feature

    comparison_sources, alternative_statuses = _comparison_maps(
        comparison_paths,
        current_profile=current_profile,
        events_sha256=_sha256(events_path),
    )

    identities: set[tuple[str, str]] = set()
    measured_count = 0
    feature_vector_complete_count = 0
    feature_vector_incomplete_count = 0
    measurement_hard_fail_count = 0
    hard_fail_only_count = 0
    hard_fail_with_incomplete_count = 0
    unavailable_rows: list[dict[str, Any]] = []
    invalid_occurrences = 0
    unique_failures: dict[tuple[str, str], dict[str, Any]] = {}
    affected_indicators_by_failure: dict[tuple[str, str], set[str]] = defaultdict(set)
    reason_indicator_instances: dict[str, set[tuple[str, str]]] = defaultdict(set)
    reason_events: dict[str, set[str]] = defaultdict(set)
    indicator_metrics: dict[str, Counter[str]] = defaultdict(Counter)
    hard_fail_indicator_instances: dict[str, set[tuple[str, str]]] = defaultdict(set)
    hard_fail_events: dict[str, set[str]] = defaultdict(set)

    for row in indicator_rows:
        event_id = str(row.get("event_id", ""))
        indicator_id = str(row.get("indicator_id", ""))
        identity = (event_id, indicator_id)
        if identity in identities:
            raise ValueError(f"duplicate event/indicator record: {identity}")
        identities.add(identity)
        if event_id not in event_index or indicator_id not in registry_by_id:
            raise ValueError(f"indicator record is outside event/registry scope: {identity}")
        required = measurement_feature_names(registry_by_id[indicator_id])
        compact = row.get("features")
        if not isinstance(compact, list) or [str(item.get("feature_name")) for item in compact] != required:
            raise ValueError(f"indicator feature order does not match registry: {identity}")
        status = str(row.get("feature_status"))
        indicator_metrics[indicator_id]["total"] += 1
        indicator_metrics[indicator_id][status] += 1
        invalid = [item for item in compact if item.get("valid") is not True]
        if invalid:
            feature_vector_incomplete_count += 1
            indicator_metrics[indicator_id]["feature_vector_incomplete"] += 1
        else:
            feature_vector_complete_count += 1
            indicator_metrics[indicator_id]["feature_vector_complete"] += 1
        event = event_index[event_id]
        expected_flags = indicator_event_quality_flags(
            event, registry_by_id[indicator_id]
        )
        expected_gate = evaluate_indicator_event_quality(indicator_id, expected_flags)
        if _canonical(row.get("quality_gate")) != _canonical(expected_gate):
            raise ValueError(f"quality gate does not match event and registry: {identity}")
        hard_fail = expected_gate["hard_fail"] is True
        if hard_fail:
            measurement_hard_fail_count += 1
            indicator_metrics[indicator_id]["measurement_hard_fail"] += 1
            if invalid:
                hard_fail_with_incomplete_count += 1
            else:
                hard_fail_only_count += 1
            for flag in expected_gate["hard_fail_flags"]:
                hard_fail_indicator_instances[str(flag)].add(identity)
                hard_fail_events[str(flag)].add(event_id)
        expected_status = (
            "measured"
            if not invalid and expected_gate["measurement_allowed"] is True
            else "unavailable"
        )
        if status != expected_status:
            raise ValueError(
                f"feature status does not match feature validity and quality gate: {identity}"
            )
        if status == "measured":
            measured_count += 1
            continue
        unavailable_rows.append(row)
        indicator_metrics[indicator_id]["invalid_feature_occurrences"] += len(invalid)
        invalid_occurrences += len(invalid)
        for compact_feature in invalid:
            feature_name = str(compact_feature.get("feature_name", ""))
            key = (event_id, feature_name)
            full = feature_index.get(key)
            if full is None:
                raise ValueError(f"missing full feature record: {key}")
            comparable_fields = ("feature_name", "feature_version", "value", "unit", "confidence", "valid", "reason", "source_frames")
            if any(_canonical(compact_feature.get(field)) != _canonical(full.get(field)) for field in comparable_fields):
                raise ValueError(f"compact/full feature mismatch: {key}")
            reason = str(full.get("reason", ""))
            if key in unique_failures and unique_failures[key]["reason"] != reason:
                raise ValueError(f"one event feature has inconsistent reasons: {key}")
            unique_failures[key] = full
            affected_indicators_by_failure[key].add(indicator_id)
            reason_indicator_instances[reason].add(identity)
            reason_events[reason].add(event_id)

    summary_validity = summary.get("indicator_feature_validity", {})
    if not isinstance(summary_validity, dict) or set(summary_validity) != set(registry_by_id):
        raise ValueError("summary indicator feature validity does not match registry")
    summary_measured = sum(int(item.get("valid", 0)) for item in summary_validity.values())
    summary_total = sum(int(item.get("total", 0)) for item in summary_validity.values())
    summary_feature_counts = {"measured": summary_measured, "unavailable": summary_total - summary_measured}
    if summary_feature_counts != {"measured": measured_count, "unavailable": len(unavailable_rows)}:
        raise ValueError("indicator feature counts do not match summary")
    if any(row.get("grade") is not None for row in indicator_rows):
        raise ValueError("feature observation audit cannot consume graded records")

    event_failures: list[dict[str, Any]] = []
    rows_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in unavailable_rows:
        rows_by_event[str(row["event_id"])].append(row)
    for event_id, rows in sorted(rows_by_event.items(), key=lambda item: (int(event_index[item[0]]["start_ms"]), item[0])):
        event = event_index[event_id]
        failures = []
        event_feature_keys: set[tuple[str, str]] = set()
        for row in sorted(rows, key=lambda item: str(item["indicator_id"])):
            indicator_id = str(row["indicator_id"])
            invalid = [item for item in row["features"] if item.get("valid") is not True]
            for item in invalid:
                event_feature_keys.add((event_id, str(item["feature_name"])))
            alternatives = alternative_statuses.get((event_id, indicator_id), {})
            failures.append(
                {
                    "indicator_id": indicator_id,
                    "invalid_required_features": [
                        {"feature_name": str(item["feature_name"]), "reason": str(item["reason"])} for item in invalid
                    ],
                    "quality_gate_status": str(row.get("quality_gate", {}).get("status", "")),
                    "hard_fail": row.get("quality_gate", {}).get("hard_fail") is True,
                    "hard_fail_flags": sorted(
                        str(flag)
                        for flag in row.get("quality_gate", {}).get(
                            "hard_fail_flags", []
                        )
                    ),
                    "measurement_failure_class": (
                        "quality_hard_fail_and_feature_vector_incomplete"
                        if row.get("quality_gate", {}).get("hard_fail") is True
                        and invalid
                        else "quality_hard_fail_only"
                        if row.get("quality_gate", {}).get("hard_fail") is True
                        else "feature_vector_incomplete"
                    ),
                    "scoring_block_flags": sorted(str(flag) for flag in row.get("quality_gate", {}).get("scoring_block_flags", [])),
                    "alternative_profile_measurement_status": dict(sorted(alternatives.items())),
                }
            )
        invalid_features = []
        for key in sorted(event_feature_keys, key=lambda item: item[1]):
            full = unique_failures[key]
            reason = str(full["reason"])
            invalid_features.append(
                {
                    "feature_name": key[1],
                    "feature_version": str(full.get("feature_version", "")),
                    "unit": str(full.get("unit", "")),
                    "reason": reason,
                    "confidence": float(full.get("confidence", 0.0)),
                    "source_frames": [int(value) for value in full.get("source_frames", [])],
                    "affected_indicator_ids": sorted(affected_indicators_by_failure[key]),
                    "recovery_class": _recovery(reason)["class"],
                    "required_action": _recovery(reason)["required_action"],
                    "diagnostics": _selected_diagnostics(full),
                }
            )
        event_failures.append(
            {
                "event_id": event_id,
                "event_code": str(event["event_code"]),
                "person_track_id": int(event["person_track_id"]),
                "start_ms": int(event["start_ms"]),
                "end_ms": int(event["end_ms"]),
                "video_seek_seconds": round(int(event["start_ms"]) / 1000.0, 3),
                "indicator_failures": failures,
                "invalid_features": invalid_features,
            }
        )

    reason_unique_counts = Counter(str(feature["reason"]) for feature in unique_failures.values())
    reason_metrics = []
    for reason, unique_count in reason_unique_counts.most_common():
        policy = _recovery(reason)
        reason_metrics.append(
            {
                "reason": reason,
                "recovery_class": policy["class"],
                "required_action": policy["required_action"],
                "unique_event_feature_count": unique_count,
                "affected_indicator_instance_count": len(reason_indicator_instances[reason]),
                "affected_event_count": len(reason_events[reason]),
                "automatic_recovery_allowed": False,
            }
        )

    hard_fail_metrics = []
    for flag in sorted(
        hard_fail_indicator_instances,
        key=lambda item: (-len(hard_fail_indicator_instances[item]), item),
    ):
        policy = _hard_fail_recovery(flag)
        hard_fail_metrics.append(
            {
                "flag": flag,
                "recovery_class": policy["class"],
                "required_action": policy["required_action"],
                "affected_indicator_instance_count": len(
                    hard_fail_indicator_instances[flag]
                ),
                "affected_event_count": len(hard_fail_events[flag]),
                "automatic_recovery_allowed": False,
            }
        )

    profile_metrics = []
    for source in comparison_sources:
        alternative = source["alternative_profile"]
        gained = []
        regressed = []
        for row in indicator_rows:
            identity = (str(row["event_id"]), str(row["indicator_id"]))
            alternative_status = alternative_statuses.get(identity, {}).get(alternative)
            if alternative_status not in {"measured", "unavailable"}:
                raise ValueError(f"comparison lacks indicator status for {identity}: {alternative}")
            current_status = str(row["feature_status"])
            item = {"event_id": identity[0], "indicator_id": identity[1]}
            if current_status == "unavailable" and alternative_status == "measured":
                gained.append(item)
            elif current_status == "measured" and alternative_status == "unavailable":
                regressed.append(item)
        profile_metrics.append(
            {
                "alternative_profile": alternative,
                "current_unavailable_alternative_measured_count": len(gained),
                "current_measured_alternative_unavailable_count": len(regressed),
                "current_unavailable_alternative_measured_instances": gained,
                "current_measured_alternative_unavailable_instances": regressed,
                "automatic_profile_fallback_allowed": False,
                "accuracy_claim": False,
            }
        )

    indicator_report = {}
    for indicator_id in sorted(registry_by_id):
        counts = indicator_metrics[indicator_id]
        indicator_report[indicator_id] = {
            "indicator_instance_count": counts["total"],
            "measured_count": counts["measured"],
            "unavailable_count": counts["unavailable"],
            "feature_vector_complete_count": counts["feature_vector_complete"],
            "feature_vector_incomplete_count": counts["feature_vector_incomplete"],
            "measurement_hard_fail_count": counts["measurement_hard_fail"],
            "invalid_required_feature_occurrence_count": counts["invalid_feature_occurrences"],
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "audited_no_measurement_or_scoring_gate_change",
        "source": {
            "video_id": video_id,
            "current_profile": current_profile,
            "registry_version": registry_version,
            "indicator_features": _source(indicator_features_path),
            "features": _source(features_path),
            "events": _source(events_path),
            "summary": _source(summary_path),
            "registry": _source(registry_path),
            "profile_comparisons": comparison_sources,
        },
        "counts": {
            "event_count": len(event_rows),
            "indicator_record_count": len(indicator_rows),
            "feature_measured_indicator_count": measured_count,
            "feature_unavailable_indicator_count": len(unavailable_rows),
            "feature_vector_complete_indicator_count": feature_vector_complete_count,
            "feature_vector_incomplete_indicator_count": feature_vector_incomplete_count,
            "measurement_hard_fail_indicator_count": measurement_hard_fail_count,
            "measurement_hard_fail_only_indicator_count": hard_fail_only_count,
            "measurement_hard_fail_with_incomplete_vector_count": hard_fail_with_incomplete_count,
            "affected_event_count": len(rows_by_event),
            "invalid_required_feature_occurrence_count": invalid_occurrences,
            "unique_invalid_event_feature_count": len(unique_failures),
            "grade_count": 0,
            "threshold_count": 0,
        },
        "reason_metrics": reason_metrics,
        "measurement_hard_fail_metrics": hard_fail_metrics,
        "recovery_priority": sorted(
            reason_metrics,
            key=lambda item: (-int(item["unique_event_feature_count"]), str(item["reason"])),
        ),
        "indicator_metrics": indicator_report,
        "profile_alternative_metrics": profile_metrics,
        "event_failures": event_failures,
        "assertions": {
            "required_feature_contract_matches_registry": True,
            "compact_features_match_full_features": True,
            "quality_gates_match_event_and_registry": True,
            "feature_status_respects_vector_and_measurement_gate": True,
            "profile_comparisons_use_identical_candidate_boundaries": True,
            "measurement_gate_modified": False,
            "scoring_gate_modified": False,
            "automatic_profile_fallback_enabled": False,
            "cross_model_counts_are_accuracy_metrics": False,
            "human_ground_truth_still_required": True,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }


def validate_feature_observation_gap_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION or report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported feature observation gap audit version")
    if report.get("status") != "audited_no_measurement_or_scoring_gate_change":
        raise ValueError("feature observation gap audit status is unsafe")
    assertions = report.get("assertions", {})
    for field in (
        "required_feature_contract_matches_registry",
        "compact_features_match_full_features",
        "quality_gates_match_event_and_registry",
        "feature_status_respects_vector_and_measurement_gate",
        "profile_comparisons_use_identical_candidate_boundaries",
        "human_ground_truth_still_required",
    ):
        if assertions.get(field) is not True:
            raise ValueError(f"required audit assertion is false: {field}")
    for field in (
        "measurement_gate_modified",
        "scoring_gate_modified",
        "automatic_profile_fallback_enabled",
        "cross_model_counts_are_accuracy_metrics",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if assertions.get(field) is not False:
            raise ValueError(f"unsafe audit assertion: {field}")
    counts = report.get("counts", {})
    if int(counts.get("indicator_record_count", -1)) != int(counts.get("feature_measured_indicator_count", -1)) + int(counts.get("feature_unavailable_indicator_count", -1)):
        raise ValueError("feature status counts do not cover all indicator records")
    if int(counts.get("affected_event_count", -1)) != len(report.get("event_failures", [])):
        raise ValueError("affected event count does not match event failures")
    total = int(counts.get("indicator_record_count", -1))
    if total != int(counts.get("feature_vector_complete_indicator_count", -1)) + int(
        counts.get("feature_vector_incomplete_indicator_count", -1)
    ):
        raise ValueError("feature vector completeness counts do not cover all records")
    hard_fail_count = int(counts.get("measurement_hard_fail_indicator_count", -1))
    if hard_fail_count != int(
        counts.get("measurement_hard_fail_only_indicator_count", -1)
    ) + int(counts.get("measurement_hard_fail_with_incomplete_vector_count", -1)):
        raise ValueError("measurement hard fail counts do not balance")
    if int(counts.get("grade_count", -1)) != 0 or int(counts.get("threshold_count", -1)) != 0:
        raise ValueError("feature gap audit must not contain grades or thresholds")
    if any(item.get("automatic_recovery_allowed") is not False for item in report.get("reason_metrics", [])):
        raise ValueError("feature gap audit cannot authorize automatic recovery")
    if any(
        item.get("automatic_recovery_allowed") is not False
        for item in report.get("measurement_hard_fail_metrics", [])
    ):
        raise ValueError("measurement hard fail audit cannot authorize automatic recovery")
    if any(item.get("automatic_profile_fallback_allowed") is not False or item.get("accuracy_claim") is not False for item in report.get("profile_alternative_metrics", [])):
        raise ValueError("profile alternatives must remain non-automatic and non-accuracy")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--indicator-features", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--profile-comparison", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_feature_observation_gaps(
        indicator_features_path=args.indicator_features,
        features_path=args.features,
        events_path=args.events,
        summary_path=args.summary,
        registry_path=args.registry,
        comparison_paths=args.profile_comparison,
    )
    validate_feature_observation_gap_audit(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "counts": report["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
