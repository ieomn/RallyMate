from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any

from rallymate_scoring.loop import run_minimum_scoring_loop
from rallymate_scoring.scoring_loop_report import write_scoring_loop_report
from rallymate_vision.validation import validate_run_artifacts


DEFAULT_VIDEO_ID = "850cb0006b406c7176eeda8d711cd065"
DEFAULT_BACKEND = "rtmpose-m-halpe26-256x192"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _feature_sample(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": record["event_id"],
        "feature_status": record["feature_status"],
        "scoring_status": record["scoring_status"],
        "quality_gate": record["quality_gate"],
        "features": record["features"],
    }


def _registry_indicator_scope(registry: dict[str, Any]) -> dict[str, Any]:
    indicators = registry.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("feasibility registry must declare at least one indicator")
    indicator_ids = [str(item["indicator_id"]) for item in indicators]
    if len(indicator_ids) != len(set(indicator_ids)):
        raise ValueError("feasibility registry indicator IDs must be unique")
    raw_events = registry.get("scope", {}).get("events", [])
    event_codes = sorted({str(value).split(".", 1)[0] for value in raw_events})
    return {
        "registry_version": str(registry.get("registry_version", "unknown")),
        "indicator_count": len(indicator_ids),
        "indicator_ids": sorted(indicator_ids),
        "event_codes": event_codes,
        "semantics": (
            "registry-declared measurable indicators; count does not imply "
            "event accuracy, feature accuracy, calibration, or score readiness"
        ),
    }


