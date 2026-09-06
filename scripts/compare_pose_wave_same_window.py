#!/usr/bin/env python3
"""Compare two uncalibrated pose-wave reports over the exact same frame window."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _indicator_row(report: dict, indicator_id: str) -> dict:
    item = report["indicator_results"][indicator_id]
    return {
        "instances": item["instances"],
        "feature_status_counts": item["feature_status_counts"],
        "feature_valid": item["feature_status_counts"].get("measured", 0),
        "feature_total": item["instances"],
        "scoring_status_counts": item["scoring_status_counts"],
        "quality_gate_status_counts": item["quality_gate_status_counts"],
    }


def _event_quality_flags(report: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    events_path = Path(report["artifacts"]["events_jsonl"])
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        for flag in json.loads(line).get("quality_flags", []):
            counts[flag] = counts.get(flag, 0) + 1
    return dict(sorted(counts.items()))


def _score_reason_counts(report: dict) -> dict[str, int]:
    """Aggregate event-indicator reason codes from the embedded loop summary."""

    counts: Counter[str] = Counter()
    validity = report.get("summary", {}).get("indicator_feature_validity", {})
    if not isinstance(validity, dict):
        return {}
    for item in validity.values():
        if not isinstance(item, dict):
            continue
        reason_counts = item.get("blocking_reason_code_counts", {})
        if not isinstance(reason_counts, dict):
            continue
        for reason, count in reason_counts.items():
            if isinstance(reason, str) and reason and isinstance(count, int):
                counts[reason] += count
    return dict(sorted(counts.items()))


def _shared_indicator_ids(reports: dict[str, dict]) -> list[str]:
    indicator_sets = {
        name: set(report.get("indicator_results", {}))
        for name, report in reports.items()
    }
    if any(not values for values in indicator_sets.values()):
        raise RuntimeError("input report has no indicator results")
    distinct = {frozenset(values) for values in indicator_sets.values()}
    if len(distinct) != 1:
        details = ", ".join(
            f"{name}={len(values)}" for name, values in sorted(indicator_sets.items())
        )
        raise RuntimeError(
            "reports do not cover the same registry indicator set: " + details
        )
    indicator_ids = sorted(next(iter(indicator_sets.values())))
    for name, report in reports.items():
        declared_count = report.get("indicator_scope", {}).get("indicator_count")
        if declared_count is None:
            declared_count = report.get("assertions", {}).get("target_indicator_count")
        if declared_count is not None and int(declared_count) != len(indicator_ids):
            raise RuntimeError(
                f"{name} report declares {declared_count} indicators but contains "
                f"{len(indicator_ids)}"
            )
    return indicator_ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--halpe", type=Path, required=True)
    parser.add_argument("--wholebody", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = {"halpe26": _load(args.halpe), "wholebody133": _load(args.wholebody)}
    windows = {name: value["input"]["frame_window"] for name, value in reports.items()}
    comparable_fields = (
        "frame_count",
        "start_processed_index",
        "end_processed_index_inclusive",
        "start_ms",
        "end_ms",
    )
    same_window = all(
        windows["halpe26"][field] == windows["wholebody133"][field]
        for field in comparable_fields
    )
    timeline_hashes = {
        name: report["summary"]["provenance"]["primary_timeline_sha256"]
        for name, report in reports.items()
    }
    shared_timeline = len(set(timeline_hashes.values())) == 1
    if not same_window:
        raise RuntimeError("reports do not cover the same frame/time window")
    if not shared_timeline:
        raise RuntimeError("reports do not reuse the same primary-player timeline")
    indicator_ids = _shared_indicator_ids(reports)
    for report in reports.values():
        if not report["assertions"]["all_grades_null"]:
            raise RuntimeError("uncalibrated input report emitted a grade")
        if not report["assertions"]["all_score_statuses_safe_without_calibration"]:
            raise RuntimeError("input report emitted an unsafe score status")
        if not report["assertions"]["all_threshold_versions_null"]:
            raise RuntimeError("input report emitted an uncalibrated threshold version")
    payload = {
        "schema_version": "1.0.0",
        "report_version": "pose-wave-v2-same-window-comparison/1.0.0",
        "status": "passed_same_window_computable_no_calibration",
        "comparison_scope": {
            "video_id": reports["halpe26"]["video_id"],
            "same_frame_and_time_window": same_window,
            "frame_window": {
                field: windows["halpe26"][field] for field in comparable_fields
            },
            "shared_primary_timeline": shared_timeline,
            "primary_timeline_sha256": timeline_hashes["halpe26"],
            "timeline_join_key": "processed_index",
            "indicator_count": len(indicator_ids),
            "indicator_ids": indicator_ids,
            "accuracy_claim": False,
            "reason": (
                "event and feature differences are model-dependent observations; "
                "manual event boundaries and corrected keypoints are required for accuracy comparison"
            ),
        },
        "models": {
            name: {
                "backend": report["backend"],
                "native_keypoint_format": report["input"]["native_keypoint_format"],
                "native_keypoint_count": report["input"]["native_keypoint_count"],
                "model_sha256": report["summary"]["model_versions"]["pose_model_sha256"],
                "event_counts": report["summary"]["event_counts"],
                "score_status_counts": report["summary"]["score_status_counts"],
                "quality_gate_status_counts": report["summary"]["quality_gate_status_counts"],
                "event_quality_flag_counts": _event_quality_flags(report),
                "score_block_reason_code_counts": _score_reason_counts(report),
                "grade_count": report["assertions"]["grade_count"],
                "bundle_validation": report["bundle_validation"],
                "indicator_count": len(report["indicator_results"]),
                "report": str((args.halpe if name == "halpe26" else args.wholebody).resolve()),
            }
            for name, report in reports.items()
        },
        "indicators": {
            indicator_id: {
                name: _indicator_row(report, indicator_id)
                for name, report in reports.items()
            }
            for indicator_id in indicator_ids
        },
        "assertions": {
            "same_window": same_window,
            "shared_primary_timeline": shared_timeline,
            "same_indicator_set_in_both": all(
                set(report["indicator_results"]) == set(indicator_ids)
                for report in reports.values()
            ),
            "all_grades_null": all(
                report["assertions"]["all_grades_null"] for report in reports.values()
            ),
            "all_statuses_calibration_required_or_unavailable": all(
                report["assertions"]["all_score_statuses_safe_without_calibration"]
                for report in reports.values()
            ),
            "all_threshold_versions_null": all(
                report["assertions"]["all_threshold_versions_null"]
                for report in reports.values()
            ),
            "fake_thresholds_generated": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": payload["status"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
