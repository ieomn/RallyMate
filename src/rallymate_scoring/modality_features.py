"""Compact, streaming ball/racket/court evidence for scored motion episodes.

Image velocities remain image velocities. Only an explicitly confirmed bounce
may be mapped as a landing; boxes never become sweet spots or racket normals.
"""
from __future__ import annotations

import bisect
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

import numpy as np

from rallymate_features.coordinates import validated_camera_transform
from rallymate_vision.court_projection import FixedCameraReferenceGuard, _transform
from rallymate_scoring.tactical_analyzer import analyze_tactics, contextual_technique_profile


VERSION = "observed-modalities-v1.0.0"


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _box(detection, width, height):
    box = detection.get("bbox_px")
    if box is None:
        normalized = detection.get("bbox_normalized")
        if isinstance(normalized, (list, tuple)) and len(normalized) == 4 and all(_number(x) for x in normalized):
            box = [normalized[i] * (width if i % 2 == 0 else height) for i in range(4)]
    if (not isinstance(box, (list, tuple)) or len(box) != 4 or not all(_number(x) for x in box)
            or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height):
        return None
    return [float(x) for x in box]


def _keypoints(detection, width, height):
    result = {}
    for point in detection.get("keypoints", []):
        if not isinstance(point, Mapping):
            continue
        name = point.get("id", point.get("name"))
        confidence = point.get("confidence")
        if (not isinstance(name, str) or not name.startswith("RK")
                or not _number(confidence) or not .25 <= confidence <= 1):
            continue
        xy = point.get("xy_px")
        if xy is None and all(_number(point.get(key)) for key in ("x", "y")):
            xy = [point["x"], point["y"]]
        if (isinstance(xy, (list, tuple)) and len(xy) == 2 and all(_number(v) for v in xy)
                and 0 <= xy[0] <= width and 0 <= xy[1] <= height):
            result[name] = [float(v) for v in xy]
    return result


def _compact_record(row, guard, expected_video_sha256):
    frame = row.get("frame") or {}
    if str(frame.get("video_sha256", "")).lower() != expected_video_sha256:
        raise ValueError("multimodal source video SHA-256 mismatch or missing")
    width, height, time, index = [frame.get(key) for key in ("width", "height", "timestamp_ms", "index")]
    if (not all(_number(value) for value in (width, height, time)) or min(width, height) <= 0
            or type(index) is not int or index < 0):
        return None
    camera = row.get("camera_motion") or {}
    guard.observe(camera, width, height)
    calibration = guard.calibration(row.get("court"), width, height)
    transform = validated_camera_transform(camera)
    epoch = camera.get("reference_epoch")
    balls, rackets = [], []
    for detection in row.get("detections", []):
        if not isinstance(detection, Mapping) or detection.get("class_name") not in {"ball", "racket"}:
            continue
        score = detection.get("confidence")
        box = _box(detection, width, height)
        if box is None or not _number(score) or not .25 <= score <= 1:
            continue
        if detection["class_name"] == "racket":
            rackets.append({"box_px": box, "keypoints": _keypoints(detection, width, height), "confidence": score})
            continue
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        center = detection.get("center_normalized")
        if (isinstance(center, (list, tuple)) and len(center) == 2 and all(_number(v) and 0 <= v <= 1 for v in center)):
            x, y = center[0] * width, center[1] * height
        reference = transform @ np.asarray([x, y, 1.]) / max(width, height) if transform is not None else None
        balls.append({"xy_px": [x, y], "reference_xy": reference.tolist() if reference is not None else None,
                      "track_id": detection.get("track_id"), "confidence": float(score)})
    return {"frame_index": index, "timestamp_ms": float(time), "width": width, "height": height,
            "source_sha256": frame.get("video_sha256"), "epoch": epoch,
            "camera_reference_available": transform is not None and type(epoch) is int,
            "balls": balls, "rackets": rackets, "calibration": calibration}


