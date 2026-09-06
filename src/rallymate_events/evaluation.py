from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Any, Sequence

import numpy as np


EVENT_EVALUATION_SCHEMA_VERSION = "1.0.0"
EVENT_MATCHING_STRATEGY = (
    "global_bipartite_max_cardinality_then_total_iou"
)


def _overlap_and_union(
    first: dict[str, Any], second: dict[str, Any]
) -> tuple[int, int]:
    intersection = max(
        0,
        min(int(first["end_ms"]), int(second["end_ms"]))
        - max(int(first["start_ms"]), int(second["start_ms"])),
    )
    union = max(int(first["end_ms"]), int(second["end_ms"])) - min(
        int(first["start_ms"]), int(second["start_ms"])
    )
    return intersection, union


def segment_iou(first: dict[str, Any], second: dict[str, Any]) -> float:
    intersection, union = _overlap_and_union(first, second)
    return intersection / union if union > 0 else 0.0


def _resolved_video_id(event: dict[str, Any], *, label: str) -> str | None:
    video_id = event.get("video_id")
    provenance = event.get("provenance")
    source_id = provenance.get("source_id") if isinstance(provenance, dict) else None
    for field, value in (("video_id", video_id), ("provenance.source_id", source_id)):
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"{label} {field} must be a non-empty string when supplied")
    # ``video_id`` is the canonical truth-join namespace.  ``source_id`` is a
    # run/candidate identity and may intentionally include backend/window
    # qualifiers.  Legacy predictions without a top-level video_id continue to
    # fall back to provenance.source_id.
    return str(video_id or source_id) if video_id or source_id else None


def _validate_events(events: Sequence[dict[str, Any]], *, label: str) -> None:
    event_ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"{label} event {index} must be an object")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError(f"{label} event {index} has no non-empty event_id")
        if event_id in event_ids:
            raise ValueError(f"{label} contains duplicate event_id: {event_id}")
        event_ids.add(event_id)
        event_code = event.get("event_code")
        if not isinstance(event_code, str) or not event_code:
            raise ValueError(f"{label} event {event_id} has no event_code")
        track_id = event.get("person_track_id")
        if isinstance(track_id, bool) or not isinstance(track_id, int) or track_id < 1:
            raise ValueError(
                f"{label} event {event_id} has no positive integer person_track_id"
            )
        start_ms = event.get("start_ms")
        end_ms = event.get("end_ms")
        if (
            isinstance(start_ms, bool)
            or isinstance(end_ms, bool)
            or not isinstance(start_ms, int)
            or not isinstance(end_ms, int)
            or start_ms < 0
            or end_ms <= start_ms
        ):
            raise ValueError(f"{label} event {event_id} has an invalid interval")
        _resolved_video_id(event, label=f"{label} event {event_id}")


def _group_key(event: dict[str, Any], *, label: str) -> tuple[str, str, int]:
    video_id = _resolved_video_id(event, label=label)
    if video_id is None:  # guarded once for the complete input before grouping
        raise ValueError(f"{label} has no resolved video identity")
    return (
        video_id,
        str(event["event_code"]),
        int(event["person_track_id"]),
    )


def _group_events(
    events: Sequence[dict[str, Any]], *, label: str
) -> dict[tuple[str, str, int], list[tuple[int, dict[str, Any]]]]:
    grouped: dict[tuple[str, str, int], list[tuple[int, dict[str, Any]]]] = {}
    for index, event in enumerate(events):
        grouped.setdefault(_group_key(event, label=label), []).append((index, event))
    for values in grouped.values():
        values.sort(
            key=lambda indexed: (
                indexed[1]["start_ms"],
                indexed[1]["end_ms"],
                indexed[1]["event_id"],
            )
        )
    return grouped


@dataclass
class _ResidualEdge:
    to_node: int
    reverse_index: int
    capacity: int
    cost: Fraction


def _add_residual_edge(
    graph: list[list[_ResidualEdge]],
    from_node: int,
    to_node: int,
    *,
    cost: Fraction = Fraction(0),
) -> _ResidualEdge:
    forward = _ResidualEdge(
        to_node=to_node,
        reverse_index=len(graph[to_node]),
        capacity=1,
        cost=cost,
    )
    reverse = _ResidualEdge(
        to_node=from_node,
        reverse_index=len(graph[from_node]),
        capacity=0,
        cost=-cost,
    )
    graph[from_node].append(forward)
    graph[to_node].append(reverse)
    return forward


