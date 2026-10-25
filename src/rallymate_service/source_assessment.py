"""Bounded public projection of the source-aligned measurement artifact."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from rallymate_service.artifact_jsonl import EVENT_FILE_MAX_BYTES, read_event_records

from rallymate_scoring.source_assessment import (
    ARTIFACT_NAME, ARTIFACT_VERSION, CONTEXTUAL_VERSION, CONTEXTUAL_TYPE, file_sha, source_binding,
)
from rallymate_features.source_alignment import SOURCE_LOCATORS, SOURCE_REQUIRED_JOINTS
from rallymate_scoring.indicator_assessment import (
    SOURCE_MEASUREMENT_JOINTS, source_model_requested, source_model_geometry, source_model_track, source_model_masks_match, source_model_evidence, source_sha256_equal,
)
from rallymate_scoring.model_measurement_contract import validated_model_window, validated_geometry_mask, SOURCE_BINDING_FIELDS

VERSION = "source-aligned-assessment-v1.0.0"
SEMANTICS = "source_aligned_measurement_not_technical_grade"
MAX_BYTES = 64 * 1024 * 1024
MAX_WINDOWS_PER_INDICATOR = 80
IDS = ("FS01-M02", "FS01-M04", "FS01-M05", "FS09-M05", "FS02-M01", "FS01-M01")
FEATURES = {
    "FS01-M02": {"preload_hip_height_drop_body": "body", "left_preload_knee_flexion_change_deg": "deg",
                 "right_preload_knee_flexion_change_deg": "deg", "preload_body_speed_change_rate_body_s2": "body/s2",
                 "preload_hip_reference_dy_px": "px", "preload_hip_reference_dy_torso": "torso"},
    "FS01-M04": {"post_slowdown_ankle_width_to_hip_width_ratio": "ratio"},
    "FS01-M05": {"landing_proxy_to_next_fs02_ms": "ms", "post_landing_body_speed_std_body_s": "body/s"},
    "FS09-M05": {"stable_control_proxy_to_next_fs10_or_fs02_ms": "ms", "stable_control_body_speed_std_body_s": "body/s"},
    "FS02-M01": {"launch_direction_deg": "deg"},
    "FS01-M01": {"head_center_mean_speed_frame_long_edge_s": "frame_long_edge/s", "head_axis_circular_std_deg": "deg"},
}
TRANSITIONS = {"landing_proxy_to_next_fs02_ms": ("landing_proxy_ms", {"FS02"}),
               "stable_control_proxy_to_next_fs10_or_fs02_ms": ("stable_control_onset_ms", {"FS02", "FS10"})}
REQUIREMENTS = {
    "FS01-M02": "观察髋部下沉幅度、膝部弯曲变化，以及身体移动速度是否连续。",
    "FS01-M04": "比较落地后双脚间距与髋宽；测量时段根据脚部减速位置估计，不能代替真实落地判定。",
    "FS01-M05": "观察落地后的身体稳定情况，以及同一人衔接第一步启动的过程。",
    "FS09-M05": "观察减速后的身体稳定情况，以及同一人衔接回位或再次启动的过程。",
    "FS02-M01": "通过髋部移动轨迹估计画面中的启动方向。缺少来球轨迹时，不能判断启动方向是否合适。",
    "FS01-M01": "观察准备时段的头部移动速度和朝向变化。这些测量不能确定实际视线、是否盯球或启动时机是否合适。",
}
REASONS = {
    "source_required_joint_coverage_below_70_percent": "本指标所需关节在该时段同时有效的帧不足 70%。",
    "required_joint_coverage_below_70_percent": "计算该测量值所需的关节有效帧不足 70%。",
    "source_window_not_fully_recorded": "所需测量窗口没有完整保存在本次分析中。",
    "insufficient_window_samples": "这个阶段可用的连续样本不足。",
    "source_time_or_frame_gap": "测量窗口存在跳帧或时间间断，不能把间隔视为连续动作。",
    "camera_reference_unverified": "尚未验证测量窗口的相机参照。",
    "camera_reference_unavailable": "测量窗口有相机校正证据不足的画面。",
    "camera_reference_discontinuity": "测量窗口跨越不同的相机参照，不能直接计算连续变化。",
    "subject_or_video_identity_unverified": "无法确认该窗口与视频中的同一人物对应。",
    "subject_identity_continuity_unverified": "该窗口缺少连续的人物身份依据。",
    "subject_track_switch_or_continuity_unverified": "该窗口存在人物轨迹切换，连续性尚未确认。",
    "measurement_window_subject_continuity_unverified": "测量时段内有未选定、身份不明或人物轨迹不一致的帧，不能作为同一人的连续测量。",
    "confirmed_subject_identity_switch": "该事件存在已确认的人物身份切换，不能把前后姿态作为同一人的连续测量。",
    "source_event_measurement_quality_hard_fail": "该事件未满足测量所需的证据条件，暂不能形成可靠测量。",
    "source_event_quality_evidence_invalid": "该事件的质量依据记录不完整，无法确认测量条件。",
    "successor_source_track_identity_unverified": "前后动作的源人物轨迹尚未确认一致。",
    "hip_width_unobservable_or_degenerate": "髋宽无法可靠测量，不能计算踝距与髋宽之比。",
    "body_scale_unavailable": "人体归一化尺度不可用。",
    "preload_phase_order_invalid": "原阶段候选顺序不成立，未把起跳后的变化当作预加载。",
    "reference_translation_window_below_80_ms": "候选窗口的源时间跨度不足 80 毫秒，未达到连续位移测量所需时长。",
    "decoder_pts_provenance_unavailable_for_reference_translation": "缺少可逐帧核验的源时间戳，不能可靠计算该候选窗口的位移。",
    "stable_reference_coordinate_contract_unverified": "稳定相机参考坐标尚未核验，不能计算该候选窗口的位移。",
    "stable_reference_pixel_geometry_unavailable": "缺少稳定参考的图像尺寸或校正矩阵。",
    "stable_reference_transform_unverified": "候选窗口的相机参考校正尚未逐帧核验。",
    "reference_translation_required_joints_not_continuous": "候选窗口内双肩和双髋未持续满足测量条件。",
    "reference_translation_torso_scale_below_12_px": "候选窗口的躯干投影过小，不能可靠形成固定归一化尺度。",
    "successor_not_observed": "没有找到可关联的同人后续动作，间隔不记为零。",
    "no_observed_successor": "没有找到可关联的同人后续动作，间隔不记为零。",
}


def _number(value) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _reason(value, measured: bool) -> str:
    if measured:
        return "已从该时段计算；完整动作、阶段和等级条件尚未核验。"
    if isinstance(value, str):
        if value in REASONS:
            return REASONS[value]
        if value.startswith("phase_"):
            return "相关阶段候选缺失、被截断或顺序不成立，暂不能计算这项变化。"
        if "successor" in value:
            return "同人后续动作或衔接过程的证据不足，不能确认衔接。"
        if "coverage" in value:
            return "该项实际测量所需的关节有效帧不足。"
        if "identity" in value:
            return "相关时段的连续人物身份未能确认。"
        if "preload" in value:
            return "预加载下降过程或起止转折尚不能可靠定位。"
    return "该时段缺少满足本项计算条件的连续证据。"


def _empty(reason: str) -> dict:
    return {"version": VERSION, "status": "unavailable", "score_semantics": SEMANTICS,
            "technical_grade": None, "technical_score_0_to_100": None, "indicators": [],
            "contextual_measurements": [], "reason_zh": reason}


def _signature(path: Path) -> tuple:
    stat = path.stat()
    signature = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                 stat.st_dev, stat.st_ino)
    digest = file_sha(path)
    after = path.stat()
    if (str(path.resolve()), after.st_size, after.st_mtime_ns, after.st_ctime_ns,
            after.st_dev, after.st_ino) != signature:
        raise OSError("source assessment dependency changed while reading")
    # Windows ctime is creation time; same-size rewrites can preserve all stat
    # fields. Content identity must reach this inner cache as well as the API.
    return (*signature, digest)


def _reject_constant(value):
    raise ValueError("Non-finite source artifact")


def _bound_measurement(measurement, indicator, event, item, by_id, duration_ms, *, model=False) -> bool:
    name, window = measurement.get("feature_name"), measurement.get("window_ms")
    if (name not in FEATURES[indicator] or measurement.get("unit") != FEATURES[indicator][name]
            or measurement.get("source_ref") != SOURCE_LOCATORS[indicator][0]
            or not isinstance(window, dict) or not _number(window.get("start_ms")) or not _number(window.get("end_ms"))
            or not 0 <= window["start_ms"] <= window["end_ms"] <= duration_ms):
        return False
    if name not in TRANSITIONS:
        if indicator == "FS01-M01":
            coverage,evidence=measurement.get("coverage",{}),measurement.get("evidence",{})
            if not isinstance(coverage, dict) or not isinstance(evidence, dict):
                return False
            count,valid=coverage.get("total_frame_count"),coverage.get("valid_frame_count")
            stop=event.get("key_phases_ms",{}).get("takeoff_proxy_ms")
            frames=measurement.get("source_frames")
            core=item.get("source_rule_gate",{}).get("pose_coverage",{})
            core_total,core_valid=core.get("total_frame_count"),core.get("valid_frame_count")
            if model:
                return (window == {"start_ms": event["start_ms"], "end_ms": stop} and _number(stop)
                    and event["start_ms"] < stop <= event["end_ms"] and isinstance(frames, list) and len(frames) >= 3
                    and all(evidence.get(key) is False for key in ("gaze_evaluated", "incoming_ball_alignment_evaluated", "technical_stability_evaluated")))
            return (window=={"start_ms":event["start_ms"],"end_ms":stop} and _number(stop) and event["start_ms"]<stop<=event["end_ms"]
                    and isinstance(count,int) and not isinstance(count,bool) and count>=3
                    and isinstance(valid,int) and not isinstance(valid,bool) and 0<=valid<=count and valid*10>=count*7
                    and isinstance(core_total,int) and not isinstance(core_total,bool) and core_total>0
                    and isinstance(core_valid,int) and not isinstance(core_valid,bool) and 0<=core_valid<=core_total and core_valid*10>=core_total*7
                    and set(coverage.get("required_joint_names",[]))=={"nose","left_ear","right_ear"}
                    and isinstance(frames,list) and len(frames)==count
                    and all(isinstance(f,int) and not isinstance(f,bool) and f>=0 for f in frames)
                    and all(b==a+1 for a,b in zip(frames,frames[1:]))
                    and all(evidence.get(key) is False for key in ("gaze_evaluated","incoming_ball_alignment_evaluated","technical_stability_evaluated")))
        if indicator == "FS02-M01":
            coverage, evidence = measurement.get("coverage", {}), measurement.get("evidence", {})
            if not isinstance(coverage, dict) or not isinstance(evidence, dict):
                return False
            frames = measurement.get("source_frames")
            count, valid = coverage.get("total_frame_count"), coverage.get("valid_frame_count")
            endpoints = evidence.get("endpoint_source_frames")
            if model:
                return (window == {"start_ms": event["start_ms"], "end_ms": event["end_ms"]}
                    and isinstance(frames, list) and len(frames) >= 2 and isinstance(endpoints, list)
                    and len(endpoints) >= 2 and all(f in frames for f in endpoints)
                    and evidence.get("direction_correctness_evaluated") is False
                    and evidence.get("source_clause_complete") is False
                    and isinstance(evidence.get("formula"), str) and bool(evidence["formula"]))
            return (window == {"start_ms": event["start_ms"], "end_ms": event["end_ms"]}
                    and isinstance(count, int) and not isinstance(count, bool) and count >= 2
                    and isinstance(valid, int) and not isinstance(valid, bool) and 0 <= valid <= count and valid * 10 >= count * 7
                    and set(coverage.get("required_joint_names", [])) == {"nose", "left_ear", "right_ear", "left_hip", "right_hip"}
                    and isinstance(frames, list) and len(frames) == count
                    and all(isinstance(f, int) and not isinstance(f, bool) and f >= 0 for f in frames)
                    and all(b == a + 1 for a,b in zip(frames,frames[1:]))
                    and isinstance(endpoints, list) and len(endpoints) >= 2 and all(f in frames for f in endpoints)
                    and evidence.get("direction_correctness_evaluated") is False
                    and evidence.get("source_clause_complete") is False
                    and isinstance(evidence.get("formula"), str) and bool(evidence["formula"]))
        return event["start_ms"] <= window["start_ms"] < window["end_ms"] <= event["end_ms"]
    phase, codes = TRANSITIONS[name]
    association = item.get("event_association")
    if not isinstance(association, dict):
        return False
    successor = by_id.get(association.get("successor_event_id"))
    anchor = event.get("key_phases_ms", {}).get(phase)
    return bool(successor and _number(anchor) and event["start_ms"] <= anchor <= event["end_ms"]
        and successor.get("video_id") == event.get("video_id") and successor.get("person_track_id") == event.get("person_track_id")
        and successor.get("event_code") in codes and association.get("source_event_id") == event["event_id"]
        and association.get("anchor_phase_key") == phase and association.get("anchor_ms") == anchor
        and association.get("successor_start_ms") == successor.get("start_ms")
        and association.get("successor_event_code") == successor.get("event_code")
        and association.get("link_measurement_status") == "measured"
        and window["start_ms"] == anchor and window["end_ms"] == successor.get("start_ms")
        and measurement.get("value") == window["end_ms"] - anchor)


def _model_parent(event, record, video_sha256):
    track = source_model_track(event)
    parent = validated_model_window(event, person_track_id=track) if track is not None else None
    child = validated_model_window(record, person_track_id=track, parent_window=parent) if parent else None
    event_masks = validated_geometry_mask(event.get('geometry_mask'), parent) if parent else None
    record_masks = validated_geometry_mask(record.get('geometry_mask'), child) if child else None
    if (not parent or child != parent or not source_sha256_equal(parent['video_sha256'], video_sha256)
            or parent['start_ms'] != event['start_ms'] or parent['end_ms'] != event['end_ms']
            or event_masks is None or record_masks is None
            or any(event_masks.get(name) != mask for name, mask in record_masks.items())):
        return None
    return parent


def _model_metric(measurement, indicator, event, item, by_id, parent, video_sha256):
    if parent is None:
        return None
    name = measurement['feature_name']
    track = source_model_track(event)
    geometry = source_model_geometry(measurement, SOURCE_MEASUREMENT_JOINTS[name], person_track_id=track,
        video_sha256=video_sha256, parent_window=None if name in TRANSITIONS else parent)
    core = source_model_geometry(event, SOURCE_REQUIRED_JOINTS[indicator], person_track_id=track, video_sha256=video_sha256)
    window = measurement.get('source_window', {})
    coverage = measurement.get('coverage', {})
    if (not geometry or not geometry['passes'] or not core or not core['passes']
            or not source_model_masks_match(measurement, event)
            or measurement.get('source_frames') != window.get('source_frames')
            or measurement.get('window_ms') != {'start_ms': window.get('start_ms'), 'end_ms': window.get('end_ms')}
            or not isinstance(coverage, dict)
            or any(coverage.get(key) != geometry[key] for key in ('semantics', 'valid_frame_count', 'total_frame_count', 'fraction', 'passes'))
            or coverage.get('visibility_verified') is not False
            or 'source_visibility_70_percent_verified' not in coverage or coverage['source_visibility_70_percent_verified'] is not None):
        return None
    if name in TRANSITIONS:
        successor = by_id.get(item.get('event_association', {}).get('successor_event_id'), {})
        successor_parent = validated_model_window(successor, person_track_id=track)
        if source_model_track(successor) != track or successor_parent is None or not source_model_masks_match(measurement, successor):
            return None
        # The link spans two candidates; bind both endpoints and every overlapping
        # sample to their independently transported candidate windows.
        for reference, endpoint in ((parent, window['start_ms']), (successor_parent, window['end_ms'])):
            if (any(window[key] != reference[key] for key in ('video_sha256', 'person_track_id', 'selection_epoch', 'camera_reference_epoch'))
                    or endpoint not in reference['video_timestamps_ms']):
                return None
            positions = {frame: i for i, frame in enumerate(reference['source_frames'])}
            for i, frame in enumerate(window['source_frames']):
                j = positions.get(frame)
                if j is not None and (window['video_timestamps_ms'][i] != reference['video_timestamps_ms'][j]
                        or window['source_timestamps_ms'][i] != reference['source_timestamps_ms'][j]
                        or any(window['source_bindings'][i][key] != reference['source_bindings'][j][key] for key in SOURCE_BINDING_FIELDS)):
                    return None
    return geometry


_CONTEXTUAL_NEUTRAL = {
    "score": None, "grade": None, "formal_event_verified": False, "scoring_allowed": False,
    "assessment_status": "measurement_only", "coordinate_system": "stable_camera_reference_image_pixels",
    "sampling_protocol": "original_source_frames",
}
_CONTEXTUAL_ADAPTER = {
    "version": "native-sam-primary-epochs-v1.0.0",
    "provenance": "unique_same_frame_selected_track_SAM_request_and_pose_binding_and_camera",
    "source_files_changed": False,
}
_CONTEXTUAL_PRE_HELPER_REASONS = {
    "contextual_measurement_failed",
    "contextual_source_window_incomplete", "contextual_raw_source_identity_mismatch",
    "contextual_native_adapter_rejected", "contextual_native_adapter_version_mismatch",
    "contextual_source_loader_rejected", "contextual_source_binding_unavailable",
    "contextual_event_source_binding_mismatch",
}
_CONTEXTUAL_HELPER_REASONS = {
    "invalid_external_window", "original_source_sampling_not_declared", "invalid_video_time_array",
    "window_not_complete_consecutive_source_frames", "source_time_binding_unavailable",
    "source_time_binding_mismatch", "source_video_clock_origin_changed", "video_identity_changed",
    "subject_identity_changed", "camera_reference_changed", "source_timebase_changed",
    "nonadjacent_processed_indexes", "source_pts_not_increasing", "video_time_not_increasing",
    "primary_timeline_window_mismatch", "primary_identity_unverified", "primary_timeline_binding_mismatch",
    "primary_timeline_clock_mismatch", "coordinate_contract_unverified", "modified_sampling_or_geometry",
    "camera_reference_unverified", "camera_application_evidence_invalid", "frame_dimensions_unavailable",
    "frame_dimensions_invalid_or_changed", "model_source_binding_unavailable", "model_source_binding_mismatch",
    "hip_geometry_unavailable", "hip_geometry_shape_invalid", "hip_model_geometry_unavailable",
    "model_confidence_must_remain_unknown", "hip_confidence_invalid", "nonfinite_reference_geometry",
    "hip_outside_original_frame", "nonfinite_path_geometry", "zero_net_displacement", "nonfinite_ratio",
}
_CONTEXTUAL_BINDING_KEYS = {
    "frame_index", "processed_index", "video_sha256", "pts", "time_base_num", "time_base_den",
    "source_timestamp_ms", "video_timestamp_ms", "timestamp_source", "primary_player_id",
    "person_track_id", "selection_epoch", "camera_reference_epoch",
}


def _contextual_sha(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) else None


def _contextual_integer(value):
    return type(value) is int and value >= 0


def _contextual_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _contextual_window(event):
    """Independently check the complete transported parent, never a subwindow.

    This validates saved evidence, not the original detector/camera observations.
    Native PTS and rounded source/video exports remain separate clock fields.
    """
    window = event.get("source_window")
    provenance = event.get("measurement_provenance")
    expected_provenance = {"pose_source": "sam3d", "observation_semantics": "monocular_model_estimate",
        "assessment_status": "measurement_only", "scoring_allowed": False,
        "confidence_available": False, "visibility_verified": False}
    if (not isinstance(window, dict) or not isinstance(provenance, dict)
            or any(provenance.get(k) != v or isinstance(v, bool) and provenance.get(k) is not v
                   for k, v in expected_provenance.items())):
        return None
    start, end = window.get("start_ms"), window.get("end_ms")
    identity = ("person_track_id", "selection_epoch", "camera_reference_epoch")
    arrays = [window.get(k) for k in ("source_frames", "video_timestamps_ms", "source_timestamps_ms", "source_bindings")]
    sha = _contextual_sha(window.get("video_sha256"))
    if (not sha or not _contextual_integer(start) or not _contextual_integer(end) or end <= start
            or (start, end) != (event.get("start_ms"), event.get("end_ms"))
            or any(not _contextual_integer(window.get(k)) for k in identity)
            or any(not isinstance(a, list) for a in arrays)):
        return None
    frames, video_times, source_times, bindings = arrays
    if (not 2 <= len(frames) <= 20000 or any(len(a) != len(frames) for a in arrays)
            or not all(_contextual_integer(x) for x in frames + video_times)
            or not all(_number(x) and x >= 0 for x in source_times)
            or (video_times[0], video_times[-1]) != (start, end)
            or any(b != a + 1 for a, b in zip(frames, frames[1:]))
            or any(not 0 < b - a <= 160 for a, b in zip(video_times, video_times[1:]))
            or any(not 0 < b - a <= 161.01 for a, b in zip(source_times, source_times[1:]))):
        return None
    normalized, exact_times = [], []
    lower = upper = None
    for i, binding in enumerate(bindings):
        if (not isinstance(binding, dict) or _contextual_sha(binding.get("video_sha256")) != sha
                or any(not _contextual_integer(binding.get(k)) for k in (
                    "frame_index", "processed_index", "pts", "time_base_num", "time_base_den", *identity))
                or binding["frame_index"] != frames[i]
                or any(binding[k] != window[k] for k in identity)
                or binding["time_base_num"] == 0 or binding["time_base_den"] == 0
                or not _number(binding.get("timestamp_ms")) or binding["timestamp_ms"] < 0):
            return None
        if i and (binding["processed_index"] != bindings[i-1]["processed_index"] + 1
                  or any(binding[k] != bindings[0][k] for k in ("time_base_num", "time_base_den"))):
            return None
        exact = Fraction(binding["pts"] * binding["time_base_num"] * 1000, binding["time_base_den"])
        if (abs(Fraction(str(source_times[i])) - exact) > Fraction(1, 1000000)
                or abs(Fraction(str(binding["timestamp_ms"])) - exact) > Fraction(51, 100)
                or i and exact <= exact_times[-1]
                or "video_timestamp_ms" in binding and (
                    not _contextual_integer(binding["video_timestamp_ms"]) or binding["video_timestamp_ms"] != video_times[i])):
            return None
        offset = exact - video_times[i]
        lo, hi = offset - Fraction(51, 100), offset + Fraction(51, 100)
        lower, upper = lo if lower is None else max(lower, lo), hi if upper is None else min(upper, hi)
        if lower > upper:
            return None
        exact_times.append(exact)
        normalized.append({k: copy.deepcopy(binding[k]) for k in SOURCE_BINDING_FIELDS})
        normalized[-1]["video_sha256"] = sha
        if "video_timestamp_ms" in binding:
            normalized[-1]["video_timestamp_ms"] = binding["video_timestamp_ms"]
    return {"start_ms": start, "end_ms": end, "source_frames": list(frames),
        "video_timestamps_ms": list(video_times), "source_timestamps_ms": list(source_times),
        "source_bindings": normalized, "video_sha256": sha, **{k: window[k] for k in identity}}


def _contextual_bindings(value, window, primary):
    if not isinstance(value, list) or len(value) != len(window["source_frames"]):
        return None
    result = []
    for i, binding in enumerate(value):
        parent = window["source_bindings"][i]
        if (not isinstance(binding, dict) or set(binding) != _CONTEXTUAL_BINDING_KEYS
                or binding.get("timestamp_source") != "decoder_pts"
                or not _contextual_integer(binding.get("primary_player_id")) or binding["primary_player_id"] != primary
                or _contextual_sha(binding.get("video_sha256")) != window["video_sha256"]
                or any(not _contextual_integer(binding.get(k)) or binding[k] != parent[k] for k in (
                    "frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
                    "person_track_id", "selection_epoch", "camera_reference_epoch"))
                or not _contextual_integer(binding.get("video_timestamp_ms"))
                or binding["video_timestamp_ms"] != window["video_timestamps_ms"][i]
                or not _number(binding.get("source_timestamp_ms")) or binding["source_timestamp_ms"] != parent["timestamp_ms"]):
            return None
        result.append({**binding, "video_sha256": window["video_sha256"]})
    return result


def _project_contextual_measurement(item, event, record, artifact):
    """Whitelist an additive neutral measurement, independently of its producer."""
    if not isinstance(item, dict):
        return None
    provenance = event.get("provenance")
    sha = _contextual_sha(artifact.get("video_sha256"))
    if (item.get("type") != CONTEXTUAL_TYPE or item.get("version") != CONTEXTUAL_VERSION
            or item.get("helper_version") != "bounded-hip-path-ratio-v1.0.0"
            or not isinstance(provenance, dict) or not _contextual_integer(provenance.get("primary_player_id"))
            or not isinstance(provenance.get("source_id"), str) or not provenance["source_id"]
            or not isinstance(event.get("event_id"), str) or not event["event_id"]
            or event.get("event_code") not in {"FS01", "FS02", "FS09"}
            or not _contextual_integer(event.get("person_track_id"))
            or event["person_track_id"] != provenance["primary_player_id"]
            or record.get("event_code") != event["event_code"]
            or any(item.get(k) != event.get(k) for k in ("event_id", "event_code", "video_id", "person_track_id"))
            or not _contextual_integer(item.get("person_track_id"))
            or item.get("source_id") != provenance["source_id"] or artifact.get("source_id") != provenance["source_id"]
            or not sha or _contextual_sha(item.get("video_sha256")) != sha
            or item.get("parent_window_ms") != {"start_ms": event["start_ms"], "end_ms": event["end_ms"]}
            or not all(_number(v) for v in item["parent_window_ms"].values())
            or any(k not in item or item[k] != v or v in (None, False) and item[k] is not v
                   for k, v in _CONTEXTUAL_NEUTRAL.items())):
        return None
    adapter, inputs, bounded = (item.get(k) for k in ("adapter", "input_sha256", "window_input_sha256"))
    fields = {"frames": "frames_sha256", "primary_timeline": "primary_timeline_sha256", "events": "events_sha256"}
    if (not isinstance(adapter, dict) or set(adapter) != set(_CONTEXTUAL_ADAPTER)
            or any(adapter.get(k) != v or isinstance(v, bool) and adapter.get(k) is not v for k, v in _CONTEXTUAL_ADAPTER.items())
            or not isinstance(inputs, dict) or set(inputs) != set(fields)
            or any(not _contextual_sha(artifact.get(field)) or _contextual_sha(inputs.get(k)) != _contextual_sha(artifact[field]) for k, field in fields.items())
            or not isinstance(bounded, dict) or set(bounded) != {"records", "original_primary", "adapted_primary", "event"}
            or _contextual_sha(bounded.get("event")) != _contextual_digest(event)
            or any(v is not None and not _contextual_sha(v) for v in bounded.values())):
        return None
    status, reason = item.get("status"), item.get("reason")
    numbers = [item.get(k) for k in ("hip_path_length_px", "hip_net_displacement_px", "hip_path_over_net_ratio")]
    if any(k not in item for k in ("source_window", "source_frame_bounds", "source_reference", "source_bindings",
                                   "measurement_window_video_ms", "hip_path_length_px", "hip_net_displacement_px", "hip_path_over_net_ratio")):
        return None
    window = _contextual_window(event)
    normalized_bindings, reference = [], None
    optional_failure = status == "unavailable" and reason == "contextual_measurement_failed"
    if optional_failure and (
            any(value is not None for value in numbers) or item["source_bindings"] != []
            or item["source_reference"] is not None
            or any(bounded[k] is not None for k in ("records", "original_primary", "adapted_primary"))):
        return None
    if window is None:
        if (status != "unavailable" or reason not in {"contextual_event_source_window_unavailable", "contextual_measurement_failed"}
                or any(value is not None for value in numbers)
                or any(item[k] is not None for k in ("source_window", "source_frame_bounds", "source_reference", "measurement_window_video_ms"))
                or item["source_bindings"] != [] or any(bounded[k] is not None for k in ("records", "original_primary", "adapted_primary"))):
            return None
    else:
        transported = _contextual_window({**event, "source_window": item["source_window"]})
        bounds = {"start_source_frame": window["source_frames"][0], "end_source_frame": window["source_frames"][-1]}
        if (window["video_sha256"] != sha or transported != window or item["source_frame_bounds"] != bounds
                or not all(_contextual_integer(v) for v in item["source_frame_bounds"].values())
                or item["measurement_window_video_ms"] != item["parent_window_ms"]
                or not all(_number(v) for v in item["measurement_window_video_ms"].values())
                or not optional_failure and (
                    not _contextual_sha(bounded["records"]) or not _contextual_sha(bounded["original_primary"]))):
            return None
        if item["source_bindings"]:
            normalized_bindings = _contextual_bindings(item["source_bindings"], window, provenance["primary_player_id"])
            if normalized_bindings is None:
                return None
        elif item["source_bindings"] != []:
            return None
        reference = item["source_reference"]
        expected_reference = {"video_sha256": sha, "primary_player_id": provenance["primary_player_id"],
            **{k: window[k] for k in ("person_track_id", "selection_epoch", "camera_reference_epoch")}}
        if reference is not None:
            if (not isinstance(reference, dict) or set(reference) != set(expected_reference)
                    or _contextual_sha(reference.get("video_sha256")) != sha
                    or any(not _contextual_integer(reference.get(k)) or reference[k] != v for k, v in expected_reference.items() if k != "video_sha256")):
                return None
            reference = expected_reference
        if status == "measured" or reason == "zero_net_displacement":
            if (not normalized_bindings or reference is None or not _contextual_sha(bounded["adapted_primary"])
                    or not all(_number(v) and v >= 0 for v in numbers[:2])):
                return None
            length, net, ratio = numbers
            if status == "measured":
                if (reason != "bounded_image_plane_geometry_only" or net <= 0 or not _number(ratio) or ratio <= 0
                        or length < net and not math.isclose(length, net, rel_tol=1e-9, abs_tol=1e-12)
                        or not math.isclose(ratio, length / net, rel_tol=1e-9, abs_tol=1e-12)):
                    return None
            elif status != "unavailable" or net != 0 or ratio is not None:
                return None
        elif (status != "unavailable" or reason not in _CONTEXTUAL_PRE_HELPER_REASONS | _CONTEXTUAL_HELPER_REASONS
              or any(value is not None for value in numbers)):
            return None
        if status == "unavailable" and reason in _CONTEXTUAL_PRE_HELPER_REASONS and (normalized_bindings or reference is not None):
            return None
        if reason in _CONTEXTUAL_HELPER_REASONS and (not normalized_bindings or not _contextual_sha(bounded["adapted_primary"])):
            return None
    return {**{k: item[k] for k in ("type", "version", "helper_version", "event_id", "event_code", "video_id", "source_id",
                                  "person_track_id", "status", "reason", "hip_path_length_px", "hip_net_displacement_px", "hip_path_over_net_ratio")},
        "video_sha256": sha, "parent_window_ms": copy.deepcopy(item["parent_window_ms"]),
        "measurement_window_video_ms": copy.deepcopy(item["measurement_window_video_ms"]),
        "source_frame_bounds": copy.deepcopy(item["source_frame_bounds"]), "source_window": window,
        "source_bindings": normalized_bindings, "source_reference": reference,
        "adapter": dict(_CONTEXTUAL_ADAPTER), "input_sha256": {k: _contextual_sha(v) for k, v in inputs.items()},
        "window_input_sha256": {k: _contextual_sha(v) if v is not None else None for k, v in bounded.items()},
        **_CONTEXTUAL_NEUTRAL}


def project_source_assessment(artifact: dict, events: list[dict], *, video_id: str,
                              duration_ms: int, video_sha256: str | None = None) -> dict:
    if (artifact.get("version") != ARTIFACT_VERSION or artifact.get("video_id") != video_id
            or artifact.get("source_binding") != source_binding()
            or artifact.get("score_semantics") != SEMANTICS or artifact.get("technical_grade") is not None
            or artifact.get("technical_score_0_to_100") is not None
            or video_sha256 and artifact.get("video_sha256") != video_sha256.lower()):
        return _empty("来源测量与本次视频或版本不匹配，已停止展示。")
    by_id = {}
    for event in events:
        identifier = event.get("event_id")
        if identifier in by_id:
            return _empty("事件标识存在重复，无法可靠绑定来源测量。")
        by_id[identifier] = event
    grouped = {key: [] for key in IDS}
    contextual = {}
    contextual_duplicates = set()
    seen = set()
    for record in artifact.get("records", []):
        if not isinstance(record, dict) or record.get("video_id") != video_id:
            continue
        event = by_id.get(record.get("event_id"))
        if not event or event.get("video_id") != video_id or event.get("person_track_id") != record.get("person_track_id"):
            continue
        start, end = event.get("start_ms"), event.get("end_ms")
        if not all(_number(t) for t in (start, end)) or not 0 <= start < end <= duration_ms:
            continue
        context_items = record.get("contextual_measurements", [])
        if isinstance(context_items, list):
            for context_item in context_items:
                try:
                    projected = _project_contextual_measurement(context_item, event, record, artifact)
                except (ValueError, TypeError, KeyError, OverflowError, IndexError):
                    projected = None
                if projected is not None:
                    key = projected["event_id"]
                    if key in contextual:
                        contextual_duplicates.add(key)
                    contextual[key] = projected
        record_model = source_model_requested(event, record)
        parent = _model_parent(event, record, artifact.get('video_sha256')) if record_model else None
        for item in record.get("indicators", []):
            indicator = item.get("indicator_id")
            identity = (indicator, record.get("event_id"))
            if indicator not in IDS or identity in seen or indicator.split("-")[0] != event.get("event_code"):
                continue
            seen.add(identity)
            coverage = item.get("source_rule_gate", {}).get("pose_coverage", {})
            valid, total = coverage.get("valid_frame_count"), coverage.get("total_frame_count")
            valid_counts = (isinstance(valid, int) and not isinstance(valid, bool) and isinstance(total, int)
                            and not isinstance(total, bool) and 0 <= valid <= total and total > 0)
            ratio = valid / total if valid_counts else None
            joints = coverage.get("required_joint_names", [])
            if not isinstance(joints, list) or not all(isinstance(j, str) for j in joints):
                joints = []
            measurements = []
            for measurement in item.get("source_measurements", []):
                name, label = measurement.get("feature_name"), measurement.get("name_zh")
                if name not in FEATURES[indicator] or not isinstance(label, str):
                    continue
                window = measurement.get("window_ms")
                model = record_model or source_model_requested(measurement)
                geometry = _model_metric(measurement, indicator, event, item, by_id, parent, artifact.get('video_sha256')) if model else None
                valid_window = _bound_measurement(measurement, indicator, event, item, by_id, duration_ms, model=model)
                valid_window = valid_window and (not model or geometry is not None)
                measured = (measurement.get("status") == "measured" and measurement.get("valid") is True
                            and _number(measurement.get("value")) and valid_window)
                model_details = (source_model_evidence(measurement, source_model_track(event))
                                 | {'model_geometry_coverage': geometry}) if model and geometry else {}
                measurements.append({"feature_name": name, "label_zh": label,
                    "value": measurement["value"] if measured else None, "unit": measurement.get("unit", ""),
                    "status": "measured" if measured else "unavailable", "reason_zh": _reason(measurement.get("reason"), measured),
                    "source_requirement_zh": REQUIREMENTS[indicator], "source_ref": measurement.get("source_ref"),
                    "measurement_window": window if valid_window else None,
                    "source_frames": measurement.get("source_frames", []),
                    "definition": measurement.get("definition"), "coverage": None if model else measurement.get("coverage"),
                    "evidence": measurement.get("evidence", {}), **model_details})
            model_window = {}
            if record_model:
                # Do not rebrand geometric in-frame availability as observed
                # anatomy, even when every model coordinate is finite.
                ratio = None
                valid_counts = False
                model_window = {**source_model_evidence(event, source_model_track(event)),
                    'track_diagnostics': {'source_track_ids': [source_model_track(event)]} if source_model_track(event) is not None else {},
                    **({'measurement_provenance': {'pose_source': 'sam3d'}, 'source_window': None} if parent is None else {})}
            grouped[indicator].append({"event_id": event["event_id"], "person_track_id": event["person_track_id"],
                "start_ms": start, "end_ms": end, "measurements": measurements,
                "visibility": {"status": "sufficient" if ratio is not None and ratio >= .7 else "insufficient" if ratio is not None else "unavailable",
                    "valid_frame_ratio": ratio, "valid_frame_count": valid if valid_counts else None,
                    "total_frame_count": total if valid_counts else None, "required_joint_ids": joints,
                    "threshold_ratio": .7, "scope": "indicator_window",
                    "reason_zh": ("模型给出了关节位置，但无法确认所需关节是否有 70% 的画面真实可见，因此未据此判定技术等级。" if record_model else
                        "统计该时段所有已分析帧中，本指标所需人体关节同时有效的帧。达到 70% 仅通过可见性检查，不代表技术合格。"),
                    **({'visibility_verified': False, 'source_visibility_70_percent_verified': None} if record_model else {})},
                "limitations_zh": item.get("limitations_zh", []), "association": item.get("event_association"),
                "complete_source_rule_verified": False, **model_window})
    indicators = []
    for indicator, windows in grouped.items():
        measured = [w for w in windows if any(m["status"] == "measured" for m in w["measurements"])]
        missing = [w for w in windows if not any(m["status"] == "measured" for m in w["measurements"])]
        selected = windows if len(windows) <= MAX_WINDOWS_PER_INDICATOR else measured[:60] + missing[:20]
        if len(selected) < min(len(windows), MAX_WINDOWS_PER_INDICATOR):
            ids = {w["event_id"] for w in selected}
            selected += [w for w in windows if w["event_id"] not in ids][:MAX_WINDOWS_PER_INDICATOR - len(selected)]
        indicators.append({"indicator_id": indicator, "window_count": len(windows), "measured_window_count": len(measured),
            "unavailable_window_count": len(missing), "returned_window_count": len(selected), "is_truncated": len(selected) < len(windows),
            "selection_policy": "up_to_60_measurable_and_20_unavailable_windows_then_fill_chronologically",
            "windows": sorted(selected, key=lambda w: (w["start_ms"], w["event_id"]))})
    has_windows = any(item["window_count"] for item in indicators)
    source_id = artifact.get("source_id")
    return {"version": VERSION, "status": "available" if has_windows else "unavailable", "score_semantics": SEMANTICS,
            "video_id": video_id, "video_sha256": artifact.get('video_sha256'),
            "source_id": source_id if isinstance(source_id, str) and 0 < len(source_id) <= 256 and source_id.strip() else None,
            "technical_grade": None, "technical_score_0_to_100": None, "indicators": indicators,
            "contextual_measurements": [value for key, value in contextual.items() if key not in contextual_duplicates],
            "measurement_artifact_version": ARTIFACT_VERSION,
            "source_binding": source_binding(),
            "reason_zh": ("已测量这一动作的部分条件；完整动作条件尚未验证，因此不将这些局部测量直接换算成等级或分数。" if has_windows else
                          "没有可绑定到本次视频的相应步伐候选；可人工选择时段核验，未定位到候选不代表没有动作。")}


@lru_cache(maxsize=16)
def _load(signature, events_signature, primary_signature, frames_signature, video_id, duration_ms, video_sha256, frames_sha256):
    path, event_path, primary_path = Path(signature[0]), Path(events_signature[0]), Path(primary_signature[0])
    if signature[1] > MAX_BYTES or events_signature[1] > EVENT_FILE_MAX_BYTES:
        return _empty("来源测量超出复核读取上限。")
    artifact = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    if (artifact.get("events_sha256") != file_sha(event_path)
            or artifact.get("primary_timeline_sha256") != file_sha(primary_path)
            or artifact.get("frames_sha256") != file_sha(Path(frames_signature[0]))
            or frames_sha256 and artifact.get("frames_sha256") != frames_sha256.lower()):
        return _empty("来源测量引用的事件、人物或帧记录已改变，需要重新生成。")
    events = read_event_records(event_path, parse_constant=_reject_constant)
    if any(_signature(Path(saved[0])) != saved for saved in (signature, events_signature, primary_signature, frames_signature)):
        return _empty("读取期间来源产物发生变化，请重新加载。")
    return project_source_assessment(artifact, events, video_id=video_id, duration_ms=duration_ms, video_sha256=video_sha256)


def load_source_aligned_assessment(output_dir: Path, *, video_id: str, duration_ms: int,
                                   video_sha256: str | None = None, frames_sha256: str | None = None) -> dict:
    try:
        if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms <= 0:
            return _empty("本次视频缺少有效时长，不能绑定测量窗口。")
        return copy.deepcopy(_load(_signature(output_dir / ARTIFACT_NAME), _signature(output_dir / "events.jsonl"),
                                  _signature(output_dir / "primary-player.jsonl"), _signature(output_dir / "frames.jsonl"),
                                  video_id, duration_ms, video_sha256, frames_sha256))
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return _empty("本次分析暂未生成可核验的逐项测量记录。")
