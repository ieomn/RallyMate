"""Five display groups separating measurements, current rules and legacy curves.

Only independent target alignment is a current scoring subcriterion. Selected
wrist-speed trends preserve their measurements and historical diagnostics but
never enter the current score. Missing technical rules stay explicit.
"""
from __future__ import annotations

import math
import copy
import statistics
from collections.abc import Iterable, Mapping
from typing import Any

from rallymate_scoring.scoring_context import validate_resolved_target_direction_context
from rallymate_scoring.indicator_assessment import motion_joint_observations
from rallymate_scoring.model_measurement_contract import has_model_measurement_context
from rallymate_scoring.training_dimensions import DIMENSION_NAMES, source_basis_for_dimension
from rallymate_service.footwork_review import build_footwork_review, _model_feature_evidence
from rallymate_service.preparation_observations import preparation_candidate_observations, preparation_translation_observations
from rallymate_service.relative_score import (
    LEGACY_VERSION, SCORING_RULE_IDS, build_relative_reference, dimension_scoring_audit,
)


PROCESS_EVALUATION_VERSION = "process-evaluation-v2.0.0"
PROCESS_SEMANTICS = "observable_motion_process_reference"
SUPPORTED_MOTION_VERSIONS = {"process-motion-v1.0.0", "process-motion-v1.1.0"}
_DIMENSIONS = (
    ("movement", DIMENSION_NAMES["movement"], ("movement_target_alignment",)),
    ("preparation", DIMENSION_NAMES["preparation"], ("preparation_duration", "preparation_direction")),
    ("swing", DIMENSION_NAMES["swing"], ("acceleration_trend", "swing_direction", "swing_direction_consistency", "swing_direction_reversals", "hip_to_shoulder_peak_delay", "shoulder_to_wrist_peak_delay")),
    ("contact", DIMENSION_NAMES["contact"], ("confirmed_contact", "contact_posture_control", "racket_face_control")),
    ("follow_through", DIMENSION_NAMES["follow_through"], ("deceleration_trend", "terminal_speed_ratio", "follow_duration")),
    ("transition", "两次之间的衔接", ("between_motion_interval",)),
)
_SCORING_RULES = SCORING_RULE_IDS
MAX_FOOTWORK_OBSERVATIONS_PER_FEATURE = 12
_TREND_LIMITATION = "阶段边界也由腕部运动曲线估计；历史百分比仅描述曲线趋势，已退出评分，不能独立验证动作正确性、真实受力或发力效率。"
# A small, explicit set of existing FS measurements. These remain observations:
# no confidence, coverage or feature value enters a new scoring expression.
FOOTWORK_OBSERVATION_FEATURES = {
    "FS01-M02": ("hip_center_y_body", "left_knee_flexion_deg", "right_knee_flexion_deg"),
    "FS01-M03": ("bilateral_foot_rise_min_body", "bilateral_foot_rise_synchrony_ms"),
    "FS01-M04": ("post_slowdown_stance_width_body", "hip_center_lateral_variability_body"),
    "FS01-M05": ("body_center_speed_body_s", "hip_center_relative_to_ankle_support"),
    "FS02-M02": ("body_center_speed_body_s", "launch_direction_deg"),
    "FS02-M03": ("support_knee_extension_velocity_deg_s", "hip_acceleration_along_launch_direction_body_s2"),
    "FS02-M04": ("launch_foot_speed_peak_body_s", "launch_foot_relative_displacement_body"),
    "FS02-M05": ("first_step_displacement_body", "post_step_hip_direction_consistency"),
    "FS09-M01": ("hip_center_speed_body_s", "hip_center_motion_direction_deg"),
    "FS09-M02": ("braking_ankle_speed_drop_body_s", "hip_center_deceleration_body_s2"),
    "FS09-M03": ("hip_center_speed_drop_body_s", "left_knee_flexion_change_deg", "right_knee_flexion_change_deg"),
    "FS09-M04": ("stability_duration_ms", "stance_width_body"),
    "FS09-M05": ("double_support_proxy_duration_ms", "torso_lean_variability_deg"),
}
_SCORE_SCOPES = {
    "movement": "仅启动方向与独立训练目标的一致性参考；不代表垫步、调整、支撑、制动等全部移动质量。",
    "preparation": "报告已测准备时长、腕部方向及逐关节局部几何；三段运动估计不等于原卡正式阶段，尚缺适用动作确认与完整等级条件。",
    "swing": "腕速加速趋势只保留为历史曲线诊断，已退出评分；挥拍协调与动力传递技术规则尚未实现。",
    "contact": "触球控制关注触球时的姿态、拍面与球拍控制；当前尚未实现该维度的量化评分。",
    "follow_through": "腕速减速趋势只保留为历史曲线诊断，已退出评分；收拍、回位与衔接技术规则尚未实现。",
    "transition": "仅报告相邻候选动作的时间间隔；不据间隔推断回位质量或准备就绪。",
}


