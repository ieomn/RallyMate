#!/usr/bin/env python3
"""Audit score blockers without changing gates, grades, or thresholds."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_scoring.blocker_taxonomy import (
    EXACT_BLOCKER_SPECS,
    exact_typed_reason_support_flags,
    truth_requirement_for_flag,
    typed_reason_for_flag,
)

SCHEMA_VERSION = "1.0.0"
REPORT_VERSION = "scoring-blocker-audit-v1.0.0"

EXPECTED_TYPED_REASONS = {
    item["flag"]: item["reason_code"] for item in EXACT_BLOCKER_SPECS
}
TYPED_REASON_FLAGS = {
    reason: set(flags)
    for reason, flags in exact_typed_reason_support_flags().items()
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"score row {line_number} is not an object")
        rows.append(value)
    if not rows:
        raise ValueError("scores input is empty")
    return rows


def _typed_reason_for_flag(flag: str) -> str | None:
    return typed_reason_for_flag(flag)


def audit_scoring_blockers(
    *,
    scores_path: Path,
    summary_path: Path,
    score_rows: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = score_rows if score_rows is not None else _read_jsonl(scores_path)
    summary_payload = summary
    if summary_payload is None:
        summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary_payload, dict):
        raise ValueError("summary input is not an object")

    status_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    flag_records: Counter[str] = Counter()
    flag_events: dict[str, set[str]] = defaultdict(set)
    eligible_flag_records: Counter[str] = Counter()
    eligible_flag_events: dict[str, set[str]] = defaultdict(set)
    sole_flag_records: Counter[str] = Counter()
    combinations: Counter[tuple[str, ...]] = Counter()
    combination_events: dict[tuple[str, ...], set[str]] = defaultdict(set)
    indicator_counts: dict[str, Counter[str]] = defaultdict(Counter)
    reason_records: Counter[str] = Counter()
    typed_reason_support_records: Counter[str] = Counter()
    reason_mismatches: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()

    for row in rows:
        identity = (str(row.get("event_id")), str(row.get("indicator_id")))
        if identity in identities:
            raise ValueError(f"duplicate event/indicator score row: {identity}")
        identities.add(identity)
        status = str(row.get("status"))
        if status not in {"scored", "calibration_required", "unavailable"}:
            raise ValueError(f"unsupported score status: {status}")
        if status == "scored" or row.get("grade") is not None or row.get("threshold_version") is not None:
            raise ValueError("uncalibrated blocker audit cannot contain scored/grade/threshold")
        status_counts[status] += 1
        indicator_counts[identity[1]][status] += 1
        features = row.get("feature", {}).get("items", [])
        if not isinstance(features, list) or not features:
            raise ValueError(f"score row lacks feature items: {identity}")
        features_complete = all(item.get("valid") is True for item in features)
        gate = row.get("quality_gate")
        if not isinstance(gate, dict):
            raise ValueError(f"score row lacks quality_gate: {identity}")
        hard_fail = gate.get("hard_fail") is True
        block_flags = tuple(sorted(str(flag) for flag in gate.get("scoring_block_flags", [])))
        scoring_allowed = gate.get("scoring_allowed") is True
        if scoring_allowed == bool(block_flags or hard_fail):
            raise ValueError(f"quality gate scoring_allowed is inconsistent: {identity}")
        if status == "calibration_required":
            category = "calibration_required_complete_features_no_score_block"
            if not features_complete or hard_fail or block_flags:
                raise ValueError(f"calibration_required row is not gate-clean: {identity}")
        elif hard_fail:
            category = "unavailable_hard_fail"
        elif features_complete and block_flags:
            category = "unavailable_complete_features_score_only"
        elif not features_complete and block_flags:
            category = "unavailable_feature_incomplete_and_score_blocked"
        elif not features_complete:
            category = "unavailable_feature_incomplete_without_score_block"
        else:
            raise ValueError(f"unavailable row has no observable blocker: {identity}")
        category_counts[category] += 1
        reason_codes = set(str(reason) for reason in row.get("reason_codes", []))
        reason_records.update(reason_codes)
        for flag in block_flags:
            flag_records[flag] += 1
            flag_events[flag].add(identity[0])
            expected = _typed_reason_for_flag(flag)
            if expected and expected not in reason_codes:
                reason_mismatches.append(
                    {"event_id": identity[0], "indicator_id": identity[1], "flag": flag, "missing_reason_code": expected}
                )
        block_flag_set = set(block_flags)
        for reason, supporting_flags in TYPED_REASON_FLAGS.items():
            if reason in reason_codes and block_flag_set.intersection(supporting_flags):
                typed_reason_support_records[reason] += 1
            if reason in reason_codes and not block_flag_set.intersection(
                supporting_flags
            ):
                reason_mismatches.append(
                    {
                        "event_id": identity[0],
                        "indicator_id": identity[1],
                        "unexpected_reason_code": reason,
                        "active_scoring_block_flags": list(block_flags),
                    }
                )
        for prefix, reason in (
            (
                "phase_proxy_right_censored_peak:",
                "event_phase_proxy_right_censored_unverified",
            ),
            (
                "phase_proxy_low_sample_peak:",
                "event_phase_proxy_low_sample_unverified",
            ),
        ):
            if reason in reason_codes and not any(
                flag.startswith(prefix) for flag in block_flags
            ):
                reason_mismatches.append(
                    {
                        "event_id": identity[0],
                        "indicator_id": identity[1],
                        "unexpected_reason_code": reason,
                        "active_scoring_block_flags": list(block_flags),
                    }
                )
        if block_flags:
            combinations[block_flags] += 1
            combination_events[block_flags].add(identity[0])
        if category == "unavailable_complete_features_score_only":
            for flag in block_flags:
                eligible_flag_records[flag] += 1
                eligible_flag_events[flag].add(identity[0])
            if len(block_flags) == 1:
                sole_flag_records[block_flags[0]] += 1

    expected_summary_counts = {
        str(key): int(value)
        for key, value in summary_payload.get("score_status_counts", {}).items()
    }
    if expected_summary_counts != dict(status_counts):
        raise ValueError("scores status counts do not match summary")
    if reason_mismatches:
        raise ValueError(f"typed score blocker reasons are inconsistent: {reason_mismatches[:3]}")

    flag_metrics = []
    for flag, count in flag_records.most_common():
        flag_metrics.append(
            {
                "flag": flag,
                "truth_requirement": truth_requirement_for_flag(flag),
                "indicator_record_count": count,
                "unique_event_count": len(flag_events[flag]),
                "complete_feature_score_only_record_count": eligible_flag_records[flag],
                "complete_feature_score_only_unique_event_count": len(eligible_flag_events[flag]),
                "sole_block_recoverable_to_calibration_required_count": sole_flag_records[flag],
            }
        )
    priority = sorted(
        flag_metrics,
        key=lambda item: (
            -int(item["sole_block_recoverable_to_calibration_required_count"]),
            -int(item["complete_feature_score_only_record_count"]),
            str(item["flag"]),
        ),
    )
    combination_metrics = [
        {
            "flags": list(flags),
            "indicator_record_count": count,
            "unique_event_count": len(combination_events[flags]),
        }
        for flags, count in combinations.most_common()
    ]
    by_indicator = {
        indicator_id: {
            "indicator_record_count": sum(counts.values()),
            "calibration_required_count": counts["calibration_required"],
            "unavailable_count": counts["unavailable"],
        }
        for indicator_id, counts in sorted(indicator_counts.items())
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "audited_no_quality_gate_change",
        "source": {
            "scores": {"path": str(scores_path.resolve()), "sha256": _sha256(scores_path)},
            "summary": {"path": str(summary_path.resolve()), "sha256": _sha256(summary_path)},
            "video_id": str(summary_payload.get("video_id", "")),
            "registry_version": str(summary_payload.get("model_versions", {}).get("feasibility_registry", "")),
            "quality_policy_version": str(summary_payload.get("model_versions", {}).get("quality_policy", "")),
            "scoring_loop_version": str(summary_payload.get("loop_version", "")),
        },
        "counts": {
            "indicator_record_count": len(rows),
            "status_counts": dict(status_counts),
            "mutually_exclusive_decomposition": dict(category_counts),
            "complete_feature_score_only_recoverable_to_calibration_required_if_all_truth_clears_count": category_counts["unavailable_complete_features_score_only"],
            "grade_count": 0,
            "threshold_count": 0,
        },
        "flag_metrics": flag_metrics,
        "reason_code_counts": dict(reason_records.most_common()),
        "typed_reason_metrics": [
            {
                "reason_code": reason,
                "indicator_record_count": reason_records[reason],
                "supporting_scoring_block_flags": sorted(supporting_flags),
                "supported_indicator_record_count": typed_reason_support_records[reason],
            }
            for reason, supporting_flags in sorted(TYPED_REASON_FLAGS.items())
            if reason_records[reason]
        ],
        "flag_combination_metrics": combination_metrics,
        "human_review_priority": priority,
        "indicator_metrics": by_indicator,
        "assertions": {
            "typed_reason_mapping_consistent": True,
            "quality_gate_modified": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
            "recoverable_means_calibration_required_not_scored": True,
            "counts_are_accuracy_metrics": False,
        },
    }


def validate_scoring_blocker_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION or report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported scoring blocker audit version")
    if report.get("status") != "audited_no_quality_gate_change":
        raise ValueError("scoring blocker audit status is unsafe")
    assertions = report.get("assertions", {})
    if assertions.get("typed_reason_mapping_consistent") is not True:
        raise ValueError("typed reason mapping must be consistent")
    if assertions.get("recoverable_means_calibration_required_not_scored") is not True:
        raise ValueError("recoverable semantics must stop at calibration_required")
    for field in (
        "quality_gate_modified",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
        "counts_are_accuracy_metrics",
    ):
        if assertions.get(field) is not False:
            raise ValueError(f"unsafe scoring blocker audit assertion: {field}")
    counts = report.get("counts", {})
    statuses = counts.get("status_counts", {})
    decomposition = counts.get("mutually_exclusive_decomposition", {})
    if int(counts.get("indicator_record_count", -1)) != sum(int(v) for v in statuses.values()):
        raise ValueError("status counts do not cover all records")
    if int(counts.get("indicator_record_count", -1)) != sum(int(v) for v in decomposition.values()):
        raise ValueError("blocker decomposition is not exhaustive")
    if int(counts.get("grade_count", -1)) != 0 or int(counts.get("threshold_count", -1)) != 0:
        raise ValueError("blocker audit must not contain grades or thresholds")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_scoring_blockers(scores_path=args.scores, summary_path=args.summary)
    validate_scoring_blocker_audit(report)
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
                "records": report["counts"]["indicator_record_count"],
                "complete_feature_score_only": report["counts"]["complete_feature_score_only_recoverable_to_calibration_required_if_all_truth_clears_count"],
                "grades_generated": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