def _optimal_group_matches(
    predictions: Sequence[tuple[int, dict[str, Any]]],
    truth: Sequence[tuple[int, dict[str, Any]]],
    *,
    match_iou_min: float,
) -> list[tuple[int, int, float]]:
    """Return a deterministic minimum-cost maximum bipartite flow.

    Sending flow until no augmenting path remains maximises match cardinality.
    Exact rational negative-IoU edge costs then maximise total Segment IoU among
    all maximum-cardinality matchings.  Stable event sorting and residual-edge
    insertion order provide a deterministic tie-break when both objectives tie.
    """

    prediction_count = len(predictions)
    truth_count = len(truth)
    source = 0
    prediction_offset = 1
    truth_offset = prediction_offset + prediction_count
    sink = truth_offset + truth_count
    graph: list[list[_ResidualEdge]] = [[] for _ in range(sink + 1)]
    pair_edges: dict[tuple[int, int], tuple[_ResidualEdge, Fraction]] = {}

    for prediction_index in range(prediction_count):
        _add_residual_edge(graph, source, prediction_offset + prediction_index)
    for truth_index in range(truth_count):
        _add_residual_edge(graph, truth_offset + truth_index, sink)
    for prediction_index, (_, prediction) in enumerate(predictions):
        for truth_index, (_, truth_event) in enumerate(truth):
            intersection, union = _overlap_and_union(prediction, truth_event)
            iou = Fraction(intersection, union) if union > 0 else Fraction(0)
            if float(iou) + 1e-12 < match_iou_min:
                continue
            edge = _add_residual_edge(
                graph,
                prediction_offset + prediction_index,
                truth_offset + truth_index,
                cost=-iou,
            )
            pair_edges[(prediction_index, truth_index)] = (edge, iou)

    node_count = len(graph)
    while True:
        distances: list[Fraction | None] = [None] * node_count
        predecessors: list[tuple[int, int] | None] = [None] * node_count
        distances[source] = Fraction(0)
        for _ in range(node_count - 1):
            changed = False
            for from_node, edges in enumerate(graph):
                distance = distances[from_node]
                if distance is None:
                    continue
                for edge_index, edge in enumerate(edges):
                    if edge.capacity <= 0:
                        continue
                    candidate_distance = distance + edge.cost
                    current_distance = distances[edge.to_node]
                    if current_distance is None or candidate_distance < current_distance:
                        distances[edge.to_node] = candidate_distance
                        predecessors[edge.to_node] = (from_node, edge_index)
                        changed = True
            if not changed:
                break
        if distances[sink] is None:
            break
        node = sink
        while node != source:
            predecessor = predecessors[node]
            if predecessor is None:  # pragma: no cover - defensive invariant
                raise RuntimeError("event matching residual path is incomplete")
            from_node, edge_index = predecessor
            edge = graph[from_node][edge_index]
            edge.capacity -= 1
            graph[node][edge.reverse_index].capacity += 1
            node = from_node

    matches = [
        (
            predictions[prediction_index][0],
            truth[truth_index][0],
            float(iou),
        )
        for (prediction_index, truth_index), (edge, iou) in pair_edges.items()
        if edge.capacity == 0
    ]
    prediction_sort_keys = {
        original_index: (event["start_ms"], event["event_id"])
        for original_index, event in predictions
    }
    truth_sort_keys = {
        original_index: (event["start_ms"], event["event_id"])
        for original_index, event in truth
    }
    matches.sort(
        key=lambda pair: (
            prediction_sort_keys[pair[0]],
            truth_sort_keys[pair[1]],
        )
    )
    return matches


def _matching_metadata(match_iou_min: float) -> dict[str, Any]:
    return {
        "minimum_segment_iou": match_iou_min,
        "strategy": EVENT_MATCHING_STRATEGY,
        "one_to_one": True,
        "objective_priority": ["maximum_match_count", "maximum_total_segment_iou"],
        "grouping_keys": ["resolved_video_id", "event_code", "person_track_id"],
        "video_identity_resolution": [
            "canonical_top_level_video_id",
            "legacy_provenance.source_id_fallback",
        ],
        "missing_video_identity_semantics": (
            "non_empty_truth_evaluation_rejects_unscoped_events"
        ),
        "tie_break": "stable_event_sort_and_residual_edge_order",
        "threshold_semantics": (
            "event_association_protocol_only_not_model_acceptance_or_scoring_threshold"
        ),
    }


