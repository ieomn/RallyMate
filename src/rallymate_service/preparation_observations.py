"""Project existing phase measurements without asserting a formal GS stage."""
from __future__ import annotations

import copy
import math
import statistics
from collections.abc import Mapping
from rallymate_scoring.indicator_assessment import motion_joint_observations

MOTION_SOURCE = "action_recognition.motion_analysis.episodes.process_motion"
ROTATION_SOURCE = "action_recognition.motion_analysis.episodes.rotation_analysis"
WRIST_FEATURES = {
    "net_displacement_torso": ("腕部净位移", "torso", "保留端点、内部三点中位滤波后，末点减起点的二维向量长度。"),
    "path_length_torso": ("腕部路径长度", "torso", "保留端点、内部三点中位滤波后，相邻腕位置距离之和。"),
    "mean_speed_torso_per_s": ("腕部平均速度", "torso/s", "腕部路径长度 ÷ 实测阶段用时（秒）。"),
    "start_speed_torso_per_s": ("腕部起始速度", "torso/s", "前两个相邻帧位移/各自真实时间差所得速度的均值。"),
    "end_speed_torso_per_s": ("腕部末端速度", "torso/s", "后两个相邻帧位移/各自真实时间差所得速度的均值。"),
}
ROTATION_FEATURES = {
    f"{axis}_line_change_deg": (f"{label}二维角度变化", "deg", axis)
    for axis, label in (("shoulder", "肩线"), ("hip", "髋线"))
} | {
    f"peak_{axis}_angular_speed_deg_s": (f"{label}峰值角速度", "deg/s", axis)
    for axis, label in (("shoulder", "肩线"), ("hip", "髋线"))
}
TRANSLATION_FEATURES = {
    "hip_center_dx_torso": "torso", "hip_center_dy_torso": "torso",
    "hip_center_mean_velocity_x_torso_per_s": "torso/s", "hip_center_mean_velocity_y_torso_per_s": "torso/s",
    "left_knee_flexion_change_deg": "deg", "right_knee_flexion_change_deg": "deg",
}
TRANSLATION_REFS = ["CARD-GS:word/document.xml:P0975", "CARD-GS:word/document.xml:P9325"]


def obj(value):
    return value if isinstance(value, Mapping) else {}


def rows(value):
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def interval(value):
    return integer(value.get("start_ms")) and integer(value.get("end_ms")) and value["start_ms"] < value["end_ms"]


def inside(window, outer):
    return interval(window) and interval(outer) and outer["start_ms"] <= window["start_ms"] < window["end_ms"] <= outer["end_ms"]


def series_window(episode, rotation, window, *, axis=None, model_windows=None):
    """Keep actual decoded frame/time identity; never infer frames from FPS."""
    reference = obj(obj(episode.get("evidence")).get("process_continuity"))
    if ("camera_reference_epoch" not in reference
            or not integer(episode.get("person_track_id")) or not integer(reference.get("selection_epoch"))
            or reference.get("camera_reference_epoch") is not None and not integer(reference["camera_reference_epoch"])
            or not isinstance(reference.get("camera_compensated"), bool)
            or reference.get("normalization_basis") not in {"full_torso", "shoulder_width", "hip_width",
                                                            "left_shoulder_left_torso", "right_shoulder_right_torso"}):
        return None
    temporal = obj(obj(episode.get("temporal_recognition")).get("input_summary"))
    if temporal and any(temporal.get(key) != value for key, value in {
        "person_track_id": episode["person_track_id"], "selection_epoch": reference["selection_epoch"],
        "camera_reference_epoch": reference["camera_reference_epoch"], "normalization_basis": reference["normalization_basis"],
    }.items()):
        return None
    selected = [row for row in rows(rotation.get("series")) if number(row.get("timestamp_ms"))
                and window["start_ms"] <= row["timestamp_ms"] <= window["end_ms"]]
    if (len(selected) < 2 or selected[0]["timestamp_ms"] != window["start_ms"]
            or selected[-1]["timestamp_ms"] != window["end_ms"]
            or any(not integer(row.get("timestamp_ms")) or not integer(row.get("frame_index"))
                   or not integer(row.get("person_track_id")) or row.get("person_track_id") != episode["person_track_id"]
                   or not integer(row.get("selection_epoch")) or row.get("selection_epoch") != reference["selection_epoch"] for row in selected)):
        return None
    times = [row["timestamp_ms"] for row in selected]
    frames = [row["frame_index"] for row in selected]
    dt = [b-a for a, b in zip(times, times[1:])]
    df = [b-a for a, b in zip(frames, frames[1:])]
    if min(dt) <= 0 or min(df) <= 0 or max(dt) > min(160, statistics.median(dt)*1.8) or max(df) > statistics.median(df)*1.8:
        return None
    if axis and (any(not number(row.get(f"{axis}_line_angle_deg")) or not integer(row.get(f"{axis}_segment_id")) for row in selected)
                 or len({row[f"{axis}_segment_id"] for row in selected}) != 1):
        return None
    binding = {"source_frames": frames, "source_timestamps_ms": times,
               "reference": {"person_track_id": episode["person_track_id"], **dict(reference)}}
    if model_windows is not None:
        # The lightweight rotation series lacks the decoder binding. It cannot
        # authorize SAM provenance: require an independently validated original
        # window with the exact same frame interval, then preserve both clocks.
        matching = [proof for proof in model_windows if proof["start_ms"] == window["start_ms"]
                    and proof["end_ms"] == window["end_ms"] and proof["source_frames"] == frames
                    and proof["reference"] == binding["reference"]]
        if not matching:
            return None
        proof = matching[0]
        if any(proof.get(key) is None for key in ("source_binding_start", "source_binding_end", "source_time_origin_ms")):
            return None
        binding.update({key: copy.deepcopy(proof[key]) for key in ("source_binding_start", "source_binding_end", "source_time_origin_ms")})
    return binding


