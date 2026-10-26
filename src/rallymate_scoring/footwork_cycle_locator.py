"""Locate source-bound ankle motion windows without assigning a tennis family.

Every motion run is retained, including short or censored runs. Existing cycle
thresholds are reused; no event label, saved score, or manual complete bit can
authorize a window. This locator does not score cards or establish contact.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from fractions import Fraction

import numpy as np

from rallymate_features.coordinates import COORDINATE_CONTRACT_VERSION, validated_camera_transform
from rallymate_features.schemas import PoseSequence, model_bindings_continuous
from rallymate_features.source_alignment import prepare_source_aligned_context
from rallymate_scoring.footwork_cycle_card_evidence import (
    CORE_JOINTS, CYCLE_POLICY_VERSION, ORDER_DEADBAND_TORSO, _measure, _quality_evidence,
)
from rallymate_scoring.temporal_card_evidence import (
    MIN_DURATION_MS, LOW_ANKLE_SPEED_TORSO_S, _source_frame_evidence, _source_times,
)

VERSION = "source-footwork-cycle-locator-v1.0.0"
SEMANTICS = "automatic_image_plane_motion_candidate_not_tennis_family_contact_or_grade"


def _exact(row):
    return Fraction(row["pts"] * row["time_base_num"] * 1000, row["time_base_den"])


def _frame_reason(context, i, row, primary_counts):
    seq = context.sequence
    frame = int(seq.source_frames[i])
    if row.get("status") != "verified_decoder_binding":
        return "source_clock_binding_unverified"
    if row["frame_index"] != frame or row["video_timestamp_ms"] != seq.timestamp_ms[i]:
        return "source_frame_clock_mismatch"
    selected = row.get("primary_selection")
    if (primary_counts[frame] != 1 or not isinstance(selected, Mapping)
            or selected.get("selection_status") != "selected"
            or type(selected.get("identity_ambiguous")) is not bool
            or selected.get("timestamp_ms") != seq.timestamp_ms[i]
            or any(type(selected.get(k)) is not int or selected[k] != row[target]
                   for k, target in (("source_track_id", "person_track_id"),
                                     ("primary_player_id", "primary_player_id")))
            or row["primary_player_id"] != seq.primary_player_id):
        return "selected_subject_binding_unverified"
    for key in ("processed_index", "selection_epoch", "camera_reference_epoch", "pts", "time_base_num", "time_base_den"):
        if key in selected and (type(selected[key]) is not int or selected[key] != row[key]):
            return "selected_subject_epoch_or_processed_mismatch"
    if "video_sha256" in selected and (not isinstance(selected["video_sha256"], str)
            or selected["video_sha256"].lower() != row["video_sha256"]):
        return "selected_source_video_mismatch"
    if "source_timestamp_ms" in selected and (type(selected["source_timestamp_ms"]) not in (int, float)
            or selected["source_timestamp_ms"] != row["source_timestamp_ms"]):
        return "selected_source_timestamp_mismatch"
    if seq.model_estimate_bindings is not None:
        model = seq.model_estimate_bindings[i]
        if not isinstance(model, Mapping):
            return "model_geometry_source_binding_missing"
        if any(type(model.get(k)) is not int or model[k] != row[k] for k in ("frame_index", "processed_index",
                "person_track_id", "selection_epoch", "camera_reference_epoch", "pts", "time_base_num", "time_base_den")):
            return "model_geometry_source_binding_mismatch"
        if type(model.get("timestamp_ms")) not in (int, float) or model["timestamp_ms"] != row["source_timestamp_ms"]:
            return "model_geometry_source_timestamp_mismatch"
        if str(model.get("video_sha256", "")).lower() != row["video_sha256"]:
            return "model_geometry_video_mismatch"
    metadata = seq.coordinate_metadata
    if (metadata.get("contract_version") != COORDINATE_CONTRACT_VERSION
            or metadata.get("coordinate_system") != "camera_reference_xy_divided_by_source_long_edge"):
        return "stable_reference_coordinate_contract_unverified"
    if (seq.camera_motion_status is None or seq.camera_reference_epochs is None
            or seq.camera_motion_status[i] not in {"reference", "stationary", "compensated"}
            or seq.camera_reference_epochs[i] != row["camera_reference_epoch"]):
        return "camera_reference_unavailable"
    if seq.frame_dimensions_px is None or seq.frame_transforms_to_reference is None:
        return "stable_reference_transform_unavailable"
    size = seq.frame_dimensions_px[i]
    applied = metadata.get("camera_compensation", {}).get("applied_source_frames", [])
    if (not np.isfinite(size).all() or np.any(size <= 0) or frame not in applied
            or validated_camera_transform({"compensation_valid": True,
                "matrix_to_reference": seq.frame_transforms_to_reference[i]}) is None):
        return "stable_reference_transform_unverified"
    if not all(context.masks[name][i] for name in CORE_JOINTS):
        return "required_joint_missing"
    torso = np.linalg.norm((context.points["left_shoulder"][i] + context.points["right_shoulder"][i]) / 2 - context.hip[i])
    if not np.isfinite(torso) or torso <= 0 or torso * np.max(size) < 12:
        return "torso_below_12_source_pixels"
    return None


def _pair_reason(context, before, after, rows):
    a, b = rows[before], rows[after]
    if b["frame_index"] != a["frame_index"] + 1 or b["processed_index"] != a["processed_index"] + 1:
        return "source_frame_or_processed_gap"
    for key in ("video_sha256", "primary_player_id", "person_track_id", "selection_epoch",
                "camera_reference_epoch", "time_base_num", "time_base_den"):
        if a[key] != b[key]:
            return "source_continuity_changed:" + key
    if not 0 < _exact(b) - _exact(a) <= 160:
        return "source_pts_gap"
    offset_a, offset_b = _exact(a) - a["video_timestamp_ms"], _exact(b) - b["video_timestamp_ms"]
    if abs(offset_a - offset_b) > Fraction(102, 100):
        return "source_clock_origin_discontinuity"
    seq = context.sequence
    if not np.array_equal(seq.frame_dimensions_px[before], seq.frame_dimensions_px[after]):
        return "source_dimensions_changed"
    if seq.model_estimate_bindings is not None and not model_bindings_continuous(seq, before, after):
        return "model_geometry_source_discontinuity"
    return None


def _step_reasons(step):
    reasons = []
    for field, reason in (("preceding_low_motion_duration_ms", "preceding_low_motion_below_80_ms"),
                          ("motion_duration_ms", "motion_below_80_ms"),
                          ("following_low_motion_duration_ms", "following_low_motion_below_80_ms")):
        if step[field] < MIN_DURATION_MS:
            reasons.append(reason)
    if abs(step["delta_xy_torso"][0]) < ORDER_DEADBAND_TORSO:
        reasons.append("horizontal_motion_below_0_08_torso")
    return reasons


def locate_footwork_cycles(sequence: PoseSequence, *, primary_timeline: Sequence[Mapping],
                          source_run_id: str, events: Sequence[Mapping] = ()) -> dict:
    """Scan the entire supplied source snapshot, never extend a saved window.

    Each continuity segment uses one fixed torso scale. Candidate boundaries
    are the *full* adjacent low-motion runs, never optimized for a grade. A
    second measurement on exactly those boundaries records whether the same
    motion survives the existing window-specific scale. Neither result is an
    FS03/FS04 classification or authorization to bypass the manual card route.
    """
    if not isinstance(source_run_id, str) or not source_run_id:
        raise ValueError("source_run_id_required")
    if not isinstance(primary_timeline, (list, tuple)):
        raise ValueError("primary_timeline_required")
    frames = sequence.source_frames.astype(int).tolist()
    if len(set(frames)) != len(frames) or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError("unique_ordered_source_frames_required")
    unique = {}
    for event in events:
        if (not isinstance(event, Mapping) or event.get("video_id") != source_run_id
                or not isinstance(event.get("event_id"), str)
                or type(event.get("start_ms")) not in (int, float)
                or type(event.get("end_ms")) not in (int, float)
                or not np.isfinite([event["start_ms"], event["end_ms"]]).all()
                or event["start_ms"] > event["end_ms"]):
            raise ValueError("source_event_inventory_invalid")
        if event["event_id"] in unique and unique[event["event_id"]] != event:
            raise ValueError("source_event_id_conflict")
        unique[event["event_id"]] = event
    context = prepare_source_aligned_context(sequence, list(unique.values()), primary_timeline=primary_timeline)
    indexes = np.arange(len(frames))
    bindings = _source_frame_evidence(context, indexes)
    primary_counts = Counter(row.get("source_frame_index") for row in primary_timeline if isinstance(row, Mapping))
    qualities = []
    for event in unique.values():
        quality, invalid = _quality_evidence(event, CORE_JOINTS)
        if invalid and not quality:
            raise ValueError(invalid)
        qualities.append((event, quality))
    reasons, segments, boundaries, current = [], [], [], []
    for i, row in enumerate(bindings):
        reason = _frame_reason(context, i, row, primary_counts)
        involved = [(e, q) for e, q in qualities if e["start_ms"] <= sequence.timestamp_ms[i] <= e["end_ms"]
                    and e.get("person_track_id") == sequence.primary_player_id]
        # A censored *old event phase* does not erase intact frames from this
        # independent scan. The new candidate must establish its own two low
        # runs. Confirmed identity problems still invalidate the source rows.
        if any(any("confirmed" in flag and ("identity" in flag or "id_switch" in flag)
                   for flag in q["blocking_flags"]) for _, q in involved):
            reason = reason or "original_event_hard_quality_block"
        reasons.append(reason)
        if reason:
            if current:
                segments.append(current)
                current = []
            boundaries.append({"source_frame": frames[i], "reason": reason, "kind": "excluded_frame"})
            continue
        if current and (gap := _pair_reason(context, current[-1], i, bindings)):
            boundaries.append({"before_source_frame": frames[current[-1]], "source_frame": frames[i],
                               "reason": gap, "kind": "continuity_break"})
            segments.append(current)
            current = []
        current.append(i)
    if current:
        segments.append(current)
    output, candidates = [], []
    for number, selected in enumerate(segments):
        selected = np.asarray(selected)
        values, raw, reason = (_measure(context, selected) if _source_times(sequence, selected) is not None
                               else ({}, {}, "source_clock_binding_unverified"))
        source_frames = [frames[i] for i in selected]
        segment_id = f"segment-{number + 1}"
        segment = {"segment_id": segment_id, "source_frames": source_frames,
            "source_frame_bindings": [bindings[i] for i in selected],
            "associated_event_quality": [{"event_id": e["event_id"], "event_code": e.get("event_code"),
                "start_ms": e["start_ms"], "end_ms": e["end_ms"], "quality": deepcopy(q)}
                for e, q in qualities if e["start_ms"] <= sequence.timestamp_ms[selected[-1]]
                and e["end_ms"] >= sequence.timestamp_ms[selected[0]]
                and e.get("person_track_id") == sequence.primary_player_id],
            "required_joint_names": list(CORE_JOINTS), "common_valid_frame_count": len(selected),
            "source_geometry_series": [{"source_frame": frames[i], "points": {
                name: context.points[name][i].tolist() for name in CORE_JOINTS},
                "confidence": {name: float(sequence.confidence[name][i]) if np.isfinite(sequence.confidence[name][i]) else None
                               for name in CORE_JOINTS},
                "model_estimate_mask": {name: bool(sequence.model_estimate_masks.get(name, np.zeros(len(frames), dtype=bool))[i])
                                        for name in CORE_JOINTS}} for i in selected],
            "points_semantics": "already_camera_compensated_reference_xy_do_not_apply_transform_again",
            "measurement_reason": reason, "series": raw}
        output.append(segment)
        if reason:
            continue
        by_frame = {frames[i]: i for i in selected}
        for side, steps in raw["motion_step_candidates"].items():
            for step in steps:
                first = step["preceding_source_frames"][0]
                last = step["following_source_frames"][-1]
                local = np.arange(by_frame[first], by_frame[last] + 1)
                _, checked, local_reason = _measure(context, local)
                matching = next((s for s in checked.get("motion_step_candidates", {}).get(side, [])
                    if s["start_source_frame"] == step["start_source_frame"]
                    and s["end_source_frame"] == step["end_source_frame"]), None)
                reject = _step_reasons(step)
                if local_reason:
                    reject.append(local_reason)
                elif matching is None:
                    reject.append("motion_boundary_changed_under_candidate_window_reference")
                else:
                    reject.extend(_step_reasons(matching))
                candidates.append({"candidate_id": f"{source_run_id}:ankle:{side}:{step['start_source_frame']}:{step['end_source_frame']}",
                    "segment_id": segment_id, "moving_ankle": side,
                    "start_source_frame": first, "end_source_frame": last,
                    "source_frames": frames[local[0]:local[-1] + 1],
                    "motion": deepcopy(step), "candidate_window_motion": deepcopy(matching),
                    "candidate_window_fixed_torso_scale": checked.get("fixed_torso_scale"),
                    "complete_visual_motion_cycle": not reject, "reason_codes": list(dict.fromkeys(reject)),
                    "score": None, "grade": None, "event_code": None})
    return {"version": VERSION, "cycle_policy_version": CYCLE_POLICY_VERSION, "semantics": SEMANTICS,
        "source_run_id": source_run_id, "file_artifacts_verified": False,
        "policy": {"speed_threshold_torso_s": LOW_ANKLE_SPEED_TORSO_S, "minimum_phase_duration_ms": MIN_DURATION_MS,
                   "minimum_horizontal_motion_torso": ORDER_DEADBAND_TORSO, "minimum_window_frames": 7,
                   "reference_scope": "one_fixed_median_torso_per_continuity_segment_then_exact_candidate_window_recheck"},
        "source_frame_count": len(frames), "source_frames": frames,
        "excluded_frame_count": sum(reason is not None for reason in reasons), "boundaries": boundaries,
        "excluded_frames": [{"source_frame": frames[i], "reason": reason,
            "source_binding": bindings[i],
            "required_joint_validity": {name: bool(context.masks[name][i]) for name in CORE_JOINTS}}
            for i, reason in enumerate(reasons) if reason is not None],
        "segments": output, "candidates": candidates,
        "summary": {"motion_candidate_count": len(candidates),
                    "complete_visual_motion_cycle_count": sum(c["complete_visual_motion_cycle"] for c in candidates),
                    "incomplete_motion_candidate_count": sum(not c["complete_visual_motion_cycle"] for c in candidates)},
        "tennis_family_assignment_count": 0, "scored_card_instance_count": 0,
        "score": None, "grade": None, "source_grade_supported": False,
        "limitation": "仅定位连续图像踝运动；低速不代表触地，不确定并步/交叉步/微调动作类别，不改变既有人工窗口或评分。"}
