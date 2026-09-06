#!/usr/bin/env python3
"""Run RTMPose-X on the exact M71 residual scope and strict M70 router baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.pose_observability_residual import (
    operational_transitions_with_preserved_source_gate,
)
from rallymate_evaluation.pose_observability_router import (
    pose_required_joints_from_registry,
    select_required_joint_superset_frames,
)
from rallymate_evaluation.pose_policy_composition import compose_disjoint_pose_policy_rows
from rallymate_evaluation.pose_x_operational import (
    PoseXOperationalError,
    assert_m71_comparable_scope,
    comparable_frame_identity,
    compare_recovery_sets,
    expected_per_video_status,
    latency_distribution,
    load_x_operational_protocol,
    source_binding,
    validate_per_video_report,
    verify_source_binding,
)
from rallymate_evaluation.small_roi_recovery import feature_vector_transitions
from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.rtmpose_backend import RtmposePoseBackend

try:
    from scripts.build_m70_anchored_pose_extension import (
        validate_anchored_pose_extension_report,
    )
    from scripts.run_m71_larger_pose_model_extension import (
        validate_larger_pose_model_extension_report,
    )
    from scripts.run_residual_pose_profile_ablation import (
        validate_pose_profile_ablation_report,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from build_m70_anchored_pose_extension import (
        validate_anchored_pose_extension_report,
    )
    from run_m71_larger_pose_model_extension import (
        validate_larger_pose_model_extension_report,
    )
    from run_residual_pose_profile_ablation import (
        validate_pose_profile_ablation_report,
    )


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PoseXOperationalError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PoseXOperationalError(f"{label} must be a JSON object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    result = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not result or any(not isinstance(row, dict) for row in result):
        raise PoseXOperationalError(f"invalid JSONL: {path}")
    return result


def _selected_rows(path: Path, targets: set[int]) -> list[dict[str, Any]]:
    result = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                index = int(row["frame"]["processed_index"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise PoseXOperationalError(
                    f"invalid original frame at line {line_number}"
                ) from exc
            if index in targets:
                result.append(row)
    if len(result) != len(targets):
        raise PoseXOperationalError("original frames do not cover the exact target scope")
    return result


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def _same_source(left: dict[str, Any], right_path: Path) -> bool:
    return (
        Path(str(left.get("path", ""))).resolve() == right_path.resolve()
        and str(left.get("sha256", "")).upper()
        == source_binding(right_path)["sha256"]
    )


def _replace_target_poses(
    rows: list[dict[str, Any]],
    timeline: dict[int, dict[str, Any]],
    inferred: dict[int, dict[str, Any] | None],
    targets: set[int],
) -> list[dict[str, Any]]:
    output = []
    for baseline in rows:
        index = int(baseline["frame"]["processed_index"])
        if index not in targets:
            output.append(baseline)
            continue
        track_id = timeline[index].get("source_track_id")
        row = dict(baseline)
        poses = [
            pose
            for pose in baseline.get("poses", [])
            if pose.get("person_track_id") != track_id
        ]
        pose = inferred.get(index)
        if pose is not None:
            poses.append(pose)
        row["poses"] = poses
        output.append(row)
    return output


def _identity_sha256(values: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def run_video(
    *,
    workspace: Path,
    protocol_path: Path,
    video_id: str,
    device: str,
    output_directory: Path,
) -> dict[str, Any]:
    root = workspace.resolve()
    verified = load_x_operational_protocol(root, protocol_path)
    matches = [item for item in verified["videos"] if item["video_id"] == video_id]
    if len(matches) != 1:
        raise PoseXOperationalError(f"unknown development video ID: {video_id}")
    scope = matches[0]
    output_directory = (
        output_directory.resolve()
        if output_directory.is_absolute()
        else (root / output_directory).resolve()
    )
    if not output_directory.is_relative_to(root):
        raise PoseXOperationalError("output directory must stay inside the workspace")
    if output_directory.exists():
        raise PoseXOperationalError("output directory already exists; experiment is immutable")

    ablation_path = Path(scope["m70_ablation_report"]["path"])
    anchored_path = Path(scope["m70_anchored_report"]["path"])
    m71_path = Path(scope["m71_l_report"]["path"])
    ablation = _json(ablation_path, "M70 ablation report")
    anchored = _json(anchored_path, "M70 anchored report")
    m71 = _json(m71_path, "M71 L report")
    validate_pose_profile_ablation_report(ablation)
    validate_anchored_pose_extension_report(anchored)
    validate_larger_pose_model_extension_report(m71)
    target_identity = assert_m71_comparable_scope(
        ablation,
        m71,
        expected_video_id=video_id,
        expected_target_count=scope["target_frame_count"],
    )
    if anchored.get("video_id") != video_id:
        raise PoseXOperationalError("M70 anchored report video identity drifted")
    if not _same_source(anchored["sources"]["ablation_report"], ablation_path):
        raise PoseXOperationalError("M70 anchored report does not bind the protocol ablation")
    if not _same_source(m71["sources"]["m70_ablation_report"], ablation_path):
        raise PoseXOperationalError("M71 report does not bind the protocol ablation")
    if not _same_source(m71["sources"]["m70_anchored_report"], anchored_path):
        raise PoseXOperationalError("M71 report does not bind the protocol M70 anchor")
    if m71.get("candidate", {}).get("candidate_id") != "rtmpose-l-halpe26-384x288":
        raise PoseXOperationalError("historical M71 report is not the RTMPose-L comparator")
    if m71.get("candidate", {}).get("input_size_hw") != [384, 288]:
        raise PoseXOperationalError("historical M71 L input size drifted")

    current_path = verify_source_binding(
        anchored["sources"]["current_frames"], "M68 current frames"
    )
    baseline_path = verify_source_binding(
        anchored["artifacts"]["anchored_frames"], "M70 anchored frames"
    )
    original_path = verify_source_binding(
        ablation["sources"]["original_frames"], "original frames"
    )
    timeline_path = verify_source_binding(
        anchored["sources"]["primary_timeline"], "primary timeline"
    )
    events_path = verify_source_binding(anchored["sources"]["events"], "events")
    indicator_path = verify_source_binding(
        anchored["sources"]["indicator_features"], "indicator features"
    )
    registry_path = verify_source_binding(
        anchored["sources"]["registry"], "feasibility registry"
    )
    if not _same_source(m71["sources"]["m68_current_frames"], current_path):
        raise PoseXOperationalError("M71 and M97 M68 baselines differ")
    if not _same_source(m71["sources"]["m70_anchored_frames"], baseline_path):
        raise PoseXOperationalError("M71 and M97 M70 router baselines differ")
    for key, path in (
        ("original_frames", original_path),
        ("primary_timeline", timeline_path),
        ("events", events_path),
        ("indicator_features", indicator_path),
        ("registry", registry_path),
    ):
        if not _same_source(m71["sources"][key], path):
            raise PoseXOperationalError(f"M71 and M97 {key} sources differ")

    current_rows = _rows(current_path)
    baseline_rows = _rows(baseline_path)
    timeline_rows = _rows(timeline_path)
    indicator_rows = _rows(indicator_path)
    timeline = {int(row["processed_index"]): row for row in timeline_rows}
    frame_scope = {item["processed_index"]: item for item in target_identity}
    targets = set(frame_scope)
    original_rows = _selected_rows(original_path, targets)
    original_by_source = {
        int(row["frame"]["index"]): row for row in original_rows
    }
    expected_source_indexes = {
        item["source_frame_index"] for item in target_identity
    }
    if set(original_by_source) != expected_source_indexes:
        raise PoseXOperationalError("original source-frame scope differs from M71")

    candidate = verified["candidate"]
    backend = RtmposePoseBackend(
        Path(candidate["checkpoint"]["path"]),
        Path(candidate["config"]["path"]),
        device=device,
        runtime="pytorch",
        profile="analysis",
        native_keypoint_format="halpe26",
    )
    metadata = backend.metadata().to_dict()
    if (
        metadata.get("model_sha256") != candidate["checkpoint"]["sha256"]
        or metadata.get("config_sha256") != candidate["config"]["sha256"]
        or metadata.get("input_size") != [384, 288]
        or metadata.get("profile") != "analysis"
        or metadata.get("native_keypoint_count") != 26
    ):
        raise PoseXOperationalError("loaded X runtime metadata drifted")
    contexts = {
        (item["roi_margin"], item["min_roi_size_px"])
        for item in target_identity
    }
    estimators = {
        context: PoseEstimator(
            backend,
            roi_margin=context[0],
            min_roi_size_px=context[1],
        )
        for context in contexts
    }

    try:
        import torch
    except ImportError as exc:
        raise PoseXOperationalError("PyTorch is unavailable for X inference") from exc

    def sync() -> None:
        if device != "cpu":
            torch.cuda.synchronize()

    inferred: dict[int, dict[str, Any] | None] = {}
    observation_rows = []
    latencies = []
    capture = cv2.VideoCapture(scope["video"]["path"])
    if not capture.isOpened():
        raise PoseXOperationalError(f"could not decode development video {video_id}")
    source_index = 0
    last_source = max(expected_source_indexes)
    try:
        while source_index <= last_source:
            ok, image = capture.read()
            if not ok:
                raise PoseXOperationalError(
                    f"video ended before target source frame {last_source}"
                )
            original = original_by_source.get(source_index)
            if original is not None:
                index = int(original["frame"]["processed_index"])
                item = frame_scope[index]
                context = (item["roi_margin"], item["min_roi_size_px"])
                track_id = timeline[index].get("source_track_id")
                if track_id != item["track_id"]:
                    raise PoseXOperationalError("primary timeline track differs from M71 scope")
                detections = [
                    detection
                    for detection in original.get("detections", [])
                    if detection.get("track_id") == track_id
                    and detection.get("class_name") == "player"
                ]
                pose = None
                reason = "candidate_pose_produced"
                elapsed_ms = 0.0
                if not isinstance(track_id, int) or len(detections) != 1:
                    reason = "selected_track_or_detection_unavailable"
                else:
                    sync()
                    started = time.perf_counter()
                    poses = estimators[context].estimate(
                        image,
                        detections,
                        max_players=1,
                        timestamp_ms=int(original["frame"]["timestamp_ms"]),
                    )
                    sync()
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    latencies.append(elapsed_ms)
                    if len(poses) > 1:
                        raise PoseXOperationalError("one target ROI returned multiple poses")
                    pose = poses[0] if poses else None
                    if pose is None:
                        reason = "candidate_pose_unavailable"
                inferred[index] = pose
                observation_rows.append(
                    {
                        **item,
                        "pose_output_produced": pose is not None,
                        "reason": reason,
                        "latency_ms": (
                            elapsed_ms
                            if reason != "selected_track_or_detection_unavailable"
                            else None
                        ),
                        "pose": pose,
                    }
                )
            source_index += 1
    finally:
        capture.release()
    if len(observation_rows) != scope["target_frame_count"]:
        raise PoseXOperationalError("X inference did not retain the exact target frame count")
    if [comparable_frame_identity(row) for row in observation_rows] != target_identity:
        raise PoseXOperationalError("X observation identities drifted from M71")

    candidate_rows = _replace_target_poses(
        baseline_rows, timeline, inferred, targets
    )
    required_joints = pose_required_joints_from_registry(
        _json(registry_path, "feasibility registry")
    )
    selected, audit = select_required_joint_superset_frames(
        baseline_rows=baseline_rows,
        candidate_rows=candidate_rows,
        primary_timeline=timeline_rows,
        target_processed_indexes=targets,
        required_joints=required_joints,
    )
    routed_rows = (
        compose_disjoint_pose_policy_rows(
            baseline_rows=baseline_rows,
            timeline_rows=timeline_rows,
            experiments=[
                {
                    "name": candidate["candidate_id"],
                    "frame_rows": candidate_rows,
                    "target_processed_indexes": selected,
                }
            ],
        )[0]
        if selected
        else baseline_rows
    )

    output_directory.mkdir(parents=True, exist_ok=False)
    observations_path = output_directory / "x-pose-observations.jsonl"
    routed_path = output_directory / "frames.m70-plus-rtmpose-x.jsonl"
    comparison_path = output_directory / "fixed-boundary-comparison.json"
    _write_rows(observations_path, observation_rows)
    _write_rows(routed_path, routed_rows)

    current_profile = "m68-current-routed"
    routed_profile = "m70-plus-rtmpose-x-384-context-extension"
    m68_router_report = _json(
        verify_source_binding(ablation["sources"]["m68_router_report"], "M68 router report"),
        "M68 router report",
    )
    comparison = evaluate_fixed_boundary_pose_ab_files(
        events_path=events_path,
        registry_path=registry_path,
        model_inputs={
            current_profile: {
                "frames_path": current_path,
                "primary_timeline_path": timeline_path,
                "model_sha256": str(m68_router_report["settings"]["pose_model_sha256"]),
                "pose_backend": "rtmpose",
                "pose_profile": current_profile,
            },
            routed_profile: {
                "frames_path": routed_path,
                "primary_timeline_path": timeline_path,
                "model_sha256": candidate["checkpoint"]["sha256"],
                "pose_backend": "rtmpose",
                "pose_profile": routed_profile,
            },
        },
        boundary_source_kind="candidate",
        boundary_source_label="candidate_boundaries_not_truth_m97_x_operational_extension",
        source_id=f"{video_id}:m97-x-operational-extension",
    )
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    feature = feature_vector_transitions(comparison, current_profile, routed_profile)
    operational = operational_transitions_with_preserved_source_gate(
        comparison, current_profile, routed_profile, indicator_rows
    )
    comparison_m70 = {
        "feature_vector": compare_recovery_sets(
            feature, anchored["comparison_to_m68"]["feature_vector"]
        ),
        "operational_measurement": compare_recovery_sets(
            operational, anchored["comparison_to_m68"]["operational_measurement"]
        ),
    }
    comparison_m71 = {
        "feature_vector": compare_recovery_sets(
            feature, m71["comparison_to_m68"]["feature_vector"]
        ),
        "operational_measurement": compare_recovery_sets(
            operational, m71["comparison_to_m68"]["operational_measurement"]
        ),
    }
    inference_count = sum(row["pose_output_produced"] is True for row in observation_rows)
    report = {
        "schema_version": "1.0.0",
        "report_version": "rtmpose-x-operational-extension-v1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_validation",
        "video_id": video_id,
        "sources": {
            "protocol": verified["protocol"],
            "production_registry": verified["production_default"]["registry"],
            "candidate_registry": candidate["registry"],
            "candidate_model": candidate["checkpoint"],
            "candidate_config": candidate["config"],
            "video": scope["video"],
            "m70_ablation_report": scope["m70_ablation_report"],
            "m70_anchored_report": scope["m70_anchored_report"],
            "m71_l_report": scope["m71_l_report"],
            "m68_current_frames": source_binding(current_path),
            "m70_anchored_frames": source_binding(baseline_path),
            "original_frames": source_binding(original_path),
            "primary_timeline": source_binding(timeline_path),
            "events": source_binding(events_path),
            "indicator_features": source_binding(indicator_path),
            "feasibility_registry": source_binding(registry_path),
        },
        "artifacts": {
            "x_pose_observations": source_binding(observations_path),
            "m70_plus_x_frames": source_binding(routed_path),
            "fixed_boundary_comparison": source_binding(comparison_path),
        },
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "runtime_metadata": metadata,
            "profile": "analysis",
            "flip_test": True,
            "native_keypoint_format": "halpe26",
            "input_size_hw": [384, 288],
        },
        "comparability": {
            "M71_L_candidate_id": m71["candidate"]["candidate_id"],
            "target_identity_sha256": _identity_sha256(target_identity),
            "same_processed_and_source_frames_as_M71": True,
            "same_timestamps_tracks_and_ROI_contexts_as_M71": True,
            "same_analysis_profile_and_flip_test_as_M71": True,
            "same_M70_router_baseline_as_M71": True,
            "same_M68_fixed_boundary_comparison_baseline_as_M71": True,
        },
        "inference": {
            "target_frame_count": len(observation_rows),
            "observation_row_count": len(observation_rows),
            "pose_output_produced": inference_count,
            "pose_output_missing": len(observation_rows) - inference_count,
            "reason_counts": dict(sorted(Counter(row["reason"] for row in observation_rows).items())),
            "context_counts": {
                f"margin={margin:.2f},min={minimum}": sum(
                    row["roi_margin"] == margin and row["min_roi_size_px"] == minimum
                    for row in observation_rows
                )
                for margin, minimum in sorted(contexts)
            },
            "latency_ms": latency_distribution(latencies),
            "decode_strategy": "sequential_from_source_frame_zero_matching_historical_M71",
        },
        "router_audit": audit,
        "comparison_to_m68": {
            "feature_vector": feature,
            "operational_measurement": operational,
        },
        "comparison_to_m70": comparison_m70,
        "comparison_to_m71": comparison_m71,
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "current_video_required_joint_router_result_only": True,
            "operational_gain_is_observability_not_accuracy": True,
            "next_required_evidence": (
                "human corrected Halpe26 keypoints and preregistered independent-video validation"
            ),
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "sealed_holdout_opened_or_hashed": False,
            "production_enabled": False,
            "automatic_profile_fallback_enabled": False,
            "feature_gate_modified": False,
            "measurement_gate_modified": False,
            "event_boundaries_modified": False,
            "routing_uses_feature_values": False,
            "routing_uses_event_outcomes": False,
            "routing_uses_grades_or_thresholds": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
        },
    }
    report["status"] = expected_per_video_status(report)
    validate_per_video_report(report)
    report_path = output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("models/rtmpose/m97-x-operational-protocol.json"),
    )
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    report = run_video(
        workspace=args.root,
        protocol_path=args.protocol,
        video_id=args.video_id,
        device=args.device,
        output_directory=args.output_directory,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "video_id": report["video_id"],
                "report": str((args.output_directory / "report.json").resolve()),
                "target_frames": report["inference"]["target_frame_count"],
                "pose_output_produced": report["inference"]["pose_output_produced"],
                "selected_frames": report["router_audit"]["selected_frame_count"],
                "feature_recovered_from_M68": report["comparison_to_m68"]["feature_vector"]["recovered_indicator_instance_count"],
                "operational_recovered_from_M68": report["comparison_to_m68"]["operational_measurement"]["recovered_indicator_instance_count"],
                "additional_operational_over_M71": report["comparison_to_m71"]["operational_measurement"]["additional_recovery_count"],
                "lost_M71_operational": report["comparison_to_m71"]["operational_measurement"]["lost_reference_recovery_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