def _ground_truth_required_result(
    predictions: Sequence[dict[str, Any]], *, match_iou_min: float
) -> dict[str, Any]:
    by_code = {}
    for code in ("FS01", "FS02", "FS09"):
        by_code[code] = {
            "event_f1": None,
            "matched": None,
            "predicted": sum(item.get("event_code") == code for item in predictions),
            "ground_truth": 0,
            "mean_segment_iou": None,
        }
    return {
        "schema_version": EVENT_EVALUATION_SCHEMA_VERSION,
        "status": "ground_truth_required",
        "matching": _matching_metadata(match_iou_min),
        "event_f1": None,
        "precision": None,
        "recall": None,
        "mean_segment_iou": None,
        "boundary_mae_ms": None,
        "boundary_p95_ms": None,
        "phase_boundary_mae_ms": None,
        "phase_boundary_p95_ms": None,
        "phase_boundary_valid_rate": None,
        "phase_boundary_by_name": {},
        "matched": None,
        "false_positive": None,
        "false_negative": None,
        "predicted": len(predictions),
        "ground_truth": 0,
        "unverified_prediction_count": len(predictions),
        "by_event_code": by_code,
        "matches": [],
        "reason": "non_empty_manual_event_ground_truth_required_for_accuracy_metrics",
    }


