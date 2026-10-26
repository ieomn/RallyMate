"""Conservative pose-only review windows; not tennis, hand or contact labels.

This UNREGISTERED EXPERIMENT does not modify the racket-supported routes. Its
engineering policy is frozen before replay and deliberately refuses missing
source frames, missing joints and source/selection/camera discontinuities.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from bisect import bisect_left, bisect_right
from fractions import Fraction
import hashlib
import math
import re
import statistics
from typing import Any

VERSION = "pose-only-arm-motion-review-v1.0.0"
EXPERIMENTAL = True
REGISTERED_IN_PRODUCTION = False
FILTER_VERSION = "within-contiguous-segment-coordinate-median-radius1-v1"
POLICY = {
    "semantics": "engineering_hypotheses_not_calibrated_tennis_criteria",
    "minimum_joint_confidence": .35,
    "minimum_samples": 7,
    "duration_ms": [300, 1000],
    "peak_margin_ms": 100,
    "minimum_wrist_net_excursion_torso": .8,
    "minimum_wrist_path_efficiency": .55,
    "minimum_peak_wrist_speed_torso_per_second": 2.,
    "maximum_endpoint_speed_fraction_of_peak": .65,
    "minimum_elbow_net_excursion_torso": .25,
    "minimum_elbow_wrist_net_cosine": .25,
    "minimum_wrist_to_elbow_net_excursion_ratio": 1.1,
    "minimum_image_shoulder_axis_range_deg": 8.,
    "maximum_image_shoulder_axis_speed_deg_per_second": 600.,
    "minimum_peak_wrist_y_below_shoulder_torso": -.2,
    "same_side_peak_suppression_ms": 600,
    "filter_version": FILTER_VERSION,
    "normalization": "camera_reference_points_minus_each_frame_shoulder_midpoint_divided_by_segment_median_full_torso_scale",
    "speed": "central_difference_of_filtered_wrist_using_exact_decoder_pts_seconds",
    "endpoint_speed": "max_of_first_two_and_last_two_internal_central_difference_speeds_respectively",
    "elbow_wrist_cooperation": "net_elbow_dot_net_wrist/(norm_elbow*norm_wrist)>=0.25_and_elbow_net>=0.25_and_wrist_net/elbow_net>=1.1",
    "shoulder_axis": "unwrapped_atan2(right_shoulder-left_shoulder)_image_plane_degrees_not_3D_rotation",
    "missing_data": "split_no_interpolation_no_smoothing_across_missing_source_frame_or_joint",
    "source_scope": "one_video_one_selected_person_one_selection_epoch_one_camera_epoch_one_timebase",
}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(value):
    return isinstance(value, (tuple, list)) and len(value) == 2 and all(_finite(x) for x in value)


def _mid(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _clock(row):
    if (not isinstance(row, Mapping) or any(type(row.get(k)) is not int for k in
            ("frame_index", "pts", "time_base_num", "time_base_den"))
            or row["frame_index"] < 0 or row["time_base_num"] <= 0 or row["time_base_den"] <= 0):
        return None
    return Fraction(row["pts"] * row["time_base_num"], row["time_base_den"])


def _identity(row):
    return (row.get("video_sha256"), row.get("person_track_id"),
            row.get("selection_epoch"), row.get("camera_reference_epoch"))


def _valid_sample(sample, row, side):
    binding = getattr(sample, "source_binding", None)
    if (any(type(row.get(k)) is not int or row[k] < 0 for k in ("person_track_id", "selection_epoch", "camera_reference_epoch"))
            or not isinstance(binding, Mapping) or _clock(binding) != _clock(row)
            or _identity(binding) != _identity(row)
            or binding.get("frame_index") != getattr(sample, "frame_index", None)
            or binding.get("person_track_id") != getattr(sample, "track", None)
            or binding.get("selection_epoch") != getattr(sample, "selection_epoch", None)
            or binding.get("camera_reference_epoch") != getattr(sample, "camera_reference_epoch", None)
            or getattr(sample, "timestamp_source", None) != "decoder_pts"
            or getattr(sample, "camera_compensated", None) is not True
            or getattr(sample, "normalization_basis", None) != "full_torso"
            or not _finite(getattr(sample, "time", None))
            or abs(float(_clock(row)) * 1000 - sample.time) > .51):
        return False
    names = ("left_shoulder", "right_shoulder", "left_hip", "right_hip", side + "_elbow", side + "_wrist")
    points, confidence = getattr(sample, "reference_points", {}), getattr(sample, "point_confidences", {})
    return all(_point(points.get(n)) and _finite(confidence.get(n)) and
               POLICY["minimum_joint_confidence"] <= confidence[n] <= 1 for n in names)


def _segments(samples, timeline, side, diagnostics):
    by_frame = {}
    duplicates = set()
    for sample in samples:
        index = getattr(sample, "frame_index", None)
        if index in by_frame:
            duplicates.add(index)
        by_frame[index] = sample
    segment, previous = [], None
    for row in timeline:
        sample = by_frame.get(row["frame_index"])
        valid = row["frame_index"] not in duplicates and sample is not None and _valid_sample(sample, row, side)
        continuous = (previous is None or row["frame_index"] == previous["frame_index"] + 1
                      and _identity(row) == _identity(previous)
                      and (row["time_base_num"], row["time_base_den"]) == (previous["time_base_num"], previous["time_base_den"]))
        if not valid or not continuous:
            if len(segment) >= POLICY["minimum_samples"]:
                yield segment
            segment = []
        if valid:
            segment.append((sample, row))
        else:
            diagnostics[side + "_unusable_source_frames"] += 1
        previous = row
    if len(segment) >= POLICY["minimum_samples"]:
        yield segment


def _trajectory(segment, side):
    frames = [s.reference_points for s, _ in segment]
    centers = [_mid(p["left_shoulder"], p["right_shoulder"]) for p in frames]
    scales = [max(math.dist(center, _mid(p["left_hip"], p["right_hip"])),
                  .75 * math.dist(p["left_shoulder"], p["right_shoulder"])) for p, center in zip(frames, centers)]
    scale = statistics.median(scales)
    if scale <= 0:
        return None
    raw = {name: [tuple((p[side + "_" + name][axis] - center[axis]) / scale for axis in (0, 1))
                  for p, center in zip(frames, centers)] for name in ("wrist", "elbow")}
    filtered = {name: [tuple(statistics.median(x[axis] for x in points[max(0, i-1):i+2])
                             for axis in (0, 1)) for i in range(len(points))] for name, points in raw.items()}
    times = [_clock(row) for _, row in segment]
    speeds = [None] + [math.dist(filtered["wrist"][i-1], filtered["wrist"][i+1]) / float(times[i+1] - times[i-1])
                       for i in range(1, len(times)-1)] + [None]
    angles = []
    for p in frames:
        a, b = p["left_shoulder"], p["right_shoulder"]
        angle = math.degrees(math.atan2(b[1]-a[1], b[0]-a[0]))
        if angles:
            angle = angles[-1] + (angle-angles[-1]+180) % 360 - 180
        angles.append(angle)
    return raw, filtered, times, speeds, angles, scale, scales


def _already_present(candidate, existing):
    """Never infer a legacy candidate's source identity from coincident times."""
    for other in existing:
        side = other.get("wrist_side", other.get("racket_hand_candidate"))
        if (_identity(other) != _identity(candidate) or side != candidate["wrist_side"]
                or not all(_finite(other.get(k)) for k in ("start_ms", "end_ms"))):
            continue
        if max(other["start_ms"], candidate["start_ms"]) < min(other["end_ms"], candidate["end_ms"]):
            return True
    return False