def _score_scope(identifier: str, *, measured: bool, score: float | None) -> dict[str, Any]:
    """Explain an absent score separately from absent measurements."""
    if score is not None:
        reason, text = "engineering_subcriterion_only", "当前数值仅是已实现子项的工程参考，尚不是整维技术分。"
    elif identifier == "preparation":
        reason = "rule_not_implemented"
        text = ("已取得准备候选测量；准备技术评分规则尚未实现，持拍手、动作类型与正式阶段的适用条件也需明确。" if measured
                else "准备技术评分规则尚未实现；本次也没有可用准备候选测量。")
    elif identifier == "movement":
        reason = "independent_reference_missing"
        text = ("已取得步伐运动测量；当前计分子项缺少通过绑定检查的独立目标与启动测量组合，其他移动技术规则尚未实现。" if measured
                else "启动方向一致性缺少通过绑定检查的独立目标与启动测量组合；其他移动技术规则尚未实现。")
    elif identifier == "contact":
        reason, text = "rule_not_implemented", "触球控制量化规则尚未实现；当前没有将触球姿态、拍面控制与经验证触球事件绑定评价。"
    elif identifier == "transition":
        reason = "observed_interval_not_recovery_quality" if measured else "continuous_between_motion_evidence_missing"
        text = "动作间隔仅作观察，不能直接换算衔接质量分。" if measured else "缺少连续动作间隔证据；该补充项也未定义技术评分。"
    else:
        reason, text = "selection_biased_proxy_excluded", "腕速趋势受同一曲线分段选择影响，已退出评分；保留可用测量及历史诊断，完整技术评分规则尚未实现。"
    return {"score_scope": "engineering_subcriterion_reference" if identifier == "movement" else "observation_only",
            "score_scope_zh": _SCORE_SCOPES[identifier], "score_covers_all_source_cards": False,
            "status_reason": reason, "status_reason_zh": text}


