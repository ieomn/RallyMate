from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import re
from typing import Any, Iterable, Sequence

from rallymate_events.evaluation import segment_iou


EVENT_DISAGREEMENT_SCHEMA_VERSION = "1.0.0"
EVENT_DISAGREEMENT_REPORT_VERSION = "cross-model-event-disagreement/1.0.0"
DEFAULT_MATCH_IOU_THRESHOLDS = (0.1, 0.3, 0.5)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class _MatchState:
    count: int
    total_iou: float
    pairs: tuple[tuple[int, int, float], ...]


def _canonical_event_sha256(events: Sequence[dict[str, Any]]) -> str:
    payload = json.dumps(
        list(events),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_events(events: Sequence[dict[str, Any]], side: str) -> None:
    event_ids: set[str] = set()
    for index, event in enumerate(events):
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError(f"{side} event {index} has no event_id")
        if event_id in event_ids:
            raise ValueError(f"{side} contains duplicate event_id: {event_id}")
        event_ids.add(event_id)
        if not isinstance(event.get("event_code"), str) or not event["event_code"]:
            raise ValueError(f"{side} event {event_id} has no event_code")
        if not isinstance(event.get("person_track_id"), int):
            raise ValueError(f"{side} event {event_id} has no integer person_track_id")
        start_ms = event.get("start_ms")
        end_ms = event.get("end_ms")
        if not isinstance(start_ms, int) or not isinstance(end_ms, int):
            raise ValueError(f"{side} event {event_id} boundaries must be integers")
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError(f"{side} event {event_id} has an invalid interval")


def _normalise_thresholds(values: Iterable[float]) -> tuple[float, ...]:
    thresholds = tuple(sorted(set(float(value) for value in values)))
    if not thresholds:
        raise ValueError("at least one minimum IoU threshold is required")
    if any(not math.isfinite(value) or value <= 0 or value > 1 for value in thresholds):
        raise ValueError("minimum IoU thresholds must be finite and in (0, 1]")
    return thresholds


def _state_is_better(candidate: _MatchState, incumbent: _MatchState) -> bool:
    """Prefer more matches, then greater total IoU, then stable index ordering."""
    if candidate.count != incumbent.count:
        return candidate.count > incumbent.count
    if not math.isclose(candidate.total_iou, incumbent.total_iou, abs_tol=1e-12):
        return candidate.total_iou > incumbent.total_iou
    return tuple((left, right) for left, right, _ in candidate.pairs) < tuple(
        (left, right) for left, right, _ in incumbent.pairs
    )


def _ordered_optimal_matches(
    left: Sequence[dict[str, Any]],
    right: Sequence[dict[str, Any]],
    minimum_iou: float,
) -> tuple[tuple[int, int, float], ...]:
    """Return an order-preserving, one-to-one global optimum for one group."""

    @lru_cache(maxsize=None)
    def solve(left_index: int, right_index: int) -> _MatchState:
        if left_index >= len(left) or right_index >= len(right):
            return _MatchState(0, 0.0, ())
        candidates = [
            solve(left_index + 1, right_index),
            solve(left_index, right_index + 1),
        ]
        iou = segment_iou(left[left_index], right[right_index])
        if iou + 1e-12 >= minimum_iou:
            tail = solve(left_index + 1, right_index + 1)
            candidates.append(
                _MatchState(
                    count=tail.count + 1,
                    total_iou=tail.total_iou + iou,
                    pairs=((left_index, right_index, iou),) + tail.pairs,
                )
            )
        best = candidates[0]
        for candidate in candidates[1:]:
            if _state_is_better(candidate, best):
                best = candidate
        return best

    return solve(0, 0).pairs


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 8) if values else None


def _event_stub(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "event_code": event["event_code"],
        "person_track_id": event["person_track_id"],
        "start_ms": event["start_ms"],
        "end_ms": event["end_ms"],
    }


def _group_key(event: dict[str, Any]) -> tuple[str, int]:
    return str(event["event_code"]), int(event["person_track_id"])


def _group_events(
    events: Sequence[dict[str, Any]],
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(_group_key(event), []).append(event)
    for values in grouped.values():
        values.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["event_id"]))
    return grouped


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 8) if denominator else None


def _aggregate_counts(
    *, left_count: int, right_count: int, matched_count: int
) -> dict[str, Any]:
    return {
        "left_event_count": left_count,
        "right_event_count": right_count,
        "matched_count": matched_count,
        "left_unmatched_count": left_count - matched_count,
        "right_unmatched_count": right_count - matched_count,
        "left_to_right_match_rate": _rate(matched_count, left_count),
        "right_to_left_match_rate": _rate(matched_count, right_count),
    }


