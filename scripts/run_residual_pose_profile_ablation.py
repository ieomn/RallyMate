#!/usr/bin/env python3
"""Isolate a 384x288 Pose profile under the exact M68 crop context."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.pose_crop_context import classify_routed_pose_sources
from rallymate_evaluation.pose_observability_residual import (
    operational_transitions_with_preserved_source_gate,
    validate_pose_observability_residual_audit_sources,
)
from rallymate_evaluation.pose_observability_router import (
    pose_required_joints_from_registry,
    select_required_joint_dominating_candidates,
    select_required_joint_superset_frames,
)
from rallymate_evaluation.pose_policy_composition import compose_disjoint_pose_policy_rows
from rallymate_evaluation.small_roi_recovery import feature_vector_transitions
from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.rtmpose_backend import RtmposePoseBackend


EXPERIMENT_VERSION = "residual-pose-profile-context-ablation-v1.0.0"


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


def _bound(binding: dict[str, Any], label: str) -> Path:
    path = Path(str(binding.get("path", "")))
    if not path.is_file() or _sha256(path) != str(binding.get("sha256", "")).upper():
        raise ValueError(f"{label} source binding failed")
    return path


def _balanced(impact: dict[str, Any]) -> tuple[int, int]:
    transitions = impact.get("status_transitions", {})
    current = int(transitions.get("measured_to_measured", 0)) + int(
        transitions.get("measured_to_unavailable", 0)
    )
    experimental = int(transitions.get("measured_to_measured", 0)) + int(
        transitions.get("unavailable_to_measured", 0)
    )
    return current, experimental


def _context_settings(
    router_report: dict[str, Any], experiment_reports: dict[str, dict[str, Any]]
) -> dict[str, dict[str, float | int]]:
    margin = experiment_reports["roi_margin_candidate"].get("settings", {})
    settings = {
        "baseline": {
            "roi_margin": float(router_report["settings"]["baseline_roi_margin"]),
            "min_roi_size_px": int(margin["min_roi_size_px"]),
        },
        "roi_margin_candidate": {
            "roi_margin": float(margin["experimental_roi_margin"]),
            "min_roi_size_px": int(margin["min_roi_size_px"]),
        },
    }
    if "small_roi_min8" in experiment_reports:
        small = experiment_reports["small_roi_min8"].get("settings", {})
        settings["small_roi_min8"] = {
            "roi_margin": float(small["roi_margin"]),
            "min_roi_size_px": int(small["experimental_min_roi_size_px"]),
        }
    expected = {
        "baseline": {"roi_margin": 0.15, "min_roi_size_px": 32},
        "roi_margin_candidate": {"roi_margin": 0.3, "min_roi_size_px": 32},
    }
    if "small_roi_min8" in experiment_reports:
        expected["small_roi_min8"] = {
            "roi_margin": 0.15,
            "min_roi_size_px": 8,
        }
    if settings != expected:
        raise ValueError("M68 crop contexts differ from the registered ablation scope")
    return settings


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


def validate_pose_profile_ablation_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("experiment_version") != EXPERIMENT_VERSION:
        raise ValueError("unsupported Pose profile ablation report")
    if report.get("status") not in {
        "experimental_multicandidate_regression_free_not_production",
        "experimental_multicandidate_rejected_due_to_regression",
    }:
        raise ValueError("unsafe Pose profile ablation status")
    inference = report.get("inference", {})
    target = int(inference.get("target_frame_count", -1))
    if target < 1 or int(inference.get("pose_output_produced", -1)) + int(
        inference.get("pose_output_missing", -1)
    ) != target:
        raise ValueError("Pose profile inference accounting is inconsistent")
    context = report.get("crop_context_replay", {})
    if sum(int(value) for value in context.get("source_frame_count_by_policy", {}).values()) != target:
        raise ValueError("crop context replay accounting is inconsistent")
    for field in (
        "classification_uses_feature_values",
        "classification_uses_event_outcomes",
        "classification_uses_grades_or_thresholds",
    ):
        if context.get(field) is not False:
            raise ValueError(f"unsafe crop context replay field: {field}")
    for name in ("profile_only", "multicandidate"):
        audit = report.get("router_audits", {}).get(name, {})
        if int(audit.get("selected_frame_count", -1)) + int(
            audit.get("rejected_frame_count", -1)
        ) != target:
            raise ValueError(f"{name} router accounting is inconsistent")
        for field in (
            "selection_uses_feature_values",
            "selection_uses_event_outcomes",
            "selection_uses_grades_or_thresholds",
            "required_joint_validity_regression_allowed",
        ):
            if audit.get(field) is not False:
                raise ValueError(f"unsafe {name} router field: {field}")
    if report["router_audits"]["multicandidate"].get(
        "equal_validity_different_pose_keeps_baseline"
    ) is not True:
        raise ValueError("multicandidate tie policy is unsafe")
    impacts = report.get("impacts", {})
    for name in ("profile_only", "uniform_context_combined", "multicandidate"):
        for kind in ("feature_vector", "operational_measurement"):
            impact = impacts.get(name, {}).get(kind, {})
            if int(impact.get("recovered_indicator_instance_count", -1)) < 0 or int(
                impact.get("regressed_indicator_instance_count", -1)
            ) < 0:
                raise ValueError("ablation impact accounting is missing")
    multi_regressions = sum(
        int(impacts["multicandidate"][kind]["regressed_indicator_instance_count"])
        for kind in ("feature_vector", "operational_measurement")
    )
    expected = (
        "experimental_multicandidate_regression_free_not_production"
        if multi_regressions == 0
        else "experimental_multicandidate_rejected_due_to_regression"
    )
    if report.get("status") != expected:
        raise ValueError("ablation status hides multicandidate regression")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get("candidate_promoted") is not False:
        raise ValueError("ablation changed production")
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
        "crop_context_or_pose_profile_accuracy_claimed",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"unsafe Pose profile ablation claim: {field}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--router-report", type=Path, required=True)
    parser.add_argument("--uniform-high-resolution-report", type=Path, required=True)
    parser.add_argument("--residual-audit", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; experiments are immutable")

    router = json.loads(args.router_report.read_text(encoding="utf-8"))
    uniform = json.loads(args.uniform_high_resolution_report.read_text(encoding="utf-8"))
    residual = json.loads(args.residual_audit.read_text(encoding="utf-8"))
    validate_pose_observability_residual_audit_sources(residual)
    video_id = str(router.get("video_id", ""))
    if uniform.get("video_id") != video_id:
        parser.error("M68 and M69 report video IDs differ")
    scope = next(
        (item for item in residual["candidate_frame_scope"] if item["video_id"] == video_id),
        None,
    )
    if scope is None:
        parser.error("residual audit does not contain this video")
    for binding, path, label in (
        (uniform["sources"]["router_report"], args.router_report, "M68 router"),
        (uniform["sources"]["residual_audit"], args.residual_audit, "residual audit"),
        (uniform["sources"]["registry"], args.registry, "registry"),
        (uniform["sources"]["video"], args.video, "video"),
    ):
        if Path(binding["path"]).resolve() != path.resolve() or str(binding["sha256"]).upper() != _sha256(path):
            parser.error(f"M69 {label} binding differs")

    current_path = _bound(router["artifacts"]["routed_frames"], "M68 routed frames")
    original_path = _bound(router["sources"]["baseline_frames"], "M68 baseline frames")
    timeline_path = _bound(router["sources"]["primary_timeline"], "primary timeline")
    events_path = _bound(router["sources"]["events"], "events")
    indicator_path = _bound(router["sources"]["indicator_features"], "indicator-features")
    registry_path = _bound(router["sources"]["registry"], "registry")
    if registry_path.resolve() != args.registry.resolve():
        parser.error("M68 registry path differs")
    uniform_candidate_path = _bound(uniform["artifacts"]["candidate_frames"], "M69 candidate frames")
    uniform_routed_path = _bound(uniform["artifacts"]["routed_frames"], "M69 routed frames")
    model_path = _bound(uniform["sources"]["candidate_model"], "384x288 model")
    config_path = _bound(uniform["sources"]["candidate_config"], "384x288 config")

    experiment_rows: dict[str, list[dict[str, Any]]] = {}
    experiment_reports: dict[str, dict[str, Any]] = {}
    context_sources: dict[str, Any] = {}
    for name, binding in router["sources"]["experiments"].items():
        frames_path = _bound(binding["frames"], f"{name} frames")
        report_path = _bound(binding["report"], f"{name} report")
        experiment_rows[name] = _read_jsonl(frames_path)
        experiment_reports[name] = json.loads(report_path.read_text(encoding="utf-8"))
        context_sources[name] = {"frames": _source(frames_path), "report": _source(report_path)}

    original_rows = _read_jsonl(original_path)
    current_rows = _read_jsonl(current_path)
    uniform_candidate_rows = _read_jsonl(uniform_candidate_path)
    uniform_routed_rows = _read_jsonl(uniform_routed_path)
    timeline_rows = _read_jsonl(timeline_path)
    indicator_rows = _read_jsonl(indicator_path)
    timeline = {int(row["processed_index"]): row for row in timeline_rows}
    targets = {int(value) for value in scope["processed_indexes"]}
    source_indexes = {int(value) for value in scope["source_frame_indexes"]}
    original_by_source = {
        int(row["frame"]["index"]): row
        for row in original_rows
        if int(row["frame"]["processed_index"]) in targets
    }
    if set(original_by_source) != source_indexes:
        parser.error("target source and processed frame scopes differ")

    crop_source, crop_replay = classify_routed_pose_sources(
        baseline_rows=original_rows,
        routed_rows=current_rows,
        primary_timeline=timeline_rows,
        experiment_rows_by_name=experiment_rows,
        target_processed_indexes=targets,
    )
    contexts = _context_settings(router, experiment_reports)
    backend = RtmposePoseBackend(
        model_path,
        config_path,
        device=args.device,
        runtime="pytorch",
        profile="analysis",
        native_keypoint_format="halpe26",
    )
    estimators = {
        name: PoseEstimator(
            backend,
            roi_margin=float(values["roi_margin"]),
            min_roi_size_px=int(values["min_roi_size_px"]),
        )
        for name, values in contexts.items()
    }

    inferred: dict[int, dict[str, Any] | None] = {}
    frame_results: list[dict[str, Any]] = []
    capture = cv2.VideoCapture(str(args.video))
    source_index = 0
    last_source = max(source_indexes)
    while source_index <= last_source:
        ok, image = capture.read()
        if not ok:
            capture.release()
            parser.error(f"video ended before target source frame {last_source}")
        original = original_by_source.get(source_index)
        if original is not None:
            index = int(original["frame"]["processed_index"])
            track_id = timeline[index].get("source_track_id")
            detections = [
                item
                for item in original.get("detections", [])
                if item.get("track_id") == track_id and item.get("class_name") == "player"
            ]
            pose = None
            reason = "candidate_pose_produced"
            context = crop_source[index]
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
                    "matched_crop_context": context,
                    "roi_margin": contexts[context]["roi_margin"],
                    "min_roi_size_px": contexts[context]["min_roi_size_px"],
                    "pose_output_produced": pose is not None,
                    "reason": reason,
                }
            )
        source_index += 1
    capture.release()

    context_candidate_rows = _replace_selected_poses(
        current_rows, timeline, inferred, targets
    )
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    required_joints = pose_required_joints_from_registry(registry)
    profile_selected, profile_audit = select_required_joint_superset_frames(
        baseline_rows=current_rows,
        candidate_rows=context_candidate_rows,
        primary_timeline=timeline_rows,
        target_processed_indexes=targets,
        required_joints=required_joints,
    )
    profile_routed_rows = (
        compose_disjoint_pose_policy_rows(
            baseline_rows=current_rows,
            timeline_rows=timeline_rows,
            experiments=[
                {
                    "name": "context_matched_384x288",
                    "frame_rows": context_candidate_rows,
                    "target_processed_indexes": profile_selected,
                }
            ],
        )[0]
        if profile_selected
        else current_rows
    )
    multicandidate_selected, multicandidate_audit = select_required_joint_dominating_candidates(
        baseline_rows=current_rows,
        candidate_rows_by_name={
            "context_matched_384x288": context_candidate_rows,
            "uniform_margin030_384x288": uniform_candidate_rows,
        },
        primary_timeline=timeline_rows,
        target_processed_indexes=targets,
        required_joints=required_joints,
    )
    multi_experiments = [
        {
            "name": name,
            "frame_rows": rows,
            "target_processed_indexes": {
                index for index, selected_name in multicandidate_selected.items()
                if selected_name == name
            },
        }
        for name, rows in (
            ("context_matched_384x288", context_candidate_rows),
            ("uniform_margin030_384x288", uniform_candidate_rows),
        )
        if any(selected_name == name for selected_name in multicandidate_selected.values())
    ]
    multicandidate_rows = (
        compose_disjoint_pose_policy_rows(
            baseline_rows=current_rows,
            timeline_rows=timeline_rows,
            experiments=multi_experiments,
        )[0]
        if multi_experiments
        else current_rows
    )

    args.output_directory.mkdir(parents=True, exist_ok=False)
    context_candidate_path = args.output_directory / "frames.context-matched-candidate.jsonl"
    profile_routed_path = args.output_directory / "frames.profile-only-routed.jsonl"
    multicandidate_path = args.output_directory / "frames.multicandidate-routed.jsonl"
    _write_jsonl(context_candidate_path, context_candidate_rows)
    _write_jsonl(profile_routed_path, profile_routed_rows)
    _write_jsonl(multicandidate_path, multicandidate_rows)

    current_profile = "m68-current-routed"
    profile_only = "halpe384-context-matched-router"
    uniform_profile = "halpe384-margin030-router"
    multicandidate_profile = "halpe384-multicandidate-router"
    current_input = {
        "frames_path": current_path,
        "primary_timeline_path": timeline_path,
        "model_sha256": str(router["settings"]["pose_model_sha256"]),
        "pose_backend": "rtmpose",
        "pose_profile": current_profile,
    }
    candidates = {
        "profile_only": (
            profile_only,
            profile_routed_path,
        ),
        "uniform_context_combined": (
            uniform_profile,
            uniform_routed_path,
        ),
        "multicandidate": (
            multicandidate_profile,
            multicandidate_path,
        ),
    }
    comparisons: dict[str, dict[str, Any]] = {}
    comparison_paths: dict[str, Path] = {}
    for name, (model, frames_path) in candidates.items():
        comparison = evaluate_fixed_boundary_pose_ab_files(
            events_path=events_path,
            registry_path=args.registry,
            model_inputs={
                current_profile: current_input,
                model: {
                    "frames_path": frames_path,
                    "primary_timeline_path": timeline_path,
                    "model_sha256": _sha256(model_path),
                    "pose_backend": "rtmpose",
                    "pose_profile": model,
                },
            },
            boundary_source_kind="candidate",
            boundary_source_label=(
                "candidate_boundaries_not_truth_m70_pose_profile_ablation"
            ),
            source_id=f"{video_id}:m70-{name}",
        )
        comparison_path = args.output_directory / f"fixed-boundary-{name}.json"
        comparison_path.write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        comparisons[name] = comparison
        comparison_paths[name] = comparison_path

    impacts: dict[str, dict[str, Any]] = {}
    for name, (model, _) in candidates.items():
        comparison = comparisons[name]
        impacts[name] = {
            "feature_vector": feature_vector_transitions(comparison, current_profile, model),
            "operational_measurement": operational_transitions_with_preserved_source_gate(
                comparison, current_profile, model, indicator_rows
            ),
        }
    multi_regressions = sum(
        int(impacts["multicandidate"][kind]["regressed_indicator_instance_count"])
        for kind in ("feature_vector", "operational_measurement")
    )
    report = {
        "schema_version": "1.0.0",
        "experiment_version": EXPERIMENT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "experimental_multicandidate_regression_free_not_production"
            if multi_regressions == 0
            else "experimental_multicandidate_rejected_due_to_regression"
        ),
        "video_id": video_id,
        "sources": {
            "video": _source(args.video),
            "m68_router_report": _source(args.router_report),
            "m69_uniform_high_resolution_report": _source(args.uniform_high_resolution_report),
            "residual_audit": _source(args.residual_audit),
            "registry": _source(args.registry),
            "current_frames": _source(current_path),
            "original_frames": _source(original_path),
            "primary_timeline": _source(timeline_path),
            "events": _source(events_path),
            "indicator_features": _source(indicator_path),
            "candidate_model": _source(model_path),
            "candidate_config": _source(config_path),
            "crop_context_sources": context_sources,
            "uniform_candidate_frames": _source(uniform_candidate_path),
            "uniform_routed_frames": _source(uniform_routed_path),
        },
        "artifacts": {
            "context_matched_candidate_frames": _source(context_candidate_path),
            "profile_only_routed_frames": _source(profile_routed_path),
            "multicandidate_routed_frames": _source(multicandidate_path),
            "fixed_boundary_comparisons": {
                name: _source(path) for name, path in comparison_paths.items()
            },
        },
        "settings": {
            "candidate_profile": uniform["settings"]["candidate_profile"],
            "candidate_input_size_hw": uniform["settings"]["candidate_input_size_hw"],
            "crop_contexts": contexts,
            "required_joints": list(required_joints),
        },
        "crop_context_replay": crop_replay,
        "inference": {
            "target_frame_count": len(targets),
            "pose_output_produced": sum(item["pose_output_produced"] for item in frame_results),
            "pose_output_missing": sum(not item["pose_output_produced"] for item in frame_results),
            "frame_results": sorted(frame_results, key=lambda item: item["processed_index"]),
        },
        "router_audits": {
            "profile_only": profile_audit,
            "multicandidate": multicandidate_audit,
        },
        "impacts": impacts,
        "counts": {
            name: {
                "current_feature_complete": _balanced(values["feature_vector"])[0],
                "experimental_feature_complete": _balanced(values["feature_vector"])[1],
                "current_operational_measured": _balanced(values["operational_measurement"])[0],
                "experimental_operational_measured": _balanced(values["operational_measurement"])[1],
            }
            for name, values in impacts.items()
        },
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "single_factor_pose_profile_ablation_completed": True,
            "multicandidate_current_video_regression_free": multi_regressions == 0,
            "next_required_evidence": (
                "manual corrected keypoints, per-view feature error, and a "
                "preregistered independent-video release validation"
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
            "crop_context_or_pose_profile_accuracy_claimed": False,
        },
    }
    validate_pose_profile_ablation_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "target_frames": len(targets),
                "profile_selected": len(profile_selected),
                "multicandidate_selected": len(multicandidate_selected),
                "profile_operational_recovered": impacts["profile_only"]["operational_measurement"]["recovered_indicator_instance_count"],
                "multicandidate_operational_recovered": impacts["multicandidate"]["operational_measurement"]["recovered_indicator_instance_count"],
                "multicandidate_operational_regressed": impacts["multicandidate"]["operational_measurement"]["regressed_indicator_instance_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
