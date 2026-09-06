#!/usr/bin/env python3
"""Re-infer M69 residual events with a higher-resolution Pose profile."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.pose_observability_residual import (
    operational_transitions_with_preserved_source_gate,
    validate_pose_observability_residual_audit_sources,
)
from rallymate_evaluation.pose_observability_router import (
    pose_required_joints_from_registry,
    select_required_joint_superset_frames,
)
from rallymate_evaluation.pose_policy_composition import compose_disjoint_pose_policy_rows
from rallymate_evaluation.small_roi_recovery import feature_vector_transitions
from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.rtmpose_backend import RtmposePoseBackend


EXPERIMENT_VERSION = "residual-high-resolution-pose-experiment-v1.0.0"


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
        raise ValueError(f"invalid JSONL: {path}")
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def _balanced(impact: dict[str, Any]) -> tuple[int, int]:
    transitions = impact.get("status_transitions", {})
    current = int(transitions.get("measured_to_measured", 0)) + int(
        transitions.get("measured_to_unavailable", 0)
    )
    experimental = int(transitions.get("measured_to_measured", 0)) + int(
        transitions.get("unavailable_to_measured", 0)
    )
    return current, experimental


def validate_residual_high_resolution_experiment_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("experiment_version") != EXPERIMENT_VERSION:
        raise ValueError("unsupported residual high-resolution experiment")
    status = report.get("status")
    if status not in {
        "experimental_regression_free_high_resolution_candidate_not_production",
        "experimental_high_resolution_candidate_rejected_due_to_regression",
    }:
        raise ValueError("unsafe high-resolution experiment status")
    target = int(report.get("inference", {}).get("target_frame_count", -1))
    produced = int(report.get("inference", {}).get("pose_output_produced", -1))
    missing = int(report.get("inference", {}).get("pose_output_missing", -1))
    if target < 1 or produced + missing != target:
        raise ValueError("high-resolution inference accounting is inconsistent")
    audit = report.get("router_audit", {})
    if int(audit.get("selected_frame_count", -1)) + int(
        audit.get("rejected_frame_count", -1)
    ) != target:
        raise ValueError("high-resolution router accounting is inconsistent")
    for field, expected in {
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
        "required_joint_validity_regression_allowed": False,
    }.items():
        if audit.get(field) is not expected:
            raise ValueError(f"unsafe router field: {field}")
    vector = report.get("feature_vector_impact", {})
    operational = report.get("operational_measurement_impact", {})
    regressions = int(vector.get("regressed_indicator_instance_count", -1)) + int(
        operational.get("regressed_indicator_instance_count", -1)
    )
    if status.startswith("experimental_regression_free") and regressions != 0:
        raise ValueError("regression-free status hides a regression")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get("candidate_promoted") is not False:
        raise ValueError("high-resolution experiment changed production")
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "feature_gate_modified",
        "measurement_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"unsafe high-resolution claim: {field}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--router-report", type=Path, required=True)
    parser.add_argument("--residual-audit", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidate-profile", default="rtmpose-m-halpe26-384x288")
    parser.add_argument("--device", default="0")
    parser.add_argument("--roi-margin", type=float, default=0.30)
    parser.add_argument("--min-roi-size", type=int, default=8)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; experiments are immutable")
    if not 0 <= args.roi_margin <= 1 or args.min_roi_size < 1:
        parser.error("candidate ROI settings are invalid")

    router_report = json.loads(args.router_report.read_text(encoding="utf-8"))
    residual = json.loads(args.residual_audit.read_text(encoding="utf-8"))
    validate_pose_observability_residual_audit_sources(residual)
    video_id = str(router_report.get("video_id", ""))
    scope = next(
        (item for item in residual["candidate_frame_scope"] if item["video_id"] == video_id),
        None,
    )
    if scope is None:
        parser.error("residual audit does not contain this video")
    report_binding = next(
        (
            item
            for item in residual["sources"]["router_reports"]
            if Path(item["path"]).resolve() == args.router_report.resolve()
        ),
        None,
    )
    if report_binding is None or str(report_binding["sha256"]).upper() != _sha256(args.router_report):
        parser.error("residual audit does not bind this router report")
    summary_path = Path(router_report["sources"]["summary"]["path"])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if _sha256(summary_path) != str(router_report["sources"]["summary"]["sha256"]).upper():
        parser.error("router report summary binding failed")
    provenance = summary.get("provenance", {})
    if str(provenance.get("video_sha256", "")).upper() != _sha256(args.video):
        parser.error("video differs from scoring source")
    if _sha256(args.registry) != str(residual["sources"]["registry"]["sha256"]).upper():
        parser.error("registry differs from residual audit")

    baseline_path = Path(router_report["artifacts"]["routed_frames"]["path"])
    timeline_path = Path(router_report["sources"]["primary_timeline"]["path"])
    events_path = Path(router_report["sources"]["events"]["path"])
    indicator_path = Path(router_report["sources"]["indicator_features"]["path"])
    for path, expected, label in (
        (baseline_path, router_report["artifacts"]["routed_frames"]["sha256"], "baseline"),
        (timeline_path, router_report["sources"]["primary_timeline"]["sha256"], "timeline"),
        (events_path, router_report["sources"]["events"]["sha256"], "events"),
        (indicator_path, router_report["sources"]["indicator_features"]["sha256"], "indicator-features"),
    ):
        if _sha256(path) != str(expected).upper():
            parser.error(f"{label} source binding failed")
    baseline_rows = _read_jsonl(baseline_path)
    timeline_rows = _read_jsonl(timeline_path)
    indicator_rows = _read_jsonl(indicator_path)
    target_indexes = {int(value) for value in scope["processed_indexes"]}
    source_indexes = {int(value) for value in scope["source_frame_indexes"]}
    if len(target_indexes) != int(scope["target_frame_count"]) or len(source_indexes) != len(target_indexes):
        parser.error("residual target frame scope is invalid")
    baseline_by_source = {
        int(row["frame"]["index"]): row
        for row in baseline_rows
        if int(row["frame"]["processed_index"]) in target_indexes
    }
    if set(baseline_by_source) != source_indexes:
        parser.error("residual source/processed frame scope differs from baseline")
    timeline = {int(row["processed_index"]): row for row in timeline_rows}

    backend = RtmposePoseBackend(
        args.model,
        args.config,
        device=args.device,
        runtime="pytorch",
        profile="analysis",
        native_keypoint_format="halpe26",
    )
    estimator = PoseEstimator(
        backend, roi_margin=args.roi_margin, min_roi_size_px=args.min_roi_size
    )
    inferred: dict[int, dict[str, Any] | None] = {}
    frame_results: list[dict[str, Any]] = []
    capture = cv2.VideoCapture(str(args.video))
    source_index = 0
    last_source = max(source_indexes)
    while source_index <= last_source:
        ok, frame = capture.read()
        if not ok:
            capture.release()
            parser.error(f"video ended before residual source frame {last_source}")
        baseline = baseline_by_source.get(source_index)
        if baseline is not None:
            processed_index = int(baseline["frame"]["processed_index"])
            track_id = timeline[processed_index].get("source_track_id")
            detections = [
                item
                for item in baseline.get("detections", [])
                if item.get("track_id") == track_id and item.get("class_name") == "player"
            ]
            pose = None
            reason = "candidate_pose_produced"
            if not isinstance(track_id, int) or len(detections) != 1:
                reason = "selected_track_or_detection_unavailable"
            else:
                poses = estimator.estimate(
                    frame,
                    detections,
                    max_players=1,
                    timestamp_ms=int(baseline["frame"]["timestamp_ms"]),
                )
                pose = poses[0] if poses else None
                if pose is None:
                    reason = "candidate_pose_unavailable"
            inferred[processed_index] = pose
            frame_results.append(
                {
                    "processed_index": processed_index,
                    "source_frame_index": source_index,
                    "timestamp_ms": int(baseline["frame"]["timestamp_ms"]),
                    "track_id": track_id,
                    "pose_output_produced": pose is not None,
                    "reason": reason,
                }
            )
        source_index += 1
    capture.release()

    normalized_rows: list[dict[str, Any]] = []
    for baseline in baseline_rows:
        processed_index = int(baseline["frame"]["processed_index"])
        if processed_index not in target_indexes:
            normalized_rows.append(baseline)
            continue
        track_id = timeline[processed_index].get("source_track_id")
        row = dict(baseline)
        poses = [
            pose
            for pose in baseline.get("poses", [])
            if pose.get("person_track_id") != track_id
        ]
        candidate_pose = inferred.get(processed_index)
        if candidate_pose is not None:
            poses.append(candidate_pose)
        row["poses"] = poses
        normalized_rows.append(row)

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    required_joints = pose_required_joints_from_registry(registry)
    selected, router_audit = select_required_joint_superset_frames(
        baseline_rows=baseline_rows,
        candidate_rows=normalized_rows,
        primary_timeline=timeline_rows,
        target_processed_indexes=target_indexes,
        required_joints=required_joints,
    )
    routed_rows = (
        compose_disjoint_pose_policy_rows(
            baseline_rows=baseline_rows,
            timeline_rows=timeline_rows,
            experiments=[
                {
                    "name": "high_resolution_required_joint_superset",
                    "frame_rows": normalized_rows,
                    "target_processed_indexes": selected,
                }
            ],
        )[0]
        if selected
        else baseline_rows
    )

    args.output_directory.mkdir(parents=True, exist_ok=False)
    candidate_path = args.output_directory / "frames.high-resolution-candidate.jsonl"
    routed_path = args.output_directory / "frames.high-resolution-routed.jsonl"
    _write_jsonl(candidate_path, normalized_rows)
    _write_jsonl(routed_path, routed_rows)
    current_profile = str(router_report["settings"]["baseline_pose_profile"]) + "-m68-routed"
    experimental_profile = args.candidate_profile + "-required-joint-superset-experimental"
    comparison = evaluate_fixed_boundary_pose_ab_files(
        events_path=events_path,
        registry_path=args.registry,
        model_inputs={
            current_profile: {
                "frames_path": baseline_path,
                "primary_timeline_path": timeline_path,
                "model_sha256": str(router_report["settings"]["pose_model_sha256"]),
                "pose_backend": "rtmpose",
                "pose_profile": current_profile,
            },
            experimental_profile: {
                "frames_path": routed_path,
                "primary_timeline_path": timeline_path,
                "model_sha256": _sha256(args.model),
                "pose_backend": "rtmpose",
                "pose_profile": experimental_profile,
            },
        },
        boundary_source_kind="candidate",
        boundary_source_label="candidate_boundaries_not_truth_m69_high_resolution",
        source_id=f"{video_id}:m69-high-resolution-experiment",
    )
    comparison_path = args.output_directory / "fixed-boundary-comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    vector_impact = feature_vector_transitions(
        comparison, current_profile, experimental_profile
    )
    operational_impact = operational_transitions_with_preserved_source_gate(
        comparison, current_profile, experimental_profile, indicator_rows
    )
    current_feature, experimental_feature = _balanced(vector_impact)
    current_operational, experimental_operational = _balanced(operational_impact)
    residual_identities = {
        (str(item["event_id"]), str(item["indicator_id"]))
        for item in residual["residual_items"]
        if item["video_id"] == video_id
    }
    recovered_identities = {
        (item["event_id"], item["indicator_id"])
        for item in operational_impact["recovered_indicator_instances"]
    }
    residual_recovered = sorted(residual_identities & recovered_identities)
    regressions = int(vector_impact["regressed_indicator_instance_count"]) + int(
        operational_impact["regressed_indicator_instance_count"]
    )
    report = {
        "schema_version": "1.0.0",
        "experiment_version": EXPERIMENT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "experimental_regression_free_high_resolution_candidate_not_production"
            if regressions == 0
            else "experimental_high_resolution_candidate_rejected_due_to_regression"
        ),
        "video_id": video_id,
        "sources": {
            "video": _source(args.video),
            "router_report": _source(args.router_report),
            "residual_audit": _source(args.residual_audit),
            "baseline_frames": _source(baseline_path),
            "primary_timeline": _source(timeline_path),
            "events": _source(events_path),
            "indicator_features": _source(indicator_path),
            "registry": _source(args.registry),
            "candidate_model": _source(args.model),
            "candidate_config": _source(args.config),
        },
        "artifacts": {
            "candidate_frames": _source(candidate_path),
            "routed_frames": _source(routed_path),
            "fixed_boundary_comparison": _source(comparison_path),
        },
        "settings": {
            "baseline_profile": current_profile,
            "candidate_profile": args.candidate_profile,
            "candidate_input_size_hw": [384, 288],
            "candidate_roi_margin": args.roi_margin,
            "candidate_min_roi_size_px": args.min_roi_size,
            "required_joints": list(required_joints),
        },
        "inference": {
            "target_frame_count": len(target_indexes),
            "pose_output_produced": sum(
                item["pose_output_produced"] for item in frame_results
            ),
            "pose_output_missing": sum(
                not item["pose_output_produced"] for item in frame_results
            ),
            "frame_results": sorted(
                frame_results, key=lambda item: item["processed_index"]
            ),
        },
        "router_audit": router_audit,
        "feature_vector_impact": vector_impact,
        "operational_measurement_impact": operational_impact,
        "residual_impact": {
            "input_residual_indicator_instance_count": len(residual_identities),
            "recovered_residual_indicator_instance_count": len(residual_recovered),
            "remaining_residual_indicator_instance_count": len(residual_identities)
            - len(residual_recovered),
            "recovered_residual_indicator_instances": [
                {"event_id": event_id, "indicator_id": indicator_id}
                for event_id, indicator_id in residual_recovered
            ],
        },
        "counts": {
            "indicator_instances": len(indicator_rows),
            "current_feature_complete": current_feature,
            "experimental_feature_complete": experimental_feature,
            "current_operational_measured": current_operational,
            "experimental_operational_measured": experimental_operational,
        },
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "current_video_regression_free": regressions == 0,
            "next_required_evidence": (
                "manual corrected keypoints and preregistered independent-video "
                "release validation"
            ),
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "production_enabled": False,
            "automatic_profile_fallback_enabled": False,
            "feature_gate_modified": False,
            "measurement_gate_modified": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
        },
    }
    validate_residual_high_resolution_experiment_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "target_frames": len(target_indexes),
                "selected_frames": len(selected),
                "feature_recovered": vector_impact[
                    "recovered_indicator_instance_count"
                ],
                "feature_regressed": vector_impact[
                    "regressed_indicator_instance_count"
                ],
                "operational_recovered": operational_impact[
                    "recovered_indicator_instance_count"
                ],
                "operational_regressed": operational_impact[
                    "regressed_indicator_instance_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