def _row(episode, phase, window, binding, name, label, unit, value, source, formula, evidence, card, anchor):
    provenance = obj(obj(episode.get("process_motion")).get("measurement_provenance"))
    return {
        "id": f"preparation_candidate_{name}", "feature_name": name,
        "label_zh": f"准备候选 · {label}", "status": "measured", "value": value, "unit": unit, "score": None,
        "start_ms": window["start_ms"], "end_ms": window["end_ms"], "episode_id": episode["episode_id"],
        "person_track_id": episode["person_track_id"], "sample_count": len(binding["source_frames"]), **binding,
        "phase": "preparation", "phase_window": {"start_ms": phase["start_ms"], "end_ms": phase["end_ms"]},
        "window_scope": "continuous_window_in_estimated_preparation",
        "measurement_semantics": "preparation_candidate_2d_not_formal_stage_or_grade",
        "assessment_status": "measurement_only", "full_source_rule_satisfied": False,
        "feature_version": "preparation-candidate-projection-v1.0.0", "source": source,
        "reason_zh": ("单目模型估计，未提供关键点置信度或可见性。" if provenance else "") + "同一主体与机位参照的二维准备候选测量；阶段由运动曲线估计。尚未确认持拍手、正反手及原卡正式阶段，不判断技术达标。",
        "rule_zh": formula + "按原始阶段和证据取值，不换算技术分。",
        "related_source_cards": [card], "source_refs": [anchor],
        "source_card_applicability": "unconfirmed", "applicability_missing": ["independent_racket_hand_association", "formal_event_type", "formal_source_stage"],
        "original_metric_evidence": copy.deepcopy(evidence), "measurement_provenance": copy.deepcopy(provenance),
    }


