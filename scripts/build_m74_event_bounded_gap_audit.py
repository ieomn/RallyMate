#!/usr/bin/env python3
"""Audit and replay short event-internal Pose gaps on the M73 residual set."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.event_bounded_pose_gaps import (
    EVENT_BOUNDED_POSE_GAP_REPORT_VERSION,
    interpolate_event_bounded_pose_gaps,
    validate_event_bounded_pose_gap_report,
)
from rallymate_features import (
    EventInterval,
    clear_feature_cache,
    compute_event_features,
    pose_sequence_from_records,
)
from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_scoring.feasibility import load_feasibility_registry


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _source(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return {"path": str(resolved), "sha256": _sha256(resolved)}


def _valid_fraction(result: dict[str, Any]) -> float | None:
    value = result.get("provenance", {}).get("valid_fraction")
    return float(value) if isinstance(value, (int, float)) else None


def _feature_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_name": result["feature_name"],
        "feature_version": result["feature_version"],
        "valid": bool(result["valid"]),
        "reason": result["reason"],
        "value": result["value"],
        "unit": result["unit"],
        "confidence": result["confidence"],
        "valid_fraction": _valid_fraction(result),
        "source_frames": result["source_frames"],
    }


def _event_interval(record: dict[str, Any]) -> EventInterval:
    return EventInterval(
        event_id=str(record["event_id"]),
        event_code=str(record["event_code"]),
        start_ms=int(record["start_ms"]),
        end_ms=int(record["end_ms"]),
        person_track_id=int(record["person_track_id"]),
        key_phases=record.get("key_phases"),
    )


def build_report(m73_summary_path: Path) -> dict[str, Any]:
    summary_path = m73_summary_path.resolve()
    m73_summary = _load_json(summary_path)
    residual_items = m73_summary.get("remaining_residual", {}).get("items")
    if not isinstance(residual_items, list) or not residual_items:
        raise ValueError("M73 summary does not expose a non-empty residual set")

    reports_by_video: dict[str, tuple[Path, dict[str, Any]]] = {}
    per_video_sources = m73_summary.get("sources", {}).get("per_video_reports", [])
    for source in per_video_sources:
        path = Path(source["path"]).resolve()
        if _sha256(path) != str(source["sha256"]).upper():
            raise ValueError("M73 per-video report SHA does not match summary")
        payload = _load_json(path)
        reports_by_video[str(payload["video_id"])] = (path, payload)

    registry_path = Path(next(iter(reports_by_video.values()))[1]["sources"]["registry"]["path"]).resolve()
    registry = load_feasibility_registry(registry_path)
    registry_indicators = {
        str(item["indicator_id"]): item for item in registry["indicators"]
    }
    source_bundle: dict[str, Any] = {
        "m73_summary": _source(summary_path),
        "registry": _source(registry_path),
        "per_video": [],
    }
    sequences: dict[str, Any] = {}
    events_by_video: dict[str, dict[str, dict[str, Any]]] = {}

    for video_id, (report_path, report) in sorted(reports_by_video.items()):
        fused_path = Path(report["artifacts"]["fused_frames"]["path"]).resolve()
        timeline_path = Path(report["sources"]["primary_timeline"]["path"]).resolve()
        events_path = Path(report["sources"]["events"]["path"]).resolve()
        for path, expected in (
            (fused_path, report["artifacts"]["fused_frames"]["sha256"]),
            (timeline_path, report["sources"]["primary_timeline"]["sha256"]),
            (events_path, report["sources"]["events"]["sha256"]),
        ):
            if _sha256(path) != str(expected).upper():
                raise ValueError(f"M73 source SHA mismatch: {path}")
        frames = _load_jsonl(fused_path)
        timeline = _load_jsonl(timeline_path)
        events = _load_jsonl(events_path)
        sequences[video_id] = pose_sequence_from_records(frames, timeline)
        events_by_video[video_id] = {str(item["event_id"]): item for item in events}
        source_bundle["per_video"].append(
            {
                "video_id": video_id,
                "m73_report": _source(report_path),
                "frames": _source(fused_path),
                "primary_timeline": _source(timeline_path),
                "events": _source(events_path),
            }
        )

    output_items: list[dict[str, Any]] = []
    classification_counts: Counter[str] = Counter()
    run_kind_counts: Counter[str] = Counter()
    feature_transition_counts: Counter[str] = Counter()
    total_interpolated = 0
    unique_interpolated: set[tuple[str, str, str, int]] = set()
    eligible_run_spans: list[int] = []
    for residual in residual_items:
        video_id = str(residual["video_id"])
        event_record = events_by_video[video_id][str(residual["event_id"])]
        event = _event_interval(event_record)
        indicator_id = str(residual["indicator_id"])
        required_features = list(registry_indicators[indicator_id]["required_features"])
        unknown = [name for name in required_features if name not in FEATURE_DEFINITIONS]
        if unknown:
            raise ValueError(f"M74 cannot replay non-Pose features: {unknown}")
        sequence = sequences[video_id]
        baseline_results = {
            result.feature_name: result.to_dict()
            for result in compute_event_features(sequence, event, required_features)
        }
        invalid_names = [name for name in required_features if not baseline_results[name]["valid"]]
        expected_invalid_names = sorted(
            item["feature_name"] for item in residual["invalid_features"]
        )
        if sorted(invalid_names) != expected_invalid_names:
            raise ValueError(
                f"M73 residual replay drift for {event.event_id}/{indicator_id}: "
                f"{sorted(invalid_names)} != {expected_invalid_names}"
            )
        joints: list[str] = []
        for name in invalid_names:
            provenance_joints = baseline_results[name].get("provenance", {}).get(
                "required_joints_used"
            )
            candidates = (
                provenance_joints
                if isinstance(provenance_joints, list) and provenance_joints
                else FEATURE_DEFINITIONS[name]["required_joints"]
            )
            for joint in candidates:
                if joint not in joints:
                    joints.append(joint)

        filled_sequence, gap_audit = interpolate_event_bounded_pose_gaps(
            sequence,
            event,
            joints,
            max_gap_ms=160,
        )
        total_interpolated += int(gap_audit["interpolated_joint_observation_count"])
        unique_interpolated.update(
            (
                video_id,
                event.event_id,
                str(point["joint_name"]),
                int(point["source_frame"]),
            )
            for point in gap_audit["interpolated_points"]
        )
        for joint in gap_audit["joint_audits"]:
            for run in joint["runs"]:
                run_kind_counts[run["kind"]] += 1
                if run["eligible_for_bounded_interpolation"]:
                    eligible_run_spans.append(
                        int(run["bounding_observation_span_ms"])
                    )
        counterfactual_results = {
            result.feature_name: result.to_dict()
            for result in compute_event_features(
                filled_sequence, event, required_features
            )
        }
        transitions = []
        for name in required_features:
            before = baseline_results[name]
            after = counterfactual_results[name]
            transition = (
                "invalid_to_valid"
                if not before["valid"] and after["valid"]
                else "invalid_preserved"
                if not before["valid"]
                else "valid_preserved"
                if after["valid"]
                else "valid_to_invalid"
            )
            feature_transition_counts[transition] += 1
            if transition != "valid_preserved":
                transitions.append(
                    {
                        "feature_name": name,
                        "transition": transition,
                        "before": _feature_snapshot(before),
                        "counterfactual": _feature_snapshot(after),
                    }
                )
        feature_vector_complete = all(
            counterfactual_results[name]["valid"] for name in required_features
        )
        output_items.append(
            {
                "video_id": video_id,
                "event_id": event.event_id,
                "event_code": event.event_code,
                "start_ms": event.start_ms,
                "end_ms": event.end_ms,
                "indicator_id": indicator_id,
                "original_classification": residual["classification"],
                "required_features": required_features,
                "invalid_features_before": invalid_names,
                "joint_scope": joints,
                "gap_audit": gap_audit,
                "feature_transitions": transitions,
                "counterfactual_feature_vector_complete": feature_vector_complete,
                "production_measurement_status": "unchanged_unavailable",
            }
        )
        classification_counts[str(residual["classification"])] += 1
        clear_feature_cache(filled_sequence)

    recovered = sum(
        item["counterfactual_feature_vector_complete"] for item in output_items
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": EVENT_BOUNDED_POSE_GAP_REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "experimental_bounded_gap_counterfactual_found_recovery_requires_truth"
            if recovered
            else "experimental_bounded_gap_counterfactual_no_indicator_recovery"
        ),
        "scope": {
            "fixed_camera": True,
            "single_primary_player": True,
            "pose_topology": "halpe26_baseline_with_m73_mapped_additions",
            "counterfactual_only": True,
            "event_boundaries_reused": True,
        },
        "sources": source_bundle,
        "contract": {
            "max_bounding_observation_span_ms": 160,
            "timebase": "timestamp_ms",
            "eligible_gap": "strictly_internal_with_observed_valid_endpoints_inside_same_event",
            "coordinate_interpolation": "linear_in_timestamp",
            "effective_confidence": "minimum_of_two_observed_boundary_confidences",
            "existing_smoothing_contract_alignment": "rallymate_features.smoothing.max_gap_ms=160",
        },
        "residual_scope": {
            "source_milestone": "M73",
            "indicator_instance_count": len(output_items),
            "classification_counts": dict(sorted(classification_counts.items())),
        },
        "counterfactual_summary": {
            "indicator_replay_interpolated_joint_observation_count": total_interpolated,
            "unique_event_joint_observation_count": len(unique_interpolated),
            "eligible_internal_missing_run_count": len(eligible_run_spans),
            "eligible_bounding_observation_span_ms": {
                "minimum": min(eligible_run_spans) if eligible_run_spans else None,
                "maximum": max(eligible_run_spans) if eligible_run_spans else None,
            },
            "missing_run_kind_counts": dict(sorted(run_kind_counts.items())),
            "feature_transition_counts": dict(sorted(feature_transition_counts.items())),
            "counterfactual_recovered_indicator_instance_count": recovered,
            "counterfactual_remaining_indicator_instance_count": len(output_items) - recovered,
            "production_recovered_indicator_instance_count": 0,
        },
        "items": output_items,
        "decision": {
            "materialize_production_pose_artifact": False,
            "reason": (
                "counterfactual_gain_requires_artificial_keypoint_observations_and_truth_validation"
                if recovered
                else "strict_bounded_gap_interpolation_did_not_close_any_remaining_indicator_contract"
            ),
            "next_step": (
                "validate_interpolated_keypoints_against_manual_keypoint_truth_before_any_contract_change"
                if recovered
                else "preserve_unavailable_and_address_event_boundary_or_phase_evidence"
            ),
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "grade_generated": False,
            "scoring_threshold_generated": False,
            "production_pose_artifact_generated": False,
            "production_scoring_allowed": False,
            "event_boundary_extrapolation_allowed": False,
            "cross_event_interpolation_allowed": False,
            "event_phase_creation_allowed": False,
            "zero_fill_allowed": False,
        },
    }
    for sequence in sequences.values():
        clear_feature_cache(sequence)
    validate_event_bounded_pose_gap_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m73-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.m73_summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output.resolve()),
                **report["counterfactual_summary"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
