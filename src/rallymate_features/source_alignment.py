"""Source-clause measurements with explicit image-plane operational definitions.

These outputs supplement old features. Their names/units never reinterpret a
legacy feature, and their evidence gates never authorize a technical grade.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from rallymate_events.associations import SuccessorEventIndex, event_identity, finite_time
from rallymate_features.coordinates import body_scale
from rallymate_features.geometry import angle_three_points_deg
from rallymate_features.schemas import PoseSequence
from rallymate_features.validity import DEFAULT_KEYPOINT_CONFIDENCE_MIN


VERSION = "source-aligned-fs-measurements-v1.0.0"
SOURCE_PHASE_VERSION = "source-preload-turning-point-v1.0.0"
JOINTS = ("left_shoulder", "right_shoulder", "left_hip", "right_hip",
          "left_knee", "right_knee", "left_ankle", "right_ankle")
HIPS, KNEES, ANKLES = JOINTS[2:4], JOINTS[4:6], JOINTS[6:8]
MAX_GAP_MS = 160  # Existing engineering time-gap policy; not a Word grade threshold.
SOURCE_LOCATORS = {
    "FS01-M02": ("CARD-FS:word/document.xml:P0115", "CARD-FS:word/document.xml:P0127"),
    "FS01-M04": ("CARD-FS:word/document.xml:P0185", "CARD-FS:word/document.xml:P0197"),
    "FS01-M05": ("CARD-FS:word/document.xml:P0220", "CARD-FS:word/document.xml:P0232"),
    "FS09-M05": ("CARD-FS:word/document.xml:P1644", "CARD-FS:word/document.xml:P1656"),
}
CORE_GROUPS = {"FS01-M02": (HIPS, KNEES), "FS01-M04": (ANKLES,),
               "FS01-M05": (ANKLES, HIPS), "FS09-M05": (JOINTS,)}
SOURCE_REQUIRED_JOINTS = {"FS01-M02": JOINTS, "FS01-M04": HIPS + KNEES + ANKLES,
                          "FS01-M05": JOINTS, "FS09-M05": JOINTS}


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


@dataclass
class SourceAlignedContext:
    sequence: PoseSequence
    source_events: Sequence[Mapping[str, Any]]
    events: SuccessorEventIndex
    points: dict[str, np.ndarray]
    masks: dict[str, np.ndarray]
    scale: np.ndarray
    hip: np.ndarray
    center: np.ndarray
    knees: dict[str, np.ndarray]
    primary_timeline: Sequence[Mapping[str, Any]] | None
    identity_rows: dict[int, Mapping[str, Any]]

    def indexes(self, start: float, end: float) -> np.ndarray:
        times = self.sequence.timestamp_ms
        return np.arange(np.searchsorted(times, start, side="left"), np.searchsorted(times, end, side="right"))

    def coverage(self, indexes: np.ndarray, groups: Sequence[tuple[str, ...]],
                 required_joints: tuple[str, ...] | None = None) -> dict:
        rows = []
        for joints in groups:
            valid = np.logical_and.reduce([self.masks[name][indexes] for name in joints])
            count, total = int(valid.sum()), int(indexes.size)
            rows.append({"joint_names": list(joints), "valid_frame_count": count, "total_frame_count": total,
                         "fraction": count / total if total else None,
                         "meets_70_percent": bool(total and count * 10 >= total * 7)})
        required_joints = required_joints or tuple(dict.fromkeys(name for group in groups for name in group))
        valid = np.logical_and.reduce([self.masks[name][indexes] for name in required_joints])
        total, count = int(indexes.size), int(valid.sum())
        return {"scope": "inclusive_window_all_sampled_source_frames", "groups": rows,
                "required_joint_names": list(required_joints), "valid_frame_count": count,
                "total_frame_count": total, "fraction": count / total if total else None,
                "passes": bool(total and count * 10 >= total * 7),
                "threshold": .7, "operator": ">=", "source_rule": "below_70_percent_is_unavailable",
                "point_validity": {"minimum_confidence": DEFAULT_KEYPOINT_CONFIDENCE_MIN,
                    "semantics": "existing_engineering_confidence_cutoff_not_specified_by_Word; finite_xy_and_confidence_in_0_to_1"},
                "group_semantics": "all_named_joints_simultaneously_observed_in_each_counted_frame",
                "interpretation": "conservative_simultaneous_required_joint_mask; Word_does_not_define_confidence_or_joint_combination_protocol"}

    def window_reason(self, indexes: np.ndarray, start: float, end: float, *, minimum_samples: int = 2) -> str | None:
        sequence = self.sequence
        if not indexes.size or sequence.timestamp_ms[0] > start or sequence.timestamp_ms[-1] < end:
            return "source_window_not_fully_recorded"
        if indexes.size < minimum_samples:
            return "insufficient_window_samples"
        times, frames = sequence.timestamp_ms[indexes], sequence.source_frames[indexes]
        if not np.isfinite(times).all() or np.any(np.diff(times) <= 0) or np.any(np.diff(times) > MAX_GAP_MS) or np.any(np.diff(frames) != 1):
            return "source_time_or_frame_gap"
        if sequence.camera_motion_status is None or sequence.camera_reference_epochs is None:
            return "camera_reference_unverified"
        if any(sequence.camera_motion_status[i] not in {"reference", "stationary", "compensated"} for i in indexes):
            return "camera_reference_unavailable"
        epochs = {sequence.camera_reference_epochs[i] for i in indexes}
        if None in epochs or len(epochs) != 1:
            return "camera_reference_discontinuity"
        return None


_CACHE: dict[tuple[int, int, int], SourceAlignedContext] = {}


def prepare_source_aligned_context(sequence: PoseSequence,
                                   events: Sequence[Mapping[str, Any]], *,
                                   primary_timeline: Sequence[Mapping[str, Any]] | None = None) -> SourceAlignedContext:
    key = id(sequence), id(events), id(primary_timeline)
    cached = _CACHE.get(key)
    if cached is not None and cached.sequence is sequence and cached.source_events is events:
        return cached
    points, masks = {}, {}
    size = len(sequence.timestamp_ms)
    for name in JOINTS:
        xy = np.asarray(sequence.keypoints_xy.get(name, np.full((size, 2), np.nan)), dtype=float).copy()
        confidence = np.asarray(sequence.confidence.get(name, np.full(size, np.nan)), dtype=float)
        valid = np.isfinite(xy).all(axis=1) & np.isfinite(confidence) & (confidence >= DEFAULT_KEYPOINT_CONFIDENCE_MIN) & (confidence <= 1)
        xy[~valid] = np.nan
        points[name], masks[name] = xy, valid
    hip = (points["left_hip"] + points["right_hip"]) / 2
    shoulders = (points["left_shoulder"] + points["right_shoulder"]) / 2
    clean_sequence = replace(sequence, keypoints_xy=points, confidence={name: np.where(masks[name], sequence.confidence.get(name, np.nan), np.nan) for name in JOINTS})
    scale, _ = body_scale(clean_sequence)
    knees = {side: 180 - angle_three_points_deg(points[f"{side}_hip"], points[f"{side}_knee"], points[f"{side}_ankle"])
             for side in ("left", "right")}
    identity_rows = {}
    for row in primary_timeline or []:
        if not isinstance(row, Mapping) or not isinstance(row.get("source_frame_index"), int) or isinstance(row["source_frame_index"], bool):
            continue
        frame = row["source_frame_index"]
        if frame in identity_rows and identity_rows[frame] != row:
            identity_rows[frame] = {}  # Conflicting observations cannot overwrite identity evidence.
        else:
            identity_rows[frame] = row
    context = SourceAlignedContext(sequence, events, SuccessorEventIndex(events), points, masks, scale,
                                   hip, (hip + shoulders) / 2, knees, primary_timeline, identity_rows)
    # Keep bounded caller-owned snapshots; callers may explicitly clear after
    # writing an artifact. Inputs must not be mutated while a context is reused.
    if len(_CACHE) >= 2:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = context
    return context


def clear_source_aligned_feature_cache(sequence: PoseSequence | None = None) -> None:
    for key in list(_CACHE):
        if sequence is None or _CACHE[key].sequence is sequence:
            del _CACHE[key]


def _identity_reason(context: SourceAlignedContext, event: Mapping) -> str | None:
    identity = event_identity(event)
    if identity is None or identity[1] != context.sequence.primary_player_id:
        return "subject_or_video_identity_unverified"
    diagnostics = _mapping(event.get("track_diagnostics"))
    if diagnostics.get("primary_identity_ambiguous") is not False:
        return "subject_identity_continuity_unverified"
    if diagnostics.get("source_track_switch_candidate_count") != 0:
        return "subject_track_switch_or_continuity_unverified"
    if identity in context.events.conflicts or context.events.events.get(identity) != event:
        return "event_conflicting_or_not_in_snapshot"
    return None


def _phase(event: Mapping, name: str) -> tuple[float | None, str | None]:
    value = finite_time(_mapping(event.get("key_phases_ms")).get(name))
    if value is None or not event["start_ms"] <= value <= event["end_ms"]:
        return None, "phase_not_observed_or_outside_event:" + name
    flags = event.get("quality_flags", [])
    if any(isinstance(flag, str) and flag.endswith(":" + name) and ("censored" in flag or "low_sample_peak" in flag) for flag in flags):
        return None, "phase_is_censored_peak_not_confirmed_slowdown:" + name
    return value, None


def _row(context: SourceAlignedContext, *, indicator: str, name: str, label: str, unit: str,
         definition: str, start: float, end: float, required: tuple[str, ...], reason: str | None,
         value: float | None = None, indexes: np.ndarray | None = None, evidence: dict | None = None,
         minimum_samples: int = 2) -> dict:
    indexes = context.indexes(start, end) if indexes is None else indexes
    coverage = context.coverage(indexes, (required,))
    reason = reason or context.window_reason(indexes, start, end, minimum_samples=minimum_samples)
    reason = reason or (None if coverage["passes"] else "required_joint_coverage_below_70_percent")
    valid = bool(reason is None and value is not None and np.isfinite(value))
    return {"feature_name": name, "name_zh": label, "unit": unit,
            "status": "measured" if valid else "unavailable", "valid": valid,
            "value": float(value) if valid else None, "reason": "observed_measurement" if valid else reason or "measurement_not_observed",
            "source_frames": [int(frame) for frame in context.sequence.source_frames[indexes]],
            "window_ms": {"start_ms": start, "end_ms": end}, "definition": definition,
            "coverage": coverage, "source_ref": SOURCE_LOCATORS[indicator][0], "evidence": evidence or {},
            "value_semantics": "observed_image_plane_quantity_not_technique_grade",
            "zero_semantics": "measured_zero_is_not_missing_and_does_not_assign_E_grade"}


def _scale(context: SourceAlignedContext, indexes: np.ndarray) -> float | None:
    values = context.scale[indexes]
    values = values[np.isfinite(values) & (values > 1e-6)]
    return float(np.median(values)) if values.size else None


def _speed_summary(context: SourceAlignedContext, indexes: np.ndarray, scale: float | None) -> dict:
    if scale is None or indexes.size < 3:
        return {"std": None, "rate": None, "valid_interval_count": 0}
    times = context.sequence.timestamp_ms[indexes].astype(float) / 1000
    points = context.center[indexes]
    delta = np.diff(times)
    valid = np.isfinite(points[:-1]).all(axis=1) & np.isfinite(points[1:]).all(axis=1) & (delta > 0) & (delta * 1000 <= MAX_GAP_MS)
    velocities = np.full(delta.shape, np.nan)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        velocities[valid] = np.linalg.norm(np.diff(points, axis=0)[valid], axis=1) / delta[valid] / scale
    valid &= np.isfinite(velocities)
    velocities[~valid] = np.nan
    midtimes = (times[:-1] + times[1:]) / 2
    pairs = np.isfinite(velocities[:-1]) & np.isfinite(velocities[1:])
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        rates = np.abs(np.diff(velocities)[pairs] / np.diff(midtimes)[pairs])
        std = float(np.std(velocities[valid])) if valid.sum() >= 2 else None
    rates = rates[np.isfinite(rates)]
    return {"std": std if std is not None and np.isfinite(std) else None,
            "rate": float(np.median(rates)) if rates.size else None,
            "valid_interval_count": int(valid.sum()), "total_interval_count": int(valid.size),
            "timestamps_ms": [float(t * 1000) for t in midtimes],
            "speed_body_s": [float(v) if np.isfinite(v) else None for v in velocities],
            "derivative_policy": "adjacent_observed_intervals_only_no_interpolation_no_cross_gap_stencil"}


def locate_source_preload(context: SourceAlignedContext, event: Mapping) -> dict:
    """Find an observed pre-takeoff dip, preserving the original phase intact.

    Two increasing and two decreasing observed changes reject a lone pose
    spike. Flat quantization plateaus may connect changes; missing points do
    not. This is an explicit candidate-support rule, not a coaching cutoff.
    """
    original = finite_time(_mapping(event.get("key_phases_ms")).get("preload_ms"))
    result = {"phase_contract_version": SOURCE_PHASE_VERSION, "original_preload_ms": original,
              "source_preload_ms": None, "source_preload_start_ms": None, "status": "unavailable",
              "repair_reason": "pre_takeoff_descent_turn_not_observed", "source_frames": [],
              "selection_semantics": "deepest_observed_pre_takeoff_hip_y_turn_with_two_descent_and_two_ascent_changes; plateau_allowed_no_interpolation",
              "is_ground_truth": False, "original_event_modified": False,
              "minimum_directional_changes_each_side": 2,
              "support_rule_semantics": "engineering_observation_support_not_Word_technical_threshold"}
    takeoff, reason = _phase(event, "takeoff_proxy_ms")
    if reason is not None:
        return {**result, "repair_reason": reason}
    indexes = context.indexes(event["start_ms"], takeoff)
    reason = _identity_reason(context, event) or context.window_reason(indexes, event["start_ms"], takeoff)
    if reason is not None:
        return {**result, "repair_reason": reason}
    if indexes.size < 5:
        return {**result, "repair_reason": "pre_takeoff_window_too_short_for_turn_evidence"}
    y = context.hip[indexes, 1]
    changes = np.diff(y)
    epsilon = 1e-12  # Coordinate arithmetic guard, not meaningful motion size.
    candidates = []
    for peak in range(2, len(y) - 2):
        if (not np.isfinite(y[peak]) or not np.isfinite(changes[peak]) or changes[peak] >= -epsilon
                or not np.isfinite(changes[peak - 1]) or changes[peak - 1] < -epsilon):
            continue
        left, right, down, up = peak, peak, 0, 0
        while left > 0 and np.isfinite(changes[left - 1]) and changes[left - 1] >= -epsilon:
            down += changes[left - 1] > epsilon
            left -= 1
        while right < len(changes) and np.isfinite(changes[right]) and changes[right] <= epsilon:
            up += changes[right] < -epsilon
            right += 1
        if down >= 2 and up >= 2 and y[peak] - y[left] > epsilon:
            candidates.append((float(y[peak]), peak, left, right))
    if not candidates:
        boundary_peak = bool(np.isfinite(y).any() and (np.nanargmax(y) == 0 or np.nanargmax(y) == len(y) - 1))
        return {**result, "repair_reason": "descent_turn_boundary_censored" if boundary_peak else result["repair_reason"]}
    _, peak, left, right = max(candidates, key=lambda item: (item[0], -item[1]))
    if left == 0:
        return {**result, "repair_reason": "source_preload_start_boundary_censored"}
    if not np.isfinite(y[left - 1]):
        return {**result, "repair_reason": "source_preload_start_preceding_observation_missing"}
    support = indexes[left:right + 1]
    coverage = context.coverage(support, (HIPS,))
    if not coverage["passes"]:
        return {**result, "repair_reason": "preload_turn_hip_coverage_below_70_percent"}
    start, end = float(context.sequence.timestamp_ms[indexes[left]]), float(context.sequence.timestamp_ms[indexes[peak]])
    return {**result, "status": "observed_candidate", "source_preload_ms": end, "source_preload_start_ms": start,
            "repair_reason": "original_phase_matches_supported_turn" if original == end else
                             "original_preload_after_takeoff_replaced_by_observed_turn" if original is not None and original > takeoff else
                             "independent_pre_takeoff_turn_observed",
            "source_frames": [int(value) for value in context.sequence.source_frames[support]],
            "takeoff_proxy_ms": takeoff, "confirmation_end_ms": float(context.sequence.timestamp_ms[indexes[right]]),
            "numerical_coordinate_epsilon": epsilon}


def _preload(context: SourceAlignedContext, event: Mapping, reason: str | None) -> list[dict]:
    indicator = "FS01-M02"
    start, end = event["start_ms"], event["end_ms"]
    phase = locate_source_preload(context, event)
    if phase["status"] == "observed_candidate":
        start, end = phase["source_preload_start_ms"], phase["source_preload_ms"]
    else:
        reason = reason or phase["repair_reason"]
    indexes = context.indexes(start, end)
    scale = _scale(context, indexes)
    reason = reason or (None if scale is not None else "body_scale_unavailable")
    endpoints = bool(indexes.size >= 2 and context.sequence.timestamp_ms[indexes[0]] == start
                     and context.sequence.timestamp_ms[indexes[-1]] == end)
    reason = reason or (None if endpoints else "exact_phase_endpoint_observation_missing")
    values = {}
    if endpoints and scale is not None:
        first, last = indexes[0], indexes[-1]
        ends = np.asarray([first, last])
        ankles = (context.points["left_ankle"][ends] + context.points["right_ankle"][ends]) / 2
        height = (ankles[:, 1] - context.hip[ends, 1]) / scale
        values["preload_hip_height_drop_body"] = height[0] - height[1]
        for side in ("left", "right"):
            values[f"{side}_preload_knee_flexion_change_deg"] = context.knees[side][last] - context.knees[side][first]
    speed = _speed_summary(context, indexes, scale)
    definitions = [
        ("preload_hip_height_drop_body", "预加载髋高度下降量", "body", HIPS + ANKLES,
         "ankle-midpoint-relative hip height at observed descent start minus height at independently supported pre-takeoff turn; divided by window median body scale; image-up positive height"),
        *[(f"{side}_preload_knee_flexion_change_deg", f"{'左' if side == 'left' else '右'}膝预加载屈曲变化", "deg", (f"{side}_hip", f"{side}_knee", f"{side}_ankle"),
           "2D knee flexion at independently supported preload turn minus flexion at observed descent start; flexion=180-internal hip-knee-ankle angle") for side in ("left", "right")],
        ("preload_body_speed_change_rate_body_s2", "预加载速度变化率", "body/s2", JOINTS[:4],
         "median absolute change in adjacent observed image-plane body-center interval speeds divided by interval-midpoint time difference; not a calibrated continuity score"),
    ]
    values[definitions[-1][0]] = speed["rate"]
    return [_row(context, indicator=indicator, name=name, label=label, unit=unit, definition=definition,
                 start=start, end=end, required=required, reason=reason, value=values.get(name), indexes=indexes,
                 evidence={"phase_key": "preload_ms", "phase_semantics": "unvalidated_pose_candidate_not_ground_truth",
                           "source_phase": phase, "body_scale": scale, **({"speed_continuity_observations": speed} if name == definitions[-1][0] else {})})
            for name, label, unit, required, definition in definitions]


def _post_slowdown_ratio(context: SourceAlignedContext, event: Mapping, reason: str | None) -> list[dict]:
    start, phase_error = _phase(event, "landing_proxy_ms")
    start = event["start_ms"] if start is None else start
    end = event["end_ms"]
    indexes = context.indexes(start, end)
    hips = context.points["right_hip"][indexes] - context.points["left_hip"][indexes]
    hip_width = np.linalg.norm(hips, axis=1)
    ankle_width = np.abs(context.points["right_ankle"][indexes, 0] - context.points["left_ankle"][indexes, 0])
    valid = np.isfinite(hip_width) & np.isfinite(ankle_width) & (hip_width > 1e-6)
    ratios = ankle_width[valid] / hip_width[valid]
    invalid_denominator = bool(not indexes.size or int(valid.sum()) * 10 < indexes.size * 7)
    return [_row(context, indicator="FS01-M04", name="post_slowdown_ankle_width_to_hip_width_ratio",
                 label="减速后踝距与髋宽之比", unit="ratio", start=start, end=end, required=HIPS + ANKLES,
                 reason=reason or phase_error or ("hip_width_observability_below_70_percent_or_degenerate" if invalid_denominator else None),
                 value=float(np.median(ratios)) if ratios.size else None, indexes=indexes,
                 definition="median of observed image-horizontal ankle separation divided by 2D inter-hip distance after the landing slowdown candidate; not body-scale normalization or confirmed ground contact",
                 evidence={"phase_key": "landing_proxy_ms", "valid_ratio_samples": int(ratios.size),
                           "denominator": "same_frame_2d_inter_hip_distance", "numerator": "same_frame_image_horizontal_ankle_distance",
                           "minimum_denominator": 1e-6, "denominator_policy": "coordinate_numerical_guard_not_anatomical_or_grade_threshold"})]


def _transition(context: SourceAlignedContext, event: Mapping, indicator: str, reason: str | None) -> tuple[list[dict], dict]:
    is_split = indicator == "FS01-M05"
    phase_key = "landing_proxy_ms" if is_split else "stable_control_onset_ms"
    anchor, phase_error = _phase(event, phase_key)
    codes = ("FS02",) if is_split else ("FS10", "FS02")
    association = context.events.next_event(event, after_ms=anchor if anchor is not None else event["end_ms"], allowed_codes=codes)
    successor = association.pop("event")
    association.update({"anchor_phase_key": phase_key, "anchor_ms": anchor,
                        "source_event_id": event["event_id"], "successor_event_id": successor.get("event_id") if successor else None,
                        "successor_event_code": successor.get("event_code") if successor else None,
                        "successor_start_ms": successor.get("start_ms") if successor else None,
                        "same_person_track_id": event["person_track_id"], "new_events_generated": False,
                        "semantics": "observed_candidate_association_not_confirmed_action_or_grade"})
    start = anchor if anchor is not None else event["start_ms"]
    end = successor["start_ms"] if successor else event["end_ms"]
    link_reason = reason or phase_error or (None if successor else association["reason"])
    if successor:
        link_reason = link_reason or _identity_reason(context, successor)
        # Track identity evidence on both event windows is necessary but does
        # not certify an otherwise unobserved gap as continuous identity.
        source_tracks = _mapping(event.get("track_diagnostics")).get("source_track_ids")
        target_tracks = _mapping(successor.get("track_diagnostics")).get("source_track_ids")
        if not (isinstance(source_tracks, list) and len(source_tracks) == 1 and source_tracks == target_tracks):
            link_reason = link_reason or "successor_source_track_identity_unverified"
    indexes = context.indexes(start, end)
    minimum_samples = 1 if start == end else 2
    continuity_error = context.window_reason(indexes, start, end, minimum_samples=minimum_samples)
    association["camera_and_source_time_continuity"] = "verified" if continuity_error is None else "unavailable"
    association["continuity_reason"] = continuity_error
    association["event_boundary_gap_ms"] = successor["start_ms"] - event["end_ms"] if successor else None
    identity_error = None
    if successor and context.primary_timeline is not None:
        expected_track = _mapping(event.get("track_diagnostics")).get("source_track_ids", [])
        for index in indexes:
            row = context.identity_rows.get(int(context.sequence.source_frames[index]), {})
            if (row.get("selection_status") != "selected" or row.get("identity_ambiguous") is not False
                    or row.get("primary_player_id") != event["person_track_id"]
                    or len(expected_track) != 1 or row.get("source_track_id") != expected_track[0]
                    or row.get("timestamp_ms") != int(context.sequence.timestamp_ms[index])):
                identity_error = "between_event_subject_continuity_unverified"
                break
    elif successor and successor["start_ms"] > event["end_ms"]:
        identity_error = "between_event_subject_continuity_unverified"
    subject_error = reason or (_identity_reason(context, successor) if successor else association["reason"])
    if successor and not (isinstance(source_tracks, list) and len(source_tracks) == 1 and source_tracks == target_tracks):
        subject_error = subject_error or "successor_source_track_identity_unverified"
    subject_error = subject_error or identity_error
    association["subject_continuity"] = "verified_observed_track" if successor and subject_error is None else "unavailable"
    association["subject_continuity_reason"] = subject_error
    link_reason = link_reason or identity_error
    if successor and (not indexes.size or context.sequence.timestamp_ms[indexes[0]] != start
                      or context.sequence.timestamp_ms[indexes[-1]] != end):
        link_reason = link_reason or "exact_phase_endpoint_observation_missing"
    name = "landing_proxy_to_next_fs02_ms" if is_split else "stable_control_proxy_to_next_fs10_or_fs02_ms"
    row = _row(context, indicator=indicator, name=name, label="落地候选到后续启动间隔" if is_split else "稳定候选到后续事件间隔",
               unit="ms", start=start, end=end, required=JOINTS, reason=link_reason,
               value=end - start if successor and anchor is not None else None, indexes=indexes,
               minimum_samples=minimum_samples,
               definition="next observed same-video same-player allowed event start minus current observed phase candidate; no successor means missing evidence, not zero or E",
               evidence={"source_event_id": event["event_id"], "successor_event_id": association["successor_event_id"],
                         "anchor_phase_key": phase_key, "candidates_remain_unvalidated": True})
    speed_end = min(end, event["end_ms"])
    speed_indexes = context.indexes(start, speed_end)
    speed = _speed_summary(context, speed_indexes, _scale(context, speed_indexes))
    stability = _row(context, indicator=indicator,
        name="post_landing_body_speed_std_body_s" if is_split else "stable_control_body_speed_std_body_s",
        label="落地候选后速度波动" if is_split else "稳定候选后速度波动", unit="body/s", start=start, end=speed_end,
        required=JOINTS[:4], reason=reason or phase_error, value=speed["std"], indexes=speed_indexes,
        definition="population standard deviation of adjacent observed body-center interval speeds inside the event after the phase candidate; no grade or stable/unstable cutoff",
        evidence={"speed_observations": speed})
    association["link_measurement_status"] = row["status"]
    return [row, stability], association


def compute_source_aligned_features(sequence: PoseSequence, event: Mapping[str, Any], *,
                                    events: Sequence[Mapping[str, Any]],
                                    context: SourceAlignedContext | None = None) -> dict[str, Any]:
    context = context or prepare_source_aligned_context(sequence, events)
    if context.sequence is not sequence or context.source_events is not events:
        raise ValueError("Prepared source context does not belong to these inputs")
    result = {"version": VERSION, "video_id": event.get("video_id"), "event_id": event.get("event_id"),
              "person_track_id": event.get("person_track_id"), "event_code": event.get("event_code"),
              "indicators": [], "grade": None, "technical_score_0_to_100": None}
    start, end = finite_time(event.get("start_ms")), finite_time(event.get("end_ms"))
    if start is None or end is None or start >= end:
        return {**result, "status": "unavailable", "reason": "invalid_event_window"}
    if event.get("event_code") not in {"FS01", "FS09"}:
        return {**result, "status": "not_applicable"}
    indexes = context.indexes(start, end)
    reason = _identity_reason(context, event) or context.window_reason(indexes, start, end)
    for indicator in (("FS01-M02", "FS01-M04", "FS01-M05") if event["event_code"] == "FS01" else ("FS09-M05",)):
        core_coverage = context.coverage(indexes, CORE_GROUPS[indicator], SOURCE_REQUIRED_JOINTS[indicator])
        indicator_reason = reason or (None if core_coverage["passes"] else "source_required_joint_coverage_below_70_percent")
        association = None
        if indicator == "FS01-M02":
            rows = _preload(context, event, indicator_reason)
        elif indicator == "FS01-M04":
            rows = _post_slowdown_ratio(context, event, indicator_reason)
        else:
            rows, association = _transition(context, event, indicator, indicator_reason)
        available = any(row["status"] == "measured" for row in rows)
        limitations = ["仅提供画面二维测量，不代表真实重心、地面接触、发力或技术等级。",
                       "阶段与事件仍是待人工核验的候选；原文没有给出数值等级切点。",
                       "基线端点、归一化尺度及速度摘要是明确公开的工程操作定义，原文未规定这些数值细节。"]
        unresolved = ["event_and_phase_ground_truth_required", "coach_calibration_required", "independent_test_required"]
        if indicator == "FS01-M02":
            unresolved.append("vertical_view_distortion_not_independently_assessed")
        if indicator in {"FS01-M04", "FS01-M05"}:
            unresolved.append("heel_toe_and_ground_calibration_required_for_confirmed_contact")
        if indicator == "FS09-M05":
            unresolved.append("double_support_is_pose_proxy_not_confirmed_contact")
            limitations.append("FS10未由本模块识别；仅关联输入中已存在的同人FS10或FS02候选。")
        if association is not None and association["link_measurement_status"] != "measured":
            unresolved.append("observed_successor_identity_and_continuity_not_established")
        result["indicators"].append({"indicator_id": indicator, "status": "partial" if available else "unavailable",
            "source_measurements": rows, "event_association": association,
            "source_rule_gate": {"status": "partial" if available else "unavailable", "complete_source_rule_verified": False,
                "pose_coverage": core_coverage, "pose_gate_reason": indicator_reason,
                "unresolved_conditions": unresolved, "source_ref": SOURCE_LOCATORS[indicator][1]},
            "limitations_zh": limitations, "grade": None, "technical_score_0_to_100": None})
    result["status"] = "partial" if any(item["status"] == "partial" for item in result["indicators"]) else "unavailable"
    return result
