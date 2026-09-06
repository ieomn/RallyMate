#!/usr/bin/env python3
"""Seal M96 same-frame diagnostic fields and artifact hashes without overwriting."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.pose_same_frame_diagnostic import (
    EXPECTED_FAMILIES,
    PoseSameFrameDiagnosticError,
    _comparative_orderings,
    compare_model_observations,
    load_diagnostic_registry,
    render_diagnostic_markdown,
    select_frozen_same_frame_samples,
    sha256_file,
    summarize_model_observations,
    validate_diagnostic_report,
)


RECORD_VERSION = "m96-same-frame-field-change-record-v1.0.0"
DEFAULT_REPORT = Path("reports/m96-rtmpose-same-frame-diagnostic/diagnostic-report.json")
DEFAULT_SUMMARY = Path("reports/m96-rtmpose-same-frame-diagnostic/summary.md")
DEFAULT_OUTPUT = Path("reports/m96-rtmpose-same-frame-diagnostic/field-change-record.json")


def _inside(root: Path, relative: str | Path, label: str) -> Path:
    value = Path(relative)
    resolved = value.resolve() if value.is_absolute() else (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise PoseSameFrameDiagnosticError(f"{label} must stay inside the workspace")
    return resolved


def _artifact(root: Path, path: str | Path, role: str) -> dict[str, Any]:
    resolved = _inside(root, path, role)
    if not resolved.is_file():
        raise PoseSameFrameDiagnosticError(f"{role} does not exist: {resolved}")
    return {
        "role": role,
        "relative_path": resolved.relative_to(root).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def _recompute_samples(verified: dict[str, Any]) -> list[dict[str, Any]]:
    sampling = verified["protocol"]["sampling"]
    return [
        sample
        for video in verified["videos"]
        for sample in select_frozen_same_frame_samples(
            video["frozen_frames"]["path"],
            video_id=video["video_id"],
            declared_frame_count=video["declared_frame_count"],
            windows_per_video=int(sampling["windows_per_video"]),
            frames_per_window=int(sampling["frames_per_window"]),
        )["samples"]
    ]


def _validate_report_against_current_sources(
    root: Path,
    report: dict[str, Any],
    summary_text: str,
) -> dict[str, Any]:
    verified = load_diagnostic_registry(root)
    samples = _recompute_samples(verified)
    validate_diagnostic_report(
        report,
        expected_sample_ids=[item["sample_id"] for item in samples],
    )
    if report["sample_manifest"] != samples:
        raise PoseSameFrameDiagnosticError("report sample fields drifted from frozen selection")
    if report.get("registry") != verified["registry"]:
        raise PoseSameFrameDiagnosticError("report registry binding drifted")
    if report.get("inputs") != verified["videos"]:
        raise PoseSameFrameDiagnosticError("report video or frozen-frame bindings drifted")
    if report.get("source_registries") != verified["source_registries"]:
        raise PoseSameFrameDiagnosticError("report source registry bindings drifted")
    if report["production_default"].get("preset_id") != verified["production_default"]["preset_id"]:
        raise PoseSameFrameDiagnosticError("report production preset drifted")
    if report["production_default"].get("registry") != verified["production_default"]["registry"]:
        raise PoseSameFrameDiagnosticError("report production registry binding drifted")

    confidence_thresholds = [
        float(value) for value in verified["protocol"]["metrics"]["confidence_thresholds"]
    ]
    min_confidence = float(
        verified["protocol"]["metrics"]["cross_model_min_confidence"]
    )
    model_observations = {}
    for model in verified["models"]:
        family = model["family"]
        stored = report["models"][family]
        if stored.get("checkpoint") != model["checkpoint"] or stored.get("config") != model["config"]:
            raise PoseSameFrameDiagnosticError(f"{family} model binding drifted")
        metadata = stored.get("metadata", {})
        if (
            metadata.get("model_sha256") != model["checkpoint"]["sha256"]
            or metadata.get("config_sha256") != model["config"]["sha256"]
            or metadata.get("input_size") != model["input_size_hw"]
            or metadata.get("profile") != verified["protocol"]["execution"]["profile"]
            or metadata.get("native_keypoint_format") != "halpe26"
            or metadata.get("native_keypoint_count") != 26
        ):
            raise PoseSameFrameDiagnosticError(f"{family} runtime metadata drifted")
        recomputed = summarize_model_observations(
            stored["observations"],
            confidence_thresholds=confidence_thresholds,
            temporal_min_confidence=min_confidence,
        )
        if stored.get("summary") != recomputed:
            raise PoseSameFrameDiagnosticError(f"{family} summary does not match raw observations")
        repeatability = stored.get("repeatability", {})
        if (
            repeatability.get("semantics")
            != "same_decoded_frame_same_frozen_ROI_repeatability_not_accuracy"
            or repeatability.get("anchor_count") != 3
            or repeatability.get("pose_presence_consistent_anchor_rate") is None
        ):
            raise PoseSameFrameDiagnosticError(f"{family} repeatability fields are incomplete")
        model_observations[family] = stored["observations"]
    recomputed_cross_model = compare_model_observations(
        model_observations,
        min_confidence=min_confidence,
        distance_thresholds=[
            float(value)
            for value in verified["protocol"]["metrics"]["normalized_distance_thresholds"]
        ],
    )
    if report.get("cross_model") != recomputed_cross_model:
        raise PoseSameFrameDiagnosticError("cross-model summary does not match raw observations")
    if report.get("comparative_orderings") != _comparative_orderings(report["models"], 0.5):
        raise PoseSameFrameDiagnosticError("comparative orderings do not match summaries")
    if summary_text != render_diagnostic_markdown(report):
        raise PoseSameFrameDiagnosticError("Markdown summary does not match report")
    return verified


def build_field_change_record(
    *,
    workspace: str | Path,
    report_path: str | Path = DEFAULT_REPORT,
    summary_path: str | Path = DEFAULT_SUMMARY,
    output_path: str | Path = DEFAULT_OUTPUT,
    focused_test_count: int,
    focused_test_seconds: float,
    focused_tests_passed: bool,
    python_compile_passed: bool,
    gpu_run_passed: bool,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    output = _inside(root, output_path, "field record output")
    if output.exists():
        raise PoseSameFrameDiagnosticError(
            f"field record is immutable and already exists: {output}"
        )
    report_file = _inside(root, report_path, "diagnostic report")
    summary_file = _inside(root, summary_path, "diagnostic summary")
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PoseSameFrameDiagnosticError("diagnostic report cannot be read") from exc
    try:
        summary_text = summary_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise PoseSameFrameDiagnosticError("diagnostic summary cannot be read") from exc
    verified = _validate_report_against_current_sources(root, report, summary_text)
    if not all((focused_tests_passed, python_compile_passed, gpu_run_passed)):
        raise PoseSameFrameDiagnosticError("all verification gates must pass before sealing")
    if focused_test_count < 1 or focused_test_seconds < 0:
        raise PoseSameFrameDiagnosticError("focused test evidence is invalid")

    artifact_paths: list[tuple[str | Path, str]] = [
        ("models/rtmpose/m96-same-frame-diagnostic.json", "M96 immutable protocol registry"),
        ("src/rallymate_evaluation/pose_same_frame_diagnostic.py", "diagnostic implementation"),
        ("scripts/run_m96_rtmpose_same_frame_diagnostic.py", "GPU runner"),
        ("scripts/build_m96_same_frame_field_record.py", "field record builder"),
        ("tests/test_pose_same_frame_diagnostic.py", "focused contract tests"),
        (report_file, "immutable raw diagnostic report"),
        (summary_file, "human-readable diagnostic summary"),
        (verified["production_default"]["registry"]["path"], "unchanged production registry"),
    ]
    artifact_paths.extend(
        (item["binding"]["path"], f"source registry: {item['role']}")
        for item in verified["source_registries"]
    )
    for model in verified["models"]:
        artifact_paths.extend(
            (
                (model["checkpoint"]["path"], f"{model['family']} checkpoint"),
                (model["config"]["path"], f"{model['family']} config"),
            )
        )
    for video in verified["videos"]:
        artifact_paths.extend(
            (
                (video["video"]["path"], f"development video {video['video_id']}"),
                (
                    video["frozen_frames"]["path"],
                    f"frozen detection frames {video['video_id']}",
                ),
            )
        )
    artifacts = [_artifact(root, path, role) for path, role in artifact_paths]
    artifact_relatives = [item["relative_path"] for item in artifacts]
    if len(artifact_relatives) != len(set(artifact_relatives)):
        raise PoseSameFrameDiagnosticError("field record artifact paths must be unique")

    observed = {}
    for family in EXPECTED_FAMILIES:
        model = report["models"][family]
        coverage = model["summary"]["coverage_and_confidence"]
        observed[family] = {
            "candidate_id": model["identity"]["candidate_id"],
            "input_size_hw": model["metadata"]["input_size"],
            "pose_return_rate": coverage["pose_return_rate"],
            "confidence_threshold_coverage_including_missing_poses": {
                key: value["rate_including_missing_poses"]
                for key, value in coverage["confidence_threshold_coverage"].items()
            },
            "latency_ms_per_single_roi_call": model["summary"][
                "latency_ms_per_single_roi_call"
            ],
            "temporal_normalized_coordinate_displacement": model["summary"][
                "temporal_continuity"
            ]["normalized_coordinate_displacement"],
            "same_input_repeat_normalized_coordinate_delta": model["repeatability"][
                "normalized_coordinate_delta_from_first_call"
            ],
            "cuda_steady_state_peak": model["cuda_steady_state_peak"],
        }

    record = {
        "schema_version": "1.0.0",
        "record_version": RECORD_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "same_frame_diagnostic_sealed_ground_truth_required",
        "purpose": "retain_exact_M96_fields_artifact_hashes_and_claim_boundaries",
        "report_binding": _artifact(root, report_file, "immutable raw diagnostic report"),
        "summary_binding": _artifact(root, summary_file, "human-readable diagnostic summary"),
        "protocol_binding": verified["registry"],
        "preserved_state": {
            "production_default_preset": verified["production_default"]["preset_id"],
            "production_registry_sha256": verified["production_default"]["registry"]["sha256"],
            "production_default_changed": False,
            "sealed_holdout_opened": False,
            "sealed_holdout_file_read_or_hashed": False,
            "ground_truth_used": False,
        },
        "sample_binding": {
            "development_video_count": len(verified["videos"]),
            "sample_count": len(report["sample_manifest"]),
            "sample_manifest_sha256": report["sample_manifest_sha256"],
            "windows_per_video": verified["protocol"]["sampling"]["windows_per_video"],
            "frames_per_window": verified["protocol"]["sampling"]["frames_per_window"],
            "same_decoded_frames_and_frozen_ROIs_across_models": True,
        },
        "field_semantics": {
            "pose_return_rate": "returned_pose_count_divided_by_identical_frozen_ROI_call_count",
            "confidence_threshold_coverage": "keypoints_at_or_above_threshold_divided_by_all_expected_26xROI_points_including_missing_poses",
            "temporal_normalized_coordinate_displacement": "consecutive_frame_displacement_divided_by_mean_expanded_ROI_diagonal_contains_real_motion_and_model_variation_not_jitter_truth",
            "same_input_repeat_delta": "same_decoded_frame_same_frozen_ROI_repeat_difference_not_accuracy",
            "cross_model_agreement": "coordinate_similarity_on_identical_inputs_can_include_shared_error_not_accuracy",
            "latency": "one_ROI_estimator_call_on_this_device_with_common_realtime_no_flip_profile_only",
        },
        "observed_snapshot": observed,
        "cross_model_snapshot": report["cross_model"],
        "comparative_orderings": report["comparative_orderings"],
        "verification": {
            "focused_tests": {
                "passed": True,
                "test_count": focused_test_count,
                "unittest_seconds": round(float(focused_test_seconds), 6),
                "command": (
                    "$env:PYTHONPATH='src'; "
                    ".\\.venv\\Scripts\\python.exe -m unittest "
                    "tests.test_pose_same_frame_diagnostic "
                    "tests.test_pose_shadow_smoke tests.test_pose_deployment_smoke_v2 -v"
                ),
            },
            "python_compile_passed": True,
            "real_local_GPU_run_passed": True,
            "report_recomputed_from_raw_observations": True,
            "sample_selection_recomputed_from_frozen_frames": True,
            "current_input_hashes_reverified": True,
            "summary_matches_report": True,
        },
        "artifacts": artifacts,
        "claims": {
            "RallyMate_accuracy_measured": False,
            "RallyMate_accuracy_improved": False,
            "confidence_is_accuracy": False,
            "model_agreement_is_accuracy": False,
            "candidate_promoted": False,
            "production_default_changed": False,
            "F3_or_F4_promoted": False,
            "grade_or_threshold_generated": False,
        },
        "not_completed": [
            "human_Halpe26_ground_truth",
            "ground_truth_keypoint_accuracy_comparison",
            "fine_tuning",
            "new_checkpoint_generation",
            "candidate_promotion",
            "production_default_change",
        ],
        "self_hash_note": "record SHA-256 is reported externally because a file cannot contain its own hash",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--focused-test-count", type=int, required=True)
    parser.add_argument("--focused-test-seconds", type=float, required=True)
    parser.add_argument("--focused-tests-passed", action="store_true")
    parser.add_argument("--python-compile-passed", action="store_true")
    parser.add_argument("--gpu-run-passed", action="store_true")
    args = parser.parse_args()
    record = build_field_change_record(
        workspace=args.root,
        report_path=args.report,
        summary_path=args.summary,
        output_path=args.output,
        focused_test_count=args.focused_test_count,
        focused_test_seconds=args.focused_test_seconds,
        focused_tests_passed=args.focused_tests_passed,
        python_compile_passed=args.python_compile_passed,
        gpu_run_passed=args.gpu_run_passed,
    )
    output = (args.root / args.output).resolve()
    print(
        json.dumps(
            {
                "status": record["status"],
                "output": str(output),
                "bytes": output.stat().st_size,
                "sha256": sha256_file(output),
                "artifact_count": len(record["artifacts"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
