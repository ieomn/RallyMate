#!/usr/bin/env python3
"""Build a fixed-boundary pose-profile routing audit.

The report deliberately separates each model's self-segmented coverage from
coverage on one common set of event boundaries.  It is a model-routing safety
artifact, not an accuracy evaluation and not a calibration or scoring asset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0.0"
REPORT_VERSION = "pose-profile-routing-audit-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _summary_counts(summary: dict[str, Any]) -> dict[str, Any]:
    rows = summary.get("indicator_feature_validity")
    if not isinstance(rows, dict) or not rows:
        raise ValueError("scoring-loop summary has no indicator_feature_validity")
    measured = sum(int(row.get("valid", 0)) for row in rows.values())
    total = sum(int(row.get("total", 0)) for row in rows.values())
    score_counts = {
        str(key): int(value)
        for key, value in summary.get("score_status_counts", {}).items()
    }
    if sum(score_counts.values()) != total:
        raise ValueError("scoring-loop score counts do not match indicator total")
    grade_counts = summary.get("grade_counts", {})
    if not isinstance(grade_counts, dict) or sum(int(v) for v in grade_counts.values()):
        raise ValueError("uncalibrated routing audit must not contain grades")
    model = summary.get("model_versions", {})
    provenance = summary.get("provenance", {})
    return {
        "event_count": sum(int(v) for v in summary.get("event_counts", {}).values()),
        "indicator_event_count": total,
        "measured_indicator_event_count": measured,
        "unavailable_indicator_event_count": total - measured,
        "score_status_counts": score_counts,
        "grade_count": 0,
        "pose_model_sha256": str(model.get("pose_model_sha256", "")).upper(),
        "native_keypoint_format": str(model.get("native_keypoint_format", "")),
        "native_keypoint_count": int(model.get("native_keypoint_count", 0)),
        "registry_version": str(model.get("feasibility_registry", "")),
        "registry_sha256": str(provenance.get("feasibility_registry_sha256", "")).upper(),
        "video_sha256": str(provenance.get("video_sha256", "")).upper(),
    }


def _fixed_counts(
    report: dict[str, Any], *, current_primary: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    assertions = report.get("assertions", {})
    semantics = report.get("comparison_semantics", {})
    if assertions.get("accuracy_claim") is not False or semantics.get("accuracy_claim") is not False:
        raise ValueError("fixed-boundary input must explicitly reject accuracy claims")
    if assertions.get("grade_generated") is not False:
        raise ValueError("fixed-boundary input must not contain grades")
    order = report.get("model_order", [])
    if len(order) != 2 or order[0] != current_primary:
        raise ValueError("fixed-boundary input must put current primary first")
    candidate = str(order[1])
    models = report.get("models", {})
    if set(models) != set(order):
        raise ValueError("fixed-boundary model set does not match model_order")
    indicator_states: dict[str, int] = {
        "both_valid": 0,
        "model_a_only": 0,
        "model_b_only": 0,
        "neither_valid": 0,
    }
    for event in report.get("events", []):
        for indicator in event.get("indicators", []):
            state = str(indicator.get("paired_validity", {}).get("state"))
            if state not in indicator_states:
                raise ValueError(f"unexpected paired indicator state: {state}")
            indicator_states[state] += 1
    feature_states: dict[str, int] = {
        "both_valid": 0,
        "model_a_only": 0,
        "model_b_only": 0,
        "neither_valid": 0,
    }
    for pair in report.get("feature_pairs", []):
        state = str(pair.get("comparison", {}).get("validity_state"))
        if state not in feature_states:
            raise ValueError(f"unexpected paired feature state: {state}")
        feature_states[state] += 1
    common = {
        "event_count": int(report["boundary_source"]["event_count"]),
        "indicator_count": int(report["registry_source"]["indicator_count"]),
        "registry_version": str(report["registry_source"]["registry_version"]),
        "registry_sha256": str(report["registry_source"]["sha256"]).upper(),
        "boundary_sha256": str(report["boundary_source"]["sha256"]).upper(),
        "truth_status": str(report["boundary_source"]["truth_status"]),
        "indicator_validity_states": indicator_states,
        "feature_validity_states": feature_states,
        "feature_pair_count": len(report.get("feature_pairs", [])),
    }
    return candidate, dict(models[current_primary]), {**common, "candidate_model": dict(models[candidate])}


def _event_agreement(report: dict[str, Any], *, current_primary: str, candidate: str) -> dict[str, Any]:
    semantics = report.get("semantics", {})
    if semantics.get("accuracy_claim") is not False or semantics.get("ground_truth_provided") is not False:
        raise ValueError("event disagreement input must explicitly reject truth/accuracy")
    inputs = report.get("inputs", {})
    if inputs.get("left", {}).get("label") != current_primary:
        raise ValueError("event disagreement left model is not current primary")
    if inputs.get("right", {}).get("label") != candidate:
        raise ValueError("event disagreement right model does not match candidate")
    rows = report.get("threshold_results", [])
    selected = next(
        (row for row in rows if abs(float(row.get("minimum_segment_iou", -1)) - 0.3) < 1e-9),
        None,
    )
    if selected is None:
        raise ValueError("event disagreement input has no IoU 0.3 sensitivity row")
    absolute = selected["mean_absolute_differences_ms"]
    return {
        "minimum_segment_iou": 0.3,
        "current_event_count": int(selected["left_event_count"]),
        "candidate_event_count": int(selected["right_event_count"]),
        "matched_count": int(selected["matched_count"]),
        "current_match_rate": float(selected["left_to_right_match_rate"]),
        "candidate_match_rate": float(selected["right_to_left_match_rate"]),
        "mean_segment_iou": float(selected["mean_segment_iou"]),
        "boundary_mean_absolute_difference_ms": {
            key: float(absolute[key])
            for key in (
                "start_difference_ms",
                "end_difference_ms",
                "center_difference_ms",
                "duration_difference_ms",
            )
        },
    }


def build_pose_profile_routing_audit(
    *,
    current_primary: str,
    summary_inputs: dict[str, tuple[Path, dict[str, Any]]],
    fixed_inputs: list[tuple[Path, dict[str, Any]]],
    event_inputs: dict[str, tuple[Path, dict[str, Any]]],
) -> dict[str, Any]:
    if current_primary not in summary_inputs:
        raise ValueError("current primary summary is missing")
    independent = {
        model: _summary_counts(payload) for model, (_, payload) in summary_inputs.items()
    }
    base = independent[current_primary]
    if not base["pose_model_sha256"] or not base["video_sha256"]:
        raise ValueError("current primary summary lacks model/video provenance")

    comparisons: dict[str, dict[str, Any]] = {}
    current_fixed_counts: set[tuple[int, int]] = set()
    audited_registry_hashes: set[str] = set()
    for path, payload in fixed_inputs:
        candidate, primary_fixed, fixed = _fixed_counts(
            payload, current_primary=current_primary
        )
        if candidate in comparisons:
            raise ValueError(f"duplicate fixed-boundary candidate: {candidate}")
        candidate_fixed = fixed.pop("candidate_model")
        for model_name, model_fixed in (
            (current_primary, primary_fixed),
            (candidate, candidate_fixed),
        ):
            if model_name not in independent:
                raise ValueError(f"missing independent summary for {model_name}")
            if str(model_fixed["model_sha256"]).upper() != independent[model_name]["pose_model_sha256"]:
                raise ValueError(f"model hash mismatch for {model_name}")
        if fixed["registry_version"] != base["registry_version"]:
            raise ValueError("fixed-boundary registry version does not match current summary")
        audited_registry_hashes.add(fixed["registry_sha256"])
        for model_name in (current_primary, candidate):
            declared_hash = independent[model_name]["registry_sha256"]
            if declared_hash and declared_hash != fixed["registry_sha256"]:
                raise ValueError(f"fixed-boundary registry hash does not match {model_name}")
        current_total = int(primary_fixed["indicator_event_pair_count"])
        current_measured = int(primary_fixed["measured_indicator_event_count"])
        current_fixed_counts.add((current_measured, current_total))
        candidate_total = int(candidate_fixed["indicator_event_pair_count"])
        candidate_measured = int(candidate_fixed["measured_indicator_event_count"])
        if current_total != candidate_total:
            raise ValueError("fixed-boundary indicator totals differ between models")
        event_path, event_payload = event_inputs[candidate]
        comparisons[candidate] = {
            "fixed_boundary_source": {
                "path": str(path.resolve()),
                "sha256": _sha256(path),
                **fixed,
            },
            "fixed_boundary_measurement": {
                "current_primary_measured": current_measured,
                "candidate_measured": candidate_measured,
                "indicator_event_count": current_total,
                "candidate_minus_current": candidate_measured - current_measured,
            },
            "self_segmented_measurement": {
                "current_primary_measured": base["measured_indicator_event_count"],
                "current_primary_total": base["indicator_event_count"],
                "candidate_measured": independent[candidate]["measured_indicator_event_count"],
                "candidate_total": independent[candidate]["indicator_event_count"],
                "candidate_self_segmented_minus_fixed": (
                    independent[candidate]["measured_indicator_event_count"] - candidate_measured
                ),
                "boundary_confounding_warning": True,
            },
            "event_candidate_agreement": {
                "path": str(event_path.resolve()),
                "sha256": _sha256(event_path),
                **_event_agreement(
                    event_payload,
                    current_primary=current_primary,
                    candidate=candidate,
                ),
            },
        }
    if set(comparisons) != set(summary_inputs) - {current_primary}:
        raise ValueError("fixed/event comparison candidates do not match summary candidates")
    if len(current_fixed_counts) != 1:
        raise ValueError("current primary fixed-boundary counts disagree across comparisons")
    if len(audited_registry_hashes) != 1:
        raise ValueError("fixed-boundary comparisons use different registries")
    audited_registry_sha256 = next(iter(audited_registry_hashes))
    for row in independent.values():
        if not row["registry_sha256"]:
            row["registry_sha256"] = audited_registry_sha256

    profile_rows: dict[str, Any] = {}
    for model, (path, _) in summary_inputs.items():
        role = "current_scoring_primary"
        if model != current_primary:
            fixed_delta = comparisons[model]["fixed_boundary_measurement"]["candidate_minus_current"]
            role = (
                "coverage_candidate_requires_ground_truth"
                if fixed_delta > 0
                else "supplemental_or_analysis_not_promoted"
            )
        profile_rows[model] = {
            "role": role,
            "summary_source": {
                "path": str(path.resolve()),
                "sha256": _sha256(path),
            },
            **independent[model],
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "coverage_audit_complete_ground_truth_required",
        "scope": {
            "video_sha256": base["video_sha256"],
            "registry_version": base["registry_version"],
            "registry_sha256": audited_registry_sha256,
            "indicator_count": len(
                summary_inputs[current_primary][1]["indicator_feature_validity"]
            ),
            "current_primary": current_primary,
        },
        "profiles": profile_rows,
        "comparisons": comparisons,
        "routing_decision": {
            "selected_scoring_primary": current_primary,
            "decision_status": "retain_current_primary_pending_manual_truth",
            "automatic_model_promotion": False,
            "per_feature_or_per_event_model_cherry_picking_allowed": False,
            "runtime_fallback_to_another_pose_model_allowed": False,
            "reasons": [
                "same_boundary_measurement_coverage_does_not_improve",
                "self_segmented_coverage_is_confounding_by_event_boundaries",
                "pose_and_event_ground_truth_missing",
                "feature_error_by_profile_not_evaluated",
                "coach_calibration_and_independent_test_missing",
            ],
            "next_evidence_required": [
                "manual_event_boundaries_for_event_f1_iou_and_boundary_mae",
                "manual_keypoint_corrections_for_per_profile_feature_mae_p95_bias",
                "accepted_fixed_camera_view_evidence",
                "coach_calibration_and_sealed_independent_test",
            ],
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "fixed_boundary_coverage_is_accuracy": False,
            "self_segmented_counts_used_for_routing": False,
        },
    }


def validate_pose_profile_routing_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION or report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported pose-profile routing audit version")
    if report.get("status") != "coverage_audit_complete_ground_truth_required":
        raise ValueError("pose-profile routing audit is not complete")
    safety = report.get("safety", {})
    expected_false = (
        "accuracy_claim",
        "ground_truth_provided",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
        "fixed_boundary_coverage_is_accuracy",
        "self_segmented_counts_used_for_routing",
    )
    if any(safety.get(key) is not False for key in expected_false):
        raise ValueError("pose-profile routing audit contains an unsafe claim")
    decision = report.get("routing_decision", {})
    if decision.get("automatic_model_promotion") is not False:
        raise ValueError("routing audit must not automatically promote a model")
    if decision.get("per_feature_or_per_event_model_cherry_picking_allowed") is not False:
        raise ValueError("routing audit must prohibit model cherry-picking")
    primary = report.get("scope", {}).get("current_primary")
    if decision.get("selected_scoring_primary") != primary:
        raise ValueError("selected primary does not match audited current primary")
    profiles = report.get("profiles", {})
    comparisons = report.get("comparisons", {})
    if not isinstance(profiles, dict) or len(profiles) < 2 or primary not in profiles:
        raise ValueError("routing audit must contain current and candidate profiles")
    if set(comparisons) != set(profiles) - {primary}:
        raise ValueError("routing comparison set does not match candidate profiles")
    for candidate, comparison in comparisons.items():
        fixed = comparison.get("fixed_boundary_measurement", {})
        total = int(fixed.get("indicator_event_count", -1))
        current = int(fixed.get("current_primary_measured", -1))
        candidate_value = int(fixed.get("candidate_measured", -1))
        if total <= 0 or not (0 <= current <= total and 0 <= candidate_value <= total):
            raise ValueError(f"invalid fixed-boundary counts for {candidate}")
        if int(fixed.get("candidate_minus_current", 0)) != candidate_value - current:
            raise ValueError(f"invalid fixed-boundary delta for {candidate}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-primary", required=True)
    parser.add_argument("--current-summary", type=Path, required=True)
    parser.add_argument("--halpe384-summary", type=Path, required=True)
    parser.add_argument("--wholebody-summary", type=Path, required=True)
    parser.add_argument("--halpe384-fixed", type=Path, required=True)
    parser.add_argument("--wholebody-fixed", type=Path, required=True)
    parser.add_argument("--halpe384-events", type=Path, required=True)
    parser.add_argument("--wholebody-events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    halpe384 = "rtmpose-m-halpe26-384x288"
    wholebody = "rtmpose-m-wholebody133-256x192"
    summary_paths = {
        args.current_primary: args.current_summary,
        halpe384: args.halpe384_summary,
        wholebody: args.wholebody_summary,
    }
    fixed_paths = [args.halpe384_fixed, args.wholebody_fixed]
    event_paths = {
        halpe384: args.halpe384_events,
        wholebody: args.wholebody_events,
    }
    report = build_pose_profile_routing_audit(
        current_primary=args.current_primary,
        summary_inputs={path_id: (path, _load(path)) for path_id, path in summary_paths.items()},
        fixed_inputs=[(path, _load(path)) for path in fixed_paths],
        event_inputs={path_id: (path, _load(path)) for path_id, path in event_paths.items()},
    )
    validate_pose_profile_routing_audit(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output.resolve()),
                "selected_scoring_primary": report["routing_decision"]["selected_scoring_primary"],
                "accuracy_claim": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
