"""Bounded public projection of the source-aligned measurement artifact."""
from __future__ import annotations

import copy
import json
import math
from functools import lru_cache
from pathlib import Path

from rallymate_scoring.source_assessment import ARTIFACT_NAME, ARTIFACT_VERSION, file_sha, source_binding
from rallymate_features.source_alignment import SOURCE_LOCATORS

VERSION = "source-aligned-assessment-v1.0.0"
SEMANTICS = "source_aligned_measurement_not_technical_grade"
MAX_BYTES = 64 * 1024 * 1024
MAX_WINDOWS_PER_INDICATOR = 80
IDS = ("FS01-M02", "FS01-M04", "FS01-M05", "FS09-M05")
FEATURES = {
    "FS01-M02": {"preload_hip_height_drop_body": "body", "left_preload_knee_flexion_change_deg": "deg",
                 "right_preload_knee_flexion_change_deg": "deg", "preload_body_speed_change_rate_body_s2": "body/s2"},
    "FS01-M04": {"post_slowdown_ankle_width_to_hip_width_ratio": "ratio"},
    "FS01-M05": {"landing_proxy_to_next_fs02_ms": "ms", "post_landing_body_speed_std_body_s": "body/s"},
    "FS09-M05": {"stable_control_proxy_to_next_fs10_or_fs02_ms": "ms", "stable_control_body_speed_std_body_s": "body/s"},
}
TRANSITIONS = {"landing_proxy_to_next_fs02_ms": ("landing_proxy_ms", {"FS02"}),
               "stable_control_proxy_to_next_fs10_or_fs02_ms": ("stable_control_onset_ms", {"FS02", "FS10"})}
REQUIREMENTS = {
    "FS01-M02": "原文要求髋中心下降幅度、膝角变化以及身体中心速度的连续性。",
    "FS01-M04": "原文要求落地后双踝横向距离与髋宽的比值；当前窗口仍以脚部减速候选定位。",
    "FS01-M05": "原文要求落地后重心稳定，并连续衔接同一人的第一步启动。",
    "FS09-M05": "原文要求制动完成后恢复稳定控制，并衔接同一人的回位或第一步启动。",
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
    "successor_source_track_identity_unverified": "前后动作的源人物轨迹尚未确认一致。",
    "hip_width_unobservable_or_degenerate": "髋宽无法可靠测量，不能计算踝距与髋宽之比。",
    "body_scale_unavailable": "人体归一化尺度不可用。",
    "preload_phase_order_invalid": "原阶段候选顺序不成立，未把起跳后的变化当作预加载。",
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
        return "已从该时段计算；好坏和等级仍需结合原文由教练判断。"
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
            "technical_grade": None, "technical_score_0_to_100": None, "indicators": [], "reason_zh": reason}


def _signature(path: Path) -> tuple:
    stat = path.stat()
    return str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _reject_constant(value):
    raise ValueError("Non-finite source artifact")


def _bound_measurement(measurement, indicator, event, item, by_id, duration_ms) -> bool:
    name, window = measurement.get("feature_name"), measurement.get("window_ms")
    if (name not in FEATURES[indicator] or measurement.get("unit") != FEATURES[indicator][name]
            or measurement.get("source_ref") != SOURCE_LOCATORS[indicator][0]
            or not isinstance(window, dict) or not _number(window.get("start_ms")) or not _number(window.get("end_ms"))
            or not 0 <= window["start_ms"] <= window["end_ms"] <= duration_ms):
        return False
    if name not in TRANSITIONS:
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
                valid_window = _bound_measurement(measurement, indicator, event, item, by_id, duration_ms)
                measured = (measurement.get("status") == "measured" and measurement.get("valid") is True
                            and _number(measurement.get("value")) and valid_window)
                measurements.append({"feature_name": name, "label_zh": label,
                    "value": measurement["value"] if measured else None, "unit": measurement.get("unit", ""),
                    "status": "measured" if measured else "unavailable", "reason_zh": _reason(measurement.get("reason"), measured),
                    "source_requirement_zh": REQUIREMENTS[indicator], "source_ref": measurement.get("source_ref"),
                    "measurement_window": window if valid_window else None})
            grouped[indicator].append({"event_id": event["event_id"], "person_track_id": event["person_track_id"],
                "start_ms": start, "end_ms": end, "measurements": measurements,
                "visibility": {"status": "sufficient" if ratio is not None and ratio >= .7 else "insufficient" if ratio is not None else "unavailable",
                    "valid_frame_ratio": ratio, "valid_frame_count": valid if valid_counts else None,
                    "total_frame_count": total if valid_counts else None, "required_joint_ids": joints,
                    "threshold_ratio": .7, "scope": "indicator_window",
                    "reason_zh": "统计该时段所有已分析帧中，本指标所需人体关节同时有效的帧。达到 70% 仅通过可见性检查，不代表技术合格。"},
                "limitations_zh": item.get("limitations_zh", []), "association": item.get("event_association"),
                "complete_source_rule_verified": False})
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
    return {"version": VERSION, "status": "available" if has_windows else "unavailable", "score_semantics": SEMANTICS,
            "technical_grade": None, "technical_score_0_to_100": None, "indicators": indicators,
            "measurement_artifact_version": ARTIFACT_VERSION,
            "source_binding": source_binding(),
            "reason_zh": ("按原文计算的局部测量，配合逐段人工技术评审；没有自动转换为等级或百分制。" if has_windows else
                          "没有可绑定到本次视频的相应步伐候选；可人工选择时段核验，未定位到候选不代表没有动作。")}


@lru_cache(maxsize=16)
def _load(signature, events_signature, primary_signature, frames_signature, video_id, duration_ms, video_sha256, frames_sha256):
    path, event_path, primary_path = Path(signature[0]), Path(events_signature[0]), Path(primary_signature[0])
    if signature[1] > MAX_BYTES or events_signature[1] > 16 * 1024 * 1024:
        return _empty("来源测量超出复核读取上限。")
    artifact = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    if (artifact.get("events_sha256") != file_sha(event_path)
            or artifact.get("primary_timeline_sha256") != file_sha(primary_path)
            or artifact.get("frames_sha256") != file_sha(Path(frames_signature[0]))
            or frames_sha256 and artifact.get("frames_sha256") != frames_sha256.lower()):
        return _empty("来源测量引用的事件、人物或帧记录已改变，需要重新生成。")
    events = [json.loads(line, parse_constant=_reject_constant) for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
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
        return _empty("本次任务暂未生成可核验的原文测量记录。")
