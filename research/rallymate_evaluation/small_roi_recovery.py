from __future__ import annotations

from collections import Counter
from typing import Any

from rallymate_vision.utils import expand_box


def _area(detection: dict[str, Any]) -> float:
    x1, y1, x2, y2 = detection["bbox_px"]
    return max(0.0, float(x2) - float(x1)) * max(
        0.0, float(y2) - float(y1)
    )


def select_small_roi_targets(
    *,
    frame_rows: list[dict[str, Any]],
    timeline_rows: list[dict[str, Any]],
    event_ranges: list[tuple[int, int]],
    baseline_min_roi_size_px: int,
    experimental_min_roi_size_px: int,
    roi_margin: float,
    baseline_max_players: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select only primary-player ROIs skipped by the baseline size guard.

    This is a deterministic preflight helper.  It does not run Pose inference,
    change a production route, or infer that a recovered keypoint is accurate.
    """

    if experimental_min_roi_size_px >= baseline_min_roi_size_px:
        raise ValueError("experimental min ROI must be smaller than the baseline guard")
    if baseline_max_players < 1:
        raise ValueError("baseline_max_players must be positive")
    timeline = {int(row["processed_index"]): row for row in timeline_rows}
    if len(timeline) != len(timeline_rows):
        raise ValueError("primary timeline contains duplicate processed indexes")
    selected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for frame in frame_rows:
        frame_meta = frame["frame"]
        timestamp_ms = int(frame_meta["timestamp_ms"])
        if not any(start <= timestamp_ms <= end for start, end in event_ranges):
            continue
        counts["unique_frames_in_gap_events"] += 1
        processed_index = int(frame_meta["processed_index"])
        timeline_row = timeline.get(processed_index)
        if timeline_row is None:
            raise ValueError(f"primary timeline misses processed frame {processed_index}")
        track_id = timeline_row.get("source_track_id")
        current_pose = next(
            (
                pose
                for pose in frame.get("poses", [])
                if pose.get("person_track_id") == track_id
            ),
            None,
        )
        if current_pose is not None:
            counts["selected_pose_already_present"] += 1
            continue
        players = sorted(
            [
                item
                for item in frame.get("detections", [])
                if item.get("class_name") == "player"
            ],
            key=_area,
            reverse=True,
        )
        rank = next(
            (
                index + 1
                for index, item in enumerate(players)
                if item.get("track_id") == track_id
            ),
            None,
        )
        if rank is None:
            counts["selected_detection_missing"] += 1
            continue
        if rank > baseline_max_players:
            counts["outside_baseline_area_schedule"] += 1
            continue
        detection = players[rank - 1]
        crop_box = expand_box(
            detection["bbox_px"],
            int(frame_meta["width"]),
            int(frame_meta["height"]),
            margin=roi_margin,
        )
        crop_width = crop_box[2] - crop_box[0]
        crop_height = crop_box[3] - crop_box[1]
        if crop_width >= baseline_min_roi_size_px and crop_height >= baseline_min_roi_size_px:
            counts["pose_missing_not_explained_by_baseline_size_guard"] += 1
            continue
        if crop_width < experimental_min_roi_size_px or crop_height < experimental_min_roi_size_px:
            counts["below_experimental_size_guard"] += 1
            continue
        counts["targeted_baseline_size_guard_skip"] += 1
        selected.append(
            {
                "processed_index": processed_index,
                "source_frame_index": int(frame_meta["index"]),
                "timestamp_ms": timestamp_ms,
                "track_id": int(track_id),
                "detection": detection,
                "area_rank": rank,
                "crop_box": crop_box,
                "crop_width": crop_width,
                "crop_height": crop_height,
            }
        )
    return selected, dict(counts)


def select_roi_margin_targets(
    *,
    frame_rows: list[dict[str, Any]],
    timeline_rows: list[dict[str, Any]],
    event_ranges: list[tuple[int, int]],
    baseline_roi_margin: float,
    experimental_roi_margin: float,
    min_roi_size_px: int,
    baseline_max_players: int,
    require_baseline_crop_clipped: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select observed primary-player ROIs whose crop changes with more context.

    The selector is deliberately limited to frames belonging to an existing
    feature-gap event.  It requires a baseline Pose and never fills a missing
    detection or switches tracks.  A returned target therefore tests only the
    crop-context policy with the same video, detection, track and model.
    """

    if not 0 <= baseline_roi_margin < experimental_roi_margin:
        raise ValueError(
            "experimental ROI margin must be greater than the non-negative baseline"
        )
    if min_roi_size_px < 1 or baseline_max_players < 1:
        raise ValueError("ROI size and max players must be positive")
    timeline = {int(row["processed_index"]): row for row in timeline_rows}
    if len(timeline) != len(timeline_rows):
        raise ValueError("primary timeline contains duplicate processed indexes")
    selected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for frame in frame_rows:
        frame_meta = frame["frame"]
        timestamp_ms = int(frame_meta["timestamp_ms"])
        if not any(start <= timestamp_ms <= end for start, end in event_ranges):
            continue
        counts["unique_frames_in_gap_events"] += 1
        processed_index = int(frame_meta["processed_index"])
        timeline_row = timeline.get(processed_index)
        if timeline_row is None:
            raise ValueError(f"primary timeline misses processed frame {processed_index}")
        track_id = timeline_row.get("source_track_id")
        if track_id is None:
            counts["selected_track_missing"] += 1
            continue
        players = sorted(
            [
                item
                for item in frame.get("detections", [])
                if item.get("class_name") == "player"
            ],
            key=_area,
            reverse=True,
        )
        rank = next(
            (
                index + 1
                for index, item in enumerate(players)
                if item.get("track_id") == track_id
            ),
            None,
        )
        if rank is None:
            counts["selected_detection_missing"] += 1
            continue
        if rank > baseline_max_players:
            counts["outside_baseline_area_schedule"] += 1
            continue
        detection = players[rank - 1]
        current_pose = next(
            (
                pose
                for pose in frame.get("poses", [])
                if pose.get("person_track_id") == track_id
            ),
            None,
        )
        if current_pose is None:
            counts["selected_pose_missing"] += 1
            continue
        width = int(frame_meta["width"])
        height = int(frame_meta["height"])
        baseline_crop = expand_box(
            detection["bbox_px"], width, height, margin=baseline_roi_margin
        )
        experimental_crop = expand_box(
            detection["bbox_px"], width, height, margin=experimental_roi_margin
        )
        if (
            baseline_crop[2] - baseline_crop[0] < min_roi_size_px
            or baseline_crop[3] - baseline_crop[1] < min_roi_size_px
        ):
            counts["baseline_size_guard_ineligible"] += 1
            continue
        baseline_crop_clipped = bool(
            baseline_crop[0] == 0
            or baseline_crop[1] == 0
            or baseline_crop[2] == width
            or baseline_crop[3] == height
        )
        if require_baseline_crop_clipped and not baseline_crop_clipped:
            counts["baseline_crop_not_clipped"] += 1
            continue
        if baseline_crop == experimental_crop:
            counts["experimental_crop_unchanged_after_clamp"] += 1
            continue
        counts["targeted_context_expansion"] += 1
        selected.append(
            {
                "processed_index": processed_index,
                "source_frame_index": int(frame_meta["index"]),
                "timestamp_ms": timestamp_ms,
                "track_id": int(track_id),
                "detection": detection,
                "area_rank": rank,
                "baseline_crop_box": baseline_crop,
                "experimental_crop_box": experimental_crop,
                "baseline_crop_clipped": baseline_crop_clipped,
                "baseline_valid_keypoint_count": sum(
                    point.get("in_frame") is not False
                    and float(point.get("confidence", 0.0)) >= 0.25
                    for point in current_pose.get("keypoints", [])
                ),
            }
        )
    return selected, dict(counts)


def feature_vector_transitions(
    comparison: dict[str, Any], current_name: str, experiment_name: str
) -> dict[str, Any]:
    transitions: Counter[str] = Counter()
    gains: list[dict[str, str]] = []
    regressions: list[dict[str, str]] = []
    for event in comparison.get("events", []):
        event_id = str(event["boundary"]["event_id"])
        for indicator in event.get("indicators", []):
            indicator_id = str(indicator["indicator_id"])
            models = indicator["models"]
            current = str(models[current_name]["measurement_status"])
            experimental = str(models[experiment_name]["measurement_status"])
            transitions[f"{current}_to_{experimental}"] += 1
            identity = {"event_id": event_id, "indicator_id": indicator_id}
            if current == "unavailable" and experimental == "measured":
                gains.append(identity)
            elif current == "measured" and experimental == "unavailable":
                regressions.append(identity)
    return {
        "status_transitions": dict(sorted(transitions.items())),
        "recovered_indicator_instance_count": len(gains),
        "regressed_indicator_instance_count": len(regressions),
        "recovered_indicator_instances": gains,
        "regressed_indicator_instances": regressions,
    }


def operational_measurement_transitions(
    comparison: dict[str, Any],
    current_name: str,
    experiment_name: str,
    indicator_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = {
        (str(row["event_id"]), str(row["indicator_id"])): row
        for row in indicator_rows
    }
    if len(rows) != len(indicator_rows):
        raise ValueError("indicator features contain duplicate event/indicator rows")
    transitions: Counter[str] = Counter()
    gains: list[dict[str, str]] = []
    regressions: list[dict[str, str]] = []
    gate_status_counts: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    for event in comparison.get("events", []):
        event_id = str(event["boundary"]["event_id"])
        for indicator in event.get("indicators", []):
            indicator_id = str(indicator["indicator_id"])
            identity = (event_id, indicator_id)
            row = rows.get(identity)
            if row is None or identity in seen:
                raise ValueError(
                    "fixed-boundary comparison and indicator features differ"
                )
            seen.add(identity)
            models = indicator["models"]
            current_vector = str(models[current_name]["measurement_status"])
            experimental_vector = str(
                models[experiment_name]["measurement_status"]
            )
            compact_vector = (
                "measured"
                if all(item.get("valid") is True for item in row.get("features", []))
                else "unavailable"
            )
            if current_vector != compact_vector:
                raise ValueError(
                    "current fixed-boundary vector status differs from source features"
                )
            gate = row.get("quality_gate", {})
            measurement_allowed = gate.get("measurement_allowed") is True
            gate_status_counts[str(gate.get("status", ""))] += 1
            expected_current = (
                "measured"
                if current_vector == "measured" and measurement_allowed
                else "unavailable"
            )
            current = str(row.get("feature_status"))
            if current != expected_current:
                raise ValueError(
                    "source operational measurement status differs from vector/gate"
                )
            experimental = (
                "measured"
                if experimental_vector == "measured" and measurement_allowed
                else "unavailable"
            )
            transitions[f"{current}_to_{experimental}"] += 1
            reference = {"event_id": event_id, "indicator_id": indicator_id}
            if current == "unavailable" and experimental == "measured":
                gains.append(reference)
            elif current == "measured" and experimental == "unavailable":
                regressions.append(reference)
    if seen != set(rows):
        raise ValueError("fixed-boundary comparison does not cover indicator features")
    return {
        "status_transitions": dict(sorted(transitions.items())),
        "recovered_indicator_instance_count": len(gains),
        "regressed_indicator_instance_count": len(regressions),
        "recovered_indicator_instances": gains,
        "regressed_indicator_instances": regressions,
        "preserved_quality_gate_status_counts": dict(sorted(gate_status_counts.items())),
        "semantics": (
            "experimental_pose_vector_with_original_event_measurement_gate; "
            "not_production_and_not_accuracy"
        ),
    }


__all__ = [
    "feature_vector_transitions",
    "operational_measurement_transitions",
    "select_roi_margin_targets",
    "select_small_roi_targets",
]
