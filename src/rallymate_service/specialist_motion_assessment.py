"""Source-bound short-swing profiles for returns, net play and defense.

The caller owns native-pose and action-family verification. Local joint paths
do not require a visible court or background camera transform. Conversely,
short paths alone never establish a defensive/volley/overhead action family.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re

import numpy as np

from rallymate_scoring.tactical_analyzer import score_compact_control, contextual_technique_profile


VERSION = "specialist-motion-assessment-v1.0.0"
SPECIALIST_METRICS = {
    "movement": (),
    "preparation": (("specialist_compact_preparation", "紧凑引拍", "path_ratio_to_striking", "接发、网前及受压处理保留短引拍，减少来不及挥出的多余路径"),),
    "coordination": (("specialist_direct_drive", "短挥拍衔接", "path_efficiency", "在有限挥拍空间保持清晰连续的挥出方向，避免折返摆动"),),
    "striking": (),
    "follow_through": (
        ("specialist_short_follow_through", "短收拍控制", "path_ratio_to_striking", "紧凑动作后控制多余收拍幅度，为下一拍保留准备空间"),
        ("specialist_terminal_control", "动作末段控制", "terminal_to_peak_speed_ratio", "已完整观察恢复时，检查末段是否控制住运动幅度"),
    ),
}
SPECIALIST_RUBRIC = {
    "version": VERSION, "coordinate_space": "hip_centered_torso_normalized_body_basis",
    "threshold_origin": "engineering_initial_values_not_coach_calibration",
    "preparation_path_ratio": {"full_score_at_or_below": .65, "zero_score_at_or_above": 1.8},
    "follow_path_ratio": {"full_score_at_or_below": .85, "zero_score_at_or_above": 2.0},
    "drive_efficiency": {"zero_score_at_or_below": .4, "full_score_at_or_above": .9},
    "active_striking_path_minimum_torso": .02,
    "camera_observation_required_for_native_paths": False,
    "short_swing_alone_classifies_defense_or_volley": False,
}


def _number(value):
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_)) and math.isfinite(value)


def _binding(rows, source_context):
    if source_context.get("native_source_verified") is not True:
        return "native_source_binding_unverified"
    sha = source_context.get("source_video_sha256", source_context.get("video_sha256"))
    if not isinstance(sha, str) or re.fullmatch(r"[a-fA-F0-9]{64}", sha) is None:
        return "native_source_video_sha_missing"
    if not rows:
        return "native_motion_window_empty"
    first = rows[0]
    fields = ("track", "person", "subject_id", "reference")
    if any(field not in first for field in fields):
        return "native_identity_missing"
    if any(any(row.get(field) != first[field] for field in fields) for row in rows):
        return "native_identity_or_reference_changed"
    if any(type(row.get("frame")) is not int or not _number(row.get("time")) for row in rows):
        return "native_frame_or_clock_missing"
    times = np.asarray([row["time"] for row in rows], dtype=float)
    frames = np.asarray([row["frame"] for row in rows])
    if len(times) < 4 or np.any(np.diff(times) <= 0) or np.any(np.diff(times) > 160) or np.any(np.diff(frames) != 1):
        return "native_motion_time_gap"
    return None


def _path(wrist, first, last):
    values = wrist[first:last + 1]
    if len(values) < 2 or not np.isfinite(values).all():
        return None
    return float(np.linalg.norm(np.diff(values, axis=0), axis=1).sum())


def _bound_contact_context(context, rows, peak):
    """Validate optional detector/court contact-context provenance.

    The contact-context producer must carry the same source identities and
    actual frame. It must never bind a review from an unrelated clip/subject.
    Raw detector-only indications remain separately reported candidates.
    """
    evidence = context.get("ball_court_context")
    if not isinstance(evidence, Mapping) or evidence.get("source_binding_verified") is not True:
        return None
    sha = str(context.get("source_video_sha256", context.get("video_sha256", ""))).lower()
    if (str(evidence.get("video_sha256", "")).lower() != sha
            or evidence.get("person_track_id") != rows[peak]["track"]
            or evidence.get("source_frame") != rows[peak]["frame"]
            or evidence.get("selection_epoch") != rows[peak]["reference"][0]
            or evidence.get("camera_reference_epoch") != rows[peak]["reference"][1]
            or type(evidence.get("ball_track_id")) is not int
            or evidence.get("contact_confirmed") is not True):
        return None
    return dict(evidence)


def _specialist_family(family, context, bound_ball):
    if family == "return" and context.get("family_source_verified") is True:
        return "return", "source_bound_opponent_serve_and_incoming_ball"
    if family == "serve":
        return family, "existing_serve_family_preserved"
    if bound_ball is None:
        return family, "specialist_ball_court_context_missing"
    # A volley requires measured net location and bounce chronology. Absence of
    # a bounce detector event is never treated as verified absence of a bounce.
    net_distance = bound_ball.get("distance_to_net_court_length")
    delay = bound_ball.get("confirmed_bounce_to_contact_ms")
    net = (_number(net_distance) and 0 <= net_distance <= .25
           and bound_ball.get("court_calibration_verified") is True)
    bounce_context = (bound_ball.get("no_precontact_bounce_confirmed") is True
                      or _number(delay) and 0 < delay <= 180)
    if net and bounce_context:
        return "volley", "bound_court_net_position_and_contact_bounce_order"
    if bound_ball.get("defensive_pressure_observed") is True:
        latency = bound_ball.get("incoming_time_to_body_ms")
        proximity = bound_ball.get("contact_to_body_torso")
        if _number(latency) and 0 < latency <= 300 and _number(proximity) and 0 <= proximity <= .8:
            return "defense", "bound_incoming_ball_time_pressure_and_body_proximity"
    if (family == "overhead" and context.get("family_source_verified") is True
            and bound_ball.get("incoming_lob_observed") is True and bound_ball.get("serve_toss_excluded") is True):
        return "overhead", "bound_incoming_lob_and_existing_overhead_family"
    return family, "specialist_context_does_not_establish_family"


def assess_specialist_motion(rows: Sequence[Mapping], *, wrist, prep_end: int, peak: int,
                             side: str, family: str, source_context: Mapping,
                             source_observations=(), source_frames=()) -> dict:
    """Return additive metrics/replacements for a previously verified episode.

    ``prep_end`` is the exclusive preparation index, matching tennis_assessment.
    All measured paths use the same active wrist/body basis. Missing optional
    specialist context leaves the existing family and all generic scores intact.
    ``source_observations`` and ``source_frames`` reserve the streaming adapter
    interface; they are not silently interpreted as verified contact context.
    """
    result = {"version": VERSION, "family": family, "metrics": {}, "replace_metrics": [],
              "metric_definitions": SPECIALIST_METRICS, "status": "not_applicable",
              "context_profile": contextual_technique_profile({"family": family}), "evidence": {}}
    if side not in {"left", "right"}:
        return {**result, "status": "unavailable", "reason": "active_wrist_side_invalid"}
    reason = _binding(rows, source_context)
    if reason:
        return {**result, "status": "unavailable", "reason": reason}
    if type(prep_end) is not int or type(peak) is not int or not 2 <= prep_end <= peak < len(rows) - 1:
        return {**result, "status": "unavailable", "reason": "specialist_phase_boundaries_invalid"}
    try:
        wrist = np.asarray(wrist, dtype=float)
    except (ValueError, TypeError):
        wrist = np.empty((0, 3))
    if wrist.shape != (len(rows), 3):
        return {**result, "status": "unavailable", "reason": "active_wrist_shape_invalid"}
    bound_ball = _bound_contact_context(source_context, rows, peak)
    selected_family, basis = _specialist_family(family, source_context, bound_ball)
    profile = contextual_technique_profile({**(bound_ball or {}), "family": selected_family})
    result.update(family=selected_family, context_profile=profile,
                  evidence={"family_basis": basis, "active_wrist": side,
                            "source_video_sha256": source_context.get("source_video_sha256", source_context.get("video_sha256")),
                            "subject_id": rows[0]["subject_id"], "person_track_id": rows[0]["track"],
                            "source_selection_epoch": rows[0]["reference"][0], "camera_reference_epoch": rows[0]["reference"][1],
                            "start_ms": round(float(rows[0]["time"]), 3), "end_ms": round(float(rows[-1]["time"]), 3),
                            "source_frames": [row["frame"] for row in rows],
                            "native_source_verified": True,
                            "camera_reference_required_for_local_paths": False,
                            "path_scope": "observed_native_motion_phases_not_complete_recovery",
                            "coordinate_space": "hip_centered_torso_normalized_body_basis"})
    if selected_family not in {"return", "volley", "defense"}:
        return result
    if selected_family == family and source_context.get("family_source_verified") is not True:
        return {**result, "reason": "specialist_family_unverified"}
    if selected_family in {"volley", "defense"} and bound_ball is None:
        return {**result, "reason": "specialist_contact_context_unavailable"}
    if selected_family in {"volley", "defense"} and basis not in {
            "bound_court_net_position_and_contact_bounce_order",
            "bound_incoming_ball_time_pressure_and_body_proximity"}:
        return {**result, "reason": "specialist_contact_context_does_not_support_family"}
    preparation = _path(wrist, 0, prep_end - 1)
    striking = _path(wrist, prep_end - 1, peak)
    follow = _path(wrist, peak, len(rows) - 1)
    if source_context.get("preparation_start_clipped") is True:
        preparation = None
    if source_context.get("follow_through_end_clipped") is True:
        follow = None
    terminal = None
    if source_context.get("recovery_end_verified") is True and np.isfinite(wrist[peak:]).all():
        times = np.asarray([row["time"] for row in rows], dtype=float)
        speeds = np.linalg.norm(np.diff(wrist, axis=0), axis=1) / (np.diff(times) / 1000)
        if np.isfinite(speeds).all() and float(np.max(speeds)) > 1e-8:
            # Last 20% of elapsed recovery, not last 20% of VFR samples.
            start = times[peak] + .8 * (times[-1] - times[peak])
            overlap = np.clip(times[1:] - np.maximum(times[:-1], start), 0., None)
            if overlap.sum() > 0:
                terminal = float(np.sum(speeds * overlap) / overlap.sum() / np.max(speeds))
    compact = score_compact_control(family=selected_family, preparation_path_body=preparation,
                                    striking_path_body=striking, follow_through_path_body=follow,
                                    terminal_speed_ratio=terminal)
    rename = {"compact_preparation": "specialist_compact_preparation",
              "controlled_short_follow_through": "specialist_short_follow_through",
              "terminal_motion_control": "specialist_terminal_control"}
    metrics = {rename[item["name"]]: (item["value"], item["score"]) for item in compact["metrics"]}
    if striking is not None and striking > .02:
        efficiency = float(np.clip(np.linalg.norm(wrist[peak] - wrist[prep_end - 1]) / striking, 0, 1))
        metrics["specialist_direct_drive"] = (efficiency, float(np.clip((efficiency - .4) / .5, 0, 1) * 100))
    result["evidence"].update(preparation_path_torso=preparation, striking_path_torso=striking,
                               follow_through_path_torso=follow, terminal_to_peak_speed_ratio=terminal,
                               striking_net_displacement_torso=float(np.linalg.norm(wrist[peak] - wrist[prep_end - 1]))
                                   if striking is not None else None,
                               recovery_end_verified=source_context.get("recovery_end_verified") is True,
                               phase_frame_bounds={"preparation": [rows[0]["frame"], rows[prep_end - 1]["frame"]],
                                                   "striking": [rows[prep_end - 1]["frame"], rows[peak]["frame"]],
                                                   "follow_through": [rows[peak]["frame"], rows[-1]["frame"]]})
    if metrics:
        # Full-rotation rewards are not requirements of a compressed response.
        # Keep head, arm-space, lower-body readiness and phase continuity scores.
        replacements = []
        if "specialist_compact_preparation" in metrics:
            replacements.append("coil")
        if "specialist_direct_drive" in metrics:
            replacements.extend(("coil_release", "release_sequence", "pattern_projected_sequence"))
        if "specialist_short_follow_through" in metrics:
            replacements.append("follow_path")
        result.update(metrics=metrics, replace_metrics=replacements, status="scored")
    else:
        result.update(status="unavailable", reason="specialist_paths_unobserved_or_inactive")
    return result
