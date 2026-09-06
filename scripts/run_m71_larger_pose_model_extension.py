#!/usr/bin/env python3
"""Try RTMPose-L only where it strictly extends the M70 Pose evidence."""

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
)
from rallymate_evaluation.pose_observability_router import (
    pose_required_joints_from_registry,
    select_required_joint_superset_frames,
)
from rallymate_evaluation.pose_policy_composition import compose_disjoint_pose_policy_rows
from rallymate_evaluation.small_roi_recovery import feature_vector_transitions
from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.rtmpose_backend import RtmposePoseBackend

try:
    from scripts.build_m70_anchored_pose_extension import (
        validate_anchored_pose_extension_report,
    )
    from scripts.run_residual_pose_profile_ablation import (
        validate_pose_profile_ablation_report,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from build_m70_anchored_pose_extension import (
        validate_anchored_pose_extension_report,
    )
    from run_residual_pose_profile_ablation import (
        validate_pose_profile_ablation_report,
    )


REPORT_VERSION = "larger-pose-model-extension-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _bound(binding: dict[str, Any], label: str) -> Path:
    path = Path(str(binding.get("path", "")))
    if not path.is_file() or _sha256(path) != str(binding.get("sha256", "")).upper():
        raise ValueError(f"{label} source binding failed")
    return path


def _rows(path: Path) -> list[dict[str, Any]]:
    result = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not result or any(not isinstance(row, dict) for row in result):
        raise ValueError(f"invalid JSONL: {path}")
    return result


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def _identity_set(impact: dict[str, Any], field: str) -> set[tuple[str, str]]:
    return {
        (str(item["event_id"]), str(item["indicator_id"]))
        for item in impact.get(field, [])
    }


def _replace_selected_poses(
    rows: list[dict[str, Any]],
    timeline: dict[int, dict[str, Any]],
    inferred: dict[int, dict[str, Any] | None],
    targets: set[int],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
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


def _validate_model_candidate(
    *,
    model_registry: dict[str, Any],
    candidate_id: str,
    model_path: Path,
    config_path: Path,
) -> dict[str, Any]:
    matches = [
        item
        for item in model_registry.get("candidates", [])
        if item.get("candidate_id") == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("model registry must contain exactly one candidate")
    candidate = matches[0]
    if (
        str(candidate.get("checkpoint")) != model_path.name
        or str(candidate.get("config")) != config_path.name
        or str(candidate.get("checkpoint_sha256", "")).upper() != _sha256(model_path)
        or int(candidate.get("checkpoint_bytes", -1)) != model_path.stat().st_size
        or candidate.get("input_size_hw") != [384, 288]
    ):
        raise ValueError("model candidate files differ from registry")
    return candidate


def validate_larger_pose_model_extension_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported larger Pose model report")
    if report.get("status") not in {
        "experimental_larger_pose_model_extension_regression_free_requires_truth",
        "experimental_larger_pose_model_extension_rejected",
    }:
        raise ValueError("unsafe larger Pose model status")
    inference = report.get("inference", {})
    target = int(inference.get("target_frame_count", -1))
    if target < 1 or int(inference.get("pose_output_produced", -1)) + int(
        inference.get("pose_output_missing", -1)
    ) != target:
        raise ValueError("larger Pose model inference accounting is inconsistent")
    audit = report.get("router_audit", {})
    if int(audit.get("selected_frame_count", -1)) + int(
        audit.get("rejected_frame_count", -1)
    ) != target:
        raise ValueError("larger Pose model router accounting is inconsistent")
    for field in (
        "selection_uses_feature_values",
        "selection_uses_event_outcomes",
        "selection_uses_grades_or_thresholds",
        "required_joint_validity_regression_allowed",
    ):
        if audit.get(field) is not False:
            raise ValueError(f"unsafe larger Pose model router field: {field}")
    comparison = report.get("comparison_to_m68", {})
    regressions = sum(
        int(comparison[kind].get("regressed_indicator_instance_count", -1))
        for kind in ("feature_vector", "operational_measurement")
    )
    preservation = report.get("m70_preservation", {})
    safe = (
        regressions == 0
        and int(preservation.get("lost_m70_feature_recovery_count", -1)) == 0
        and int(preservation.get("lost_m70_operational_recovery_count", -1)) == 0
    )
    expected = (
        "experimental_larger_pose_model_extension_regression_free_requires_truth"
        if safe
        else "experimental_larger_pose_model_extension_rejected"
    )
    if report.get("status") != expected:
        raise ValueError("larger Pose model report hides a regression")
    if report.get("decision", {}).get("production_default_changed") is not False or report.get(
        "decision", {}
    ).get("candidate_promoted") is not False:
        raise ValueError("larger Pose model experiment changed production")
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
            raise ValueError(f"unsafe larger Pose model claim: {field}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--m70-ablation-report", type=Path, required=True)
    parser.add_argument("--m70-anchored-report", type=Path, required=True)
    parser.add_argument("--model-registry", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; experiments are immutable")

    ablation = json.loads(args.m70_ablation_report.read_text(encoding="utf-8"))
    anchored = json.loads(args.m70_anchored_report.read_text(encoding="utf-8"))
    validate_pose_profile_ablation_report(ablation)
    validate_anchored_pose_extension_report(anchored)
    if ablation.get("video_id") != anchored.get("video_id"):
        parser.error("M70 reports have different video IDs")
    if (
        Path(anchored["sources"]["ablation_report"]["path"]).resolve()
        != args.m70_ablation_report.resolve()
        or str(anchored["sources"]["ablation_report"]["sha256"]).upper()
        != _sha256(args.m70_ablation_report)
    ):
        parser.error("M70 anchored report does not bind the ablation report")
    video_binding = ablation["sources"]["video"]
    if (
        Path(video_binding["path"]).resolve() != args.video.resolve()
        or str(video_binding["sha256"]).upper() != _sha256(args.video)
    ):
        parser.error("video differs from the M70 source binding")

    model_registry = json.loads(args.model_registry.read_text(encoding="utf-8"))
    model_candidate = _validate_model_candidate(
        model_registry=model_registry,
        candidate_id=args.candidate_id,
        model_path=args.model,
        config_path=args.config,
    )
    current_path = _bound(anchored["sources"]["current_frames"], "M68 current frames")
    baseline_path = _bound(anchored["artifacts"]["anchored_frames"], "M70 anchored frames")
    original_path = _bound(ablation["sources"]["original_frames"], "original frames")
    timeline_path = _bound(anchored["sources"]["primary_timeline"], "primary timeline")
    events_path = _bound(anchored["sources"]["events"], "events")
    indicator_path = _bound(anchored["sources"]["indicator_features"], "indicator features")
    registry_path = _bound(anchored["sources"]["registry"], "feasibility registry")

    current_rows = _rows(current_path)
    baseline_rows = _rows(baseline_path)
    original_rows = _rows(original_path)
    timeline_rows = _rows(timeline_path)
    indicator_rows = _rows(indicator_path)
    timeline = {int(row["processed_index"]): row for row in timeline_rows}
    frame_scope = {
        int(item["processed_index"]): item
        for item in ablation["inference"]["frame_results"]
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
        parser.error("M70 target frame scope differs from original frames")

    contexts = {
        (
            float(item["roi_margin"]),
            int(item["min_roi_size_px"]),
        )
        for item in frame_scope.values()
    }
    if not contexts <= {(0.15, 32), (0.30, 32), (0.15, 8)}:
        parser.error("M70 crop context is outside the registered experiment scope")
    backend = RtmposePoseBackend(
        args.model,
        args.config,
        device=args.device,
        runtime="pytorch",
        profile="analysis",
        native_keypoint_format="halpe26",
    )
    estimators = {
        context: PoseEstimator(
            backend,
            roi_margin=context[0],
            min_roi_size_px=context[1],
        )
        for context in contexts
    }
    inferred: dict[int, dict[str, Any] | None] = {}
    results: list[dict[str, Any]] = []
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
            results.append(
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
    required_joints = pose_required_joints_from_registry(
        json.loads(registry_path.read_text(encoding="utf-8"))
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
                    "name": args.candidate_id,
                    "frame_rows": candidate_rows,
                    "target_processed_indexes": selected,
                }
            ],
        )[0]
        if selected
        else baseline_rows
    )
    args.output_directory.mkdir(parents=True, exist_ok=False)
    candidate_path = args.output_directory / "frames.larger-model-candidate.jsonl"
    routed_path = args.output_directory / "frames.m70-plus-larger-model.jsonl"
    _write_rows(candidate_path, candidate_rows)
    _write_rows(routed_path, routed_rows)

    video_id = str(ablation["video_id"])
    current_profile = "m68-current-routed"
    routed_profile = "m70-plus-rtmpose-l-384-context-extension"
    if selected:
        m68_report = json.loads(
            Path(ablation["sources"]["m68_router_report"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        comparison = evaluate_fixed_boundary_pose_ab_files(
            events_path=events_path,
            registry_path=registry_path,
            model_inputs={
                current_profile: {
                    "frames_path": current_path,
                    "primary_timeline_path": timeline_path,
                    "model_sha256": str(m68_report["settings"]["pose_model_sha256"]),
                    "pose_backend": "rtmpose",
                    "pose_profile": current_profile,
                },
                routed_profile: {
                    "frames_path": routed_path,
                    "primary_timeline_path": timeline_path,
                    "model_sha256": _sha256(args.model),
                    "pose_backend": "rtmpose",
                    "pose_profile": routed_profile,
                },
            },
            boundary_source_kind="candidate",
            boundary_source_label="candidate_boundaries_not_truth_m71_larger_pose_model",
            source_id=f"{video_id}:m71-larger-pose-model-extension",
        )
        comparison_path = args.output_directory / "fixed-boundary-comparison.json"
        comparison_path.write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
    else:
        comparison_path = _bound(
            anchored["artifacts"]["fixed_boundary_comparison"],
            "unchanged M70 fixed-boundary comparison",
        )
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        routed_profile = str(comparison["model_order"][1])

    feature = feature_vector_transitions(
        comparison, current_profile, routed_profile
    )
    operational = operational_transitions_with_preserved_source_gate(
        comparison, current_profile, routed_profile, indicator_rows
    )
    m70_feature = anchored["comparison_to_m68"]["feature_vector"]
    m70_operational = anchored["comparison_to_m68"]["operational_measurement"]
    feature_recovered = _identity_set(feature, "recovered_indicator_instances")
    operational_recovered = _identity_set(
        operational, "recovered_indicator_instances"
    )
    m70_feature_recovered = _identity_set(
        m70_feature, "recovered_indicator_instances"
    )
    m70_operational_recovered = _identity_set(
        m70_operational, "recovered_indicator_instances"
    )
    preservation = {
        "m70_feature_recovery_count": len(m70_feature_recovered),
        "extended_feature_recovery_count": len(feature_recovered),
        "additional_feature_recovery_count": len(
            feature_recovered - m70_feature_recovered
        ),
        "lost_m70_feature_recovery_count": len(
            m70_feature_recovered - feature_recovered
        ),
        "m70_operational_recovery_count": len(m70_operational_recovered),
        "extended_operational_recovery_count": len(operational_recovered),
        "additional_operational_recovery_count": len(
            operational_recovered - m70_operational_recovered
        ),
        "lost_m70_operational_recovery_count": len(
            m70_operational_recovered - operational_recovered
        ),
        "additional_operational_recoveries": [
            {"event_id": event_id, "indicator_id": indicator_id}
            for event_id, indicator_id in sorted(
                operational_recovered - m70_operational_recovered
            )
        ],
    }
    safe = (
        feature["regressed_indicator_instance_count"] == 0
        and operational["regressed_indicator_instance_count"] == 0
        and preservation["lost_m70_feature_recovery_count"] == 0
        and preservation["lost_m70_operational_recovery_count"] == 0
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "experimental_larger_pose_model_extension_regression_free_requires_truth"
            if safe
            else "experimental_larger_pose_model_extension_rejected"
        ),
        "video_id": video_id,
        "sources": {
            "video": _source(args.video),
            "m70_ablation_report": _source(args.m70_ablation_report),
            "m70_anchored_report": _source(args.m70_anchored_report),
            "m70_anchored_frames": _source(baseline_path),
            "m68_current_frames": _source(current_path),
            "original_frames": _source(original_path),
            "primary_timeline": _source(timeline_path),
            "events": _source(events_path),
            "indicator_features": _source(indicator_path),
            "registry": _source(registry_path),
            "model_registry": _source(args.model_registry),
            "candidate_model": _source(args.model),
            "candidate_config": _source(args.config),
        },
        "candidate": {
            "candidate_id": args.candidate_id,
            "registry_version": model_registry["registry_version"],
            "checkpoint_url": model_candidate["checkpoint_url"],
            "input_size_hw": model_candidate["input_size_hw"],
            "native_keypoint_format": "halpe26",
        },
        "artifacts": {
            "candidate_frames": _source(candidate_path),
            "routed_frames": _source(routed_path),
            "fixed_boundary_comparison": _source(comparison_path),
        },
        "inference": {
            "target_frame_count": len(results),
            "pose_output_produced": sum(
                item["pose_output_produced"] is True for item in results
            ),
            "pose_output_missing": sum(
                item["pose_output_produced"] is not True for item in results
            ),
            "frame_results": results,
        },
        "router_audit": audit,
        "comparison_to_m68": {
            "feature_vector": feature,
            "operational_measurement": operational,
        },
        "m70_preservation": preservation,
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "current_video_regression_free": safe,
            "next_required_evidence": (
                "manual corrected keypoints and preregistered independent-video validation"
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
    validate_larger_pose_model_extension_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "selected_frames": len(selected),
                "additional_feature_recovered": preservation[
                    "additional_feature_recovery_count"
                ],
                "additional_operational_recovered": preservation[
                    "additional_operational_recovery_count"
                ],
                "lost_m70_operational_recovered": preservation[
                    "lost_m70_operational_recovery_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
