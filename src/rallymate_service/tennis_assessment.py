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

import numpy as np

from rallymate_service.swing_kinematics import _read_rows, _signature
from rallymate_service.tennis_coaching import coaching_for_metric
from rallymate_service.tennis_profiles import SERVE_METRICS, SERVE_RUBRIC, apply_serve_profile
from rallymate_vision.sam3d_evaluation import COORDINATES_3D, _binding_error, _time

VERSION = "tennis-action-assessment-v1.0.0"
RUBRIC_VERSION = "tennis-action-rubric-v1.0.0"
DIMENSIONS = (("movement", "移动支撑"), ("preparation", "准备引拍"),
              ("coordination", "发力协调"), ("striking", "击球姿态"),
              ("follow_through", "随挥恢复"))
# low, plateau start, plateau end, high: continuous bounded trapezoid.
METRICS = {
    "movement": (("knee_load", "下肢蓄势参与", "deg", "准备阶段有明确屈膝，为后续伸展做准备"),
                 ("support_control", "准备站距", "hip_width", "双足自然分开形成准备站距，不以越宽为越好")),
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
              or record.get("relative_timestamp_ms", record.get("timestamp_ms")) != row.get("timestamp_ms")
              or abs(float(_time(record) * 1000) - origin - row.get("timestamp_ms", -1e9)) > 1.01):
            reason = "source_binding_mismatch"
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
                 reference=(record["selection_epoch"], record["camera_reference_epoch"]))
        if current:
            previous = current[-1]
            connected = (g["frame"] == previous["frame"] + 1 and 0 < g["time"] - previous["time"] <= 160
                         and (g["track"], g["person"], g["reference"]) == (previous["track"], previous["person"], previous["reference"]))
            # Discontinuous model geometry is a measurement break, not bad form.
            jump = not .65 <= g["torso"] / previous["torso"] <= 1.55
            for side in ("left", "right"):
                a, b = g["local"][f"{side}_wrist"], previous["local"][f"{side}_wrist"]
                jump |= a is not None and b is not None and np.linalg.norm(a - b) > 1.5
            if not connected or jump:
                segments.append(current)
                current = []
                reasons["geometry_discontinuity" if jump else "source_continuity_break"] += 1
        current.append(g)
    if current:
        segments.append(current)
    return segments, reasons


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
    return _smooth([row["local"][f"{side}_wrist"] if row["local"][f"{side}_wrist"] is not None else [np.nan] * 3 for row in segment])


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
            reduced = {key: row.get(key) for key in ("episode_id", "start_ms", "peak_ms", "end_ms", "person_track_id", "phases", "racket_hand")}
            reduced["continuity"] = _map(_map(row.get("evidence")).get("process_continuity"))
            reduced["family"] = family
            identifier = row["episode_id"]
            if identifier in by_id and by_id[identifier] != reduced:
                conflicts.add(identifier)
            by_id[identifier] = reduced
    return [e for key, e in by_id.items() if key not in conflicts]


