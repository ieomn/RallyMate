"""Reviewable ball-event candidates and explicit human-confirmed statistics.

The automatic layer uses only observed detector centers and local trajectory
changes. It cannot establish physical contact. Court points become landing
points only after a bounce is confirmed; airborne trajectories are never mapped
onto the landing chart. No model, video or original inference artifact is edited.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re
from threading import Lock

import numpy as np

from rallymate_features.coordinates import validated_camera_transform
from rallymate_vision.court_projection import FixedCameraReferenceGuard, _transform
from rallymate_vision.trajectory import (
    MAX_FRAMES_ARTIFACT_BYTES, MAX_FRAMES_ARTIFACT_LINES, MAX_FRAMES_LINE_BYTES,
    parse_trajectory_line,
)

VERSION = "1.0.0"
RULE_VERSION = "observed-ball-turn-review-v1"
MAX_EVENTS = 2000
MAX_REVIEWS = 4000
MAX_TRACKS = 4096
_CACHE = OrderedDict()
_CACHE_LOCK = Lock()


class ShotStatisticsError(ValueError):
    pass


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _integer(value):
    return type(value) is int and value >= 0


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None


def _box(detection, width, height):
    box = detection.get("bbox_px")
    if (not isinstance(box, (list, tuple)) or len(box) != 4 or not all(_number(v) for v in box)
            or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
        return None
    return list(box)


def _near_racket(point):
    x, y = point["pixels"]
    pad = max(6., point["radius"] * 1.5)
    for box in point["rackets"]:
        distance = math.hypot(max(box[0] - x, 0., x - box[2]), max(box[1] - y, 0., y - box[3]))
        if distance <= pad:
            return True
    return False


def _candidate(window, video_sha):
    a, b, center, d, e = window
    times = [p["timestamp_ms"] for p in window]
    if any(not 0 < second - first <= 120 for first, second in zip(times, times[1:])):
        return None
    before = (center["reference"] - a["reference"]) / ((times[2] - times[0]) / 1000.)
    after = (e["reference"] - center["reference"]) / ((times[4] - times[2]) / 1000.)
    speeds = float(np.linalg.norm(before)), float(np.linalg.norm(after))
    if min(speeds) < .025 or max(speeds) > 4.:
        return None
    cosine = float(np.dot(before, after) / (speeds[0] * speeds[1]))
    turn = math.degrees(math.acos(max(-1., min(1., cosine))))
    if turn < 35 or sum(p["confidence"] for p in window) / 5 < .35:
        return None
    racket = _near_racket(center)
    ys = [p["reference"][1] for p in window]
    bounce_turn = (before[1] > .025 and after[1] < -.025
                   and ys[0] <= ys[1] <= ys[2] and ys[2] >= ys[3] >= ys[4])
    if not racket and not bounce_turn:
        return None
    kind = "hit" if racket else "bounce"
    identifier = hashlib.sha256(f"{video_sha}:{kind}:{center['frame_index']}:{center['track_id']}".encode()).hexdigest()[:20]
    return {"id": f"shot-{identifier}", "kind": kind, "status": "candidate",
        "timestamp_ms": center["timestamp_ms"], "frame_index": center["frame_index"],
        "position_image": {"x": round(center["pixels"][0] / center["width"], 6),
                           "y": round(center["pixels"][1] / center["height"], 6)},
        "position_court": None, "source": "trajectory_rule",
        "reason_codes": ["observed_ball_direction_change", "manual_contact_review_required",
                         "racket_bbox_near_ball" if racket else "image_vertical_turn_not_ground_contact"],
        "evidence": {"rule_version": RULE_VERSION, "source_frames": [p["frame_index"] for p in window],
                     "ball_track_id": center["track_id"], "camera_reference_epoch": center["epoch"],
                     "direction_change_deg": round(turn, 2),
                     "minimum_detector_confidence": round(min(p["confidence"] for p in window), 4),
                     "uses_interpolation": False, "contact_confirmed": False},
        "_calibration": center["calibration"], "_pixels": center["pixels"]}


def _scan(path: Path, allow_partial: bool) -> dict:
    stat = path.stat()
    if stat.st_size > MAX_FRAMES_ARTIFACT_BYTES:
        raise ShotStatisticsError("frames_artifact_too_large")
    histories, events = {}, []
    camera_guard = FixedCameraReferenceGuard()
    previous_epoch = None
    source_sha, first_ms, last_ms = None, None, None
    frame_count = ball_frames = ambiguous_frames = 0
    last_index = -1
    consumed = 0
    truncated = False
    with path.open("rb") as stream:
        for line_number in range(1, MAX_FRAMES_ARTIFACT_LINES + 2):
            raw = stream.readline(MAX_FRAMES_LINE_BYTES + 1)
            if not raw:
                break
            consumed += len(raw)
            if consumed > MAX_FRAMES_ARTIFACT_BYTES or len(raw) > MAX_FRAMES_LINE_BYTES or line_number > MAX_FRAMES_ARTIFACT_LINES:
                raise ShotStatisticsError("frames_artifact_limit_exceeded")
            if not raw.endswith(b"\n") and allow_partial:
                break
            if not raw.strip():
                continue
            row = parse_trajectory_line(raw, line_number)
            frame = row.get("frame", {})
            width, height, index = frame.get("width"), frame.get("height"), frame.get("index")
            timestamp = frame.get("timestamp_ms")
            sha = frame.get("video_sha256")
            if (not all(_integer(v) for v in (width, height, index, timestamp)) or not width or not height
                    or index <= last_index or (last_ms is not None and timestamp <= last_ms)):
                raise ShotStatisticsError("invalid_or_unordered_frame_timeline")
            if _sha(sha):
                if source_sha is not None and source_sha != sha.lower():
                    raise ShotStatisticsError("multiple_source_videos")
                source_sha = sha.lower()
            elif sha is not None:
                raise ShotStatisticsError("invalid_source_video_sha256")
            frame_count += 1
            first_ms = timestamp if first_ms is None else first_ms
            last_ms, last_index = timestamp, index
            camera = row.get("camera_motion") or {}
            transform = validated_camera_transform(camera)
            epoch = camera.get("reference_epoch")
            camera_guard.observe(camera, width, height)
            calibration = camera_guard.calibration(row.get("court"), width, height)
            if transform is None or not _integer(epoch) or epoch != previous_epoch:
                histories.clear()
            previous_epoch = epoch
            if (transform is None or not _integer(epoch) or not source_sha
                    or frame.get("timestamp_source") != "decoder_pts"):
                continue
            detections = row.get("detections", [])
            if not isinstance(detections, list):
                raise ShotStatisticsError("invalid_detection_list")
            balls, rackets = [], []
            for detection in detections:
                if not isinstance(detection, dict) or detection.get("class_name") not in {"ball", "racket"}:
                    continue
                confidence = detection.get("confidence")
                box = _box(detection, width, height)
                if box is None or not _number(confidence) or not .25 <= confidence <= 1:
                    continue
                if detection["class_name"] == "racket":
                    rackets.append(box)
                elif _integer(detection.get("track_id")):
                    balls.append((detection, box))
            if balls:
                ball_frames += 1
            if len(balls) != 1:
                if len(balls) > 1:
                    ambiguous_frames += 1
                    histories.clear()
                continue
            detection, box = balls[0]
            track = detection["track_id"]
            pixels = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
            point = {"frame_index": index, "timestamp_ms": timestamp, "track_id": track,
                     "width": width, "height": height, "epoch": epoch, "pixels": pixels,
                     "reference": transform @ np.array([*pixels, 1.]) / max(width, height),
                     "confidence": detection["confidence"], "rackets": rackets,
                     "radius": max(box[2] - box[0], box[3] - box[1]) / 2,
                     "calibration": calibration}
            if track not in histories:
                if len(histories) >= MAX_TRACKS:
                    histories.clear()
                histories[track] = deque(maxlen=5)
            history = histories[track]
            if history and not 0 < timestamp - history[-1]["timestamp_ms"] <= 120:
                history.clear()
            history.append(point)
            if len(history) < 5:
                continue
            event = _candidate(list(history), source_sha)
            if event is None:
                continue
            # Neighboring windows can describe one change of direction. Never
            # count them as independent hits/bounces before a human reviews.
            if any(abs(event["timestamp_ms"] - other["timestamp_ms"]) < 250 for other in events[-4:]):
                continue
            if len(events) < MAX_EVENTS:
                events.append(event)
            else:
                truncated = True
    return {"source": {"video_sha256": source_sha, "frame_count": frame_count,
                       "start_ms": first_ms, "end_ms": last_ms,
                       "duration_ms": last_ms - first_ms if last_ms is not None else None,
                       "coordinate_space": "normalized_frame_0_1",
                       "ball_observed_frame_count": ball_frames,
                       "ambiguous_ball_frame_count": ambiguous_frames},
            "events": events, "truncated": truncated}


def _base(path: Path, allow_partial: bool):
    stat = path.stat()
    key = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, allow_partial)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached
    value = _scan(path, allow_partial)
    with _CACHE_LOCK:
        _CACHE[key] = value
        while len(_CACHE) > 3:
            _CACHE.popitem(last=False)
    return value


def _position(value, *, court=False):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"x", "y", "source"}:
        raise ShotStatisticsError("invalid_event_position")
    x, y = value.get("x"), value.get("y")
    limits = ((-.5, 1.5), (-.25, 1.25)) if court else ((0., 1.), (0., 1.))
    if not all(_number(v) and low <= v <= high for v, (low, high) in zip((x, y), limits)):
        raise ShotStatisticsError("event_position_out_of_range")
    return {"x": round(float(x), 6), "y": round(float(y), 6), **({"source": "manual"} if court else {})}


def validate_review(value: dict, statistics: dict) -> dict:
    """Validate a full review record before the store's revision-checked write."""
    if not isinstance(value, dict) or set(value) - {
            "id", "kind", "status", "timestamp_ms", "frame_index", "position_image", "position_court", "source", "note"}:
        raise ShotStatisticsError("invalid_review_fields")
    identifier = value.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identifier):
        raise ShotStatisticsError("invalid_review_id")
    existing = next((event for event in statistics.get("events", []) if event["id"] == identifier), None)
    if existing is None and not identifier.startswith("manual-"):
        raise ShotStatisticsError("unknown_candidate_id")
    if value.get("kind") not in {"hit", "bounce"} or value.get("status") not in {"candidate", "confirmed", "dismissed"}:
        raise ShotStatisticsError("invalid_review_kind_or_status")
    if existing is not None and value["kind"] != existing["kind"]:
        raise ShotStatisticsError("candidate_kind_cannot_change")
    timestamp = value.get("timestamp_ms")
    start, end = statistics["source"].get("start_ms"), statistics["source"].get("end_ms")
    if not _number(timestamp) or not _number(start) or not _number(end) or not start <= timestamp <= end:
        raise ShotStatisticsError("review_timestamp_outside_video")
    if existing is not None and existing["source"] == "trajectory_rule" and timestamp != existing["timestamp_ms"]:
        raise ShotStatisticsError("candidate_timestamp_cannot_change")
    frame_index = value.get("frame_index", existing.get("frame_index") if existing else None)
    if frame_index is not None and not _integer(frame_index):
        raise ShotStatisticsError("invalid_review_frame_index")
    if existing is not None and frame_index != existing.get("frame_index"):
        raise ShotStatisticsError("candidate_frame_cannot_change")
    if existing is None and frame_index is not None:
        raise ShotStatisticsError("manual_event_has_no_verified_source_frame")
    if value["status"] == "confirmed":
        for other in statistics.get("events", []):
            if other["id"] == identifier or other["kind"] != value["kind"] or other["status"] != "confirmed":
                continue
            same_frame = frame_index is not None and frame_index == other.get("frame_index")
            if same_frame or abs(timestamp - other["timestamp_ms"]) < 80:
                raise ShotStatisticsError("duplicate_confirmed_event_near_timestamp")
    image = _position(value.get("position_image", existing.get("position_image") if existing else None))
    if existing is not None and existing["source"] == "trajectory_rule" and image != existing.get("position_image"):
        raise ShotStatisticsError("candidate_image_position_cannot_change")
    raw_court = value.get("position_court")
    if isinstance(raw_court, dict) and raw_court.get("source") == "calibrated_ground_projection":
        # A client may echo a previously displayed automatic projection. Do
        # not relabel that derived value as a human-entered court coordinate.
        if not existing or existing["source"] != "trajectory_rule" or raw_court != existing.get("position_court"):
            raise ShotStatisticsError("unverified_automatic_court_position")
        court = None
    else:
        if isinstance(raw_court, dict) and raw_court.get("source") not in (None, "manual"):
            raise ShotStatisticsError("invalid_court_position_source")
        court = _position(raw_court, court=True)
    if court is not None and value["kind"] != "bounce":
        raise ShotStatisticsError("court_position_requires_bounce")
    note = value.get("note", "")
    if not isinstance(note, str) or len(note) > 500:
        raise ShotStatisticsError("invalid_review_note")
    return {"id": identifier, "kind": value["kind"], "status": value["status"],
            # Display formatting belongs to the client. Rounding here could
            # push a valid timestamp beyond a fractional clip boundary.
            "timestamp_ms": float(timestamp), "frame_index": frame_index,
            "position_image": image, "position_court": court,
            "source": existing["source"] if existing else "manual", "note": note}


