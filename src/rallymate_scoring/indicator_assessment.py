"""Explain all 298 source cards against this run's actual observations.

This module never equates an estimated three-phase swing to ten GS stages.
It reuses the audited Word catalogue and keeps source-bound, historical-proxy
and contextual motion measurements distinct. A missing grade is a typed state.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from functools import lru_cache
from importlib.resources import files
from typing import Any

from .indicator_observations import (
    VERSION as OBSERVATION_VERSION, MEASUREMENTS, MIN_WINDOW_DURATION_MS, MIN_WINDOW_SAMPLES,
)
from .source_assessment import source_binding
from rallymate_features.source_alignment import SOURCE_LOCATORS, HEAD, HIPS, ANKLES, JOINTS, SOURCE_REQUIRED_JOINTS
from rallymate_features.event_features import FEATURE_DEFINITIONS, MIN_VALID_FRACTION
from .model_measurement_contract import validated_model_window, validated_geometry_mask, has_model_measurement_context, MODEL_MEASUREMENT_PROVENANCE

VERSION = "word-indicator-assessment-v1.0.0"
MAX_OBSERVATIONS_PER_SERIES = 12
MOTION_VERSIONS = {"process-motion-v1.0.0", "process-motion-v1.1.0"}
# These are the current classifier's actual labels. It cannot classify slice;
# a generic future 'backhand' label must not silently mean a single-hand stroke.
TYPE_TO_GS = {"forehand": "GS01", "two_handed_backhand": "GS02"}
# Current automatic producers emit FS01/FS02/FS09 and classify GS01/GS02/GS03.
# Other registered names and externally supplied events are not detector paths.
# This inventory explains limitations only; it never rejects linked measurements.
UNSUPPORTED_AUTOMATIC_EVENT_CODES = frozenset({
    "FS03", "FS04", "FS05", "FS06", "FS07", "FS08", "FS10", "GS04", "GS05",
})
COMPLETE_GRADE_IMPLEMENTATION_MESSAGE = (
    "当前尚未实现并验证本项完整原文动作条件、阶段时序及 A～E 等级的自动判定；"
    "已有局部测量不能替代完整判定。"
)
STATUS_LABELS = {
    "source_rule_conflict": "原文规则待修订", "source_boundaries_missing": "原文阶段边界缺失",
    "measured_partial_rule": "已取得部分原文测量", "legacy_proxy_measured": "已有历史代理测量",
    "contextual_measurement": "已有相关动作测量", "event_type_unresolved": "动作类型待确认",
    "evidence_insufficient": "当前证据不足", "not_observed": "本次未观测",
}
LABELS = {
    "left_knee_flexion_deg": "左膝屈曲角", "right_knee_flexion_deg": "右膝屈曲角",
    "left_elbow_angle_deg": "左肘夹角", "right_elbow_angle_deg": "右肘夹角",
    "trunk_tilt_deg": "躯干投影倾角", "ankle_distance_over_hip_width": "踝距与髋宽比",
    "hip_inside_ankle_support_fraction": "髋投影处于踝支撑区间的比例",
    "hip_to_ankle_midpoint_torso": "髋中心相对踝中点偏移", "wrist_separation_torso": "双腕距离",
    "head_to_shoulders_distance_torso": "头部相对双肩距离", "shoulder_axis_deg": "肩线投影角",
    "hip_axis_deg": "髋线投影角", "shoulder_hip_separation_deg": "肩髋投影分离角",
}
PHASE_LABELS = {"preparation": "估计准备段", "acceleration": "估计加速段", "follow_through": "估计随挥段"}
SOURCE_FEATURES = {
    "FS01-M02": {"preload_hip_height_drop_body": "body", "left_preload_knee_flexion_change_deg": "deg",
                 "right_preload_knee_flexion_change_deg": "deg", "preload_body_speed_change_rate_body_s2": "body/s2",
                 "preload_hip_reference_dy_px": "px", "preload_hip_reference_dy_torso": "torso"},
    "FS01-M04": {"post_slowdown_ankle_width_to_hip_width_ratio": "ratio"},
    "FS01-M05": {"landing_proxy_to_next_fs02_ms": "ms", "post_landing_body_speed_std_body_s": "body/s"},
    "FS09-M05": {"stable_control_proxy_to_next_fs10_or_fs02_ms": "ms", "stable_control_body_speed_std_body_s": "body/s"},
    "FS02-M01": {"launch_direction_deg": "deg"},
    "FS01-M01": {"head_center_mean_speed_frame_long_edge_s": "frame_long_edge/s", "head_axis_circular_std_deg": "deg"},
}
SOURCE_MEASUREMENT_JOINTS = {
    'preload_hip_height_drop_body': HIPS + ANKLES,
    'left_preload_knee_flexion_change_deg': ('left_hip', 'left_knee', 'left_ankle'),
    'right_preload_knee_flexion_change_deg': ('right_hip', 'right_knee', 'right_ankle'),
    'preload_body_speed_change_rate_body_s2': JOINTS[:4],
    'preload_hip_reference_dy_px': JOINTS[:4],
    'preload_hip_reference_dy_torso': JOINTS[:4],
    'post_slowdown_ankle_width_to_hip_width_ratio': HIPS + ANKLES,
    'landing_proxy_to_next_fs02_ms': JOINTS, 'stable_control_proxy_to_next_fs10_or_fs02_ms': JOINTS,
    'post_landing_body_speed_std_body_s': JOINTS[:4], 'stable_control_body_speed_std_body_s': JOINTS[:4],
    'launch_direction_deg': HEAD + HIPS,
    'head_center_mean_speed_frame_long_edge_s': HEAD, 'head_axis_circular_std_deg': HEAD,
}
SINGLE_SIDE_MODEL_FEATURES = {
    'first_step_displacement_body', 'launch_foot_motion_duration_ms',
    'launch_foot_slowdown_to_post_hip_direction_ms', 'launch_foot_speed_drop_body_s',
    'launch_foot_speed_peak_body_s', 'post_step_hip_direction_consistency',
}


def source_model_requested(*payloads):
    """An incomplete model marker cannot silently enter the confidence path."""
    return has_model_measurement_context(*payloads)


def source_model_track(payload):
    """Canonical subject IDs and decoder source track IDs are distinct."""
    diagnostics = _obj(payload.get('track_diagnostics'))
    if 'primary_player_id' in diagnostics and diagnostics['primary_player_id'] != payload.get('person_track_id'):
        return None
    tracks = diagnostics.get('source_track_ids')
    return tracks[0] if (isinstance(tracks, list) and len(tracks) == 1
        and isinstance(tracks[0], int) and not isinstance(tracks[0], bool) and tracks[0] >= 0) else None


def source_sha256_equal(left, right):
    """Compare strict SHA-256 identities without changing transported proof text."""
    return (isinstance(left, str) and isinstance(right, str)
        and re.fullmatch(r"[0-9a-fA-F]{64}", left) is not None
        and re.fullmatch(r"[0-9a-fA-F]{64}", right) is not None
        and left.lower() == right.lower())


def source_model_geometry(payload, required, *, person_track_id, video_sha256, parent_window=None):
    """Recompute geometry availability; this never verifies Word visibility."""
    window = validated_model_window(payload, person_track_id=person_track_id, parent_window=parent_window)
    if window is None or not source_sha256_equal(window.get('video_sha256'), video_sha256):
        return None
    masks = validated_geometry_mask(payload.get('geometry_mask'), window, required_joints=required)
    if masks is None:
        return None
    total = len(window['source_frames'])
    valid = sum(all(masks[name][i] for name in required) for i in range(total))
    return {'semantics': 'model_estimate_geometry_not_verified_visibility',
            'required_joint_names': list(required), 'valid_frame_count': valid, 'total_frame_count': total,
            'fraction': valid / total, 'passes': valid * 10 >= total * 7,
            'threshold': .7, 'operator': '>=', 'visibility_verified': False,
            'source_visibility_70_percent_verified': None}


def source_model_masks_match(payload, parent):
    """Overlapping physical samples cannot change geometry availability."""
    child_window, parent_window = _obj(payload.get('source_window')), _obj(parent.get('source_window'))
    child_masks = validated_geometry_mask(payload.get('geometry_mask'), child_window) if 'source_frames' in child_window else None
    parent_masks = validated_geometry_mask(parent.get('geometry_mask'), parent_window) if 'source_frames' in parent_window else None
    if child_masks is None or parent_masks is None or any(name not in parent_masks for name in child_masks):
        return False
    positions = {frame: i for i, frame in enumerate(parent_window['source_frames'])}
    return all(mask[i] == parent_masks[name][positions[frame]] for name, mask in child_masks.items()
        for i, frame in enumerate(child_window['source_frames']) if frame in positions)


def source_model_evidence(payload, track):
    """Export only the shared validator's allowlisted transport proof."""
    window = validated_model_window(payload, person_track_id=track)
    masks = validated_geometry_mask(payload.get('geometry_mask'), window) if window else None
    return {'measurement_provenance': dict(MODEL_MEASUREMENT_PROVENANCE),
            'source_window': window, 'geometry_mask': masks, 'source_track_id': track} if masks is not None else {}