def _bound_windows(segments, episodes, sha):
    """An independent phase candidate bounds scoring; any-arm peaks alone do not.

    A single detected action is counted once, never once per arm/peak/fragment.
    Classification text and old FS condition scores play no part in selection.
    """
    out, seen_intervals = [], []
    ordered = []
    for candidate in sorted(episodes, key=lambda e: (e.get("start_ms", 0), e.get("episode_id", ""))):
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
            continue
        if any(max(lo, prior["start_ms"]) < min(hi, prior["end_ms"]) for prior in ordered):
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
        if not _finite(prep_end) or not start < prep_end < peak_time:
            continue
        if any(max(start, a) < min(end, b) for a, b in seen_intervals):
            continue
        choices = []
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
            context_start = max(start - 350, (previous_peak + peak_time) / 2 if _finite(previous_peak) else start - 350)
            context_end = min(end + 550, (next_peak + peak_time) / 2 if _finite(next_peak) else end + 550)
            a, b = int(np.searchsorted(t, context_start - .51)), int(np.searchsorted(t, context_end + .51, side="right"))
            if b - a < 13 or t[b - 1] - t[a] < 450 or not t[a] <= prep_end < peak_time <= t[b - 1]:
                continue
            wrists = {side: _wrist_series(segment, side)[a:b] for side in ("left", "right")}
            # Pick the more active arm inside the independently identified swing,
            # not the arm with greatest walking path elsewhere in the video.
            paths = {side: float(np.sum(np.linalg.norm(np.diff(w, axis=0), axis=1))) if np.isfinite(w).all() else -1 for side, w in wrists.items()}
            side = episode.get("racket_hand") if episode["family"] == "serve" else max(paths, key=paths.get)
            if side not in {"left", "right"}:
                continue
            wrist = wrists[side]
            if paths[side] < .45 or not np.isfinite(wrist).all() or np.linalg.norm(np.ptp(wrist, axis=0)) < .25:
                continue
            active_ms = sum(np.diff(t[a:b])[np.linalg.norm(np.diff(wrist, axis=0), axis=1) > .012])
            if active_ms < 160:
                continue
            speed = _local_speed(wrist, t[a:b])
            peak_candidates = [i for i in range(3, len(wrist) - 4)
                               if start <= t[a + i] <= end + 150 and t[a + i] - t[a] >= 160
                               and t[b - 1] - t[a + i] >= 160]
            if not peak_candidates:
                continue
            if episode["family"] == "serve":
                # A fast downward follow-through must not replace the detector's
                # source-bound ordered overhead-extension pivot.
                peak = int(np.argmin(abs(t - peak_time)))
                native_prep_end = prep_end
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
                    "preparation_start_clipped": context_start < t[0] - .51 and a == 0}
            choices.append((segment, (a, b, peak, side, wrist, meta)))
        if choices:
            chosen = max(choices, key=lambda item: item[1][1] - item[1][0])
            out.append(chosen)
            seen_intervals.append((start, end))
    return out


def _valid(values, minimum=.7):
    a = np.asarray([np.nan if v is None else v for v in values], dtype=float)
    finite = np.isfinite(a)
    if a.ndim > 1:
        finite = finite.all(axis=1)
    return a if len(a) and finite.mean() >= minimum else None