def _obj(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _interval(row: Mapping[str, Any]) -> tuple[int | None, int | None]:
    start, end = _num(row.get("start_ms")), _num(row.get("end_ms"))
    if (start is None or end is None or not start.is_integer() or not end.is_integer()
            or not 0 <= start < end):
        return None, None
    return int(start), int(end)


def _bound(row: Mapping[str, Any], episode: Mapping[str, Any]) -> bool:
    start, end = _interval(row)
    a, b = _interval(episode)
    return start is not None and a is not None and a <= start < end <= b


def _criterion(identifier: str, label: str, *, episode: Mapping[str, Any] | None = None,
               window: Mapping[str, Any] | None = None, value: Any = None, unit: str = "",
               score: Any = None, reason: str, rule: str, source: str) -> dict[str, Any]:
    episode = episode or {}
    start, end = _interval(window or episode)
    value = _num(value)
    if start is None:
        value = None
    score = _num(score) if value is not None else None
    if score is not None and not 0 <= score <= 100:
        score = None
    return {"id": identifier, "label_zh": label,
            "status": "measured" if value is not None else "unavailable",
            "value": round(value, 4) if value is not None else None, "unit": unit,
            "score": round(score, 1) if score is not None else None,
            "reason_zh": reason, "rule_zh": rule, "source": source,
            "start_ms": start, "end_ms": end,
            "episode_id": episode.get("episode_id") or episode.get("event_id")}


def _episodes(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    families = _obj(_obj(_obj(summary.get("action_recognition")).get("motion_analysis")).get("families"))
    selected: dict[tuple, Mapping[str, Any]] = {}
    conflicts: set[tuple] = set()
    for family in families.values():
        for episode in _rows(_obj(family).get("episodes")):
            start, end = _interval(episode)
            track = episode.get("person_track_id")
            if start is None or not isinstance(track, int) or isinstance(track, bool) or track < 0:
                continue
            if not isinstance(episode.get("episode_id"), str) or not episode["episode_id"]:
                continue
            # Return-context projections may contain the same baseline motion.
            key = (track, start, end)
            if key in conflicts:
                continue
            if key in selected and selected[key].get("process_motion") != episode.get("process_motion"):
                selected.pop(key)
                conflicts.add(key)
            else:
                selected.setdefault(key, episode)
    return sorted(selected.values(), key=lambda row: (row["start_ms"], row["end_ms"]))


def _phase(process: Mapping[str, Any], name: str, episode: Mapping[str, Any]) -> Mapping[str, Any]:
    phases = [row for row in _rows(process.get("phases")) if row.get("phase") == name]
    if len(phases) != 1 or phases[0].get("status") != "measured" or not _bound(phases[0], episode):
        return {}
    return phases[0]


def _motion_criteria(episode: Mapping[str, Any], result: dict[str, list[dict]]) -> None:
    previous_counts = {key: len(rows) for key, rows in result.items()}
    process = _obj(episode.get("process_motion"))
    valid = (process.get("version") in SUPPORTED_MOTION_VERSIONS
             and process.get("coordinate_system") == "body_relative_image_x_right_y_down"
             and process.get("status") in {"measured_2d", "partial"})
    process = process if valid else {}
    provenance = _obj(process.get("measurement_provenance"))
    model_estimated = provenance.get("observation_semantics") == "monocular_model_estimate"
    source = "action_recognition.motion_analysis.episodes.process_motion"
    prepare = _phase(process, "preparation", episode)
    acceleration = _phase(process, "acceleration", episode)
    follow = _phase(process, "follow_through", episode)
    for dimension, phase, duration_id, label in (
        ("preparation", prepare, "preparation_duration", "引拍阶段持续时间"),
        ("follow_through", follow, "follow_duration", "随挥阶段持续时间"),
    ):
        start, end = _interval(phase)
        result[dimension].append(_criterion(duration_id, label, episode=episode, window=phase,
            value=end - start if start is not None else None, unit="ms",
            reason="连续画面中的阶段用时；未设统一快慢或完整性技术阈值。" if phase else "缺少完整连续的阶段证据，不能从候选中心补出阶段。",
            rule="只报告实测时间，不将时间长短直接映射为技术分。", source=source + ".phases"))
    for dimension, phase, identifier, label in (
        ("preparation", prepare, "preparation_direction", "引拍腕部运动方向"),
        ("swing", acceleration, "swing_direction", "加速挥拍腕部运动方向"),
    ):
        angle = _num(_obj(phase.get("wrist")).get("direction_angle_deg"))
        angle = angle if angle is not None and -180 <= angle <= 180 else None
        result[dimension].append(_criterion(identifier, label, episode=episode, window=phase,
            value=angle, unit="deg",
            reason="以躯干为参照，画面向右为 0°、向下为 90°；是投影方向，不等于球场前后或击球目标。" if angle is not None else "该阶段方向缺少有效连续测量。",
            rule="方向只观察；没有球场目标与技术类型参照时，不按画面左右判对错。", source=source + ".phases.wrist.direction_angle_deg"))
    direction = _obj(process.get("direction"))
    direction_valid = (bool(acceleration) and direction.get("status") == "measured"
                       and direction.get("phase") == "acceleration"
                       and direction.get("semantics") == "projected_path_consistency_not_direction_correctness")
    consistency = _num(direction.get("direction_consistency_ratio")) if direction_valid else None
    if consistency is None or not 0 <= consistency <= 1:
        consistency = None
    reversals = _num(direction.get("reversal_count")) if direction_valid else None
    direction_samples = _num(_obj(acceleration.get("wrist")).get("speed_sample_count"))
    if (reversals is None or not reversals.is_integer() or reversals < 0
            or direction_samples is None or not direction_samples.is_integer()
            or direction_samples < 2 or reversals > direction_samples - 1
            or direction.get("reversal_definition") != "adjacent_observed_steps_turn_more_than_120_degrees"):
        reversals = None
    result["swing"].append(_criterion("swing_direction_consistency", "挥拍路径方向一致性",
        episode=episode, window=acceleration, value=consistency, unit="ratio",
        reason="净位移占运动路径的比例；弧形挥拍可能合理，较小的比例不等于技术错误。" if consistency is not None else "缺少有效加速阶段的连续方向路径测量。",
        rule="净位移长度 ÷ 实际运动路径长度，范围 0～1；只观察路径形状，不加入技术评分。",
        source=source + ".direction.direction_consistency_ratio"))
    result["swing"].append(_criterion("swing_direction_reversals", "挥拍路径局部反向次数",
        episode=episode, window=acceleration, value=reversals, unit="count",
        reason="统计相邻可辨位移的局部转向；弧线和动作调整可能合理，反向次数不直接等于动作错误次数。" if reversals is not None else "缺少有效加速阶段的局部转向测量。",
        rule="相邻可辨位移方向夹角超过 120° 记一次；排除小于 0.005 躯干尺度的位移，只观察不计技术分。",
        source=source + ".direction.reversal_count"))
    for dimension, phase, identifier, feature, label in (
        ("swing", acceleration, "acceleration_trend", "speed_increase_fraction", "历史腕速加速趋势"),
        ("follow_through", follow, "deceleration_trend", "speed_decrease_fraction", "历史腕速减速趋势"),
    ):
        wrist = _obj(phase.get("wrist"))
        fraction = _num(wrist.get(feature))
        complement = _num(wrist.get("speed_decrease_fraction" if dimension == "swing" else "speed_increase_fraction"))
        sample_count = _num(wrist.get("speed_sample_count"))
        if (fraction is None or not 0 <= fraction <= 1 or sample_count is None
                or not sample_count.is_integer() or sample_count < 3
                or complement is None or not 0 <= complement <= 1
                or abs(fraction + complement - 1) > .0002):
            fraction = None
        sign = "正" if dimension == "swing" else "负"
        reason = (f"该阶段腕速变化中，{sign}向变化占 {fraction * 100:.1f}%。" + _TREND_LIMITATION
                  if fraction is not None else "缺少至少 3 个连续腕速样本，或速度没有可分辨变化，暂不生成趋势分。")
        row = _criterion(identifier, label, episode=episode, window=phase,
            value=fraction * 100 if fraction is not None else None, unit="%",
            reason=reason,
            rule=f"历史曲线诊断：100 × 相邻腕速{sign}向变化量绝对值之和 ÷ 全部腕速变化量绝对值之和；分段选择存在偏倚，已退出当前评分。",
            source=source + ".phases.wrist." + feature)
        row["legacy_diagnostic"] = {"version": LEGACY_VERSION,
            "score": round(fraction * 100, 1) if fraction is not None else None,
            "semantics": "selection_biased_motion_curve_diagnostic", "is_technical_quality": False,
            "contributes_to_current_score": False, "exclusion_reason": "selection_biased_proxy_excluded"}
        row["sample_count"] = int(sample_count) if sample_count is not None and sample_count >= 0 else 0
        if model_estimated:
            row.update(assessment_status="mathematical_reference" if row["score"] is not None else "measurement_only",
                       measurement_provenance=dict(provenance),
                       reason_zh="单目模型估计的腕部曲线变化；按旧公式保留趋势百分比，但同曲线分段存在选择偏倚，已退出评分。关键点置信度与可见性仍未知。")
        result[dimension].append(row)
    sequence = _obj(process.get("sequence"))
    sequence_valid = sequence.get("status") in {"measured", "partial"} and sequence.get("semantics") == "image_plane_speed_peak_order"
    peaks = [_num(sequence.get(name + "_peak_ms")) for name in ("hip", "shoulder", "wrist")]
    start, end = _interval(episode)
    valid_peaks = [peak is not None and start is not None and start <= peak <= end for peak in peaks]
    resolution = _num(sequence.get("timing_resolution_ms"))
    for identifier, feature, i, label in (
        ("hip_to_shoulder_peak_delay", "hip_to_shoulder_ms", 0, "髋至肩二维运动峰值时差"),
        ("shoulder_to_wrist_peak_delay", "shoulder_to_wrist_ms", 1, "肩至腕二维运动峰值时差"),
    ):
        value = _num(sequence.get(feature)) if sequence_valid and valid_peaks[i] and valid_peaks[i + 1] else None
        if value is not None and abs(value - (peaks[i + 1] - peaks[i])) > 1:
            value = None
        timing_note = (f"采样分辨率约 {resolution:g} 毫秒；" if resolution is not None and resolution > 0 else "采样时间有限；")
        if resolution is not None and value is not None and abs(value) <= resolution:
            timing_note += "此时差未超过采样分辨率，不能区分可靠先后；"
        elif sequence.get("ordering_resolved") is False:
            timing_note += "整体峰序尚不能可靠区分；"
        row = _criterion(identifier, label, episode=episode, value=value, unit="ms",
            reason=(timing_note + "数值为后者采样峰值时间减前者；二维运动峰值不能证明真实受力或动力链传递。" if value is not None
                    else "缺少唯一且连续可定位的肩、髋、腕运动峰值；平坦或多峰信号不推断发力顺序。"),
            rule="只比较同一连续动作内的峰值时间，不按二维峰序判定正确发力模式。", source=source + ".sequence." + feature)
        row["timing_resolution_ms"] = resolution if resolution is not None and resolution > 0 and value is not None else None
        row["ordering_resolved"] = bool(value is not None and all(valid_peaks) and sequence.get("ordering_resolved") is True
                                       and resolution is not None and resolution > 0
                                       and all(b - a > resolution for a, b in zip(sorted(peaks), sorted(peaks)[1:])))
        result["swing"].append(row)
    deceleration = _obj(process.get("follow_through"))
    ratio = _num(deceleration.get("end_to_peak_speed_ratio")) if deceleration.get("status") == "measured" and follow else None
    ratio = ratio if ratio is not None and 0 <= ratio <= 1 else None
    result["follow_through"].append(_criterion("terminal_speed_ratio", "随挥末端与峰值腕速之比",
        episode=episode, window=follow, value=ratio, unit="ratio",
        reason="用于观察峰值后速度是否回落；阶段边界来自同一速度曲线，此项不能证明收拍技术完整。" if ratio is not None else "缺少连续的随挥末端和峰值腕速。",
        rule="只报告末端速度/峰值速度；不重复计入减速趋势分。", source=source + ".follow_through.end_to_peak_speed_ratio"))
    if model_estimated:
        for identifier, rows in result.items():
            for row in rows[previous_counts[identifier]:]:
                row.update(assessment_status="mathematical_reference" if row["score"] is not None else "measurement_only",
                           measurement_provenance=dict(provenance))
                if "单目模型估计" not in row["reason_zh"]:
                    row["reason_zh"] = "单目模型估计的二维几何，置信度与可见性未知。" + row["reason_zh"]


def _movement_criteria(summary: Mapping[str, Any], records: Iterable[Mapping[str, Any]],
                       review: Mapping[str, Any], result: dict[str, list[dict]]) -> None:
    coordinates_current = _obj(_obj(summary.get("minimum_scoring_loop")).get("coordinate_contract")).get("contract_version") == "isotropic-frame-long-edge-v1.1.0"
    selected: dict[tuple, Mapping[str, Any]] = {}
    conflicts: set[tuple] = set()
    for record in records:
        if record.get("indicator_id") != "FS02-M02" or record.get("event_code") != "FS02":
            continue
        key = (record.get("video_id"), record.get("person_track_id"), record.get("event_id"))
        if not all(isinstance(value, (str, int)) and not isinstance(value, bool) for value in key):
            continue
        if key in selected and selected[key] != record:
            selected.pop(key)
            conflicts.add(key)
        elif key not in conflicts:
            selected[key] = record
    seen_episodes: set[tuple] = set()
    for episode in _rows(review.get("episodes")):
        if episode.get("event_code") != "FS02" or _interval(episode)[0] is None:
            continue
        key = (episode.get("video_id"), episode.get("person_track_id"), episode.get("event_id"))
        if key in seen_episodes:
            continue
        seen_episodes.add(key)
        result["movement"].append(_criterion("movement_target_alignment", "启动方向与独立目标的一致性",
            episode=episode, reason="缺少当前坐标版本、显式目标方向或有效事件绑定，暂不评价目标方向一致性。",
            rule="目标须显式提供并绑定本视频坐标；缺失目标时不将实际移动方向当作目标。", source="indicator_features.FS02-M02.scoring_context"))
        if not coordinates_current:
            continue
        record = selected.get(key, {})
        gate = _obj(record.get("quality_gate"))
        if (record.get("feature_status") != "measured" or record.get("scoring_feature_status") != "measured"
                or gate.get("measurement_allowed") is False or gate.get("scoring_allowed") is not True):
            continue
        launches = [row for row in _rows(record.get("features")) if row.get("feature_name") == "launch_direction_deg"]
        if len(launches) != 1 or launches[0].get("unit") != "deg":
            continue
        model_evidence = None
        if has_model_measurement_context(record, episode, launches[0]):
            model_evidence = _model_feature_evidence(record, launches[0], episode)
            if model_evidence is None:
                continue
        source_frames = launches[0].get("source_frames")
        if not isinstance(source_frames, list) or len(source_frames) < 2:
            continue
        context = _obj(record.get("scoring_context"))
        try:
            validate_resolved_target_direction_context(context, event=episode, launch_direction_feature=launches[0])
        except (ValueError, TypeError, OverflowError):
            continue
        if context.get("status") != "available":
            continue
        value = _num(_obj(context.get("context_feature")).get("value"))
        if value is None or not 0 <= value <= 180:
            continue
        result["movement"][-1] = _criterion("movement_target_alignment", "启动方向与独立目标的一致性",
            episode=episode, value=value, unit="deg", score=100 * (1 - value / 180),
            reason=f"启动方向与显式训练目标相差 {value:.1f}°；仅评价此目标的方向一致性，不代表全部移动质量。",
            rule="工程试行：100 × (1 − 目标方向绝对夹角/180°)；0°=100，90°=50，180°=0。目标须显式提供并绑定本视频坐标。",
            source="indicator_features.FS02-M02.scoring_context.accepted_operator_reference")
        if model_evidence is not None:
            result["movement"][-1].update(model_evidence, assessment_status="mathematical_reference", confidence=None)


def _movement_observations(summary: Mapping[str, Any], records: list[Mapping[str, Any]],
                           review: Mapping[str, Any], result: dict[str, list[dict]]) -> None:
    video_id = _obj(summary.get("minimum_scoring_loop")).get("video_id") or summary.get("job_id")
    if not isinstance(video_id, str) or not video_id:
        return
    # Reuse the event/record join and independent feature gate; do not trust a
    # caller's pre-populated measurement values or let one absent sibling hide
    # independently valid measurements. Only this run's bound events enter.
    video_sha = _obj(_obj(summary.get("minimum_scoring_loop")).get("provenance")).get("video_sha256")
    verified = build_footwork_review(_rows(review.get("episodes")), records, video_id=video_id,
                                     video_sha256=video_sha if isinstance(video_sha, str) else None)
    for episode in verified["episodes"]:
        for indicator in episode["indicators"]:
            identifier = indicator["indicator_id"]
            allowed = FOOTWORK_OBSERVATION_FEATURES.get(identifier, ())
            for feature in indicator["measurements"]:
                name = feature["feature_name"]
                frames = feature["source_frames"]
                if name not in allowed or feature["status"] != "measured" or not frames:
                    continue
                row = _criterion(f"footwork_{identifier}_{name}", feature["name_zh"], episode=episode,
                    value=feature["value"], unit=feature["unit"],
                    reason="同一视频、事件和球员绑定的二维步伐测量；保留原始特征定义与事件窗口，不能据此确认真实触地、受力或整张原卡达标。",
                    rule="仅展示通过独立测量检查的原始特征值；不换算技术分，不使用置信度或覆盖率计分。",
                    source=f"indicator_features.{identifier}.features.{name}")
                row.update({"indicator_id": identifier, "feature_name": name,
                            "video_id": episode["video_id"], "person_track_id": episode["person_track_id"],
                            "source_frames": frames, "feature_version": feature["feature_version"],
                            "window_scope": "event_interval", "measurement_semantics": "image_plane_proxy_not_source_card_grade",
                            "quality": {"measurement_status": feature["status"], "confidence": feature["confidence"],
                                        "reason_codes": feature["reason_codes"],
                                        "aggregate_feature_status": feature["aggregate_feature_status"],
                                        "required_joints": feature["required_joints"], "review_version": feature["review_version"]}})
                if feature.get("measurement_provenance"):
                    row.update({key: copy.deepcopy(feature[key]) for key in
                                ("measurement_provenance", "source_window", "geometry_mask", "source_track_id")})
                    row.update(assessment_status="measurement_only", full_source_rule_satisfied=False,
                               label_zh="模型估计 · " + row["label_zh"],
                               reason_zh="单目模型估计的二维步伐几何；关键点置信度与可见性未知，不确认真实触地、受力或技术等级。")
                result["movement"].append(row)


def _preparation_joint_observations(episodes: list[Mapping[str, Any]], result: dict[str, list[dict]]) -> None:
    """Expose actual local joint measurements even when the wrist is hidden.

    Consume the same validated frame/reference windows as the Word-card view.
    Each window keeps its own summary; no full-stage delta crosses an occlusion.
    """
    by_id = {episode["episode_id"]: episode for episode in episodes}
    for observation in motion_joint_observations(episodes):
        if observation["phase"] != "preparation":
            continue
        name = observation["feature_name"]
        for window in observation["windows"]:
            row = _criterion(f"preparation_joint_{name}", observation["label_zh"],
                episode=by_id[observation["episode_id"]], window=window,
                value=window["mean" if name.endswith("fraction") else "median"], unit=observation["unit"],
                reason="估计准备段内同一球员、同一坐标参照的连续局部测量；不证明原卡正式阶段、真实承重、盯球或技术达标。",
                rule=observation["definition_zh"] + "仅报告实测值，尚缺正式阶段与完整等级依据，不产生准备分。",
                source="action_recognition.motion_analysis.episodes.process_motion.source_observations")
            row.update({"feature_name": name, "observation_id": observation["observation_id"],
                "person_track_id": observation["person_track_id"], "source_frames": window["source_frames"],
                "sample_count": window["sample_count"], "reference": window["reference"],
                "statistics": {key: window[key] for key in ("start", "end", "delta", "min", "max", "median", "mean", "range")},
                "window_scope": "continuous_local_window_in_estimated_preparation",
                "measurement_semantics": "joint_specific_2d_measurements_not_technical_grade",
                "assessment_status": "measurement_only", "full_source_rule_satisfied": False})
            if observation.get("measurement_provenance"):
                row["measurement_provenance"] = observation["measurement_provenance"]
                row["reason_zh"] = observation["measurement_provenance"]["reason_zh"] + row["reason_zh"]
                for edge in ("start", "end"):
                    row[f"source_binding_{edge}"] = window[f"source_binding_{edge}"]
                row["source_time_origin_ms"] = window["source_time_origin_ms"]
            result["preparation"].append(row)


def _display_criteria(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bound unscored detail only after full-input statuses/counts are made."""
    by_feature: dict[str, list[int]] = {}
    keep = set()
    for index, row in enumerate(rows):
        if row["id"].startswith(("footwork_", "preparation_joint_", "preparation_candidate_", "preparation_translation_")) and row["score"] is None:
            by_feature.setdefault(row["id"], []).append(index)
        else:
            keep.add(index)
    for indices in by_feature.values():
        if len(indices) <= MAX_FOOTWORK_OBSERVATIONS_PER_FEATURE:
            keep.update(indices)
        else:
            keep.update(indices[round(i * (len(indices) - 1) / (MAX_FOOTWORK_OBSERVATIONS_PER_FEATURE - 1))]
                        for i in range(MAX_FOOTWORK_OBSERVATIONS_PER_FEATURE))
    selected = [row for index, row in enumerate(rows) if index in keep]
    return selected, {"full_count": len(rows), "returned_count": len(selected),
                      "is_truncated": len(selected) < len(rows),
                      "policy": "all_scoring_criteria_and_uniform_time_ordered_unscored_measurements_per_feature",
                      "max_footwork_observations_per_feature": MAX_FOOTWORK_OBSERVATIONS_PER_FEATURE,
                      "count_scope": "all_validated_input_episodes_before_display_sampling"}


def build_process_evaluation(summary: Mapping[str, Any], indicator_feature_records: Iterable[Mapping[str, Any]] = (),
                             *, footwork_review: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Add an auditable process report without changing historical score fields."""
    criteria: dict[str, list[dict]] = {identifier: [] for identifier, _, _ in _DIMENSIONS}
    episodes = _episodes(summary)
    for episode in episodes:
        _motion_criteria(episode, criteria)
    _preparation_joint_observations(episodes, criteria)
    criteria["preparation"].extend(preparation_candidate_observations(episodes))
    criteria["preparation"].extend(preparation_translation_observations(episodes))
    video_id = summary.get("job_id") or _obj(summary.get("minimum_scoring_loop")).get("video_id")
    if isinstance(video_id, str) and video_id:
        for row in criteria["preparation"]:
            if row.get("measurement_semantics"):
                row["video_id"] = video_id
    records = [row for row in indicator_feature_records if isinstance(row, Mapping)]
    _movement_criteria(summary, records, _obj(footwork_review), criteria)
    _movement_observations(summary, records, _obj(footwork_review), criteria)
    episodes_by_id = {episode["episode_id"]: episode for episode in episodes}
    for episode in episodes:
        process = _obj(episode.get("process_motion"))
        transition = _obj(process.get("transition_from_previous"))
        previous = episodes_by_id.get(transition.get("previous_episode_id"))
        spacing = _num(transition.get("interval_ms"))
        if (process.get("version") in SUPPORTED_MOTION_VERSIONS and transition.get("status") == "measured"
                and transition.get("semantics") == "observed_inter_motion_interval_not_readiness"
                and previous is not None and previous["person_track_id"] == episode["person_track_id"]
                and spacing is not None and spacing >= 0
                and transition.get("start_ms") == previous["end_ms"]
                and transition.get("end_ms") == episode["start_ms"]
                and spacing == episode["start_ms"] - previous["end_ms"]):
            row = _criterion("between_motion_interval", "相邻候选动作的时间间隔", episode=episode, window=transition,
                value=spacing, unit="ms", reason=f"当前候选开始距上一候选结束 {spacing:g} 毫秒；这是视频时间间隔，不等于回位用时、准备就绪或真实两次击球间隔。",
                rule="同一跟踪主体的当前候选开始时间 − 上一候选结束时间；未确认中间回位动作和来球，不作快慢评分。",
                source="action_recognition.motion_analysis.episodes.process_motion.transition_from_previous")
            row["related_episode_id"] = previous["episode_id"]
            criteria["transition"].append(row)
    fallbacks = {
        "movement_target_alignment": ("移动目标方向", "缺少当前坐标测量、独立审核的目标方向或可绑定的步伐事件；不能用自身移动方向代替目标。"),
        "preparation_duration": ("引拍阶段持续时间", "缺少连续的引拍阶段观测。"),
        "preparation_direction": ("引拍腕部运动方向", "缺少引拍方向实测。"),
        "acceleration_trend": ("挥拍加速趋势参考", "缺少连续腕速加速阶段。"),
        "swing_direction": ("加速挥拍腕部运动方向", "缺少加速阶段的方向测量。"),
        "swing_direction_consistency": ("挥拍路径方向一致性", "缺少加速阶段的连续方向路径测量。"),
        "swing_direction_reversals": ("挥拍路径局部反向次数", "缺少加速阶段的局部转向测量。"),
        "hip_to_shoulder_peak_delay": ("髋至肩二维运动峰值时差", "缺少可定位的髋肩峰值。"),
        "shoulder_to_wrist_peak_delay": ("肩至腕二维运动峰值时差", "缺少可定位的肩腕峰值。"),
        "confirmed_contact": ("真实触球证据", "动作候选、球拍可见或腕速峰值都不能证明触球；当前未提供经验证的触球事件。"),
        "contact_posture_control": ("触球时姿态控制", "尚未将姿态稳定、身体控制测量绑定到经验证触球时段；准备或挥拍候选不能直接代替触球时段。"),
        "racket_face_control": ("触球时拍面与球拍控制", "尚未实现经验证触球时段内的拍面与球拍控制评价；腕部关键点不能代替拍面测量。"),
        "deceleration_trend": ("随挥减速趋势参考", "缺少连续随挥腕速曲线。"),
        "terminal_speed_ratio": ("随挥末端与峰值腕速之比", "缺少连续随挥末端和峰值腕速。"),
        "follow_duration": ("随挥阶段持续时间", "缺少完整随挥阶段。"),
        "between_motion_interval": ("相邻候选动作的时间间隔", "至少需要同一主体的两段独立动作；真实回位衔接还需中间姿态和来球证据。"),
    }
    dimensions = []
    for identifier, label, rules in _DIMENSIONS:
        rows = criteria[identifier]
        for rule_id in rules:
            if not any(row["id"] == rule_id for row in rows):
                name, reason = fallbacks[rule_id]
                rows.append(_criterion(rule_id, name, reason=reason, rule="缺失证据不补零、不代入其他维度，不生成技术分。", source="unavailable"))
        measured = [row for row in rows if row["status"] == "measured"]
        scores = [row["score"] for row in rows if row["score"] is not None]
        scored_ids = {row["id"] for row in rows if row["score"] is not None}
        rule_count = len(set(rules) | {row["id"] for row in rows})
        # Each scored dimension currently has exactly one explicit scoring
        # rule. No missing-rule reweighting or cross-dimension mean exists.
        score = round(statistics.fmean(scores), 1) if scores else None
        count = len({row["episode_id"] for row in measured if row["episode_id"]})
        total_count = len({row["episode_id"] for row in rows if row["episode_id"]})
        scope = _score_scope(identifier, measured=bool(measured), score=score)
        if score is None and measured and any(row.get("measurement_provenance", {}).get("scoring_allowed") is False for row in measured):
            scope["status_reason_zh"] += "证据属性：已有单目模型估计的二维过程测量；关键点置信度和可见性未提供。"
        summary_zh = (f"子项参考 {score:g}/100；{len(scored_ids)}/{rule_count} 项规则计分，{len(scores)}/{total_count} 段样本计分；不是整维技术分。" if score is not None
                      else f"已观察 {len(measured)} 项过程测量，{count} 段样本。" + scope["status_reason_zh"] if measured
                      else scope["status_reason_zh"])
        displayed_rows, selection = _display_criteria(rows)
        dimensions.append({"id": identifier, "name_zh": label, "score": score,
            "status": "measured" if score is not None and all(row["status"] == "measured" for row in rows) else "partial" if measured else "unavailable",
            "summary_zh": summary_zh, "criteria": displayed_rows, "criteria_selection": selection, **scope,
            "source_basis": source_basis_for_dimension(identifier),
            "scored_rule_count": len(scored_ids), "total_rule_count": rule_count,
            "scoring_rule_count": len(set(rules) & _SCORING_RULES),
            "sample_count": count, "scored_sample_count": len(scores), "total_sample_count": total_count,
            "measured_criterion_count": len(measured),
            "aggregation": "mean_of_independent_samples_for_one_explicit_rule" if scores else "none"})
        dimensions[-1].update(dimension_scoring_audit(identifier, displayed_rows))
        legacy_scores = [row["legacy_diagnostic"]["score"] for row in rows
                         if row.get("legacy_diagnostic", {}).get("score") is not None]
        if legacy_scores:
            dimensions[-1]["legacy_diagnostic"] = {"version": LEGACY_VERSION,
                "score": round(statistics.fmean(legacy_scores), 1), "sample_count": len(legacy_scores),
                "semantics": "selection_biased_motion_curve_diagnostic", "is_technical_quality": False,
                "contributes_to_current_score": False}
        if identifier == "movement":
            source_review = _obj(footwork_review)
            returned_count = len(_rows(source_review.get("episodes")))
            available_count = _num(source_review.get("episode_count"))
            available_count = (int(available_count) if available_count is not None and available_count.is_integer()
                               and available_count >= returned_count else returned_count)
            dimensions[-1]["measurement_input_scope"] = {
                "source": "footwork_review", "available_event_count": available_count,
                "returned_event_count": returned_count, "is_truncated": available_count > returned_count,
                "reason_zh": "步伐数量基于已读取并通过验证的事件；展示采样不改变这些统计，不将未读取事件记为已测。"}
    return {"version": PROCESS_EVALUATION_VERSION, "semantics": PROCESS_SEMANTICS,
        "is_calibrated": False, "overall_score": None, "dimensions": dimensions,
        "relative_reference": build_relative_reference(dimensions),
        "display_dimension_ids": list(DIMENSION_NAMES),
        "supplementary_dimension_ids": ["transition"],
        "summary_zh": "按动作过程分为五个训练维度，298 项技术指标每项仅归一组。腕速加减速已退出评分并保留历史诊断；已有测量、有效计分与规则缺口分别报告。",
        "overall_reason_zh": "当前仅纳入独立目标方向规则；缺测维度为空，只聚合已有有效规则的维度，不代表完整五维技术能力。腕速旧值与旧中性参考保留为历史诊断，已退出当前成绩。",
        "scored_dimension_count": sum(row["score"] is not None for row in dimensions),
        "total_dimension_count": 6, "episode_count": len(episodes),
        "limitations_zh": [_TREND_LIMITATION, "引拍、挥拍方向与肩髋腕峰序是二维过程观察，不能代替球场方向、实际受力或技术正确性。",
                           "触球控制关注触球姿态、拍面、球拍与球的关系；出球落点和目标命中率属于另行定义的结果统计，不是本轴的统一门槛。",
                           "指标数量仅表示分组范围，不代表本次全部测得或完成评分；规则尚未明确或阶段边界缺失时，仍保留相应限制。"],
        "rule_origin": "explicit_engineering_trial_not_word_rubric_or_coach_calibration"}