def _frame_window(path: Path) -> dict[str, Any]:
    first = last = None
    frame_count = 0
    formats: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        frame = record["frame"]
        first = first or frame
        last = frame
        frame_count += 1
        formats.update(
            pose["keypoint_format"]
            for pose in record.get("poses", [])
            if pose.get("keypoint_format")
        )
    if first is None or last is None:
        raise RuntimeError(f"pose frame artifact is empty: {path}")
    return {
        "frame_count": frame_count,
        "start_processed_index": int(first["processed_index"]),
        "end_processed_index_inclusive": int(last["processed_index"]),
        "start_ms": int(first["timestamp_ms"]),
        "end_ms": int(last["timestamp_ms"]),
        "native_keypoint_formats_observed": sorted(formats),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a feasibility-registry Pose wave without calibration"
    )
    parser.add_argument("--video-id", default=DEFAULT_VIDEO_ID)
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--frames-path", type=Path)
    parser.add_argument("--primary-timeline-path", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--native-keypoint-format")
    parser.add_argument("--native-keypoint-count", type=int)
    parser.add_argument("--model-sha256")
    parser.add_argument(
        "--scoring-reference-context",
        type=Path,
        help=(
            "Optional accepted/pending per-event target-direction context. "
            "It is reference evidence, not a grade or A-E threshold."
        ),
    )
    parser.add_argument(
        "--feasibility-registry",
        type=Path,
        help=(
            "Versioned feasibility registry; defaults to the existing "
            "metric-feasibility-pose-wave-v2.json path"
        ),
    )
    parser.add_argument("--materialize-validation-bundle", action="store_true")
    parser.add_argument(
        "--report-output",
        type=Path,
        default=Path("reports/fs09-pose-wave-v2-smoke.json"),
    )
    args = parser.parse_args()

    root = Path.cwd()
    frames_path = (args.frames_path or (
        root / "runs" / "pose-ab" / args.backend / args.video_id / "frames.jsonl"
    )).resolve()
    timeline_path = (args.primary_timeline_path or (
        root
        / "reports"
        / "pose-scoring-ab"
        / args.backend
        / args.video_id
        / "primary-player.jsonl"
    )).resolve()
    video_path = root / "FULL-TEST" / f"{args.video_id}.mp4"
    registry_path = (
        args.feasibility_registry
        or root / "metric-feasibility-pose-wave-v2.json"
    ).resolve()
    output_dir = (args.output_dir or (
        root / "reports" / "fs09-pose-wave-v2" / args.video_id
    )).resolve()
    frame_window = _frame_window(frames_path)
    observed_formats = frame_window["native_keypoint_formats_observed"]
    native_format = args.native_keypoint_format or (
        observed_formats[0] if len(observed_formats) == 1 else None
    )
    if native_format is None:
        raise RuntimeError(
            "native keypoint format is ambiguous; pass --native-keypoint-format"
        )
    observed_count = args.native_keypoint_count
    if observed_count is None:
        observed_count = {"coco17": 17, "halpe26": 26, "coco_wholebody133": 133}.get(
            native_format
        )
    if observed_count is None:
        raise RuntimeError("pass --native-keypoint-count for an unregistered topology")
    started = time.perf_counter()
    result = run_minimum_scoring_loop(
        frames_path=frames_path,
        primary_timeline_path=timeline_path,
        output_dir=output_dir,
        feasibility_registry_path=registry_path,
        source_id=f"{args.video_id}:{args.backend}:pose-wave-v2",
        video_id=args.video_id,
        pose_model={
            "backend": "rtmpose",
            "runtime": "pytorch",
            "profile": args.backend,
            "model_sha256": args.model_sha256,
            "native_keypoint_format": native_format,
            "native_keypoint_count": observed_count,
        },
        source_provenance={
            "video_path": str(video_path),
            "video_sha256": _sha256(video_path),
            "frames_path": str(frames_path),
            "frames_sha256": _sha256(frames_path),
            "primary_timeline_path": str(timeline_path),
            "primary_timeline_sha256": _sha256(timeline_path),
            "purpose": "real_video_registry_pose_wave_no_calibration",
            "frame_window": frame_window,
            "timeline_reuse_semantics": (
                "timeline_is_joined_by_processed_index; current_pose_artifact_is_used_for_features_and_diagnostics"
            ),
        },
        scoring_reference_context_path=(
            args.scoring_reference_context.resolve()
            if args.scoring_reference_context is not None
            else None
        ),
    )
    runtime_seconds = round(time.perf_counter() - started, 3)
    scoring_loop_report_path = output_dir / "scoring-loop-report.html"
    write_scoring_loop_report(result, scoring_loop_report_path)

    bundle_validation: dict[str, Any] = {
        "status": "not_requested",
        "semantics": "scoring artifacts were not materialized as a standalone run bundle",
    }
    if args.materialize_validation_bundle:
        shutil.copyfile(frames_path, output_dir / "frames.jsonl")
        shutil.copyfile(timeline_path, output_dir / "primary-player.jsonl")
        (output_dir / "summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "status": "completed_scoring_input_window",
                    "processing": {"processed_frames": frame_window["frame_count"]},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        bundle_validation = validate_run_artifacts(output_dir)

    indicator_scope = _registry_indicator_scope(result["feasibility"])
    registry_ids = set(indicator_scope["indicator_ids"])
    output_ids = {item["indicator_id"] for item in result["indicator_records"]}
    if output_ids != registry_ids:
        raise RuntimeError("scoring output does not cover every registry indicator")
    if any(item["grade"] is not None for item in result["scores"]):
        raise RuntimeError("un-calibrated smoke run emitted a grade")
    if any(item.get("threshold_version") is not None for item in result["scores"]):
        raise RuntimeError("un-calibrated smoke run emitted a threshold version")

    by_indicator: dict[str, dict[str, Any]] = {}
    for indicator_id in sorted(registry_ids):
        records = [
            item
            for item in result["indicator_records"]
            if item["indicator_id"] == indicator_id
        ]
        by_indicator[indicator_id] = {
            "instances": len(records),
            "feature_status_counts": dict(
                Counter(item["feature_status"] for item in records)
            ),
            "scoring_status_counts": dict(
                Counter(item["scoring_status"] for item in records)
            ),
            "quality_gate_status_counts": dict(
                Counter(item["quality_gate"]["status"] for item in records)
            ),
            "sample": _feature_sample(
                next(
                    (item for item in records if item["feature_status"] == "measured"),
                    records[0],
                )
            ),
        }

    report = {
        "schema_version": "1.0.0",
        "report_version": "fs09-pose-wave-v2-smoke/1.0.0",
        "status": "passed_real_video_features_no_calibration",
        "runtime_seconds": runtime_seconds,
        "video_id": args.video_id,
        "backend": args.backend,
        "input": {
            "video": str(video_path),
            "frames": str(frames_path),
            "primary_timeline": str(timeline_path),
            "feasibility_registry": str(registry_path),
            "scoring_reference_context": (
                str(args.scoring_reference_context.resolve())
                if args.scoring_reference_context is not None
                else None
            ),
            "frame_window": frame_window,
            "native_keypoint_format": native_format,
            "native_keypoint_count": observed_count,
        },
        "summary": result["summary"],
        "indicator_scope": indicator_scope,
        "bundle_validation": bundle_validation,
        "indicator_results": by_indicator,
        "assertions": {
            "all_registry_indicators_present": output_ids == registry_ids,
            "target_indicator_count": indicator_scope["indicator_count"],
            "all_grades_null": all(item["grade"] is None for item in result["scores"]),
            "all_score_statuses_safe_without_calibration": all(
                item["status"] in {"calibration_required", "unavailable"}
                for item in result["scores"]
            ),
            "grade_count": sum(item["grade"] is not None for item in result["scores"]),
            "all_threshold_versions_null": all(
                item.get("threshold_version") is None for item in result["scores"]
            ),
            "fake_thresholds_generated": False,
            "semantic_limit": (
                "Pose features are measurements, not proven event accuracy, "
                "feature accuracy, calibration, or score readiness"
            ),
        },
        "artifacts": {
            "directory": str(output_dir),
            "scoring_loop_report_html": str(scoring_loop_report_path),
            **{
                name: str(output_dir / filename)
                for name, filename in result["summary"]["artifacts"].items()
            },
        },
    }
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "runtime_seconds": runtime_seconds,
                "report": str(args.report_output),
                "score_status_counts": result["summary"]["score_status_counts"],
                "indicator_count": indicator_scope["indicator_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