def evaluate_events(
    predictions: list[dict[str, Any]],
    ground_truth: list[dict[str, Any]],
    *,
    match_iou_min: float = 0.5,
) -> dict[str, Any]:
    if (
        isinstance(match_iou_min, bool)
        or not isinstance(match_iou_min, (int, float))
        or not math.isfinite(float(match_iou_min))
        or not 0 < match_iou_min <= 1
    ):
        raise ValueError("match_iou_min must be finite and in (0, 1]")
    _validate_events(predictions, label="prediction")
    _validate_events(ground_truth, label="ground truth")
    if not ground_truth:
        return _ground_truth_required_result(
            predictions, match_iou_min=float(match_iou_min)
        )

    unscoped = [
        f"{label}:{event['event_id']}"
        for label, events in (("prediction", predictions), ("ground_truth", ground_truth))
        for event in events
        if _resolved_video_id(event, label=f"{label} event {event['event_id']}") is None
    ]
    if unscoped:
        raise ValueError(
            "non-empty truth evaluation requires video_id or provenance.source_id "
            f"for every event; unscoped={unscoped}"
        )

    prediction_groups = _group_events(predictions, label="prediction")
    truth_groups = _group_events(ground_truth, label="ground truth")
    matched_pairs: list[tuple[int, int, float]] = []
    for group_key in sorted(set(prediction_groups) | set(truth_groups)):
        matched_pairs.extend(
            _optimal_group_matches(
                prediction_groups.get(group_key, []),
                truth_groups.get(group_key, []),
                match_iou_min=float(match_iou_min),
            )
        )

    matched_pairs.sort(
        key=lambda pair: (
            _group_key(predictions[pair[0]], label="prediction"),
            predictions[pair[0]]["start_ms"],
            predictions[pair[0]]["event_id"],
            ground_truth[pair[1]]["event_id"],
        )
    )
    matches: list[dict[str, Any]] = []
    matched_prediction_indexes: set[int] = set()
    matched_truth_indexes: set[int] = set()
    truth_for_match: list[dict[str, Any]] = []
    for prediction_index, truth_index, iou in matched_pairs:
        matched_prediction_indexes.add(prediction_index)
        matched_truth_indexes.add(truth_index)
        prediction = predictions[prediction_index]
        truth = ground_truth[truth_index]
        prediction_phases = prediction.get("key_phases_ms", {})
        truth_phases = truth.get("key_phases_ms", {})
        if not isinstance(prediction_phases, dict):
            prediction_phases = {}
        if not isinstance(truth_phases, dict):
            truth_phases = {}
        phase_errors_ms = {
            phase_name: int(prediction_phases[phase_name] - truth_value)
            for phase_name, truth_value in truth_phases.items()
            if truth_value is not None
            and isinstance(truth_value, int)
            and not isinstance(truth_value, bool)
            and isinstance(prediction_phases.get(phase_name), int)
            and not isinstance(prediction_phases.get(phase_name), bool)
        }
        matches.append(
            {
                "prediction_event_id": prediction["event_id"],
                "ground_truth_event_id": truth["event_id"],
                "event_code": prediction["event_code"],
                "segment_iou": round(iou, 8),
                "start_error_ms": int(prediction["start_ms"] - truth["start_ms"]),
                "end_error_ms": int(prediction["end_ms"] - truth["end_ms"]),
                "phase_errors_ms": phase_errors_ms,
            }
        )
        truth_for_match.append(truth)

    tp = len(matches)
    fp = len(predictions) - len(matched_prediction_indexes)
    fn = len(ground_truth) - len(matched_truth_indexes)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    boundary_errors = [
        abs(match[boundary])
        for match in matches
        for boundary in ("start_error_ms", "end_error_ms")
    ]
    phase_errors_by_name: dict[str, list[int]] = defaultdict(list)
    phase_eligible_by_name: dict[str, int] = defaultdict(int)
    for match, truth in zip(matches, truth_for_match):
        truth_phases = truth.get("key_phases_ms", {})
        if not isinstance(truth_phases, dict):
            truth_phases = {}
        for phase_name, truth_value in truth_phases.items():
            if (
                truth_value is not None
                and isinstance(truth_value, int)
                and not isinstance(truth_value, bool)
            ):
                phase_eligible_by_name[phase_name] += 1
        for phase_name, error_ms in match["phase_errors_ms"].items():
            phase_errors_by_name[phase_name].append(abs(int(error_ms)))
    all_phase_errors = [
        error for values in phase_errors_by_name.values() for error in values
    ]
    by_code = {}
    for code in ("FS01", "FS02", "FS09"):
        code_predictions = [item for item in predictions if item.get("event_code") == code]
        code_truth = [item for item in ground_truth if item.get("event_code") == code]
        code_matches = [item for item in matches if item["event_code"] == code]
        code_tp = len(code_matches)
        code_precision = code_tp / len(code_predictions) if code_predictions else 0.0
        code_recall = code_tp / len(code_truth) if code_truth else 0.0
        code_f1 = (
            2 * code_precision * code_recall / (code_precision + code_recall)
            if code_precision + code_recall
            else 0.0
        )
        by_code[code] = {
            "event_f1": round(code_f1, 8),
            "matched": code_tp,
            "predicted": len(code_predictions),
            "ground_truth": len(code_truth),
            "mean_segment_iou": (
                round(sum(item["segment_iou"] for item in code_matches) / code_tp, 8)
                if code_tp
                else None
            ),
        }
    return {
        "schema_version": EVENT_EVALUATION_SCHEMA_VERSION,
        "status": "evaluated",
        "matching": _matching_metadata(float(match_iou_min)),
        "event_f1": round(f1, 8),
        "precision": round(precision, 8),
        "recall": round(recall, 8),
        "mean_segment_iou": (
            round(sum(item["segment_iou"] for item in matches) / tp, 8)
            if tp
            else None
        ),
        "boundary_mae_ms": (
            round(sum(boundary_errors) / len(boundary_errors), 8)
            if boundary_errors
            else None
        ),
        "boundary_p95_ms": (
            round(float(np.percentile(boundary_errors, 95)), 8)
            if boundary_errors
            else None
        ),
        "phase_boundary_mae_ms": (
            round(sum(all_phase_errors) / len(all_phase_errors), 8)
            if all_phase_errors
            else None
        ),
        "phase_boundary_p95_ms": (
            round(float(np.percentile(all_phase_errors, 95)), 8)
            if all_phase_errors
            else None
        ),
        "phase_boundary_valid_rate": (
            round(
                len(all_phase_errors) / max(sum(phase_eligible_by_name.values()), 1),
                8,
            )
            if phase_eligible_by_name
            else None
        ),
        "phase_boundary_by_name": {
            phase_name: {
                "eligible_truth_count": phase_eligible_by_name[phase_name],
                "predicted_count": len(phase_errors_by_name.get(phase_name, [])),
                "missing_prediction_count": (
                    phase_eligible_by_name[phase_name]
                    - len(phase_errors_by_name.get(phase_name, []))
                ),
                "valid_rate": round(
                    len(phase_errors_by_name.get(phase_name, []))
                    / max(phase_eligible_by_name[phase_name], 1),
                    8,
                ),
                "mae_ms": (
                    round(
                        sum(phase_errors_by_name[phase_name])
                        / len(phase_errors_by_name[phase_name]),
                        8,
                    )
                    if phase_errors_by_name.get(phase_name)
                    else None
                ),
                "p95_ms": (
                    round(float(np.percentile(phase_errors_by_name[phase_name], 95)), 8)
                    if phase_errors_by_name.get(phase_name)
                    else None
                ),
            }
            for phase_name in sorted(phase_eligible_by_name)
        },
        "matched": tp,
        "false_positive": fp,
        "false_negative": fn,
        "predicted": len(predictions),
        "ground_truth": len(ground_truth),
        "by_event_code": by_code,
        "matches": matches,
    }
