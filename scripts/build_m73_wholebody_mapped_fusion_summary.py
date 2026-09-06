#!/usr/bin/env python3
"""Aggregate M73 WholeBody133 mapped fusion and replay every residual."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.build_m72_additive_keypoint_fusion_summary import (
        _identity_set,
        _source,
        _sum,
        _verify,
        validate_summary as validate_m72_summary,
    )
    from scripts.run_m73_wholebody_mapped_keypoint_fusion import (
        validate_wholebody_mapped_keypoint_fusion_report,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from build_m72_additive_keypoint_fusion_summary import (
        _identity_set,
        _source,
        _sum,
        _verify,
        validate_summary as validate_m72_summary,
    )
    from run_m73_wholebody_mapped_keypoint_fusion import (
        validate_wholebody_mapped_keypoint_fusion_report,
    )


REPORT_VERSION = "multivideo-wholebody133-mapped-fusion-v1.0.0"


def build_summary(
    *, m72_summary_path: Path, report_paths: list[Path]
) -> dict[str, Any]:
    m72 = json.loads(m72_summary_path.read_text(encoding="utf-8"))
    validate_m72_summary(m72)
    if len(report_paths) != 3:
        raise ValueError("M73 requires exactly three per-video reports")
    by_id: dict[str, dict[str, Any]] = {}
    comparisons: dict[str, dict[str, Any]] = {}
    for path in report_paths:
        item = json.loads(path.read_text(encoding="utf-8"))
        validate_wholebody_mapped_keypoint_fusion_report(item)
        for name, binding in item.get("sources", {}).items():
            _verify(binding, f"{path.name}.sources.{name}")
        for name, binding in item.get("artifacts", {}).items():
            _verify(binding, f"{path.name}.artifacts.{name}")
        video_id = str(item["video_id"])
        if video_id in by_id:
            raise ValueError("duplicate M73 video report")
        m72_report_path = _verify(item["sources"]["m72_report"], "M72 report")
        m72_report = json.loads(m72_report_path.read_text(encoding="utf-8"))
        if m72_report.get("video_id") != video_id:
            raise ValueError("M73 report binds another video's M72 report")
        by_id[video_id] = item
        comparison_path = _verify(
            item["artifacts"]["fixed_boundary_comparison"], "M73 comparison"
        )
        comparisons[video_id] = json.loads(
            comparison_path.read_text(encoding="utf-8")
        )
    expected_videos = set(m72["scope"]["video_ids"])
    if set(by_id) != expected_videos:
        raise ValueError("M73 and M72 video scopes differ")
    reports = [by_id[video_id] for video_id in sorted(by_id)]
    total = int(m72["scope"]["indicator_instances"])
    baseline_m68 = m72["baseline_m68"]
    baseline_m72 = m72["m72_projection"]
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
        reports, "m72_preservation", "additional_feature_recovery_count"
    )
    additional_operational = _sum(
        reports, "m72_preservation", "additional_operational_recovery_count"
    )
    lost_feature = _sum(
        reports, "m72_preservation", "lost_m72_feature_recovery_count"
    )
    lost_operational = _sum(
        reports, "m72_preservation", "lost_m72_operational_recovery_count"
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
    for source_item in m72["remaining_residual"]["items"]:
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
            raise ValueError("remaining M73 residual has no invalid final feature")
        classification = str(source_item["classification"])
        if classification == "required_pose_phase_proxy_not_observed" and not any(
            item["reason"] == "required_phase_proxy_unavailable" for item in invalid
        ):
            raise ValueError("M73 phase residual lost its required phase evidence")
        if classification == "video_start_boundary_censored_observation" and int(
            source_item["start_ms"]
        ) != 0:
            raise ValueError("M73 boundary-censored residual is not at video start")
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
        "candidate_pose_output_count": _sum(
            reports, "inference", "pose_output_produced"
        ),
        "candidate_pose_missing_count": _sum(
            reports, "inference", "pose_output_missing"
        ),
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
        "additional_feature_recovery_over_m72": additional_feature,
        "additional_operational_recovery_over_m72": additional_operational,
        "lost_m72_feature_recovery_count": lost_feature,
        "lost_m72_operational_recovery_count": lost_operational,
    }
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "experimental_wholebody_mapped_fusion_regression_free_no_additional_indicator_recovery",
        "scope": {
            "video_count": 3,
            "video_ids": sorted(expected_videos),
            "indicator_count": int(m72["scope"]["indicator_count"]),
            "indicator_instances": total,
            "target_frame_count": sum(
                int(item["fusion_audit"]["target_frame_count"]) for item in reports
            ),
        },
        "sources": {
            "m72_summary": _source(m72_summary_path),
            "per_video_reports": [_source(path) for path in sorted(report_paths)],
        },
        "fusion_contract": {
            "fusion_version": "mapped-topology-missing-keypoint-addition-v1.0.0",
            "baseline_keypoint_format": "halpe26",
            "candidate_keypoint_format": "coco_wholebody133",
            "target_to_candidate_joint_names": reports[0]["fusion_audit"][
                "target_to_candidate_joint_names"
            ],
            "mapping_is_one_to_one": True,
            "baseline_valid_coordinates_overwritten": 0,
            "baseline_topology_preserved": True,
            "explicit_topology_map_required": True,
            "selection_uses_feature_values": False,
            "selection_uses_event_outcomes": False,
            "selection_uses_grades_or_thresholds": False,
        },
        "baseline_m68": baseline_m68,
        "baseline_m72": baseline_m72,
        "m73_projection": projection,
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
                "candidate_pose_outputs": int(
                    by_id[video_id]["inference"]["pose_output_produced"]
                ),
                "changed_frames": int(
                    by_id[video_id]["fusion_audit"]["changed_frame_count"]
                ),
                "added_valid_joint_observations": int(
                    by_id[video_id]["fusion_audit"][
                        "added_valid_joint_observation_count"
                    ]
                ),
                "additional_feature_recovery_over_m72": int(
                    by_id[video_id]["m72_preservation"][
                        "additional_feature_recovery_count"
                    ]
                ),
                "additional_operational_recovery_over_m72": int(
                    by_id[video_id]["m72_preservation"][
                        "additional_operational_recovery_count"
                    ]
                ),
            }
            for video_id in sorted(by_id)
        ],
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "m72_recovered_sets_preserved": lost_feature == 0 and lost_operational == 0,
            "additional_indicator_recovery_observed": (
                additional_feature > 0 or additional_operational > 0
            ),
            "denser_topology_alone_did_not_resolve_remaining_indicator_contracts": (
                additional_feature == 0 and additional_operational == 0
            ),
            "next_direction": (
                "event-local temporal coverage and phase-boundary evidence; not more "
                "unvalidated point-count expansion"
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
            "point_count_is_not_accuracy_or_indicator_recovery": True,
        },
    }
    validate_summary(report)
    return report


def validate_summary(report: dict[str, Any]) -> None:
    if (
        report.get("schema_version") != "1.0.0"
        or report.get("report_version") != REPORT_VERSION
        or report.get("status")
        != "experimental_wholebody_mapped_fusion_regression_free_no_additional_indicator_recovery"
    ):
        raise ValueError("unsupported or unsafe M73 multivideo report")
    scope = report.get("scope", {})
    total = int(scope.get("indicator_instances", -1))
    if int(scope.get("video_count", -1)) != 3 or len(scope.get("video_ids", [])) != 3:
        raise ValueError("M73 must retain the three-video scope")
    projection = report.get("m73_projection", {})
    if int(projection.get("feature_vector_complete", -1)) + int(
        projection.get("feature_vector_incomplete", -1)
    ) != total:
        raise ValueError("M73 feature projection does not balance")
    if int(projection.get("operational_measured", -1)) + int(
        projection.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("M73 operational projection does not balance")
    residual = report.get("remaining_residual", {})
    if int(residual.get("indicator_instance_count", -1)) != len(
        residual.get("items", [])
    ) or int(projection.get("non_hard_fail_operational_residual", -1)) != int(
        residual.get("indicator_instance_count", -2)
    ):
        raise ValueError("M73 residual accounting is inconsistent")
    if any(
        int(projection.get(field, -1)) != 0
        for field in (
            "feature_regressed_from_m68",
            "operational_regressed_from_m68",
            "additional_feature_recovery_over_m72",
            "additional_operational_recovery_over_m72",
            "lost_m72_feature_recovery_count",
            "lost_m72_operational_recovery_count",
        )
    ):
        raise ValueError("M73 must preserve M72 and report the observed zero gain")
    contract = report.get("fusion_contract", {})
    for field, expected in {
        "baseline_keypoint_format": "halpe26",
        "candidate_keypoint_format": "coco_wholebody133",
        "mapping_is_one_to_one": True,
        "baseline_valid_coordinates_overwritten": 0,
        "baseline_topology_preserved": True,
        "explicit_topology_map_required": True,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
    }.items():
        if contract.get(field) != expected:
            raise ValueError(f"unsafe M73 fusion contract: {field}")
    mapping = contract.get("target_to_candidate_joint_names", {})
    if not mapping or any(target != source for target, source in mapping.items()):
        raise ValueError("M73 summary contains a non-semantic topology map")
    decision = report.get("decision", {})
    if (
        decision.get("production_default_changed") is not False
        or decision.get("candidate_promoted") is not False
        or decision.get("m72_recovered_sets_preserved") is not True
        or decision.get("additional_indicator_recovery_observed") is not False
        or decision.get(
            "denser_topology_alone_did_not_resolve_remaining_indicator_contracts"
        )
        is not True
    ):
        raise ValueError("unsafe M73 decision")
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
            raise ValueError(f"unsafe M73 claim: {field}")
    if report.get("safety", {}).get(
        "point_count_is_not_accuracy_or_indicator_recovery"
    ) is not True:
        raise ValueError("M73 must disclaim point-count accuracy")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m72-summary", type=Path, required=True)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; reports are immutable")
    report = build_summary(
        m72_summary_path=args.m72_summary, report_paths=args.report
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"report": str(args.output), "projection": report["m73_projection"]}))


if __name__ == "__main__":
    main()