def preparation_candidate_observations(episodes):
    output = []
    for episode in episodes:
        rotation, process = obj(episode.get("rotation_analysis")), obj(episode.get("process_motion"))
        continuity = obj(obj(episode.get("evidence")).get("process_continuity"))
        model_context = (obj(process.get("measurement_provenance")).get("observation_semantics") == "monocular_model_estimate"
                         or continuity.get("pose_source") == "sam3d"
                         or obj(obj(process.get("source_observations")).get("measurement_provenance")).get("observation_semantics") == "monocular_model_estimate")
        model_windows = ([window for observation in motion_joint_observations([episode])
                          if observation["phase"] == "preparation" and observation["measurement_provenance"].get("scoring_allowed") is False
                          for window in observation["windows"]] if model_context else None)
        if (rotation.get("analysis_version") != "image-plane-rotation-v1.1.0"
                or rotation.get("method") != "undirected_image_plane_body_axes"
                or rotation.get("is_3d_rotation") is not False
                or process.get("version") not in {"process-motion-v1.0.0", "process-motion-v1.1.0"}):
            continue
        phases = [p for p in rows(episode.get("phases")) if p.get("phase") == "preparation"]
        if len(phases) != 1 or phases[0].get("status") != "measured" or not inside(phases[0], episode):
            continue
        phase = phases[0]
        wrist_phases = [p for p in rows(process.get("phases")) if p.get("phase") == "preparation"]
        if len(wrist_phases) == 1:
            p = wrist_phases[0]
            binding = series_window(episode, rotation, p, model_windows=model_windows) if interval(p) else None
            if (p.get("status") == "measured" and binding and inside(p, phase)
                    and p.get("boundary_semantics") == "estimated_motion_phase" and p.get("anchor_is_contact") is False
                    and p.get("sample_count") == len(binding["source_frames"]) and p["sample_count"] >= 5
                    and process.get("coordinate_system") == "body_relative_image_x_right_y_down"
                    and process.get("coordinate_unit") == "torso_length"):
                wrist = obj(p.get("wrist"))
                for name, (label, unit, formula) in WRIST_FEATURES.items():
                    value = wrist.get(name)
                    if not number(value) or value < 0 or wrist.get("speed_sample_count") != p["sample_count"]-1:
                        continue
                    output.append(_row(episode, phase, p, binding, name, label, unit, value,
                        f"{MOTION_SOURCE}.phases.wrist.{name}", formula, p, "GS01-M03-02", "CARD-GS:word/document.xml:P0887"))
        rotation_phases = [p for p in rows(rotation.get("phase_measurements")) if p.get("phase") == "preparation"]
        if len(rotation_phases) != 1:
            continue
        p = rotation_phases[0]
        if (not inside(p, phase) or p.get("source_version") != "image-plane-rotation-v1.1.0"
                or p.get("boundary_semantics") != "estimated_motion_phase" or p.get("anchor_is_contact") is not False):
            continue
        for name, (label, unit, axis) in ROTATION_FEATURES.items():
            evidence = obj(obj(p.get("metric_evidence")).get(name))
            value = obj(p.get("metrics")).get(name)
            if (evidence.get("status") != "measured" or evidence.get("scope") != "motion_phase"
                    or evidence.get("phase") != "preparation" or evidence.get("axis") != axis
                    or evidence.get("source_version") != "image-plane-rotation-v1.1.0" or evidence.get("unit") != unit
                    or evidence.get("view_semantics") != "image_plane_proxy" or evidence.get("is_3d_rotation") is not False
                    or not number(value) or value < 0 or evidence.get("value") != value or not inside(evidence, p)):
                continue
            binding = series_window(episode, rotation, evidence, axis=axis, model_windows=model_windows)
            if (not binding or evidence.get("continuous_samples") != len(binding["source_frames"])
                    or len(binding["source_frames"]) < 7 or evidence["end_ms"]-evidence["start_ms"] < 200
                    or any(not number(evidence.get(k)) or not .8 <= evidence[k] <= 1 for k in ("coverage_fraction", "time_coverage_fraction"))):
                continue
            formula = ("轴角按180°周期展开、内部三点中位滤波后，最大值减最小值。" if unit == "deg"
                       else "轴角按180°周期展开、内部三点中位滤波后，中央差分角速度绝对值的最大值；时间使用实测毫秒。")
            output.append(_row(episode, phase, evidence, binding, name, label, unit, value,
                f"{ROTATION_SOURCE}.phase_measurements.metrics.{name}", formula, evidence,
                "GS01-M03-01", "CARD-GS:word/document.xml:P0843"))
    return output


