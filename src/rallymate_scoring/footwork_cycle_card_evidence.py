"""Source-bound continuous FS03/FS04 observations; never physical step truth.

The catalogue's existing thresholds are used without fitting to heldout video.
An ankle must move between two observed low-motion intervals to make a complete
visual step. Low motion is not ground contact. No interpolation or static ankle
ordering can certify a cycle, support force, target arrival or tennis quality.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any

import numpy as np

from rallymate_features.schemas import PoseSequence, validated_source_time_binding
from rallymate_features.source_alignment import JOINTS, SourceAlignedContext, prepare_source_aligned_context
from rallymate_scoring.card_rule_catalog import get_card_rule
from rallymate_scoring.joint_quality_scope import known_joint_names
from rallymate_scoring.temporal_card_evidence import (
    MIN_DURATION_MS, LOW_ANKLE_SPEED_TORSO_S, _finite, _source_times, _window,
)

VERSION = "footwork-cycle-card-evidence-v1.0.0"
CYCLE_POLICY_VERSION = "continuous-ankle-motion-cycle-v1"
ORDER_DEADBAND_TORSO = .08  # Existing catalogue's ankle-order noise deadband.
SUPPORTED_CODES = ("FS03", "FS04")
CORE_JOINTS = JOINTS[:4] + JOINTS[6:]
LOCAL_REVIEW_FLAGS = frozenset({"primary_track_coverage_low", "primary_pose_coverage_low",
    "pose_kinematic_coverage_low", "primary_identity_ambiguous", "source_track_switch_candidates_present",
    "keypoint_jump_candidates_present", "left_right_swap_candidates_present"})


def _runs(mask):
    """Inclusive pair-index runs; no gap closing or smoothing."""
    start = None
    for i, active in enumerate(mask):
        if active and start is None:
            start = i
        if start is not None and (not active or i == len(mask) - 1):
            yield start, i if active else i - 1
            start = None


def _motion_steps(points, speed, times, frames, scale):
    steps = []
    for start, end in _runs(speed > LOW_ANKLE_SPEED_TORSO_S):
        before, after = start, end + 1
        while before > 0 and speed[before - 1] <= LOW_ANKLE_SPEED_TORSO_S:
            before -= 1
        while after < len(speed) and speed[after] <= LOW_ANKLE_SPEED_TORSO_S:
            after += 1
        observed = {"start_source_frame": int(frames[start]), "end_source_frame": int(frames[end + 1]),
            "start_ms": float(times[start]), "end_ms": float(times[end + 1]),
            "motion_duration_ms": float(times[end + 1] - times[start]),
            "preceding_low_motion_duration_ms": float(times[start] - times[before]),
            "following_low_motion_duration_ms": float(times[after] - times[end + 1]),
            "preceding_source_frames": frames[before:start + 1].astype(int).tolist(),
            "following_source_frames": frames[end + 1:after + 1].astype(int).tolist()}
        delta = (np.median(points[end + 1:after + 1], axis=0)
                 - np.median(points[before:start + 1], axis=0)) / scale
        observed.update(delta_xy_torso=delta.tolist(), displacement_torso=float(np.linalg.norm(delta)))
        observed["complete_visual_motion_cycle"] = bool(
            min(observed[key] for key in ("motion_duration_ms", "preceding_low_motion_duration_ms",
                "following_low_motion_duration_ms")) >= MIN_DURATION_MS
            and abs(delta[0]) >= ORDER_DEADBAND_TORSO)
        steps.append(observed)
    return steps


def _order_exchanges(difference, times, frames):
    signs = np.where(difference > ORDER_DEADBAND_TORSO, 1,
                     np.where(difference < -ORDER_DEADBAND_TORSO, -1, 0))
    stable, short_runs = [], []
    for sign in (-1, 1):
        for start, end in _runs(signs == sign):
            if times[end] - times[start] >= MIN_DURATION_MS:
                stable.append((start, end, sign))
            else:
                short_runs.append((start, end, sign))
    stable.sort()
    crossings, unresolved = [], []
    if not stable or stable[0][0] != 0 or stable[-1][1] != len(signs) - 1:
        unresolved.append({"reason": "ankle_order_not_stable_at_both_window_boundaries"})
    for start, end, sign in short_runs:
        unresolved.append({"source_frames": frames[start:end + 1].astype(int).tolist(),
            "order": sign, "duration_ms": float(times[end] - times[start]),
            "reason": "short_order_run_cannot_be_discarded_as_no_exchange"})
    for before, after in zip(stable, stable[1:]):
        if before[2] == after[2]:
            continue
        # A sampled near-zero interior is necessary. Opposite endpoints alone
        # can be a tracking swap; straight endpoint chords are not evidence.
        middle = np.flatnonzero(signs[before[1] + 1:after[0]] == 0) + before[1] + 1
        if middle.size:
            crossings.append({"before_source_frame": int(frames[before[1]]),
                "near_zero_source_frames": frames[middle].astype(int).tolist(),
                "after_source_frame": int(frames[after[0]]), "before_order": before[2], "after_order": after[2]})
        else:
            unresolved.append({"before_source_frame": int(frames[before[1]]),
                "after_source_frame": int(frames[after[0]]), "reason": "sampled_near_zero_interior_missing"})
    explained = {frame for crossing in crossings for frame in crossing["near_zero_source_frames"]}
    for start, end in _runs(signs == 0):
        unknown = [int(frame) for frame in frames[start:end + 1] if int(frame) not in explained]
        if unknown:
            unresolved.append({"source_frames": unknown,
                "reason": "deadband_not_bracketed_by_resolved_opposite_orders"})
    return signs, stable, crossings, unresolved


def _binding_reason(context, event, indexes, binding):
    if not isinstance(binding, Mapping):
        return "source_step_window_binding_required"
    # No automatic FS03/FS04 cycle detector has been independently registered.
    # A caller's detector name/version must not grant itself that authority.
    if binding.get("method") != "manual_anchor":
        return "automatic_cycle_detector_not_registered"
    if (not isinstance(binding.get("version"), str) or not binding["version"].strip()
            or not isinstance(binding.get("evidence_reference"), str) or not binding["evidence_reference"].strip()):
        return "source_step_window_binding_required"
    if not len(indexes):
        return "source_step_window_empty"
    sequence = context.sequence
    if (binding.get("event_id") != event.get("event_id")
            or binding.get("video_id") != event.get("video_id")
            or type(binding.get("primary_player_id")) is not int
            or binding.get("primary_player_id") != sequence.primary_player_id
            or type(binding.get("start_source_frame")) is not int
            or type(binding.get("end_source_frame")) is not int
            or binding["start_source_frame"] != int(sequence.source_frames[indexes[0]])
            or binding["end_source_frame"] != int(sequence.source_frames[indexes[-1]])):
        return "step_window_binding_identity_or_frame_mismatch"
    hashes = set()
    for i in indexes:
        row = sequence.source_time_bindings.get(int(sequence.source_frames[i]), {})
        if not row and sequence.model_estimate_bindings is not None:
            row = sequence.model_estimate_bindings[i] or {}
        hashes.add(row.get("video_sha256"))
    if hashes != {binding.get("video_sha256")} or not isinstance(binding.get("video_sha256"), str):
        return "step_window_source_video_mismatch"
    return None


def _quality_evidence(event, required):
    flags = event.get("quality_flags", [])
    if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
        return {}, "quality_flags_invalid"
    relevant, ignored = set(flags), []
    diagnostics = event.get("track_diagnostics", {})
    for flag, field in (("keypoint_jump_candidates_present", "keypoint_jump_candidate_joints"),
                        ("left_right_swap_candidates_present", "left_right_swap_candidate_joint_pairs")):
        entries = diagnostics.get(field) if isinstance(diagnostics, Mapping) else None
        if not isinstance(entries, list) or not entries:
            continue
        if flag.startswith("left_right"):
            if any(not isinstance(pair, Mapping) or not all(isinstance(pair.get(k), str) and pair[k]
                for k in ("left_joint", "right_joint")) for pair in entries):
                continue
            entries = [pair[k] for pair in entries for k in ("left_joint", "right_joint")]
        if known_joint_names(entries) and set(required).isdisjoint(entries):
            if flag in relevant:
                relevant.remove(flag)
                ignored.append(flag)
    blocks = sorted(flag for flag in relevant if flag in LOCAL_REVIEW_FLAGS
        or "confirmed_id_switch" in flag or "id_switch_confirmed" in flag or "censored" in flag)
    return {"policy_version": CYCLE_POLICY_VERSION, "raw_input_quality_flags": list(flags),
        "review_flags": blocks, "irrelevant_joint_flags": ignored}, "cycle_required_evidence_requires_review" if blocks else None


def _frame_bindings(sequence, indexes):
    rows = []
    for i in indexes:
        frame = int(sequence.source_frames[i])
        binding = validated_source_time_binding(sequence.source_time_bindings.get(frame))
        if binding is None and sequence.model_estimate_bindings is not None:
            model = sequence.model_estimate_bindings[i]
            if isinstance(model, Mapping):
                binding = validated_source_time_binding({
                    **{k: model.get(k) for k in ("frame_index", "processed_index", "video_sha256", "pts", "time_base_num",
                        "time_base_den", "person_track_id", "selection_epoch", "camera_reference_epoch")},
                    "source_timestamp_ms": model.get("timestamp_ms"), "video_timestamp_ms": int(sequence.timestamp_ms[i]),
                    "timestamp_source": "decoder_pts", "primary_player_id": sequence.primary_player_id})
        rows.append({**binding, "status": "verified_decoder_binding"} if binding else {"frame_index": frame, "status": "unavailable"})
    return rows


def _measure(context, indexes):
    if len(indexes) < 7:
        return {}, {}, "continuous_endpoint_window_missing"
    sequence, points = context.sequence, context.points
    frames = sequence.source_frames[indexes]
    exact = _source_times(sequence, indexes)
    times = np.asarray([float(t) for t in exact])
    hips = context.hip[indexes]
    shoulders = (points["left_shoulder"][indexes] + points["right_shoulder"][indexes]) / 2
    torsos = np.linalg.norm(hips - shoulders, axis=1)
    scale = float(np.median(torsos))
    if not np.isfinite(torsos).all() or np.min(torsos) <= 0 or np.min(torsos) * np.max(sequence.frame_dimensions_px[indexes[0]]) < 12:
        return {}, {}, "fixed_torso_scale_invalid_or_below_12_pixels"
    dt = np.diff(times) / 1000
    count = max(3, math.ceil(len(indexes) * .2))
    speed_count = count  # Same endpoint convention as card_kinematics v1.
    endpoint = lambda p: np.median(p[-count:], axis=0) - np.median(p[:count], axis=0)
    hip_speed = np.linalg.norm(np.diff(hips, axis=0), axis=1) / dt / scale
    tilt = np.degrees(np.arctan2(np.abs(shoulders[:, 0] - hips[:, 0]), hips[:, 1] - shoulders[:, 1]))
    ankles = {side: points[side + "_ankle"][indexes] for side in ("left", "right")}
    speeds = {side: np.linalg.norm(np.diff(p, axis=0), axis=1) / dt / scale for side, p in ankles.items()}
    steps = {side: _motion_steps(p, speeds[side], times, frames, scale) for side, p in ankles.items()}
    complete = [(step["start_ms"], side, step) for side, candidates in steps.items()
                for step in candidates if step["complete_visual_motion_cycle"]]
    complete.sort(key=lambda value: (value[0], value[1]))
    left, right = ankles["left"], ankles["right"]
    width = np.linalg.norm(left - right, axis=1) / scale
    difference = (left[:, 0] - right[:, 0]) / scale
    signs, stable, exchanges, unresolved = _order_exchanges(difference, times, frames)
    linked = []
    for exchange in exchanges:
        matches = [step for _, side, step in complete
            if step["start_source_frame"] <= min(exchange["near_zero_source_frames"])
            and step["end_source_frame"] >= max(exchange["near_zero_source_frames"])]
        if matches:
            linked.append({**exchange, "complete_motion_source_windows": [
                [step["start_source_frame"], step["end_source_frame"]] for step in matches]})
        else:
            unresolved.append({**exchange, "reason": "exchange_not_inside_complete_motion_cycle"})
    lower, upper = np.minimum(left[:, 0], right[:, 0]), np.maximum(left[:, 0], right[:, 0])
    values = {"hip_displacement_torso": float(np.linalg.norm(endpoint(hips)) / scale),
        "trunk_tilt_deg.range": float(np.ptp(tilt)),
        "hip_speed_drop_torso_s": float(np.median(hip_speed[:speed_count]) - np.median(hip_speed[-speed_count:])),
        "hip_inside_ankle_support_fraction.mean": float(np.mean((hips[:, 0] >= lower) & (hips[:, 0] <= upper))),
        "ankle_max_displacement_torso": max(float(np.linalg.norm(endpoint(p)) / scale) for p in ankles.values()),
        "ankle_order_exchange_count": len(linked) if stable and not unresolved else None,
        "ankle_width_contraction_torso": float(np.max(width[1:-1]) - np.median(width[-count:])),
        "ankle_width_expansion_torso": float(np.max(width[1:-1]) - np.median(width[:count]))}
    for side in ("left", "right"):
        knee = context.knees[side][indexes]
        values[side + "_knee_flexion_deg.range"] = float(np.ptp(knee)) if np.isfinite(knee).all() else None
    # Both ankles must perform observed, ordered, positive travel. This does not
    # turn their low-speed intervals into support/contact events.
    follow = []
    direction = float(np.sign(endpoint(hips)[0]))
    for _, side, lead in complete:
        for _, other, trailing in complete:
            if (side == other or not lead["start_ms"] < trailing["start_ms"]
                    or trailing["start_ms"] > lead["end_ms"] + MIN_DURATION_MS):
                continue
            if not (lead["delta_xy_torso"][0] * direction > 0 and trailing["delta_xy_torso"][0] * direction > 0):
                continue
            i = int(np.searchsorted(times, lead["start_ms"]))
            if (ankles[side][i, 0] - ankles[other][i, 0]) * direction <= 0:
                continue
            j = int(np.searchsorted(times, trailing["end_ms"]))
            peak = i + int(np.argmax(width[i:j + 1]))
            # Require observed opening then closing in this exact ordered pair.
            # A pre-existing wide stance followed only by narrowing is not a
            # complete lead/follow cycle, even if its final distance is small.
            if not (i < peak < j and width[peak] - width[i] >= ORDER_DEADBAND_TORSO
                    and width[peak] - width[j] >= ORDER_DEADBAND_TORSO):
                continue
            follow.append({"lead_side": side, "follow_side": other, "lead_start_source_frame": lead["start_source_frame"],
                "follow_start_source_frame": trailing["start_source_frame"],
                "width_peak_source_frame": int(frames[peak]),
                "onset_delay_ms": trailing["start_ms"] - lead["start_ms"]})
    raw = {"fixed_torso_scale": scale, "endpoint_fraction": .2, "endpoint_frame_count": count,
        "speed_endpoint_pair_count": speed_count, "pair_source_frames": [[int(a), int(b)] for a, b in zip(frames, frames[1:])],
        "hip_xy_reference": hips.tolist(), "left_ankle_xy_reference": left.tolist(), "right_ankle_xy_reference": right.tolist(),
        "hip_speed_torso_s": hip_speed.tolist(), "ankle_width_torso": width.tolist(),
        "ankle_signed_horizontal_gap_torso": difference.tolist(), "ankle_order_sign": signs.tolist(),
        "ankle_speed_torso_s": {side: value.tolist() for side, value in speeds.items()},
        "motion_step_candidates": steps, "complete_visual_motion_cycle_count": len(complete),
        "ankle_order_exchanges": linked, "unresolved_order_exchanges": unresolved, "sequential_follow_pairs": follow,
        "semantics": "continuous_image_plane_motion_only_not_ground_contact_support_force_or_tennis_step_ground_truth"}
    return values, raw, None


def compute_footwork_cycle_card_evidence(sequence: PoseSequence, event: Mapping[str, Any], *,
        events: Sequence[Mapping[str, Any]], primary_timeline=None, context: SourceAlignedContext | None = None,
        cycle_binding: Mapping[str, Any] | None = None) -> dict:
    """Compute five source-linked cards for a bound FS03/FS04 candidate window.

    ``cycle_binding`` supplies provenance and exact outer source-frame anchors,
    not a complete/pass bit. The trajectory must independently contain complete
    visual motion cycles. One prepared context can be reused across all events.
    """
    code = event.get("event_code")
    if code not in SUPPORTED_CODES:
        raise ValueError("unsupported_continuous_footwork_event")
    if not all(_finite(event.get(k)) for k in ("start_ms", "end_ms")) or event["start_ms"] >= event["end_ms"]:
        raise ValueError("invalid_continuous_footwork_window")
    if context is not None and context.sequence is not sequence:
        raise ValueError("source_context_sequence_mismatch")
    context = context or prepare_source_aligned_context(sequence, events, primary_timeline=primary_timeline)
    cards, measurement_cache = [], {}
    for number in range(1, 6):
        indicator = f"{code}-M{number:02}"
        rule = get_card_rule(indicator)
        required = tuple(dict.fromkeys((*CORE_JOINTS, *rule["required_joint_names"])))
        indexes, evidence, reason = _window(context, event, event["start_ms"], event["end_ms"], required)
        reasons = [reason] if reason else []
        binding_reason = _binding_reason(context, event, indexes, cycle_binding)
        quality, quality_reason = _quality_evidence(event, required)
        evidence.update(step_window_binding=dict(cycle_binding or {}), event_quality=quality,
            cycle_policy_version=CYCLE_POLICY_VERSION, observations_are_ground_contact=False,
            anchor_semantics="manual_visual_candidate_window_not_expert_technique_or_contact_truth",
            source_frame_bindings=_frame_bindings(sequence, indexes),
            motion_times_basis="source_decoder_pts_absolute_ms")
        values, raw = {}, {}
        if reason is None:
            cache_key = tuple(sorted(required))
            if cache_key not in measurement_cache:
                measurement_cache[cache_key] = _measure(context, indexes)
            values, raw, reason = deepcopy(measurement_cache[cache_key])
            if reason:
                reasons.append(reason)
        evidence["series"] = raw
        reason = reason or binding_reason or quality_reason
        reasons.extend(value for value in (binding_reason, quality_reason) if value)
        if rule["evaluation"]["kind"] == "manual_review":
            reason = reason or "ball_direction_and_target_evidence_required"
            reasons.append("ball_direction_and_target_evidence_required")
        elif raw and not raw.get("complete_visual_motion_cycle_count"):
            reason = reason or "complete_visual_motion_cycle_not_observed"
            reasons.append("complete_visual_motion_cycle_not_observed")
        elif raw and indicator == "FS04-M04" and not raw.get("sequential_follow_pairs"):
            reason = reason or "sequential_lead_follow_cycle_not_observed"
            reasons.append("sequential_lead_follow_cycle_not_observed")
        if raw and indicator in {"FS03-M03", "FS04-M03", "FS04-M04"} and values.get("ankle_order_exchange_count") is None:
            reason = reason or "ankle_order_not_persistently_resolved"
            reasons.append("ankle_order_not_persistently_resolved")
        conditions = []
        for condition in rule["evaluation"]["conditions"]:
            value, operator = values.get(condition["metric"]), condition["operator"]
            threshold = condition.get("bounds", condition.get("value"))
            passed = None
            if reason is None and _finite(value):
                passed = (threshold[0] <= value <= threshold[1] if operator == "between" else
                          value >= threshold if operator == "gte" else value <= threshold if operator == "lte" else value == threshold)
            conditions.append({"key": condition["key"], "metric": condition["metric"], "value": value,
                "operator": operator, "threshold": threshold, "unit": condition["unit"], "passed": passed})
        complete = reason is None and bool(conditions) and all(c["passed"] is not None for c in conditions)
        if reason is None and not complete:
            reason = "condition_measurement_unavailable"
            reasons.append(reason)
        cards.append({"indicator_id": indicator, "event_id": event.get("event_id"), "rule_version": rule["rule_version"],
            "extractor_version": VERSION, "status": "measured_visual_proxy" if complete else "needs_review",
            "reason": reason, "reason_codes": list(dict.fromkeys(reasons)),
            "measurements": values, "conditions": conditions, "evidence": evidence,
            "original_grade": None, "proxy_grade": None, "score": None, "source_grade_supported": False,
            "limitation": rule["evaluation"]["limitation"]})
    return {"version": VERSION, "event_id": event.get("event_id"), "event_code": code,
        "cards": cards, "source_grade_supported": False}