def _score_window(segment, window):
    a, b, anchor, side, wrist, meta = window
    rows = segment[a:b]
    t = np.array([row["time"] for row in rows])
    peak = anchor - a
    prep_end = min(peak, max(3, int(np.searchsorted(t, meta["prep_end_ms"], side="right"))))
    prep = rows[:prep_end]
    core_a, core_b = max(0, peak - 2), min(len(rows), peak + 3)
    core, post = rows[core_a:core_b], rows[peak:]
    values = {}
    def put(key, value, score):
        if _finite(float(value)) and _finite(float(score)):
            values[key] = (float(value), float(np.clip(score, 0, 100)))
    knee_preparation = {leg: _valid([row[f"{leg}_knee_flexion"] for row in prep]) for leg in ("left", "right")}
    loads = [float(np.nanquantile(v, .75)) for v in knee_preparation.values() if v is not None]
    if loads:
        v = max(loads)
        put("knee_load", v, _ramp(v, 0, 15))
    stance = _valid([row["stance_width_hips"] for row in prep])
    if stance is not None and meta["family"] != "serve":
        v = float(np.nanmedian(stance))
        put("support_control", v, _ramp(v, .5, 1.))
    sep = _smooth([row["separation"] for row in rows])
    prep_sep = sep[:prep_end]
    coil = float(np.quantile(prep_sep, .9))
    put("coil", coil, _band(coil, 0, 15, 45, 80))
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
    heads = [r["head"] for r in core]
    if sum(v is not None for v in heads) / len(heads) >= .7:
        # Ear midpoint preferred. Ignore front/back nose motion from normal head turns.
        h = np.asarray([v for v in heads if v is not None])[:, :2]
        v = float(np.quantile(np.linalg.norm(h - np.median(h, axis=0), axis=1), .9))
        put("head_control", v, 100 - _ramp(v, .05, .2))
    reach = _valid([r[f"{side}_reach"] for r in core])
    if reach is not None:
        v = float(np.nanmedian(reach))
        put("arm_space", v, _band(v, .3, .65, 1, 1.15))
    steps = np.linalg.norm(np.diff(wrist, axis=0), axis=1)
    before, after = float(sum(steps[:peak])), float(sum(steps[peak:]))
    if before >= .12:
        v = after / before
        post_steps = steps[peak:]
        active_slice = post_steps[:max(2, int(math.ceil(len(post_steps) * 2 / 3)))]
        active_fraction = float(np.mean(active_slice > .012))
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
    return {"episode_id": meta["episode_id"], "family": meta["family"], "start_ms": round(t[0], 3),
            "peak_ms": round(t[peak], 3), "end_ms": round(t[-1], 3), "person_track_id": rows[0]["track"],
            "primary_player_id": rows[0]["person"],
            "source_track_id": rows[0]["track"], "source_selection_epoch": rows[0]["reference"][0],
            "camera_reference_epoch": rows[0]["reference"][1], "active_arm": side,
            "candidate_window": {key: meta[key] for key in ("candidate_start_ms", "candidate_peak_ms", "candidate_end_ms")},
            "quality_reason_codes": ["preparation_start_clipped_by_source_boundary"] if meta.get("preparation_start_clipped") else [],
            "phase_refinement": "source_bound_serve_overhead_pivot" if meta["family"] == "serve" else "native_3d_active_arm_in_bounded_candidate_context",
            "anchor_semantics": "source_bound_overhead_extension_not_ball_contact" if meta["family"] == "serve" else "body_relative_arm_motion_peak_not_ball_contact",
            "frame_count": len(rows), "metrics": values}


def _empty(job_id, sha):
    return {"version": VERSION, "status": "unavailable", "source_run_id": job_id, "source_video_sha256": sha,
            "source_timestamp_semantics": "original_decoder_pts", "overall_score": None,
            "dimensions": [{"id": key, "label": label, "score": None, "observation_count": 0,
                            "metrics": [{"id": m, "label": title, "unit": unit, "target": target, "weight": 1,
                                         "value": None, "score": None, "observation_count": 0} for m, title, unit, target in METRICS[key]]}
                           for key, label in DIMENSIONS],
            "strengths": [], "improvements": [], "episodes": [], "observation_count": 0,
            "reason_zh": "尚无足够连续的主球员挥拍姿态。", "quality_reason_counts": {},
            "rubric": {"version": RUBRIC_VERSION, "semantics": "engineering_action_quality", "coach_calibrated": False,
                       "identity_labels_used": False, "confidence_used_in_score": False,
                       "aggregation": "mean_episode_dimension_scores_then_equal_dimension_mean",
                       "metric_weight_semantics": "sum_inverse_available_metrics_per_episode_divided_by_dimension_episode_count",
                       "coordinate_reference": "per_frame_hip_torso_body_basis", "ball_contact_assumed": False,
                       "score_range": [0, 100], "minimum_metric_coverage": .7,
                       "numeric_rule_origin": "versioned_engineering_initial_rules",
                       "action_window_semantics": "continuous_body_relative_arm_motion_not_verified_ball_strike",
                       "phase_velocity_estimator": "centered_five_observation_linear_fit_actual_decoder_pts",
                       "profiles": ["groundstroke_general", "serve"],
                       "serve_profile": SERVE_RUBRIC,
                       "rules": {
                           "knee_load": {"curve": "increasing_saturating_ramp", "knots": [0, 15], "measurement": "full_preparation_per_leg_p75_then_max", "semantics": "preparation_participation_not_unique_optimal_knee_depth"},
                           "support_control": {"curve": "increasing_saturating_ramp", "knots": [.5, 1.], "geometry": "three_dimensional_ankle_distance_divided_by_hip_width", "semantics": "ready_stance_extent_not_balance_or_ground_reaction", "families": ["baseline", "return"]},
                           "coil": {"curve": "trapezoid", "knots": [0, 15, 45, 80]},
                           "preparation_sync": {"curve": "decreasing_ramp", "knots": [.15, .5], "minimum_separation_range_deg": 3, "minimum_wrist_path_torso": .12},
                           "release_sequence": {"curve": "decreasing_ramp", "knots": [0, .5], "minimum_joint_range_deg": 3, "simultaneous_tolerance_frames": 1},
                           "coil_release": {"curve": "increasing_saturating_ramp", "knots": [.05, .55], "minimum_preparation_separation_deg": 5},
                           "head_control": {"curve": "decreasing_ramp", "knots": [.05, .2]},
                           "arm_space": {"curve": "trapezoid", "knots": [.3, .65, 1, 1.15]},
                           "follow_path": {"curve": "equal_mean_path_and_sustained_motion", "path_knots": [.05, .35], "active_fraction_knots": [.35, .8]},
                           "recovery_control": {"curve": "equal_mean_angular_motion_continuity_and_knee_load", "excess_velocity_variation_knots": [.5, 4.],
                                                "angle_noise_floor_deg": 3., "normal_velocity_envelope": "two_times_max_absolute_velocity",
                                                "angle_semantics": "signed_relative_axis_unwrapped_modulo_180_degrees", "knee_knots": [0, 15]}}}}


