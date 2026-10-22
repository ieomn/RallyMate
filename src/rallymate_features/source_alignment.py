"""Source-clause measurements with explicit image-plane operational definitions.

These outputs supplement old features. Their names/units never reinterpret a
legacy feature, and their evidence gates never authorize a technical grade.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any

import numpy as np

from rallymate_events.associations import SuccessorEventIndex, event_identity, finite_time
from rallymate_features.coordinates import COORDINATE_CONTRACT_VERSION, body_scale, point_series, validated_camera_transform
from rallymate_features.geometry import angle_three_points_deg
from rallymate_features.schemas import PoseSequence, model_bindings_continuous, model_geometry_evidence, validated_source_time_binding
from rallymate_features.validity import DEFAULT_KEYPOINT_CONFIDENCE_MIN
from rallymate_scoring.quality_policy import evaluate_indicator_event_quality


VERSION = "source-aligned-fs-measurements-v1.2.0"
SOURCE_PHASE_VERSION = "source-preload-turning-point-v1.0.0"
PRELOAD_REFERENCE_VERSION = "fs01-preload-reference-translation-v1.0.0"
JOINTS = ("left_shoulder", "right_shoulder", "left_hip", "right_hip",
          "left_knee", "right_knee", "left_ankle", "right_ankle")
HIPS, KNEES, ANKLES = JOINTS[2:4], JOINTS[4:6], JOINTS[6:8]
HEAD = ("nose", "left_ear", "right_ear")  # Word J004/J006/J007; eyes are J008/J009.
MAX_GAP_MS = 160  # Existing engineering time-gap policy; not a Word grade threshold.
SOURCE_LOCATORS = {
    "FS01-M02": ("CARD-FS:word/document.xml:P0115", "CARD-FS:word/document.xml:P0127"),
    "FS01-M04": ("CARD-FS:word/document.xml:P0185", "CARD-FS:word/document.xml:P0197"),
    "FS01-M05": ("CARD-FS:word/document.xml:P0220", "CARD-FS:word/document.xml:P0232"),
    "FS09-M05": ("CARD-FS:word/document.xml:P1644", "CARD-FS:word/document.xml:P1656"),
    "FS02-M01": ("CARD-FS:word/document.xml:P0258", "CARD-FS:word/document.xml:P0270"),
    "FS01-M01": ("CARD-FS:word/document.xml:P0080", "CARD-FS:word/document.xml:P0092"),
}
CORE_GROUPS = {"FS01-M02": (HIPS, KNEES), "FS01-M04": (ANKLES,),
               "FS01-M05": (ANKLES, HIPS), "FS09-M05": (JOINTS,), "FS02-M01": (HEAD, HIPS), "FS01-M01": (HEAD,)}
SOURCE_REQUIRED_JOINTS = {"FS01-M02": JOINTS, "FS01-M04": HIPS + KNEES + ANKLES,
                          "FS01-M05": JOINTS, "FS09-M05": JOINTS, "FS02-M01": HEAD + HIPS, "FS01-M01": HEAD}


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
        result = {"scope": "inclusive_window_all_sampled_source_frames", "groups": rows,
                "required_joint_names": list(required_joints), "valid_frame_count": count,
                "total_frame_count": total, "fraction": count / total if total else None,
                "passes": bool(total and count * 10 >= total * 7),
                "threshold": .7, "operator": ">=", "source_rule": "below_70_percent_is_unavailable",
                "point_validity": {"minimum_confidence": DEFAULT_KEYPOINT_CONFIDENCE_MIN,
                    "semantics": "existing_engineering_confidence_cutoff_not_specified_by_Word; finite_xy_and_confidence_in_0_to_1"},
                "group_semantics": "all_named_joints_simultaneously_observed_in_each_counted_frame",
                "interpretation": "conservative_simultaneous_required_joint_mask; Word_does_not_define_confidence_or_joint_combination_protocol"}

        if self.sequence.model_estimate_bindings is not None:
            result.update(semantics="model_estimate_geometry_not_verified_visibility", visibility_verified=False,
                source_visibility_70_percent_verified=None,
                point_validity={"minimum_confidence": None, "semantics": "strict_source_bound_finite_in_frame_model_geometry"},
                group_semantics="all_named_model_geometries_simultaneously_available_in_each_counted_frame",
                interpretation="engineering_geometry_support_only; Word_visibility_not_verified")
        return result

    def window_reason(self, indexes: np.ndarray, start: float, end: float, *, minimum_samples: int = 2) -> str | None:
        sequence = self.sequence
        if not indexes.size or sequence.timestamp_ms[0] > start or sequence.timestamp_ms[-1] < end:
            return "source_window_not_fully_recorded"
        if indexes.size < minimum_samples:
            return "insufficient_window_samples"
        times, frames = sequence.timestamp_ms[indexes], sequence.source_frames[indexes]
        if not np.isfinite(times).all() or np.any(np.diff(times) <= 0) or np.any(np.diff(times) > MAX_GAP_MS) or np.any(np.diff(frames) != 1):
            return "source_time_or_frame_gap"
        if sequence.model_estimate_bindings is not None and (any(sequence.model_estimate_bindings[i] is None for i in indexes) or any(not model_bindings_continuous(sequence, a, b) for a, b in zip(indexes, indexes[1:]))):
            return "source_model_binding_discontinuity"
        if sequence.camera_motion_status is None or sequence.camera_reference_epochs is None:
            return "camera_reference_unverified"
        if any(sequence.camera_motion_status[i] not in {"reference", "stationary", "compensated"} for i in indexes):
            return "camera_reference_unavailable"
        epochs = {sequence.camera_reference_epochs[i] for i in indexes}
        if None in epochs or len(epochs) != 1:
            return "camera_reference_discontinuity"
        return None

    def subject_window_reason(self, indexes: np.ndarray, event: Mapping) -> str | None:
        """Verify every supplied identity observation in this actual window.

        Legacy callers without a timeline retain the existing event diagnostic
        contract. A supplied, empty or conflicting timeline is never treated as
        absent, and valid event summaries cannot override its frame evidence.
        """
        if self.primary_timeline is None:
            return None
        tracks = _mapping(event.get("track_diagnostics")).get("source_track_ids")
        if (not isinstance(tracks, list) or len(tracks) != 1
                or not isinstance(tracks[0], int) or isinstance(tracks[0], bool) or tracks[0] < 0):
            return "measurement_window_subject_continuity_unverified"
        for index in indexes:
            row = self.identity_rows.get(int(self.sequence.source_frames[index]), {})
            if (row.get("selection_status") != "selected" or row.get("identity_ambiguous") is not False
                    or not isinstance(row.get("primary_player_id"), int) or isinstance(row.get("primary_player_id"), bool)
                    or row["primary_player_id"] < 0
                    or row.get("primary_player_id") != event.get("person_track_id")
                    or not isinstance(row.get("source_track_id"), int) or isinstance(row.get("source_track_id"), bool)
                    or row.get("source_track_id") != tracks[0]
                    or isinstance(row.get("timestamp_ms"), bool)
                    or row.get("timestamp_ms") != float(self.sequence.timestamp_ms[index])):
                return "measurement_window_subject_continuity_unverified"
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
    for name in JOINTS + HEAD:
        xy, valid = point_series(sequence, name, confidence_min=DEFAULT_KEYPOINT_CONFIDENCE_MIN)
        if sequence.model_estimate_bindings is None:
            valid &= np.asarray(sequence.confidence.get(name, np.full(size, np.nan))) <= 1
            xy[~valid] = np.nan
        points[name], masks[name] = xy, valid
    hip = (points["left_hip"] + points["right_hip"]) / 2
    shoulders = (points["left_shoulder"] + points["right_shoulder"]) / 2
    clean_sequence = replace(sequence, keypoints_xy=points, confidence={
        name: np.where(masks[name], sequence.confidence.get(name, np.full(size, np.nan)), np.nan)
        for name in JOINTS + HEAD}, model_estimate_masks={name: masks[name] for name in JOINTS + HEAD} if sequence.model_estimate_bindings is not None else {})
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


def _event_quality_gate(event: Mapping) -> dict | None:
    diagnostics = _mapping(event.get("track_diagnostics"))
    flags = event.get("quality_flags", [])
    if not isinstance(flags, list) or any(not isinstance(flag, str) or not flag for flag in flags):
        return None
    flags = list(flags)
    confirmed = diagnostics.get("confirmed_id_switch_count")
    if confirmed is not None and (not isinstance(confirmed, int) or isinstance(confirmed, bool) or confirmed < 0):
        return None
    if isinstance(confirmed, int) and not isinstance(confirmed, bool) and confirmed > 0:
        flags.append("confirmed_id_switch_present")
    # Reuse the established public policy: candidate jump/swap/identity flags
    # are not confirmed measurement failures. Confirmed switches are.
    indicator = {"FS09": "FS09-M05", "FS02": "FS02-M02"}.get(event.get("event_code"), "FS01-M02")
    try:
        return evaluate_indicator_event_quality(indicator, flags)
    except ValueError:
        return None


def _identity_reason(context: SourceAlignedContext, event: Mapping) -> str | None:
    identity = event_identity(event)
    if identity is None or identity[1] < 0 or identity[1] != context.sequence.primary_player_id:
        return "subject_or_video_identity_unverified"
    diagnostics = _mapping(event.get("track_diagnostics"))
    gate = _event_quality_gate(event)
    if gate is None:
        return "source_event_quality_evidence_invalid"
    if gate["hard_fail"]:
        # The shared policy has already classified these as confirmed failures;
        # this chooses an explanation, never upgrades candidate flags.
        if any("id_switch" in flag.lower() for flag in gate["hard_fail_flags"]):
            return "confirmed_subject_identity_switch"
        return "source_event_measurement_quality_hard_fail"
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
    if not isinstance(flags, list) or any(not isinstance(flag, str) or not flag for flag in flags):
        return None, "source_event_quality_evidence_invalid"
    if any(isinstance(flag, str) and flag.endswith(":" + name) and ("censored" in flag or "low_sample_peak" in flag) for flag in flags):
        return None, "phase_is_censored_peak_not_confirmed_slowdown:" + name
    return value, None


def _row(context: SourceAlignedContext, *, event: Mapping, indicator: str, name: str, label: str, unit: str,
         definition: str, start: float, end: float, required: tuple[str, ...], reason: str | None,
         value: float | None = None, indexes: np.ndarray | None = None, evidence: dict | None = None,
         minimum_samples: int = 2) -> dict:
    indexes = context.indexes(start, end) if indexes is None else indexes
    coverage = context.coverage(indexes, (required,))
    reason = reason or context.window_reason(indexes, start, end, minimum_samples=minimum_samples)
    reason = reason or context.subject_window_reason(indexes, event)
    reason = reason or (None if coverage["passes"] else "required_joint_coverage_below_70_percent")
    valid = bool(reason is None and value is not None and np.isfinite(value))
    evidence = dict(evidence or {})
    gate = _event_quality_gate(event)
    if gate is not None and gate["hard_fail"]:
        evidence["source_event_quality_gate"] = {
            "policy_version": gate["policy_version"], "hard_fail_flags": gate["hard_fail_flags"],
            "semantics": "existing_measurement_evidence_gate_not_technical_fault_penalty",
        }
    return {"feature_name": name, "name_zh": label, "unit": unit,
            "status": "measured" if valid else "unavailable", "valid": valid,
            "value": float(value) if valid else None, "reason": "observed_measurement" if valid else reason or "measurement_not_observed",
            "source_frames": [int(frame) for frame in context.sequence.source_frames[indexes]],
            "window_ms": {"start_ms": start, "end_ms": end}, "definition": definition,
            "coverage": coverage, "source_ref": SOURCE_LOCATORS[indicator][0], "evidence": evidence,
            "value_semantics": "observed_image_plane_quantity_not_technique_grade",
            "zero_semantics": "measured_zero_is_not_missing_and_does_not_assign_E_grade",
            **({**model_geometry_evidence(context.sequence, indexes, JOINTS + HEAD), "confidence": None,
                "value_semantics": "model_estimate_image_plane_quantity_not_verified_visibility_or_technique_grade",
                "reason": "model_estimate_measurement" if valid else reason or "measurement_not_observed"}
               if context.sequence.model_estimate_bindings is not None else {})}


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
    reason = (_identity_reason(context, event) or context.window_reason(indexes, event["start_ms"], takeoff)
              or context.subject_window_reason(indexes, event))
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


def _preload_reference_translation(context: SourceAlignedContext, event: Mapping, *, phase: Mapping,
                                   start: float, end: float, indexes: np.ndarray,
                                   reason: str | None) -> list[dict]:
    """FS candidate-local translation; do not reuse GS stage labels or ankle height.

    PoseSequence coordinates already contain the validated camera transform.
    Recover reference pixels once, then use one median torso length for the
    whole observed window. Sample/duration, torso-size and confidence checks
    match preparation_translation; none is a Word grading threshold.
    """
    from rallymate_scoring.preparation_translation import MIN_DURATION_MS, MIN_SAMPLES

    sequence, required = context.sequence, JOINTS[:4]
    metadata = sequence.coordinate_metadata
    evidence = {"measurement_version": PRELOAD_REFERENCE_VERSION,
                "phase_semantics": "unvalidated_pose_candidate_not_ground_truth",
                "source_phase": dict(phase), "source_clause_complete": False,
                "coordinate_system": "stable_camera_reference_image_x_right_y_down",
                "normalization": "median_observed_full_torso_scale_fixed_per_candidate_window",
                "formula": "dy_px=hip_y_reference_px[last]-hip_y_reference_px[first]; dy_torso=dy_px/median(norm(shoulder_midpoint_px-hip_midpoint_px))",
                "minimum_samples": MIN_SAMPLES, "minimum_duration_ms": MIN_DURATION_MS,
                "gate_origin": "existing_preparation_translation_measurement_policy_not_Word_grade_threshold",
                "sign_convention": "image_down_positive", "is_metric_or_3d": False,
                "is_ground_truth": False}
    reason = reason or context.window_reason(indexes, start, end, minimum_samples=MIN_SAMPLES)
    reason = reason or context.subject_window_reason(indexes, event)
    if not reason and (sequence.timestamp_ms[indexes[0]] != start or sequence.timestamp_ms[indexes[-1]] != end):
        reason = "exact_phase_endpoint_observation_missing"
    if (metadata.get("contract_version") != COORDINATE_CONTRACT_VERSION
            or metadata.get("coordinate_system") != "camera_reference_xy_divided_by_source_long_edge"):
        reason = reason or "stable_reference_coordinate_contract_unverified"
    dimensions = sequence.frame_dimensions_px
    transforms = sequence.frame_transforms_to_reference
    applied = set(_mapping(metadata.get("camera_compensation")).get("applied_source_frames", []))
    if dimensions is None or transforms is None:
        reason = reason or "stable_reference_pixel_geometry_unavailable"
    elif indexes.size:
        sizes = np.asarray(dimensions[indexes], dtype=float)
        if (not np.isfinite(sizes).all() or np.any(sizes <= 0) or not np.all(sizes == sizes[0])):
            reason = reason or "frame_dimensions_missing_or_changed"
        if any(int(sequence.source_frames[i]) not in applied
               or validated_camera_transform({"compensation_valid": True,
                                               "matrix_to_reference": transforms[i]}) is None for i in indexes):
            reason = reason or "stable_reference_transform_unverified"
    times = None
    exact_times = None
    if sequence.model_estimate_bindings is not None:
        if indexes.size and all(sequence.model_estimate_bindings[i] is not None for i in indexes):
            bindings = [sequence.model_estimate_bindings[i] for i in indexes]
            exact_times = [Fraction(b["pts"] * b["time_base_num"] * 1000, b["time_base_den"]) for b in bindings]
            evidence.update(time_basis="bound_source_pts", source_track_id=bindings[0]["person_track_id"],
                            selection_epoch=bindings[0]["selection_epoch"])
    elif indexes.size:
        bindings = [validated_source_time_binding(sequence.source_time_bindings.get(int(sequence.source_frames[i])))
                    for i in indexes]
        if all(binding is not None for binding in bindings):
            keys = ("video_sha256", "primary_player_id", "person_track_id", "selection_epoch",
                    "camera_reference_epoch", "time_base_num", "time_base_den")
            tracks = _mapping(event.get("track_diagnostics")).get("source_track_ids")
            matches = all(
                binding["frame_index"] == int(sequence.source_frames[i])
                and binding["video_timestamp_ms"] == sequence.timestamp_ms[i]
                and binding["primary_player_id"] == sequence.primary_player_id == event.get("person_track_id")
                and tracks == [binding["person_track_id"]]
                and sequence.camera_reference_epochs is not None
                and binding["camera_reference_epoch"] == sequence.camera_reference_epochs[i]
                and all(binding[key] == bindings[0][key] for key in keys)
                for i, binding in zip(indexes, bindings))
            matches = matches and all(b["processed_index"] == a["processed_index"] + 1
                                      for a, b in zip(bindings, bindings[1:]))
            if matches:
                exact_times = [Fraction(b["pts"] * b["time_base_num"] * 1000, b["time_base_den"]) for b in bindings]
                evidence.update(time_basis="bound_source_pts", source_track_id=bindings[0]["person_track_id"],
                                selection_epoch=bindings[0]["selection_epoch"])
            else:
                reason = reason or "source_decoder_binding_discontinuity"
    # Rounded playback milliseconds never decide the exact duration boundary.
    if exact_times is None:
        reason = reason or "decoder_pts_provenance_unavailable_for_reference_translation"
    else:
        times = np.asarray([float(value) for value in exact_times])
        duration_exact = exact_times[-1] - exact_times[0]
        duration = float(duration_exact)
        evidence.update(source_duration_ms=duration, source_timestamps_ms=times.tolist())
        gaps = [b - a for a, b in zip(exact_times, exact_times[1:])]
        video_gaps = np.diff(sequence.timestamp_ms[indexes])
        if (not np.isfinite(times).all() or any(gap <= 0 or gap > MAX_GAP_MS for gap in gaps)
                or any(abs(gap - Fraction(str(float(video_gap)))) > Fraction(101, 100)
                       for gap, video_gap in zip(gaps, video_gaps))):
            reason = reason or "source_time_or_frame_gap"
        elif duration_exact < MIN_DURATION_MS:
            reason = reason or "reference_translation_window_below_80_ms"
    valid = np.logical_and.reduce([context.masks[name][indexes] for name in required])
    if sequence.model_estimate_bindings is None:
        # Same 0.35 confidence floor as preparation_translation._point; SAM
        # uses bound finite model geometry and never fabricates confidence.
        valid &= np.logical_and.reduce([np.asarray(sequence.confidence.get(name, np.full(len(sequence.timestamp_ms), np.nan)))[indexes] >= .35 for name in required])
    if not valid.all():
        reason = reason or "reference_translation_required_joints_not_continuous"
    displacement, normalized = None, None
    if not reason:
        long_edge = float(np.max(dimensions[indexes[0]]))
        hips = context.hip[indexes] * long_edge
        shoulders = (context.points["left_shoulder"][indexes] + context.points["right_shoulder"][indexes]) * long_edge / 2
        scales = np.linalg.norm(shoulders - hips, axis=1)
        if not np.isfinite(scales).all() or np.any(scales < 12):
            reason = "reference_translation_torso_scale_below_12_px"
        else:
            scale = float(np.median(scales))
            displacement = float(hips[-1, 1] - hips[0, 1])
            normalized = displacement / scale
            evidence.update(fixed_torso_scale_px=scale, hip_reference_y_start_px=float(hips[0, 1]),
                            hip_reference_y_end_px=float(hips[-1, 1]),
                            camera_reference_epoch=sequence.camera_reference_epochs[indexes[0]],
                            frame_dimensions_px=dimensions[indexes[0]].tolist())
    return [_row(context, event=event, indicator="FS01-M02", name=name, label=label, unit=unit,
                 start=start, end=end, indexes=indexes, required=required, reason=reason,
                 value=value, evidence=evidence, minimum_samples=MIN_SAMPLES, definition=definition)
            for name, label, unit, value, definition in (
                ("preload_hip_reference_dy_px", "预加载候选髋纵向位移（稳定参考像素）", "px", displacement,
                 "同一预加载下降转折候选窗内，稳定相机参考髋中点末帧纵坐标减首帧，画面向下为正；不减踝位置，不确认真实重心或完整预加载。"),
                ("preload_hip_reference_dy_torso", "预加载候选髋纵向位移（固定躯干尺度）", "torso", normalized,
                 "稳定参考髋纵向像素位移除以同窗肩中点到髋中点距离的固定中位数；画面向下为正，与旧相对踝body量独立，不换算等级。"))]


def _preload(context: SourceAlignedContext, event: Mapping, reason: str | None) -> list[dict]:
    indicator = "FS01-M02"
    start, end = event["start_ms"], event["end_ms"]
    phase = locate_source_preload(context, event)
    if phase["status"] == "observed_candidate":
        start, end = phase["source_preload_start_ms"], phase["source_preload_ms"]
    else:
        reason = reason or phase["repair_reason"]
    indexes = context.indexes(start, end)
    reference_reason = reason
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
    rows = [_row(context, event=event, indicator=indicator, name=name, label=label, unit=unit, definition=definition,
                 start=start, end=end, required=required, reason=reason, value=values.get(name), indexes=indexes,
                 evidence={"phase_key": "preload_ms", "phase_semantics": "unvalidated_pose_candidate_not_ground_truth",
                           "source_phase": phase, "body_scale": scale, **({"speed_continuity_observations": speed} if name == definitions[-1][0] else {})})
            for name, label, unit, required, definition in definitions]
    return rows + _preload_reference_translation(context, event, phase=phase, start=start, end=end,
                                                indexes=indexes, reason=reference_reason)


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
    return [_row(context, event=event, indicator="FS01-M04", name="post_slowdown_ankle_width_to_hip_width_ratio",
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
        if context.subject_window_reason(indexes, event):
            identity_error = "between_event_subject_continuity_unverified"
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
    row = _row(context, event=event, indicator=indicator, name=name, label="落地候选到后续启动间隔" if is_split else "稳定候选到后续事件间隔",
               unit="ms", start=start, end=end, required=JOINTS, reason=link_reason,
               value=end - start if successor and anchor is not None else None, indexes=indexes,
               minimum_samples=minimum_samples,
               definition="next observed same-video same-player allowed event start minus current observed phase candidate; no successor means missing evidence, not zero or E",
               evidence={"source_event_id": event["event_id"], "successor_event_id": association["successor_event_id"],
                         "anchor_phase_key": phase_key, "candidates_remain_unvalidated": True})
    speed_end = min(end, event["end_ms"])
    speed_indexes = context.indexes(start, speed_end)
    speed = _speed_summary(context, speed_indexes, _scale(context, speed_indexes))
    stability = _row(context, event=event, indicator=indicator,
        name="post_landing_body_speed_std_body_s" if is_split else "stable_control_body_speed_std_body_s",
        label="落地候选后速度波动" if is_split else "稳定候选后速度波动", unit="body/s", start=start, end=speed_end,
        required=JOINTS[:4], reason=reason or phase_error, value=speed["std"], indexes=speed_indexes,
        definition="population standard deviation of adjacent observed body-center interval speeds inside the event after the phase candidate; no grade or stable/unstable cutoff",
        evidence={"speed_observations": speed})
    association["link_measurement_status"] = row["status"]
    return [row, stability], association


def _head_stability(context: SourceAlignedContext, event: Mapping, reason: str | None) -> list[dict]:
    """Early candidate segment only; head motion does not verify gaze or timing."""
    stop,phase_reason = _phase(event,"takeoff_proxy_ms")
    start,end=event["start_ms"],stop if stop is not None else event["end_ms"]
    indexes=context.indexes(start,end)
    reason=reason or phase_reason or context.window_reason(indexes,start,end,minimum_samples=3)
    valid=np.logical_and.reduce([context.masks[name][indexes] for name in HEAD])
    centers=np.mean(np.stack([context.points[name][indexes] for name in HEAD]),axis=0)
    dt=np.diff(context.sequence.timestamp_ms[indexes])/1000
    pairs=valid[:-1]&valid[1:]&(dt>0)&(dt<=MAX_GAP_MS/1000)
    speed=None
    speed_reason=reason
    if context.sequence.coordinate_metadata.get("contract_version")!="isotropic-frame-long-edge-v1.1.0":
        speed_reason=speed_reason or "isotropic_coordinate_contract_unverified"
    if not speed_reason and int(pairs.sum())>=2:
        speed=float(np.sum(np.linalg.norm(np.diff(centers,axis=0)[pairs],axis=1))/np.sum(dt[pairs]))
    ears=context.points["right_ear"][indexes]-context.points["left_ear"][indexes]
    axes_valid=valid&(np.linalg.norm(ears,axis=1)>1e-6)
    angles=np.arctan2(ears[axes_valid,1],ears[axes_valid,0])
    deviation=None
    if not reason and angles.size>=3:
        resultant=float(abs(np.mean(np.exp(2j*angles))))
        if resultant>1e-12:
            deviation=float(np.degrees(np.sqrt(max(0.,-2*np.log(min(resultant,1.)))))/2)
    common={"window_scope":"FS01_candidate_start_to_takeoff_proxy_not_confirmed_pre_split_stage",
            "takeoff_proxy_ms":stop,"gaze_evaluated":False,"incoming_ball_alignment_evaluated":False,
            "technical_stability_evaluated":False}
    return [
        _row(context,event=event,indicator="FS01-M01",name="head_center_mean_speed_frame_long_edge_s",
             label="候选前段头部中心速度",unit="frame_long_edge/s",start=start,end=end,required=HEAD,
             definition="鼻和双耳二维质心在稳定画面参照中的相邻有效帧累计路程/这些相邻帧真实时长之和，按画面长边归一化；不跨缺测求速度，不判稳定与否。",
             reason=speed_reason,value=speed,indexes=indexes,minimum_samples=3,
             evidence={**common,"formula":"sum(norm(C[i+1]-C[i]))/sum(delta_t[i]); C=(nose+left_ear+right_ear)/3; adjacent_valid_pairs_only",
                 "used_adjacent_source_frame_pairs":[[int(context.sequence.source_frames[indexes[i]]),int(context.sequence.source_frames[indexes[i+1]])] for i in np.flatnonzero(pairs)],
                 "observed_duration_ms":float(np.sum(dt[pairs])*1000)}),
        _row(context,event=event,indicator="FS01-M01",name="head_axis_circular_std_deg",label="候选前段头部轴角波动",
             unit="deg",start=start,end=end,required=HEAD,definition="双耳无方向投影轴的180°周期圆标准差：sqrt(-2 ln|mean(exp(2iθ))|)/2；只描述已观测轴角分布，不确认真实头朝向或注视。",
             reason=reason,value=deviation,indexes=indexes,minimum_samples=3,
             evidence={**common,"formula":"degrees(sqrt(-2*ln(abs(mean(exp(2j*theta))))))/2",
                       "used_source_frames":[int(f) for f in context.sequence.source_frames[indexes[axes_valid]]],
                       "angle_period_deg":180}),
    ]


def _launch_direction(context: SourceAlignedContext, event: Mapping, reason: str | None) -> list[dict]:
    """Only the P0258 direction subclause; no ball/target correctness inference."""
    from rallymate_features.fs01_fs02_features import summarize_fs01_fs02_event_feature
    indexes = context.indexes(event["start_ms"], event["end_ms"])
    scale = _scale(context, indexes)
    value, evidence = None, {}
    reason = reason or context.subject_window_reason(indexes, event)
    reason = reason or (None if scale is not None else "body_scale_unavailable")
    if reason is None:
        measured = summarize_fs01_fs02_event_feature("launch_direction_deg", context.sequence, indexes, scale)
        value = measured.value
        evidence = {"formula": "atan2(-v_y,v_x), v=smoothed_hip_edge_displacement+median_smoothed_hip_velocity*event_duration",
                    "feature_version": "fs01-fs02-pose-proxies-v0.7.0",
                    "coordinate_convention": "0_deg_image_right_90_deg_image_up",
                    "endpoint_source_frames": [int(context.sequence.source_frames[i]) for i in measured.evidence_indexes],
                    "computation_diagnostics": measured.diagnostics,
                    "scope": "FS02_candidate_event_direction_only",
                    "incoming_ball_direction_available": False, "direction_correctness_evaluated": False,
                    "source_clause_complete": False}
        reason = None if value is not None else measured.reason
    return [_row(context, event=event, indicator="FS02-M01", name="launch_direction_deg", label="启动方向（图像代理）", unit="deg",
        definition="FS02候选完整窗口内平滑髋中心端点位移与中位速度乘时长的向量和方向，右为0°、上为90°；原文允许无球轨迹时仅输出启动方向，此操作定义不等于原文完整来球方向判断。",
        start=event["start_ms"], end=event["end_ms"], required=HEAD + HIPS, reason=reason, value=value,
        indexes=indexes, evidence=evidence)]


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
    if event.get("event_code") not in {"FS01", "FS02", "FS09"}:
        return {**result, "status": "not_applicable"}
    indexes = context.indexes(start, end)
    result.update(model_geometry_evidence(sequence, indexes, JOINTS + HEAD))
    reason = _identity_reason(context, event) or context.window_reason(indexes, start, end)
    for indicator in {"FS01": ("FS01-M02", "FS01-M04", "FS01-M05", "FS01-M01"), "FS02": ("FS02-M01",), "FS09": ("FS09-M05",)}[event["event_code"]]:
        core_coverage = context.coverage(indexes, CORE_GROUPS[indicator], SOURCE_REQUIRED_JOINTS[indicator])
        indicator_reason = reason or (None if core_coverage["passes"] else "source_required_joint_coverage_below_70_percent")
        association = None
        if indicator == "FS01-M02":
            rows = _preload(context, event, indicator_reason)
        elif indicator == "FS01-M04":
            rows = _post_slowdown_ratio(context, event, indicator_reason)
        elif indicator == "FS02-M01":
            rows = _launch_direction(context, event, indicator_reason)
        elif indicator == "FS01-M01":
            rows = _head_stability(context, event, indicator_reason)
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
        if indicator == "FS02-M01":
            unresolved.extend(["incoming_ball_direction_not_bound", "direction_correctness_not_evaluated"])
            limitations.append("仅输出启动方向图像代理；未绑定活动球轨迹，不判断与来球方向是否一致，亦未确认垫步落地和真实第一步边界。")
        if indicator == "FS01-M01":
            unresolved.extend(["true_gaze_unobservable","opponent_contact_and_incoming_ball_not_bound","pre_split_stage_not_confirmed"])
            limitations.append("仅描述FS01候选起点至起跳代理的头部局部运动；未确认原文垫步前正式窗口，不判真正盯球、时机正确或头部稳定合格。")
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
