"""Read-only, event-bound footwork measurements for video review.

This view exposes observed event intervals and valid image-plane features. It
does not infer missing phases or convert observability into technical grades.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_service.training_evaluation import (
    _FEATURE_LABELS,
    _INDICATOR_FEATURES,
    _load_metric_cards,
)


MAX_EVENT_FILE_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 5000
MAX_INDICATOR_RECORDS = MAX_EVENTS * 13
MAX_RETURNED_EPISODES = 300
REVIEW_VERSION = "footwork-independent-measurements-v1.1.0"
_EVENT_NAMES = {
    "FS01": "准备与分腿垫步",
    "FS02": "第一步启动",
    "FS09": "制动与重新稳定",
}
_REASON_LABELS = {
    "events_artifact_missing": "本次任务没有保存步伐事件文件，暂不能提供逐段回放。",
    "events_artifact_invalid": "本次任务的步伐事件文件无法读取或内容损坏，逐段复核暂不可用。",
    "events_artifact_limit_exceeded": "步伐事件文件超出复核读取上限，暂不能生成逐段复核。",
    "record_limit_exceeded": "步伐事件或指标记录超出复核处理上限，暂不能生成逐段复核。",
    "video_identity_unavailable": "步伐事件缺少唯一的视频身份，无法确认记录属于本次视频。",
    "no_valid_event_intervals": "没有与本次视频身份匹配且时间区间有效的步伐事件，暂不能定位回放。",
}
_EXTRA_FEATURE_LABELS = {
    "shoulder_hip_angular_velocity": "肩髋相对投影角速度",
    "hip_center_y_body": "髋中心相对脚踝高度",
    "hip_center_relative_to_ankle_support": "髋中心相对支撑区域位置",
    "hip_center_vertical_velocity_body_s": "髋中心上移速度",
    "drive_side_code": "蹬伸侧代理编码",
    "launch_side_code": "启动侧代理编码",
    "launch_foot_speed_drop_body_s": "启动脚速度下降",
    "post_step_stance_width_body": "第一步后双脚间距",
    "hip_center_relative_to_ankle_midpoint_x_body": "髋中心相对踝中点横向位置",
    "hip_center_relative_to_ankle_midpoint_y_body": "髋中心相对踝中点纵向位置",
    "left_ankle_speed_body_s": "左踝速度",
    "right_ankle_speed_body_s": "右踝速度",
    "left_ankle_speed_drop_body_s": "左踝速度下降",
    "right_ankle_speed_drop_body_s": "右踝速度下降",
    "braking_side_code": "制动侧代理编码",
    "hip_height_delta_body": "髋中心高度变化",
    "shoulder_hip_angular_velocity_change_deg_s": "画面肩髋相对角速度变化",
    "hip_deceleration_to_double_support_proxy_ms": "身体减速至双支撑代理时差",
}


def _empty(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "1.1.0",
        "review_version": REVIEW_VERSION,
        "status": "unavailable",
        "reason": reason,
        "reason_zh": _REASON_LABELS.get(reason, "步伐逐段复核暂不可用。"),
        "episodes": [],
        "episode_count": 0,
        "returned_episode_count": 0,
        "is_truncated": False,
        "measurement_summary": {"measured_feature_count": 0, "unavailable_feature_count": 0,
                                "partial_indicator_count": 0, "measured_indicator_count": 0},
        "limitations_zh": [
            "步伐事件是视频运动代理片段，不代表真实脚步数、击球次数或正式技术等级。",
            "身体尺度归一化数值不是米制距离；脚部运动代理不等于实际离地、触地或受力。",
        ],
    }


def _identity(value: Any) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 256 and value.strip() else None


def _integer(value: Any, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _finite(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _bounded_rows(rows: Iterable[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
    result = []
    for index, row in enumerate(rows):
        if index >= limit:
            raise ValueError("record_limit_exceeded")
        if isinstance(row, Mapping):
            result.append(row)
    return result


def _unique_by_key(rows: Iterable[Mapping[str, Any]], key: str) -> tuple[dict[str, Mapping[str, Any]], int]:
    selected: dict[str, Mapping[str, Any]] = {}
    conflicts: set[str] = set()
    for row in rows:
        name = _identity(row.get(key))
        if name is None or name in conflicts:
            continue
        if name in selected and selected[name] != row:
            del selected[name]
            conflicts.add(name)
        else:
            selected[name] = row
    return selected, len(conflicts)


def _feature_candidates(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_features = record.get("features")
    candidates = [item for item in raw_features if isinstance(item, Mapping)
                  and item.get("feature_name") != "target_direction_alignment_error_deg"] if isinstance(raw_features, list) else []
    scoring_features = record.get("scoring_features")
    gate = record.get("quality_gate")
    # Independent target-direction evidence is present only in the scoring
    # feature list. Require its own measured status and the scoring gate;
    # invalid/conflicting duplicates cannot revive a measurement value.
    if (isinstance(scoring_features, list)
            and record.get("scoring_feature_status") == "measured"
            and (not isinstance(gate, Mapping) or gate.get("scoring_allowed") is True)):
        candidates.extend(scoring_features)
    return [item for item in candidates if isinstance(item, Mapping)]


def _features(record: Mapping[str, Any], indicator_id: str) -> list[dict[str, Any]]:
    candidates = _feature_candidates(record)
    allowed = _INDICATOR_FEATURES[indicator_id][1]
    selected, _ = _unique_by_key(
        (item for item in candidates if isinstance(item, Mapping)), "feature_name"
    )
    result = []
    for name in allowed:
        feature = selected.get(name)
        if feature is None or feature.get("valid") is not True:
            continue
        value, confidence, unit = feature.get("value"), feature.get("confidence"), feature.get("unit")
        if not _finite(value) or not _finite(confidence) or not 0 <= confidence <= 1:
            continue
        expected_unit = FEATURE_DEFINITIONS.get(name, {}).get("unit")
        if name == "target_direction_alignment_error_deg":
            expected_unit = "deg"
        if not isinstance(unit, str) or unit != expected_unit:
            continue
        result.append({
            "feature_name": name,
            "name_zh": _EXTRA_FEATURE_LABELS.get(name, _FEATURE_LABELS.get(name, name)),
            "value": value,
            "unit": unit,
            "confidence": confidence,
        })
    return result


def _reason_text(code: str) -> tuple[str, str]:
    if code in {"feature_record_missing", "required_feature_missing"}:
        return "该项没有独立测量记录。", "回放所列区间，确认所需关节可见；重新分析后查看该项证据。"
    if code in {"conflicting_feature_records", "invalid_feature_payload", "record_status_invalid"}:
        return "该项记录无效或互相冲突，未采用数值。", "重新分析当前视频；不要使用冲突记录推断动作质量。"
    if "camera" in code:
        return "该区间的相机补偿未通过验证。", "使用固定机位，或选择相机补偿连续有效的区间再比较。"
    if "phase" in code or "restabilization" in code or "event_edge" in code:
        return "该项所需的阶段边界或稳定区间证据不足。", "保留启动前和动作结束后的画面，复核对应阶段是否完整。"
    if "track" in code or "id_switch" in code or "identity" in code:
        return "该项所在区间的主体连续性未通过验证。", "按时间窗确认始终跟踪同一名球员，再复核测量。"
    if "direction" in code or "target" in code:
        return "该项所需的独立目标方向未确认。", "先标定目标方向；画面内移动方向不能直接当作战术选择是否正确。"
    if "body_scale" in code:
        return "身体尺度基准不足，不能可靠地归一化距离。", "保留躯干与下肢的完整画面，避免严重透视缩短。"
    if "sample" in code or "fraction" in code or "joint" in code or "confidence" in code:
        return "该项所需的有效关键点或连续样本不足。", "让所需关节在区间内持续可见；其他已测项仍可单独复核。"
    if code == "measurement_gate_blocked":
        return "该项未通过原始测量质量门槛。", "复核本区间的主体、关键点和阶段证据后再分析。"
    return "该项未满足独立测量条件。", "结合该项原因和时间窗复核；缺测不能解释为动作错误。"


def _measurements(record: Mapping[str, Any], indicator_id: str, event: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expose valid sibling features without changing aggregate/scoring gates.

    ``feature_status`` historically means *all* required functions were valid.
    A missing knee must not suppress an independently valid ankle distance.
    A measurement hard gate, conflicting duplicate or bad numeric payload
    still invalidates the feature; the aggregate score path remains unchanged.
    """
    candidates = _feature_candidates(record)
    selected, _ = _unique_by_key(candidates, "feature_name")
    present = {_identity(item.get("feature_name")) for item in candidates}
    raw_gate = record.get("quality_gate")
    gate = raw_gate if isinstance(raw_gate, Mapping) else {}
    gate_allowed = raw_gate is None or (isinstance(raw_gate, Mapping) and gate.get("measurement_allowed") is True)
    result = []
    for name in _INDICATOR_FEATURES[indicator_id][1]:
        feature = selected.get(name)
        definition = FEATURE_DEFINITIONS.get(name, {})
        expected_unit = "deg" if name == "target_direction_alignment_error_deg" else definition.get("unit")
        reasons = []
        if not gate_allowed:
            flags = gate.get("hard_fail_flags")
            if isinstance(flags, list):
                reasons.extend(flag for flag in flags if _identity(flag))
            if not reasons:
                reasons.append("measurement_gate_blocked")
        if record.get("feature_status") not in {"measured", "unavailable"}:
            reasons.append("record_status_invalid")
        if feature is None:
            reasons.append("conflicting_feature_records" if name in present else "feature_record_missing")
        else:
            if feature.get("valid") is not True:
                reasons.append(_identity(feature.get("reason")) or "required_feature_missing")
            elif (not _finite(feature.get("value")) or not _finite(feature.get("confidence"))
                    or not 0 <= feature["confidence"] <= 1 or feature.get("unit") != expected_unit):
                reasons.append("invalid_feature_payload")
        if name == "target_direction_alignment_error_deg" and feature is None:
            reasons.append("target_direction_not_observed")
        measured = not reasons
        reason_zh, hint = _reason_text(reasons[0]) if reasons else (
            "该特征通过自身测量检查，可独立查看；数值不表示动作好坏。", None,
        )
        source_frames = feature.get("source_frames", []) if feature else []
        source_frames = sorted(set(value for value in source_frames if _integer(value))) if isinstance(source_frames, list) else []
        result.append({
            "feature_name": name, "name_zh": _EXTRA_FEATURE_LABELS.get(name, _FEATURE_LABELS.get(name, name)),
            "value": feature["value"] if measured else None, "unit": expected_unit,
            "confidence": feature["confidence"] if measured else None,
            "status": "measured" if measured else "unavailable", "reason_codes": list(dict.fromkeys(reasons)),
            "reason_zh": reason_zh, "review_hint_zh": hint,
            "window": {"start_ms": event["start_ms"], "end_ms": event["end_ms"], "scope": "event_interval"},
            "source_frames": source_frames,
            "feature_version": _identity(feature.get("feature_version")) if feature else None,
            "review_version": REVIEW_VERSION, "required_joints": list(definition.get("required_joints", [])),
            "view_semantics": "independent_target_context" if name == "target_direction_alignment_error_deg" else "image_plane_proxy",
            "view_label_required": False,
            "aggregate_feature_status": record.get("feature_status"),
        })
    return result


