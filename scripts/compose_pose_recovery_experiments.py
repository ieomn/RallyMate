#!/usr/bin/env python3
"""Compose disjoint small-ROI and ROI-margin experiments, then recompute once."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.pose_policy_composition import (
    compose_disjoint_pose_policy_rows,
)
from rallymate_evaluation.small_roi_recovery import (
    feature_vector_transitions,
    operational_measurement_transitions,
)


REPORT_VERSION = "pose-recovery-policy-composition-v1.0.0"


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


def _validate_report(report: dict[str, Any]) -> None:
    if report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported composition report version")
    if report.get("status") != "experimental_observability_only_not_production":
        raise ValueError("unsafe composition report status")
    audit = report.get("composition_audit", {})
    for field in (
        "target_sets_disjoint",
        "non_pose_frame_data_preserved",
        "non_selected_poses_preserved",
    ):
        if audit.get(field) is not True:
            raise ValueError(f"unsafe composition audit: {field}")
    safety = report.get("safety", {})
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_fallback_enabled",
        "measurement_gate_modified",
        "scoring_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"unsafe composition claim: {field}")
    if safety.get("independent_recovery_counts_were_not_added_arithmetically") is not True:
        raise ValueError("composition report uses arithmetic projection")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-frames", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--indicator-features", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--margin-frames", type=Path, required=True)
    parser.add_argument("--margin-report", type=Path, required=True)
    parser.add_argument("--small-roi-frames", type=Path)
    parser.add_argument("--small-roi-report", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; experiments are immutable")
    if (args.small_roi_frames is None) != (args.small_roi_report is None):
        parser.error("small ROI frames and report must be supplied together")

    baseline_rows = _read_jsonl(args.baseline_frames)
    timeline_rows = _read_jsonl(args.primary_timeline)
    indicator_rows = _read_jsonl(args.indicator_features)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    margin_report = json.loads(args.margin_report.read_text(encoding="utf-8"))
    if margin_report.get("experiment_version") != "roi-margin-pose-recovery-v1.0.0":
        parser.error("margin report version is invalid")
    if (
        margin_report.get("artifacts", {}).get("experimental_frames", {}).get("sha256")
        != _sha256(args.margin_frames)
    ):
        parser.error("margin frames hash does not match its report")
    margin_targets = {
        int(item["processed_index"])
        for item in margin_report.get("inference", {}).get("frame_results", [])
    }
    experiments = [
        {
            "name": "roi_margin_0.30",
            "frame_rows": _read_jsonl(args.margin_frames),
            "target_processed_indexes": margin_targets,
        }
    ]
    source_experiments = {
        "roi_margin_0.30": {
            "frames": _source(args.margin_frames),
            "report": _source(args.margin_report),
        }
    }
    if args.small_roi_frames is not None and args.small_roi_report is not None:
        small_report = json.loads(args.small_roi_report.read_text(encoding="utf-8"))
        if small_report.get("experiment_version") != "small-roi-pose-recovery-v1.1.0":
            parser.error("small ROI report version is invalid")
        if (
            small_report.get("artifacts", {}).get("experimental_frames", {}).get("sha256")
            != _sha256(args.small_roi_frames)
        ):
            parser.error("small ROI frames hash does not match its report")
        small_targets = {
            int(item)
            for item in small_report.get("inference", {}).get(
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

    combined_rows, composition_audit = compose_disjoint_pose_policy_rows(
        baseline_rows=baseline_rows,
        timeline_rows=timeline_rows,
        experiments=experiments,
    )
    args.output_directory.mkdir(parents=True, exist_ok=False)
    combined_path = args.output_directory / "frames.combined-experimental.jsonl"
    with combined_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in combined_rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    current_profile = str(summary["model_versions"]["pose_profile"])
    model_sha = str(summary["model_versions"]["pose_model_sha256"])
    experiment_name = f"{current_profile}-combined-small-roi-and-margin-experimental"
    comparison_path = args.output_directory / "fixed-boundary-comparison.json"
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
            experiment_name: {
                "frames_path": combined_path,
                "primary_timeline_path": args.primary_timeline,
                "model_sha256": model_sha,
                "pose_backend": "rtmpose",
                "pose_profile": experiment_name,
            },
        },
        boundary_source_kind="candidate",
        boundary_source_label=(
            "candidate_boundaries_not_truth_composed_pose_observability_experiment"
        ),
        source_id=f"{summary['video_id']}:composed-pose-policy-experiment",
    )
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    vector_impact = feature_vector_transitions(
        comparison, current_profile, experiment_name
    )
    operational_impact = operational_measurement_transitions(
        comparison, current_profile, experiment_name, indicator_rows
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "experimental_observability_only_not_production",
        "video_id": summary["video_id"],
        "sources": {
            "baseline_frames": _source(args.baseline_frames),
            "primary_timeline": _source(args.primary_timeline),
            "events": _source(args.events),
            "indicator_features": _source(args.indicator_features),
            "summary": _source(args.summary),
            "registry": _source(args.registry),
            "experiments": source_experiments,
        },
        "artifacts": {
            "combined_frames": _source(combined_path),
            "fixed_boundary_comparison": _source(comparison_path),
        },
        "composition_audit": composition_audit,
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
            "automatic_fallback_enabled": False,
            "measurement_gate_modified": False,
            "scoring_gate_modified": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "independent_recovery_counts_were_not_added_arithmetically": True,
        },
    }
    _validate_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "composition_audit": composition_audit,
                "feature_vector_impact": vector_impact,
                "operational_measurement_impact": operational_impact,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
