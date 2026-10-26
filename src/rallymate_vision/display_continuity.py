"""Bounded presentation-only continuity; measured pose records stay untouched."""
from __future__ import annotations

import math
from collections import deque
from typing import Any

DISPLAY_CONTINUITY_VERSION = "short-joint-gap-v1.0.0"
IDENTITY_REPAIR_VERSION = "short-target-dropout-v1.0.0"
MAX_DISPLAY_GAP_MS = 150


def _anchors(before: dict, after: dict, target: dict) -> dict:
    return {"before_timestamp_ms": before["timestamp_ms"], "after_timestamp_ms": after["timestamp_ms"],
            "before_frame_index": before["frame_index"], "after_frame_index": after["frame_index"],
            "before_processed_index": before["processed_index"], "after_processed_index": after["processed_index"],
            "person_track_id": target["person_track_id"], "selection_epoch": target["selection_epoch"],
            "before_selection_epoch": before["selection_epoch"], "after_selection_epoch": after["selection_epoch"]}


def _point_between(a: dict, b: dict, before: dict, after: dict, target: dict) -> dict:
    alpha = (target["timestamp_ms"] - before["timestamp_ms"]) / (after["timestamp_ms"] - before["timestamp_ms"])
    return {"name": a["name"], "x": round(a["x"] + (b["x"] - a["x"]) * alpha, 6),
            "y": round(a["y"] + (b["y"] - a["y"]) * alpha, 6),
            "confidence": min(a["confidence"], b["confidence"]), "source": "interpolated",
            "anchors": _anchors(before, after, target)}


def _time_segment(rows: list[dict], verified: set[int], *, allow_known_empty: bool = False) -> bool:
    if not rows or rows[-1]["timestamp_ms"] - rows[0]["timestamp_ms"] > MAX_DISPLAY_GAP_MS:
        return False
    dimensions = {(row["width"], row["height"]) for row in rows}
    return (len(dimensions) == 1 and all(row["frame_index"] in verified and (row["keypoints"] or allow_known_empty) for row in rows)
            and all(b["frame_index"] == a["frame_index"] + 1
                    and b["processed_index"] == a["processed_index"] + 1
                    and 0 < b["timestamp_ms"] - a["timestamp_ms"] <= 100 for a, b in zip(rows, rows[1:])))


def _target_geometry_continuous(before: dict, after: dict) -> bool:
    names = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
    centers, scales = [], []
    for row in (before, after):
        points = {p["name"]: p for p in row["keypoints"]}
        if not all(name in points for name in names):
            return False
        xy = [(points[n]["x"] * row["width"], points[n]["y"] * row["height"]) for n in names]
        shoulder = tuple((xy[0][i] + xy[1][i]) / 2 for i in (0, 1))
        hip = tuple((xy[2][i] + xy[3][i]) / 2 for i in (0, 1))
        centers.append(tuple((shoulder[i] + hip[i]) / 2 for i in (0, 1)))
        scales.append(math.dist(shoulder, hip))
    # These are conservative display identity gates, not technique thresholds.
    return (min(scales) >= 2 and max(scales) / min(scales) <= 1.5
            and math.dist(*centers) <= min(scales) * .6)


def _repair_target_dropouts(frames: list[dict], verified: set[int], evidence: dict[int, dict]) -> None:
    index = 1
    while index < len(frames) - 1:
        before = frames[index - 1]
        target = before["person_track_id"]
        if target is None or (frames[index]["person_track_id"] == target and frames[index]["keypoints"]) or before.get("identity_repair"):
            index += 1
            continue
        following = index + 1
        while (following < len(frames) and frames[following]["timestamp_ms"] - before["timestamp_ms"] <= MAX_DISPLAY_GAP_MS
               and (frames[following]["person_track_id"] != target or not frames[following]["keypoints"])):
            following += 1
        if following >= len(frames) or frames[following]["person_track_id"] != target or not frames[following]["keypoints"]:
            index += 1
            continue
        after = frames[following]
        segment = frames[index - 1:following + 1]
        proof = [evidence.get(row["frame_index"], {}) for row in segment]
        primary_ids = {item.get("primary_player_id") for item in proof}
        known_empty = [not row["keypoints"] and item.get("selected_source_track_id") == target
                       and item.get("primary_selection_status") == "selected"
                       and row.get("reason_code") in {"selected_pose_missing_or_conflicting", "confident_in_frame_keypoints_unavailable"}
                       and item.get("pose_track_ids", []).count(target) == (1 if row.get("reason_code") == "confident_in_frame_keypoints_unavailable" else 0)
                       for row, item in zip(segment[1:-1], proof[1:-1])]
        safe = (_time_segment(segment, verified, allow_known_empty=True) and None not in primary_ids and len(primary_ids) == 1
                and all(item.get("identity_ambiguous") is False for item in proof)
                and all(known or target not in item.get("observed_track_ids", ()) for known, item in zip(known_empty, proof[1:-1]))
                and all(known or row["keypoints"] and row["selection_status"] == "selected"
                        for known, row in zip(known_empty, segment[1:-1]))
                and before["selection_status"] == after["selection_status"] == "selected"
                and _target_geometry_continuous(before, after))
        if not safe:
            index += 1
            continue
        left = {p["name"]: p for p in before["keypoints"] if p["source"] == "observed"}
        right = {p["name"]: p for p in after["keypoints"] if p["source"] == "observed"}
        if len(left.keys() & right.keys()) < 8:
            index += 1
            continue
        for row in frames[index:following]:
            row["identity_repair"] = {
                "version": IDENTITY_REPAIR_VERSION, "method": "same_target_detection_dropout",
                "dropout_kind": "pose_unavailable" if not row["keypoints"] else "target_not_detected",
                "original_track_id": row["person_track_id"], "original_selection_epoch": row["selection_epoch"],
                "target_track_id": target, "primary_player_id": next(iter(primary_ids)),
                "before_frame_index": before["frame_index"], "after_frame_index": after["frame_index"],
                "before_timestamp_ms": before["timestamp_ms"], "after_timestamp_ms": after["timestamp_ms"],
                "before_selection_epoch": before["selection_epoch"], "after_selection_epoch": after["selection_epoch"],
            }
            row["person_track_id"] = target
            row["selection_status"] = "display_interpolated"
            row["keypoints"] = [_point_between(point, right[name], before, after, row)
                                for name, point in left.items() if name in right]
        index = following + 1


