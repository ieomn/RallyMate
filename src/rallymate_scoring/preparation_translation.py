"""Local preparation geometry in a stable camera reference, never body recentering.

Candidate preparation windows do not certify Word GS stage boundaries. Values
retain source frames and explicit sign/units; no technical grade is inferred.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping

from .indicator_observations import _angle
from .pose_observations import MODEL_ESTIMATE, measurement_provenance, sample_source_reference
from .temporal_recognition import samples_are_continuous

VERSION = "preparation-translation-v1.0.0"
MIN_SAMPLES, MIN_DURATION_MS = 3, 80
SOURCE_REFS = ["CARD-GS:word/document.xml:P0975", "CARD-GS:word/document.xml:P9325"]


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _point(sample, name):
    point = getattr(sample, "reference_points", {}).get(name)
    if not isinstance(point, (tuple, list)) or len(point) != 2 or not all(_finite(x) for x in point):
        return None
    if getattr(sample, "observation_semantics", None) != MODEL_ESTIMATE:
        confidence = getattr(sample, "point_confidences", {}).get(name)
        if not _finite(confidence) or not .35 <= confidence <= 1:
            return None
    return point


def _time(sample):
    if getattr(sample, "timestamp_source", None) != "decoder_pts":
        return None
    if getattr(sample, "observation_semantics", None) == MODEL_ESTIMATE:
        binding = getattr(sample, "source_binding", {})
        values = [binding.get(key) for key in ("pts", "time_base_num", "time_base_den")]
        if any(not isinstance(value, int) or isinstance(value, bool) for value in values) or values[0] < 0 or min(values[1:]) <= 0:
            return None
        return values[0] * values[1] / values[2] * 1000
    return float(sample.time) if _finite(sample.time) else None


def _valid_reference(sample):
    epoch = getattr(sample, "camera_reference_epoch", None)
    if getattr(sample,"observation_semantics",None)==MODEL_ESTIMATE:
        binding=getattr(sample,"source_binding",{})
        sha=binding.get("video_sha256")
        source_time=_time(sample)
        if (getattr(sample,"pose_source",None)!="sam3d" or not isinstance(sha,str) or len(sha)!=64
                or any(c not in "0123456789abcdefABCDEF" for c in sha)
                or binding.get("frame_index")!=sample.frame_index or binding.get("person_track_id")!=sample.track
                or binding.get("camera_reference_epoch")!=epoch
                or any(not isinstance(binding.get(k),int) or isinstance(binding[k],bool) or binding[k]<0
                       for k in ("frame_index","processed_index","person_track_id","selection_epoch","camera_reference_epoch"))
                or source_time is None or not _finite(binding.get("timestamp_ms"))
                or abs(binding["timestamp_ms"]-source_time)>.51):
            return False
    return (getattr(sample, "camera_compensated", False) is True
            and isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= 0
            and _finite(sample.scale) and sample.scale >= 12 and _time(sample) is not None)


def _continuous(a,b):
    return (b.frame_index == a.frame_index + 1 and samples_are_continuous(a,b)
            and 0 < _time(b) - _time(a) <= 160)


def _window(samples, values, feature, *, axis=None):
    start,end = samples[0],samples[-1]
    source_duration = _time(end)-_time(start)
    if len(samples) < MIN_SAMPLES or source_duration < MIN_DURATION_MS:
        return None
    scale = statistics.median(sample.scale for sample in samples)
    if axis is None:
        value = values[-1] - values[0]
    else:
        value = (values[-1][axis] - values[0][axis])/scale
        if "velocity" in feature:
            value /= source_duration/1000
    row = {"start_ms": start.time, "end_ms": end.time, "source_frames": [sample.frame_index for sample in samples],
           "source_timestamps_ms": [_time(sample) for sample in samples],
           "video_timestamps_ms": [sample.time for sample in samples],
           "sample_count":len(samples), "value":value, "person_track_id":start.track,
           "selection_epoch":start.selection_epoch, "camera_reference_epoch":start.camera_reference_epoch,
           "fixed_scale_px":scale, "source_duration_ms":source_duration,
           "time_basis":"bound_source_pts" if getattr(start,"observation_semantics",None)==MODEL_ESTIMATE else "decoder_timestamp_ms",
           "measurement_provenance":measurement_provenance(samples),
           "reference":{"person_track_id":start.track,"selection_epoch":start.selection_epoch,
                        "camera_reference_epoch":start.camera_reference_epoch,"camera_compensated":True,
                        "normalization_basis":start.normalization_basis,**sample_source_reference(start)},
           "source_reference":sample_source_reference(start)}
    if getattr(start,"observation_semantics",None)==MODEL_ESTIMATE:
        row["source_binding_start"]={**start.source_binding,"video_timestamp_ms":start.time}
        row["source_binding_end"]={**end.source_binding,"video_timestamp_ms":end.time}
    return row


def analyze_preparation_translation(samples, phases):
    specs = [
        ("hip_center_dx_torso","髋中心画面横向平移","torso",0,"髋中心末帧横坐标减首帧，除以该连续窗固定躯干尺度；画面右为正。"),
        ("hip_center_dy_torso","髋中心画面竖向平移","torso",1,"髋中心末帧纵坐标减首帧，除以该连续窗固定躯干尺度；画面下为正。"),
        ("hip_center_mean_velocity_x_torso_per_s","髋中心横向平均速度","torso/s",0,"髋中心横向净位移除以固定窗躯干尺度，再除以原始解码时间差；画面右为正。"),
        ("hip_center_mean_velocity_y_torso_per_s","髋中心竖向平均速度","torso/s",1,"髋中心竖向净位移除以固定窗躯干尺度，再除以原始解码时间差；画面下为正。"),
        ("left_knee_flexion_change_deg","左膝屈曲变化","deg",None,"末帧与首帧的180°减髋—膝—踝二维夹角之差；正值表示局部屈膝增加。"),
        ("right_knee_flexion_change_deg","右膝屈曲变化","deg",None,"末帧与首帧的180°减髋—膝—踝二维夹角之差；正值表示局部屈膝增加。"),
    ]
    prep = [p for p in phases if isinstance(p,Mapping) and p.get("phase")=="preparation"]
    phase = prep[0] if len(prep)==1 else {}
    start,end=phase.get("start_ms"),phase.get("end_ms")
    valid = phase.get("status")=="measured" and _finite(start) and _finite(end) and 0 <= start < end
    selected = [s for s in samples if start <= s.time <= end] if valid else []
    measurements=[]
    for name,label,unit,axis,definition in specs:
        windows,current,values=[],[],[]
        def flush():
            if current and (window:=_window(current,values,name,axis=axis)) is not None:
                windows.append(window)
            current.clear();values.clear()
        for sample in selected:
            value=None
            if _valid_reference(sample):
                names=("left_hip","right_hip") if axis is not None else tuple(f"{name.split('_')[0]}_{joint}" for joint in ("hip","knee","ankle"))
                points=[_point(sample,joint) for joint in names]
                if all(p is not None for p in points):
                    if axis is not None:
                        value=tuple((points[0][i]+points[1][i])/2 for i in (0,1))
                    else:
                        angle=_angle(*points,minimum=.1*sample.scale)
                        value=180-angle if angle is not None else None
            if value is None:
                flush();continue
            if current and not _continuous(current[-1],sample):
                flush()
            current.append(sample);values.append(value)
        flush()
        full=bool(len(windows)==1 and windows[0]["start_ms"]==start and windows[0]["end_ms"]==end
                  and windows[0]["sample_count"]==len(selected))
        measurements.append({"feature_name":name,"label_zh":label,"unit":unit,"definition":definition,
            "source_refs":SOURCE_REFS[:],"full_phase_coverage":full,
            "status":"measured" if full else "partial" if windows else "unavailable", "windows":windows,
            "reason_codes":[] if full else ["local_windows_only_not_complete_source_stage"] if windows else ["insufficient_continuous_joint_camera_or_pts_evidence"]})
    return {"version":VERSION,"status":"measured" if all(m["status"]=="measured" for m in measurements) else "partial" if any(m["windows"] for m in measurements) else "unavailable",
        "scope":"contextual_preparation_window","assessment_status":"measurement_only","score":None,
        "formal_stage_verified":False,"coordinate_system":"stable_camera_reference_image_x_right_y_down",
        "normalization":"median_observed_torso_scale_fixed_per_continuous_window", "measurements":measurements,
        "limitations_zh":["准备段是运动候选，不确认GS01引拍阶段或GS05对方出球至垫步落地的正式窗口。",
                          "画面髋中点是视觉身体中心代理；未知持拍侧时不称向持拍侧加载，不测真实承重或受力。",
                          "每个量只用自身所需的真实连续关节；遮挡分窗，不补点、不跨机位或主体拼接。"]}
