"""Continuous-window visual evidence for five source footwork cards.

Engineering thresholds below are provisional, not numbers from the Word cards.
The module produces condition observations, never a source A--E grade. Stable
image-plane geometry cannot establish gaze, forces, centre of mass or contact.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from fractions import Fraction
import math
from typing import Any

import numpy as np

from rallymate_events.associations import event_identity, finite_time
from rallymate_features.coordinates import COORDINATE_CONTRACT_VERSION, validated_camera_transform
from rallymate_features.schemas import PoseSequence, validated_source_time_binding, MODEL_MEASUREMENT_PROVENANCE
from rallymate_features.source_alignment import (
    JOINTS, HIPS, SourceAlignedContext, locate_source_preload,
    prepare_source_aligned_context,
)
from rallymate_scoring.quality_policy import evaluate_indicator_event_quality
from rallymate_scoring.joint_quality_scope import known_joint_names
from rallymate_scoring.card_scoring_policy import ADVISORY_QUALITY_FLAGS

VERSION = "temporal-card-evidence-v1.0.0"
SOURCE_BINDING_EVIDENCE_VERSION = "temporal-source-frame-bindings-v1.0.0"
MIN_DURATION_MS = 80
MAX_BRAKING_WINDOW_MS = 600
LOW_HIP_SPEED_TORSO_S = .6
LOW_ANKLE_SPEED_TORSO_S = .5

# (metric, operator, threshold, unit). Single source for runtime and catalogue.
CARD_CONDITIONS = {
    "FS01-M02": (
        ("hip_drop_torso", "gte", .03, "torso"),
        ("left_knee_flexion_change_deg", "gte", 4., "deg"),
        ("right_knee_flexion_change_deg", "gte", 4., "deg"),
        ("joint_descent_fraction", "gte", .7, "fraction"),
        ("speed_step_variation_ratio", "lte", 1.5, "ratio"),
    ),
    "FS01-M05": (
        ("hip_inside_support_fraction", "gte", .8, "fraction"),
        ("body_speed_std_torso_s", "lte", .3, "torso/s"),
        ("successor_interval_ms", "between", (0., 500.), "ms"),
    ),
    "FS09-M03": (
        ("hip_speed_drop_torso_s", "gte", .2, "torso/s"),
        ("hip_speed_drop_fraction", "gte", .25, "fraction"),
        ("left_knee_flexion_change_deg", "gte", 4., "deg"),
        ("right_knee_flexion_change_deg", "gte", 4., "deg"),
        ("hip_drop_torso", "gte", .03, "torso"),
        ("trunk_tilt_std_deg", "lte", 10., "deg"),
        ("joint_braking_fraction", "gte", .5, "fraction"),
    ),
    "FS09-M04": (
        ("hip_inside_support_fraction", "gte", .8, "fraction"),
        ("supported_low_motion_duration_ms", "gte", 120., "ms"),
        ("shoulder_hip_angular_speed_drop_deg_s", "gte", 5., "deg/s"),
    ),
    "FS09-M05": (
        ("body_speed_std_torso_s", "lte", .3, "torso/s"),
        ("supported_low_motion_duration_ms", "gte", 120., "ms"),
        ("successor_interval_ms", "between", (0., 500.), "ms"),
    ),
}
CARD_REQUIRED_JOINTS = {card: JOINTS for card in CARD_CONDITIONS}
CARD_REQUIRED_JOINTS["FS09-M04"] = JOINTS[:4] + JOINTS[6:]
LOCAL_QUALITY_VERSION = "temporal-card-local-quality-v2"
BOUNDARY_REVIEW_FLAGS = frozenset({"primary_pose_coverage_low", "pose_kinematic_coverage_low"})
JOINT_REVIEW_FLAGS = frozenset({"keypoint_jump_candidates_present", "left_right_swap_candidates_present"})


def _finite(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _identity_reason(context: SourceAlignedContext, event: Mapping) -> str | None:
    identity = event_identity(event)
    diagnostics = event.get("track_diagnostics", {})
    if (identity is None or identity[1] != context.sequence.primary_player_id
            or not isinstance(diagnostics, Mapping)):
        return "subject_identity_unverified"
    if (identity in context.events.conflicts or context.events.events.get(identity) != event):
        return "event_conflicting_or_not_in_snapshot"
    candidates = diagnostics.get("source_track_switch_candidate_count")
    confirmed = diagnostics.get("confirmed_id_switch_count")
    if (type(diagnostics.get("primary_identity_ambiguous")) is not bool
            or type(candidates) is not int or candidates < 0
            or confirmed is not None and (type(confirmed) is not int or confirmed != 0)):
        return "subject_continuity_unverified"
    tracks = diagnostics.get("source_track_ids")
    if not isinstance(tracks, list) or len(tracks) != 1 or type(tracks[0]) is not int or tracks[0] < 0:
        return "source_track_identity_unverified"
    if context.primary_timeline is None:
        return "frame_subject_timeline_required"
    return None


def _subject_window_reason(context: SourceAlignedContext, indexes: np.ndarray, event: Mapping) -> str | None:
    """Candidate ambiguity is advisory only with a closed raw identity chain."""
    tracks = event.get("track_diagnostics", {}).get("source_track_ids")
    if (context.primary_timeline is None or not isinstance(tracks, list) or len(tracks) != 1
            or type(tracks[0]) is not int or tracks[0] < 0):
        return "measurement_window_subject_continuity_unverified"
    for index in indexes:
        frame = int(context.sequence.source_frames[index])
        row = context.identity_rows.get(frame, {})
        if (row.get("selection_status") != "selected" or type(row.get("identity_ambiguous")) is not bool
                or type(row.get("primary_player_id")) is not int
                or row["primary_player_id"] != event.get("person_track_id")
                or type(row.get("source_track_id")) is not int or row["source_track_id"] != tracks[0]
                or not _finite(row.get("timestamp_ms"))
                or row["timestamp_ms"] != float(context.sequence.timestamp_ms[index])):
            return "measurement_window_subject_continuity_unverified"
        binding = context.sequence.source_time_bindings.get(frame, {})
        if not binding and context.sequence.model_estimate_bindings is not None:
            model_binding = context.sequence.model_estimate_bindings[index]
            if isinstance(model_binding, Mapping):
                binding = model_binding
        if any(key in row and (type(row[key]) is not int or row[key] != binding.get(key))
               for key in ("selection_epoch", "camera_reference_epoch")):
            return "measurement_window_subject_epoch_mismatch"
    # Callers separately require the complete decoder bindings through
    # _source_times; a selected numeric track never waives that check.
    return None


def _phase(event: Mapping, name: str) -> tuple[float | None, str | None]:
    phases = event.get("key_phases_ms", {})
    value = finite_time(phases.get(name)) if isinstance(phases, Mapping) else None
    if value is None or not event["start_ms"] <= value <= event["end_ms"]:
        return None, "phase_not_observed:" + name
    flags = event.get("quality_flags", [])
    if (not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags)):
        return None, "quality_flags_invalid"
    if any(flag.endswith(":" + name) and ("censored" in flag or "low_sample_peak" in flag) for flag in flags):
        return None, "phase_boundary_censored:" + name
    return value, None


def _source_times(sequence: PoseSequence, indexes: np.ndarray) -> list[Fraction] | None:
    bindings = []
    for i in indexes:
        binding = validated_source_time_binding(sequence.source_time_bindings.get(int(sequence.source_frames[i])))
        if binding is None and sequence.model_estimate_bindings is not None:
            model = sequence.model_estimate_bindings[i]
            if isinstance(model, Mapping):
                binding = validated_source_time_binding({
                    **{key: model.get(key) for key in ("frame_index", "processed_index", "video_sha256", "pts",
                        "time_base_num", "time_base_den", "person_track_id", "selection_epoch", "camera_reference_epoch")},
                    "source_timestamp_ms": model.get("timestamp_ms"),
                    "video_timestamp_ms": int(sequence.timestamp_ms[i]),
                    "timestamp_source": "decoder_pts", "primary_player_id": sequence.primary_player_id})
        bindings.append(binding)
    if not bindings or any(binding is None for binding in bindings):
        return None
    keys = ("video_sha256", "primary_player_id", "person_track_id", "selection_epoch",
            "camera_reference_epoch", "time_base_num", "time_base_den")
    if any(any(binding[key] != bindings[0][key] for key in keys)
           or binding["frame_index"] != int(sequence.source_frames[i])
           or binding["video_timestamp_ms"] != sequence.timestamp_ms[i]
           or sequence.camera_reference_epochs is not None and binding["camera_reference_epoch"] != sequence.camera_reference_epochs[i]
           or binding["primary_player_id"] != sequence.primary_player_id for i, binding in zip(indexes, bindings)):
        return None
    if any(b["processed_index"] != a["processed_index"] + 1 for a, b in zip(bindings, bindings[1:])):
        return None
    times = [Fraction(b["pts"] * b["time_base_num"] * 1000, b["time_base_den"]) for b in bindings]
    origins = [t - Fraction(str(float(sequence.timestamp_ms[i]))) for i, t in zip(indexes, times)]
    if max(origins) - min(origins) > Fraction(102, 100):
        return None
    if any(not 0 < b - a <= 160 for a, b in zip(times, times[1:])):
        return None
    return times


def _source_frame_evidence(context: SourceAlignedContext, indexes: np.ndarray) -> list[dict]:
    """Expose original clocks and selection rows; never authorize a missing row.

    Source time and exported video time remain separate. The model contract is
    retained verbatim beside the decoder binding, rather than inferred from a
    geometry mask or substituted for the raw selected-person observation.
    """
    sequence, rows = context.sequence, []
    for i in indexes:
        frame = int(sequence.source_frames[i])
        binding = validated_source_time_binding(sequence.source_time_bindings.get(frame))
        model = sequence.model_estimate_bindings[i] if sequence.model_estimate_bindings is not None else None
        if binding is None and isinstance(model, Mapping):
            binding = validated_source_time_binding({
                **{key: model.get(key) for key in ("frame_index", "processed_index", "video_sha256", "pts",
                    "time_base_num", "time_base_den", "person_track_id", "selection_epoch", "camera_reference_epoch")},
                "source_timestamp_ms": model.get("timestamp_ms"),
                "video_timestamp_ms": int(sequence.timestamp_ms[i]), "timestamp_source": "decoder_pts",
                "primary_player_id": sequence.primary_player_id})
        row = ({**binding, "status": "verified_decoder_binding"} if binding else
               {"frame_index": frame, "status": "unavailable"})
        row["primary_selection"] = deepcopy(context.identity_rows.get(frame))
        row["model_geometry_source"] = ({
            "source_binding": deepcopy(model),
            "pose_source": MODEL_MEASUREMENT_PROVENANCE["pose_source"],
            "observation_semantics": MODEL_MEASUREMENT_PROVENANCE["observation_semantics"],
            "metadata_source": "PoseSequence.model_estimate_bindings_contract",
            "confidence_available": False,
        } if isinstance(model, Mapping) else None)
        rows.append(row)
    return rows


def _window(context: SourceAlignedContext, event: Mapping, start: float, end: float,
            required: tuple[str, ...] = JOINTS) -> tuple[np.ndarray, dict, str | None]:
    indexes = context.indexes(start, end)
    coverage = context.coverage(indexes, (required,), required)
    evidence = {"requested_window_ms": {"start_ms": start, "end_ms": end},
                "source_frames": context.sequence.source_frames[indexes].astype(int).tolist(),
                "timestamps_ms": context.sequence.timestamp_ms[indexes].astype(float).tolist(),
                "coverage": coverage, "reference": "one_camera_epoch_fixed_median_torso",
                "interpolation_used": False}
    reason = (_identity_reason(context, event)
              or context.window_reason(indexes, start, end, minimum_samples=3)
              or _subject_window_reason(context, indexes, event))
    tracks = event.get("track_diagnostics", {}).get("source_track_ids", [])
    if context.sequence.model_estimate_bindings is not None:
        if any(not isinstance(binding := context.sequence.model_estimate_bindings[i], Mapping)
               or tracks != [binding.get("person_track_id")]
               or binding.get("frame_index") != int(context.sequence.source_frames[i]) for i in indexes):
            reason = reason or "model_geometry_subject_binding_mismatch"
    elif any(tracks != [context.sequence.source_time_bindings.get(int(context.sequence.source_frames[i]), {}).get("person_track_id")]
             for i in indexes):
        reason = reason or "source_clock_subject_binding_mismatch"
    if not coverage["passes"]:
        reason = reason or "required_joint_coverage_below_70_percent"
    if indexes.size and (context.sequence.timestamp_ms[indexes[0]] != start
                         or context.sequence.timestamp_ms[indexes[-1]] != end):
        reason = reason or "exact_window_endpoints_missing"
    exact_times = _source_times(context.sequence, indexes)
    if exact_times is None:
        reason = reason or "source_clock_binding_unverified"
    else:
        evidence.update(source_timestamps_ms=[float(t) for t in exact_times], time_basis="decoder_pts",
                        source_duration_ms=float(exact_times[-1] - exact_times[0]))
    if exact_times is not None and exact_times[-1] - exact_times[0] < MIN_DURATION_MS:
        reason = reason or "continuous_window_below_80_ms"
    sequence = context.sequence
    metadata = sequence.coordinate_metadata
    if (metadata.get("contract_version") != COORDINATE_CONTRACT_VERSION
            or metadata.get("coordinate_system") != "camera_reference_xy_divided_by_source_long_edge"):
        reason = reason or "stable_reference_coordinate_contract_unverified"
    transforms, dimensions = sequence.frame_transforms_to_reference, sequence.frame_dimensions_px
    compensation = metadata.get("camera_compensation")
    applied = compensation.get("applied_source_frames", []) if isinstance(compensation, Mapping) else []
    if not isinstance(applied, (list, tuple, set)):
        applied = []
    if transforms is None or dimensions is None:
        reason = reason or "stable_reference_transform_unavailable"
    elif indexes.size:
        sizes = dimensions[indexes]
        if (not np.isfinite(sizes).all() or np.any(sizes <= 0) or not np.all(sizes == sizes[0])):
            reason = reason or "source_dimensions_changed_or_invalid"
        if any(int(sequence.source_frames[i]) not in applied
               or validated_camera_transform({"compensation_valid": True, "matrix_to_reference": transforms[i]}) is None
               for i in indexes):
            reason = reason or "stable_reference_transform_unverified"
    if indexes.size and not all(context.masks[name][indexes].all() for name in required):
        # The 70% source gate is necessary but not sufficient for a continuous
        # trajectory. Interpolating this gap could invent the joint descent.
        reason = reason or "required_joint_gap_inside_continuous_window"
    return indexes, evidence, reason


def _longest_interval_duration(times: list[Fraction], active: np.ndarray) -> float:
    longest = current = Fraction(0)
    for before, after, keep in zip(times, times[1:], active):
        current = current + after - before if bool(keep) else Fraction(0)
        longest = max(longest, current)
    return float(longest)


def _measure(context: SourceAlignedContext, indexes: np.ndarray) -> tuple[dict, dict, str | None]:
    """Every derivative uses adjacent source frames within the same window."""
    points, hips = context.points, context.hip[indexes]
    shoulders = (points["left_shoulder"][indexes] + points["right_shoulder"][indexes]) / 2
    torso = np.linalg.norm(shoulders - hips, axis=1)
    if not np.isfinite(torso).all() or np.any(torso <= 1e-6):
        return {}, {}, "fixed_torso_scale_invalid"
    scale = float(np.median(torso))
    if context.sequence.frame_dimensions_px is not None:
        edge = float(np.max(context.sequence.frame_dimensions_px[indexes[0]]))
        if np.min(torso) * edge < 12:
            return {}, {}, "torso_below_12_source_pixels"
    exact_times = _source_times(context.sequence, indexes)
    if exact_times is None:
        return {}, {}, "source_clock_binding_unverified"
    times = np.asarray([float(t - exact_times[0]) for t in exact_times])
    dt = np.diff(times) / 1000
    left, right = points["left_ankle"][indexes], points["right_ankle"][indexes]
    lower, upper = np.minimum(left[:, 0], right[:, 0]), np.maximum(left[:, 0], right[:, 0])
    inside = (hips[:, 0] >= lower) & (hips[:, 0] <= upper)
    hip_speed = np.linalg.norm(np.diff(hips, axis=0), axis=1) / dt / scale
    centre = (hips + shoulders) / 2
    body_speed = np.linalg.norm(np.diff(centre, axis=0), axis=1) / dt / scale
    ankle_speeds = [np.linalg.norm(np.diff(p, axis=0), axis=1) / dt / scale for p in (left, right)]
    knee = {side: context.knees[side][indexes] for side in ("left", "right")}
    # Different conditions at different moments do not establish a joint load.
    simultaneous = ((np.diff(hips[:, 1]) > 0) & (np.diff(knee["left"]) > 0)
                    & (np.diff(knee["right"]) > 0))
    simultaneous_braking = simultaneous[1:] & (np.diff(hip_speed) < 0)
    low_motion = ((hip_speed <= LOW_HIP_SPEED_TORSO_S)
                  & (ankle_speeds[0] <= LOW_ANKLE_SPEED_TORSO_S)
                  & (ankle_speeds[1] <= LOW_ANKLE_SPEED_TORSO_S)
                  & inside[:-1] & inside[1:])
    tilt = np.degrees(np.unwrap(np.arctan2(shoulders[:, 0] - hips[:, 0], hips[:, 1] - shoulders[:, 1])))
    angular = []
    for names in (("left_shoulder", "right_shoulder"), HIPS):
        vector = points[names[1]][indexes] - points[names[0]][indexes]
        angle = np.degrees(np.unwrap(np.arctan2(vector[:, 1], vector[:, 0])))
        angular.append(np.abs(np.diff(angle) / dt))
    # No arbitrary event quartiles: compare first/last adjacent physical pair.
    angular_speed = np.maximum(angular[0], angular[1])
    first_speed, last_speed = float(hip_speed[0]), float(hip_speed[-1])
    speed_drop = first_speed - last_speed
    values = {
        "hip_drop_torso": float((hips[-1, 1] - hips[0, 1]) / scale),
        "left_knee_flexion_change_deg": float(knee["left"][-1] - knee["left"][0]),
        "right_knee_flexion_change_deg": float(knee["right"][-1] - knee["right"][0]),
        "joint_descent_fraction": float(np.sum(np.diff(times)[simultaneous]) / (times[-1] - times[0])),
        "speed_step_variation_ratio": float(np.max(np.abs(np.diff(body_speed))) / max(float(np.mean(body_speed)), .05)),
        "hip_inside_support_fraction": float(np.mean(inside)),
        "body_speed_std_torso_s": float(np.std(body_speed)),
        "hip_speed_drop_torso_s": speed_drop,
        "hip_speed_drop_fraction": speed_drop / first_speed if first_speed > 1e-9 else None,
        "trunk_tilt_std_deg": float(np.std(tilt)),
        "joint_braking_fraction": float(np.sum(np.diff(times)[1:][simultaneous_braking]) / np.sum(np.diff(times)[1:])),
        "supported_low_motion_duration_ms": _longest_interval_duration(exact_times, low_motion),
        "shoulder_hip_angular_speed_drop_deg_s": float(angular_speed[0] - angular_speed[-1]),
    }
    raw = {"fixed_torso_scale": scale, "hip_y_reference": hips[:, 1].tolist(),
           "left_knee_flexion_deg": knee["left"].tolist(), "right_knee_flexion_deg": knee["right"].tolist(),
           "hip_inside_ankle_interval": inside.tolist(), "hip_speed_torso_s": hip_speed.tolist(),
           "body_speed_torso_s": body_speed.tolist(), "left_ankle_speed_torso_s": ankle_speeds[0].tolist(),
           "right_ankle_speed_torso_s": ankle_speeds[1].tolist(),
           "simultaneous_descent_intervals": simultaneous.tolist(),
           "simultaneous_braking_intervals_after_first": simultaneous_braking.tolist(),
           "supported_low_motion_intervals": low_motion.tolist(),
           "shoulder_hip_max_angular_speed_deg_s": angular_speed.tolist(),
           "pair_source_frames": [[int(a), int(b)] for a, b in zip(context.sequence.source_frames[indexes[:-1]], context.sequence.source_frames[indexes[1:]])]}
    # Some cards need only the six support joints. Irrelevant missing knee
    # observations must remain JSON null and must not block that local card.
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return None if isinstance(value, float) and not math.isfinite(value) else value
    return clean(values), clean(raw), None


def _successor(context: SourceAlignedContext, event: Mapping, anchor: float, codes: tuple[str, ...]) -> tuple[dict, str | None]:
    association = context.events.next_event(event, after_ms=anchor, allowed_codes=codes)
    successor = association.pop("event")
    if successor is None:
        return association, association["reason"]
    start = float(successor["start_ms"])
    indexes = context.indexes(anchor, start)
    reason = (_identity_reason(context, successor)
              or context.window_reason(indexes, anchor, start, minimum_samples=1 if start == anchor else 2)
              or _subject_window_reason(context, indexes, event))
    if successor.get("track_diagnostics", {}).get("source_track_ids") != event.get("track_diagnostics", {}).get("source_track_ids"):
        reason = reason or "successor_source_track_changed"
    if not indexes.size or context.sequence.timestamp_ms[indexes[0]] != anchor or context.sequence.timestamp_ms[indexes[-1]] != start:
        reason = reason or "successor_endpoint_observation_missing"
    exact_times = _source_times(context.sequence, indexes)
    if exact_times is None:
        reason = reason or "successor_source_clock_unverified"
    association.update(successor_event_id=successor["event_id"], successor_event_code=successor["event_code"],
                       successor_start_ms=start, interval_ms=float(exact_times[-1] - exact_times[0]) if exact_times else None,
                       source_frames=context.sequence.source_frames[indexes].astype(int).tolist(),
                       source_frame_binding_version=SOURCE_BINDING_EVIDENCE_VERSION,
                       source_frame_bindings=_source_frame_evidence(context, indexes),
                       source_track_ids=deepcopy(event.get("track_diagnostics", {}).get("source_track_ids")),
                       successor_source_track_ids=deepcopy(successor.get("track_diagnostics", {}).get("source_track_ids")))
    return association, reason


def _preload_fallback(context: SourceAlignedContext, event: Mapping, original: Mapping) -> dict:
    """Recover a censored detector phase using complete, observed local turns.

    This does not infer takeoff. Search ends before the existing landing proxy
    when available, then choose the earliest supported dip, never the best grade.
    At least two observed changes on either side reject isolated pose spikes.
    """
    limit, _ = _phase(event, "landing_proxy_ms")
    limit = limit if limit is not None else float(event["end_ms"])
    indexes = context.indexes(float(event["start_ms"]), limit)
    y = context.hip[indexes, 1]
    changes = np.diff(y)
    candidates = []
    for peak in range(2, len(y) - 2):
        if not np.isfinite(changes[peak - 1:peak + 1]).all() or changes[peak - 1] < 0 or changes[peak] >= 0:
            continue
        left, right = peak, peak
        while left > 0 and np.isfinite(changes[left - 1]) and changes[left - 1] >= 0:
            left -= 1
        while right < len(changes) and np.isfinite(changes[right]) and changes[right] <= 0:
            right += 1
        if (left == 0 or not np.isfinite(y[left - 1])
                or np.count_nonzero(changes[left:peak] > 0) < 2
                or np.count_nonzero(changes[peak:right] < 0) < 2):
            continue
        a, b, confirmation = [float(context.sequence.timestamp_ms[indexes[i]]) for i in (left, peak, right)]
        if b - a < MIN_DURATION_MS:
            continue
        _, _, reason = _window(context, event, a, confirmation)
        if reason is not None:
            continue
        candidates.append({"start_ms": a, "end_ms": b, "confirmation_end_ms": confirmation,
                           "source_frames": context.sequence.source_frames[indexes[left:right + 1]].astype(int).tolist()})
    return {"trigger": original.get("repair_reason"), "strategy": "earliest_complete_local_dip_before_landing_or_event_end",
            "candidates": candidates, "selected": candidates[0] if candidates else None,
            "takeoff_inferred": False, "original_event_modified": False,
            "phase_semantics": "local_dip_visual_proxy_not_confirmed_pre_takeoff_load"}


def _local_dip_proof(context: SourceAlignedContext, event: Mapping, selected: Mapping) -> dict:
    """Retain the original parent and every source row for independent readers.

    The parent includes the observations on either side of the selected dip;
    declaring a subwindow or a pass bit never substitutes for those rows.
    """
    sequence = context.sequence
    indexes = context.indexes(event["start_ms"], event["end_ms"])
    landing, landing_reason = _phase(event, "landing_proxy_ms")
    search_end = landing if landing is not None else float(event["end_ms"])
    rows = []
    for i in indexes:
        frame = int(sequence.source_frames[i])
        binding = validated_source_time_binding(sequence.source_time_bindings.get(frame))
        if binding is None and sequence.model_estimate_bindings is not None:
            model = sequence.model_estimate_bindings[i]
            if isinstance(model, Mapping):
                binding = validated_source_time_binding({
                    **{key: model.get(key) for key in ("frame_index", "processed_index", "video_sha256", "pts",
                        "time_base_num", "time_base_den", "person_track_id", "selection_epoch", "camera_reference_epoch")},
                    "source_timestamp_ms": model.get("timestamp_ms"),
                    "video_timestamp_ms": int(sequence.timestamp_ms[i]), "timestamp_source": "decoder_pts",
                    "primary_player_id": sequence.primary_player_id})
        rows.append({"frame_index": frame, "timestamp_ms": float(sequence.timestamp_ms[i]),
            "source_binding": binding,
            "model_geometry_source": {
                "source_binding": deepcopy(sequence.model_estimate_bindings[i]),
                "pose_source": MODEL_MEASUREMENT_PROVENANCE["pose_source"],
                "observation_semantics": MODEL_MEASUREMENT_PROVENANCE["observation_semantics"],
                "metadata_source": "PoseSequence.model_estimate_bindings_contract",
                "confidence_available": False,
            } if sequence.model_estimate_bindings is not None and isinstance(sequence.model_estimate_bindings[i], Mapping) else None,
            "primary_selection": deepcopy(context.identity_rows.get(frame)),
            "points": {joint: context.points[joint][i].tolist() if np.isfinite(context.points[joint][i]).all() else None
                       for joint in JOINTS},
            "confidence": {joint: float(value) if _finite(value := float(sequence.confidence.get(joint, np.full(len(sequence.timestamp_ms), np.nan))[i])) else None
                           for joint in JOINTS},
            "model_estimate_mask": {joint: bool(sequence.model_estimate_masks.get(joint, np.zeros(len(sequence.timestamp_ms), dtype=bool))[i])
                                    for joint in JOINTS} if sequence.model_estimate_bindings is not None else None,
            "camera_reference_epoch": sequence.camera_reference_epochs[i] if sequence.camera_reference_epochs is not None else None,
            "camera_motion_status": sequence.camera_motion_status[i] if sequence.camera_motion_status is not None else None,
            "frame_dimensions_px": sequence.frame_dimensions_px[i].tolist() if sequence.frame_dimensions_px is not None else None,
            "frame_transform_to_reference": sequence.frame_transforms_to_reference[i].tolist() if sequence.frame_transforms_to_reference is not None else None})
    proof = {"version": "temporal-local-dip-proof-v1.0.0", "required_joint_names": list(JOINTS),
        "parent_event": {key: deepcopy(event.get(key)) for key in
                         ("video_id", "event_id", "event_code", "person_track_id", "start_ms", "end_ms", "key_phases_ms", "quality_flags")},
        "source_track_ids": deepcopy(event.get("track_diagnostics", {}).get("source_track_ids")),
        "coordinate_contract_version": sequence.coordinate_metadata.get("contract_version"),
        "coordinate_system": sequence.coordinate_metadata.get("coordinate_system"),
        "points_semantics": "already_camera_compensated_reference_xy_do_not_apply_transform_again",
        "camera_compensation": deepcopy(sequence.coordinate_metadata.get("camera_compensation")),
        "measurement_window_ms": {"start_ms": selected["start_ms"], "end_ms": selected["end_ms"]},
        "search_end_ms": search_end, "search_end_basis": "landing_proxy_ms" if landing is not None else "event_end",
        "landing_phase_reason": landing_reason,
        "confirmation_end_ms": selected["confirmation_end_ms"], "rows": rows,
        "expert_technical_grade_verified": False, "takeoff_inferred": False}
    def json_value(value):
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, dict):
            return {key: json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_value(item) for item in value]
        return None if isinstance(value, float) and not math.isfinite(value) else value
    return json_value(proof)


def _result(indicator: str, event: Mapping, evidence: dict, measurements: dict, reason: str | None) -> dict:
    flags = event.get("quality_flags", [])
    if isinstance(flags, list) and all(isinstance(flag, str) and flag for flag in flags):
        required = set(CARD_REQUIRED_JOINTS[indicator])
        diagnostics = event.get("track_diagnostics", {})
        scoped_flags, irrelevant = list(flags), []
        # Remove a pose diagnostic only when complete named-joint provenance
        # proves it irrelevant to this card. Missing/empty provenance fails closed.
        jumps = diagnostics.get("keypoint_jump_candidate_joints") if isinstance(diagnostics, Mapping) else None
        if ("keypoint_jump_candidates_present" in scoped_flags and isinstance(jumps, list) and jumps
                and known_joint_names(jumps) and required.isdisjoint(jumps)):
            scoped_flags.remove("keypoint_jump_candidates_present")
            irrelevant.append("keypoint_jump_candidates_present")
        pairs = diagnostics.get("left_right_swap_candidate_joint_pairs") if isinstance(diagnostics, Mapping) else None
        if ("left_right_swap_candidates_present" in scoped_flags and isinstance(pairs, list) and pairs
                and all(isinstance(pair, Mapping) and all(isinstance(pair.get(key), str) and pair[key]
                    for key in ("left_joint", "right_joint")) for pair in pairs)
                and known_joint_names([joint for pair in pairs for joint in (pair["left_joint"], pair["right_joint"])])
                and required.isdisjoint(joint for pair in pairs for joint in (pair["left_joint"], pair["right_joint"]))):
            scoped_flags.remove("left_right_swap_candidates_present")
            irrelevant.append("left_right_swap_candidates_present")
        local_flags = sorted(set(scoped_flags) & (BOUNDARY_REVIEW_FLAGS | JOINT_REVIEW_FLAGS))
        quality = evaluate_indicator_event_quality(indicator, scoped_flags)
        # Event-wide candidate warnings cannot override complete measurements.
        # Confirmed failures and required source/phase evidence remain hard.
        quality["hard_fail_flags"] = sorted(set(quality["hard_fail_flags"]) - ADVISORY_QUALITY_FLAGS)
        quality["scoring_block_flags"] = sorted(set(quality["scoring_block_flags"]) - ADVISORY_QUALITY_FLAGS)
        quality["hard_fail"] = bool(quality["hard_fail_flags"])
        quality["measurement_allowed"] = not quality["hard_fail"]
        quality["advisory_flags"] = sorted(set(quality["advisory_flags"]) | (set(scoped_flags) & ADVISORY_QUALITY_FLAGS))
        quality["status"] = "hard_fail" if quality["hard_fail"] else "advisory" if quality["advisory_flags"] else "pass"
        quality["scoring_allowed"] = not quality["hard_fail"] and not quality["scoring_block_flags"]
        quality.update(temporal_policy_version=LOCAL_QUALITY_VERSION, raw_input_quality_flags=list(flags),
                       temporal_local_review_flags=local_flags, irrelevant_joint_flags=irrelevant,
                       blocking_flags=sorted(set(quality["hard_fail_flags"] + quality["scoring_block_flags"])),
                       event_identity_diagnostics=deepcopy(diagnostics))
        evidence["event_quality"] = quality
        if quality["hard_fail"]:
            reason = reason or "event_quality_hard_fail"
        elif quality["scoring_block_flags"]:
            reason = reason or "event_required_source_evidence_invalid"
    else:
        reason = reason or "quality_flags_invalid"
    conditions = []
    for metric, operator, threshold, unit in CARD_CONDITIONS[indicator]:
        value = measurements.get(metric)
        passed = None
        if reason is None and _finite(value):
            passed = (threshold[0] <= value <= threshold[1] if operator == "between" else
                      value >= threshold if operator == "gte" else value <= threshold)
        conditions.append({"key": metric, "metric": metric, "value": value, "operator": operator,
                           "threshold": threshold, "unit": unit, "passed": passed,
                           "status": "measured" if passed is not None else "unavailable"})
    complete = reason is None and all(c["passed"] is not None for c in conditions)
    return {"indicator_id": indicator, "event_id": event.get("event_id"), "rule_version": VERSION,
            "status": "measured_visual_proxy" if complete else "needs_review",
            "reason": reason if reason else (None if complete else "required_condition_unavailable"),
            "evidence_mode": "visual_proxy", "original_grade": None, "source_grade_supported": False,
            "conditions": conditions, "measurements": measurements, "evidence": evidence,
            "advisory_reason_codes": list(evidence.get("advisory_reason_codes", [])),
            "threshold_basis": "engineering_provisional_not_source_or_calibrated",
            "grade_policy": "condition_completion_v1", "missing_policy": "unknown_never_E_or_zero",
            "limitations": ["2D hip midpoint is not physical centre of mass",
                            "low bilateral ankle motion with projected support is not verified ground contact",
                            "pose candidate boundaries are not ground truth",
                            "model geometry does not establish original-card joint visibility"]}


def compute_temporal_card_evidence(sequence: PoseSequence, event: Mapping[str, Any], *,
                                   events: Sequence[Mapping[str, Any]],
                                   primary_timeline: Sequence[Mapping[str, Any]] | None = None,
                                   context: SourceAlignedContext | None = None,
                                   allow_phase_fallback: bool = True) -> dict:
    """Evaluate same-window evidence; consumers may apply the proxy grade policy.

    Existing phase candidates are preserved. FS01 preload is independently
    repaired by the observed pre-takeoff turn; braking uses a bounded interval
    beginning at its observed speed-peak candidate, not whole-event quartiles.
    """
    context = context or prepare_source_aligned_context(sequence, events, primary_timeline=primary_timeline)
    if (context.sequence is not sequence or context.source_events is not events
            or primary_timeline is not None and context.primary_timeline is not primary_timeline):
        raise ValueError("context must belong to the supplied sequence and event snapshot")
    code = event.get("event_code")
    cards = ("FS01-M02", "FS01-M05") if code == "FS01" else (("FS09-M03", "FS09-M04", "FS09-M05") if code == "FS09" else ())
    output = {"version": VERSION, "event_id": event.get("event_id"), "cards": [], "original_grade_count": 0}
    if not cards:
        return output
    start, end = finite_time(event.get("start_ms")), finite_time(event.get("end_ms"))
    if start is None or end is None or start >= end:
        output["cards"] = [_result(card, event, {}, {}, "event_window_invalid") for card in cards]
        return output
    for card in cards:
        evidence, values, reason = {}, {}, None
        association, successor_reason = None, None
        if card == "FS01-M02":
            phase = locate_source_preload(context, event, identity_validator=_identity_reason,
                                          subject_validator=_subject_window_reason)
            a, b = phase.get("source_preload_start_ms"), phase.get("source_preload_ms")
            evidence["phase_selection"] = phase
            if a is None or b is None:
                reason = phase["repair_reason"]
                if allow_phase_fallback:
                    fallback = _preload_fallback(context, event, phase)
                    evidence["fallback"] = fallback
                    if fallback["selected"] is not None:
                        a, b = fallback["selected"]["start_ms"], fallback["selected"]["end_ms"]
                        reason = None
        elif card == "FS09-M03":
            a, reason = _phase(event, "peak_speed_ms")
            b = None
            if a is not None:
                limit = min(end, a + MAX_BRAKING_WINDOW_MS)
                phase_end = event.get("key_phases_ms", {}).get("restabilization_onset_ms")
                if _finite(phase_end) and a + MIN_DURATION_MS <= phase_end <= limit:
                    limit = float(phase_end)
                candidates = context.indexes(a, limit)
                b = float(sequence.timestamp_ms[candidates[-1]]) if candidates.size else None
            evidence["phase_selection"] = {"start": "peak_speed_ms_visual_braking_candidate", "end": "first_restabilization_candidate_or_600_ms_cap", "physical_braking_confirmed": False}
        else:
            key = ("landing_proxy_ms" if card == "FS01-M05" else
                   "deceleration_peak_ms" if card == "FS09-M04" else "stable_control_onset_ms")
            a, reason = _phase(event, key)
            b = end
            evidence["phase_selection"] = {"start": key, "end": "event_end", "physical_contact_confirmed": False}
        if card in {"FS01-M05", "FS09-M05"} and a is not None:
            association, successor_reason = _successor(context, event, a, ("FS02",) if card == "FS01-M05" else ("FS10", "FS02"))
            evidence["successor"] = association
            if successor_reason is None:
                b = min(b, association["successor_start_ms"])
        if a is not None and b is not None and reason is None:
            indexes, window, reason = _window(context, event, a, b, CARD_REQUIRED_JOINTS[card])
            evidence.update(window)
            evidence.update(source_frame_binding_version=SOURCE_BINDING_EVIDENCE_VERSION,
                source_frame_bindings=_source_frame_evidence(context, indexes),
                source_track_ids=deepcopy(event.get("track_diagnostics", {}).get("source_track_ids")))
            if reason is None:
                values, raw, reason = _measure(context, indexes)
                evidence["series"] = raw
        elif reason is None:
            reason = "phase_window_not_observed"
        if association is not None:
            if successor_reason is None:
                values["successor_interval_ms"] = association["interval_ms"]
            reason = reason or successor_reason
        if evidence.get("fallback", {}).get("selected") is not None:
            evidence["advisory_reason_codes"] = ["fallback_local_dip_action_type_unverified"]
            evidence["fallback"]["proof"] = _local_dip_proof(context, event, evidence["fallback"]["selected"])
        output["cards"].append(_result(card, event, evidence, values, reason))
    return output