def display_statistics(frames: list[dict]) -> dict[str, Any]:
    counts = [sum(point.get("source") == "interpolated" for point in row["keypoints"]) for row in frames]
    return {"version": DISPLAY_CONTINUITY_VERSION, "max_gap_ms": MAX_DISPLAY_GAP_MS,
            "interpolated_keypoints": sum(counts), "affected_frames": sum(count > 0 for count in counts),
            "identity_repair_version": IDENTITY_REPAIR_VERSION,
            "repaired_identity_frames": sum(bool(row.get("identity_repair")) for row in frames),
            "response_score_recovered_keypoints": sum(bool(point.get("score_semantics")) for row in frames for point in row["keypoints"]),
            "confidence_semantics": "minimum_anchor_confidence_not_model_estimate", "used_for_scoring": False}


def build_display_frames(frames: list[dict], *, verified_frame_indexes: set[int],
                         recovered_points: dict[int, list[dict]] | None = None,
                         identity_evidence: dict[int, dict] | None = None) -> tuple[list[dict], dict]:
    recovered_points = recovered_points or {}
    display = []
    for row in frames:
        points = {p["name"]: {**p, "source": "observed"} for p in row["keypoints"]}
        if row["frame_index"] in verified_frame_indexes:
            for point in recovered_points.get(row["frame_index"], []):
                points.setdefault(point["name"], dict(point))
        display.append({**row, "observed_person_track_id": row["person_track_id"],
                        "observed_selection_epoch": row["selection_epoch"], "keypoints": list(points.values())})
    _repair_target_dropouts(display, verified_frame_indexes, identity_evidence or {})
    # Only original/display-normalized observations can anchor a repair. Derived
    # points never become anchors, so a series of gaps cannot extend the limit.
    maps = [{p["name"]: p for p in row["keypoints"] if p["source"] == "observed"} for row in display]
    names = set().union(*(points.keys() for points in maps)) if maps else set()
    for name in sorted(names):
        previous = None
        for current, points in enumerate(maps):
            if name not in points:
                continue
            if previous is not None and current - previous > 1:
                segment = display[previous:current + 1]
                same_identity = len({(row["person_track_id"], row["selection_epoch"]) for row in segment}) == 1
                if same_identity and _time_segment(segment, verified_frame_indexes) and not any(row.get("identity_repair") for row in segment):
                    for row in segment[1:-1]:
                        row["keypoints"].append(_point_between(maps[previous][name], points[name], segment[0], segment[-1], row))
            previous = current
    return display, display_statistics(display)


def one_display_identity(frames: list[dict]) -> bool:
    """Only explicitly proven short target dropouts may bridge raw epochs."""
    visible = [row for row in frames if row["keypoints"]]
    if not visible or len({row["person_track_id"] for row in visible}) != 1:
        return False
    for before, after in zip(visible, visible[1:]):
        if before["selection_epoch"] == after["selection_epoch"]:
            continue
        repair = before.get("identity_repair") or after.get("identity_repair")
        if not repair or not repair["before_frame_index"] <= before["frame_index"] < after["frame_index"] <= repair["after_frame_index"]:
            return False
    return True


def iter_display_frames(observations):
    """Apply the same policy with only 300 ms of source context in memory.

    Each input tuple is (raw_frame, verified_time, recovered_points, identity_evidence).
    Yielding is delayed by the 150 ms lookahead; callers can maintain their own
    bounded scoring/selection window without loading a complete video artifact.
    """
    pending = deque()
    last_emitted = -1

    def ready(cutoff):
        nonlocal last_emitted
        rows = [item[0] for item in pending]
        display, _ = build_display_frames(rows,
            verified_frame_indexes={item[0]["frame_index"] for item in pending if item[1]},
            recovered_points={item[0]["frame_index"]: item[2] for item in pending},
            identity_evidence={item[0]["frame_index"]: item[3] for item in pending})
        for raw, row in zip(rows, display):
            if raw["frame_index"] > last_emitted and raw["timestamp_ms"] <= cutoff:
                last_emitted = raw["frame_index"]
                yield raw, row

    for item in observations:
        pending.append(item)
        if len(pending) > 10000:
            raise ValueError("display continuity context exceeds its bounded frame limit")
        cutoff = item[0]["timestamp_ms"] - MAX_DISPLAY_GAP_MS
        yield from ready(cutoff)
        while pending and pending[0][0]["timestamp_ms"] < cutoff - MAX_DISPLAY_GAP_MS:
            pending.popleft()
    yield from ready(math.inf)