def build_footwork_review(
    events: Iterable[Mapping[str, Any]],
    indicator_records: Iterable[Mapping[str, Any]],
    *,
    video_id: str | None = None,
) -> dict[str, Any]:
    """Join records only on matching video, event, person and event code."""
    try:
        event_rows = _bounded_rows(events, MAX_EVENTS)
        records = _bounded_rows(indicator_records, MAX_INDICATOR_RECORDS)
    except ValueError:
        return _empty("record_limit_exceeded")
    if video_id is None:
        video_ids = {_identity(row.get("video_id")) for row in event_rows}
        video_ids.discard(None)
        if len(video_ids) != 1:
            return _empty("video_identity_unavailable")
        video_id = next(iter(video_ids))
    if _identity(video_id) is None:
        return _empty("video_identity_unavailable")

    matching_events = [row for row in event_rows if row.get("video_id") == video_id]
    unique_events, event_conflicts = _unique_by_key(matching_events, "event_id")
    episodes = []
    valid_events: dict[str, Mapping[str, Any]] = {}
    rejected_events = len(event_rows) - len(matching_events) + event_conflicts
    for event_id, event in unique_events.items():
        start, end = event.get("start_ms"), event.get("end_ms")
        if (not isinstance(event.get("event_code"), str) or event["event_code"] not in _EVENT_NAMES
                or not _integer(event.get("person_track_id"), 1)
                or not _integer(start) or not _integer(end) or end <= start):
            rejected_events += 1
            continue
        valid_events[event_id] = event

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    rejected_records = 0
    for record in records:
        event = valid_events.get(record.get("event_id")) if isinstance(record.get("event_id"), str) else None
        indicator_id = record.get("indicator_id")
        if (event is None or not isinstance(indicator_id, str) or indicator_id not in _INDICATOR_FEATURES
                or not _integer(record.get("person_track_id"), 1)
                or any(record.get(key) != event.get(key) for key in ("video_id", "event_id", "event_code", "person_track_id"))
                or _INDICATOR_FEATURES[indicator_id][0] != event["event_code"]):
            rejected_records += 1
            continue
        grouped.setdefault(event["event_id"], []).append(record)

    cards = _load_metric_cards()
    for event_id, event in valid_events.items():
        indicators = []
        unique_records, conflicts = _unique_by_key(grouped.get(event_id, []), "indicator_id")
        rejected_records += conflicts
        for indicator_id, record in sorted(unique_records.items()):
            raw_gate = record.get("quality_gate")
            gate = raw_gate if isinstance(raw_gate, Mapping) else {}
            gate_valid = raw_gate is None or (isinstance(raw_gate, Mapping) and gate.get("measurement_allowed") is True)
            measured = record.get("feature_status") == "measured" and gate_valid
            features = _features(record, indicator_id) if measured else []
            status = record.get("scoring_status")
            scoring_status = status if isinstance(status, str) and status in {"scored", "calibration_required", "unavailable"} else "unavailable"
            if not measured or not features or gate.get("scoring_allowed") is False:
                scoring_status = "unavailable"
            measurements = _measurements(record, indicator_id, event)
            measured_count = sum(item["status"] == "measured" for item in measurements)
            measurement_status = "measured" if measured_count == len(measurements) else "partial" if measured_count else "unavailable"
            indicators.append({
                "indicator_id": indicator_id,
                "name_zh": str(cards.get(indicator_id, {}).get("name") or indicator_id),
                "feature_status": "measured" if measured and features else "unavailable",
                "scoring_status": scoring_status,
                "features": features,
                "measurements": measurements,
                "measurement_status": measurement_status,
                "measured_feature_count": measured_count,
                "expected_feature_count": len(measurements),
            })
        episodes.append({
            "event_id": event_id,
            "video_id": video_id,
            "event_code": event["event_code"],
            "name_zh": _EVENT_NAMES[event["event_code"]],
            "person_track_id": event["person_track_id"],
            "start_ms": event["start_ms"],
            "end_ms": event["end_ms"],
            "indicators": indicators,
        })
    episodes.sort(key=lambda event: (event["start_ms"], event["end_ms"], event["event_id"]))
    result = _empty("no_valid_event_intervals")
    all_indicators = [item for episode in episodes for item in episode["indicators"]]
    result.update({
        "status": "available" if episodes else "unavailable",
        "reason": None if episodes else "no_valid_event_intervals",
        "reason_zh": None if episodes else _REASON_LABELS["no_valid_event_intervals"],
        "episodes": episodes[:MAX_RETURNED_EPISODES],
        "episode_count": len(episodes),
        "returned_episode_count": min(len(episodes), MAX_RETURNED_EPISODES),
        "is_truncated": len(episodes) > MAX_RETURNED_EPISODES,
        "rejected_event_count": rejected_events,
        "rejected_indicator_count": rejected_records,
        "measurement_summary": {
            "measured_feature_count": sum(item["measured_feature_count"] for item in all_indicators),
            "unavailable_feature_count": sum(item["expected_feature_count"] - item["measured_feature_count"] for item in all_indicators),
            "partial_indicator_count": sum(item["measurement_status"] == "partial" for item in all_indicators),
            "measured_indicator_count": sum(item["measurement_status"] == "measured" for item in all_indicators),
            "scope": "all_bound_episodes_including_truncated",
        },
    })
    return result


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError("nonfinite_json_number")


