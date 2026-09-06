#!/usr/bin/env python3
"""Re-run only guard-skipped primary-player ROIs as a non-production experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.small_roi_recovery import (
    feature_vector_transitions,
    operational_measurement_transitions,
    select_small_roi_targets,
)
from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.rtmpose_backend import RtmposePoseBackend


SCHEMA_VERSION = "1.1.0"
EXPERIMENT_VERSION = "small-roi-pose-recovery-v1.1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"JSONL must contain non-empty objects: {path}")
    return rows


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def validate_small_roi_experiment_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION or report.get("experiment_version") != EXPERIMENT_VERSION:
        raise ValueError("unsupported small ROI experiment version")
    if report.get("status") != "experimental_observability_only_not_production":
        raise ValueError("small ROI experiment status is unsafe")
    safety = report.get("safety", {})
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "measurement_gate_modified",
        "scoring_gate_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"unsafe small ROI experiment claim: {field}")
    if safety.get("current_production_default_unchanged") is not True:
        raise ValueError("small ROI experiment changed the production default")
    if (
        safety.get(
            "recovered_means_observable_under_experimental_small_roi_not_accurate"
        )
        is not True
    ):
        raise ValueError("small ROI experiment makes an unsupported accuracy claim")
    counts = report.get("inference", {}).get("counts", {})
    if int(counts.get("inference_attempted", -1)) != int(counts.get("pose_output_recovered", -1)) + int(counts.get("pose_output_still_missing", -1)):
        raise ValueError("small ROI inference counts do not balance")
    vector_impact = report.get("feature_vector_impact", {})
    operational_impact = report.get("operational_measurement_impact", {})
    if int(vector_impact.get("regressed_indicator_instance_count", -1)) != 0:
        raise ValueError("small ROI experiment regressed a feature vector")
    if int(operational_impact.get("regressed_indicator_instance_count", -1)) != 0:
        raise ValueError("small ROI experiment regressed an operational measurement")
    if int(operational_impact.get("recovered_indicator_instance_count", -1)) > int(
        vector_impact.get("recovered_indicator_instance_count", -1)
    ):
        raise ValueError("operational recovery cannot exceed feature-vector recovery")
    if safety.get("feature_vector_status_is_not_operational_measurement_status") is not True:
        raise ValueError("small ROI experiment conflates vector and operational status")


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
    parser.add_argument("--baseline-min-roi-size", type=int, default=32)
    parser.add_argument("--experimental-min-roi-size", type=int, default=8)
    parser.add_argument("--roi-margin", type=float, default=0.15)
    parser.add_argument("--baseline-max-players", type=int, default=2)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; experiments are immutable")

    frame_rows = _read_jsonl(args.frames)
    timeline_rows = _read_jsonl(args.primary_timeline)
    indicator_rows = _read_jsonl(args.indicator_features)
    gap_audit = json.loads(args.gap_audit.read_text(encoding="utf-8"))
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    if gap_audit.get("assertions", {}).get("automatic_profile_fallback_enabled") is not False:
        parser.error("gap audit does not prohibit automatic profile fallback")
    source = gap_audit.get("source", {})
    if str(source.get("events", {}).get("sha256", "")).upper() != _sha256(args.events):
        parser.error("gap audit events hash does not match")
    provenance = summary.get("provenance", {})
    expected = {
        args.video: provenance.get("video_sha256"),
        args.frames: provenance.get("frames_sha256"),
        args.primary_timeline: provenance.get("primary_timeline_sha256"),
    }
    for path, expected_sha in expected.items():
        if not isinstance(expected_sha, str) or _sha256(path).upper() != expected_sha.upper():
            parser.error(f"summary provenance hash mismatch: {path}")
    indicator_features_sha = summary.get("artifact_sha256", {}).get(
        "indicator_features_jsonl"
    )
    if (
        not isinstance(indicator_features_sha, str)
        or _sha256(args.indicator_features).upper()
        != indicator_features_sha.upper()
    ):
        parser.error("summary indicator-features hash mismatch")
    current_profile = str(summary.get("model_versions", {}).get("pose_profile", ""))
    current_model_sha = str(summary.get("model_versions", {}).get("pose_model_sha256", ""))
    if not current_profile or _sha256(args.model).upper() != current_model_sha.upper():
        parser.error("pose profile/model does not match scoring summary")
    event_ranges = [
        (int(event["start_ms"]), int(event["end_ms"]))
        for event in gap_audit.get("event_failures", [])
    ]
    targets, selection_counts = select_small_roi_targets(
        frame_rows=frame_rows,
        timeline_rows=timeline_rows,
        event_ranges=event_ranges,
        baseline_min_roi_size_px=args.baseline_min_roi_size,
        experimental_min_roi_size_px=args.experimental_min_roi_size,
        roi_margin=args.roi_margin,
        baseline_max_players=args.baseline_max_players,
    )
    if not targets:
        parser.error("no baseline size-guard skips are eligible for the experiment")

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
        roi_margin=args.roi_margin,
        min_roi_size_px=args.experimental_min_roi_size,
    )
    target_by_source_frame = {item["source_frame_index"]: item for item in targets}
    recovered: dict[int, dict[str, Any]] = {}
    recovered_keypoint_valid_counts: Counter[int] = Counter()
    cap = cv2.VideoCapture(str(args.video))
    source_index = 0
    last_target = max(target_by_source_frame)
    while source_index <= last_target:
        ok, frame = cap.read()
        if not ok:
            cap.release()
            parser.error(f"video decode ended before source frame {last_target}")
        target = target_by_source_frame.get(source_index)
        if target is not None:
            poses = estimator.estimate(
                frame,
                [target["detection"]],
                max_players=1,
                timestamp_ms=target["timestamp_ms"],
            )
            if poses:
                pose = poses[0]
                recovered[target["processed_index"]] = pose
                valid_count = sum(
                    point.get("in_frame") is not False and float(point.get("confidence", 0.0)) >= 0.25
                    for point in pose["keypoints"]
                )
                recovered_keypoint_valid_counts[int(valid_count)] += 1
        source_index += 1
    cap.release()

    args.output_directory.mkdir(parents=True, exist_ok=False)
    experimental_frames_path = args.output_directory / "frames.experimental.jsonl"
    with experimental_frames_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in frame_rows:
            processed_index = int(row["frame"]["processed_index"])
            if processed_index in recovered:
                row = dict(row)
                row["poses"] = list(row.get("poses", [])) + [recovered[processed_index]]
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    experiment_name = f"{current_profile}-small-roi-min{args.experimental_min_roi_size}-experimental"
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
        boundary_source_label="halpe256_candidate_boundaries_not_truth_small_roi_experiment",
        source_id=f"{summary['video_id']}:small-roi-experiment",
    )
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    vector_impact = feature_vector_transitions(
        comparison, current_profile, experiment_name
    )
    operational_impact = operational_measurement_transitions(
        comparison,
        current_profile,
        experiment_name,
        indicator_rows,
    )
    inference_counts = {
        **selection_counts,
        "inference_attempted": len(targets),
        "pose_output_recovered": len(recovered),
        "pose_output_still_missing": len(targets) - len(recovered),
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
            "baseline_min_roi_size_px": args.baseline_min_roi_size,
            "experimental_min_roi_size_px": args.experimental_min_roi_size,
            "roi_margin": args.roi_margin,
            "baseline_max_players": args.baseline_max_players,
            "keypoint_observation_confidence_min": 0.25,
            "event_boundary_source": "candidate_not_truth",
        },
        "inference": {
            "counts": inference_counts,
            "recovered_keypoint_valid_count_distribution": {
                str(key): value for key, value in sorted(recovered_keypoint_valid_counts.items())
            },
            "target_processed_indices": sorted(int(item["processed_index"]) for item in targets),
            "recovered_processed_indices": sorted(recovered),
        },
        "artifacts": {
            "experimental_frames": _source(experimental_frames_path),
            "fixed_boundary_comparison": _source(comparison_path),
        },
        "feature_vector_impact": vector_impact,
        "operational_measurement_impact": operational_impact,
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "production_enabled": False,
            "current_production_default_unchanged": True,
            "automatic_profile_fallback_enabled": False,
            "measurement_gate_modified": False,
            "scoring_gate_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "recovered_means_observable_under_experimental_small_roi_not_accurate": True,
            "feature_vector_status_is_not_operational_measurement_status": True,
        },
    }
    validate_small_roi_experiment_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
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
