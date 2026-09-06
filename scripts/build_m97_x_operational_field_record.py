#!/usr/bin/env python3
"""Seal M97 operational fields, provenance, and artifact hashes without overwrite."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.pose_x_operational import (
    PoseXOperationalError,
    VIDEO_IDS,
    build_multivideo_summary,
    latency_distribution,
    load_x_operational_protocol,
    sha256_file,
    validate_multivideo_summary,
    validate_per_video_report,
)


RECORD_VERSION = "m97-x-operational-field-change-record-v1.0.0"
DEFAULT_REPORT_ROOT = Path("reports/measurement-recovery-m97")
DEFAULT_OUTPUT = DEFAULT_REPORT_ROOT / "field-change-record.json"
M96_BINDINGS = {
    "reports/m96-rtmpose-same-frame-diagnostic/diagnostic-report.json": (
        "B6361C909CF483C06B19B7EDA62DB1221D29BE1004E8F4FFF046FE7C5A0CCF71"
    ),
    "reports/m96-rtmpose-same-frame-diagnostic/summary.md": (
        "D39F696E05F5265B405B6063C0831205760B6CD0A491AFEDF7161C94B59419D3"
    ),
    "reports/m96-rtmpose-same-frame-diagnostic/field-change-record.json": (
        "EBAA52D305B963719093BE2663240C11F32146D1AEDA347E3D2977A0225ED1DD"
    ),
}


def _inside(root: Path, path: str | Path, label: str) -> Path:
    value = Path(path)
    resolved = value.resolve() if value.is_absolute() else (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise PoseXOperationalError(f"{label} must stay inside the workspace")
    return resolved


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PoseXOperationalError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PoseXOperationalError(f"{label} must be an object")
    return value


def _load_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise PoseXOperationalError(f"{label} is not valid JSONL") from exc
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise PoseXOperationalError(f"{label} is empty or malformed")
    return rows


def _artifact(root: Path, path: Path, roles: list[str]) -> dict[str, Any]:
    resolved = _inside(root, path, "artifact")
    if not resolved.is_file():
        raise PoseXOperationalError(f"artifact is missing: {resolved}")
    return {
        "relative_path": resolved.relative_to(root).as_posix(),
        "roles": sorted(set(roles)),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def _environment() -> dict[str, Any]:
    import cv2
    import torch

    packages = {}
    for name in ("mmpose", "mmcv", "mmengine", "numpy"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    driver = None
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=driver_version",
                "--format=csv,noheader",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        driver = completed.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    cuda_available = bool(torch.cuda.is_available())
    return {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "opencv": cv2.__version__,
        "packages": packages,
        "cuda_available": cuda_available,
        "gpu": torch.cuda.get_device_name(0) if cuda_available else None,
        "gpu_total_memory_bytes": (
            torch.cuda.get_device_properties(0).total_memory if cuda_available else None
        ),
        "nvidia_driver": driver,
    }


def _m96_speed_context(root: Path) -> dict[str, Any]:
    for relative, expected in M96_BINDINGS.items():
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise PoseXOperationalError(f"frozen M96 binding drifted: {relative}")
    report = _load_json(
        root / "reports/m96-rtmpose-same-frame-diagnostic/diagnostic-report.json",
        "M96 diagnostic report",
    )
    return {
        "scope": {
            "sample_count": len(report["sample_manifest"]),
            "same_decoded_frames_and_frozen_ROIs_across_models": True,
            "execution_profile": "realtime",
            "flip_test": False,
        },
        "latency_ms_per_single_roi_call": {
            family: report["models"][family]["summary"][
                "latency_ms_per_single_roi_call"
            ]
            for family in ("M", "L", "X")
        },
        "interpretation": (
            "M96 is the direct same-frame M/L/X realtime-no-flip comparison; "
            "M97 X analysis-flip latency is a different execution scope and must "
            "not be ratioed against it"
        ),
    }


def build_field_change_record(
    *,
    workspace: str | Path,
    output_path: str | Path,
    focused_test_count: int,
    focused_test_seconds: float,
    focused_tests_passed: bool,
    python_compile_passed: bool,
    gpu_runs_passed: bool,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    output = _inside(root, output_path, "field record output")
    if output.exists():
        raise PoseXOperationalError(f"field record is immutable and exists: {output}")
    if not all((focused_tests_passed, python_compile_passed, gpu_runs_passed)):
        raise PoseXOperationalError("all verification gates must pass before sealing")
    if focused_test_count < 1 or focused_test_seconds < 0:
        raise PoseXOperationalError("focused test evidence is invalid")

    verified = load_x_operational_protocol(root)
    summary_path = root / "reports/measurement-recovery-m97/summary/report.json"
    summary_markdown_path = root / "reports/measurement-recovery-m97/summary/summary.md"
    summary = _load_json(summary_path, "M97 summary")
    validate_multivideo_summary(summary)
    report_paths = [
        root
        / "reports/measurement-recovery-m97/rtmpose-x-384-context-extension-v1"
        / video_id
        / "report.json"
        for video_id in VIDEO_IDS
    ]
    per_video = [_load_json(path, f"M97 {path.parent.name} report") for path in report_paths]
    for report in per_video:
        validate_per_video_report(report)
    rebuilt = build_multivideo_summary(
        verified_protocol=verified,
        report_paths=report_paths,
    )
    if {
        key: value for key, value in rebuilt.items() if key != "generated_at"
    } != {
        key: value for key, value in summary.items() if key != "generated_at"
    }:
        raise PoseXOperationalError("saved M97 summary differs from raw report reconstruction")

    observation_rows: dict[str, list[dict[str, Any]]] = {}
    all_timed: list[float] = []
    timed_pose_outputs: list[float] = []
    warm_timed: list[float] = []
    cold_starts = []
    fast_empty_returns = []
    for report in per_video:
        video_id = str(report["video_id"])
        path = Path(report["artifacts"]["x_pose_observations"]["path"])
        rows = _load_jsonl(path, f"M97 {video_id} observations")
        observation_rows[video_id] = rows
        timed_rows = [row for row in rows if row.get("latency_ms") is not None]
        values = [float(row["latency_ms"]) for row in timed_rows]
        if latency_distribution(values) != report["inference"]["latency_ms"]:
            raise PoseXOperationalError("per-video latency no longer matches observations")
        all_timed.extend(values)
        warm_timed.extend(values[1:])
        timed_pose_outputs.extend(
            float(row["latency_ms"])
            for row in timed_rows
            if row.get("pose_output_produced") is True
        )
        cold_starts.append(
            {
                "video_id": video_id,
                "processed_index": int(timed_rows[0]["processed_index"]),
                "latency_ms": float(timed_rows[0]["latency_ms"]),
            }
        )
        fast_empty_returns.extend(
            {
                "video_id": video_id,
                "processed_index": int(row["processed_index"]),
                "reason": str(row["reason"]),
                "latency_ms": float(row["latency_ms"]),
            }
            for row in timed_rows
            if row.get("pose_output_produced") is not True
        )
    if latency_distribution(all_timed) != summary["inference"]["latency_ms"]:
        raise PoseXOperationalError("summary latency no longer matches observations")

    roles: dict[Path, list[str]] = defaultdict(list)

    def add(path: str | Path, role: str) -> None:
        resolved = _inside(root, path, role)
        roles[resolved].append(role)

    for path, role in (
        ("models/rtmpose/m97-x-operational-protocol.json", "immutable M97 protocol"),
        ("src/rallymate_evaluation/pose_x_operational.py", "M97 validation and summary implementation"),
        ("scripts/run_m97_x_pose_operational_extension.py", "M97 X GPU runner"),
        ("scripts/build_m97_x_pose_operational_summary.py", "M97 summary builder"),
        ("scripts/build_m97_x_operational_field_record.py", "M97 field record builder"),
        ("tests/test_m97_x_pose_operational.py", "M97 protocol and per-video contract tests"),
        ("tests/test_m97_x_pose_operational_summary.py", "M97 real-output summary tests"),
        (summary_path, "immutable M97 machine-readable summary"),
        (summary_markdown_path, "M97 human-readable result summary"),
    ):
        add(path, role)
    add(verified["production_default"]["registry"]["path"], "unchanged production registry")
    add(verified["candidate"]["registry"]["path"], "X candidate provenance registry")
    add(verified["candidate"]["checkpoint"]["path"], "X checkpoint")
    add(verified["candidate"]["config"]["path"], "X runtime config")
    for label, binding in verified["historical_summaries"].items():
        add(binding["path"], f"frozen {label} summary")
    for item in verified["videos"]:
        video_id = item["video_id"]
        add(item["video"]["path"], f"development video {video_id}")
        add(item["m70_ablation_report"]["path"], f"M70 frame/context scope {video_id}")
        add(item["m70_anchored_report"]["path"], f"M70 router baseline {video_id}")
        add(item["m71_l_report"]["path"], f"M71 L comparator {video_id}")
    for path, report in zip(report_paths, per_video):
        add(path, f"M97 per-video report {report['video_id']}")
        for name, binding in report["sources"].items():
            add(binding["path"], f"M97 {report['video_id']} source {name}")
        for name, binding in report["artifacts"].items():
            add(binding["path"], f"M97 {report['video_id']} output {name}")
    for relative in M96_BINDINGS:
        add(relative, "frozen related M96 same-frame diagnostic")
    artifacts = [
        _artifact(root, path, roles[path])
        for path in sorted(roles, key=lambda item: item.relative_to(root).as_posix())
    ]

    record = {
        "schema_version": "1.0.0",
        "record_version": RECORD_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "m97_x_operational_result_sealed_without_promotion",
        "purpose": (
            "retain the exact M97 258-frame X384 operational result, source hashes, "
            "reference-set additions/losses, speed scope, and claim boundaries"
        ),
        "protocol_binding": verified["protocol"],
        "summary_binding": next(
            item for item in artifacts if item["relative_path"] == summary_path.relative_to(root).as_posix()
        ),
        "human_summary_binding": next(
            item
            for item in artifacts
            if item["relative_path"] == summary_markdown_path.relative_to(root).as_posix()
        ),
        "preserved_state": {
            "production_default_preset": verified["production_default"]["preset_id"],
            "production_registry_sha256": verified["production_default"]["registry"]["sha256"],
            "production_default_changed": False,
            "candidate_promoted": False,
            "sealed_holdout": {
                **verified["holdout_guard"],
                "declared_hash_reverified_in_M97": False,
            },
            "ground_truth_used": False,
        },
        "execution_environment": _environment(),
        "scope": summary["scope"],
        "comparability": summary["comparability"],
        "result": {
            "status": summary["status"],
            "baseline_M68": summary["baseline_M68"],
            "baseline_M70": summary["baseline_M70"],
            "baseline_M71": summary["baseline_M71"],
            "X_projection": summary["X_projection"],
            "comparison_to_M70": summary["comparison_to_M70"],
            "comparison_to_M71": summary["comparison_to_M71"],
            "by_video": summary["by_video"],
        },
        "speed_diagnostics": {
            "M97_X_analysis_flip_all_timed_estimator_calls": latency_distribution(all_timed),
            "M97_X_analysis_flip_pose_output_calls": latency_distribution(timed_pose_outputs),
            "M97_X_analysis_flip_after_first_timed_call_per_process": latency_distribution(warm_timed),
            "cold_first_calls": cold_starts,
            "timed_empty_returns": fast_empty_returns,
            "source_detection_unavailable_count": sum(
                int(report["inference"]["reason_counts"].get("selected_track_or_detection_unavailable", 0))
                for report in per_video
            ),
            "M96_direct_same_frame_context": _m96_speed_context(root),
            "cross_scope_speed_ratio_allowed": False,
        },
        "field_semantics": summary["field_semantics"],
        "verification": {
            "focused_tests": {
                "passed": True,
                "test_count": focused_test_count,
                "unittest_seconds": round(float(focused_test_seconds), 6),
                "command": (
                    "$env:PYTHONPATH='src'; runtime/rtmpose/.venv/Scripts/python.exe "
                    "-m unittest tests.test_m97_x_pose_operational "
                    "tests.test_m97_x_pose_operational_summary "
                    "tests.test_m71_larger_pose_model_summary "
                    "tests.test_m71_larger_pose_model_extension "
                    "tests.test_m70_pose_profile_ablation_summary "
                    "tests.test_pose_observability_router -v"
                ),
            },
            "python_compile_passed": True,
            "real_local_GPU_runs_passed": True,
            "per_video_GPU_run_count": 3,
            "summary_rebuilt_from_bound_per_video_reports": True,
            "recovery_set_arithmetic_revalidated": True,
            "latency_recomputed_from_raw_observations": True,
            "current_input_hashes_reverified": True,
            "sealed_holdout_hash_recomputed": False,
        },
        "artifacts": artifacts,
        "claims": {
            "RallyMate_accuracy_measured": False,
            "RallyMate_accuracy_improved": False,
            "confidence_is_accuracy": False,
            "operational_recovery_is_accuracy": False,
            "candidate_promoted": False,
            "production_default_changed": False,
            "F3_or_F4_promoted": False,
            "grade_or_threshold_generated": False,
        },
        "completed": [
            "X384_replayed_on_exact_M71_258_frame_scope",
            "per_frame_M71_ROI_and_context_identity_verified",
            "strict_required_joint_superset_no_regression_routing_recomputed",
            "fixed_boundary_feature_complete_recomputed",
            "preserved_source_gate_operational_measured_recomputed",
            "M70_and_M71_recovery_set_additions_and_losses_recorded",
            "X_analysis_flip_latency_recorded_on_local_GPU",
        ],
        "not_completed": [
            "human_Halpe26_ground_truth",
            "ground_truth_keypoint_accuracy_comparison",
            "per_view_feature_error",
            "preregistered_independent_video_release_validation",
            "direct_M_and_L_latency_rerun_on_exact_M97_258_frame_scope",
            "candidate_promotion",
            "production_default_change",
        ],
        "self_hash_note": "record SHA-256 is reported externally because a file cannot contain its own hash",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--focused-test-count", type=int, required=True)
    parser.add_argument("--focused-test-seconds", type=float, required=True)
    parser.add_argument("--focused-tests-passed", action="store_true")
    parser.add_argument("--python-compile-passed", action="store_true")
    parser.add_argument("--gpu-runs-passed", action="store_true")
    args = parser.parse_args()
    record = build_field_change_record(
        workspace=args.root,
        output_path=args.output,
        focused_test_count=args.focused_test_count,
        focused_test_seconds=args.focused_test_seconds,
        focused_tests_passed=args.focused_tests_passed,
        python_compile_passed=args.python_compile_passed,
        gpu_runs_passed=args.gpu_runs_passed,
    )
    output = _inside(args.root.resolve(), args.output, "field record output")
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
