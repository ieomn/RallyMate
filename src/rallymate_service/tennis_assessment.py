"""Versioned action-quality rubric over saved, source-bound native 3D poses.

This is an engineering rubric, not the legacy indicator grade. It measures joint
geometry in a per-frame body basis, so a common camera rigid transform cannot
become an athlete's motion. It never uses identity labels, pose confidence, ball
contact assumptions, absolute camera-axis velocity, or a model inference call.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from rallymate_service.swing_kinematics import _read_rows, _signature
from rallymate_service.tennis_coaching import coaching_for_metric, strength_for_metric
from rallymate_service.tennis_profiles import SERVE_METRICS, SERVE_RUBRIC, apply_serve_profile
from rallymate_service.tennis_subjects import subject_reference, select_report_subject, reviewed_subject_timeline
from rallymate_service.tennis_source_motion import source_motion_by_frame, window_source_motion, verified_action_context, bind_reviewed_directions
from rallymate_service.tennis_motion_patterns import analyze_motion_pattern, PATTERN_METRICS, TECHNIQUES, RUBRIC as PATTERN_RUBRIC
from rallymate_vision.sam3d_evaluation import COORDINATES_3D, _binding_error, _time
from rallymate_scoring.stroke_candidates import detect_stroke_candidates, _normalization

VERSION = "tennis-action-assessment-v1.4.0"
RUBRIC_VERSION = "tennis-action-rubric-v1.3.0"
DIMENSIONS = (("movement", "移动支撑"), ("preparation", "准备引拍"),
              ("coordination", "动作协调"), ("striking", "击球姿态"),
              ("follow_through", "随挥恢复"))
# low, plateau start, plateau end, high: continuous bounded trapezoid.
METRICS = {
    "movement": (("knee_load", "下肢蓄势参与", "deg", "准备阶段有明确屈膝，为后续伸展做准备"),
                 ("support_control", "准备站距", "hip_width", "双足自然分开形成准备站距，不以越宽为越好"),
                 ("leg_extension_continuity", "下肢伸展连贯", "ratio", "临近挥拍时同一腿连贯伸展，减少反复屈伸，不追求锁直膝盖")),
    "preparation": (("coil", "转体蓄势", "deg", "肩髋保持适度相对转动"),
                    ("preparation_sync", "引拍协同", "phase_fraction", "手臂引拍与身体准备接近同步")),
    "coordination": (("release_sequence", "伸展释放衔接", "phase_fraction", "下肢伸展、躯干释放与主挥拍连续衔接"),
                     ("coil_release", "相对转动释放", "ratio", "准备后的肩髋分离在挥拍中自然释放")),
    "striking": (("head_control", "头颈控制", "torso", "主挥拍期间头颈相对躯干保持平稳"),
                 ("arm_space", "手臂伸展空间", "arm_length", "主挥拍时保留舒展的手臂空间")),
    "follow_through": (("follow_path", "随挥延展", "ratio", "主挥拍后继续完成手臂运动"),
                       ("recovery_control", "恢复衔接", "ratio", "随挥后保持动作连续、减少突然往返，并重新屈膝准备")),
}
DRILLS = {
    "movement": ("屈膝准备与支撑", "准备时先放低重心，再完成挥拍，保持自然站距。", "做 2 组每组 10 次：小步调整、屈膝准备、挥拍、回到准备姿势。", "用相同机位录 10 次连续挥拍，对比准备屈膝和支撑控制两项是否提高。"),
    "preparation": ("身体与手臂一起准备", "让肩髋转动与手臂引拍衔接，避免只把手臂向后拉。", "做 2 组每组 10 次慢速空挥：双手在身前起始，身体转动带动引拍，再连续挥出。", "录 10 次同类挥拍，对比转体蓄势与引拍协同，检查准备是否连续。"),
    "coordination": ("连续完成伸展与转动", "从屈膝准备开始，把伸展、躯干释放和手臂挥出连成一次动作。", "做 2 组每组 10 次半速空挥：屈膝准备、自然伸展转动、挥出，逐步减少中途停顿。", "录 10 次同类挥拍，比较伸展释放衔接和相对转动释放，保持完整随挥。"),
    "striking": ("保持头颈与手臂空间", "挥拍时保持头颈平稳，让手臂在身体侧前方自然展开。", "做 2 组每组 10 次半速挥拍：保持身体平衡，手臂舒展挥出，完成随挥后再停。", "录 10 次同类挥拍，对比头颈控制和手臂伸展空间，不强行锁直肘部。"),
    "follow_through": ("完成随挥再恢复准备", "挥出后让手臂继续运行，再用小步回到下一次准备。", "做 2 组每组 10 次连续空挥：挥出、自然随挥、调整脚步、重新屈膝，不在挥出后突然刹住。", "录 10 次连续挥拍，检查随挥延展和恢复控制是否提高，并保持下一次准备。"),
}


def _map(value):
    return value if isinstance(value, Mapping) else {}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(record, name):
    p = _map(record.get("keypoints_3d")).get(name)
    return np.asarray(p, dtype=float) if isinstance(p, (list, tuple)) and len(p) == 3 and all(_finite(v) for v in p) else None


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-8 else None


def _angle(a, b, c):
    if any(v is None for v in (a, b, c)):
        return None
    u, v = _unit(a - b), _unit(c - b)
    return None if u is None or v is None else round(float(np.degrees(np.arccos(np.clip(u @ v, -1, 1)))), 9)


def _mean(values):
    values = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.mean(values)) if values else None


def _ramp(value, low, high):
    return 100.0 * float(np.clip((value - low) / (high - low), 0, 1))


def _band(value, low, good_low, good_high, high):
    return min(_ramp(value, low, good_low), 100 - _ramp(value, good_high, high))


def _geometry(record):
    p = {name: _point(record, name) for name in (
        "left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_elbow", "right_elbow",
        "left_wrist", "right_wrist", "left_knee", "right_knee", "left_ankle", "right_ankle", "left_ear", "right_ear", "nose")}
    if any(p[k] is None for k in ("left_shoulder", "right_shoulder", "left_hip", "right_hip")):
        return None
    hip = (p["left_hip"] + p["right_hip"]) / 2
    shoulder = (p["left_shoulder"] + p["right_shoulder"]) / 2
    torso = float(np.linalg.norm(shoulder - hip))
    up = _unit(shoulder - hip)
    if up is None:
        return None
    hip_axis = p["right_hip"] - p["left_hip"]
    lateral = _unit(hip_axis - up * (hip_axis @ up))
    if lateral is None or torso <= 1e-8:
        return None
    basis = np.stack((lateral, up, np.cross(lateral, up)))
    # Stable tie-breaking for periodic actions under numerically equivalent camera
    # transforms. Precision is far finer than native model coordinates.
    local = {name: np.round(basis @ (point - hip) / torso, 9) if point is not None else None for name, point in p.items()}
    shoulder_axis = local["right_shoulder"] - local["left_shoulder"]
    # Axes are undirected; label endpoint representations cannot imply 180 deg.
    separation = round(math.degrees(math.atan2(abs(shoulder_axis[2]), abs(shoulder_axis[0]))), 8)
    signed_separation = round(math.degrees(math.atan2(shoulder_axis[2], shoulder_axis[0])), 8)
    knees = {side: 180 - a if (a := _angle(p[f"{side}_hip"], p[f"{side}_knee"], p[f"{side}_ankle"])) is not None else None for side in ("left", "right")}
    knee = _mean(list(knees.values()))
    support, stance_width_hips = None, None
    if local["left_ankle"] is not None and local["right_ankle"] is not None:
        left, right = local["left_ankle"][[0, 2]], local["right_ankle"][[0, 2]]
        line = right - left
        length_squared = float(line @ line)
        projection = float(np.clip(-(left @ line) / length_squared, 0, 1)) if length_squared > 1e-8 else 0.
        # Movement along the support span is weight transfer, not instability.
        # This is a local geometric support proxy, not a measured center of mass.
        support = float(np.linalg.norm(left + projection * line))
        hip_width = float(np.linalg.norm(local["right_hip"] - local["left_hip"]))
        if hip_width > 1e-8:
            stance_width_hips = float(np.linalg.norm(local["right_ankle"] - local["left_ankle"]) / hip_width)
    head = None
    if local["left_ear"] is not None and local["right_ear"] is not None:
        head = (local["left_ear"] + local["right_ear"]) / 2 - np.array([0., 1., 0.])
    elif local["nose"] is not None:
        head = local["nose"] - np.array([0., 1., 0.])
    out = {"separation": separation, "signed_separation": signed_separation, "knee": knee, "support": support, "head": head,
           "torso": torso, "local": local, "stance_width_hips": stance_width_hips,
           "left_knee_flexion": knees["left"], "right_knee_flexion": knees["right"]}
    for side in ("left", "right"):
        a, b, c = (p[f"{side}_{joint}"] for joint in ("shoulder", "elbow", "wrist"))
        length = np.linalg.norm(a - b) + np.linalg.norm(b - c) if b is not None and c is not None else 0
        out[f"{side}_reach"] = round(float(np.linalg.norm(a - c) / length), 9) if length > 1e-8 else None
    return out


def _segments(timeline, records, sha):
    selected_counts = Counter(row.get("source_frame_index") for row in timeline)
    by_key = {}
    for row in records:
        by_key.setdefault((row.get("frame_index"), row.get("person_track_id")), []).append(row)
    origins = [float(_time(r) * 1000) - r["relative_timestamp_ms"] for r in records
               if _binding_error(r) is None and _finite(r.get("relative_timestamp_ms"))]
    origin = float(np.median(origins)) if origins else 0.
    segments, current, reasons = [], [], Counter()
    for row in timeline:
        frame, track = row.get("source_frame_index"), row.get("source_track_id")
        candidates = by_key.get((frame, track), [])
        record = candidates[0] if len(candidates) == 1 else None
        reason = None
        if (type(frame) is not int or selected_counts[frame] != 1 or type(track) is not int
                or type(row.get("primary_player_id")) is not int or row.get("selection_status") != "selected"
                or row.get("identity_ambiguous") is not False):
            reason = "primary_subject_unverified"
        elif record is None:
            reason = "native_record_missing_or_duplicate"
        elif (_binding_error(record) is not None or record.get("status") != "ok"
              or str(record.get("video_sha256", "")).lower() != sha
              or record.get("coordinate_system_3d") != COORDINATES_3D or record.get("units_3d") != "model_units"
              or any(key in row and row[key] != record.get(key) for key in ("selection_epoch", "camera_reference_epoch"))
              or record.get("relative_timestamp_ms", record.get("timestamp_ms")) != row.get("timestamp_ms")
              or abs(float(_time(record) * 1000) - origin - row.get("timestamp_ms", -1e9)) > 1.01):
            reason = "source_binding_mismatch"
        subject = subject_reference(row, record) if reason is None else None
        if reason is None and subject is None:
            reason = "subject_identity_unverified"
        g = _geometry(record) if reason is None else None
        if reason is None and g is None:
            reason = "torso_geometry_missing"
        if reason:
            reasons[reason] += 1
            if current:
                segments.append(current)
            current = []
            continue
        g.update(time=float(_time(record) * 1000) - origin, frame=frame, track=track, person=row["primary_player_id"],
                 reference=(record["selection_epoch"], record["camera_reference_epoch"]), wrist_discontinuities=(), wrist_unavailable=(), **subject)
        if current:
            previous = current[-1]
            connected = (g["frame"] == previous["frame"] + 1 and 0 < g["time"] - previous["time"] <= 160
                         and (g["track"], g["person"], g["reference"], g["subject_id"]) == (previous["track"], previous["person"], previous["reference"], previous["subject_id"]))
            # Torso/source breaks affect every measurement. A wrist jump only
            # invalidates that joint; the chosen active wrist is checked later.
            jump = not .65 <= g["torso"] / previous["torso"] <= 1.55
            if not connected or jump:
                segments.append(current)
                current = []
                reasons["geometry_discontinuity" if jump else "source_continuity_break"] += 1
            else:
                affected = []
                for side in ("left", "right"):
                    a, b = g["local"][f"{side}_wrist"], previous["local"][f"{side}_wrist"]
                    if a is not None and b is not None and np.linalg.norm(a - b) > 1.5:
                        affected.append(side)
                g["wrist_discontinuities"] = tuple(affected)
                if affected:
                    g["wrist_unavailable"] = tuple(affected)
                    previous["wrist_unavailable"] = tuple(sorted(set(previous["wrist_unavailable"]) | set(affected)))
                    reasons["wrist_geometry_discontinuity"] += 1
        current.append(g)
    if current:
        segments.append(current)
    return segments, reasons


def _joint_scoped_window(rows, observations):
    """Omit both endpoints of an uncertain wrist pair, without filling gaps.

    Selection must already reject an active-wrist discontinuity. Other joints
    retain their observations, and the original source artifacts stay intact.
    """
    missing = {}
    for index, row in enumerate(rows):
        for side in row.get("wrist_unavailable", ()):
            missing.setdefault(row["frame"], set()).add(side + "_wrist")
        for side in row.get("wrist_discontinuities", ()):
            for endpoint in (index - 1, index) if index else (index,):
                missing.setdefault(rows[endpoint]["frame"], set()).add(side + "_wrist")
    if not missing:
        return rows, observations, {}
    scoped_rows = []
    for row in rows:
        names = missing.get(row["frame"], set())
        if names:
            local = {**row["local"], **{name: None for name in names}}
            row = {**row, "local": local, **{name.replace("_wrist", "_reach"): None for name in names}}
        scoped_rows.append(row)
    scoped_observations = [{**row, "points": {name: point for name, point in row["points"].items()
                                               if name not in missing.get(row["frame"], set())}}
                           for row in observations]
    return scoped_rows, scoped_observations, {frame: sorted(names) for frame, names in missing.items()}


def _smooth(values):
    """Three-observation median, never fills a missing central observation."""
    data = np.asarray(values, dtype=float)
    out = data.copy()
    for i in range(1, len(data) - 1):
        block = data[i - 1:i + 2]
        if np.isfinite(block).all():
            out[i] = np.median(block, axis=0)
    return out


def _wrist_series(segment, side):
    # A known bad neighbor outside a later subwindow must not influence its
    # first/last valid observation through the three-observation median.
    return _smooth([row["local"][f"{side}_wrist"] if row["local"][f"{side}_wrist"] is not None
                    and side not in (*row.get("wrist_unavailable", ()), *row.get("wrist_discontinuities", ()))
                    else [np.nan] * 3 for row in segment])


def _local_velocity(points, times_ms):
    """Centered local slope with five observations and the actual decoder clock.

    A single adjacent-coordinate difference can select a model joint spike or a
    fast return during follow-through. A supported local trajectory derivative
    preserves a sustained swing while avoiding that one-frame extremum.
    """
    points, times_ms = np.asarray(points, dtype=float), np.asarray(times_ms, dtype=float)
    if points.ndim == 1:
        points = points[:, None]
    velocities = np.full_like(points, np.nan)
    for i in range(len(points)):
        lo, hi = max(0, i - 2), min(len(points), i + 3)
        clock, window = times_ms[lo:hi], points[lo:hi]
        if len(clock) < 3 or not np.isfinite(window).all() or not (np.diff(clock) > 0).all():
            continue
        dt = (clock - np.mean(clock)) / 1000
        denominator = float(dt @ dt)
        if denominator > 1e-12:
            velocities[i] = (dt[:, None] * window).sum(axis=0) / denominator
    return velocities


def _local_speed(points, times_ms):
    return np.round(np.linalg.norm(_local_velocity(points, times_ms), axis=1), 7)


def _angular_continuity(angles_deg, times_ms):
    """Excess velocity variation, not the amount of a normal recovery turn."""
    angles = np.degrees(np.unwrap(2 * np.radians(np.asarray(angles_deg, dtype=float))) / 2)
    velocity = _local_velocity(_smooth(angles), times_ms)[:, 0]
    duration = (times_ms[-1] - times_ms[0]) / 1000
    if len(velocity) < 5 or duration <= 0 or not np.isfinite(velocity).all():
        return None
    total_variation = float(np.sum(np.abs(np.diff(velocity))))
    # One smooth acceleration/deceleration envelope is normal. Penalize excess
    # repeated reversals; retain a small angle floor against near-static noise.
    excess = max(0., total_variation - 2 * float(np.max(np.abs(velocity))))
    roughness = excess / max(float(np.mean(np.abs(velocity))), 3. / duration)
    return roughness, 100 - _ramp(roughness, .5, 4.)


def _summary_episodes(summary):
    families = _map(_map(_map(summary.get("action_recognition")).get("motion_analysis")).get("families"))
    by_id, conflicts = {}, set()
    for family, data in families.items():
        for row in _map(data).get("episodes", []):
            if not isinstance(row, Mapping) or not isinstance(row.get("episode_id"), str):
                continue
            reduced = {key: row.get(key) for key in ("episode_id", "start_ms", "peak_ms", "end_ms", "person_track_id", "phases", "racket_hand", "motion_wrist", "racket_hand_semantics", "classification")}
            reduced["hand_evidence"] = _map(_map(row.get("evidence")).get("hand_evidence"))
            reduced["racket_hand_evidence"] = _map(row.get("racket_hand_evidence"))
            reduced["paired_wrist_acceleration"] = _map(_map(row.get("evidence")).get("paired_wrist_acceleration"))
            reduced["continuity"] = _map(_map(row.get("evidence")).get("process_continuity"))
            reduced["stroke_classification"] = _map(_map(row.get("evidence")).get("stroke_classification"))
            reduced["return_context"] = _map(_map(row.get("evidence")).get("return_context"))
            reduced["phase_authority"] = row.get("phase_authority")
            reduced["phase_ambiguity"] = _map(row.get("phase_ambiguity"))
            reduced["measurement_provenance"] = _map(row.get("measurement_provenance"))
            reduced["family"] = family
            identifier = row["episode_id"]
            if identifier in by_id and by_id[identifier] != reduced:
                conflicts.add(identifier)
            by_id[identifier] = reduced
    return [e for key, e in by_id.items() if key not in conflicts]


def _has_return_context(episode):
    """A family/label or reviewed technique alone never establishes a return."""
    context = _map(episode.get("return_context"))
    start, end = context.get("incoming_start_ms"), context.get("incoming_end_ms")
    peak = episode.get("peak_ms")
    return (episode.get("family") == "return" and isinstance(context.get("opponent_serve_id"), str)
            and bool(context["opponent_serve_id"]) and type(context.get("server_track_id")) is int
            and context["server_track_id"] >= 0 and context["server_track_id"] != episode.get("person_track_id")
            and type(context.get("ball_track_id")) is int and context["ball_track_id"] >= 0 and type(context.get("ball_observations")) is int
            and context["ball_observations"] >= 3 and all(_finite(v) for v in (start, end, peak))
            and 0 <= start < end and peak - 350 <= end <= peak + 100)


def _verified_return_aliases(episodes):
    by_id = {e.get("episode_id"): e for e in episodes}
    aliases = {}
    for episode in episodes:
        identifier = episode.get("episode_id")
        if not _has_return_context(episode) or not isinstance(identifier, str) or not identifier.endswith("-return"):
            continue
        original = by_id.get(identifier[:-7])
        if (original and original.get("family") == "baseline"
                and all(original.get(k) == episode.get(k) for k in
                        ("start_ms", "peak_ms", "end_ms", "person_track_id", "phases", "continuity"))):
            aliases[original["episode_id"]] = identifier
    return aliases


def _motion_wrist(episode):
    """The legacy racket_hand field names the measured wrist, not grip ownership."""
    side = episode.get("motion_wrist", episode.get("racket_hand"))
    # Reduced historical rows may contain a null field added by the adapter.
    if side is None and episode.get("racket_hand_semantics") is None:
        side = episode.get("racket_hand")
    if (side not in ("left", "right") or episode.get("racket_hand") != side
            or episode.get("motion_wrist") is not None
            and episode.get("racket_hand_semantics") != "motion_wrist_legacy_alias"):
        return None
    return side


def _source_phase_authority(episode):
    """This authorizes phase timing only, never model confidence or real contact."""
    proof = _map(episode.get("measurement_provenance"))
    phases = {p.get("phase"): p for p in episode.get("phases", []) if isinstance(p, Mapping)}
    prep, acceleration, follow = (_map(phases.get(name)) for name in ("preparation", "acceleration", "follow_through"))
    return (episode.get("phase_authority") == "source_bound_primary_motion"
            and proof.get("pose_source") == "sam3d" and proof.get("observation_semantics") == "monocular_model_estimate"
            and proof.get("assessment_status") == "measurement_only" and proof.get("scoring_allowed") is False
            and type(proof.get("model_estimate_sample_count")) is int and proof["model_estimate_sample_count"] >= 7
            and _motion_wrist(episode) in {"left", "right"}
            and all(p.get("status") == "measured" for p in (prep, acceleration, follow))
            and prep.get("start_ms") == episode.get("start_ms") and prep.get("end_ms") == acceleration.get("start_ms")
            and acceleration.get("end_ms") == episode.get("peak_ms") == follow.get("start_ms")
            and follow.get("end_ms") == episode.get("end_ms"))


def _bound_phase_ambiguity(episode, segments, sha):
    """Describe an upstream unscorable sequence only with its bound evidence."""
    ambiguity, proof = _map(episode.get("phase_ambiguity")), _map(episode.get("measurement_provenance"))
    reason = ambiguity.get("reason_code")
    continuity = _map(episode.get("continuity"))
    peak = ambiguity.get("candidate_motion_peak_ms")
    phases = {p.get("phase"): p for p in episode.get("phases", []) if isinstance(p, Mapping)}
    if (reason not in ("unresolved_racket_hand_with_competing_arm_sequence", "external_peak_motion_geometry_unverified")
            or episode.get("phase_authority") != "source_bound_primary_motion"
            or episode.get("family") != "baseline" or proof.get("pose_source") != "sam3d"
            or proof.get("observation_semantics") != "monocular_model_estimate" or proof.get("assessment_status") != "measurement_only"
            or proof.get("scoring_allowed") is not False or type(proof.get("model_estimate_sample_count")) is not int
            or proof["model_estimate_sample_count"] < 7 or continuity.get("pose_source") != "sam3d"
            or str(continuity.get("video_sha256", "")).lower() != sha
            or ambiguity.get("candidate_wrist") not in ("left", "right")
            or ambiguity.get("candidate_wrist") != _motion_wrist(episode)
            or not _finite(peak) or peak != episode.get("peak_ms")
            or set(phases) != {"preparation", "acceleration", "follow_through"}
            or any(p.get("status") != "unavailable" or p.get("reason_code") != reason
                   or p.get("start_ms") is not None or p.get("end_ms") is not None for p in phases.values())):
        return None
    observed_times = [peak]
    if reason == "unresolved_racket_hand_with_competing_arm_sequence":
        competing = ambiguity.get("competing_motion_peak_ms")
        if (ambiguity.get("competing_wrist") not in ("left", "right")
                or ambiguity.get("competing_wrist") == ambiguity.get("candidate_wrist")
                or not _finite(competing) or competing <= peak):
            return None
        observed_times.append(competing)
    else:
        start, end = ambiguity.get("candidate_start_ms"), ambiguity.get("candidate_end_ms")
        window_start, window_end = ambiguity.get("motion_window_start_ms"), ambiguity.get("motion_window_end_ms")
        geometry = _map(ambiguity.get("local_sweep_geometry"))
        count = geometry.get("tested_window_count")
        nonnegative = [geometry.get(key) for key in ("horizontal_excursion_torso_units", "vertical_range_torso_units", "path_torso_units")]
        if (not all(_finite(v) for v in (start, end)) or not 0 <= start < end or start <= peak <= end
                or not all(_finite(v) for v in (window_start, window_end)) or not window_start < peak < window_end
                or window_start != episode.get("start_ms") or window_end != episode.get("end_ms")
                or geometry.get("status") != "not_supported" or type(count) is not int or count < 0
                or type(geometry.get("crosses_body_midline")) is not bool
                or not all(_finite(v) and v >= 0 for v in nonnegative)
                or not _finite(geometry.get("minimum_wrist_y_torso_units"))):
            return None
        # These are diagnostics of the full reported window. The detector tests
        # its original local subwindows; do not re-grade summary geometry here.
        observed_times.extend([start, end, window_start, window_end])
    bound = any(segment and segment[0]["track"] == episode.get("person_track_id")
               and segment[0]["reference"] == (continuity.get("source_selection_epoch"), continuity.get("camera_reference_epoch"))
               and all(any(abs(row["time"] - time) <= 1.01 for row in segment) for time in observed_times) for segment in segments)
    return reason if bound else None


def _paired_hand_support(episode, rows, observations, sha):
    """Recompute bilateral phase support from the bound original 2D observations.

    A technique label or a serialized 'supported' flag is not sufficient. The
    motion wrist remains the phase reference; this only permits the reviewed
    grip hand to supply hand-specific measurements of the same two-hand action.
    """
    from rallymate_scoring.paired_motion import paired_wrist_phase_evidence

    proof = _map(episode.get("paired_wrist_acceleration"))
    continuity = _map(episode.get("continuity"))
    reviewed = _map(episode.get("verified_action_context"))
    phases = {p.get("phase"): p for p in episode.get("phases", []) if isinstance(p, Mapping)}
    acceleration = _map(phases.get("acceleration"))
    motion, primary = _motion_wrist(episode), reviewed.get("racket_hand")
    start, end = acceleration.get("start_ms"), acceleration.get("end_ms")
    def missing(reason):
        return {"version": "paired-wrist-phase-v1", "status": "unavailable", "reason_code": reason}
    if (episode.get("motion_wrist") != motion or not _source_phase_authority(episode)
            or motion == primary or primary not in ("left", "right")
            or reviewed.get("technique") not in
               ("baseline_two_hand_backhand", "return_two_hand_backhand")
            or reviewed.get("source_selection_epoch") != continuity.get("source_selection_epoch")
            or reviewed.get("camera_reference_epoch") != continuity.get("camera_reference_epoch")
            or proof.get("version") != "paired-wrist-phase-v1" or proof.get("status") != "supported"
            or proof.get("motion_wrist") != motion or proof.get("paired_wrist") != primary
            or proof.get("start_ms") != start or proof.get("end_ms") != end):
        return missing("two_hand_phase_role_contract_unverified")
    source = _map(proof.get("source"))
    expected = {"person_track_id": episode.get("person_track_id"),
                "selection_epoch": continuity.get("selection_epoch"),
                "source_selection_epoch": continuity.get("source_selection_epoch"),
                "camera_reference_epoch": continuity.get("camera_reference_epoch"),
                "normalization_basis": continuity.get("normalization_basis"),
                "pose_source": "sam3d", "observation_semantics": "monocular_model_estimate"}
    if (any(source.get(key) != value for key, value in expected.items())
            or str(source.get("video_sha256", "")).lower() != sha):
        return missing("two_hand_phase_source_binding_unverified")
    basis = expected["normalization_basis"]
    normalized_side = {"full_torso": None, "left_shoulder_left_torso": "left", "right_shoulder_right_torso": "right"}
    if basis not in normalized_side or not rows:
        return missing("two_hand_phase_normalization_unverified")
    endpoints = [min(range(len(rows)), key=lambda i: abs(rows[i]["time"] - timestamp)) for timestamp in (start, end)]
    if any(abs(rows[i]["time"] - timestamp) > 1.01 for i, timestamp in zip(endpoints, (start, end))):
        return missing("two_hand_phase_endpoints_unobserved")
    selected = rows[endpoints[0]:endpoints[1] + 1]
    camera, context = window_source_motion(selected, observations or {}, sha)
    if not context.get("source_binding_verified"):
        return missing("two_hand_phase_source_observations_missing")
    samples = []
    for row, observed in zip(selected, camera):
        if row.get("wrist_unavailable") or row.get("wrist_discontinuities"):
            return missing("two_hand_phase_wrist_discontinuity")
        norm = _normalization(observed["points"], normalized_side[basis])
        if norm is None or norm[2] != basis:
            return missing("two_hand_phase_normalization_unverified")
        center, scale, _ = norm
        binding = {"video_sha256": sha, "frame_index": row["frame"], "person_track_id": row["track"],
                   "selection_epoch": row["reference"][0], "camera_reference_epoch": row["reference"][1],
                   "pts": observed["source_pts"], "time_base_num": observed["time_base_num"],
                   "time_base_den": observed["time_base_den"]}
        binding["timestamp_ms"] = binding["pts"] * binding["time_base_num"] / binding["time_base_den"] * 1000
        samples.append(SimpleNamespace(time=row["time"], frame_index=row["frame"], track=row["track"],
                       selection_epoch=expected["selection_epoch"], camera_reference_epoch=row["reference"][1],
                       center=center, scale=scale, normalization_basis=basis, pose_source="sam3d",
                       observation_semantics="monocular_model_estimate", source_binding=binding,
                       points={name: ((point[0] - center[0]) / scale, (point[1] - center[1]) / scale)
                               for name, point in observed["points"].items()}))
    bindings = proof.get("sample_binding")
    if not isinstance(bindings, list) or len(bindings) != len(samples):
        return missing("two_hand_phase_sample_binding_mismatch")
    for supplied, sample in zip(bindings, samples):
        if not isinstance(supplied, Mapping):
            return missing("two_hand_phase_sample_binding_mismatch")
        binding = _map(supplied.get("source_binding"))
        timestamp = supplied.get("timestamp_ms")
        source_time = binding.get("timestamp_ms")
        if (supplied.get("frame_index") != sample.frame_index or not _finite(timestamp)
                or abs(timestamp - sample.time) > 1.01
                or not _finite(source_time) or abs(source_time - sample.source_binding["timestamp_ms"]) > 1.01
                or str(binding.get("video_sha256", "")).lower() != sha
                or any(binding.get(k) != sample.source_binding[k] for k in
                       ("frame_index", "person_track_id", "selection_epoch", "camera_reference_epoch", "pts", "time_base_num", "time_base_den"))):
            return missing("two_hand_phase_sample_binding_mismatch")
    recomputed = paired_wrist_phase_evidence(samples, start_ms=samples[0].time, end_ms=samples[-1].time, motion_wrist=motion)
    return {**recomputed, "verification_method": "recomputed_from_source_bound_native_image_observations",
            "declared_phase_start_ms": start, "declared_phase_end_ms": end}


def _hand_roles(episode, side, paired=None):
    reviewed = _map(episode.get("verified_action_context"))
    primary = reviewed.get("racket_hand")
    provenance = {"method": reviewed.get("method"), "reviewer": reviewed.get("reviewer")}
    if primary not in ("left", "right"):
        primary = _map(episode.get("hand_evidence")).get("hand")
        provenance = {"method": "source_bound_racket_association"} if primary in ("left", "right") else {"method": "unverified"}
    if primary not in ("left", "right"):
        primary = None
    motion = _motion_wrist(episode) if _source_phase_authority(episode) else side
    return {"version": "tennis-hand-roles-v1", "motion_wrist": motion, "measurement_hand": side,
            "primary_racket_hand": primary, "primary_hand_provenance": provenance,
            "relationship": "verified_two_hand" if paired else "same_side" if primary == motion else "motion_only",
            "paired_wrist_evidence": paired}


def _bound_windows(segments, episodes, sha, rejection_counts=None, candidate_outcomes=None, *, source_observations=None):
    """An independent phase candidate bounds scoring; any-arm peaks alone do not.

    A single detected action is counted once, never once per arm/peak/fragment.
    Classification text and old FS condition scores play no part in selection.
    """
    out, seen_intervals = [], []
    rejected = rejection_counts if rejection_counts is not None else Counter()
    outcomes = candidate_outcomes if candidate_outcomes is not None else []
    def reject(candidate, code):
        rejected[code] += 1
        outcomes.append({"episode_id": candidate.get("episode_id"), "start_ms": candidate.get("start_ms"),
                         "end_ms": candidate.get("end_ms"), "person_track_id": candidate.get("person_track_id"),
                         "status": "rejected", "reason_codes": [code]})
    ordered = []
    return_aliases = _verified_return_aliases(episodes)
    for candidate in sorted(episodes, key=lambda e: (e.get("start_ms", 0), e.get("episode_id", ""))):
        if candidate.get("episode_id") in return_aliases:
            reject(candidate, "same_action_represented_by_verified_return")
            continue
        if candidate.get("family") == "return" and not _has_return_context(candidate):
            reject(candidate, "return_context_unverified")
            continue
        phase_ambiguity = _bound_phase_ambiguity(candidate, segments, sha)
        if phase_ambiguity:
            reject(candidate, phase_ambiguity)
            continue
        if candidate.get("phase_authority") == "source_bound_primary_motion" and not _source_phase_authority(candidate):
            reject(candidate, "phase_authority_source_unverified")
            continue
        reviewed = _map(candidate.get("verified_action_context"))
        associated_hand = _map(candidate.get("hand_evidence")).get("hand")
        if (_source_phase_authority(candidate) and reviewed.get("racket_hand") not in ("left", "right")
                and associated_hand in ("left", "right") and associated_hand != _motion_wrist(candidate)):
            reject(candidate, "racket_association_conflicts_with_motion_wrist")
            continue
        if (_source_phase_authority(candidate) and reviewed.get("racket_hand") in {"left", "right"}
                and reviewed["racket_hand"] != _motion_wrist(candidate)
                and reviewed.get("technique") not in ("baseline_two_hand_backhand", "return_two_hand_backhand")):
            reject(candidate, "reviewed_hand_conflicts_with_motion_phases")
            continue
        if _map(reviewed.get("validity")).get("status") == "invalid":
            reject(candidate, "reviewed_action_incomplete_or_invalid")
            outcomes[-1]["review_reason_codes"] = _map(reviewed.get("validity")).get("reason_codes", [])
            continue
        technique = reviewed.get("technique")
        if technique in TECHNIQUES and ((technique == "serve") != (candidate.get("family") == "serve")
                                       or technique.startswith("return_") != (candidate.get("family") == "return")):
            reject(candidate, "reviewed_technique_conflicts_with_detected_phases")
            continue
        lo, mid, hi = (candidate.get(k) for k in ("start_ms", "peak_ms", "end_ms"))
        reference = _map(candidate.get("continuity"))
        candidate_phases = {p.get("phase"): p for p in candidate.get("phases", []) if isinstance(p, Mapping) and p.get("status") == "measured"}
        candidate_prep_end = _map(candidate_phases.get("preparation")).get("end_ms")
        if (not all(_finite(v) for v in (lo, mid, hi, candidate_prep_end)) or not 0 <= lo < candidate_prep_end < mid < hi
                or not all(k in candidate_phases for k in ("preparation", "acceleration", "follow_through"))
                or reference.get("pose_source") != "sam3d" or str(reference.get("video_sha256", "")).lower() != sha
                or candidate.get("family") not in {"baseline", "return", "serve"}
                or candidate.get("family") == "serve" and candidate.get("racket_hand") not in {"left", "right"}
                or not any(segment and segment[0]["track"] == candidate.get("person_track_id")
                           and segment[0]["reference"] == (reference.get("source_selection_epoch"), reference.get("camera_reference_epoch"))
                           and segment[0]["time"] <= mid <= segment[-1]["time"] for segment in segments)):
            reject(candidate, "candidate_source_or_complete_phase_unverified")
            continue
        if any(max(lo, prior["start_ms"]) < min(hi, prior["end_ms"]) for prior in ordered):
            reject(candidate, "candidate_overlap_duplicate")
            continue
        ordered.append(candidate)
    for candidate_index, episode in enumerate(ordered):
        start, peak_time, end = (episode.get(k) for k in ("start_ms", "peak_ms", "end_ms"))
        continuity = _map(episode.get("continuity"))
        if (not all(_finite(v) for v in (start, peak_time, end)) or not 0 <= start < peak_time < end
                or continuity.get("pose_source") != "sam3d" or str(continuity.get("video_sha256", "")).lower() != sha
                or episode.get("family") not in {"baseline", "return", "serve"}):
            continue
        phases = {p.get("phase"): p for p in episode.get("phases", []) if isinstance(p, Mapping) and p.get("status") == "measured"}
        if not all(k in phases for k in ("preparation", "acceleration", "follow_through")):
            continue
        prep_end = phases["preparation"].get("end_ms")
        source_phases = _source_phase_authority(episode)
        if not _finite(prep_end) or not start < prep_end < peak_time:
            continue
        if any(max(start, a) < min(end, b) for a, b in seen_intervals):
            continue
        choices = []
        active_jump_seen = False
        paired_failure = None
        for segment in segments:
            if not segment or (segment[0]["track"], segment[0]["reference"]) != (
                episode.get("person_track_id"), (continuity.get("source_selection_epoch"), continuity.get("camera_reference_epoch"))):
                continue
            t = np.asarray([r["time"] for r in segment])
            # The 2D proposal can stop in the backswing or select the balancing
            # arm. Bind a finite complete context before choosing the active arm;
            # no samples are synthesized and neighboring actions cannot overlap.
            previous_peak = ordered[candidate_index - 1].get("peak_ms") if candidate_index else None
            next_peak = ordered[candidate_index + 1].get("peak_ms") if candidate_index + 1 < len(ordered) else None
            context_start = start if source_phases else max(start - 350, (previous_peak + peak_time) / 2 if _finite(previous_peak) else start - 350)
            context_end = end if source_phases else min(end + 550, (next_peak + peak_time) / 2 if _finite(next_peak) else end + 550)
            a, b = int(np.searchsorted(t, context_start - .51)), int(np.searchsorted(t, context_end + .51, side="right"))
            if b - a < 13 or t[b - 1] - t[a] < 450 or not t[a] <= prep_end < peak_time <= t[b - 1]:
                continue
            if source_phases and (abs(t[a] - start) > 1.01 or abs(t[b - 1] - end) > 1.01
                                  or min(abs(t[a:b] - peak_time)) > 1.01 or min(abs(t[a:b] - prep_end)) > 1.01):
                continue
            wrists = {side: _wrist_series(segment, side)[a:b] for side in ("left", "right")}
            # Pick the more active arm inside the independently identified swing,
            # not the arm with greatest walking path elsewhere in the video.
            paths = {side: float(np.sum(np.linalg.norm(np.diff(w, axis=0), axis=1))) if np.isfinite(w).all() else -1 for side, w in wrists.items()}
            reviewed_hand = _map(episode.get("verified_action_context")).get("racket_hand")
            classification = _map(episode.get("classification"))
            side = reviewed_hand if reviewed_hand in {"left", "right"} else (
                _motion_wrist(episode) if source_phases else episode.get("racket_hand") if episode["family"] == "serve" or classification.get("status") == "rule_inferred" else None)
            if side not in {"left", "right"}:
                side = max(paths, key=paths.get)
            if side not in {"left", "right"}:
                continue
            associated_hand = _map(episode.get("hand_evidence")).get("hand")
            if reviewed_hand not in ("left", "right") and associated_hand in ("left", "right") and associated_hand != side:
                paired_failure = "racket_association_conflicts_with_motion_wrist"
                continue
            if any(side in (*row.get("wrist_discontinuities", ()), *row.get("wrist_unavailable", ())) for row in segment[a:b]):
                active_jump_seen = True
                continue
            paired = None
            if source_phases and side != _motion_wrist(episode):
                motion_side = _motion_wrist(episode)
                if any(motion_side in (*row.get("wrist_discontinuities", ()), *row.get("wrist_unavailable", ())) for row in segment[a:b]):
                    paired_failure = "two_hand_phase_wrist_discontinuity"
                    continue
                paired = _paired_hand_support(episode, segment[a:b], source_observations, sha)
                if paired.get("status") != "supported":
                    paired_failure = paired.get("reason_code", "two_hand_phase_support_unverified")
                    continue
            wrist = wrists[side]
            if paths[side] < .45 or not np.isfinite(wrist).all() or np.linalg.norm(np.ptp(wrist, axis=0)) < .25:
                continue
            step_ms = np.diff(t[a:b])
            active_ms = sum(step_ms[np.linalg.norm(np.diff(wrist, axis=0), axis=1) / (step_ms / 1000) > .36])
            if active_ms < 160:
                continue
            speed = _local_speed(wrist, t[a:b])
            peak_candidates = [i for i in range(3, len(wrist) - 4)
                               if start <= t[a + i] <= end + 150 and t[a + i] - t[a] >= 160
                               and t[b - 1] - t[a + i] >= 160]
            if not peak_candidates:
                continue
            if source_phases or episode["family"] == "serve":
                # A fast downward follow-through must not replace the detector's
                # source-bound ordered overhead-extension pivot.
                peak = int(np.argmin(abs(t - peak_time)))
                native_prep_end = float(t[int(np.argmin(abs(t - prep_end)))]) if source_phases else prep_end
            else:
                local_peak = max(peak_candidates, key=lambda i: (speed[i], -i))
                peak = a + local_peak
                onset_candidates = [i for i in range(2, local_peak)
                                    if 120 <= t[peak] - t[a + i] <= 450]
                if not onset_candidates:
                    continue
                onset = min(onset_candidates, key=lambda i: (speed[i], -i))
                native_prep_end = float(t[a + onset])
            if peak - a < 3 or b - peak < 4:
                continue
            meta = {"episode_id": episode["episode_id"], "prep_end_ms": native_prep_end,
                    "family": episode["family"], "candidate_start_ms": start, "candidate_peak_ms": peak_time, "candidate_end_ms": end,
                    "classification": episode.get("classification"), "phases": episode.get("phases", []),
                    "stroke_classification": episode.get("stroke_classification"),
                    "return_context": episode.get("return_context"),
                    "source_phase_authority": source_phases,
                    "hand_roles": _hand_roles(episode, side, paired),
                    "verified_action_context": episode.get("verified_action_context"),
                    "preparation_start_clipped": context_start < t[0] - .51 and a == 0}
            choices.append((segment, (a, b, peak, side, wrist, meta)))
        if choices:
            chosen = max(choices, key=lambda item: item[1][1] - item[1][0])
            out.append(chosen)
            seen_intervals.append((start, end))
            rows, window = chosen
            outcomes.append({"episode_id": episode["episode_id"], "start_ms": round(rows[window[0]]["time"], 3),
                             "end_ms": round(rows[window[1] - 1]["time"], 3),
                             "person_track_id": episode.get("person_track_id"),
                             "subject_id": rows[window[0]]["subject_id"], "status": "measured", "reason_codes": []})
        else:
            reject(episode, "active_wrist_discontinuity_in_motion" if active_jump_seen else paired_failure or "candidate_lacks_continuous_complete_motion")
    return out


def _valid(values, minimum=.7):
    a = np.asarray([np.nan if v is None else v for v in values], dtype=float)
    finite = np.isfinite(a)
    if a.ndim > 1:
        finite = finite.all(axis=1)
    return a if len(a) and finite.mean() >= minimum else None


def _time_mean(values, times, minimum=.7):
    """Trapezoidal mean on observed adjacent PTS; missing samples are not filled.

    Frame counts would overweight high-frame-rate portions of a VFR video.
    Coverage is elapsed time with two valid endpoints, not pose confidence.
    """
    values, times = np.asarray(values, dtype=float), np.asarray(times, dtype=float)
    if len(values) < 2 or len(values) != len(times) or not np.isfinite(times).all():
        return None
    dt = np.diff(times)
    if not (dt > 0).all():
        return None
    valid = np.isfinite(values[:-1]) & np.isfinite(values[1:])
    covered = float(dt[valid].sum())
    if covered <= 0 or covered / float(dt.sum()) < minimum:
        return None
    return float(np.sum(dt[valid] * (values[:-1][valid] + values[1:][valid]) / 2) / covered)


def _temporal_median(values, times, radius_ms=60.):
    """Suppress isolated estimation spikes over elapsed time, never fill gaps."""
    values, times = np.asarray(values, dtype=float), np.asarray(times, dtype=float)
    output = values.copy()
    for i, t in enumerate(times):
        window = values[np.abs(times - t) <= radius_ms]
        if np.isfinite(values[i]) and np.isfinite(window).all():
            output[i] = np.median(window)
    return output


def _leg_extension_continuity(rows, times, prep_end, peak):
    """Net same-leg extension / total flexion variation near the forward swing.

    The preparatory load apex is sought only from 200 ms before acceleration;
    earlier split steps and waiting bounces are outside this measurement.
    No preferred knee depth, actual force, or ground contact is inferred.
    """
    start = int(np.searchsorted(times, times[prep_end - 1] - 200))
    choices = []
    for leg in ("left", "right"):
        values = _valid([row[f"{leg}_knee_flexion"] for row in rows[start:peak + 1]], minimum=1.)
        if values is None or len(values) < 5:
            continue
        values = _temporal_median(values, times[start:peak + 1])
        prep_count = prep_end - start
        apex = max(range(prep_count), key=lambda i: (round(values[i], 7), i))
        trace = values[apex:]
        net = max(0., float(trace[0] - trace[-1]))
        travel = float(np.abs(np.diff(trace)).sum())
        # A continuous three-degree denominator floor prevents tiny estimation
        # changes receiving full credit; this is not a target knee excursion.
        ratio = net / max(travel, 3.)
        choices.append((net, ratio))
    return max(choices, key=lambda item: item[0])[1] if choices else None


def _follow_deceleration(wrist, times, peak, reason_codes=None):
    """Net speed reduction / total speed variation before the first recovery.

    Search at most 450 ms for the FIRST local trough supported by 80 ms of
    renewed movement. Later recovery acceleration is outside the measurement.
    A serve's overhead-position pivot is not automatically a wrist-speed peak.
    Absent an observed speed peak and recovery trough, this measure is missing.
    """
    def unavailable(code):
        if reason_codes is not None:
            reason_codes.append(code)
        return None
    speed = _local_speed(wrist, times)
    if len(speed) < 7 or not np.isfinite(speed).all() or times[-1] - times[peak] < 160:
        return unavailable("follow_deceleration_incomplete_window")
    # The centered five-observation fit already suppresses a joint spike.
    # Another median would erase short, genuine recovery troughs at 50/60 fps.
    local = speed[np.abs(times - times[peak]) <= 60]
    if speed[peak] < float(np.max(local)) - 1e-7:
        return unavailable("follow_deceleration_anchor_not_wrist_speed_peak")
    end = int(np.searchsorted(times, times[peak] + 450, side="right"))
    stop = None
    for i in range(peak + 2, end):
        if times[i] + 80 > times[-1] + 1e-7:
            break
        left = int(np.searchsorted(times, times[i] - 80))
        right = int(np.searchsorted(times, times[i] + 80, side="right"))
        before, after = speed[max(peak, left):i], speed[i + 1:right]
        if (len(before) < 2 or len(after) < 2 or speed[i] >= speed[peak] - 1e-7
                or np.min(before) < speed[i] - 1e-7 or np.min(after) < speed[i] - 1e-7):
            continue
        # A supported return to motion identifies the boundary. Tiny noisy
        # sign changes alone do not establish a recovery phase.
        if float(np.max(after) - speed[i]) >= .1 * speed[peak]:
            stop = i
            break
    if stop is None:
        return unavailable("follow_deceleration_recovery_trough_unobserved")
    trace = speed[peak:stop + 1]
    travel = float(np.abs(np.diff(trace)).sum())
    return float(np.clip((trace[0] - trace[-1]) / travel, 0, 1)) if travel > 1e-7 else None


def _follow_active_fraction(steps, times, peak):
    """Active share of the first two thirds of elapsed follow-through time."""
    cutoff = times[peak] + (times[-1] - times[peak]) * 2 / 3
    step_ms = np.diff(times)[peak:]
    observed_ms = np.clip(np.minimum(times[peak + 1:], cutoff) - times[peak:-1], 0., None)
    return float(np.sum(observed_ms[steps[peak:] / (step_ms / 1000) > .36]) / np.sum(observed_ms))


def _score_window(segment, window, *, source_observations=None, video_sha256=None):
    a, b, anchor, side, wrist, meta = window
    rows = segment[a:b]
    observations, context = window_source_motion(rows, source_observations or {}, video_sha256)
    rows, observations, missing_wrists = _joint_scoped_window(rows, observations)
    if missing_wrists:
        context["unavailable_wrist_observations"] = missing_wrists
    t = np.array([row["time"] for row in rows])
    peak = anchor - a
    prep_end = min(peak, max(3, int(np.searchsorted(t, meta["prep_end_ms"], side="right"))))
    prep = rows[:prep_end]
    core_a, core_b = max(0, peak - 2), min(len(rows), peak + 3)
    core, post = rows[core_a:core_b], rows[peak:]
    values = {}
    quality_codes = ["preparation_start_clipped_by_source_boundary"] if meta.get("preparation_start_clipped") else []
    if missing_wrists:
        quality_codes.append("non_active_wrist_discontinuity_unmeasured")
    def put(key, value, score):
        if _finite(float(value)) and _finite(float(score)):
            values[key] = (float(value), float(np.clip(score, 0, 100)))
    knee_preparation = {leg: _valid([row[f"{leg}_knee_flexion"] for row in prep]) for leg in ("left", "right")}
    loads = [(_time_mean(v, t[:prep_end]), _time_mean([_ramp(x, 0, 15) for x in v], t[:prep_end]))
             for v in knee_preparation.values() if v is not None]
    loads = [item for item in loads if all(v is not None for v in item)]
    if loads:
        v, score = max(loads, key=lambda item: item[1])
        put("knee_load", v, score)
    stance = _valid([row["stance_width_hips"] for row in prep])
    if stance is not None and meta["family"] != "serve":
        v = _time_mean(stance, t[:prep_end])
        score = _time_mean([_ramp(x, .5, 1.) for x in stance], t[:prep_end])
        if v is not None and score is not None:
            put("support_control", v, score)
    extension_continuity = _leg_extension_continuity(rows, t, prep_end, peak)
    if extension_continuity is not None:
        put("leg_extension_continuity", extension_continuity, 100 * extension_continuity)
    sep = _smooth([row["separation"] for row in rows])
    prep_sep = sep[:prep_end]
    coil = float(np.quantile(prep_sep, .9))
    # The last half of preparation should sustain the turning posture. A single
    # best sample no longer stands in for the complete preparation phase.
    settled = int(np.searchsorted(t[:prep_end], (t[0] + t[prep_end - 1]) / 2))
    settled = min(settled, prep_end - 2)
    put("coil", _time_mean(prep_sep[settled:], t[settled:prep_end]),
        _time_mean([_band(x, 0, 15, 45, 80) for x in prep_sep[settled:]], t[settled:prep_end]))
    prep_wrist = wrist[:prep_end]
    distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(prep_wrist, axis=0), axis=1))]
    delta = float(np.ptp(prep_sep))
    # No observable pair of movement onsets means no synchrony measurement.
    # Already-prepared posture is still measured by coil and knee participation.
    if not meta.get("preparation_start_clipped") and delta >= 3 and distance[-1] >= .12:
        onset_body = int(np.flatnonzero(prep_sep >= min(prep_sep) + .2 * delta)[0])
        onset_arm = int(np.flatnonzero(distance >= .2 * distance[-1])[0])
        if onset_body > 0 and onset_arm > 0:
            v = abs(t[onset_body] - t[onset_arm]) / max(1, t[prep_end - 1] - t[0])
            put("preparation_sync", v, 100 - _ramp(v, .15, .5))
    release = max(0., coil - float(np.min(sep[prep_end - 1:peak + 2]))) / max(coil, 1.)
    put("coil_release", release, _ramp(release, .05, .55) if coil >= 5 else 0.)
    legs = []
    for leg in ("left", "right"):
        data = _valid([row[f"{leg}_knee_flexion"] for row in rows[:peak + 2]], minimum=1.)
        if data is not None:
            release_amount = float(np.max(np.maximum.accumulate(data) - data))
            # Unilateral support is normal: flexion of the other leg must not
            # cancel the actual loaded leg's extension.
            loaded = float(np.max(data[:prep_end])) >= 10
            legs.append((release_amount if loaded else 0., data))
    if legs:
        knee_release, knee_all = max(legs, key=lambda value: value[0])
        sep_release = float(np.max(np.maximum.accumulate(sep[:peak + 2]) - sep[:peak + 2]))
        if knee_release < 3 or sep_release < 3:
            put("release_sequence", 1., 0.)
        else:
            dt = np.diff(t[:peak + 2])
            lower = int(np.argmax(-np.diff(_smooth(knee_all)) / dt)) + 1
            trunk = int(np.argmax(-np.diff(sep[:peak + 2]) / dt)) + 1
            tolerance = float(np.median(dt))
            disorder = max(0., t[lower] - t[trunk] - tolerance) + max(0., t[trunk] - t[peak] - tolerance)
            gap = max(0., t[trunk] - t[lower] - .5 * (t[peak] - t[0]))
            v = (disorder + gap) / max(1., t[peak] - t[0])
            put("release_sequence", v, 100 - _ramp(v, 0, .5))
    # Evaluate the whole forward acceleration, not just a fortunate five-frame
    # snapshot at its fastest point. Head turns about the torso-up axis remain
    # excluded, because front/back nose motion is not loss of head control.
    striking_start = max(0, prep_end - 1)
    striking_end = min(len(rows), max(peak + 2, int(np.searchsorted(t, t[peak] + 80, side="right"))))
    striking_rows = rows[striking_start:striking_end]
    striking_times = t[striking_start:striking_end]
    heads = [r["head"] for r in striking_rows]
    if sum(v is not None for v in heads) / len(heads) >= .7:
        # Ear midpoint preferred. Ignore front/back nose motion from normal head turns.
        h = np.asarray([v for v in heads if v is not None])[:, :2]
        v = float(np.quantile(np.linalg.norm(h - np.median(h, axis=0), axis=1), .9))
        put("head_control", v, 100 - _ramp(v, .05, .2))
    reach = _valid([r[f"{side}_reach"] for r in striking_rows])
    if reach is not None:
        v = _time_mean(reach, striking_times)
        score = _time_mean([_band(x, .3, .65, 1, 1.15) for x in reach], striking_times)
        if v is not None and score is not None:
            put("arm_space", v, score)
    steps = np.linalg.norm(np.diff(wrist, axis=0), axis=1)
    before, after = float(sum(steps[:peak])), float(sum(steps[peak:]))
    if before >= .12:
        v = after / before
        # Clip elapsed intervals at the first two thirds of follow-through,
        # instead of taking two thirds of its frames in a VFR recording.
        active_fraction = _follow_active_fraction(steps, t, peak)
        # A long isolated step cannot substitute for a continuous follow-through.
        put("follow_path", v, .5 * _ramp(v, .05, .35) + .5 * _ramp(active_fraction, .35, .8))
    post_knee = _valid([r["knee"] for r in post])
    continuity = None
    if len(post) >= 5:
        n = max(5, len(post) // 2)
        continuity = _angular_continuity([r["signed_separation"] for r in post[-n:]], [r["time"] for r in post[-n:]])
        if continuity is not None and post_knee is not None:
            late_load = float(np.nanmedian(post_knee[-n:]))
            ratio, smoothness = continuity
            put("recovery_control", ratio, .5 * smoothness + .5 * _ramp(late_load, 0, 15))
    if meta["family"] == "serve":
        values = apply_serve_profile(rows, prep_end=prep_end, peak=peak, side=side, wrist=wrist, values=values,
                                     recovery_continuity=continuity)
    reviewed = _map(meta.get("verified_action_context"))
    technique = reviewed.get("technique")
    verified = technique in TECHNIQUES
    provenance = {"method": reviewed.get("method"), "reviewer": reviewed.get("reviewer")} if verified else {"method": "unverified"}
    classification = _map(meta.get("stroke_classification")) if meta["family"] == "return" else _map(meta.get("classification"))
    classified = {"forehand": "forehand", "two_handed_backhand": "two_hand_backhand", "backhand": "one_hand_backhand"}.get(classification.get("label"))
    if not verified and classified and classification.get("status") == "rule_inferred":
        technique = ("return_" if meta["family"] == "return" else "baseline_") + classified
        verified, provenance = True, {"method": "source_bound_rule_inferred", "limitations": "technical_classification_not_independently_accuracy_validated"}
    # A detector-confirmed serve has its own ordered overhead phase contract.
    if not verified and meta["family"] == "serve":
        technique, verified, provenance = "serve", True, {"method": "source_bound_serve_phases"}
    direction = bind_reviewed_directions(reviewed, source_observations or {}, context)
    context.update(direction["directions"])
    # Complete recovery must be independently reviewed; a generic follow phase
    # or the end of a clip does not establish it.
    context["recovery_end_verified"] = reviewed.get("recovery_end_verified") is True
    context["preparation_start_clipped"] = bool(meta.get("preparation_start_clipped"))
    pattern = analyze_motion_pattern(rows, prep_end=prep_end, peak=peak, side=side, technique=technique,
                                     classification_verified=verified, source_observations=observations, source_context=context)
    for replacement, previous in (("pattern_preparation_coupling", "preparation_sync"),
                                  ("pattern_projected_sequence", "release_sequence"),
                                  ("pattern_phase_continuity", "follow_path")):
        if replacement in pattern["metrics"]:
            values.pop(previous, None)
    values.update(pattern["metrics"])
    pattern_public = {key: value for key, value in pattern.items() if key not in {"metrics", "metric_definitions"}}
    pattern_public["classification_provenance"] = provenance
    pattern_public["direction_binding"] = direction["evidence"]
    return {"episode_id": meta["episode_id"], "family": meta["family"], "start_ms": round(t[0], 3),
            "peak_ms": round(t[peak], 3), "end_ms": round(t[-1], 3), "person_track_id": rows[0]["track"],
            "primary_player_id": rows[0]["person"],
            "subject_id": rows[0]["subject_id"], "identity_basis": rows[0]["identity_basis"],
            "source_track_id": rows[0]["track"], "source_selection_epoch": rows[0]["reference"][0],
            "camera_reference_epoch": rows[0]["reference"][1], "active_arm": side,
            "hand_roles": meta.get("hand_roles"),
            "candidate_window": {key: meta[key] for key in ("candidate_start_ms", "candidate_peak_ms", "candidate_end_ms")},
            "quality_reason_codes": quality_codes,
            "phase_refinement": "source_bound_primary_motion_phases" if meta.get("source_phase_authority") else "source_bound_serve_overhead_pivot" if meta["family"] == "serve" else "native_3d_active_arm_in_bounded_candidate_context",
            "anchor_semantics": "source_bound_overhead_extension_not_ball_contact" if meta["family"] == "serve" else "source_bound_image_plane_wrist_motion_peak_not_ball_contact" if meta.get("source_phase_authority") else "body_relative_arm_motion_peak_not_ball_contact",
            "frame_count": len(rows), "metrics": values, "motion_pattern": pattern_public,
            **({"return_context": {**dict(meta["return_context"]), "receiver_peak_ms": meta["candidate_peak_ms"],
                                   "verification_method": "source_bound_opponent_serve_and_incoming_ball"}}
               if meta["family"] == "return" else {})}


def _empty(job_id, sha):
    return {"version": VERSION, "status": "unavailable", "source_run_id": job_id, "source_video_sha256": sha,
            "source_timestamp_semantics": "original_decoder_pts", "overall_score": None,
            "dimensions": [{"id": key, "label": label, "score": None, "observation_count": 0,
                            "metrics": [{"id": m, "label": title, "unit": unit, "target": target, "weight": 1,
                                         "value": None, "score": None, "observation_count": 0} for m, title, unit, target in METRICS[key]]}
                           for key, label in DIMENSIONS],
            "strengths": [], "improvements": [], "episodes": [], "observation_count": 0,
            "subject_scope": {"version": "subject-scoped-assessment-v1", "selected_subject_id": None,
                              "selection_basis": "no_verified_subject", "cross_subject_aggregation": False, "subjects": []},
            "episode_validity": [],
            "pattern_coverage": {"measured_requirements": 0, "applicable_requirements": 0, "fraction": None,
                                 "verified_technique_episodes": 0, "generic_episodes": 0,
                                 "full_source_technique_covered": False, "profiles": []},
            "reason_zh": "尚无足够连续的主球员挥拍姿态。", "quality_reason_counts": {},
            "candidate_accounting": {"proposed_count": 0, "accepted_count": 0, "rejected_count": 0, "rejection_counts": {}},
            "rubric": {"version": RUBRIC_VERSION, "semantics": "engineering_action_quality", "coach_calibrated": False,
                       "identity_labels_used": False, "confidence_used_in_score": False,
                       "aggregation": "mean_episode_dimension_scores_then_equal_dimension_mean",
                       "metric_weight_semantics": "sum_inverse_available_metrics_per_episode_divided_by_dimension_episode_count",
                       "coordinate_reference": "per_frame_hip_torso_body_basis", "ball_contact_assumed": False,
                       "score_range": [0, 100], "minimum_metric_coverage": .7,
                       "numeric_rule_origin": "versioned_engineering_initial_rules",
                       "revision_scope": "phase_specific_observability_and_same_subject_candidate_binding_no_weight_or_numeric_cutpoint_change",
                       "static_metric_integration": "trapezoidal_actual_pts_mean_of_per_observation_rules_no_missing_interpolation",
                       "integrated_metric_ids": ["knee_load", "support_control", "coil", "arm_space"],
                       "missing_metrics_renormalized": True,
                       "scope": "visible_body_motion_not_competitive_level_or_ball_outcome",
                       "action_window_semantics": "source_bound_primary_motion_phases_when_verified_otherwise_legacy_bounded_body_relative_context_not_ball_contact",
                       "phase_velocity_estimator": "centered_five_observation_linear_fit_actual_decoder_pts",
                       "profiles": ["groundstroke_general", "serve"],
                       "serve_profile": {key: value for key, value in SERVE_RUBRIC.items() if key != "internal_sources"},
                       "motion_patterns": PATTERN_RUBRIC,
                       "metric_replacement": {"pattern_preparation_coupling": "preparation_sync", "pattern_projected_sequence": "release_sequence", "pattern_phase_continuity": "follow_path"},
                       "rules": {
                           "knee_load": {"curve": "time_mean_increasing_saturating_ramp", "knots": [0, 15], "measurement": "full_preparation_per_leg_time_mean_then_best_observed_leg", "semantics": "preparation_participation_not_unique_optimal_knee_depth"},
                           "support_control": {"curve": "time_mean_increasing_saturating_ramp", "knots": [.5, 1.], "geometry": "three_dimensional_ankle_distance_divided_by_hip_width", "semantics": "ready_stance_extent_not_balance_or_ground_reaction", "families": ["baseline", "return"]},
                           "coil": {"curve": "time_mean_trapezoid", "knots": [0, 15, 45, 80], "phase": "last_half_preparation"},
                           "leg_extension_continuity": {"curve": "100_times_net_extension_divided_by_max_total_angle_variation_and_3deg", "phase": "preparation_load_apex_within_200ms_before_acceleration_to_wrist_peak", "denominator_floor_deg": 3., "temporal_median_radius_ms": 60., "same_leg_required": True, "true_force_assumed": False},
                           "preparation_sync": {"curve": "decreasing_ramp", "knots": [.15, .5], "minimum_separation_range_deg": 3, "minimum_wrist_path_torso": .12},
                           "release_sequence": {"curve": "decreasing_ramp", "knots": [0, .5], "minimum_joint_range_deg": 3, "simultaneous_tolerance_frames": 1},
                           "coil_release": {"curve": "increasing_saturating_ramp", "knots": [.05, .55], "minimum_preparation_separation_deg": 5},
                           "head_control": {"curve": "decreasing_ramp", "knots": [.05, .2], "phase": "acceleration_onset_to_peak_plus_80ms"},
                           "arm_space": {"curve": "time_mean_trapezoid", "knots": [.3, .65, 1, 1.15], "phase": "acceleration_onset_to_peak_plus_80ms"},
                           "follow_path": {"curve": "equal_mean_path_and_sustained_motion", "path_knots": [.05, .35], "active_fraction_knots": [.35, .8], "active_speed_floor_torso_per_second": .36, "active_fraction_weighting": "elapsed_decoder_pts"},
                           "recovery_control": {"curve": "equal_mean_angular_motion_continuity_and_knee_load", "excess_velocity_variation_knots": [.5, 4.],
                                                "angle_noise_floor_deg": 3., "normal_velocity_envelope": "two_times_max_absolute_velocity",
                                                "angle_semantics": "signed_relative_axis_unwrapped_modulo_180_degrees", "knee_knots": [0, 15]}}}}


def analyze_tennis_assessment(timeline, records, *, video_sha256, source_run_id=None, motion_episodes=(), source_frames=(), action_context=None):
    """Pure CPU entry point, useful for regression and independent recomputation."""
    result = _empty(source_run_id, video_sha256.lower())
    result["dimensions"] = []
    segments, reasons = _segments(timeline, records, video_sha256.lower())
    sha = video_sha256.lower()
    motion_episodes = [{**e, "verified_action_context": verified_action_context(action_context, e, sha)} for e in motion_episodes]
    observations = source_motion_by_frame(records, list(source_frames), sha)
    rejected = Counter()
    outcomes = []
    all_episodes = [_score_window(segment, window, source_observations=observations, video_sha256=sha)
                    for segment, window in _bound_windows(segments, motion_episodes, sha, rejected, outcomes, source_observations=observations)]
    scope = select_report_subject(segments)
    episodes = [e for e in all_episodes if e["subject_id"] == scope["selected_subject_id"]]
    for subject in scope["subjects"]:
        subject["measured_episode_count"] = sum(e["subject_id"] == subject["subject_id"] for e in all_episodes)
        subject["included_in_report"] = subject["subject_id"] == scope["selected_subject_id"]
    for outcome in outcomes:
        if outcome["status"] == "measured":
            outcome["status"] = "scored" if outcome["subject_id"] == scope["selected_subject_id"] else "other_subject"
            if outcome["status"] == "other_subject":
                outcome["reason_codes"] = ["different_subject_not_aggregated"]
    result["subject_scope"], result["episode_validity"] = scope, outcomes
    result["candidate_accounting"] = {"proposed_count": len(motion_episodes), "accepted_count": len(episodes),
                                      "other_subject_count": len(all_episodes) - len(episodes),
                                      "rejected_count": len(motion_episodes) - len(all_episodes), "rejection_counts": dict(rejected)}
    result["episodes"] = [{**e, "metrics": {k: {"value": round(v[0], 5), "score": round(v[1], 2)} for k, v in e["metrics"].items()}} for e in episodes]
    result["quality_reason_counts"] = dict(reasons)
    result["observation_count"] = len(episodes)
    measured = sum(e["motion_pattern"]["coverage"]["measured_requirements"] for e in episodes)
    applicable = sum(e["motion_pattern"]["coverage"]["applicable_requirements"] for e in episodes)
    verified_count = sum(e["motion_pattern"]["classification_status"] == "verified" for e in episodes)
    result["pattern_coverage"] = {"measured_requirements": measured, "applicable_requirements": applicable,
                                 "fraction": measured / applicable if applicable else None,
                                 "verified_technique_episodes": verified_count, "generic_episodes": len(episodes) - verified_count,
                                 "full_source_technique_covered": False, "profiles": sorted({e["motion_pattern"]["profile_id"] for e in episodes})}
    if not episodes and motion_episodes:
        result["reason_zh"] = f"识别到 {len(motion_episodes)} 个动作候选，但没有片段同时满足来源一致、阶段完整和连续主球员姿态要求。"
    families = {e["family"] for e in episodes}
    for identifier, label in DIMENSIONS:
        definitions = dict((row[0], row) for row in (SERVE_METRICS[identifier] if families == {"serve"} else METRICS[identifier]))
        if "serve" in families:
            for row in SERVE_METRICS[identifier]:
                definitions.setdefault(row[0], row)
        for row in PATTERN_METRICS[identifier]:
            if any(row[0] in e["metrics"] or any(row[0] in req["metric_ids"] for req in e["motion_pattern"]["requirements"]) for e in episodes):
                definitions[row[0]] = row
        available_counts = [sum(key in e["metrics"] for key in definitions) for e in episodes]
        count = sum(n > 0 for n in available_counts)
        metrics = []
        for key, title, unit, target in definitions.values():
            observations = [(e["metrics"][key], 1 / n) for e, n in zip(episodes, available_counts) if key in e["metrics"]]
            mass = sum(w for _, w in observations)
            metrics.append({"id": key, "label": title, "unit": unit, "target": target,
                            "weight": round(mass / count, 8) if observations else 1,
                            "value": round(sum(v[0] * w for v, w in observations) / mass, 5) if observations else None,
                            "score": round(sum(v[1] * w for v, w in observations) / mass, 2) if observations else None,
                            "observation_count": len(observations)})
        scored = [m for m in metrics if m["score"] is not None]
        score = sum(m["score"] * m["weight"] for m in scored) / sum(m["weight"] for m in scored) if scored else None
        result["dimensions"].append({"id": identifier, "label": label, "score": round(score, 2) if score is not None else None,
                                     "observation_count": count, "metrics": metrics})
    available = [d for d in result["dimensions"] if d["score"] is not None]
    if available:
        result["overall_score"] = round(float(np.mean([d["score"] for d in available])), 2)
        result["status"] = "scored" if len(available) == 5 else "partial"
        result["reason_zh"] = f"已分析 {len(episodes)} 个连续动作片段。"
        result["strengths"] = []
        for dimension in sorted(available, key=lambda d: -d["score"]):
            strong = max((m for m in dimension["metrics"] if m["score"] is not None), key=lambda m: m["score"])
            advice = strength_for_metric(strong["id"], strong["value"], family="serve" if families == {"serve"} else "baseline", score=strong["score"])
            if advice:
                result["strengths"].append(advice)
            if len(result["strengths"]) == 2:
                break
        for d in sorted(available, key=lambda d: d["score"])[:2]:
            weak = min((m for m in d["metrics"] if m["score"] is not None), key=lambda m: m["score"])
            example = min((e for e in episodes if weak["id"] in e["metrics"]), key=lambda e: e["metrics"][weak["id"]][1])
            actual_value, actual_score = example["metrics"][weak["id"]]
            advice = coaching_for_metric(weak["id"], actual_value, family=example["family"], score=actual_score)
            result["improvements"].append({"dimension_id": d["id"], **advice,
                                           "start_ms": example["start_ms"], "end_ms": example["end_ms"]})
    return result


@lru_cache(maxsize=4)
def _cached(directory, signatures, sha, job_id, episodes_json):
    directory = Path(directory)
    timeline = list(_read_rows(directory / "primary-player.jsonl"))
    reviewed_selection = directory / "tennis-subject-selection.json"
    if reviewed_selection.is_file():
        timeline = reviewed_subject_timeline(json.loads(reviewed_selection.read_text(encoding="utf-8")),
                                             video_sha256=sha, native_sha256=signatures[1][2] if signatures[1] else None,
                                             frames_sha256=signatures[2][2] if signatures[2] else None)
    keys = {(r.get("source_frame_index"), r.get("source_track_id")) for r in timeline}
    records = [r for r in _read_rows(directory / "sam3d_frames.jsonl") if (r.get("frame_index"), r.get("person_track_id")) in keys]
    frames = list(_read_rows(directory / "frames.jsonl")) if (directory / "frames.jsonl").is_file() else []
    # A sidecar chooses a different source-bound target timeline. Its motion
    # proposals must use that same timeline instead of the historical summary.
    # The original primary timeline and formal report remain untouched.
    candidate_episodes = (_summary_episodes({"action_recognition": detect_stroke_candidates(frames, primary_timeline=timeline)})
                          if reviewed_selection.is_file() else json.loads(episodes_json))
    context_path = directory / "action-context.json"
    context = json.loads(context_path.read_text(encoding="utf-8")) if context_path.is_file() else None
    result = analyze_tennis_assessment(timeline, records, video_sha256=sha, source_run_id=job_id,
                                      motion_episodes=candidate_episodes, source_frames=frames, action_context=context)
    if signatures != _input_signatures(directory):
        raise ValueError("assessment_inputs_changed_during_read")
    return result


def _input_signatures(directory):
    return tuple(_signature(directory / name) if (directory / name).is_file() else None
                 for name in ("primary-player.jsonl", "sam3d_frames.jsonl", "frames.jsonl", "action-context.json", "tennis-subject-selection.json"))


def build_tennis_assessment(summary: Mapping, run_dir: Path | str | None = None, *, job_id: str | None = None) -> dict:
    """Recompute from native artifacts only; no report score or user label is read."""
    job_id = job_id or summary.get("job_id")
    sha = _map(_map(summary.get("minimum_scoring_loop")).get("provenance")).get("video_sha256")
    result = _empty(job_id, sha.lower() if isinstance(sha, str) else None)
    try:
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha):
            return result
        if run_dir is None or not isinstance(job_id, str):
            return result
        directory = Path(run_dir).resolve()
        if directory.name != job_id or summary.get("job_id", job_id) != job_id:
            result["reason_zh"] = "动作姿态与任务来源不一致。"
            return result
        signatures = _input_signatures(directory)
        return deepcopy(_cached(str(directory), signatures, sha.lower(), job_id, json.dumps(_summary_episodes(summary), sort_keys=True)))
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        result["reason_zh"] = "动作姿态记录缺失或来源校验未通过。"
        return result
