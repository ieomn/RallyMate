#!/usr/bin/env python3
"""Aggregate M66 small-ROI and M67 ROI-margin composition experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_VERSION = "multivideo-pose-observability-recovery-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _sum_impact(reports: list[dict[str, Any]], field: str) -> dict[str, int]:
    return {
        "recovered": sum(
            int(report[field]["recovered_indicator_instance_count"])
            for report in reports
        ),
        "regressed": sum(
            int(report[field]["regressed_indicator_instance_count"])
            for report in reports
        ),
    }


def build_summary(
    *,
    m66: dict[str, Any],
    margin_reports: list[dict[str, Any]],
    clipped_reports: list[dict[str, Any]],
    combined_reports: list[dict[str, Any]],
    source_records: dict[str, Any],
) -> dict[str, Any]:
    video_ids = sorted(str(report["video_id"]) for report in combined_reports)
    if len(video_ids) != 3 or len(set(video_ids)) != 3:
        raise ValueError("M67 summary requires exactly three unique videos")
    if len(margin_reports) != 3 or len(clipped_reports) != 3:
        raise ValueError("M67 summary requires three reports per ROI-margin policy")
    baseline = m66["baseline"]
    if int(baseline["indicator_instances"]) <= 0:
        raise ValueError("M66 baseline is invalid")
    if any(
        report.get("status") != "experimental_observability_only_not_production"
        for report in margin_reports + clipped_reports + combined_reports
    ):
        raise ValueError("an input report is not safely experimental")
    if any(
        report.get("safety", {}).get("accuracy_claim") is not False
        or report.get("safety", {}).get("production_enabled") is not False
        for report in margin_reports + clipped_reports + combined_reports
    ):
        raise ValueError("an input report makes an unsafe claim")
    margin_vector = _sum_impact(margin_reports, "feature_vector_impact")
    margin_operational = _sum_impact(
        margin_reports, "operational_measurement_impact"
    )
    clipped_vector = _sum_impact(clipped_reports, "feature_vector_impact")
    clipped_operational = _sum_impact(
        clipped_reports, "operational_measurement_impact"
    )
    combined_vector = _sum_impact(combined_reports, "feature_vector_impact")
    combined_operational = _sum_impact(
        combined_reports, "operational_measurement_impact"
    )
    valid_transitions: Counter[str] = Counter()
    for report in margin_reports:
        valid_transitions.update(
            {
                str(key): int(value)
                for key, value in report["inference"][
                    "valid_keypoint_count_transition_counts"
                ].items()
            }
        )
    indicator_instances = int(baseline["indicator_instances"])
    projected_measured = (
        int(baseline["operational_measured"])
        + combined_operational["recovered"]
        - combined_operational["regressed"]
    )
    projected_complete = (
        int(baseline["feature_vector_complete"])
        + combined_vector["recovered"]
        - combined_vector["regressed"]
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "experimental_observability_gain_not_production_or_accuracy",
        "scope": {
            "video_count": 3,
            "video_ids": video_ids,
            "indicator_count": int(m66["scope"]["indicator_count"]),
            "indicator_instances": indicator_instances,
            "current_profile": m66["scope"]["current_profile"],
            "registry_version": m66["scope"]["registry_version"],
            "registry_sha256": m66["scope"]["registry_sha256"],
        },
        "sources": source_records,
        "baseline": baseline,
        "m66_small_roi": {
            "eligible_target_frames": int(
                m66["experiment"]["eligible_target_frames"]
            ),
            "pose_output_produced_frames": int(
                m66["experiment"]["pose_output_recovered_frames"]
            ),
            "feature_vector_recovered": int(
                m66["experiment"]["feature_vector_recovered"]
            ),
            "feature_vector_regressed": int(
                m66["experiment"]["regressed_feature_vectors"]
            ),
            "operational_recovered": int(
                m66["experiment"]["operational_measurement_recovered"]
            ),
            "operational_regressed": int(
                m66["experiment"]["regressed_operational_measurements"]
            ),
        },
        "m67_uniform_roi_margin": {
            "baseline_margin": 0.15,
            "experimental_margin": 0.30,
            "target_frames": sum(
                int(report["inference"]["counts"]["inference_attempted"])
                for report in margin_reports
            ),
            "pose_output_produced_frames": sum(
                int(report["inference"]["counts"]["pose_output_produced"])
                for report in margin_reports
            ),
            "valid_keypoint_count_transition_counts": dict(
                sorted(valid_transitions.items())
            ),
            "feature_vector_impact": margin_vector,
            "operational_impact": margin_operational,
        },
        "m67_clipped_only_candidate": {
            "target_frames": sum(
                int(report["inference"]["counts"]["inference_attempted"])
                for report in clipped_reports
            ),
            "feature_vector_impact": clipped_vector,
            "operational_impact": clipped_operational,
            "isolated_uniform_margin_regression": False,
        },
        "composed_experimental_projection": {
            "composition_was_recomputed_from_combined_frames": True,
            "target_sets_disjoint_within_each_video": all(
                report["composition_audit"]["target_sets_disjoint"] is True
                for report in combined_reports
            ),
            "target_frames": sum(
                int(report["composition_audit"]["target_frame_count"])
                for report in combined_reports
            ),
            "feature_vector_impact": combined_vector,
            "operational_impact": combined_operational,
            "feature_vector_complete": projected_complete,
            "feature_vector_incomplete": indicator_instances - projected_complete,
            "operational_measured": projected_measured,
            "operational_unavailable": indicator_instances - projected_measured,
            "measurement_hard_fail": int(baseline["measurement_hard_fail"]),
            "non_hard_fail_feature_incomplete": (
                indicator_instances
                - projected_measured
                - int(baseline["measurement_hard_fail"])
            ),
        },
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "reason_codes": [
                "manual_keypoint_truth_missing",
                "uniform_roi_margin_has_measured_regression",
                "valid_keypoint_count_not_monotonic",
                "independent_video_release_test_missing",
            ],
            "next_required_evidence": (
                "manual corrected keypoints on target frames, per-view feature-error "
                "evaluation, and a preregistered independent-video release test"
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
            "experimental_projection_is_not_current_production_status": True,
            "feature_coverage_is_not_accuracy": True,
        },
    }
    validate_summary(report)
    return report


def validate_summary(report: dict[str, Any]) -> None:
    if report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported M67 summary version")
    if report.get("status") != "experimental_observability_gain_not_production_or_accuracy":
        raise ValueError("unsafe M67 summary status")
    projection = report.get("composed_experimental_projection", {})
    total = int(report.get("scope", {}).get("indicator_instances", -1))
    if int(projection.get("operational_measured", -1)) + int(
        projection.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("M67 operational projection does not balance")
    if int(projection.get("feature_vector_complete", -1)) + int(
        projection.get("feature_vector_incomplete", -1)
    ) != total:
        raise ValueError("M67 feature projection does not balance")
    if projection.get("composition_was_recomputed_from_combined_frames") is not True:
        raise ValueError("M67 projection was arithmetically combined")
    if report.get("decision", {}).get("production_default_changed") is not False:
        raise ValueError("M67 summary changed production without evidence")
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
            raise ValueError(f"unsafe M67 summary claim: {field}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m66", type=Path, required=True)
    parser.add_argument("--margin-report", type=Path, action="append", required=True)
    parser.add_argument("--clipped-report", type=Path, action="append", required=True)
    parser.add_argument("--combined-report", type=Path, action="append", required=True)
    parser.add_argument("--video-validation", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; summary artifacts are immutable")
    m66 = json.loads(args.m66.read_text(encoding="utf-8"))
    margin_reports = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.margin_report
    ]
    clipped_reports = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.clipped_report
    ]
    combined_reports = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.combined_report
    ]
    report = build_summary(
        m66=m66,
        margin_reports=margin_reports,
        clipped_reports=clipped_reports,
        combined_reports=combined_reports,
        source_records={
            "m66": _source(args.m66),
            "margin_reports": [_source(path) for path in args.margin_report],
            "clipped_reports": [_source(path) for path in args.clipped_report],
            "combined_reports": [_source(path) for path in args.combined_report],
            "dynamic_video_validations": [
                _source(path) for path in args.video_validation
            ],
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["composed_experimental_projection"], ensure_ascii=False))


if __name__ == "__main__":
    main()