def _compare_at_threshold(
    left_events: Sequence[dict[str, Any]],
    right_events: Sequence[dict[str, Any]],
    minimum_iou: float,
) -> dict[str, Any]:
    left_groups = _group_events(left_events)
    right_groups = _group_events(right_events)
    group_keys = sorted(set(left_groups) | set(right_groups))
    matches: list[dict[str, Any]] = []
    unmatched_left: list[dict[str, Any]] = []
    unmatched_right: list[dict[str, Any]] = []
    by_group: dict[str, dict[str, Any]] = {}

    for event_code, track_id in group_keys:
        left = left_groups.get((event_code, track_id), [])
        right = right_groups.get((event_code, track_id), [])
        pairs = _ordered_optimal_matches(left, right, minimum_iou)
        matched_left = {left_index for left_index, _, _ in pairs}
        matched_right = {right_index for _, right_index, _ in pairs}
        group_matches: list[dict[str, Any]] = []
        for left_index, right_index, iou in pairs:
            left_event = left[left_index]
            right_event = right[right_index]
            left_duration = left_event["end_ms"] - left_event["start_ms"]
            right_duration = right_event["end_ms"] - right_event["start_ms"]
            row = {
                "left_event_id": left_event["event_id"],
                "right_event_id": right_event["event_id"],
                "event_code": event_code,
                "person_track_id": track_id,
                "segment_iou": round(iou, 8),
                "start_difference_ms": right_event["start_ms"] - left_event["start_ms"],
                "end_difference_ms": right_event["end_ms"] - left_event["end_ms"],
                "center_difference_ms": round(
                    (right_event["start_ms"] + right_event["end_ms"]
                     - left_event["start_ms"] - left_event["end_ms"])
                    / 2,
                    8,
                ),
                "duration_difference_ms": right_duration - left_duration,
            }
            matches.append(row)
            group_matches.append(row)
        group_unmatched_left = [
            _event_stub(event) for index, event in enumerate(left) if index not in matched_left
        ]
        group_unmatched_right = [
            _event_stub(event) for index, event in enumerate(right) if index not in matched_right
        ]
        unmatched_left.extend(group_unmatched_left)
        unmatched_right.extend(group_unmatched_right)
        group_id = f"{event_code}:track-{track_id}"
        by_group[group_id] = {
            "event_code": event_code,
            "person_track_id": track_id,
            **_aggregate_counts(
                left_count=len(left), right_count=len(right), matched_count=len(pairs)
            ),
            "mean_segment_iou": _mean([row["segment_iou"] for row in group_matches]),
        }

    by_event_code: dict[str, dict[str, Any]] = {}
    for event_code in sorted(
        {str(event["event_code"]) for event in left_events}
        | {str(event["event_code"]) for event in right_events}
    ):
        left_count = sum(event["event_code"] == event_code for event in left_events)
        right_count = sum(event["event_code"] == event_code for event in right_events)
        code_matches = [row for row in matches if row["event_code"] == event_code]
        by_event_code[event_code] = {
            **_aggregate_counts(
                left_count=left_count,
                right_count=right_count,
                matched_count=len(code_matches),
            ),
            "mean_segment_iou": _mean([row["segment_iou"] for row in code_matches]),
        }

    difference_fields = (
        "start_difference_ms",
        "end_difference_ms",
        "center_difference_ms",
        "duration_difference_ms",
    )
    return {
        "minimum_segment_iou": minimum_iou,
        **_aggregate_counts(
            left_count=len(left_events),
            right_count=len(right_events),
            matched_count=len(matches),
        ),
        "mean_segment_iou": _mean([row["segment_iou"] for row in matches]),
        "signed_mean_differences_ms": {
            field: _mean([float(row[field]) for row in matches])
            for field in difference_fields
        },
        "mean_absolute_differences_ms": {
            field: _mean([abs(float(row[field])) for row in matches])
            for field in difference_fields
        },
        "by_event_code": by_event_code,
        "by_group": by_group,
        "matches": matches,
        "unmatched": {
            "left_event_ids": [row["event_id"] for row in unmatched_left],
            "right_event_ids": [row["event_id"] for row in unmatched_right],
            "left_events": unmatched_left,
            "right_events": unmatched_right,
        },
    }