def build_pose_only_review_candidates(samples: Sequence[Any], timeline: Sequence[Mapping[str, Any]],
                                     existing_candidates: Sequence[Mapping[str, Any]] = ()) -> dict:
    """Return explicit unclassified review candidates or a source-clock refusal.

    The complete timeline must include missing poses and bind each original
    frame to the selected person/epoch/camera; engineering-derived selection
    identity is allowed but must be labelled by the caller. Existing candidates
    suppress only with explicit matching source identities AND wrist side.
    """
    result = {"version": VERSION, "policy": dict(POLICY), "candidates": [],
              "experimental": EXPERIMENTAL, "registered_in_production": REGISTERED_IN_PRODUCTION,
              "status": "review_only", "automatic_scoring_allowed": False,
              "source_grade_supported": False, "original_grade": None, "score": None,
              "diagnostics": {"source_frame_count": len(timeline)}}
    previous = None
    for row in timeline:
        clock = _clock(row)
        if (clock is None or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("video_sha256", "")))
                or any(row.get(k) is not None and (type(row[k]) is not int or row[k] < 0)
                       for k in ("person_track_id", "selection_epoch", "camera_reference_epoch"))
                or previous is not None and (row["frame_index"] != previous["frame_index"] + 1 or clock <= _clock(previous))):
            result.update(status="unavailable", reason="incomplete_or_invalid_original_source_timeline")
            return result
        previous = row
    diagnostics = Counter()
    proposed = []
    for side in ("left", "right"):
        for segment in _segments(samples, timeline, side, diagnostics):
            data = _trajectory(segment, side)
            if data is None:
                continue
            raw, filtered, times, speeds, angles, scale, scales = data
            diagnostics[side + "_continuous_segments"] += 1
            for peak in range(2, len(segment)-2):
                speed = speeds[peak]
                if (speed < POLICY["minimum_peak_wrist_speed_torso_per_second"]
                        or not (speed >= speeds[peak-1] and speed > speeds[peak+1])
                        or filtered["wrist"][peak][1] < POLICY["minimum_peak_wrist_y_below_shoulder_torso"]):
                    continue
                windows = []
                # The other side needs at least the fixed peak margin, so no
                # endpoint farther than duration_max - margin can be eligible.
                # This bounds work by local seconds rather than video length.
                reach = Fraction(POLICY["duration_ms"][1] - POLICY["peak_margin_ms"], 1000)
                starts = range(bisect_left(times, times[peak]-reach), peak-1)
                ends = range(peak+2, bisect_right(times, times[peak]+reach))
                for start in starts:
                    for end in ends:
                        duration = float(times[end]-times[start])*1000
                        if (not POLICY["duration_ms"][0] <= duration <= POLICY["duration_ms"][1]
                                or end-start+1 < POLICY["minimum_samples"]
                                or min(float(times[peak]-times[start]), float(times[end]-times[peak]))*1000 < POLICY["peak_margin_ms"]):
                            continue
                        wrist, elbow = filtered["wrist"][start:end+1], filtered["elbow"][start:end+1]
                        wnet, enet = math.dist(wrist[0], wrist[-1]), math.dist(elbow[0], elbow[-1])
                        path = sum(math.dist(a, b) for a, b in zip(wrist, wrist[1:]))
                        cosine = (sum((elbow[-1][i]-elbow[0][i])*(wrist[-1][i]-wrist[0][i]) for i in (0, 1)) / (enet*wnet)) if enet*wnet else -1
                        local_angles = angles[start:end+1]
                        axis_range = max(local_angles)-min(local_angles)
                        axis_speed = max(abs(angles[i+1]-angles[i])/float(times[i+1]-times[i]) for i in range(start, end))
                        endpoints = (max(speeds[start+1:start+3]), max(speeds[end-2:end]))
                        if (wnet < POLICY["minimum_wrist_net_excursion_torso"] or not path
                                or wnet/path < POLICY["minimum_wrist_path_efficiency"]
                                or enet < POLICY["minimum_elbow_net_excursion_torso"]
                                or cosine < POLICY["minimum_elbow_wrist_net_cosine"]
                                or wnet/enet < POLICY["minimum_wrist_to_elbow_net_excursion_ratio"]
                                or axis_range < POLICY["minimum_image_shoulder_axis_range_deg"]
                                or axis_speed > POLICY["maximum_image_shoulder_axis_speed_deg_per_second"]
                                or max(endpoints) > speed*POLICY["maximum_endpoint_speed_fraction_of_peak"]):
                            continue
                        windows.append((duration, start, end, wnet, enet, wnet/path, cosine, axis_range, endpoints))
                if not windows:
                    continue
                duration, start, end, wnet, enet, efficiency, cosine, axis_range, endpoints = min(windows)
                first, maximum, last = segment[start][1], segment[peak][1], segment[end][1]
                identity = _identity(maximum)
                context_start, context_end = max(0, start-1), min(len(segment), end+2)
                candidate = {"version": VERSION, "candidate_id": hashlib.sha256(f"{VERSION}:{identity}:{side}:{maximum['frame_index']}".encode()).hexdigest()[:20],
                    "route": VERSION, "family": "unclassified_pose_motion_review", "classification": {"label": "unclassified_swing_review"},
                    "status": "manual_review_required", "automatic_scoring_allowed": False, "contact_confirmed": False,
                    "technique_label": None, "dominant_hand": None, "original_grade": None, "proxy_grade": None, "score": None,
                    "video_sha256": identity[0], "person_track_id": identity[1], "selection_epoch": identity[2], "camera_reference_epoch": identity[3],
                    "wrist_side": side, "start_frame": first["frame_index"], "peak_frame": maximum["frame_index"], "end_frame": last["frame_index"],
                    "start_ms": float(times[start])*1000, "peak_ms": float(times[peak])*1000, "end_ms": float(times[end])*1000,
                    "measurements": {"wrist_net_excursion_torso": wnet, "elbow_net_excursion_torso": enet, "wrist_path_efficiency": efficiency,
                        "elbow_wrist_net_cosine": cosine, "shoulder_axis_range_deg": axis_range, "peak_wrist_speed_torso_per_second": speed,
                        "endpoint_wrist_speeds_torso_per_second": endpoints},
                    "evidence": {"filter_version": FILTER_VERSION, "segment_fixed_torso_scale_reference_px": scale,
                        "segment_normalization": {"start_frame": segment[0][1]["frame_index"],
                            "end_frame": segment[-1][1]["frame_index"],
                            "source_frame_indexes": [row["frame_index"] for _, row in segment],
                            "torso_scale_reference_px_values": scales,
                            "scale_definition": "median(max(distance(shoulder_midpoint,hip_midpoint),0.75*shoulder_width)) over every valid frame of this continuous segment"},
                        "filter_context": {"source_frames": [row for _, row in segment[context_start:context_end]],
                            "raw_body_relative_wrist": raw["wrist"][context_start:context_end],
                            "raw_body_relative_elbow": raw["elbow"][context_start:context_end],
                            "window_offset": start-context_start, "window_count": end-start+1,
                            "segment_start_frame": segment[0][1]["frame_index"],
                            "segment_end_frame": segment[-1][1]["frame_index"]},
                        "source_frames": [row for _, row in segment[start:end+1]],
                        "image_shoulder_axis_deg": angles[start:end+1],
                        "raw_body_relative_wrist": raw["wrist"][start:end+1], "filtered_body_relative_wrist": filtered["wrist"][start:end+1],
                        "raw_body_relative_elbow": raw["elbow"][start:end+1], "filtered_body_relative_elbow": filtered["elbow"][start:end+1],
                        "interpolation_used": False, "required_joint_coverage_fraction": 1.0},
                    "limitations": ["No racket observation is required; this is only an arm-motion review window.",
                        "Shoulder/elbow cooperation cannot establish a tennis stroke, technique, dominant hand or contact.",
                        "All conditions are uncalibrated image-plane engineering hypotheses, not source-card grades."]}
                if not _already_present(candidate, existing_candidates):
                    proposed.append(candidate)
    for candidate in sorted(proposed, key=lambda c: -c["measurements"]["peak_wrist_speed_torso_per_second"]):
        if any(_identity(c) == _identity(candidate) and c["wrist_side"] == candidate["wrist_side"]
               and abs(c["peak_ms"]-candidate["peak_ms"]) <= POLICY["same_side_peak_suppression_ms"] for c in result["candidates"]):
            continue
        result["candidates"].append(candidate)
    result["candidates"].sort(key=lambda c: (c["peak_ms"], c["wrist_side"]))
    result["diagnostics"].update(diagnostics)
    return result