def _ball_summary(rows):
    counts = sum(bool(row["balls"]) for row in rows)
    ambiguous = sum(len(row["balls"]) > 1 for row in rows)
    tracks = defaultdict(list)
    for row in rows:
        if len(row["balls"]) != 1:
            continue
        ball = row["balls"][0]
        if type(ball["track_id"]) is int and ball["reference_xy"] is not None and row["camera_reference_available"]:
            tracks[ball["track_id"]].append((row, ball))
    motion = []
    for track, points in tracks.items():
        samples = []
        for (a, pa), (b, pb) in zip(points, points[1:]):
            dt = b["timestamp_ms"] - a["timestamp_ms"]
            if (not 0 < dt <= 160 or a["epoch"] != b["epoch"] or a["source_sha256"] != b["source_sha256"]
                    or (a["width"], a["height"]) != (b["width"], b["height"])):
                continue
            vector = (np.asarray(pb["reference_xy"]) - np.asarray(pa["reference_xy"])) * 1000 / dt
            samples.append({"source_frames": [a["frame_index"], b["frame_index"]],
                            "timestamp_ms": b["timestamp_ms"], "velocity_xy": vector.tolist(),
                            "speed": float(np.linalg.norm(vector))})
        if samples:
            motion.append({"track_id": track, "sample_count": len(samples),
                           "median_image_speed": float(np.median([item["speed"] for item in samples])),
                           "peak_image_speed": max(item["speed"] for item in samples),
                           "samples": samples})
    return {"observed_frame_count": counts, "total_frame_count": len(rows),
            "coverage": counts / len(rows) if rows else 0., "ambiguous_frame_count": ambiguous,
            "tracks": motion, "speed_unit": "frame_long_edge_per_second",
            "physical_speed_m_s": None, "spin_rpm": None,
            "semantics": "camera_compensated_observed_image_motion_no_interpolation"}


def _event_sha(event):
    return str(event.get("video_sha256", (event.get("evidence") or {}).get("video_sha256", ""))).lower()


def _confirmed_landings(rows, shot_events, expected_video_sha256):
    by_frame = {row["frame_index"]: row for row in rows}
    result = []
    for event in shot_events:
        if (event.get("kind") != "bounce" or event.get("status") != "confirmed"
                or _event_sha(event) != expected_video_sha256):
            continue
        row = by_frame.get(event.get("frame_index"))
        if row is None or event.get("timestamp_ms") != row["timestamp_ms"]:
            continue
        point = event.get("position_court")
        track = (event.get("evidence") or {}).get("ball_track_id")
        if type(track) is not int:
            continue
        balls = [ball for ball in row["balls"] if ball["track_id"] == track]
        if len(balls) != 1:
            continue
        xy = None
        if (isinstance(point, Mapping) and point.get("source") == "manual"
                and event.get("source_authority") == "human_review"
                and all(_number(point.get(key)) for key in ("x", "y"))):
            xy = point["x"], point["y"]
        elif row["calibration"] is not None:
            xy = _transform(row["calibration"][0], *balls[0]["xy_px"])
        if xy is not None:
            result.append({"x": float(xy[0]), "y": float(xy[1]), "timestamp_ms": row["timestamp_ms"],
                           "source_frame": row["frame_index"], "bounce_confirmed": True,
                           "ball_track_id": track, "video_sha256": expected_video_sha256,
                           "camera_reference_epoch": row["epoch"], "event_id": event.get("id")})
    return result