def _obj(value):
    return value if isinstance(value, Mapping) else {}


def _rows(value):
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, (list, tuple)) else []


def _num(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _interval(row, *, allow_zero=False):
    a, b = row.get("start_ms"), row.get("end_ms")
    return _num(a) and _num(b) and 0 <= a and (a <= b if allow_zero else a < b)


@lru_cache(maxsize=1)
def _catalog():
    return json.loads(files("rallymate_scoring").joinpath("data/technical_review_rules.json").read_text(encoding="utf-8"))


def _episodes(summary):
    families = _obj(_obj(_obj(summary.get("action_recognition")).get("motion_analysis")).get("families"))
    selected, conflicts = {}, set()
    for family_name, family in families.items():
        # A projected return row duplicates its original baseline movement.
        if family_name == "return":
            continue
        for row in _rows(_obj(family).get("episodes")):
            track = row.get("person_track_id")
            if not _interval(row) or not isinstance(track, int) or isinstance(track, bool) or track < 0:
                continue
            identifier = row.get("episode_id")
            if not isinstance(identifier, str) or not identifier:
                continue
            key = track, row["start_ms"], row["end_ms"]
            if key in conflicts:
                continue
            if key in selected and selected[key].get("process_motion") != row.get("process_motion"):
                selected.pop(key)
                conflicts.add(key)
            else:
                selected.setdefault(key, {**row, "family": row.get("family", family_name)})
    return sorted(selected.values(), key=lambda r: (r["start_ms"], r["end_ms"], r["episode_id"]))


def _condition(name, stats):
    delta = stats.get("delta")
    if not _num(delta):
        return None
    status = "observed_increase" if delta > 0 else "observed_decrease" if delta < 0 else "no_net_change"
    text = "增加" if delta > 0 else "减少" if delta < 0 else "净变化为零"
    # This states the measured sign only. No tolerance or technical threshold
    # is invented to turn an arbitrarily small change into a 'correct' action.
    return {"status": status, "label_zh": f"该连续段的{LABELS.get(name, name)}{text}",
            "semantics": "arithmetic_observation_not_technical_grade",
            "reason_zh": "仅比较实测起止值；没有测量误差标定，不能据此确认技术动作达到要求。"}


def _candidate_event_code(episode):
    classification = _obj(episode.get("classification"))
    if episode.get("family") != "baseline" or classification.get("status") != "rule_inferred":
        return None
    label = classification.get("label")
    if label == "backhand":
        return "GS03" if classification.get("label_zh") == "单手反手挥拍" else None
    return TYPE_TO_GS.get(label)


def _valid_motion_window(window, phase):
    """Keep measurements traceable to actual, ordered source frames.

    This checks the producer's evidence contract, not recognition accuracy.
    Missing/invalid evidence cannot be replaced by a plausible scalar summary.
    """
    frames, count = window.get("source_frames"), window.get("sample_count")
    if (not _interval(window) or not phase["start_ms"] <= window["start_ms"] < window["end_ms"] <= phase["end_ms"]
            or window["end_ms"] - window["start_ms"] < MIN_WINDOW_DURATION_MS
            or not isinstance(count, int) or isinstance(count, bool) or count < MIN_WINDOW_SAMPLES
            or not isinstance(frames, list) or len(frames) != count
            or any(not isinstance(f, int) or isinstance(f, bool) or f < 0 for f in frames)
            or any(a >= b for a, b in zip(frames, frames[1:]))
            or not all(_num(window.get(k)) for k in ("start", "end", "delta", "min", "max", "median", "mean", "range"))):
        return False
    return (abs(window["end"] - window["start"] - window["delta"]) <= .0001
            and abs(window["max"] - window["min"] - window["range"]) <= .0001
            and window["range"] >= 0
            and all(window["min"] <= window[key] <= window["max"] for key in ("start", "end", "mean", "median")))


def motion_joint_observations(episodes):
    """Validate source-bound joint windows for card and dimension consumers."""
    observations = []
    for episode in episodes:
        process = _obj(episode.get("process_motion"))
        source = _obj(process.get("source_observations"))
        if (process.get("version") not in MOTION_VERSIONS
                or process.get("coordinate_system") != "body_relative_image_x_right_y_down"
                or process.get("coordinate_unit") != "torso_length"
                or source.get("version") != OBSERVATION_VERSION
                or source.get("semantics") != "joint_specific_2d_measurements_not_technical_grade"):
            continue
        continuity = _obj(_obj(episode.get("evidence")).get("process_continuity"))
        reference_keys = ("selection_epoch", "camera_reference_epoch", "camera_compensated", "normalization_basis")
        if not all(key in continuity for key in reference_keys):
            continue
        expected_reference = {"person_track_id": episode["person_track_id"], **{key: continuity[key] for key in reference_keys}}
        provenance = _obj(process.get("measurement_provenance"))
        if provenance.get("observation_semantics") == "monocular_model_estimate":
            model_keys = ("pose_source", "observation_semantics", "video_sha256", "source_selection_epoch")
            if (not all(key in continuity for key in model_keys) or continuity["pose_source"] != "sam3d"
                    or continuity["observation_semantics"] != "monocular_model_estimate"
                    or provenance.get("scoring_allowed") is not False
                    or source.get("measurement_provenance") != provenance):
                continue
            expected_reference.update({key: continuity[key] for key in model_keys})
        for phase in _rows(source.get("phases")):
            if (phase.get("phase") not in PHASE_LABELS or not _interval(phase)
                    or not episode["start_ms"] <= phase["start_ms"] < phase["end_ms"] <= episode["end_ms"]):
                continue
            for name, metric in _obj(phase.get("measurements")).items():
                if name not in MEASUREMENTS or not isinstance(metric, Mapping) or metric.get("unit") != MEASUREMENTS[name][1]:
                    continue
                if metric.get("status") not in {"measured", "partial"}:
                    continue
                total, valid = metric.get("total_frame_count"), metric.get("valid_frame_count")
                if (not isinstance(total, int) or isinstance(total, bool) or total <= 0
                        or not isinstance(valid, int) or isinstance(valid, bool) or not 0 <= valid <= total):
                    continue
                windows = []
                rejected_window = False
                for window in _rows(metric.get("windows")):
                    if (window.get("reference") != expected_reference or not _valid_motion_window(window, phase)
                            or window["sample_count"] + sum(w["sample_count"] for w in windows) > valid
                            or windows and (window["start_ms"] <= windows[-1]["end_ms"]
                                            or window["source_frames"][0] <= windows[-1]["source_frames"][-1])):
                        rejected_window = True
                        continue
                    if provenance.get("observation_semantics") == "monocular_model_estimate":
                        origin = window.get("source_time_origin_ms")
                        bound = _num(origin)
                        for edge, frame_index in (("start", window["source_frames"][0]), ("end", window["source_frames"][-1])):
                            binding = _obj(window.get(f"source_binding_{edge}"))
                            expected = {"video_sha256": expected_reference["video_sha256"],
                                        "person_track_id": expected_reference["person_track_id"],
                                        "selection_epoch": expected_reference["source_selection_epoch"],
                                        "camera_reference_epoch": expected_reference["camera_reference_epoch"],
                                        "frame_index": frame_index, "video_timestamp_ms": window[f"{edge}_ms"]}
                            bound = bound and all(binding.get(key) == value for key, value in expected.items())
                            bound = bound and _num(binding.get("timestamp_ms")) and abs(binding["timestamp_ms"] - origin - window[f"{edge}_ms"]) <= 1.01
                        if not bound:
                            rejected_window = True
                            continue
                    kept = {"reference": dict(expected_reference), "source_frames": list(window["source_frames"]),
                            **{k: window[k] for k in ("sample_count", "start_ms", "end_ms", "start", "end", "delta", "min", "max", "median", "mean", "range")}}
                    for edge in ("start", "end"):
                        if isinstance(window.get(f"source_binding_{edge}"), Mapping):
                            kept[f"source_binding_{edge}"] = dict(window[f"source_binding_{edge}"])
                    if "source_time_origin_ms" in window:
                        kept["source_time_origin_ms"] = window["source_time_origin_ms"]
                    windows.append(kept)
                if not windows:
                    continue
                complete = (not rejected_window and metric.get("status") == "measured" and valid == total and len(windows) == 1
                            and windows[0]["sample_count"] == total
                            and windows[0]["start_ms"] == phase["start_ms"] and windows[0]["end_ms"] == phase["end_ms"])
                summary_values = windows[0] if complete else metric
                statistics = {key: summary_values.get(key) if _num(summary_values.get(key)) and (complete or key not in {"start", "end", "delta"})
                              and valid * 10 >= total * 7 and not rejected_window else None
                              for key in ("start", "end", "delta", "min", "max", "median", "mean", "range")}
                identifier = f"motion:{episode['episode_id']}:{phase['phase']}:{name}"
                observations.append({"observation_id": identifier, "source": "motion_joint_measurement",
                    "scope": "contextual_motion_phase", "status": "measured" if complete else "partial",
                    "feature_name": name, "label_zh": LABELS[name], "value": statistics["mean" if name.endswith("fraction") else "median"],
                    "unit": metric["unit"], "episode_id": episode["episode_id"], "person_track_id": episode["person_track_id"],
                    "phase": phase["phase"], "phase_label_zh": PHASE_LABELS[phase["phase"]],
                    "start_ms": phase["start_ms"], "end_ms": phase["end_ms"], "statistics": statistics, "windows": windows,
                    "coverage": round(valid / total, 6), "definition_zh": MEASUREMENTS[name][3],
                    "condition": _condition(name, statistics), "full_source_rule_satisfied": False,
                    "measurement_provenance": dict(provenance),
                    "candidate_event_code": _candidate_event_code(episode),
                    "rule_motion_type": _obj(episode.get("classification")).get("label", "unclassified")})
    return observations


def _source_observations(source):
    result = []
    if (source.get("version") != "source-aligned-assessment-v1.0.0"
            or source.get("source_binding") != source_binding()):
        return result
    allowed = set(SOURCE_FEATURES)
    for indicator in _rows(source.get("indicators")):
        identifier = indicator.get("indicator_id")
        if identifier not in allowed:
            continue
        for window in _rows(indicator.get("windows")):
            if not _interval(window):
                continue
            for measurement in _rows(window.get("measurements")):
                bounds = _obj(measurement.get("measurement_window"))
                if (measurement.get("status") != "measured" or not _num(measurement.get("value"))
                        or not _interval(bounds, allow_zero=True)):
                    continue
                name = measurement.get("feature_name")
                if (name not in SOURCE_FEATURES[identifier] or measurement.get("unit") != SOURCE_FEATURES[identifier][name]
                        or measurement.get("source_ref") != SOURCE_LOCATORS[identifier][0]):
                    continue
                model_details = {}
                if source_model_requested(window, measurement):
                    track = source_model_track(window)
                    parent = validated_model_window(window, person_track_id=track) if track is not None else None
                    geometry = source_model_geometry(measurement, SOURCE_MEASUREMENT_JOINTS[name],
                        person_track_id=track, video_sha256=source.get('video_sha256'),
                        parent_window=None if name in {'landing_proxy_to_next_fs02_ms', 'stable_control_proxy_to_next_fs10_or_fs02_ms'} else parent)
                    core = source_model_geometry(window, SOURCE_REQUIRED_JOINTS[identifier],
                        person_track_id=track, video_sha256=source.get('video_sha256'))
                    if (parent is None or geometry is None or not geometry['passes'] or core is None or not core['passes']
                            or not source_model_masks_match(measurement, window)
                            or measurement.get('model_geometry_coverage') != geometry
                            or _obj(window.get('visibility')).get('visibility_verified') is not False
                            or _obj(window.get('visibility')).get('valid_frame_ratio') is not None
                            or measurement.get('source_frames') != _obj(measurement.get('source_window')).get('source_frames')
                            or _obj(measurement.get('source_window')).get('start_ms') != bounds['start_ms']
                            or _obj(measurement.get('source_window')).get('end_ms') != bounds['end_ms']):
                        continue
                    model_details = source_model_evidence(measurement, track)
                    model_details['model_geometry_coverage'] = geometry
                    model_details['track_diagnostics'] = {'source_track_ids': [track]}
                    model_details['source_track_id'] = track
                result.append({"observation_id": f"source:{identifier}:{window.get('event_id')}:{name}",
                    "source": "source_aligned", "scope": "source_indicator_window", "status": "measured",
                    "indicator_id": identifier, "feature_name": name, "label_zh": measurement.get("label_zh", name),
                    "value": measurement["value"], "unit": measurement.get("unit", ""), "event_id": window.get("event_id"),
                    "person_track_id": window.get("person_track_id"), "start_ms": bounds["start_ms"], "end_ms": bounds["end_ms"],
                    "definition_zh": measurement.get("source_requirement_zh", "原文对应的局部测量。"),
                    "source_ref": measurement.get("source_ref"), "windows": [], "full_source_rule_satisfied": False,
                    "source_frames": measurement.get("source_frames", []), "measurement_definition": measurement.get("definition"),
                    "coverage": measurement.get("coverage"), "evidence": measurement.get("evidence", {}),
                    "assessment_status": "measurement_only", "scoring_allowed": False, **model_details})
    return result


def _legacy_observations(records, summary, known):
    output, observed, selected, conflicts = [], set(), {}, set()
    expected_video = _obj(summary.get("minimum_scoring_loop")).get("video_id") or summary.get("job_id")
    for record in records:
        identifier, event = record.get("indicator_id"), record.get("event_id")
        if identifier not in known or not isinstance(event, str) or not event:
            continue
        if expected_video is not None and record.get("video_id") != expected_video:
            continue
        if record.get("event_code") != known[identifier]["event_code"]:
            continue
        key = identifier, event
        if key in conflicts:
            continue
        if key in selected and selected[key] != record:
            selected.pop(key)
            conflicts.add(key)
        else:
            selected[key] = record
    for (identifier, event), record in selected.items():
        observed.add(known[identifier]["event_code"])
        record_model = source_model_requested(record)
        track = source_model_track(record)
        parent = validated_model_window(record, person_track_id=track) if record_model and track is not None else None
        if record_model and (parent is None or validated_geometry_mask(record.get('geometry_mask'), parent) is None):
            continue
        names = [m.get("feature_name") for m in _rows(record.get("features"))]
        if any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names)):
            continue
        for metric in _rows(record.get("features")):
            name = metric.get("feature_name")
            frames = metric.get("source_frames")
            if (metric.get("valid") is not True or not _num(metric.get("value")) or not isinstance(name, str) or not name
                    or not isinstance(frames, list) or not frames
                    or any(not isinstance(f, int) or isinstance(f, bool) or f < 0 for f in frames)):
                continue
            model_details = {}
            if record_model or source_model_requested(metric):
                child = validated_model_window(metric, person_track_id=track, parent_window=parent) if parent else None
                definition = FEATURE_DEFINITIONS.get(name, {})
                required = definition.get('required_joints', [])
                selected_side = metric.get('selected_side')
                if 'required_joints_used' in metric or 'selected_side' in metric:
                    if (name not in SINGLE_SIDE_MODEL_FEATURES or selected_side not in {'left', 'right'}
                            or metric.get('required_joints_used') != ['left_hip', 'right_hip', f'{selected_side}_ankle']):
                        continue
                    required = metric['required_joints_used']
                masks = validated_geometry_mask(metric.get('geometry_mask'), child, required_joints=required) if child else None
                if (child is None or masks is None or not required or metric.get('unit') != definition.get('unit')
                        or any(not any(masks[joint]) for joint in required)
                        or 'confidence' not in metric or metric['confidence'] is not None
                                                or _obj(record.get('quality_gate')).get('measurement_allowed') is not True
                        or _obj(record.get('provenance')).get('video_sha256') != parent['video_sha256']
                        or not source_model_masks_match(metric, record)
                        or len(set(frames)) != len(frames) or any(frame not in child['source_frames'] for frame in frames)):
                    continue
                joint_coverage = sum(all(masks[joint][i] for joint in required)
                                     for i in range(len(child['source_frames']))) / len(child['source_frames'])
                if joint_coverage < MIN_VALID_FRACTION:
                    continue
                model_details = source_model_evidence(metric, track)
                model_details.update(start_ms=child['start_ms'], end_ms=child['end_ms'],
                    person_track_id=record.get('person_track_id'), track_diagnostics={'source_track_ids': [track]},
                    source_track_id=track,
                    assessment_status='measurement_only', scoring_allowed=False,
                    source_visibility_70_percent_verified=None)
                if selected_side is not None:
                    model_details.update(selected_side=selected_side, required_joints_used=list(required))
            condition = None
            if (name == "hip_center_relative_to_ankle_support"
                    and "区间" in known[identifier]["calculation"]):
                condition = {"status": "inside_interval" if 0 <= metric["value"] <= 1 else "outside_interval",
                    "value": 0 <= metric["value"] <= 1,
                    "label_zh": "代表帧髋投影位于双踝区间" if 0 <= metric["value"] <= 1 else "代表帧髋投影位于双踝区间外",
                    "operator": "0 <= projected_position <= 1", "interval": [0, 1],
                    "semantics": "binary_geometry_only_not_full_action_grade_or_balance", "source_frames": frames}
            output.append({"observation_id": f"legacy:{identifier}:{event}:{name}", "source": "legacy_feature",
                "scope": "legacy_event_feature", "status": "measured", "indicator_id": identifier,
                "feature_name": name, "label_zh": LABELS.get(name, name), "value": metric["value"],
                "unit": metric.get("unit", ""), "event_id": event, "source_frames": frames,
                "definition_zh": "历史事件内代理特征；单位、阶段或定义可能只覆盖原文的一部分，不替代源对齐测量。",
                "windows": [], "full_source_rule_satisfied": False, "condition": condition, **model_details})
    return output, observed