def preparation_translation_observations(episodes):
    """Expose independently gated local hip/knee windows, including signed zero."""
    output = []
    for episode in episodes:
        process, rotation = obj(episode.get("process_motion")), obj(episode.get("rotation_analysis"))
        data = obj(process.get("preparation_translation"))
        phases = [p for p in rows(episode.get("phases")) if p.get("phase") == "preparation"]
        if (process.get("version") not in {"process-motion-v1.0.0", "process-motion-v1.1.0"}
                or rotation.get("analysis_version") != "image-plane-rotation-v1.1.0"
                or data.get("version") != "preparation-translation-v1.0.0" or data.get("scope") != "contextual_preparation_window"
                or data.get("score") is not None or data.get("formal_stage_verified") is not False
                or data.get("assessment_status") != "measurement_only"
                or data.get("coordinate_system") != "stable_camera_reference_image_x_right_y_down"
                or data.get("normalization") != "median_observed_torso_scale_fixed_per_continuous_window"
                or len(phases) != 1 or phases[0].get("status") != "measured" or not inside(phases[0], episode)):
            continue
        phase = phases[0]
        for metric in rows(data.get("measurements")):
            name = metric.get("feature_name")
            if (name not in TRANSLATION_FEATURES or metric.get("unit") != TRANSLATION_FEATURES[name]
                    or metric.get("status") not in {"measured", "partial"} or metric.get("source_refs") != TRANSLATION_REFS
                    or not isinstance(metric.get("definition"), str) or not metric["definition"]):
                continue
            last_end = -1
            for window in rows(metric.get("windows")):
                if not inside(window, phase) or window["start_ms"] <= last_end or not number(window.get("value")):
                    continue
                binding = series_window(episode, rotation, window)
                times = window.get("source_timestamps_ms")
                if (not binding or window.get("reference") != binding["reference"] or window.get("source_frames") != binding["source_frames"]
                        or window.get("video_timestamps_ms") != binding["source_timestamps_ms"]
                        or window.get("sample_count") != len(binding["source_frames"]) or window["sample_count"] < 3
                        or binding["reference"].get("camera_compensated") is not True
                        or not number(window.get("fixed_scale_px")) or window["fixed_scale_px"] < 12
                        or not isinstance(times, list) or len(times) != window["sample_count"] or not all(number(t) for t in times)
                        or any(not 0 < b-a <= 160 for a, b in zip(times, times[1:]))
                        or not number(window.get("source_duration_ms")) or window["source_duration_ms"] < 80
                        or abs(times[-1]-times[0]-window["source_duration_ms"]) > .000001):
                    continue
                provenance = obj(window.get("measurement_provenance"))
                process_provenance = obj(process.get("measurement_provenance"))
                model = (binding["reference"].get("pose_source") == "sam3d" or provenance.get("pose_source") == "sam3d"
                         or process_provenance.get("pose_source") == "sam3d")
                if model:
                    semantics = {"pose_source": "sam3d", "observation_semantics": "monocular_model_estimate",
                                 "assessment_status": "measurement_only", "scoring_allowed": False,
                                 "confidence_available": False, "visibility_verified": False}
                    if (window.get("time_basis") != "bound_source_pts"
                            or any(provenance.get(k) != v or process_provenance.get(k) != v for k, v in semantics.items())
                            or provenance.get("model_estimate_sample_count") != window["sample_count"]
                            or binding["reference"].get("pose_source") != "sam3d"
                            or binding["reference"].get("observation_semantics") != "monocular_model_estimate"
                            or any(abs((source-times[0])-(video-window["start_ms"])) > 1.01
                                   for source, video in zip(times, window["video_timestamps_ms"]))):
                        continue
                    valid = True
                    for edge, frame, timestamp in (("start", binding["source_frames"][0], times[0]), ("end", binding["source_frames"][-1], times[-1])):
                        raw = obj(window.get(f"source_binding_{edge}"))
                        expected = {"video_sha256": binding["reference"].get("video_sha256"), "person_track_id": episode["person_track_id"],
                                    "selection_epoch": binding["reference"].get("source_selection_epoch"),
                                    "camera_reference_epoch": binding["reference"].get("camera_reference_epoch"),
                                    "frame_index": frame, "video_timestamp_ms": window[f"{edge}_ms"]}
                        valid = valid and all(raw.get(k) == v for k, v in expected.items())
                        valid = valid and all(integer(raw.get(k)) for k in ("pts", "time_base_num", "time_base_den", "processed_index"))
                        valid = valid and raw.get("time_base_num", 0) > 0 and raw.get("time_base_den", 0) > 0
                        valid = valid and abs(raw["pts"]*raw["time_base_num"]/raw["time_base_den"]*1000-timestamp) <= .000001
                        valid = valid and number(raw.get("timestamp_ms")) and abs(raw["timestamp_ms"]-timestamp) <= .51
                    if not valid:
                        continue
                elif window.get("time_basis") != "decoder_timestamp_ms" or times != binding["source_timestamps_ms"]:
                    continue
                row = _row(episode, phase, window, binding, name, metric["label_zh"], metric["unit"], window["value"],
                           f"{MOTION_SOURCE}.preparation_translation.measurements.{name}", metric["definition"], window,
                           "GS01-M03-04", TRANSLATION_REFS[0])
                row.update(id=f"preparation_translation_{name}", feature_version=data["version"],
                           related_source_cards=["GS01-M03-04", "GS05-M01-03"], source_refs=TRANSLATION_REFS[:],
                           applicability_missing=["formal_source_stage", "independent_racket_hand_association", "opponent_ball_contact", "split_step_landing"],
                           video_timestamps_ms=list(window["video_timestamps_ms"]), source_timestamps_ms=list(times),
                           time_basis=window["time_basis"], fixed_scale_px=window["fixed_scale_px"],
                           source_duration_ms=window["source_duration_ms"], full_phase_coverage=metric.get("full_phase_coverage") is True)
                row["reason_zh"] = ("单目模型估计，未提供关键点置信度或可见性。" if model else "") + "固定窗身体尺度下的稳定图像局部测量，画面右/下为正；髋中点是视觉代理，不是真实承重。缺持拍侧关联及原卡正式起止，不能称加载持拍侧或对方出球至垫步落地。"
                if model:
                    for edge in ("start", "end"):
                        row[f"source_binding_{edge}"] = copy.deepcopy(window[f"source_binding_{edge}"])
                    row["source_time_origin_ms"] = times[0] - window["start_ms"]
                output.append(row)
                last_end = window["end_ms"]
    return output