def build_shot_statistics(frames_path: str | Path, *, job_id=None, reviews=None, revision=0,
                          allow_partial=False, expected_video_sha256=None, trusted_reviews=False) -> dict:
    """Merge current candidates with reviews previously checked at write time.

    ``trusted_reviews`` is only for records loaded from the source-bound server
    store, never a client request flag. A later candidate-rule change must not
    erase a person's earlier review. Newly submitted unknown candidate IDs are
    still rejected by ``validate_review`` before persistence.
    """
    path = Path(frames_path).resolve()
    if type(allow_partial) is not bool or type(trusted_reviews) is not bool or not _integer(revision):
        raise ShotStatisticsError("invalid_statistics_request")
    if trusted_reviews and not _sha(expected_video_sha256):
        raise ShotStatisticsError("trusted_reviews_require_source_binding")
    base = _base(path, allow_partial)
    if expected_video_sha256 is not None and (not _sha(expected_video_sha256)
            or base["source"]["video_sha256"] != expected_video_sha256.lower()):
        raise ShotStatisticsError("review_source_video_mismatch")
    payload = {"schema_version": VERSION, "job_id": job_id, "revision": revision,
               "source": deepcopy(base["source"]), "events": deepcopy(base["events"])}
    if reviews is None:
        reviews = []
    if not isinstance(reviews, list) or len(reviews) > MAX_REVIEWS:
        raise ShotStatisticsError("review_record_limit_exceeded")
    events = {event["id"]: event for event in payload["events"]}
    seen = set()
    for review in reviews:
        historical = None
        if (trusted_reviews and isinstance(review, dict) and review.get("id") not in events
                and not str(review.get("id", "")).startswith("manual-")):
            if (not isinstance(review.get("id"), str)
                    or not re.fullmatch(r"shot-[a-f0-9]{20}", review["id"])
                    or review.get("source") != "trajectory_rule"
                    or not _integer(review.get("frame_index"))
                    or review.get("position_image") is None):
                raise ShotStatisticsError("invalid_persisted_candidate_binding")
            historical = {key: deepcopy(review.get(key)) for key in (
                "id", "kind", "status", "timestamp_ms", "frame_index", "position_image", "source")}
            historical.update(position_court=None,
                reason_codes=["historical_candidate_source_changed"])
            review = deepcopy(review)
            if isinstance(review.get("position_court"), dict) and review["position_court"].get("source") == "calibrated_ground_projection":
                # No current same-frame calibration is attached to this old
                # candidate. Keep explicit manual marks, never a stale map.
                review["position_court"] = None
            payload["events"] = [*events.values(), historical]
        normalized = validate_review(review, payload)
        identifier = normalized["id"]
        if identifier in seen:
            raise ShotStatisticsError("duplicate_review_id")
        seen.add(identifier)
        original = events.get(identifier, historical or {})
        events[identifier] = {**original, **normalized,
            "reason_codes": [*original.get("reason_codes", []), "human_reviewed"],
            "reviewed": True}
        # Subsequent persisted reviews must also see earlier confirmations,
        # otherwise two different IDs at the same instant could count twice.
        payload["events"] = list(events.values())
    payload["events"] = sorted(events.values(), key=lambda e: (e["timestamp_ms"], e["id"]))
    for event in payload["events"]:
        calibration, pixels = event.pop("_calibration", None), event.pop("_pixels", None)
        if event["kind"] != "bounce" or event["status"] != "confirmed":
            event["position_court"] = None
        elif event.get("position_court") is None and calibration is not None and pixels is not None:
            mapped = _transform(calibration[0], *pixels)
            if mapped is not None and -.5 <= mapped[0] <= 1.5 and -.25 <= mapped[1] <= 1.25:
                event["position_court"] = {"x": round(mapped[0], 6), "y": round(mapped[1], 6),
                                           "source": "calibrated_ground_projection"}
        event.setdefault("position_court", None)
        event.setdefault("position_image", None)
        event.setdefault("reviewed", False)
    count = lambda kind, status: sum(e["kind"] == kind and e["status"] == status for e in payload["events"])
    payload["summary"] = {"confirmed_hit_count": count("hit", "confirmed"),
        "confirmed_bounce_count": count("bounce", "confirmed"),
        "mapped_bounce_count": sum(e["kind"] == "bounce" and e["status"] == "confirmed" and e["position_court"] is not None for e in payload["events"]),
        "candidate_hit_count": count("hit", "candidate"), "candidate_bounce_count": count("bounce", "candidate"),
        "dismissed_count": sum(e["status"] == "dismissed" for e in payload["events"]),
        "count_semantics": "review_confirmed_events_not_total_video"}
    payload["status"] = ("unavailable" if not payload["source"]["frame_count"] else
                          "partial" if allow_partial or base["truncated"] else "reviewed" if reviews else "unreviewed")
    payload["limitations_zh"] = [
        "自动标记来自实际球观测的轨迹转折，仅供回放确认，不等于已证实触球或落地。",
        "清晰击球次数只累计已确认记录；未确认、未检出和被遮挡的击球不计入，不能作为整段视频总击球次数。",
        "落点图只显示已确认落地事件；空中球、插值轨迹和外推点不进入落点统计。",
        "没有可靠球场标定时，可回放后手动点选小球场落点；手动位置是人工记录，不是自动测量。",
    ]
    if base["truncated"]:
        payload["limitations_zh"].append("候选数量达到上限，当前列表不完整。")
    return payload