def _related_measurements(rule):
    text, names = rule["calculation"], set()
    if "肩线" in text or "髋线" in text:
        names.update({"shoulder_axis_deg", "hip_axis_deg", "shoulder_hip_separation_deg"})
    if "腕/肘" in text:
        names.update({"left_elbow_angle_deg", "right_elbow_angle_deg", "wrist_separation_torso"})
    if "膝" in text:
        names.update({"left_knee_flexion_deg", "right_knee_flexion_deg"})
    # A body-relative hip/ankle relationship does not measure world-space hip
    # translation, forward weight transfer, balance, or actual support forces.
    if "髋中心" in text and ("双踝区间" in text or "双踝中点" in text):
        names.add("hip_inside_ankle_support_fraction" if "区间" in text else "hip_to_ankle_midpoint_torso")
    if "躯干" in text:
        names.add("trunk_tilt_deg")
    if "踝距" in text and "髋宽" in text:
        names.add("ankle_distance_over_hip_width")
    # Head position is not a gaze measurement. Keep this comparison explicitly
    # absent for '持续盯球' cards with no corresponding head-position formula.
    if "头部中心" in text or "头部位置" in text:
        names.add("head_to_shoulders_distance_torso")
    return names


def _measurement_blockers(rule):
    text = rule["unevaluable"]
    rows = []
    if rule.get("indicator_id") == "FS02-M01":
        rows.append({"code": "incoming_ball_direction_not_bound", "message_zh": "启动方向是图像代理；未绑定活动球轨迹，不能判断是否与来球方向一致。"})
    for needle, code, message in (
        ("真实注视", "true_gaze_unobservable", "二维姿态不能直接确认眼球注视方向。"),
        ("真正盯球", "true_gaze_unobservable", "头部稳定不能证明正在盯球。"),
        ("球拍专项关键点", "racket_keypoints_required", "球拍框或手腕轨迹不能替代拍头、拍轴和拍面关键点。"),
        ("击球事件不可用", "ball_contact_evidence_required", "需关联活动球轨迹与实际击球事件，腕速峰值不能替代触球。"),
        ("稳定地面基线", "ground_reference_required", "真实离地判断需要稳定地面参照。"),
        ("脚跟/脚尖", "foot_contact_evidence_required", "精确落地接触帧需要脚跟、脚尖和地面标定。"),
    ):
        if needle in text:
            rows.append({"code": code, "message_zh": message})
    if any(term in text for term in ("地面反作用力", "蹬地力", "真实发力", "真实承重")):
        rows.append({"code": "force_unobservable", "message_zh": "视觉姿态不能直接测量真实地面反作用力、肌力或承重；独立的膝角、位移及时序仍可作为几何观测。"})
    return rows