def load_footwork_review(
    events_path: Path,
    indicator_records: Iterable[Mapping[str, Any]],
    *,
    video_id: str | None = None,
) -> dict[str, Any]:
    """Missing or corrupt optional event artifacts do not break the report."""
    try:
        if not events_path.is_file():
            return _empty("events_artifact_missing")
        if events_path.stat().st_size > MAX_EVENT_FILE_BYTES:
            return _empty("events_artifact_limit_exceeded")
        with events_path.open("rb") as handle:
            contents = handle.read(MAX_EVENT_FILE_BYTES + 1)
        if len(contents) > MAX_EVENT_FILE_BYTES:
            return _empty("events_artifact_limit_exceeded")
        rows = []
        for line_number, line in enumerate(contents.splitlines(), 1):
            if line_number > MAX_EVENTS:
                return _empty("events_artifact_limit_exceeded")
            if not line.strip():
                continue
            row = json.loads(line, object_pairs_hook=_strict_object, parse_constant=_reject_nonfinite)
            if not isinstance(row, dict):
                return _empty("events_artifact_invalid")
            rows.append(row)
        return build_footwork_review(rows, indicator_records, video_id=video_id)
    except (OSError, ValueError, UnicodeError):
        return _empty("events_artifact_invalid")


__all__ = ["build_footwork_review", "load_footwork_review"]