def analyze_tennis_assessment(timeline, records, *, video_sha256, source_run_id=None, motion_episodes=()):
    """Pure CPU entry point, useful for regression and independent recomputation."""
    result = _empty(source_run_id, video_sha256.lower())
    result["dimensions"] = []
    segments, reasons = _segments(timeline, records, video_sha256.lower())
    episodes = [_score_window(segment, window) for segment, window in _bound_windows(segments, motion_episodes, video_sha256.lower())]
    result["episodes"] = [{**e, "metrics": {k: {"value": round(v[0], 5), "score": round(v[1], 2)} for k, v in e["metrics"].items()}} for e in episodes]
    result["quality_reason_counts"] = dict(reasons)
    result["observation_count"] = len(episodes)
    families = {e["family"] for e in episodes}
    for identifier, label in DIMENSIONS:
        definitions = dict((row[0], row) for row in (SERVE_METRICS[identifier] if families == {"serve"} else METRICS[identifier]))
        if "serve" in families:
            for row in SERVE_METRICS[identifier]:
                definitions.setdefault(row[0], row)
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
        result["strengths"] = [{"title": d["label"], "observation": f"{d['observation_count']} 次动作中，{d['label']}为 {d['score']:.1f} 分。"}
                               for d in sorted(available, key=lambda d: -d["score"])[:2] if d["score"] >= 60]
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
    keys = {(r.get("source_frame_index"), r.get("source_track_id")) for r in timeline}
    records = [r for r in _read_rows(directory / "sam3d_frames.jsonl") if (r.get("frame_index"), r.get("person_track_id")) in keys]
    result = analyze_tennis_assessment(timeline, records, video_sha256=sha, source_run_id=job_id, motion_episodes=json.loads(episodes_json))
    if signatures != tuple(_signature(directory / name) for name in ("primary-player.jsonl", "sam3d_frames.jsonl")):
        raise ValueError("assessment_inputs_changed_during_read")
    return result


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
        signatures = tuple(_signature(directory / name) for name in ("primary-player.jsonl", "sam3d_frames.jsonl"))
        return deepcopy(_cached(str(directory), signatures, sha.lower(), job_id, json.dumps(_summary_episodes(summary), sort_keys=True)))
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        result["reason_zh"] = "动作姿态记录缺失或来源校验未通过。"
        return result
