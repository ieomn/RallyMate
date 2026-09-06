#!/usr/bin/env python3
"""Aggregate the M70 crop-context/Pose-profile ablation and safe extension."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rallymate_evaluation.pose_observability_residual import (
    validate_pose_observability_residual_audit_sources,
)

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


REPORT_VERSION = "multivideo-pose-profile-context-ablation-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _verify_bindings(value: Any, label: str) -> None:
    if isinstance(value, dict):
        if set(value) >= {"path", "sha256"}:
            path = Path(str(value["path"]))
            expected = str(value["sha256"]).upper()
            if not path.is_file() or _sha256(path) != expected:
                raise ValueError(f"{label} binding failed")
            return
        for key, nested in value.items():
            _verify_bindings(nested, f"{label}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _verify_bindings(nested, f"{label}[{index}]")


def _identity_set(section: dict[str, Any], field: str) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for item in section.get(field, []):
        if not isinstance(item, dict):
            raise ValueError(f"{field} must contain identity objects")
        identities.add((str(item["event_id"]), str(item["indicator_id"])))
    return identities


def _sum(reports: Iterable[dict[str, Any]], *keys: str) -> int:
    total = 0
    for report in reports:
        value: Any = report
        for key in keys:
            value = value[key]
        total += int(value)
    return total


def build_summary(
    *,
    residual_audit_path: Path,
    ablation_report_paths: list[Path],
    anchored_report_paths: list[Path],
) -> dict[str, Any]:
    residual = json.loads(residual_audit_path.read_text(encoding="utf-8"))
    validate_pose_observability_residual_audit_sources(residual)
    if len(ablation_report_paths) != 3 or len(anchored_report_paths) != 3:
        raise ValueError("M70 requires exactly three ablation and three anchored reports")

    ablations: dict[str, dict[str, Any]] = {}
    anchored: dict[str, dict[str, Any]] = {}
    for path in ablation_report_paths:
        item = json.loads(path.read_text(encoding="utf-8"))
        validate_pose_profile_ablation_report(item)
        _verify_bindings(item.get("sources", {}), "ablation.sources")
        _verify_bindings(item.get("artifacts", {}), "ablation.artifacts")
        if str(item["sources"]["residual_audit"]["sha256"]).upper() != _sha256(
            residual_audit_path
        ):
            raise ValueError("M70 ablation does not bind the residual audit")
        video_id = str(item["video_id"])
        if video_id in ablations:
            raise ValueError("duplicate M70 ablation video")
        ablations[video_id] = item
    for path in anchored_report_paths:
        item = json.loads(path.read_text(encoding="utf-8"))
        validate_anchored_pose_extension_report(item)
        _verify_bindings(item.get("sources", {}), "anchored.sources")
        _verify_bindings(item.get("artifacts", {}), "anchored.artifacts")
        video_id = str(item["video_id"])
        if video_id in anchored:
            raise ValueError("duplicate M70 anchored video")
        expected_ablation = next(
            candidate
            for candidate in ablation_report_paths
            if json.loads(candidate.read_text(encoding="utf-8"))["video_id"] == video_id
        )
        binding = item["sources"]["ablation_report"]
        if Path(binding["path"]).resolve() != expected_ablation.resolve() or str(
            binding["sha256"]
        ).upper() != _sha256(expected_ablation):
            raise ValueError("anchored report does not bind its ablation report")
        anchored[video_id] = item

    expected_videos = set(residual["scope"]["video_ids"])
    if set(ablations) != expected_videos or set(anchored) != expected_videos:
        raise ValueError("M70 reports do not cover the residual video scope")
    ordered_ablation = [ablations[key] for key in sorted(ablations)]
    ordered_anchored = [anchored[key] for key in sorted(anchored)]

    uniform_feature: set[tuple[str, str]] = set()
    uniform_operational: set[tuple[str, str]] = set()
    multi_feature: set[tuple[str, str]] = set()
    multi_operational: set[tuple[str, str]] = set()
    for item in ordered_ablation:
        uniform_feature |= _identity_set(
            item["impacts"]["uniform_context_combined"]["feature_vector"],
            "recovered_indicator_instances",
        )
        uniform_operational |= _identity_set(
            item["impacts"]["uniform_context_combined"]["operational_measurement"],
            "recovered_indicator_instances",
        )
        multi_feature |= _identity_set(
            item["impacts"]["multicandidate"]["feature_vector"],
            "recovered_indicator_instances",
        )
        multi_operational |= _identity_set(
            item["impacts"]["multicandidate"]["operational_measurement"],
            "recovered_indicator_instances",
        )

    indicator_instances = int(residual["scope"]["indicator_instances"])
    baseline_feature = int(residual["counts"]["feature_vector_complete"])
    baseline_operational = int(residual["counts"]["operational_measured"])
    residual_input = int(residual["counts"]["non_hard_fail_feature_incomplete"])
    hard_fail = int(residual["counts"]["measurement_hard_fail"])

    def strategy(section: str, *, selected: int) -> dict[str, int]:
        feature_recovered = _sum(
            ordered_ablation,
            "impacts",
            section,
            "feature_vector",
            "recovered_indicator_instance_count",
        )
        feature_regressed = _sum(
            ordered_ablation,
            "impacts",
            section,
            "feature_vector",
            "regressed_indicator_instance_count",
        )
        operational_recovered = _sum(
            ordered_ablation,
            "impacts",
            section,
            "operational_measurement",
            "recovered_indicator_instance_count",
        )
        operational_regressed = _sum(
            ordered_ablation,
            "impacts",
            section,
            "operational_measurement",
            "regressed_indicator_instance_count",
        )
        return {
            "selected_frame_count": selected,
            "feature_recovered": feature_recovered,
            "feature_regressed": feature_regressed,
            "operational_recovered": operational_recovered,
            "operational_regressed": operational_regressed,
        }

    profile_only = strategy(
        "profile_only",
        selected=_sum(ordered_ablation, "router_audits", "profile_only", "selected_frame_count"),
    )
    uniform = strategy(
        "uniform_context_combined",
        selected=0,
    )
    # The authoritative M69 router counts are in the bound M69 reports.
    uniform["selected_frame_count"] = 0
    for item in ordered_ablation:
        m69_path = Path(item["sources"]["m69_uniform_high_resolution_report"]["path"])
        m69 = json.loads(m69_path.read_text(encoding="utf-8"))
        uniform["selected_frame_count"] += int(m69["router_audit"]["selected_frame_count"])
    multicandidate = strategy(
        "multicandidate",
        selected=_sum(
            ordered_ablation, "router_audits", "multicandidate", "selected_frame_count"
        ),
    )
    multicandidate.update(
        {
            "lost_uniform_feature_recovery_count": len(uniform_feature - multi_feature),
            "additional_feature_recovery_count": len(multi_feature - uniform_feature),
            "lost_uniform_operational_recovery_count": len(
                uniform_operational - multi_operational
            ),
            "additional_operational_recovery_count": len(
                multi_operational - uniform_operational
            ),
            "preserves_uniform_recovered_sets": (
                uniform_feature <= multi_feature
                and uniform_operational <= multi_operational
            ),
        }
    )

    anchored_feature = _sum(
        ordered_anchored,
        "comparison_to_m68",
        "feature_vector",
        "recovered_indicator_instance_count",
    )
    anchored_feature_regressed = _sum(
        ordered_anchored,
        "comparison_to_m68",
        "feature_vector",
        "regressed_indicator_instance_count",
    )
    anchored_operational = _sum(
        ordered_anchored,
        "comparison_to_m68",
        "operational_measurement",
        "recovered_indicator_instance_count",
    )
    anchored_operational_regressed = _sum(
        ordered_anchored,
        "comparison_to_m68",
        "operational_measurement",
        "regressed_indicator_instance_count",
    )
    anchored_projection = {
        "extension_selected_frame_count": _sum(
            ordered_anchored, "router_audit", "selected_frame_count"
        ),
        "feature_recovered": anchored_feature,
        "feature_regressed": anchored_feature_regressed,
        "feature_vector_complete": baseline_feature
        + anchored_feature
        - anchored_feature_regressed,
        "feature_vector_incomplete": indicator_instances
        - baseline_feature
        - anchored_feature
        + anchored_feature_regressed,
        "operational_recovered": anchored_operational,
        "operational_regressed": anchored_operational_regressed,
        "operational_measured": baseline_operational
        + anchored_operational
        - anchored_operational_regressed,
        "operational_unavailable": indicator_instances
        - baseline_operational
        - anchored_operational
        + anchored_operational_regressed,
        "measurement_hard_fail": hard_fail,
        "non_hard_fail_feature_incomplete": residual_input
        - anchored_operational
        + anchored_operational_regressed,
        "additional_feature_recovery_over_m69": _sum(
            ordered_anchored,
            "m69_preservation",
            "additional_feature_recovery_count",
        ),
        "additional_operational_recovery_over_m69": _sum(
            ordered_anchored,
            "m69_preservation",
            "additional_operational_recovery_count",
        ),
        "lost_m69_feature_recovery_count": _sum(
            ordered_anchored,
            "m69_preservation",
            "lost_m69_feature_recovery_count",
        ),
        "lost_m69_operational_recovery_count": _sum(
            ordered_anchored,
            "m69_preservation",
            "lost_m69_operational_recovery_count",
        ),
    }

    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "m69_anchored_extension_regression_free_requires_truth",
        "scope": {
            "video_count": 3,
            "video_ids": sorted(expected_videos),
            "indicator_count": int(residual["scope"]["indicator_count"]),
            "indicator_instances": indicator_instances,
            "input_residual_indicator_instances": residual_input,
            "input_residual_events": int(residual["counts"]["residual_event_count"]),
            "target_event_frame_count": int(residual["counts"]["candidate_frame_count"]),
        },
        "sources": {
            "residual_audit": _source(residual_audit_path),
            "ablation_reports": [_source(path) for path in sorted(ablation_report_paths)],
            "anchored_reports": [_source(path) for path in sorted(anchored_report_paths)],
        },
        "context_replay": {
            "target_frame_count": _sum(
                ordered_ablation, "crop_context_replay", "target_frame_count"
            ),
            "source_frame_count_by_policy": {
                policy: sum(
                    int(item["crop_context_replay"]["source_frame_count_by_policy"].get(policy, 0))
                    for item in ordered_ablation
                )
                for policy in ("baseline", "roi_margin_candidate", "small_roi_min8")
            },
            "pose_output_produced": _sum(
                ordered_ablation, "inference", "pose_output_produced"
            ),
            "pose_output_missing": _sum(
                ordered_ablation, "inference", "pose_output_missing"
            ),
            "changes_only_pose_profile_with_exact_m68_crop_context": True,
        },
        "baseline": {
            "feature_vector_complete": baseline_feature,
            "feature_vector_incomplete": indicator_instances - baseline_feature,
            "operational_measured": baseline_operational,
            "operational_unavailable": indicator_instances - baseline_operational,
            "measurement_hard_fail": hard_fail,
            "non_hard_fail_feature_incomplete": residual_input,
        },
        "strategies": {
            "context_matched_profile_only": profile_only,
            "uniform_context_m69": uniform,
            "naive_multicandidate": multicandidate,
            "m69_anchored_extension": anchored_projection,
        },
        "by_video": [
            {
                "video_id": video_id,
                "target_frames": ablations[video_id]["inference"]["target_frame_count"],
                "profile_only_selected_frames": ablations[video_id]["router_audits"]["profile_only"]["selected_frame_count"],
                "naive_multicandidate_selected_frames": ablations[video_id]["router_audits"]["multicandidate"]["selected_frame_count"],
                "anchored_extension_selected_frames": anchored[video_id]["router_audit"]["selected_frame_count"],
                "anchored_feature_recovered": anchored[video_id]["comparison_to_m68"]["feature_vector"]["recovered_indicator_instance_count"],
                "anchored_operational_recovered": anchored[video_id]["comparison_to_m68"]["operational_measurement"]["recovered_indicator_instance_count"],
                "additional_operational_recovery_over_m69": anchored[video_id]["m69_preservation"]["additional_operational_recovery_count"],
            }
            for video_id in sorted(expected_videos)
        ],
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "naive_multicandidate_rejected": True,
            "naive_rejection_reason": "does_not_preserve_all_m69_recovered_indicator_instances",
            "anchored_extension_is_best_current_regression_free_candidate": True,
            "reason_codes": [
                "manual_keypoint_truth_missing",
                "per_view_feature_error_not_evaluated",
                "independent_video_release_test_missing",
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
        raise ValueError("unsupported M70 summary")
    if report.get("status") != "m69_anchored_extension_regression_free_requires_truth":
        raise ValueError("unsafe M70 status")
    scope = report.get("scope", {})
    total = int(scope.get("indicator_instances", -1))
    if int(report.get("context_replay", {}).get("target_frame_count", -1)) != int(
        scope.get("target_event_frame_count", -2)
    ):
        raise ValueError("M70 target frame accounting is inconsistent")
    baseline = report.get("baseline", {})
    if int(baseline.get("feature_vector_complete", -1)) + int(
        baseline.get("feature_vector_incomplete", -1)
    ) != total:
        raise ValueError("M70 baseline feature counts do not balance")
    anchored = report.get("strategies", {}).get("m69_anchored_extension", {})
    if int(anchored.get("feature_vector_complete", -1)) + int(
        anchored.get("feature_vector_incomplete", -1)
    ) != total:
        raise ValueError("M70 anchored feature counts do not balance")
    if int(anchored.get("operational_measured", -1)) + int(
        anchored.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("M70 anchored operational counts do not balance")
    if any(
        int(anchored.get(field, -1)) != 0
        for field in (
            "feature_regressed",
            "operational_regressed",
            "lost_m69_feature_recovery_count",
            "lost_m69_operational_recovery_count",
        )
    ):
        raise ValueError("M70 anchored extension hides a regression")
    naive = report.get("strategies", {}).get("naive_multicandidate", {})
    if naive.get("preserves_uniform_recovered_sets") is not False or int(
        naive.get("lost_uniform_operational_recovery_count", 0)
    ) < 1:
        raise ValueError("M70 summary hides naive multicandidate loss")
    decision = report.get("decision", {})
    if (
        decision.get("production_default_changed") is not False
        or decision.get("candidate_promoted") is not False
        or decision.get("naive_multicandidate_rejected") is not True
    ):
        raise ValueError("M70 changed production or accepted the unsafe route")
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
            raise ValueError(f"unsafe M70 claim: {field}")
    if report.get("safety", {}).get(
        "current_three_video_regression_free_is_not_independent_validation"
    ) is not True:
        raise ValueError("M70 omits the independent-test disclaimer")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--residual-audit", type=Path, required=True)
    parser.add_argument("--ablation-report", type=Path, action="append", required=True)
    parser.add_argument("--anchored-report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; summaries are immutable")
    report = build_summary(
        residual_audit_path=args.residual_audit,
        ablation_report_paths=args.ablation_report,
        anchored_report_paths=args.anchored_report,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "status": report["status"]}))


if __name__ == "__main__":
    main()
