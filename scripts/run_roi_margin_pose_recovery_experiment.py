#!/usr/bin/env python3
"""Re-run gap-event ROIs with more crop context as a non-production experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.small_roi_recovery import (
    feature_vector_transitions,
    operational_measurement_transitions,
    select_roi_margin_targets,
)
from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.rtmpose_backend import RtmposePoseBackend


SCHEMA_VERSION = "1.0.0"
EXPERIMENT_VERSION = "roi-margin-pose-recovery-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"JSONL must contain non-empty objects: {path}")
    return rows


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def validate_roi_margin_experiment_report(report: dict[str, Any]) -> None:
    if (
        report.get("schema_version") != SCHEMA_VERSION
        or report.get("experiment_version") != EXPERIMENT_VERSION
    ):
        raise ValueError("unsupported ROI margin experiment version")
    if report.get("status") != "experimental_observability_only_not_production":
        raise ValueError("ROI margin experiment status is unsafe")
    settings = report.get("settings", {})
    baseline = settings.get("baseline_roi_margin")
    experimental = settings.get("experimental_roi_margin")
    if (
        isinstance(baseline, bool)
        or isinstance(experimental, bool)
        or not isinstance(baseline, (int, float))
        or not isinstance(experimental, (int, float))
        or not 0 <= float(baseline) < float(experimental)
    ):
        raise ValueError("ROI margin experiment settings are invalid")
    safety = report.get("safety", {})
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "measurement_gate_modified",
        "scoring_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"unsafe ROI margin experiment claim: {field}")
    for field in (
        "current_production_default_unchanged",
        "same_model_weights_detection_track_and_event_boundaries",
        "experimental_frames_replace_same_track_pose_without_baseline_fallback",
        "feature_vector_status_is_not_operational_measurement_status",
        "recovered_means_observable_under_experimental_crop_not_accurate",
    ):
        if safety.get(field) is not True:
            raise ValueError(f"missing ROI margin safety assertion: {field}")
    counts = report.get("inference", {}).get("counts", {})
    attempted = int(counts.get("inference_attempted", -1))
    recovered = int(counts.get("pose_output_produced", -1))
    missing = int(counts.get("pose_output_missing", -1))
    if attempted < 1 or attempted != recovered + missing:
        raise ValueError("ROI margin inference counts do not balance")
    frame_results = report.get("inference", {}).get("frame_results", [])
    if len(frame_results) != attempted:
        raise ValueError("ROI margin frame results do not cover every target")
    indexes = [int(item["processed_index"]) for item in frame_results]
    if indexes != sorted(set(indexes)):
        raise ValueError("ROI margin frame results are duplicated or unsorted")
    vector = report.get("feature_vector_impact", {})
    operational = report.get("operational_measurement_impact", {})
    for impact in (vector, operational):
        if int(impact.get("recovered_indicator_instance_count", -1)) < 0:
            raise ValueError("ROI margin recovery count is invalid")
        if int(impact.get("regressed_indicator_instance_count", -1)) < 0:
            raise ValueError("ROI margin regression count is invalid")
    if int(operational["recovered_indicator_instance_count"]) > int(
        vector["recovered_indicator_instance_count"]
    ):
        raise ValueError("operational recovery cannot exceed vector recovery")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--indicator-features", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--gap-audit", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--baseline-roi-margin", type=float, default=0.15)
    parser.add_argument("--experimental-roi-margin", type=float, default=0.30)
    parser.add_argument("--min-roi-size", type=int, default=32)
    parser.add_argument("--baseline-max-players", type=int, default=2)
    parser.add_argument("--only-clipped-baseline-roi", action="store_true")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; experiments are immutable")

    frame_rows = _read_jsonl(args.frames)
    timeline_rows = _read_jsonl(args.primary_timeline)
    indicator_rows = _read_jsonl(args.indicator_features)
    gap_audit = json.loads(args.gap_audit.read_text(encoding="utf-8"))
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    if (
        gap_audit.get("assertions", {}).get("automatic_profile_fallback_enabled")
        is not False
    ):
        parser.error("gap audit does not prohibit automatic profile fallback")
    if (
        str(gap_audit.get("source", {}).get("events", {}).get("sha256", "")).upper()
        != _sha256(args.events)
    ):
        parser.error("gap audit events hash does not match")
    provenance = summary.get("provenance", {})
    for path, expected_sha in {
        args.video: provenance.get("video_sha256"),
        args.frames: provenance.get("frames_sha256"),
        args.primary_timeline: provenance.get("primary_timeline_sha256"),
    }.items():
        if (
            not isinstance(expected_sha, str)
            or _sha256(path).upper() != expected_sha.upper()
        ):
            parser.error(f"summary provenance hash mismatch: {path}")
    expected_indicator_sha = summary.get("artifact_sha256", {}).get(
        "indicator_features_jsonl"
    )
    if (
        not isinstance(expected_indicator_sha, str)
        or _sha256(args.indicator_features).upper()
        != expected_indicator_sha.upper()
    ):
        parser.error("summary indicator-features hash mismatch")
    current_profile = str(summary.get("model_versions", {}).get("pose_profile", ""))
    current_model_sha = str(
        summary.get("model_versions", {}).get("pose_model_sha256", "")
    )
    if not current_profile or _sha256(args.model).upper() != current_model_sha.upper():
        parser.error("pose profile/model does not match scoring summary")

    event_ranges = [
        (int(event["start_ms"]), int(event["end_ms"]))
        for event in gap_audit.get("event_failures", [])
    ]
    targets, selection_counts = select_roi_margin_targets(
        frame_rows=frame_rows,
        timeline_rows=timeline_rows,
        event_ranges=event_ranges,
        baseline_roi_margin=args.baseline_roi_margin,
        experimental_roi_margin=args.experimental_roi_margin,
        min_roi_size_px=args.min_roi_size,
        baseline_max_players=args.baseline_max_players,
        require_baseline_crop_clipped=args.only_clipped_baseline_roi,
    )
    if not targets:
        parser.error("no gap-event ROI changes under the experimental margin")

    backend = RtmposePoseBackend(
        args.model,
        args.config,
        device=args.device,
        runtime="pytorch",
        profile="realtime",
        native_keypoint_format="halpe26",
    )
    estimator = PoseEstimator(
        backend,
        roi_margin=args.experimental_roi_margin,
        min_roi_size_px=args.min_roi_size,
    )
    target_by_source_frame = {item["source_frame_index"]: item for item in targets}
    recovered: dict[int, dict[str, Any] | None] = {}
    frame_results: list[dict[str, Any]] = []
    valid_count_transitions: Counter[str] = Counter()
    capture = cv2.VideoCapture(str(args.video))
    source_index = 0
    last_target = max(target_by_source_frame)
    while source_index <= last_target:
        ok, frame = capture.read()
        if not ok:
            capture.release()
            parser.error(f"video decode ended before source frame {last_target}")
        target = target_by_source_frame.get(source_index)
        if target is not None:
            poses = estimator.estimate(
                frame,
                [target["detection"]],
                max_players=1,
                timestamp_ms=target["timestamp_ms"],
            )
            pose = poses[0] if poses else None
            recovered[target["processed_index"]] = pose
            experimental_valid = (
                sum(
                    point.get("in_frame") is not False
                    and float(point.get("confidence", 0.0)) >= 0.25
                    for point in pose["keypoints"]
                )
                if pose is not None
                else 0
            )
            baseline_valid = int(target["baseline_valid_keypoint_count"])
            direction = (
                "increased"
                if experimental_valid > baseline_valid
                else "decreased"
                if experimental_valid < baseline_valid
                else "unchanged"
            )
            valid_count_transitions[direction] += 1
            frame_results.append(
                {
                    "processed_index": int(target["processed_index"]),
                    "source_frame_index": int(target["source_frame_index"]),
                    "timestamp_ms": int(target["timestamp_ms"]),
                    "track_id": int(target["track_id"]),
                    "baseline_crop_box": target["baseline_crop_box"],
                    "experimental_crop_box": target["experimental_crop_box"],
                    "baseline_crop_clipped": bool(target["baseline_crop_clipped"]),
                    "baseline_valid_keypoint_count": baseline_valid,
                    "experimental_valid_keypoint_count": experimental_valid,
                    "valid_keypoint_count_change": experimental_valid - baseline_valid,
                    "pose_output_produced": pose is not None,
                }
            )
        source_index += 1
    capture.release()
    frame_results.sort(key=lambda item: item["processed_index"])

    args.output_directory.mkdir(parents=True, exist_ok=False)
    experimental_frames_path = args.output_directory / "frames.experimental.jsonl"
    with experimental_frames_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in frame_rows:
            processed_index = int(row["frame"]["processed_index"])
            if processed_index in recovered:
                row = dict(row)
                target_track = next(
                    item["track_id"]
                    for item in targets
                    if item["processed_index"] == processed_index
                )
                poses = [
                    pose
                    for pose in row.get("poses", [])
                    if pose.get("person_track_id") != target_track
                ]
                if recovered[processed_index] is not None:
                    poses.append(recovered[processed_index])
                row["poses"] = poses
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    experiment_name = (
        f"{current_profile}-roi-margin-{args.experimental_roi_margin:g}-experimental"
    )
    comparison_path = args.output_directory / "fixed-boundary-comparison.json"
    comparison = evaluate_fixed_boundary_pose_ab_files(
        events_path=args.events,
        registry_path=args.registry,
        model_inputs={
            current_profile: {
                "frames_path": args.frames,
                "primary_timeline_path": args.primary_timeline,
                "model_sha256": current_model_sha,
                "pose_backend": "rtmpose",
                "pose_profile": current_profile,
            },
            experiment_name: {
                "frames_path": experimental_frames_path,
                "primary_timeline_path": args.primary_timeline,
                "model_sha256": current_model_sha,
                "pose_backend": "rtmpose",
                "pose_profile": experiment_name,
            },
        },
        boundary_source_kind="candidate",
        boundary_source_label=(
            "halpe256_candidate_boundaries_not_truth_roi_margin_experiment"
        ),
        source_id=f"{summary['video_id']}:roi-margin-experiment",
    )
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    vector_impact = feature_vector_transitions(
        comparison, current_profile, experiment_name
    )
    operational_impact = operational_measurement_transitions(
        comparison,
        current_profile,
        experiment_name,
        indicator_rows,
    )
    produced = sum(item["pose_output_produced"] for item in frame_results)
    inference_counts = {
        **selection_counts,
        "inference_attempted": len(targets),
        "pose_output_produced": produced,
        "pose_output_missing": len(targets) - produced,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "experiment_version": EXPERIMENT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "experimental_observability_only_not_production",
        "source": {
            "video": _source(args.video),
            "frames": _source(args.frames),
            "primary_timeline": _source(args.primary_timeline),
            "events": _source(args.events),
            "indicator_features": _source(args.indicator_features),
            "summary": _source(args.summary),
            "registry": _source(args.registry),
            "gap_audit": _source(args.gap_audit),
            "pose_model": _source(args.model),
            "pose_config": _source(args.config),
            "current_profile": current_profile,
        },
        "settings": {
            "baseline_roi_margin": args.baseline_roi_margin,
            "experimental_roi_margin": args.experimental_roi_margin,
            "min_roi_size_px": args.min_roi_size,
            "baseline_max_players": args.baseline_max_players,
            "keypoint_observation_confidence_min": 0.25,
            "event_boundary_source": "candidate_not_truth",
            "selection_scope": "baseline_pose_present_frames_inside_feature_gap_events",
            "only_clipped_baseline_roi": args.only_clipped_baseline_roi,
        },
        "inference": {
            "counts": inference_counts,
            "valid_keypoint_count_transition_counts": dict(
                sorted(valid_count_transitions.items())
            ),
            "frame_results": frame_results,
        },
        "artifacts": {
            "experimental_frames": _source(experimental_frames_path),
            "fixed_boundary_comparison": _source(comparison_path),
        },
        "feature_vector_impact": vector_impact,
        "operational_measurement_impact": operational_impact,
        "candidate_decision": {
            "eligible_for_production_promotion": False,
            "reason": "manual_keypoint_truth_and_independent_release_review_required",
            "regression_free_observability_gain": (
                vector_impact["recovered_indicator_instance_count"] > 0
                and vector_impact["regressed_indicator_instance_count"] == 0
                and operational_impact["regressed_indicator_instance_count"] == 0
            ),
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "production_enabled": False,
            "current_production_default_unchanged": True,
            "automatic_profile_fallback_enabled": False,
            "measurement_gate_modified": False,
            "scoring_gate_modified": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "same_model_weights_detection_track_and_event_boundaries": True,
            "experimental_frames_replace_same_track_pose_without_baseline_fallback": True,
            "feature_vector_status_is_not_operational_measurement_status": True,
            "recovered_means_observable_under_experimental_crop_not_accurate": True,
        },
    }
    validate_roi_margin_experiment_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "inference": inference_counts,
                "feature_vector_impact": vector_impact,
                "operational_measurement_impact": operational_impact,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