def _select_observations(observations):
    """Bound the public evidence list without favouring favourable measurements.

    Keep endpoints and uniform interior observations from every distinct series;
    full input counts and per-card counts remain available separately.
    """
    series = defaultdict(list)
    for index, observation in enumerate(observations):
        key = (observation["source"], observation.get("indicator_id"), observation["feature_name"],
               observation.get("phase"), observation.get("candidate_event_code"))
        order = observation.get("start_ms")
        if not _num(order):
            order = min(observation.get("source_frames", [index]))
        series[key].append((order, index))
    keep = set()
    for values in series.values():
        values.sort()
        if len(values) <= MAX_OBSERVATIONS_PER_SERIES:
            keep.update(index for _, index in values)
        else:
            keep.update(values[round(i * (len(values) - 1) / (MAX_OBSERVATIONS_PER_SERIES - 1))][1]
                        for i in range(MAX_OBSERVATIONS_PER_SERIES))
    return [observation for index, observation in enumerate(observations) if index in keep]


def _evaluated_event_grades(records, summary, known):
    """Recompute persisted rule results; a bare claimed grade is insufficient.

    This is arithmetic validation, not an approval ledger or an accuracy claim.
    F2/F4, human labels and model brand have no role in this calculation.
    """
    from .scoring import score_indicator

    video = _obj(summary.get("minimum_scoring_loop")).get("video_id") or summary.get("job_id")
    unique, conflicts = {}, set()
    for record in records:
        identifier, event = record.get("indicator_id"), record.get("event_id")
        score = _obj(record.get("score_result"))
        if (identifier not in known or record.get("video_id") != video
                or record.get("event_code") != known[identifier]["event_code"]
                or not isinstance(event, str) or not event or score.get("status") != "scored"
                or record.get("scoring_status") != "scored" or not isinstance(score.get("grade_rule"), Mapping)):
            continue
        traces = _rows(score.get("evidence"))
        if not traces or not any(trace.get("video_id") == video and trace.get("event_id") == event
            and trace.get("person_track_id") == record.get("person_track_id") for trace in traces):
            continue
        try:
            recomputed = score_indicator(indicator_id=identifier,
                features=list(record.get("scoring_features", record.get("features", []))),
                calibration=score["grade_rule"], model_versions=dict(_obj(score.get("model_versions"))),
                evidence=list(traces), event_quality_flags=_obj(record.get("quality_gate")).get("input_quality_flags", []),
                feasibility_level=record.get("feasibility_level"))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        if (recomputed["status"] != "scored" or recomputed["grade"] != score.get("grade")
                or recomputed["grade"] != record.get("grade")):
            continue
        key = identifier, event
        if key in conflicts:
            continue
        value = {"event_id": event, "grade": recomputed["grade"], "evidence": list(traces),
                 "rule_version": recomputed["threshold_version"], "rule": dict(score["grade_rule"])}
        if key in unique and unique[key] != value:
            unique.pop(key)
            conflicts.add(key)
        else:
            unique[key] = value
    result = defaultdict(list)
    for (identifier, _), value in unique.items():
        result[identifier].append(value)
    return result


