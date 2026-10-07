"""Compose a replay-first report without turning evidence into technique grades.

Recognition, independent measurements and calibration are separate layers.
Recommendations point at observations to review, never infer a coaching error
from a low coverage/reference score. Historical artifacts are read-only inputs.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

REPORT_VERSION = "training-report-v1.0.0"
SCORE_SEMANTICS = "measurement_evidence_quality"
ROTATION_UNITS = {"shoulder_line_change_deg": "deg", "hip_line_change_deg": "deg",
                  "shoulder_hip_separation_change_deg": "deg", "shoulder_hip_separation_max_deg": "deg",
                  "peak_shoulder_angular_speed_deg_s": "deg/s", "peak_hip_angular_speed_deg_s": "deg/s"}


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(value) if math.isfinite(value) else None
        except OverflowError:
            pass
    return None


def _interval(row: Mapping[str, Any]) -> tuple[int | None, int | None]:
    start, end = _number(row.get("start_ms")), _number(row.get("end_ms"))
    if start is None or end is None or not 0 <= start < end:
        return None, None
    return int(start), int(end)


def _focus(identifier: str, title: str, text: str, *, basis: str,
           row: Mapping[str, Any] | None = None, metrics: list[str] | None = None,
           kind: str = "review", status: str = "available") -> dict[str, Any]:
    start, end = _interval(row or {})
    return {"id": identifier, "title_zh": title, "summary_zh": text,
            "basis_zh": basis, "kind": kind, "status": status,
            "start_ms": start, "end_ms": end, "metric_ids": metrics or [],
            "is_technical_error_diagnosis": False}


def _footwork_observations(review: Mapping[str, Any]) -> tuple[list[dict], int, int]:
    observations, measured, missing = [], 0, 0
    for episode in _rows(review.get("episodes")):
        for indicator in _rows(episode.get("indicators")):
            # The new independent contract may expose valid measurements even
            # when a sibling feature makes the legacy vector unavailable.
            independent = indicator.get("measurements")
            rows = _rows(independent if isinstance(independent, list) else indicator.get("features"))
            available = []
            for item in rows:
                valid = (item.get("status") == "measured" if isinstance(independent, list)
                         else indicator.get("feature_status") == "measured")
                if valid and _number(item.get("value")) is not None:
                    measured += 1
                    available.append(item)
                else:
                    missing += 1
            if available:
                observations.append({"episode": episode, "indicator": indicator,
                                     "measurements": available, "missing": len(rows) - len(available)})
    return observations, measured, missing


def _motion_episodes(families: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """A return-context projection may reference the same baseline episode."""
    episodes, seen = [], set()
    for family in families.values():
        for episode in _rows(_object(family).get("episodes")):
            start, end = _interval(episode)
            track = _number(episode.get("person_track_id"))
            identity = (("interval", track, start, end) if track is not None and start is not None
                        else ("id", episode["episode_id"]) if isinstance(episode.get("episode_id"), str)
                        else None)
            if identity is not None and identity in seen:
                continue
            if identity is not None:
                seen.add(identity)
            episodes.append(episode)
    return episodes


def _rotation_metric_names(container: Mapping[str, Any], episode: Mapping[str, Any]) -> list[str]:
    start, end = _interval(episode)
    if start is None:
        return []
    evidence = _object(container.get("metric_evidence"))
    names = []
    for name, value in _object(container.get("metrics")).items():
        detail = _object(evidence.get(name))
        a, b = _interval(detail)
        if (name in ROTATION_UNITS and _number(value) is not None and detail.get("status") == "measured"
                and detail.get("unit") == ROTATION_UNITS[name] and a is not None and start <= a < b <= end
                and ("value" not in detail or _number(detail.get("value")) == _number(value))):
            names.append(name)
    return names


def _rotation_local_windows(rotation: Mapping[str, Any], episode: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    windows = []
    start, end = _interval(episode)
    for window in _rows(rotation.get("local_windows")):
        a, b = _interval(window)
        count = _number(window.get("continuous_samples"))
        if (window.get("status") == "measured" and window.get("scope") == "continuous_local_window"
                and start is not None and a is not None and start <= a < b <= end and b - a >= 200
                and count is not None and count >= 7 and count.is_integer()
                and _rotation_metric_names(window, window)):
            windows.append(window)
    return windows


def _no_candidate_reason(summary: Mapping[str, Any], recognition: Mapping[str, Any]) -> str | None:
    """Describe recorded admission counts, never infer a continuous interval."""
    source = recognition if "candidate_count" in recognition else _object(summary.get("action_recognition"))
    if source.get("candidate_count") != 0 or isinstance(source.get("candidate_count"), bool):
        return None
    processing = _object(summary.get("processing"))
    diagnostics = _object(source.get("diagnostics"))
    total, samples = _number(processing.get("processed_frames")), _number(diagnostics.get("valid_pose_samples"))
    if (total is None or not total.is_integer() or total <= 0 or samples is None
            or not samples.is_integer() or not 0 <= samples <= total
            or _number(diagnostics.get("frame_count")) != total):
        return None
    reason = f"共处理 {int(total)} 帧，其中 {int(samples)} 帧满足动作识别入口条件"
    counts = _object(_object(processing.get("camera_motion")).get("status_counts"))
    values = [_number(value) for value in counts.values()]
    unavailable = _number(counts.get("unavailable", 0))
    if (counts and all(value is not None and value.is_integer() and value >= 0 for value in values)
            and sum(values) == total and unavailable is not None and unavailable > 0):
        reason += f"；{int(unavailable)} 帧相机校正证据不足"
    return reason + "。入口帧数不代表连续时长，未形成候选不代表没有该动作。"


def build_analysis_report(result: Mapping[str, Any], *, summary: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build an additive user report; never alter existing scores or artifacts."""
    training = _object(result.get("training_evaluation"))
    recognition = _object(result.get("action_recognition"))
    motion = _object(recognition.get("motion_analysis"))
    families = _object(motion.get("families"))
    episodes = _motion_episodes(families)
    summary = _object(summary)
    no_candidate_reason = _no_candidate_reason(summary, recognition) if not episodes else None
    footwork = _object(result.get("footwork_review"))
    foot_observations, foot_measured, foot_missing = _footwork_observations(footwork)
    rotation_observations = []
    rotation_measured = rotation_local = 0
    for episode in episodes:
        rotation = _object(episode.get("rotation_analysis"))
        if rotation.get("is_3d_rotation") is not False:
            continue
        measured_names = _rotation_metric_names(rotation, episode)
        local = _rotation_local_windows(rotation, episode)
        rotation_measured += len(measured_names)
        rotation_local += len(local)
        if measured_names or local:
            rotation_observations.append({"episode": episode, "rotation": rotation,
                                          "names": measured_names, "local": local})

    focus = []
    if foot_observations:
        # Prefer a partially measurable group: it gives a concrete review
        # target while explicitly explaining which evidence is absent.
        chosen = max(foot_observations, key=lambda item: (item["missing"] > 0, len(item["measurements"])))
        indicator, episode = chosen["indicator"], chosen["episode"]
        names = [str(item.get("name_zh") or item.get("label_zh") or item.get("feature_name"))
                 for item in chosen["measurements"][:2]]
        text = f"先回看{episode.get('name_zh') or '这段步伐'}，对照{'、'.join(names)}与动作发生顺序。"
        if chosen["missing"]:
            text += "同组仍有缺测项，已测部分可独立复核。"
        focus.append(_focus("footwork-review", "先看步伐的移动与稳定过程", text,
                            basis="建议来自实际测量与对应时间窗；尚未判定技术错误。", row=episode,
                            metrics=[str(indicator["indicator_id"])],
                            status="partial" if chosen["missing"] else "available"))
    elif footwork.get("episode_count", 0):
        focus.append(_focus("footwork-capture", "让双脚和髋部持续入镜",
                            "已找到步伐候选，但尚无可复核的单项数值；检查缺测原因，并保留完整准备、移动和恢复过程。",
                            basis="来自步伐候选的测量缺失，不能据此判断步伐好坏。", kind="capture", status="unavailable"))

    if rotation_observations:
        chosen = max(rotation_observations, key=lambda item: (len(item["names"]), len(item["local"])))
        local_only = not chosen["names"]
        window = chosen["local"][0] if local_only else _object(chosen["rotation"].get("metric_evidence")).get(chosen["names"][0], {})
        text = ("这段动作的整段覆盖不足，但有可靠的局部连续观测；定位后对照肩线或髋线在该区间的变化。"
                if local_only else "回看同一动作阶段内肩线、髋线的画面变化；结合阶段边界检查运动过程。")
        text += "这些角度是二维投影，不能用幅度大小判断转体是否正确。"
        focus.append(_focus("rotation-review", "分阶段复核肩髋变化", text,
                            basis="来自连续二维轴观测；不是三维转体或动力链诊断。",
                            row=window if _interval(window)[0] is not None else chosen["episode"],
                            metrics=chosen["names"], status="partial" if local_only else "available"))
    elif episodes:
        focus.append(_focus("rotation-capture", "检查肩髋是否被遮挡或重叠",
                            "当前挥拍片段尚无连续肩髋轴测量。展开转体明细查看原因，补录时保留肩髋与完整挥拍过程。",
                            basis="来自二维轴连续观测缺失，不代表转体不足。", row=episodes[0],
                            kind="capture", status="unavailable"))

    unknown = [episode for episode in episodes if episode.get("stroke_type") == "unclassified"
               or episode.get("classification") == "unclassified"
               or _object(episode.get("classification")).get("status") == "unclassified"
               or _object(episode.get("temporal_recognition")).get("classification_status") == "unclassified"]
    if unknown:
        focus.append(_focus("recognition-review", "确认这段挥拍的动作类型",
                            "运动片段已保留，持拍手或动作类型仍需回放确认；步伐和二维转体的独立测量继续可用。",
                            basis="动作识别不确定与测量缺失分开处理。", row=unknown[0], status="partial"))
    elif not focus:
        focus.append(_focus("capture-full-motion", "保留一段完整动作",
                            (no_candidate_reason or "当前没有可定位的独立测量。")
                            + "保持人物全身在画面内，录下准备、移动、挥拍和恢复过程后重新分析。",
                            basis="当前缺少可复核的动作窗口。", kind="capture", status="unavailable"))

    score = _number(training.get("score_0_to_100"))
    if training.get("score_semantics") != SCORE_SEMANTICS or training.get("available") is not True or score is None or not 0 <= score <= 100:
        score = None
    measured_indicators = training.get("evaluated_indicator_count", 0)
    independent_available = bool(foot_measured or rotation_measured or rotation_local)
    has_measurement = independent_available or training.get("available") is True
    summary = _object(summary)
    processing = _object(summary.get("processing"))
    source_timing = _object(processing.get("source_timing"))
    primary = _object(summary.get("primary_player"))
    video = _object(_object(summary.get("input")).get("video"))
    layers = [
        {"id": "capture", "label_zh": "视频与时间", "status": "available" if source_timing.get("timing_verified") is True else "unknown",
         "reason_zh": ("源视频时间已核验；缺口与失效镜头校正区间独立处理。" if source_timing.get("timing_verified") is True
                       else "当前报告未提供已核验的源时间状态；各项仍按自身时间证据判断。"), "source": "processing.source_timing"},
        {"id": "subject", "label_zh": "主体连续片段", "status": "available" if primary else "unknown",
         "reason_zh": "测量不跨主体切换、相机参考变化与时间缺口。", "source": "primary_player"},
        {"id": "recognition", "label_zh": "动作与阶段", "status": "partial" if unknown else "available" if episodes else "unavailable",
         "reason_zh": no_candidate_reason or "规则基线提出动作和阶段候选；候选中心不是确认触球。", "source": "action_recognition.motion_analysis"},
        {"id": "measurement", "label_zh": "逐项测量", "status": "available" if has_measurement else "unavailable",
         "reason_zh": "分别使用关节、连续时间窗与适用性检查；局部有效不等于整组可评分。", "source": "footwork_review;rotation_analysis"},
        {"id": "evaluation", "label_zh": "参考评价", "status": "available" if score is not None else "unavailable",
         "reason_zh": "参考分描述测量证据；技术分等待教练标定。", "source": "training_evaluation"},
    ]
    return {"version": REPORT_VERSION, "headline_zh": "训练报告已生成" if has_measurement else "分析完成，建议补充可测片段",
            "score_semantics": SCORE_SEMANTICS, "reference_score_0_to_100": score,
            "technical_score_0_to_100": None, "technical_score_status": "calibration_required",
            "focus_areas": focus[:3], "layers": layers,
            "measurement_summary": {"measured_indicator_count": measured_indicators,
                                    "footwork_measured_feature_instances": foot_measured,
                                    "footwork_missing_feature_instances": foot_missing,
                                    "rotation_measured_metric_instances": rotation_measured,
                                    "rotation_local_window_count": rotation_local,
                                    "scope": "returned_replay_episodes",
                                    "is_truncated": footwork.get("is_truncated") is True},
            "view_policy": {"global_view_required_for_projected_measurements": False,
                            "axis_rotation_3d_available": False,
                            "comparison_scope": "same_subject_same_continuous_window_same_phase"},
            "video_specs": {key: _number(video.get(key)) for key in ("width", "height", "fps", "frame_count", "duration_ms")},
            "recognition_backend": _object(motion.get("temporal_backend")),
            "recommendation_semantics": "evidence_linked_review_and_capture_not_technical_error_diagnosis"}
