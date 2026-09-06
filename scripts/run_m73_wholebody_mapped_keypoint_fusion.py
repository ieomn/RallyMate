#!/usr/bin/env python3
"""Use WholeBody133 only to fill explicitly mapped missing M72 keypoints."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.pose_observability_router import (
    pose_required_joints_from_registry,
)
from rallymate_evaluation.pose_policy_composition import (
    add_missing_keypoints_from_mapped_topology_candidate_rows,
)
from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.metadata import keypoint_schema
from rallymate_vision.pose.rtmpose_backend import RtmposePoseBackend

try:
    from scripts.run_m71_larger_pose_model_extension import (
        _bound,
        _identity_set,
        _replace_selected_poses,
        _rows,
        _sha256,
        _source,
        _write_rows,
        feature_vector_transitions,
        operational_transitions_with_preserved_source_gate,
        validate_larger_pose_model_extension_report,
    )
    from scripts.run_m72_additive_keypoint_fusion import (
        FINE_FOOT_REFERENCE_JOINTS,
        validate_additive_keypoint_fusion_report,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from run_m71_larger_pose_model_extension import (
        _bound,
        _identity_set,
        _replace_selected_poses,
        _rows,
        _sha256,
        _source,
        _write_rows,
        feature_vector_transitions,
        operational_transitions_with_preserved_source_gate,
        validate_larger_pose_model_extension_report,
    )
    from run_m72_additive_keypoint_fusion import (
        FINE_FOOT_REFERENCE_JOINTS,
        validate_additive_keypoint_fusion_report,
    )


REPORT_VERSION = "wholebody133-mapped-keypoint-fusion-v1.0.0"
EXPECTED_BASELINE_FORMAT = "halpe26"
EXPECTED_CANDIDATE_FORMAT = "coco_wholebody133"


def _validate_candidate(
    *, candidate: dict[str, Any], model_path: Path, config_path: Path
) -> None:
    expected_config = str(candidate.get("config_runtime_relative_path", "")).replace(
        "\\", "/"
    )
    actual_config = str(config_path.resolve()).replace("\\", "/")
    if (
        candidate.get("schema_version") != "1.0.0"
        or candidate.get("candidate_id")
        != "rtmpose-m-coco-wholebody133-256x192"
        or candidate.get("status")
        != "visual_and_contract_candidate_not_scoring_promoted"
        or candidate.get("native_keypoint_format") != EXPECTED_CANDIDATE_FORMAT
        or int(candidate.get("native_keypoint_count", -1)) != 133
        or candidate.get("input_size_hw") != [256, 192]
        or str(candidate.get("checkpoint")) != model_path.name
        or str(candidate.get("checkpoint_sha256", "")).upper()
        != _sha256(model_path)
        or int(candidate.get("checkpoint_bytes", -1)) != model_path.stat().st_size
        or not expected_config
        or not actual_config.endswith(expected_config)
    ):
        raise ValueError("WholeBody133 candidate files differ from the registry")


def _semantic_map(registry: dict[str, Any]) -> dict[str, str]:
    required = set(pose_required_joints_from_registry(registry)) | set(
        FINE_FOOT_REFERENCE_JOINTS
    )
    baseline_names = {
        str(point["name"])
        for point in keypoint_schema(EXPECTED_BASELINE_FORMAT)["keypoints"]
    }
    candidate_names = {
        str(point["name"])
        for point in keypoint_schema(EXPECTED_CANDIDATE_FORMAT)["keypoints"]
    }
    if not required or not required <= baseline_names or not required <= candidate_names:
        raise ValueError("required scoring joints do not have an exact topology map")
    return {name: name for name in sorted(required)}


def validate_wholebody_mapped_keypoint_fusion_report(
    report: dict[str, Any],
) -> None:
    if (
        report.get("schema_version") != "1.0.0"
        or report.get("report_version") != REPORT_VERSION
    ):
        raise ValueError("unsupported M73 WholeBody mapped fusion report")
    if report.get("status") not in {
        "experimental_wholebody_mapped_fusion_regression_free_requires_truth",
        "experimental_wholebody_mapped_fusion_rejected",
    }:
        raise ValueError("unsafe M73 report status")
    inference = report.get("inference", {})
    target = int(inference.get("target_frame_count", -1))
    if target < 1 or int(inference.get("pose_output_produced", -1)) + int(
        inference.get("pose_output_missing", -1)
    ) != target:
        raise ValueError("M73 inference accounting is inconsistent")
    audit = report.get("fusion_audit", {})
    for field, expected in {
        "baseline_keypoint_format": EXPECTED_BASELINE_FORMAT,
        "candidate_keypoint_format": EXPECTED_CANDIDATE_FORMAT,
        "mapping_is_one_to_one": True,
        "baseline_valid_coordinates_overwritten": 0,
        "baseline_topology_preserved": True,
        "non_target_frames_preserved": True,
        "non_pose_frame_data_preserved": True,
        "non_selected_poses_preserved": True,
        "explicit_topology_map_required": True,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
    }.items():
        if audit.get(field) != expected:
            raise ValueError(f"unsafe M73 fusion audit field: {field}")
    mapping = audit.get("target_to_candidate_joint_names")
    if not isinstance(mapping, dict) or not mapping or any(
        target_name != source_name for target_name, source_name in mapping.items()
    ):
        raise ValueError("M73 may only use the registered same-name semantic map")
    if int(audit.get("changed_frame_count", -1)) + int(
        audit.get("unchanged_target_frame_count", -1)
    ) != target:
        raise ValueError("M73 fusion target accounting is inconsistent")
    comparison = report.get("comparison_to_m68", {})
    preservation = report.get("m72_preservation", {})
    regressions = sum(
        int(comparison.get(kind, {}).get("regressed_indicator_instance_count", -1))
        for kind in ("feature_vector", "operational_measurement")
    )
    safe = (
        regressions == 0
        and int(preservation.get("lost_m72_feature_recovery_count", -1)) == 0
        and int(preservation.get("lost_m72_operational_recovery_count", -1)) == 0
    )
    expected_status = (
        "experimental_wholebody_mapped_fusion_regression_free_requires_truth"
        if safe
        else "experimental_wholebody_mapped_fusion_rejected"
    )
    if report.get("status") != expected_status:
        raise ValueError("M73 report hides a regression")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get(
        "candidate_promoted"
    ) is not False:
        raise ValueError("M73 experiment changed production")
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_fallback_enabled",
        "feature_gate_modified",
        "measurement_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"unsafe M73 claim: {field}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--m72-report", type=Path, required=True)
    parser.add_argument("--candidate-registry", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; experiments are immutable")

    m72 = json.loads(args.m72_report.read_text(encoding="utf-8"))
    validate_additive_keypoint_fusion_report(m72)
    m71_path = _bound(m72["sources"]["m71_report"], "M71 report")
    m71 = json.loads(m71_path.read_text(encoding="utf-8"))
    validate_larger_pose_model_extension_report(m71)
    if m71.get("video_id") != m72.get("video_id"):
        parser.error("M71 and M72 video IDs differ")
    video_path = _bound(m71["sources"]["video"], "video")
    if video_path.resolve() != args.video.resolve():
        parser.error("video differs from the M71 source binding")

    candidate_registry = json.loads(
        args.candidate_registry.read_text(encoding="utf-8")
    )
    _validate_candidate(
        candidate=candidate_registry,
        model_path=args.model,
        config_path=args.config,
    )
    baseline_path = _bound(m72["artifacts"]["fused_frames"], "M72 fused frames")
    current_path = _bound(m72["sources"]["m68_current_frames"], "M68 frames")
    original_path = _bound(m71["sources"]["original_frames"], "original frames")
    timeline_path = _bound(m72["sources"]["primary_timeline"], "primary timeline")
    events_path = _bound(m72["sources"]["events"], "events")
    indicator_path = _bound(
        m72["sources"]["indicator_features"], "indicator features"
    )
    registry_path = _bound(m72["sources"]["registry"], "feasibility registry")
    previous_comparison_path = _bound(
        m72["artifacts"]["fixed_boundary_comparison"], "M72 comparison"
    )

    baseline_rows = _rows(baseline_path)
    original_rows = _rows(original_path)
    timeline_rows = _rows(timeline_path)
    indicator_rows = _rows(indicator_path)
    timeline = {int(row["processed_index"]): row for row in timeline_rows}
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    semantic_map = _semantic_map(registry)
    frame_scope = {
        int(item["processed_index"]): item for item in m71["inference"]["frame_results"]
    }
    targets = set(frame_scope)
    original_by_source = {
        int(row["frame"]["index"]): row
        for row in original_rows
        if int(row["frame"]["processed_index"]) in targets
    }
    expected_source_indexes = {
        int(item["source_frame_index"]) for item in frame_scope.values()
    }
    if set(original_by_source) != expected_source_indexes:
        parser.error("M73 target scope differs from original frames")
    contexts = {
        (float(item["roi_margin"]), int(item["min_roi_size_px"]))
        for item in frame_scope.values()
    }
    if not contexts <= {(0.15, 32), (0.30, 32), (0.15, 8)}:
        parser.error("M73 crop context is outside the frozen M71 scope")

    backend = RtmposePoseBackend(
        args.model,
        args.config,
        device=args.device,
        runtime="pytorch",
        profile="analysis",
        native_keypoint_format=EXPECTED_CANDIDATE_FORMAT,
    )
    estimators = {
        context: PoseEstimator(
            backend, roi_margin=context[0], min_roi_size_px=context[1]
        )
        for context in contexts
    }
    inferred: dict[int, dict[str, Any] | None] = {}
    frame_results: list[dict[str, Any]] = []
    capture = cv2.VideoCapture(str(args.video))
    source_index = 0
    last_source = max(expected_source_indexes)
    while source_index <= last_source:
        ok, image = capture.read()
        if not ok:
            capture.release()
            parser.error(f"video ended before source frame {last_source}")
        original = original_by_source.get(source_index)
        if original is not None:
            index = int(original["frame"]["processed_index"])
            item = frame_scope[index]
            context = (float(item["roi_margin"]), int(item["min_roi_size_px"]))
            track_id = timeline[index].get("source_track_id")
            detections = [
                detection
                for detection in original.get("detections", [])
                if detection.get("track_id") == track_id
                and detection.get("class_name") == "player"
            ]
            pose = None
            reason = "candidate_pose_produced"
            if not isinstance(track_id, int) or len(detections) != 1:
                reason = "selected_track_or_detection_unavailable"
            else:
                poses = estimators[context].estimate(
                    image,
                    detections,
                    max_players=1,
                    timestamp_ms=int(original["frame"]["timestamp_ms"]),
                )
                pose = poses[0] if poses else None
                if pose is None:
                    reason = "candidate_pose_unavailable"
            inferred[index] = pose
            frame_results.append(
                {
                    "processed_index": index,
                    "source_frame_index": source_index,
                    "timestamp_ms": int(original["frame"]["timestamp_ms"]),
                    "track_id": track_id,
                    "roi_margin": context[0],
                    "min_roi_size_px": context[1],
                    "pose_output_produced": pose is not None,
                    "reason": reason,
                }
            )
        source_index += 1
    capture.release()

    candidate_rows = _replace_selected_poses(
        baseline_rows, timeline, inferred, targets
    )
    fused_rows, fusion_audit = (
        add_missing_keypoints_from_mapped_topology_candidate_rows(
            baseline_rows=baseline_rows,
            candidate_rows=candidate_rows,
            timeline_rows=timeline_rows,
            target_processed_indexes=targets,
            target_to_candidate_joint_names=semantic_map,
            baseline_keypoint_format=EXPECTED_BASELINE_FORMAT,
            candidate_keypoint_format=EXPECTED_CANDIDATE_FORMAT,
        )
    )
    args.output_directory.mkdir(parents=True, exist_ok=False)
    candidate_path = args.output_directory / "frames.wholebody133-candidate.jsonl"
    fused_path = (
        args.output_directory / "frames.m72-plus-wholebody133-mapped-keypoints.jsonl"
    )
    _write_rows(candidate_path, candidate_rows)
    _write_rows(fused_path, fused_rows)

    previous_comparison = json.loads(
        previous_comparison_path.read_text(encoding="utf-8")
    )
    current_profile = str(previous_comparison["model_order"][0])
    current_model_sha = str(
        previous_comparison["models"][current_profile]["model_sha256"]
    )
    fused_profile = "m73-m72-plus-wholebody133-mapped-missing-keypoints"
    comparison = evaluate_fixed_boundary_pose_ab_files(
        events_path=events_path,
        registry_path=registry_path,
        model_inputs={
            current_profile: {
                "frames_path": current_path,
                "primary_timeline_path": timeline_path,
                "model_sha256": current_model_sha,
                "pose_backend": "rtmpose",
                "pose_profile": current_profile,
            },
            fused_profile: {
                "frames_path": fused_path,
                "primary_timeline_path": timeline_path,
                "model_sha256": _sha256(args.model),
                "pose_backend": "rtmpose_mapped_topology_additive_observability_experiment",
                "pose_profile": fused_profile,
            },
        },
        boundary_source_kind="candidate",
        boundary_source_label=(
            "candidate_boundaries_not_truth_m73_wholebody_mapped_fusion"
        ),
        source_id=f"{m72['video_id']}:m73-wholebody-mapped-fusion",
    )
    comparison_path = args.output_directory / "fixed-boundary-comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    feature = feature_vector_transitions(comparison, current_profile, fused_profile)
    operational = operational_transitions_with_preserved_source_gate(
        comparison, current_profile, fused_profile, indicator_rows
    )
    m72_feature = m72["comparison_to_m68"]["feature_vector"]
    m72_operational = m72["comparison_to_m68"]["operational_measurement"]
    feature_recovered = _identity_set(feature, "recovered_indicator_instances")
    operational_recovered = _identity_set(
        operational, "recovered_indicator_instances"
    )
    m72_feature_recovered = _identity_set(
        m72_feature, "recovered_indicator_instances"
    )
    m72_operational_recovered = _identity_set(
        m72_operational, "recovered_indicator_instances"
    )
    preservation = {
        "m72_feature_recovery_count": len(m72_feature_recovered),
        "fused_feature_recovery_count": len(feature_recovered),
        "additional_feature_recovery_count": len(
            feature_recovered - m72_feature_recovered
        ),
        "lost_m72_feature_recovery_count": len(
            m72_feature_recovered - feature_recovered
        ),
        "m72_operational_recovery_count": len(m72_operational_recovered),
        "fused_operational_recovery_count": len(operational_recovered),
        "additional_operational_recovery_count": len(
            operational_recovered - m72_operational_recovered
        ),
        "lost_m72_operational_recovery_count": len(
            m72_operational_recovered - operational_recovered
        ),
        "additional_operational_recoveries": [
            {"event_id": event_id, "indicator_id": indicator_id}
            for event_id, indicator_id in sorted(
                operational_recovered - m72_operational_recovered
            )
        ],
    }
    safe = (
        feature["regressed_indicator_instance_count"] == 0
        and operational["regressed_indicator_instance_count"] == 0
        and preservation["lost_m72_feature_recovery_count"] == 0
        and preservation["lost_m72_operational_recovery_count"] == 0
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "experimental_wholebody_mapped_fusion_regression_free_requires_truth"
            if safe
            else "experimental_wholebody_mapped_fusion_rejected"
        ),
        "video_id": str(m72["video_id"]),
        "sources": {
            "video": _source(args.video),
            "m72_report": _source(args.m72_report),
            "m72_fused_frames": _source(baseline_path),
            "m68_current_frames": _source(current_path),
            "original_frames": _source(original_path),
            "primary_timeline": _source(timeline_path),
            "events": _source(events_path),
            "indicator_features": _source(indicator_path),
            "registry": _source(registry_path),
            "candidate_registry": _source(args.candidate_registry),
            "candidate_model": _source(args.model),
            "candidate_config": _source(args.config),
        },
        "candidate": {
            "candidate_id": candidate_registry["candidate_id"],
            "native_keypoint_format": EXPECTED_CANDIDATE_FORMAT,
            "native_keypoint_count": 133,
            "input_size_hw": [256, 192],
            "checkpoint_url": candidate_registry["checkpoint_url"],
        },
        "artifacts": {
            "candidate_frames": _source(candidate_path),
            "fused_frames": _source(fused_path),
            "fixed_boundary_comparison": _source(comparison_path),
        },
        "inference": {
            "target_frame_count": len(frame_results),
            "pose_output_produced": sum(
                item["pose_output_produced"] is True for item in frame_results
            ),
            "pose_output_missing": sum(
                item["pose_output_produced"] is not True for item in frame_results
            ),
            "frame_results": frame_results,
        },
        "fusion_audit": fusion_audit,
        "comparison_to_m68": {
            "feature_vector": feature,
            "operational_measurement": operational,
        },
        "m72_preservation": preservation,
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "current_video_regression_free": safe,
            "next_required_evidence": (
                "independently adjudicated cross-topology per-joint error and a "
                "preregistered independent-video release validation"
            ),
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "production_enabled": False,
            "automatic_fallback_enabled": False,
            "feature_gate_modified": False,
            "measurement_gate_modified": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
        },
    }
    validate_wholebody_mapped_keypoint_fusion_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "candidate_pose_outputs": report["inference"]["pose_output_produced"],
                "changed_frames": fusion_audit["changed_frame_count"],
                "added_joint_observations": fusion_audit[
                    "added_valid_joint_observation_count"
                ],
                "additional_feature_recovered": preservation[
                    "additional_feature_recovery_count"
                ],
                "additional_operational_recovered": preservation[
                    "additional_operational_recovery_count"
                ],
                "lost_m72_operational_recovered": preservation[
                    "lost_m72_operational_recovery_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
