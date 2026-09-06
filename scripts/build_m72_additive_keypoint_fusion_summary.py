#!/usr/bin/env python3
"""Aggregate M72 additive keypoint fusion and replay the remaining residual."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.run_m72_additive_keypoint_fusion import (
        validate_additive_keypoint_fusion_report,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from run_m72_additive_keypoint_fusion import (
        validate_additive_keypoint_fusion_report,
    )


REPORT_VERSION = "multivideo-additive-keypoint-fusion-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _verify(binding: dict[str, Any], label: str) -> Path:
    path = Path(str(binding.get("path", "")))
    if not path.is_file() or _sha256(path) != str(binding.get("sha256", "")).upper():
        raise ValueError(f"{label} binding failed")
    return path


def _identity_set(section: dict[str, Any], field: str) -> set[tuple[str, str]]:
    return {
        (str(item["event_id"]), str(item["indicator_id"]))
        for item in section.get(field, [])
    }


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
    m71_summary_path: Path,
    residual_audit_path: Path,
    report_paths: list[Path],
) -> dict[str, Any]:
    m71 = json.loads(m71_summary_path.read_text(encoding="utf-8"))
    residual = json.loads(residual_audit_path.read_text(encoding="utf-8"))
    if m71.get("report_version") != "multivideo-larger-pose-model-extension-v1.0.0":
        raise ValueError("M72 requires the M71 multivideo summary")
    if residual.get("report_version") != "pose-observability-residual-audit-v1.0.0":
        raise ValueError("M72 requires the M68 residual audit")
    if len(report_paths) != 3:
        raise ValueError("M72 requires exactly three per-video reports")

    by_id: dict[str, dict[str, Any]] = {}
    comparisons: dict[str, dict[str, Any]] = {}
    for path in report_paths:
        item = json.loads(path.read_text(encoding="utf-8"))
        validate_additive_keypoint_fusion_report(item)
        for name, binding in item.get("sources", {}).items():
            _verify(binding, f"{path.name}.sources.{name}")
        for name, binding in item.get("artifacts", {}).items():
            _verify(binding, f"{path.name}.artifacts.{name}")
        video_id = str(item["video_id"])
        if video_id in by_id:
            raise ValueError("duplicate M72 video report")
        m71_report_path = _verify(item["sources"]["m71_report"], "M71 report")
        m71_report = json.loads(m71_report_path.read_text(encoding="utf-8"))
        if m71_report.get("video_id") != video_id:
            raise ValueError("M72 report binds another video's M71 report")
        by_id[video_id] = item
        comparison_path = _verify(
            item["artifacts"]["fixed_boundary_comparison"], "M72 comparison"
        )
        comparisons[video_id] = json.loads(comparison_path.read_text(encoding="utf-8"))

    expected_videos = set(m71["scope"]["video_ids"])
    if set(by_id) != expected_videos or set(residual["scope"]["video_ids"]) != expected_videos:
        raise ValueError("M72, M71, and residual audit video scopes differ")
    reports = [by_id[video_id] for video_id in sorted(by_id)]
    total = int(m71["scope"]["indicator_instances"])
    baseline_m68 = m71["baseline_m68"]
    baseline_m71 = m71["m71_projection"]
    feature_recovered = _sum(
        reports, "comparison_to_m68", "feature_vector", "recovered_indicator_instance_count"
    )
    feature_regressed = _sum(
        reports, "comparison_to_m68", "feature_vector", "regressed_indicator_instance_count"
    )
    operational_recovered = _sum(
        reports, "comparison_to_m68", "operational_measurement", "recovered_indicator_instance_count"
    )
    operational_regressed = _sum(
        reports, "comparison_to_m68", "operational_measurement", "regressed_indicator_instance_count"
    )
    additional_feature = _sum(
        reports, "m71_preservation", "additional_feature_recovery_count"
    )
    additional_operational = _sum(
        reports, "m71_preservation", "additional_operational_recovery_count"
    )
    lost_feature = _sum(
        reports, "m71_preservation", "lost_m71_feature_recovery_count"
    )
    lost_operational = _sum(
        reports, "m71_preservation", "lost_m71_operational_recovery_count"
    )

    recovered_operational: set[tuple[str, str, str]] = set()
    for video_id, item in by_id.items():
        recovered_operational |= {
            (video_id, event_id, indicator_id)
            for event_id, indicator_id in _identity_set(
                item["comparison_to_m68"]["operational_measurement"],
                "recovered_indicator_instances",
            )
        }
    remaining: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    feature_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    for source_item in residual["residual_items"]:
        identity = (
            str(source_item["video_id"]),
            str(source_item["event_id"]),
            str(source_item["indicator_id"]),
        )
        if identity in recovered_operational:
            continue
        video_id, event_id, indicator_id = identity
        comparison = comparisons[video_id]
        final_model = str(comparison["model_order"][1])
        invalid: list[dict[str, Any]] = []
        for pair in comparison["feature_pairs"]:
            if (
                str(pair["event_id"]) != event_id
                or indicator_id not in pair["indicator_ids"]
            ):
                continue
            observation = pair["models"][final_model]
            if observation.get("valid") is True:
                continue
            reason = str(observation.get("reason"))
            feature = str(pair["feature_name"])
            reason_counts[reason] += 1
            feature_counts[feature] += 1
            invalid.append(
                {
                    "feature_name": feature,
                    "reason": reason,
                    "valid_fraction": observation.get("provenance", {}).get(
                        "valid_fraction"
                    ),
                    "confidence": observation.get("confidence"),
                    "source_frames": observation.get("source_frames", []),
                }
            )
        if not invalid:
            raise ValueError("remaining residual has no invalid final feature")
        if any(item["reason"] == "required_phase_proxy_unavailable" for item in invalid):
            classification = "required_pose_phase_proxy_not_observed"
        elif int(source_item["start_ms"]) == 0:
            classification = "video_start_boundary_censored_observation"
        else:
            classification = "event_observation_coverage_below_feature_contract"
        classification_counts[classification] += 1
        remaining.append(
            {
                "video_id": video_id,
                "event_id": event_id,
                "event_code": str(source_item["event_code"]),
                "start_ms": int(source_item["start_ms"]),
                "end_ms": int(source_item["end_ms"]),
                "indicator_id": indicator_id,
                "classification": classification,
                "invalid_features": invalid,
            }
        )

    projection = {
        "changed_frame_count": _sum(reports, "fusion_audit", "changed_frame_count"),
        "added_valid_joint_observation_count": _sum(
            reports, "fusion_audit", "added_valid_joint_observation_count"
        ),
        "feature_recovered_from_m68": feature_recovered,
        "feature_regressed_from_m68": feature_regressed,
        "feature_vector_complete": int(baseline_m68["feature_vector_complete"])
        + feature_recovered
        - feature_regressed,
        "feature_vector_incomplete": int(baseline_m68["feature_vector_incomplete"])
        - feature_recovered
        + feature_regressed,
        "operational_recovered_from_m68": operational_recovered,
        "operational_regressed_from_m68": operational_regressed,
        "operational_measured": int(baseline_m68["operational_measured"])
        + operational_recovered
        - operational_regressed,
        "operational_unavailable": int(baseline_m68["operational_unavailable"])
        - operational_recovered
        + operational_regressed,
        "measurement_hard_fail": int(baseline_m68["measurement_hard_fail"]),
        "non_hard_fail_operational_residual": len(remaining),
        "additional_feature_recovery_over_m71": additional_feature,
        "additional_operational_recovery_over_m71": additional_operational,
        "lost_m71_feature_recovery_count": lost_feature,
        "lost_m71_operational_recovery_count": lost_operational,
    }
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "experimental_additive_keypoint_fusion_regression_free_requires_truth",
        "scope": {
            "video_count": 3,
            "video_ids": sorted(expected_videos),
            "indicator_count": int(m71["scope"]["indicator_count"]),
            "indicator_instances": total,
            "target_frame_count": sum(
                int(item["fusion_audit"]["target_frame_count"]) for item in reports
            ),
        },
        "sources": {
            "m71_summary": _source(m71_summary_path),
            "residual_audit": _source(residual_audit_path),
            "per_video_reports": [_source(path) for path in sorted(report_paths)],
        },
        "fusion_contract": {
            "fusion_version": "same-topology-missing-keypoint-addition-v1.0.0",
            "allowed_joint_names": reports[0]["fusion_audit"]["allowed_joint_names"],
            "baseline_valid_coordinates_overwritten": 0,
            "same_topology_required": True,
            "selection_uses_feature_values": False,
            "selection_uses_event_outcomes": False,
            "selection_uses_grades_or_thresholds": False,
        },
        "baseline_m68": baseline_m68,
        "baseline_m71": baseline_m71,
        "m72_projection": projection,
        "remaining_residual": {
            "indicator_instance_count": len(remaining),
            "classification_counts": dict(sorted(classification_counts.items())),
            "invalid_feature_reason_counts": dict(sorted(reason_counts.items())),
            "invalid_feature_counts": dict(sorted(feature_counts.items())),
            "items": remaining,
        },
        "by_video": [
            {
                "video_id": video_id,
                "changed_frames": int(by_id[video_id]["fusion_audit"]["changed_frame_count"]),
                "added_valid_joint_observations": int(
                    by_id[video_id]["fusion_audit"]["added_valid_joint_observation_count"]
                ),
                "additional_feature_recovery_over_m71": int(
                    by_id[video_id]["m71_preservation"]["additional_feature_recovery_count"]
                ),
                "additional_operational_recovery_over_m71": int(
                    by_id[video_id]["m71_preservation"]["additional_operational_recovery_count"]
                ),
            }
            for video_id in sorted(by_id)
        ],
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "m71_recovered_sets_preserved": lost_feature == 0 and lost_operational == 0,
            "fusion_is_experimental_truth_required": True,
            "next_required_evidence": (
                "independently adjudicated per-joint error for every fusion addition and "
                "a preregistered independent-video validation"
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
            "current_three_video_regression_free_is_not_independent_validation": True,
        },
    }
    validate_summary(report)
    return report


def validate_summary(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported M72 multivideo report")
    if report.get("status") != "experimental_additive_keypoint_fusion_regression_free_requires_truth":
        raise ValueError("unsafe M72 summary status")
    scope = report.get("scope", {})
    total = int(scope.get("indicator_instances", -1))
    if int(scope.get("video_count", -1)) != 3 or len(scope.get("video_ids", [])) != 3:
        raise ValueError("M72 must retain the three-video scope")
    projection = report.get("m72_projection", {})
    if int(projection.get("feature_vector_complete", -1)) + int(
        projection.get("feature_vector_incomplete", -1)
    ) != total:
        raise ValueError("M72 feature projection does not balance")
    if int(projection.get("operational_measured", -1)) + int(
        projection.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("M72 operational projection does not balance")
    residual = report.get("remaining_residual", {})
    if int(residual.get("indicator_instance_count", -1)) != len(residual.get("items", [])):
        raise ValueError("M72 residual item accounting is inconsistent")
    if int(projection.get("non_hard_fail_operational_residual", -1)) != int(
        residual.get("indicator_instance_count", -2)
    ):
        raise ValueError("M72 residual projection does not match replayed items")
    if any(
        int(projection.get(field, -1)) != 0
        for field in (
            "feature_regressed_from_m68",
            "operational_regressed_from_m68",
            "lost_m71_feature_recovery_count",
            "lost_m71_operational_recovery_count",
        )
    ):
        raise ValueError("M72 summary is not regression-free")
    contract = report.get("fusion_contract", {})
    for field, expected in {
        "baseline_valid_coordinates_overwritten": 0,
        "same_topology_required": True,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
    }.items():
        if contract.get(field) != expected:
            raise ValueError(f"unsafe M72 fusion contract: {field}")
    decision = report.get("decision", {})
    if (
        decision.get("production_default_changed") is not False
        or decision.get("candidate_promoted") is not False
        or decision.get("m71_recovered_sets_preserved") is not True
        or decision.get("fusion_is_experimental_truth_required") is not True
    ):
        raise ValueError("unsafe M72 decision")
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
            raise ValueError(f"unsafe M72 claim: {field}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m71-summary", type=Path, required=True)
    parser.add_argument("--residual-audit", type=Path, required=True)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; reports are immutable")
    report = build_summary(
        m71_summary_path=args.m71_summary,
        residual_audit_path=args.residual_audit,
        report_paths=args.report,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"report": str(args.output), "projection": report["m72_projection"]}))


if __name__ == "__main__":
    main()
