#!/usr/bin/env python3
"""Aggregate the three-video M71 RTMPose-L extension experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.run_m71_larger_pose_model_extension import (
        validate_larger_pose_model_extension_report,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from run_m71_larger_pose_model_extension import (
        validate_larger_pose_model_extension_report,
    )


REPORT_VERSION = "multivideo-larger-pose-model-extension-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _verify_binding(binding: dict[str, Any], label: str) -> Path:
    path = Path(str(binding.get("path", "")))
    expected = str(binding.get("sha256", "")).upper()
    if not path.is_file() or _sha256(path) != expected:
        raise ValueError(f"{label} binding failed")
    return path


def _sum(reports: list[dict[str, Any]], *keys: str) -> int:
    total = 0
    for report in reports:
        value: Any = report
        for key in keys:
            value = value[key]
        total += int(value)
    return total


def build_summary(
    *,
    m70_summary_path: Path,
    report_paths: list[Path],
) -> dict[str, Any]:
    m70 = json.loads(m70_summary_path.read_text(encoding="utf-8"))
    if m70.get("report_version") != "multivideo-pose-profile-context-ablation-v1.0.0":
        raise ValueError("M71 requires the versioned M70 multivideo summary")
    if len(report_paths) != 3:
        raise ValueError("M71 requires exactly three per-video reports")

    by_id: dict[str, dict[str, Any]] = {}
    candidate_ids: set[str] = set()
    model_hashes: set[str] = set()
    registry_versions: set[str] = set()
    for path in report_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        validate_larger_pose_model_extension_report(report)
        for name, binding in report.get("sources", {}).items():
            _verify_binding(binding, f"{path.name}.sources.{name}")
        for name, binding in report.get("artifacts", {}).items():
            _verify_binding(binding, f"{path.name}.artifacts.{name}")
        video_id = str(report["video_id"])
        if video_id in by_id:
            raise ValueError("duplicate M71 video report")
        anchored_path = _verify_binding(
            report["sources"]["m70_anchored_report"], "M70 anchored report"
        )
        anchored = json.loads(anchored_path.read_text(encoding="utf-8"))
        if anchored.get("video_id") != video_id:
            raise ValueError("M71 report binds an M70 report for another video")
        by_id[video_id] = report
        candidate_ids.add(str(report["candidate"]["candidate_id"]))
        model_hashes.add(str(report["sources"]["candidate_model"]["sha256"]).upper())
        registry_versions.add(str(report["candidate"]["registry_version"]))

    expected_videos = set(m70["scope"]["video_ids"])
    if set(by_id) != expected_videos:
        raise ValueError("M71 reports do not cover the exact M70 video scope")
    if len(candidate_ids) != 1 or len(model_hashes) != 1 or len(registry_versions) != 1:
        raise ValueError("M71 reports do not use one versioned model candidate")
    reports = [by_id[video_id] for video_id in sorted(by_id)]

    scope_total = int(m70["scope"]["indicator_instances"])
    m68 = m70["baseline"]
    m70_projection = m70["strategies"]["m69_anchored_extension"]
    feature_recovered = _sum(
        reports,
        "comparison_to_m68",
        "feature_vector",
        "recovered_indicator_instance_count",
    )
    feature_regressed = _sum(
        reports,
        "comparison_to_m68",
        "feature_vector",
        "regressed_indicator_instance_count",
    )
    operational_recovered = _sum(
        reports,
        "comparison_to_m68",
        "operational_measurement",
        "recovered_indicator_instance_count",
    )
    operational_regressed = _sum(
        reports,
        "comparison_to_m68",
        "operational_measurement",
        "regressed_indicator_instance_count",
    )
    additional_feature = _sum(
        reports, "m70_preservation", "additional_feature_recovery_count"
    )
    additional_operational = _sum(
        reports, "m70_preservation", "additional_operational_recovery_count"
    )
    lost_feature = _sum(
        reports, "m70_preservation", "lost_m70_feature_recovery_count"
    )
    lost_operational = _sum(
        reports, "m70_preservation", "lost_m70_operational_recovery_count"
    )

    projection = {
        "selected_frame_count": _sum(reports, "router_audit", "selected_frame_count"),
        "feature_recovered_from_m68": feature_recovered,
        "feature_regressed_from_m68": feature_regressed,
        "feature_vector_complete": int(m68["feature_vector_complete"])
        + feature_recovered
        - feature_regressed,
        "feature_vector_incomplete": int(m68["feature_vector_incomplete"])
        - feature_recovered
        + feature_regressed,
        "operational_recovered_from_m68": operational_recovered,
        "operational_regressed_from_m68": operational_regressed,
        "operational_measured": int(m68["operational_measured"])
        + operational_recovered
        - operational_regressed,
        "operational_unavailable": int(m68["operational_unavailable"])
        - operational_recovered
        + operational_regressed,
        "measurement_hard_fail": int(m68["measurement_hard_fail"]),
        "non_hard_fail_operational_residual": int(
            m68["non_hard_fail_feature_incomplete"]
        )
        - operational_recovered
        + operational_regressed,
        "additional_feature_recovery_over_m70": additional_feature,
        "additional_operational_recovery_over_m70": additional_operational,
        "lost_m70_feature_recovery_count": lost_feature,
        "lost_m70_operational_recovery_count": lost_operational,
    }

    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "experimental_larger_pose_model_extension_regression_free_requires_truth",
        "scope": {
            "video_count": 3,
            "video_ids": sorted(expected_videos),
            "indicator_count": int(m70["scope"]["indicator_count"]),
            "indicator_instances": scope_total,
            "target_frame_count": _sum(reports, "inference", "target_frame_count"),
        },
        "sources": {
            "m70_summary": _source(m70_summary_path),
            "per_video_reports": [_source(path) for path in sorted(report_paths)],
        },
        "candidate": {
            "candidate_id": next(iter(candidate_ids)),
            "registry_version": next(iter(registry_versions)),
            "model_sha256": next(iter(model_hashes)),
            "native_keypoint_format": "halpe26",
            "input_size_hw": [384, 288],
            "selection_policy": "required_joint_validity_strict_superset_on_m70_residual_frames",
        },
        "inference": {
            "pose_output_produced": _sum(reports, "inference", "pose_output_produced"),
            "pose_output_missing": _sum(reports, "inference", "pose_output_missing"),
        },
        "baseline_m68": m68,
        "baseline_m70": m70_projection,
        "m71_projection": projection,
        "by_video": [
            {
                "video_id": video_id,
                "target_frames": int(by_id[video_id]["inference"]["target_frame_count"]),
                "pose_output_produced": int(
                    by_id[video_id]["inference"]["pose_output_produced"]
                ),
                "selected_frames": int(
                    by_id[video_id]["router_audit"]["selected_frame_count"]
                ),
                "feature_recovered_from_m68": int(
                    by_id[video_id]["comparison_to_m68"]["feature_vector"][
                        "recovered_indicator_instance_count"
                    ]
                ),
                "operational_recovered_from_m68": int(
                    by_id[video_id]["comparison_to_m68"]["operational_measurement"][
                        "recovered_indicator_instance_count"
                    ]
                ),
                "additional_feature_recovery_over_m70": int(
                    by_id[video_id]["m70_preservation"][
                        "additional_feature_recovery_count"
                    ]
                ),
                "additional_operational_recovery_over_m70": int(
                    by_id[video_id]["m70_preservation"][
                        "additional_operational_recovery_count"
                    ]
                ),
            }
            for video_id in sorted(by_id)
        ],
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "m70_recovered_sets_preserved": lost_feature == 0 and lost_operational == 0,
            "current_three_video_extension_regression_free": (
                feature_regressed == 0
                and operational_regressed == 0
                and lost_feature == 0
                and lost_operational == 0
            ),
            "reason_codes": [
                "manual_keypoint_truth_missing",
                "per_view_feature_error_not_evaluated",
                "independent_video_release_test_missing",
                "candidate_is_selective_extension_not_global_model_replacement",
            ],
            "next_required_evidence": (
                "manual corrected keypoints, per-view feature error, and a preregistered "
                "independent-video release validation"
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
        raise ValueError("unsupported M71 multivideo report")
    if report.get("status") != "experimental_larger_pose_model_extension_regression_free_requires_truth":
        raise ValueError("unsafe M71 report status")
    scope = report.get("scope", {})
    total = int(scope.get("indicator_instances", -1))
    if int(scope.get("video_count", -1)) != 3 or len(scope.get("video_ids", [])) != 3:
        raise ValueError("M71 must retain the exact three-video scope")
    inference = report.get("inference", {})
    if int(inference.get("pose_output_produced", -1)) + int(
        inference.get("pose_output_missing", -1)
    ) != int(scope.get("target_frame_count", -2)):
        raise ValueError("M71 inference accounting is inconsistent")
    projection = report.get("m71_projection", {})
    if int(projection.get("feature_vector_complete", -1)) + int(
        projection.get("feature_vector_incomplete", -1)
    ) != total:
        raise ValueError("M71 feature projection is inconsistent")
    if int(projection.get("operational_measured", -1)) + int(
        projection.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("M71 operational projection is inconsistent")
    if any(
        int(projection.get(field, -1)) != 0
        for field in (
            "feature_regressed_from_m68",
            "operational_regressed_from_m68",
            "lost_m70_feature_recovery_count",
            "lost_m70_operational_recovery_count",
        )
    ):
        raise ValueError("M71 report is not regression-free")
    decision = report.get("decision", {})
    if (
        decision.get("production_default_changed") is not False
        or decision.get("candidate_promoted") is not False
        or decision.get("m70_recovered_sets_preserved") is not True
        or decision.get("current_three_video_extension_regression_free") is not True
    ):
        raise ValueError("M71 decision is unsafe")
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
            raise ValueError(f"unsafe M71 claim: {field}")
    if (
        report.get("safety", {}).get(
            "current_three_video_regression_free_is_not_independent_validation"
        )
        is not True
    ):
        raise ValueError("M71 independent-test limitation is missing")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m70-summary", type=Path, required=True)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; reports are immutable")
    report = build_summary(
        m70_summary_path=args.m70_summary,
        report_paths=args.report,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"report": str(args.output), "m71_projection": report["m71_projection"]}))


if __name__ == "__main__":
    main()