def build_multimodal_assessment(records: Iterable[Mapping], episodes: Sequence[Mapping], *,
                                 expected_video_sha256: str,
                                 shot_events: Sequence[Mapping] = ()) -> dict:
    """Consume frames once, retaining only compact detector evidence.

    ``episode.context_features`` may carry source-verified tactical context.
    Existing episode family/score remains usable when a modality is missing.
    """
    if not isinstance(expected_video_sha256, str) or re.fullmatch(r"[a-fA-F0-9]{64}", expected_video_sha256) is None:
        raise ValueError("expected_video_sha256 must be a SHA-256 digest")
    expected_video_sha256 = expected_video_sha256.lower()
    guard, rows = FixedCameraReferenceGuard(), []
    for source in records:
        row = _compact_record(source, guard, expected_video_sha256)
        if row is not None:
            if rows and row["timestamp_ms"] <= rows[-1]["timestamp_ms"]:
                raise ValueError("multimodal source timestamps must increase")
            rows.append(row)
    times = [row["timestamp_ms"] for row in rows]
    landings = _confirmed_landings(rows, shot_events, expected_video_sha256)
    output, contexts = [], []
    for episode in episodes:
        start, end = episode.get("start_ms"), episode.get("end_ms")
        if not _number(start) or not _number(end) or end <= start:
            continue
        window = rows[bisect.bisect_left(times, start):bisect.bisect_right(times, end)]
        explicit = episode.get("context_features")
        context = {**(dict(explicit) if isinstance(explicit, Mapping) else {}),
                   "episode_id": episode.get("episode_id"), "family": episode.get("family")}
        # Caller-supplied placement must pass source identity/contact checks
        # below as well; it cannot bypass the detector/frame association.
        context.pop("landing_position_court", None)
        context.pop("landing_evidence", None)
        peak = episode.get("peak_ms", (start + end) / 2)
        episode_sha = str(episode.get("video_sha256", episode.get("source_video_sha256",
                          (episode.get("continuity") or {}).get("video_sha256", "")))).lower()
        contact = next((item for item in shot_events
            if item.get("id") == context.get("contact_event_id") and item.get("kind") == "hit"
            and item.get("status") == "confirmed" and _event_sha(item) == expected_video_sha256
            and item.get("person_track_id") == episode.get("person_track_id")
            and type(item.get("person_track_id")) is int
            and _number(item.get("timestamp_ms")) and _number(peak) and abs(item["timestamp_ms"] - peak) <= 150), None)
        if episode_sha == expected_video_sha256 and contact is not None:
            track = (contact.get("evidence") or {}).get("ball_track_id")
            contact_row = next((row for row in window if row["frame_index"] == contact.get("frame_index")
                                and row["timestamp_ms"] == contact["timestamp_ms"]), None)
            next_contact = min((item["timestamp_ms"] for item in shot_events
                if item.get("kind") == "hit" and _event_sha(item) == expected_video_sha256
                and _number(item.get("timestamp_ms")) and item["timestamp_ms"] > contact["timestamp_ms"]
                and (item.get("evidence") or {}).get("ball_track_id") == track), default=end + 2000)
            candidates = [point for point in landings if point["ball_track_id"] == track
                          and contact["timestamp_ms"] < point["timestamp_ms"] < next_contact]
            if contact_row is not None and type(track) is int and candidates:
                landing = candidates[0]
                trajectory = rows[bisect.bisect_left(times, contact["timestamp_ms"]):bisect.bisect_right(times, landing["timestamp_ms"])]
                continuous = (len(trajectory) >= 2 and all(row["epoch"] == contact_row["epoch"]
                    and sum(ball["track_id"] == track for ball in row["balls"]) == 1 for row in trajectory)
                    and all(0 < b["timestamp_ms"] - a["timestamp_ms"] <= 160 for a, b in zip(trajectory, trajectory[1:])))
                proof = context.get("contact_position_evidence") or {}
                ground_bound = (str(proof.get("video_sha256", "")).lower() == expected_video_sha256
                    and proof.get("source_frame") == contact["frame_index"]
                    and proof.get("person_track_id") == episode.get("person_track_id")
                    and proof.get("source") in {"observed_ground_contact", "human_review"})
                if continuous and ground_bound:
                    context.update(landing_position_court={"x": landing["x"], "y": landing["y"]}, landing_evidence=landing)
        contexts.append(context)
        names = sorted({name for row in window for racket in row["rackets"] for name in racket["keypoints"]})
        racket_frames = sum(bool(row["rackets"]) for row in window)
        output.append({"episode_id": episode.get("episode_id"), "start_ms": start, "end_ms": end,
                       "ball": _ball_summary(window),
                       "racket": {"observed_frame_count": racket_frames, "keypoint_ids_observed": names,
                                  "sweet_spot_available": "RK005" in names, "throat_available": "RK013" in names,
                                  "racket_face_normal_available": False,
                                  "semantics": "detector_boxes_and_explicit_keypoints_never_box_derived_anatomy"},
                       "court": {"calibrated_frame_count": sum(row["calibration"] is not None for row in window),
                                 "confirmed_landings": [point for point in landings if start <= point["timestamp_ms"] <= end]},
                       "context_profile": contextual_technique_profile(context)})
    tactics = analyze_tactics(contexts)
    for episode, tactical in zip(output, tactics["shots"]):
        episode["tactics"] = tactical
    return {"version": VERSION, "source_video_sha256": expected_video_sha256,
            "frame_count": len(rows), "per_episode": output, "tactics": tactics,
            "summary": {"ball_observed_frames": sum(bool(row["balls"]) for row in rows),
                        "racket_observed_frames": sum(bool(row["rackets"]) for row in rows),
                        "court_calibrated_frames": sum(row["calibration"] is not None for row in rows),
                        "confirmed_landing_count": len(landings)},
            "limitations": ["Image ball speed is not physical ball speed.",
                            "Spin and racket surface normal require independent observations.",
                            "Airborne ball homography is not a landing measurement."],
            "missing_modality_disables_pose_score": False}
