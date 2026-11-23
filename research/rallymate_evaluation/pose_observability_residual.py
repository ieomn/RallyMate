from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rallymate_evaluation.fixed_boundary_ab import validate_fixed_boundary_ab_report
from rallymate_evaluation.pose_observability_router import (
    validate_pose_observability_router_report,
)
from rallymate_features.event_features import FEATURE_DEFINITIONS


REPORT_VERSION = "pose-observability-residual-audit-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"invalid non-empty JSONL: {path}")
    return rows


def _bound_path(binding: Mapping[str, Any], label: str) -> Path:
    path = Path(str(binding.get("path", "")))
    expected = str(binding.get("sha256", "")).upper()
    if not path.is_file() or not expected or _sha256(path) != expected:
        raise ValueError(f"{label} source binding failed")
    return path


def _counter_rows(counter: Counter[str]) -> dict[str, int]:
    return {key: int(value) for key, value in sorted(counter.items())}


def operational_transitions_with_preserved_source_gate(
    comparison: Mapping[str, Any],
    current_model: str,
    experimental_model: str,
    indicator_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare a second-stage Pose projection while retaining source gates.

    The current vector may already be an earlier experimental composition, so
    its compact feature status is taken from the fixed-boundary comparison. The
    source indicator rows are used only as the immutable event/identity quality
    gate. This prevents a later profile experiment from silently clearing a
    hard failure while avoiding a false requirement that its current vector
    equal the original pre-composition vector.
    """

    source = {
        (str(row["event_id"]), str(row["indicator_id"])): row
        for row in indicator_rows
    }
    if len(source) != len(indicator_rows):
        raise ValueError("indicator source contains duplicate identities")
    transitions: Counter[str] = Counter()
    recovered: list[dict[str, str]] = []
    regressed: list[dict[str, str]] = []
    gate_counts: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    for event in comparison.get("events", []):
        event_id = str(event.get("boundary", {}).get("event_id", ""))
        for indicator in event.get("indicators", []):
            indicator_id = str(indicator.get("indicator_id", ""))
            identity = (event_id, indicator_id)
            row = source.get(identity)
            if row is None or identity in seen:
                raise ValueError("comparison and source indicator scopes differ")
            seen.add(identity)
            models = indicator.get("models", {})
            if current_model not in models or experimental_model not in models:
                raise ValueError("comparison model is missing")
            gate = row.get("quality_gate", {})
            allowed = gate.get("measurement_allowed") is True
            gate_counts[str(gate.get("status", ""))] += 1
            current = (
                "measured"
                if models[current_model].get("measurement_status") == "measured"
                and allowed
                else "unavailable"
            )
            experimental = (
                "measured"
                if models[experimental_model].get("measurement_status") == "measured"
                and allowed
                else "unavailable"
            )
            transitions[f"{current}_to_{experimental}"] += 1
            reference = {"event_id": event_id, "indicator_id": indicator_id}
            if current == "unavailable" and experimental == "measured":
                recovered.append(reference)
            elif current == "measured" and experimental == "unavailable":
                regressed.append(reference)
    if seen != set(source):
        raise ValueError("comparison does not cover every source indicator row")
    return {
        "status_transitions": _counter_rows(transitions),
        "recovered_indicator_instance_count": len(recovered),
        "regressed_indicator_instance_count": len(regressed),
        "recovered_indicator_instances": recovered,
        "regressed_indicator_instances": regressed,
        "preserved_quality_gate_status_counts": _counter_rows(gate_counts),
        "semantics": (
            "second_stage_experimental_pose_vectors_with_original_source_"
            "measurement_gate; not_production_and_not_accuracy"
        ),
    }


def build_pose_observability_residual_audit(
    *, registry_path: Path, router_report_paths: list[Path]
) -> dict[str, Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    indicators = registry.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("registry must contain indicators")
    indicator_ids = [str(item.get("indicator_id", "")) for item in indicators]
    if len(set(indicator_ids)) != len(indicator_ids) or any(not value for value in indicator_ids):
        raise ValueError("registry indicator IDs must be unique and non-empty")
    if len(router_report_paths) != 3:
        raise ValueError("residual audit requires exactly three router reports")

    registry_sha = _sha256(registry_path)
    source_reports: list[dict[str, str]] = []
    residual_items: list[dict[str, Any]] = []
    frame_scopes: list[dict[str, Any]] = []
    video_counts: list[dict[str, Any]] = []
    by_indicator: Counter[str] = Counter()
    by_feature: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    total_instances = 0
    feature_incomplete = 0
    operational_unavailable = 0
    measurement_hard_fail = 0
    seen_videos: set[str] = set()

    for report_path in sorted(router_report_paths, key=lambda path: str(path)):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        validate_pose_observability_router_report(report)
        video_id = str(report.get("video_id", ""))
        if not video_id or video_id in seen_videos:
            raise ValueError("router reports must have unique non-empty video IDs")
        seen_videos.add(video_id)
        source_reports.append(_source(report_path))
        if str(report.get("sources", {}).get("registry", {}).get("sha256", "")).upper() != registry_sha:
            raise ValueError("router report registry differs from residual registry")

        comparison_path = _bound_path(
            report.get("artifacts", {}).get("fixed_boundary_comparison", {}),
            "fixed-boundary comparison",
        )
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        validate_fixed_boundary_ab_report(comparison)
        if len(comparison.get("model_order", [])) != 2:
            raise ValueError("fixed-boundary report must have two models")
        candidate_model = str(comparison["model_order"][1])
        indicator_path = _bound_path(
            report.get("sources", {}).get("indicator_features", {}),
            "indicator-features",
        )
        indicator_rows = _read_jsonl(indicator_path)
        indicator_map = {
            (str(row["event_id"]), str(row["indicator_id"])): row
            for row in indicator_rows
        }
        if len(indicator_map) != len(indicator_rows):
            raise ValueError("indicator-features contains duplicate identities")
        event_path = _bound_path(report.get("sources", {}).get("events", {}), "events")
        event_rows = _read_jsonl(event_path)
        event_map = {str(row["event_id"]): row for row in event_rows}
        if len(event_map) != len(event_rows):
            raise ValueError("events contains duplicate event IDs")
        frames_path = _bound_path(
            report.get("artifacts", {}).get("routed_frames", {}), "routed frames"
        )
        frames = _read_jsonl(frames_path)

        feature_map: dict[tuple[str, str], Mapping[str, Any]] = {}
        for pair in comparison.get("feature_pairs", []):
            identity = (str(pair["event_id"]), str(pair["feature_name"]))
            if identity in feature_map:
                raise ValueError("fixed-boundary report contains duplicate feature pairs")
            model_feature = pair.get("models", {}).get(candidate_model)
            if not isinstance(model_feature, Mapping):
                raise ValueError("candidate feature record is missing")
            feature_map[identity] = model_feature

        video_total = video_feature_incomplete = video_operational_unavailable = 0
        video_hard_fail = video_residual = 0
        residual_event_ids: set[str] = set()
        seen_identities: set[tuple[str, str]] = set()
        for event in comparison.get("events", []):
            boundary = event.get("boundary", {})
            event_id = str(boundary.get("event_id", ""))
            source_event = event_map.get(event_id)
            if source_event is None:
                raise ValueError("fixed-boundary event is missing from source events")
            for indicator in event.get("indicators", []):
                indicator_id = str(indicator.get("indicator_id", ""))
                identity = (event_id, indicator_id)
                if identity in seen_identities or identity not in indicator_map:
                    raise ValueError("fixed-boundary indicator identity is missing or duplicated")
                seen_identities.add(identity)
                source_indicator = indicator_map[identity]
                gate = source_indicator.get("quality_gate", {})
                measurement_allowed = gate.get("measurement_allowed") is True
                model_state = indicator.get("models", {}).get(candidate_model, {})
                vector_complete = model_state.get("measurement_status") == "measured"
                video_total += 1
                if not vector_complete:
                    video_feature_incomplete += 1
                if not measurement_allowed:
                    video_hard_fail += 1
                if not vector_complete or not measurement_allowed:
                    video_operational_unavailable += 1
                if vector_complete or not measurement_allowed:
                    continue

                invalid_features: list[dict[str, Any]] = []
                required_features = indicator.get("required_features", [])
                if not isinstance(required_features, list) or not required_features:
                    raise ValueError("indicator required feature contract is missing")
                for feature_name in required_features:
                    feature = feature_map.get((event_id, str(feature_name)))
                    if feature is None:
                        raise ValueError("required feature is missing from fixed comparison")
                    if feature.get("valid") is True:
                        continue
                    provenance = feature.get("provenance", {})
                    reason = str(feature.get("reason", ""))
                    definition = FEATURE_DEFINITIONS.get(str(feature_name), {})
                    item = {
                        "feature_name": str(feature_name),
                        "feature_version": str(feature.get("feature_version", "")),
                        "reason": reason,
                        "confidence": feature.get("confidence"),
                        "source_frames": list(feature.get("source_frames", [])),
                        "required_joints": list(definition.get("required_joints", [])),
                        "valid_fraction": provenance.get("valid_fraction"),
                        "body_scale": provenance.get("body_scale"),
                    }
                    invalid_features.append(item)
                    by_feature[str(feature_name)] += 1
                    by_reason[reason] += 1
                if not invalid_features:
                    raise ValueError("incomplete indicator has no invalid required feature")
                by_indicator[indicator_id] += 1
                residual_event_ids.add(event_id)
                video_residual += 1
                residual_items.append(
                    {
                        "video_id": video_id,
                        "event_id": event_id,
                        "event_code": str(boundary.get("event_code", "")),
                        "person_track_id": boundary.get("person_track_id"),
                        "start_ms": int(boundary["start_ms"]),
                        "end_ms": int(boundary["end_ms"]),
                        "indicator_id": indicator_id,
                        "quality_gate_status": str(gate.get("status", "")),
                        "measurement_allowed": True,
                        "invalid_features": invalid_features,
                    }
                )

        if seen_identities != set(indicator_map):
            raise ValueError("fixed comparison and indicator-features scope differ")
        target_rows = [
            row
            for row in frames
            if any(
                int(event_map[event_id]["start_ms"])
                <= int(row["frame"]["timestamp_ms"])
                <= int(event_map[event_id]["end_ms"])
                for event_id in residual_event_ids
            )
        ]
        processed = [int(row["frame"]["processed_index"]) for row in target_rows]
        source_indexes = [int(row["frame"]["index"]) for row in target_rows]
        timestamps = [int(row["frame"]["timestamp_ms"]) for row in target_rows]
        if processed != sorted(set(processed)):
            raise ValueError("residual target frame indexes are not unique and sorted")
        frame_scopes.append(
            {
                "video_id": video_id,
                "event_ids": sorted(residual_event_ids),
                "target_frame_count": len(target_rows),
                "processed_indexes": processed,
                "source_frame_indexes": source_indexes,
                "timestamp_ms": timestamps,
            }
        )
        video_counts.append(
            {
                "video_id": video_id,
                "indicator_instances": video_total,
                "feature_vector_incomplete": video_feature_incomplete,
                "operational_unavailable": video_operational_unavailable,
                "measurement_hard_fail": video_hard_fail,
                "non_hard_fail_feature_incomplete": video_residual,
                "residual_event_count": len(residual_event_ids),
                "candidate_frame_count": len(target_rows),
            }
        )
        total_instances += video_total
        feature_incomplete += video_feature_incomplete
        operational_unavailable += video_operational_unavailable
        measurement_hard_fail += video_hard_fail

    residual_items.sort(
        key=lambda item: (
            item["video_id"], item["start_ms"], item["event_id"], item["indicator_id"]
        )
    )
    frame_scopes.sort(key=lambda item: item["video_id"])
    video_counts.sort(key=lambda item: item["video_id"])
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "residual_observability_gaps_require_candidate_or_truth",
        "scope": {
            "video_count": len(seen_videos),
            "video_ids": sorted(seen_videos),
            "indicator_count": len(indicator_ids),
            "indicator_ids": indicator_ids,
            "indicator_instances": total_instances,
        },
        "sources": {
            "registry": _source(registry_path),
            "router_reports": source_reports,
        },
        "counts": {
            "feature_vector_complete": total_instances - feature_incomplete,
            "feature_vector_incomplete": feature_incomplete,
            "operational_measured": total_instances - operational_unavailable,
            "operational_unavailable": operational_unavailable,
            "measurement_hard_fail": measurement_hard_fail,
            "non_hard_fail_feature_incomplete": len(residual_items),
            "residual_event_count": len(
                {(item["video_id"], item["event_id"]) for item in residual_items}
            ),
            "invalid_feature_occurrence_count": sum(
                len(item["invalid_features"]) for item in residual_items
            ),
            "candidate_frame_count": sum(
                int(item["target_frame_count"]) for item in frame_scopes
            ),
        },
        "by_video": video_counts,
        "by_indicator": _counter_rows(by_indicator),
        "by_invalid_feature": _counter_rows(by_feature),
        "by_reason": _counter_rows(by_reason),
        "candidate_frame_scope": frame_scopes,
        "residual_items": residual_items,
        "next_candidate": {
            "profile": "rtmpose-m-halpe26-384x288",
            "selection_policy": "required_joint_validity_strict_superset_then_fixed_boundary_recompute",
            "reason": "most residuals are low-valid-fraction Pose observations; higher input resolution must still prove no current-set regression",
            "production_enabled": False,
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "feature_gate_relaxed": False,
            "measurement_gate_relaxed": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "production_enabled": False,
            "maturity_promoted": False,
        },
    }
    validate_pose_observability_residual_audit(report)
    return report


def validate_pose_observability_residual_audit(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported residual audit contract")
    if report.get("status") != "residual_observability_gaps_require_candidate_or_truth":
        raise ValueError("unsafe residual audit status")
    scope = report.get("scope", {})
    counts = report.get("counts", {})
    total = int(scope.get("indicator_instances", -1))
    if int(counts.get("feature_vector_complete", -1)) + int(
        counts.get("feature_vector_incomplete", -1)
    ) != total:
        raise ValueError("feature residual accounting does not balance")
    if int(counts.get("operational_measured", -1)) + int(
        counts.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("operational residual accounting does not balance")
    residual_items = report.get("residual_items", [])
    if len(residual_items) != int(counts.get("non_hard_fail_feature_incomplete", -1)):
        raise ValueError("residual item count is inconsistent")
    invalid_count = sum(len(item.get("invalid_features", [])) for item in residual_items)
    if invalid_count != int(counts.get("invalid_feature_occurrence_count", -1)):
        raise ValueError("invalid feature occurrence count is inconsistent")
    unique_events = {(item.get("video_id"), item.get("event_id")) for item in residual_items}
    if len(unique_events) != int(counts.get("residual_event_count", -1)):
        raise ValueError("residual event count is inconsistent")
    frame_scopes = report.get("candidate_frame_scope", [])
    if sum(int(item.get("target_frame_count", -1)) for item in frame_scopes) != int(
        counts.get("candidate_frame_count", -1)
    ):
        raise ValueError("candidate frame count is inconsistent")
    for item in frame_scopes:
        count = int(item.get("target_frame_count", -1))
        for field in ("processed_indexes", "source_frame_indexes", "timestamp_ms"):
            values = item.get(field, [])
            if not isinstance(values, list) or len(values) != count:
                raise ValueError("candidate frame arrays do not balance")
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "feature_gate_relaxed",
        "measurement_gate_relaxed",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "production_enabled",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"unsafe residual audit claim: {field}")
    if report.get("next_candidate", {}).get("production_enabled") is not False:
        raise ValueError("residual candidate cannot enable production")


def validate_pose_observability_residual_audit_sources(report: Mapping[str, Any]) -> None:
    validate_pose_observability_residual_audit(report)
    registry = _bound_path(report.get("sources", {}).get("registry", {}), "registry")
    bindings = report.get("sources", {}).get("router_reports", [])
    if not isinstance(bindings, list):
        raise ValueError("router report bindings are missing")
    paths = [_bound_path(binding, "router report") for binding in bindings]
    rebuilt = build_pose_observability_residual_audit(
        registry_path=registry, router_report_paths=paths
    )
    ignored = {"generated_at"}
    left = {key: value for key, value in report.items() if key not in ignored}
    right = {key: value for key, value in rebuilt.items() if key not in ignored}
    if left != right:
        raise ValueError("residual audit differs from source replay")


__all__ = [
    "REPORT_VERSION",
    "build_pose_observability_residual_audit",
    "operational_transitions_with_preserved_source_gate",
    "validate_pose_observability_residual_audit",
    "validate_pose_observability_residual_audit_sources",
]
