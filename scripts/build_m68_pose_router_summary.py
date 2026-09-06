#!/usr/bin/env python3
"""Aggregate three required-joint-superset router experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.pose_observability_router import (
    ROUTER_VERSION,
    validate_pose_observability_router_report,
)


REPORT_VERSION = "multivideo-required-joint-superset-router-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _sum(report_rows: list[dict[str, Any]], section: str, field: str) -> int:
    return sum(int(report[section][field]) for report in report_rows)


def build_summary(
    *,
    router_reports: list[dict[str, Any]],
    m67_summary: dict[str, Any],
    sources: dict[str, Any],
) -> dict[str, Any]:
    if len(router_reports) != 3:
        raise ValueError("M68 requires exactly three router reports")
    for report in router_reports:
        validate_pose_observability_router_report(report)
    video_ids = sorted(str(report["video_id"]) for report in router_reports)
    if len(set(video_ids)) != 3:
        raise ValueError("M68 router reports must cover three unique videos")
    joint_sets = {
        tuple(report["router_audit"]["required_joints"])
        for report in router_reports
    }
    if len(joint_sets) != 1:
        raise ValueError("router reports do not share one required-joint scope")
    registry_hashes = {
        str(report["sources"]["registry"]["sha256"]).upper()
        for report in router_reports
    }
    model_hashes = {
        str(report["settings"]["pose_model_sha256"]).upper()
        for report in router_reports
    }
    if len(registry_hashes) != 1 or len(model_hashes) != 1:
        raise ValueError("router reports mix registry or Pose model versions")
    baseline_instances = _sum(router_reports, "counts", "indicator_instances")
    baseline_feature = _sum(
        router_reports, "counts", "baseline_feature_complete"
    )
    baseline_operational = _sum(
        router_reports, "counts", "baseline_operational_measured"
    )
    routed_feature = _sum(router_reports, "counts", "routed_feature_complete")
    routed_operational = _sum(
        router_reports, "counts", "routed_operational_measured"
    )
    feature_recovered = _sum(
        router_reports, "feature_vector_impact", "recovered_indicator_instance_count"
    )
    feature_regressed = _sum(
        router_reports, "feature_vector_impact", "regressed_indicator_instance_count"
    )
    operational_recovered = _sum(
        router_reports,
        "operational_measurement_impact",
        "recovered_indicator_instance_count",
    )
    operational_regressed = _sum(
        router_reports,
        "operational_measurement_impact",
        "regressed_indicator_instance_count",
    )
    if routed_feature != baseline_feature + feature_recovered - feature_regressed:
        raise ValueError("M68 feature projection is not balanced")
    if (
        routed_operational
        != baseline_operational + operational_recovered - operational_regressed
    ):
        raise ValueError("M68 operational projection is not balanced")
    m67_baseline = m67_summary.get("baseline", {})
    if (
        int(m67_baseline.get("indicator_instances", -1)) != baseline_instances
        or int(m67_baseline.get("feature_vector_complete", -1)) != baseline_feature
        or int(m67_baseline.get("operational_measured", -1))
        != baseline_operational
    ):
        raise ValueError("M68 baseline differs from the bound M67 source")
    hard_fail = int(m67_baseline.get("measurement_hard_fail", -1))
    routed_unavailable = baseline_instances - routed_operational
    if hard_fail < 0 or hard_fail > routed_unavailable:
        raise ValueError("M68 hard-fail accounting is invalid")

    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "regression_free_observability_router_candidate_requires_truth",
        "scope": {
            "video_count": 3,
            "video_ids": video_ids,
            "indicator_count": 13,
            "indicator_instances": baseline_instances,
            "registry_sha256": next(iter(registry_hashes)),
            "pose_model_sha256": next(iter(model_hashes)),
            "required_joints": list(next(iter(joint_sets))),
        },
        "sources": sources,
        "router": {
            "router_version": ROUTER_VERSION,
            "candidate_margin_target_frames": _sum(
                router_reports, "router_audit", "target_frame_count"
            ),
            "selected_margin_frames": _sum(
                router_reports, "router_audit", "selected_frame_count"
            ),
            "rejected_margin_frames": _sum(
                router_reports, "router_audit", "rejected_frame_count"
            ),
            "total_composed_experimental_frames": sum(
                int(report["composition_audit"]["target_frame_count"])
                for report in router_reports
            ),
            "selection_policy": (
                "candidate_required_joint_valid_set_strictly_contains_baseline"
            ),
            "selection_uses_feature_values": False,
            "selection_uses_event_outcomes": False,
            "selection_uses_grades_or_thresholds": False,
        },
        "baseline": {
            "feature_vector_complete": baseline_feature,
            "feature_vector_incomplete": baseline_instances - baseline_feature,
            "operational_measured": baseline_operational,
            "operational_unavailable": baseline_instances - baseline_operational,
            "measurement_hard_fail": hard_fail,
        },
        "routed_projection": {
            "feature_vector_recovered": feature_recovered,
            "feature_vector_regressed": feature_regressed,
            "feature_vector_complete": routed_feature,
            "feature_vector_incomplete": baseline_instances - routed_feature,
            "operational_recovered": operational_recovered,
            "operational_regressed": operational_regressed,
            "operational_measured": routed_operational,
            "operational_unavailable": routed_unavailable,
            "measurement_hard_fail": hard_fail,
            "non_hard_fail_feature_incomplete": routed_unavailable - hard_fail,
        },
        "comparison_to_uniform_margin": {
            "uniform_margin_target_frames": int(
                m67_summary["m67_uniform_roi_margin"]["target_frames"]
            ),
            "uniform_margin_operational_recovered": int(
                m67_summary["m67_uniform_roi_margin"]["operational_impact"][
                    "recovered"
                ]
            ),
            "uniform_margin_operational_regressed": int(
                m67_summary["m67_uniform_roi_margin"]["operational_impact"][
                    "regressed"
                ]
            ),
            "unrouted_composition_operational_recovered": int(
                m67_summary["composed_experimental_projection"][
                    "operational_impact"
                ]["recovered"]
            ),
            "unrouted_composition_operational_regressed": int(
                m67_summary["composed_experimental_projection"][
                    "operational_impact"
                ]["regressed"]
            ),
            "router_preserves_same_net_operational_gain_without_current_set_regression": (
                operational_recovered - operational_regressed
                == int(
                    m67_summary["composed_experimental_projection"][
                        "operational_impact"
                    ]["recovered"]
                )
                - int(
                    m67_summary["composed_experimental_projection"][
                        "operational_impact"
                    ]["regressed"]
                )
                and operational_regressed == 0
            ),
        },
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "reason_codes": [
                "manual_keypoint_truth_missing",
                "per_view_feature_error_not_evaluated",
                "independent_video_release_test_missing",
            ],
            "next_required_evidence": (
                "manual corrected keypoints on routed and rejected frames, "
                "per-view feature error, and a preregistered independent-video test"
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
            "current_three_video_regression_free_is_not_independent_validation": True,
        },
    }
    validate_summary(report)
    return report


def validate_summary(report: dict[str, Any]) -> None:
    if report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported M68 summary")
    if report.get("status") != (
        "regression_free_observability_router_candidate_requires_truth"
    ):
        raise ValueError("unsafe M68 status")
    scope = report.get("scope", {})
    if int(scope.get("video_count", -1)) != 3 or int(
        scope.get("indicator_instances", -1)
    ) <= 0:
        raise ValueError("M68 scope is invalid")
    router = report.get("router", {})
    if (
        int(router.get("selected_margin_frames", -1))
        + int(router.get("rejected_margin_frames", -1))
        != int(router.get("candidate_margin_target_frames", -1))
    ):
        raise ValueError("M68 router frame accounting is inconsistent")
    for field in (
        "selection_uses_feature_values",
        "selection_uses_event_outcomes",
        "selection_uses_grades_or_thresholds",
    ):
        if router.get(field) is not False:
            raise ValueError(f"unsafe M68 router selection: {field}")
    baseline = report.get("baseline", {})
    routed = report.get("routed_projection", {})
    total = int(scope["indicator_instances"])
    if int(baseline.get("feature_vector_complete", -1)) + int(
        baseline.get("feature_vector_incomplete", -1)
    ) != total or int(routed.get("feature_vector_complete", -1)) + int(
        routed.get("feature_vector_incomplete", -1)
    ) != total:
        raise ValueError("M68 feature balance is invalid")
    if int(baseline.get("operational_measured", -1)) + int(
        baseline.get("operational_unavailable", -1)
    ) != total or int(routed.get("operational_measured", -1)) + int(
        routed.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("M68 operational balance is invalid")
    if int(routed.get("feature_vector_regressed", -1)) != 0 or int(
        routed.get("operational_regressed", -1)
    ) != 0:
        raise ValueError("M68 summary is not regression-free")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get(
        "candidate_promoted"
    ) is not False:
        raise ValueError("M68 experimental result cannot change production")
    for field, value in report.get("safety", {}).items():
        if field == "current_three_video_regression_free_is_not_independent_validation":
            if value is not True:
                raise ValueError("M68 independent validation disclaimer is missing")
        elif value is not False:
            raise ValueError(f"unsafe M68 claim: {field}")


def validate_summary_sources(report: dict[str, Any]) -> None:
    validate_summary(report)
    sources = report.get("sources", {})
    router_sources = sources.get("router_reports", [])
    if len(router_sources) != 3:
        raise ValueError("M68 source report set is incomplete")
    for source in router_sources + [sources.get("m67_summary", {})]:
        path = Path(source.get("path", ""))
        if not path.is_file() or _sha256(path) != str(source.get("sha256", "")).upper():
            raise ValueError("M68 source hash mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--router-report", action="append", type=Path, required=True)
    parser.add_argument("--m67-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; M68 summary is immutable")
    router_reports = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.router_report
    ]
    m67 = json.loads(args.m67_summary.read_text(encoding="utf-8"))
    report = build_summary(
        router_reports=router_reports,
        m67_summary=m67,
        sources={
            "router_reports": [_source(path) for path in args.router_report],
            "m67_summary": _source(args.m67_summary),
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    validate_summary_sources(report)
    print(json.dumps({"report": str(args.output), **report["routed_projection"]}))


if __name__ == "__main__":
    main()
