#!/usr/bin/env python3
"""Aggregate M69 residual high-resolution Pose experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.pose_observability_residual import (
    validate_pose_observability_residual_audit_sources,
)
try:
    from scripts.run_residual_high_resolution_pose_experiment import (
        EXPERIMENT_VERSION,
        validate_residual_high_resolution_experiment_report,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from run_residual_high_resolution_pose_experiment import (
        EXPERIMENT_VERSION,
        validate_residual_high_resolution_experiment_report,
    )


REPORT_VERSION = "multivideo-residual-high-resolution-pose-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _verify_bindings(collection: dict[str, Any]) -> None:
    for label, binding in collection.items():
        path = Path(str(binding.get("path", "")))
        expected = str(binding.get("sha256", "")).upper()
        if not path.is_file() or not expected or _sha256(path) != expected:
            raise ValueError(f"experiment {label} source binding failed")


def build_summary(
    *, residual_audit_path: Path, experiment_report_paths: list[Path]
) -> dict[str, Any]:
    residual = json.loads(residual_audit_path.read_text(encoding="utf-8"))
    validate_pose_observability_residual_audit_sources(residual)
    if len(experiment_report_paths) != 3:
        raise ValueError("M69 summary requires exactly three experiments")
    reports: list[dict[str, Any]] = []
    report_sources: list[dict[str, str]] = []
    video_ids: set[str] = set()
    for path in sorted(experiment_report_paths, key=lambda item: str(item)):
        report = json.loads(path.read_text(encoding="utf-8"))
        validate_residual_high_resolution_experiment_report(report)
        _verify_bindings(report.get("sources", {}))
        _verify_bindings(report.get("artifacts", {}))
        if str(report["sources"]["residual_audit"]["sha256"]).upper() != _sha256(
            residual_audit_path
        ):
            raise ValueError("experiment does not bind the residual audit")
        video_id = str(report["video_id"])
        if video_id in video_ids:
            raise ValueError("M69 experiments contain duplicate videos")
        video_ids.add(video_id)
        reports.append(report)
        report_sources.append(_source(path))
    if video_ids != set(residual["scope"]["video_ids"]):
        raise ValueError("M69 experiments do not cover the residual video scope")

    def total(section: str, field: str) -> int:
        return sum(int(report[section][field]) for report in reports)

    indicator_instances = total("counts", "indicator_instances")
    current_feature = total("counts", "current_feature_complete")
    experimental_feature = total("counts", "experimental_feature_complete")
    current_operational = total("counts", "current_operational_measured")
    experimental_operational = total("counts", "experimental_operational_measured")
    vector_recovered = total("feature_vector_impact", "recovered_indicator_instance_count")
    vector_regressed = total("feature_vector_impact", "regressed_indicator_instance_count")
    operational_recovered = total(
        "operational_measurement_impact", "recovered_indicator_instance_count"
    )
    operational_regressed = total(
        "operational_measurement_impact", "regressed_indicator_instance_count"
    )
    residual_input = total("residual_impact", "input_residual_indicator_instance_count")
    residual_recovered = total(
        "residual_impact", "recovered_residual_indicator_instance_count"
    )
    residual_remaining = total(
        "residual_impact", "remaining_residual_indicator_instance_count"
    )
    if indicator_instances != int(residual["scope"]["indicator_instances"]):
        raise ValueError("M69 indicator scope differs from residual audit")
    if current_feature != int(residual["counts"]["feature_vector_complete"]):
        raise ValueError("M69 feature baseline differs from residual audit")
    if current_operational != int(residual["counts"]["operational_measured"]):
        raise ValueError("M69 operational baseline differs from residual audit")
    if residual_input != int(residual["counts"]["non_hard_fail_feature_incomplete"]):
        raise ValueError("M69 residual input differs from residual audit")
    if current_feature + vector_recovered - vector_regressed != experimental_feature:
        raise ValueError("M69 feature projection does not balance")
    if (
        current_operational + operational_recovered - operational_regressed
        != experimental_operational
    ):
        raise ValueError("M69 operational projection does not balance")
    if residual_recovered + residual_remaining != residual_input:
        raise ValueError("M69 residual recovery does not balance")
    regressions = vector_regressed + operational_regressed

    model_hashes = {
        str(report["sources"]["candidate_model"]["sha256"]).upper()
        for report in reports
    }
    profiles = {str(report["settings"]["candidate_profile"]) for report in reports}
    required_joint_sets = {
        tuple(report["settings"]["required_joints"]) for report in reports
    }
    if len(model_hashes) != 1 or len(profiles) != 1 or len(required_joint_sets) != 1:
        raise ValueError("M69 experiments mix candidate contracts")
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "regression_free_high_resolution_observability_candidate_requires_truth"
            if regressions == 0
            else "high_resolution_candidate_rejected_due_to_regression"
        ),
        "scope": {
            "video_count": len(video_ids),
            "video_ids": sorted(video_ids),
            "indicator_count": int(residual["scope"]["indicator_count"]),
            "indicator_instances": indicator_instances,
            "input_residual_indicator_instances": residual_input,
            "input_residual_events": int(residual["counts"]["residual_event_count"]),
        },
        "sources": {
            "residual_audit": _source(residual_audit_path),
            "experiment_reports": report_sources,
        },
        "candidate": {
            "experiment_version": EXPERIMENT_VERSION,
            "pose_profile": next(iter(profiles)),
            "pose_model_sha256": next(iter(model_hashes)),
            "input_size_hw": [384, 288],
            "roi_margin": 0.30,
            "min_roi_size_px": 8,
            "required_joints": list(next(iter(required_joint_sets))),
            "changes_pose_model_and_crop_context_together": True,
            "causal_attribution_to_one_setting_claimed": False,
        },
        "inference": {
            "target_frame_count": total("inference", "target_frame_count"),
            "pose_output_produced": total("inference", "pose_output_produced"),
            "pose_output_missing": total("inference", "pose_output_missing"),
            "selected_frame_count": sum(
                int(report["router_audit"]["selected_frame_count"])
                for report in reports
            ),
            "rejected_frame_count": sum(
                int(report["router_audit"]["rejected_frame_count"])
                for report in reports
            ),
        },
        "baseline": {
            "feature_vector_complete": current_feature,
            "feature_vector_incomplete": indicator_instances - current_feature,
            "operational_measured": current_operational,
            "operational_unavailable": indicator_instances - current_operational,
            "measurement_hard_fail": int(residual["counts"]["measurement_hard_fail"]),
            "non_hard_fail_feature_incomplete": residual_input,
        },
        "experimental_projection": {
            "feature_vector_recovered": vector_recovered,
            "feature_vector_regressed": vector_regressed,
            "feature_vector_complete": experimental_feature,
            "feature_vector_incomplete": indicator_instances - experimental_feature,
            "operational_recovered": operational_recovered,
            "operational_regressed": operational_regressed,
            "operational_measured": experimental_operational,
            "operational_unavailable": indicator_instances - experimental_operational,
            "measurement_hard_fail": int(residual["counts"]["measurement_hard_fail"]),
            "non_hard_fail_feature_incomplete": residual_remaining,
            "recovered_residual_indicator_instances": residual_recovered,
            "remaining_residual_indicator_instances": residual_remaining,
        },
        "by_video": [
            {
                "video_id": report["video_id"],
                "target_frames": report["inference"]["target_frame_count"],
                "pose_output_produced": report["inference"]["pose_output_produced"],
                "selected_frames": report["router_audit"]["selected_frame_count"],
                "feature_recovered": report["feature_vector_impact"][
                    "recovered_indicator_instance_count"
                ],
                "feature_regressed": report["feature_vector_impact"][
                    "regressed_indicator_instance_count"
                ],
                "operational_recovered": report["operational_measurement_impact"][
                    "recovered_indicator_instance_count"
                ],
                "operational_regressed": report[
                    "operational_measurement_impact"
                ]["regressed_indicator_instance_count"],
                "residual_remaining": report["residual_impact"][
                    "remaining_residual_indicator_instance_count"
                ],
            }
            for report in sorted(reports, key=lambda item: item["video_id"])
        ],
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "reason_codes": [
                "manual_keypoint_truth_missing",
                "candidate_changes_model_and_crop_context_together",
                "per_view_feature_error_not_evaluated",
                "independent_video_release_test_missing",
            ],
            "next_required_evidence": (
                "manual corrected keypoints on selected and rejected frames, "
                "per-view feature error, isolated ablation, and a preregistered "
                "independent-video release test"
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
            "current_three_video_regression_free_is_not_independent_validation": True,
        },
    }
    validate_summary(report)
    return report


def validate_summary(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported M69 summary")
    if report.get("status") not in {
        "regression_free_high_resolution_observability_candidate_requires_truth",
        "high_resolution_candidate_rejected_due_to_regression",
    }:
        raise ValueError("unsafe M69 summary status")
    total = int(report.get("scope", {}).get("indicator_instances", -1))
    for section in ("baseline", "experimental_projection"):
        state = report.get(section, {})
        if int(state.get("feature_vector_complete", -1)) + int(
            state.get("feature_vector_incomplete", -1)
        ) != total:
            raise ValueError("M69 feature counts do not balance")
        if int(state.get("operational_measured", -1)) + int(
            state.get("operational_unavailable", -1)
        ) != total:
            raise ValueError("M69 operational counts do not balance")
    candidate = report.get("candidate", {})
    if candidate.get("changes_pose_model_and_crop_context_together") is not True or candidate.get(
        "causal_attribution_to_one_setting_claimed"
    ) is not False:
        raise ValueError("M69 combined candidate semantics are missing")
    projection = report.get("experimental_projection", {})
    regressions = int(projection.get("feature_vector_regressed", -1)) + int(
        projection.get("operational_regressed", -1)
    )
    if report.get("status", "").startswith("regression_free") and regressions != 0:
        raise ValueError("M69 hides a current-set regression")
    if report.get("decision", {}).get("production_default_changed") is not False or report.get(
        "decision", {}
    ).get("candidate_promoted") is not False:
        raise ValueError("M69 changed production")
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
            raise ValueError(f"unsafe M69 claim: {field}")
    if report.get("safety", {}).get(
        "current_three_video_regression_free_is_not_independent_validation"
    ) is not True:
        raise ValueError("M69 omits independent-test disclaimer")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--residual-audit", type=Path, required=True)
    parser.add_argument("--experiment-report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; summaries are immutable")
    report = build_summary(
        residual_audit_path=args.residual_audit,
        experiment_report_paths=args.experiment_report,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "projection": report["experimental_projection"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