def build_indicator_assessment(summary: Mapping[str, Any], indicator_feature_records: Iterable[Mapping[str, Any]] = (),
                               *, source_assessment: Mapping[str, Any] | None = None,
                               process_evaluation: Mapping[str, Any] | None = None) -> dict:
    """Return every Word card with a reason, provenance and linked observations.

    ``process_evaluation`` is accepted for response-composition compatibility;
    its trial numeric scores are deliberately never reused as source grades.
    """
    catalog = _catalog()
    known = {rule["indicator_id"]: rule for rule in catalog["rules"]}
    records = [r for r in indicator_feature_records if isinstance(r, Mapping)]
    evaluated_grades = _evaluated_event_grades(records, summary, known)
    episodes = _episodes(summary)
    observations = motion_joint_observations(episodes) + _source_observations(_obj(source_assessment))
    legacy, observed_events = _legacy_observations(records, summary, known)
    observations.extend(legacy)
    # Conflicting duplicate observation IDs cannot overwrite one another.
    unique, conflicts = {}, set()
    for row in observations:
        key = row["observation_id"]
        if key in conflicts:
            continue
        if key in unique and unique[key] != row:
            unique.pop(key)
            conflicts.add(key)
        else:
            unique[key] = row
    observations = list(unique.values())
    direct, contextual = defaultdict(list), defaultdict(list)
    for row in observations:
        if row.get("indicator_id") in known:
            direct[row["indicator_id"]].append(row)
            observed_events.add(known[row["indicator_id"]]["event_code"])
        elif (code := row.get("candidate_event_code")) is not None:
            contextual[code].append(row)
    baseline_present = any(e.get("family") == "baseline" for e in episodes)
    indicators = []
    for identifier, rule in known.items():
        linked = direct[identifier][:]
        blockers = [{"code": b["code"], "message_zh": b["message"]} for b in rule.get("blockers", [])]
        unsupported_type = rule["event_code"] in UNSUPPORTED_AUTOMATIC_EVENT_CODES
        unsupported_message = (
            f"当前算法尚未支持该动作类型识别（{rule['event_code']}）；"
            "无法自动绑定该类型及正式阶段，不能据此判定视频中没有该动作。"
        ) if unsupported_type else None
        source_conflict = any(b["code"] == "source_rule_conflict" for b in blockers)
        boundary_missing = any(b["code"] == "source_boundaries_missing" for b in blockers)
        # A rule-based forehand label only selects relevant contextual evidence;
        # its three generic motion phases never satisfy the GS stage contract.
        requested = _related_measurements(rule)
        if not source_conflict:
            linked.extend(o for o in contextual[rule["event_code"]] if o["feature_name"] in requested)
        source_rows = [o for o in linked if o["source"] == "source_aligned"]
        legacy_rows = [o for o in linked if o["source"] == "legacy_feature"]
        context_rows = [o for o in linked if o["scope"] == "contextual_motion_phase"]
        if source_conflict:
            status = "source_rule_conflict"
            explanation = "名称、定义或相邻标题存在原文冲突，保留来源并停止自动技术结论。"
        elif boundary_missing:
            status = "source_boundaries_missing"
            explanation = "原文未给该阶段完整起止定义；相关局部测量可参考，不能补写成已完成正式阶段。"
        elif source_rows:
            status = "measured_partial_rule"
            explanation = f"已取得 {len(source_rows)} 项次原文对应的局部测量；全部动作条件和等级尚未验证。"
        elif legacy_rows:
            status = "legacy_proxy_measured"
            explanation = f"已有 {len(legacy_rows)} 项次历史事件代理测量；原文要求的完整时序、真实接触或数值等级尚未满足。"
        elif context_rows:
            status = "contextual_measurement"
            explanation = "同类挥拍中已有相关二维测量；动作类型为算法候选，三段运动估计不等同于本卡的正式阶段。"
        elif rule["event_code"] in observed_events:
            status = "evidence_insufficient"
            explanation = "已找到相关动作候选，但本项所需的有效关节、连续窗口或专用证据不足。"
        elif rule["event_code"].startswith("GS") and baseline_present:
            status = "event_type_unresolved"
            explanation = "检测到挥拍运动，但尚无与本技术类型及正式阶段可靠对应的本项测量。"
        else:
            status = "not_observed"
            explanation = "本次未定位到可绑定本项的动作窗口；这不是动作不合格或 E 级。"
        if unsupported_type:
            blockers.append({"code": "event_type_recognizer_not_implemented", "message_zh": unsupported_message})
            # Preserve existing wire states/counts. The explicit reason and
            # display label distinguish software support from run evidence,
            # while preserving known candidates and their missing evidence.
            if status in {"event_type_unresolved", "not_observed"}:
                explanation = unsupported_message
            else:
                explanation += unsupported_message
        blockers.extend(_measurement_blockers(rule))
        if context_rows:
            blockers.append({"code": "formal_stage_not_confirmed", "message_zh": "原文十阶段尚未逐一确认，相关测量只对应实际估计运动段。"})
        grades = evaluated_grades[identifier]
        if not grades:
            blockers.append({"code": "complete_grade_verifier_not_implemented", "message_zh": COMPLETE_GRADE_IMPLEMENTATION_MESSAGE})
            blockers.append({"code": "qualitative_rubric_uncalibrated", "message_zh": "原文 A～E 为定性标准，尚无可执行数值分界；已有测量与二元条件直接计算，不以人工真值或审批作为计算前提。"})
        indicators.append({"indicator_id": identifier, "name_zh": rule["name_zh"], "event_code": rule["event_code"],
            "stage_code": rule["stage_code"], "status": status,
            "status_label_zh": "算法尚未支持该类型" if unsupported_type and status in {"event_type_unresolved", "not_observed"} else STATUS_LABELS[status],
            "explanation_zh": explanation, "calculation_zh": rule["calculation"],
            "observation_ids": [o["observation_id"] for o in linked], "blockers": blockers,
            "source": {"document_id": rule["source_document_id"], "sha256": rule["source_document_sha256"],
                       "table": rule["source_table"], "rubric_sha256": rule["rubric_sha256"]},
            "grade": {"status": "proxy_scored", "label_zh": "已计算数值代理等级", "events": grades,
                      "grade_counts": dict(Counter(row["grade"] for row in grades)),
                      "semantics": "explicit_numeric_proxy_not_complete_source_grade",
                      "scope": "supplied_mathematical_rule_not_automatic_full_word_rubric_verification",
                      "reason_zh": "已从原始特征独立重算数值代理规则；该等级不证明原卡全部动作、阶段与可见性条件，不计入原卡完整判级。"} if grades else
                     {"status": "source_rule_conflict" if source_conflict else "calibration_required",
                      "label_zh": "待核对原文" if source_conflict else "暂不生成等级",
                      "reason_zh": ("原文冲突需先核对；" if source_conflict else "")
                                   + COMPLETE_GRADE_IMPLEMENTATION_MESSAGE + "缺测不等于 E 级或零分。"}})
    counts = {"total": len(indicators), "statuses": dict(Counter(i["status"] for i in indicators)),
              "source_aligned_measured": len({o["indicator_id"] for o in observations if o["source"] == "source_aligned"}),
              "legacy_measured": len({o["indicator_id"] for o in observations if o["source"] == "legacy_feature"}),
              "contextual_measured": sum(any(unique[key]["scope"] == "contextual_motion_phase" for key in i["observation_ids"]) for i in indicators),
              "observations": len(observations),
              "formal_graded": 0,
              "proxy_graded": sum(i["grade"]["status"] == "proxy_scored" for i in indicators),
              "graded_event_instances": sum(len(rows) for rows in evaluated_grades.values()),
              "motion_joint_measurement_types": len({o["feature_name"] for o in observations if o["source"] == "motion_joint_measurement"}),
              "arithmetic_conditions": sum(o.get("condition") is not None for o in observations)}
    selected = _select_observations(observations)
    selected_ids = {o["observation_id"] for o in selected}
    for indicator in indicators:
        indicator["observation_count"] = len(indicator["observation_ids"])
        indicator["observation_ids"] = [key for key in indicator["observation_ids"] if key in selected_ids]
        indicator["returned_observation_count"] = len(indicator["observation_ids"])
        indicator["observations_truncated"] = indicator["observation_count"] > indicator["returned_observation_count"]
    counts["returned_observations"] = len(selected)
    return {"version": VERSION, "source_binding": {"reference_version": catalog["source_reference_version"],
                "source_documents": [d for d in catalog["source_documents"] if d["id"] in {"CARD-FS", "CARD-GS"}]},
            "counts": counts, "indicators": indicators, "observations": selected,
            "full_observation_count": len(observations), "returned_observation_count": len(selected),
            "is_truncated": len(selected) < len(observations),
            "selection_policy": "uniform_time_order_per_measurement_series_including_endpoints_max_12",
            "observation_selection": {"strategy": "uniform_time_order_per_measurement_series_including_endpoints",
                "max_observations_per_series": MAX_OBSERVATIONS_PER_SERIES,
                "total_count": len(observations), "returned_count": len(selected), "is_truncated": len(selected) < len(observations),
                "count_scope": "validated_input_observations_before_display_sampling",
                "source_input_already_truncated": any(i.get("is_truncated") is True for i in _rows(_obj(source_assessment).get("indicators"))),
                "reason_zh": "每项测量按时间均匀展示代表窗口并保留首尾；不按数值高低挑选。完整已接收计数保留，完整时序仍在原始分析产物中。"},
            "summary_zh": f"已逐项核对 {len(indicators)} 项 Word 标准，保留 {len(observations)} 项次真实测量与各项不能判断的具体原因。",
            "limitations_zh": ["相关测量数量不等于已通过或完整可评分的指标数量；同一测量可支持多张卡，但不重复当成独立证据。",
                               "有值仅说明观察到该数值；二维方向变化、覆盖率和模型置信度都不是技术准确率。",
                               "普通视频无法保证看见真实视线、足底受力、拍面角度或触球；缺少这些证据时不填常量，也不自动判为 E。"]}
