#!/usr/bin/env python3
"""Route a larger-ROI Pose only on strict required-joint validity dominance."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.pose_observability_router import (
    EXPERIMENT_VERSION,
    pose_required_joints_from_registry,
    select_required_joint_superset_frames,
    validate_pose_observability_router_report,
)
from rallymate_evaluation.pose_policy_composition import compose_disjoint_pose_policy_rows
from rallymate_evaluation.small_roi_recovery import (
    feature_vector_transitions,
    operational_measurement_transitions,
)


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


def _balanced_counts(impact: dict[str, Any]) -> tuple[int, int]:
    transitions = impact["status_transitions"]
    baseline_measured = int(transitions.get("measured_to_measured", 0)) + int(
        transitions.get("measured_to_unavailable", 0)
    )
    experimental_measured = int(transitions.get("measured_to_measured", 0)) + int(
        transitions.get("unavailable_to_measured", 0)
    )
    return baseline_measured, experimental_measured


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-frames", type=Path, required=True)
    parser.add_argument("--candidate-margin-frames", type=Path, required=True)
    parser.add_argument("--candidate-margin-report", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--indicator-features", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--small-roi-frames", type=Path)
    parser.add_argument("--small-roi-report", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; experiments are immutable")
    if (args.small_roi_frames is None) != (args.small_roi_report is None):
        parser.error("small ROI frames and report must be supplied together")

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    margin_report = json.loads(
        args.candidate_margin_report.read_text(encoding="utf-8")
    )
    if margin_report.get("experiment_version") != "roi-margin-pose-recovery-v1.0.0":
        parser.error("candidate margin report is invalid")
    sources = margin_report.get("source", {})
    artifacts = margin_report.get("artifacts", {})
    if sources.get("frames", {}).get("sha256") != _sha256(
        args.baseline_frames
    ):
        parser.error("candidate report does not bind baseline frames")
    if artifacts.get("experimental_frames", {}).get("sha256") != _sha256(
        args.candidate_margin_frames
    ):
        parser.error("candidate report does not bind margin frames")

    baseline_rows = _read_jsonl(args.baseline_frames)
    margin_rows = _read_jsonl(args.candidate_margin_frames)
    timeline_rows = _read_jsonl(args.primary_timeline)
    indicator_rows = _read_jsonl(args.indicator_features)
    margin_targets = {
        int(item["processed_index"])
        for item in margin_report.get("inference", {}).get("frame_results", [])
    }
    required_joints = pose_required_joints_from_registry(registry)
    selected_margin, router_audit = select_required_joint_superset_frames(
        baseline_rows=baseline_rows,
        candidate_rows=margin_rows,
        primary_timeline=timeline_rows,
        target_processed_indexes=margin_targets,
        required_joints=required_joints,
    )

    experiments: list[dict[str, Any]] = []
    source_experiments: dict[str, Any] = {
        "roi_margin_candidate": {
            "frames": _source(args.candidate_margin_frames),
            "report": _source(args.candidate_margin_report),
        }
    }
    if args.small_roi_frames is not None and args.small_roi_report is not None:
        small_report = json.loads(args.small_roi_report.read_text(encoding="utf-8"))
        if small_report.get("experiment_version") != "small-roi-pose-recovery-v1.1.0":
            parser.error("small ROI report is invalid")
        if small_report.get("artifacts", {}).get("experimental_frames", {}).get(
            "sha256"
        ) != _sha256(args.small_roi_frames):
            parser.error("small ROI report does not bind its frames")
        small_targets = {
            int(index)
            for index in small_report.get("inference", {}).get(
                "recovered_processed_indices", []
            )
        }
        experiments.append(
            {
                "name": "small_roi_min8",
                "frame_rows": _read_jsonl(args.small_roi_frames),
                "target_processed_indexes": small_targets,
            }
        )
        source_experiments["small_roi_min8"] = {
            "frames": _source(args.small_roi_frames),
            "report": _source(args.small_roi_report),
        }
    if selected_margin:
        experiments.append(
            {
                "name": "required_joint_superset_margin_0.30",
                "frame_rows": margin_rows,
                "target_processed_indexes": selected_margin,
            }
        )
    if not experiments:
        parser.error("router selected no experiment frames")
    combined_rows, composition_audit = compose_disjoint_pose_policy_rows(
        baseline_rows=baseline_rows,
        timeline_rows=timeline_rows,
        experiments=experiments,
    )

    args.output_directory.mkdir(parents=True, exist_ok=False)
    routed_frames_path = args.output_directory / "frames.routed-experimental.jsonl"
    with routed_frames_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in combined_rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    current_profile = str(summary["model_versions"]["pose_profile"])
    model_sha = str(summary["model_versions"]["pose_model_sha256"])
    routed_profile = f"{current_profile}-required-joint-superset-router-experimental"
    comparison = evaluate_fixed_boundary_pose_ab_files(
        events_path=args.events,
        registry_path=args.registry,
        model_inputs={
            current_profile: {
                "frames_path": args.baseline_frames,
                "primary_timeline_path": args.primary_timeline,
                "model_sha256": model_sha,
                "pose_backend": "rtmpose",
                "pose_profile": current_profile,
            },
            routed_profile: {
                "frames_path": routed_frames_path,
                "primary_timeline_path": args.primary_timeline,
                "model_sha256": model_sha,
                "pose_backend": "rtmpose",
                "pose_profile": routed_profile,
            },
        },
        boundary_source_kind="candidate",
        boundary_source_label="candidate_boundaries_not_truth_pose_observability_router",
        source_id=f"{summary['video_id']}:pose-observability-router-experiment",
    )
    comparison_path = args.output_directory / "fixed-boundary-comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    vector_impact = feature_vector_transitions(
        comparison, current_profile, routed_profile
    )
    operational_impact = operational_measurement_transitions(
        comparison, current_profile, routed_profile, indicator_rows
    )
    vector_baseline, vector_routed = _balanced_counts(vector_impact)
    operational_baseline, operational_routed = _balanced_counts(operational_impact)
    regressions = int(vector_impact["regressed_indicator_instance_count"]) + int(
        operational_impact["regressed_indicator_instance_count"]
    )
    report = {
        "schema_version": "1.0.0",
        "experiment_version": EXPERIMENT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "experimental_regression_free_observability_candidate_not_production"
            if regressions == 0
            else "experimental_router_rejected_due_to_regression"
        ),
        "video_id": summary["video_id"],
        "sources": {
            "baseline_frames": _source(args.baseline_frames),
            "candidate_margin_frames": _source(args.candidate_margin_frames),
            "candidate_margin_report": _source(args.candidate_margin_report),
            "primary_timeline": _source(args.primary_timeline),
            "events": _source(args.events),
            "indicator_features": _source(args.indicator_features),
            "summary": _source(args.summary),
            "registry": _source(args.registry),
            "experiments": source_experiments,
        },
        "artifacts": {
            "routed_frames": _source(routed_frames_path),
            "fixed_boundary_comparison": _source(comparison_path),
        },
        "settings": {
            "baseline_pose_profile": current_profile,
            "candidate_pose_profile": current_profile,
            "pose_model_sha256": model_sha,
            "baseline_roi_margin": margin_report["settings"]["baseline_roi_margin"],
            "candidate_roi_margin": margin_report["settings"][
                "experimental_roi_margin"
            ],
            "keypoint_confidence_min": router_audit["confidence_min"],
        },
        "router_audit": router_audit,
        "composition_audit": composition_audit,
        "feature_vector_impact": vector_impact,
        "operational_measurement_impact": operational_impact,
        "counts": {
            "indicator_instances": len(indicator_rows),
            "baseline_feature_complete": vector_baseline,
            "routed_feature_complete": vector_routed,
            "baseline_operational_measured": operational_baseline,
            "routed_operational_measured": operational_routed,
        },
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "current_video_regression_free": regressions == 0,
            "next_required_evidence": (
                "manual corrected keypoints, per-view feature error, and a "
                "preregistered independent-video release test"
            ),
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "production_enabled": False,
            "automatic_fallback_enabled": False,
            "measurement_gate_modified": False,
            "scoring_gate_modified": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
        },
    }
    validate_pose_observability_router_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "selected_margin_frames": len(selected_margin),
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