def compare_event_candidates(
    left_events: Sequence[dict[str, Any]],
    right_events: Sequence[dict[str, Any]],
    *,
    left_label: str,
    right_label: str,
    minimum_iou_thresholds: Iterable[float] = DEFAULT_MATCH_IOU_THRESHOLDS,
    left_input_sha256: str | None = None,
    right_input_sha256: str | None = None,
    left_source: str | None = None,
    right_source: str | None = None,
) -> dict[str, Any]:
    """Compare two candidate event timelines without treating either as truth.

    The result measures cross-model agreement only. It is deliberately symmetric in
    its match-rate reporting and never emits an accuracy, precision, recall, or F1
    claim.
    """
    if not left_label or not right_label or left_label == right_label:
        raise ValueError("left_label and right_label must be distinct non-empty strings")
    _validate_events(left_events, "left")
    _validate_events(right_events, "right")
    thresholds = _normalise_thresholds(minimum_iou_thresholds)
    supplied_hashes = (left_input_sha256, right_input_sha256)
    if any(value is not None and not _SHA256_RE.fullmatch(value) for value in supplied_hashes):
        raise ValueError("input SHA-256 values must contain exactly 64 hexadecimal characters")
    left_sha = left_input_sha256 or _canonical_event_sha256(left_events)
    right_sha = right_input_sha256 or _canonical_event_sha256(right_events)
    report = {
        "schema_version": EVENT_DISAGREEMENT_SCHEMA_VERSION,
        "report_version": EVENT_DISAGREEMENT_REPORT_VERSION,
        "status": "compared_candidate_events_no_ground_truth",
        "semantics": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "interpretation": (
                "Cross-model candidate-event agreement only; agreement is not accuracy "
                "and disagreement does not identify which model is correct."
            ),
        },
        "inputs": {
            "left": {
                "label": left_label,
                "event_count": len(left_events),
                "sha256": left_sha.lower(),
                "hash_scope": (
                    "raw_jsonl_bytes"
                    if left_input_sha256 is not None
                    else "canonical_event_records"
                ),
                "source": left_source,
            },
            "right": {
                "label": right_label,
                "event_count": len(right_events),
                "sha256": right_sha.lower(),
                "hash_scope": (
                    "raw_jsonl_bytes"
                    if right_input_sha256 is not None
                    else "canonical_event_records"
                ),
                "source": right_source,
            },
        },
        "matching": {
            "strategy": "ordered_dynamic_programming_max_match_count_then_total_iou",
            "one_to_one": True,
            "order_preserving_within_group": True,
            "grouping_keys": ["event_code", "person_track_id"],
            "minimum_segment_iou_thresholds": list(thresholds),
            "delta_convention": "right_minus_left",
            "threshold_semantics": "candidate_matching_sensitivity_only_not_scoring_threshold",
        },
        "threshold_results": [
            _compare_at_threshold(left_events, right_events, threshold)
            for threshold in thresholds
        ],
    }
    validate_event_disagreement_report(report)
    return report


def validate_event_disagreement_report(report: dict[str, Any]) -> None:
    """Validate safety-critical semantics and internal count consistency."""
    if report.get("schema_version") != EVENT_DISAGREEMENT_SCHEMA_VERSION:
        raise ValueError("unsupported event disagreement schema_version")
    semantics = report.get("semantics", {})
    if semantics.get("accuracy_claim") is not False:
        raise ValueError("cross-model disagreement report must set accuracy_claim=false")
    if semantics.get("ground_truth_provided") is not False:
        raise ValueError("cross-model disagreement report cannot represent ground truth")
    matching = report.get("matching", {})
    if matching.get("delta_convention") != "right_minus_left":
        raise ValueError("unsupported delta convention")
    thresholds = _normalise_thresholds(
        matching.get("minimum_segment_iou_thresholds", [])
    )
    results = report.get("threshold_results")
    if not isinstance(results, list) or len(results) != len(thresholds):
        raise ValueError("threshold_results must contain exactly one result per threshold")
    for side in ("left", "right"):
        item = report.get("inputs", {}).get(side, {})
        if not _SHA256_RE.fullmatch(str(item.get("sha256", ""))):
            raise ValueError(f"{side} input is missing a valid SHA-256")
        if not isinstance(item.get("event_count"), int) or item["event_count"] < 0:
            raise ValueError(f"{side} input has invalid event_count")
    for threshold, result in zip(thresholds, results):
        if not math.isclose(result.get("minimum_segment_iou", -1), threshold):
            raise ValueError("threshold result ordering or value mismatch")
        matched = result.get("matched_count")
        left_count = result.get("left_event_count")
        right_count = result.get("right_event_count")
        if not all(isinstance(value, int) and value >= 0 for value in (matched, left_count, right_count)):
            raise ValueError("event counts must be non-negative integers")
        if matched > min(left_count, right_count):
            raise ValueError("matched_count exceeds an input event count")
        if len(result.get("matches", [])) != matched:
            raise ValueError("matched_count does not equal matches length")
        unmatched = result.get("unmatched", {})
        if len(unmatched.get("left_event_ids", [])) != left_count - matched:
            raise ValueError("left unmatched IDs are inconsistent with counts")
        if len(unmatched.get("right_event_ids", [])) != right_count - matched:
            raise ValueError("right unmatched IDs are inconsistent with counts")
        expected_left_rate = _rate(matched, left_count)
        expected_right_rate = _rate(matched, right_count)
        if result.get("left_to_right_match_rate") != expected_left_rate:
            raise ValueError("left-to-right match rate is inconsistent with counts")
        if result.get("right_to_left_match_rate") != expected_right_rate:
            raise ValueError("right-to-left match rate is inconsistent with counts")
        for match in result.get("matches", []):
            if match.get("segment_iou", 0) + 1e-12 < threshold:
                raise ValueError("match is below the declared minimum IoU")
