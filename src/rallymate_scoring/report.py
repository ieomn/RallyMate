from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_LABELS = {"ready": "证据就绪", "partial": "部分可用", "blocked": "证据不足"}
GRANULARITY_LABELS = {
    "supported": "结构支持",
    "partial": "部分支持",
    "unsupported": "结构不支持",
}
BLOCKER_LABELS = {
    "event_segmentation_not_implemented": "缺少 GS/FS 事件切分",
    "metric_feature_calibration_not_implemented": "缺少指标特征函数与真值阈值",
    "dedicated_ball_trajectory_missing": "缺少网球专项球轨迹",
    "racket_keypoints_missing": "缺少球拍关键点",
    "metric_court_calibration_required": "缺少米制场地标定",
    "production_temporal_tracker_missing": "缺少生产级连续跟踪",
    "pose_joint_not_in_coco17": "所需关节不在 COCO-17",
    "observed_evidence_below_threshold": "本视频证据覆盖不足",
    "event_ground_truth_required": "候选事件已有，缺少人工事件真值评测",
    "feature_error_not_evaluated": "特征已实现，缺少人工关键点误差评测",
    "coach_calibration_missing": "缺少多教练标定数据",
    "independent_test_missing": "尚未通过独立测试",
    "reported_scored_status_rejected_without_F4": "上游评分状态与 F4 成熟度不一致，已拒绝",
    "trusted_calibration_not_loaded_or_no_scored_event": "未加载受信标定或本任务没有可评分事件",
}
MEASUREMENT_LABELS = {
    "measured": "特征已测量",
    "unavailable": "观测不足",
    "not_in_minimum_scoring_loop": "不在本轮闭环",
}
SCORING_LABELS = {
    "scored": "已评分",
    "calibration_required": "待教练标定",
    "unavailable": "不可评价",
}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _number(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _metric(label: str, value: str, note: str = "") -> str:
    return (
        '<div class="metric"><span>'
        + _e(label)
        + "</span><strong>"
        + _e(value)
        + "</strong><small>"
        + _e(note)
        + "</small></div>"
    )


def _bar(label: str, value: float, detail: str = "") -> str:
    percent = max(0.0, min(100.0, value * 100))
    tone = "good" if percent >= 75 else "warn" if percent >= 40 else "bad"
    return f"""
    <div class="bar-row"><div><span>{_e(label)}</span><b>{percent:.1f}%</b></div>
    <div class="bar"><i class="{tone}" style="width:{percent:.1f}%"></i></div>
    <small>{_e(detail)}</small></div>"""


def build_analysis_report_html(
    summary: dict[str, Any], readiness: dict[str, Any]
) -> str:
    job_id = str(summary.get("job_id", "unknown"))
    video = summary.get("input", {}).get("video", {})
    processing = summary.get("processing", {})
    coverage = summary.get("coverage", {})
    quality = summary.get("quality", {})
    runtime = summary.get("runtime", {})
    readiness_summary = readiness.get("summary", {})
    observed = readiness.get("observed_model_evidence", {})
    granularity = readiness_summary.get("model_granularity", {})
    evidence = readiness_summary.get("observed_evidence", {})
    pose_assessment = readiness.get("pose_assessment", {})
    indicator_results = readiness.get("indicator_results", [])
    minimum_loop = readiness_summary.get("minimum_scoring_loop", {})
    calculation_readiness = summary.get("calculation_readiness", {})
    if not isinstance(calculation_readiness, dict):
        calculation_readiness = {}
    calculated_indicator_count = int(
        calculation_readiness.get("indicator_with_measured_candidate_count", 0)
    )
    calculation_target_count = int(
        calculation_readiness.get("registry_indicator_count", 0)
    )
    calculation_instance_text = (
        f"{int(calculation_readiness.get('measured_indicator_event_instance_count', 0))}/"
        f"{int(calculation_readiness.get('indicator_event_instance_count', 0))} 指标事件实例"
    )
    measurement_portfolio = summary.get("measurement_portfolio", {})
    if not isinstance(measurement_portfolio, dict):
        measurement_portfolio = {}
    portfolio_measured_count = int(
        measurement_portfolio.get("measured_indicator_count", calculated_indicator_count)
    )
    portfolio_target_count = int(
        measurement_portfolio.get("registry_indicator_count", calculation_target_count)
    )
    scoring_cycle = summary.get("scoring_cycle_measurement", {})
    if not isinstance(scoring_cycle, dict):
        scoring_cycle = {}
    linked_cycle_count = int(scoring_cycle.get("linked_cycle_count", 0))
    complete_cycle_count = int(scoring_cycle.get("complete_cycle_count", 0))
    best_cycle_measured_count = int(
        scoring_cycle.get("best_cycle_measured_indicator_count", 0)
    )
    best_cycle_scoring_gate_count = int(
        scoring_cycle.get("best_cycle_scoring_gate_passed_indicator_count", 0)
    )
    total_indicator_count = int(readiness_summary.get("indicator_count", len(indicator_results)))
    loop_indicator_count = int(minimum_loop.get("indicator_count", 0))
    recognized_loop_count = int(
        minimum_loop.get("recognized_indicator_count", loop_indicator_count)
    )
    outside_loop_count = int(
        minimum_loop.get(
            "not_in_loop_indicator_count",
            max(total_indicator_count - recognized_loop_count, 0),
        )
    )
    measured_indicator_count = int(minimum_loop.get("measured_indicator_count", 0))
    scored_indicator_count = int(minimum_loop.get("scored_indicator_count", 0))
    formal_grade_status = str(
        minimum_loop.get("formal_grade_status", "unavailable")
    )
    grade_counts = minimum_loop.get("grade_counts", {})
    if not isinstance(grade_counts, dict):
        grade_counts = {}
    maturity_counts = minimum_loop.get("feasibility_levels", {})
    if not isinstance(maturity_counts, dict):
        maturity_counts = {}
    if not maturity_counts and minimum_loop.get("feasibility_level"):
        maturity_counts = {
            str(minimum_loop["feasibility_level"]): recognized_loop_count
        }
    maturity_display = " / ".join(
        f"{level}×{maturity_counts[level]}" for level in sorted(maturity_counts)
    ) or "成熟度未记录"
    loop_registry = minimum_loop.get("registry", {})
    if not isinstance(loop_registry, dict):
        loop_registry = {}
    loop_registry_version = loop_registry.get("version") or "未记录"
    loop_source = minimum_loop.get("source", {})
    if not isinstance(loop_source, dict):
        loop_source = {}
    registry_trace = " · ".join(
        str(value)
        for value in (
            loop_registry.get("path"),
            loop_registry.get("sha256"),
            loop_registry.get("read_status"),
        )
        if value
    ) or "未记录"
    source_trace = " · ".join(
        str(value)
        for value in (
            loop_source.get("pose_backend"),
            loop_source.get("pose_profile"),
            loop_source.get("event_version"),
            loop_source.get("feature_version"),
        )
        if value
    ) or "未记录"
    loop_display = (
        f"{measured_indicator_count}/{loop_indicator_count}"
        if loop_indicator_count
        else "未执行"
    )

    headline_metrics = "".join(
        [
            _metric("处理状态", "成功", f"任务 {job_id[:8]}"),
            _metric("处理帧数", f"{int(processing.get('processed_frames', 0)):,}", "frame_stride = 1"),
            _metric("GPU 推理耗时", f"{_number(processing.get('elapsed_seconds'))} s", f"{_number(processing.get('effective_processed_fps'))} fps"),
            _metric(
                "闭环指标已测量",
                loop_display,
                (
                    f"{maturity_display}；事件级 A～E，无总分"
                    if formal_grade_status == "scored"
                    else f"{maturity_display}；不等于 A～E"
                ),
            ),
        ]
    )

    coverage_bars = "".join(
        [
            _bar("主球员 Pose 轨迹", float(observed.get("primary_pose_track_fraction", 0)), f"{observed.get('pose_track_count', 0)} 个 pose track"),
            _bar("比赛球帧覆盖", float(coverage.get("ball_frame_fraction", 0)), f"最长单轨迹 {_pct(observed.get('ball_longest_track_fraction'))} · {observed.get('ball_track_count', 0)} 条轨迹"),
            _bar("球拍帧覆盖", float(coverage.get("racket_frame_fraction", 0)), f"最长单轨迹 {_pct(observed.get('racket_longest_track_fraction'))} · {observed.get('racket_track_count', 0)} 条轨迹"),
            _bar("场地区域", float(coverage.get("court_detected_fraction", 0)), f"米制标定 {_pct(coverage.get('court_calibrated_fraction'))}"),
        ]
    )

    granularity_cards = "".join(
        [
            _metric("结构支持", str(granularity.get("supported", 0)), "以 pose-only 为主"),
            _metric("部分支持", str(granularity.get("partial", 0)), "仍需球轨迹/场地/跟踪"),
            _metric("结构不支持", str(granularity.get("unsupported", 0)), "主要缺少球拍关键点"),
            _metric("本视频证据就绪", str(evidence.get("ready", 0)), "不等于最终可评分"),
        ]
    )

    matrix_rows = []
    for item in readiness.get("capability_matrix", []):
        matrix_rows.append(
            "<tr>"
            f"<td><strong>{_e(item.get('capability'))}</strong></td>"
            f"<td>{_e(item.get('current_output'))}</td>"
            f"<td>{_e(item.get('granularity'))}</td>"
            f"<td>{_e(item.get('affected_indicators'))}</td>"
            f"<td><code>{_e(item.get('next_action'))}</code></td>"
            "</tr>"
        )

    joint_rows = []
    for joint_id, value in observed.get("primary_pose_joint_valid_fraction", {}).items():
        percent = float(value) * 100
        state = "good" if percent >= 75 else "warn" if percent >= 40 else "bad"
        joint_rows.append(
            f'<div class="joint"><span>{_e(joint_id)}</span><b class="{state}">{percent:.1f}%</b></div>'
        )

    indicator_rows = []
    for result in indicator_results:
        blockers = [BLOCKER_LABELS.get(item, item) for item in result.get("blockers", [])]
        evidence_status = result.get("observed_evidence_status", "blocked")
        model_granularity = result.get("model_granularity", "unsupported")
        measurement_status = result.get(
            "measurement_status", "not_in_minimum_scoring_loop"
        )
        scoring_status = result.get("scoring_status", "unavailable")
        result_grade_counts = result.get("grade_counts", {})
        if not isinstance(result_grade_counts, dict):
            result_grade_counts = {}
        feasibility_level = result.get("feasibility_level")
        if feasibility_level:
            state_cell = (
                f'<span class="pill measured">{_e(feasibility_level)} · '
                f'{_e(MEASUREMENT_LABELS.get(measurement_status, measurement_status))}</span>'
                f'<small>{_e(SCORING_LABELS.get(scoring_status, scoring_status))} · '
                f'{_e(result.get("measured_event_instances"))}/'
                f'{_e(result.get("candidate_event_instances"))} 个候选事件实例有效'
                f'{" · 事件级等级 " + _e(result_grade_counts) if result_grade_counts else ""}</small>'
            )
        else:
            state_cell = (
                '<span class="pill unavailable">不在本轮最小闭环</span>'
                '<small>状态为 unavailable，不显示数字 0</small>'
            )
        search = " ".join(
            [
                str(result.get("indicator_id", "")),
                str(result.get("indicator_name", "")),
                str(result.get("stage_code", "")),
                " ".join(blockers),
            ]
        ).lower()
        indicator_rows.append(
            f"""<tr class="indicator" data-domain="{_e(result.get('domain'))}" data-status="{_e(evidence_status)}" data-granularity="{_e(model_granularity)}" data-search="{_e(search)}">
            <td><code>{_e(result.get('indicator_id'))}</code><br><strong>{_e(result.get('indicator_name'))}</strong><small>{_e(result.get('stage_code'))}</small></td>
            <td><span class="pill {evidence_status}">{_e(STATUS_LABELS.get(evidence_status, evidence_status))}</span><br><b>{_pct(result.get('observed_evidence'))}</b></td>
            <td><span class="pill {model_granularity}">{_e(GRANULARITY_LABELS.get(model_granularity, model_granularity))}</span></td>
            <td>{state_cell}</td>
            <td><ul>{''.join(f'<li>{_e(blocker)}</li>' for blocker in blockers)}</ul></td>
            <td><details><summary>查看规则</summary><p><b>所需点：</b>{_e(result.get('required_points_contract'))}</p><p><b>计算契约：</b>{_e(result.get('calculation_contract'))}</p><p><b>依赖：</b>{_e(', '.join(result.get('dependencies', [])))}</p></details></td>
            </tr>"""
        )

    warning_items = "".join(
        f"<li>{_e(item)}</li>" for item in quality.get("metadata_warnings", [])
    ) or "<li>无拍摄规格告警</li>"
    generated_at = datetime.now(timezone.utc).isoformat()
    if loop_indicator_count:
        if formal_grade_status == "scored" and scored_indicator_count:
            verdict = (
                f"{loop_indicator_count} 项指标已接入闭环，"
                f"{scored_indicator_count} 项已有受信版本化标定的事件级 A～E；本轮不设计总分"
            )
        elif measured_indicator_count:
            verdict = (
                f"{loop_indicator_count} 项指标已接入闭环，"
                f"{measured_indicator_count} 项产生有效特征；正式 A～E 待教练标定"
            )
        else:
            verdict = (
                f"{loop_indicator_count} 项指标已接入闭环，但当前观测不足；"
                "没有评分不等于零分"
            )
        if formal_grade_status == "scored" and scored_indicator_count:
            conclusion = (
                f"本任务有 {measured_indicator_count}/{loop_indicator_count} 项闭环指标至少产生了有效特征实例，"
                f"其中 {scored_indicator_count} 项通过 F4、独立测试与运维受信晋级账本输出事件级单指标 A～E，"
                f"等级分布为 {grade_counts or '无'}；注册表成熟度为 {maturity_display}"
                f"（注册表 {loop_registry_version}）。本轮不设计总分，其余事件实例仍保持 "
                "calibration_required 或 unavailable。"
                f"其余 {outside_loop_count} 项不在本轮评分闭环，不能把未实现解释成动作得了零分。"
            )
        else:
            conclusion = (
                f"本任务有 {measured_indicator_count}/{loop_indicator_count} 项闭环指标至少产生了有效特征实例，"
                f"注册表成熟度为 {maturity_display}（注册表 {loop_registry_version}）。"
                "FS01、FS02、FS09 当前是规则候选事件，不是人工真值；这些指标在人工事件、关键点误差、"
                "多教练标定和独立测试完成前保持 calibration_required 或 unavailable。"
                f"其余 {outside_loop_count} 项不在本轮评分闭环，不能把未实现解释成动作得了零分。"
            )
    else:
        verdict = "本任务未执行版本化指标评分闭环；没有评分不等于零分"
        conclusion = (
            "该任务仅有静态颗粒度审计，未产生版本化指标事件和特征产物。"
            "所有未评价项使用 unavailable / not_in_minimum_scoring_loop 语义。"
        )
    if formal_grade_status == "scored" and scored_indicator_count:
        priority_items = (
            "<li><strong>漂移监控：</strong>按机位、球员和事件类型持续监控特征分布与不可用率。</li>"
            "<li><strong>真值复测：</strong>按预注册协议定期复算 Event F1、特征误差和独立测试指标。</li>"
            "<li><strong>标定治理：</strong>审计受信晋级账本、资产哈希、审批记录和模型版本；变更后重新晋级。</li>"
            "<li><strong>结果边界：</strong>仅解释事件级单指标 A～E，不计算跨事件、跨指标总分。</li>"
            f"<li><strong>本轮边界：</strong>不扩展 Ball/Racket/Court 或其余 {outside_loop_count} 项评分。</li>"
        )
    else:
        priority_items = (
            "<li><strong>事件真值：</strong>完整视频独立标注 FS01/FS02/FS09，计算 Event F1、Segment IoU 和 Boundary MAE。</li>"
            "<li><strong>关键点真值：</strong>裁决肩、髋、膝、踝和足部点，计算特征 MAE/P95/Bias 与误差预算。</li>"
            "<li><strong>教练标定：</strong>收集多教练 A～E 或排序，先审查一致性，再创建版本化阈值或序数模型。</li>"
            "<li><strong>独立测试：</strong>按球员、场次和机位隔离测试后才允许 F4。</li>"
            f"<li><strong>本轮边界：</strong>不扩展 Ball/Racket/Court 或其余 {outside_loop_count} 项评分。</li>"
        )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RallyMate 完整分析报告 · {_e(job_id)}</title>
<style>
:root{{--ink:#152019;--muted:#66716a;--line:#d8ddd8;--paper:#f4f5f1;--white:#fff;--green:#207a4b;--amber:#b66b00;--red:#b83f36;--blue:#2c5fa8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif}}main{{max-width:1280px;margin:auto;padding:32px 24px 80px}}
header{{border-bottom:3px solid var(--ink);padding-bottom:22px}}header small,.kicker{{font:700 11px ui-monospace,Consolas,monospace;letter-spacing:.1em;color:var(--muted)}}h1{{font-size:34px;margin:8px 0}}h2{{font-size:22px;margin:0 0 16px}}h3{{font-size:16px}}p{{color:var(--muted)}}
.verdict{{background:var(--ink);color:white;padding:22px 26px;margin:24px 0}}.verdict strong{{color:#c8ff70}}.verdict p{{color:#d7ddd9;margin:8px 0 0}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);background:var(--line);gap:1px}}.metric{{background:var(--white);padding:18px;min-height:112px;display:flex;flex-direction:column}}.metric span{{font-size:11px;color:var(--muted)}}.metric strong{{font-size:27px;margin-top:8px}}.metric small{{color:var(--muted);margin-top:auto}}
section{{margin-top:36px;background:var(--white);border:1px solid var(--line);padding:24px}}.two{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.bar-row{{margin:14px 0}}.bar-row>div:first-child{{display:flex;justify-content:space-between}}.bar{{height:7px;background:#e7eae7;margin:6px 0}}.bar i{{display:block;height:100%}}.good{{color:var(--green)!important}}.warn{{color:var(--amber)!important}}.bad{{color:var(--red)!important}}.bar i.good{{background:var(--green)}}.bar i.warn{{background:var(--amber)}}.bar i.bad{{background:var(--red)}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:11px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:11px;color:var(--muted);background:#f5f6f3;position:sticky;top:0}}td code{{font-size:11px}}td small{{display:block;color:var(--muted)}}td ul{{margin:0;padding-left:17px}}.no{{color:var(--red);font-weight:700}}
.joint-grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}}.joint{{border:1px solid var(--line);padding:8px;display:flex;justify-content:space-between;font-family:ui-monospace,Consolas,monospace}}
.pill{{display:inline-block;padding:3px 7px;border:1px solid currentColor;font-size:11px}}.pill.ready,.pill.supported,.pill.measured{{color:var(--green)}}.pill.partial{{color:var(--amber)}}.pill.blocked,.pill.unsupported,.pill.unavailable{{color:var(--red)}}
.filters{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}}.filters input,.filters select{{padding:8px 10px;border:1px solid var(--line);background:white;font:inherit}}.filters input{{min-width:280px}}#visible-count{{margin-left:auto;color:var(--muted)}}
.artifacts a{{display:inline-block;margin-right:16px;color:var(--blue)}}details summary{{cursor:pointer;color:var(--blue)}}details p{{font-size:12px}}footer{{margin-top:30px;color:var(--muted);font-size:11px}}
@media(max-width:800px){{.metrics{{grid-template-columns:1fr 1fr}}.two{{grid-template-columns:1fr}}.joint-grid{{grid-template-columns:repeat(3,1fr)}}main{{padding:20px 12px}}table{{font-size:12px}}}}
@media print{{.filters{{display:none}}body{{background:white}}main{{max-width:none}}section{{break-inside:avoid}}}}
</style></head><body><main>
<header><small>RALLYMATE · MODEL INFERENCE & SCORING GRANULARITY</small><h1>模型推理与评分颗粒度完整分析报告</h1><p>任务 {_e(job_id)} · 生成时间 {_e(generated_at)}</p></header>
<div class="verdict"><strong>核心结论：{_e(verdict)}</strong><p>{_e(conclusion)}</p></div>
<div class="metrics">{headline_metrics}</div>

<section><span class="kicker">01 · INPUT & RUNTIME</span><h2>输入视频与运行环境</h2><div class="two"><div>
<table><tr><td>视频规格</td><td>{_e(video.get('width'))} × {_e(video.get('height'))} / {_e(video.get('fps'))} fps</td></tr>
<tr><td>视频总帧/时长</td><td>{_e(video.get('frame_count'))} / {_number(float(video.get('duration_ms', 0))/1000)} s</td></tr>
<tr><td>推理设备</td><td>{_e(runtime.get('cuda_device_name') or runtime.get('device_used'))}</td></tr>
<tr><td>模型</td><td>Detect: {_e(Path(str(summary.get('models',{}).get('detect',''))).name)}<br>Pose: {_e(Path(str(summary.get('models',{}).get('pose',''))).name)}</td></tr></table></div>
<div><h3>拍摄质量告警</h3><ul>{warning_items}</ul><p>亮度中位数 {_e(quality.get('brightness_median'))} · 对比度 {_e(quality.get('contrast_median'))} · 模糊分 {_e(quality.get('blur_score_median'))}</p></div></div></section>

<section><span class="kicker">02 · OBSERVED EVIDENCE</span><h2>本视频实际证据与连续性</h2><div class="two"><div>{coverage_bars}</div><div>
<table><tr><td>球最长缺失</td><td>{_e(observed.get('ball_max_missing_run_frames'))} 帧</td></tr><tr><td>球拍最长缺失</td><td>{_e(observed.get('racket_max_missing_run_frames'))} 帧</td></tr>
<tr><td>球轨迹数量</td><td>{_e(observed.get('ball_track_count'))}</td></tr><tr><td>球拍轨迹数量</td><td>{_e(observed.get('racket_track_count'))}</td></tr><tr><td>主 Pose Track ID</td><td>{_e(observed.get('primary_pose_track_id'))}</td></tr></table>
<p>帧覆盖率高不代表人工真值上的召回率；轨迹数量过多通常意味着误检或 ID 碎片。</p></div></div></section>

<section><span class="kicker">03 · MODEL GRANULARITY</span><h2>{total_indicator_count} 项模型颗粒度结论</h2><div class="metrics">{granularity_cards}</div>
<table><thead><tr><th>能力</th><th>当前输出</th><th>颗粒度判断</th><th>影响指标</th><th>下一步</th></tr></thead><tbody>{''.join(matrix_rows)}</tbody></table></section>

<section><span class="kicker">04 · POSE DETAIL</span><h2>主球员关键点有效率</h2><p>{_e(pose_assessment.get('reason'))}</p><div class="joint-grid">{''.join(joint_rows)}</div>
<p><strong>当前后端：</strong>{_e(summary.get('models',{}).get('pose_backend',{}).get('model_name') or Path(str(summary.get('models',{}).get('pose',''))).name)} · {_e(summary.get('models',{}).get('pose_format'))}。YOLO Pose 可作 COCO17 基线；RTMPose Halpe26 增加脚趾与脚跟，但映射或覆盖完整不等于像素精度已通过真值。</p></section>

<section><span class="kicker">05 · PRIORITY</span><h2>当前 {loop_indicator_count} 项闭环指标下一步</h2><p>闭环注册表：{_e(loop_registry_version)} · 成熟度：{_e(maturity_display)}。这里显示的是注册表声明、特征测量和受信标定运行状态，不是未经真值支持的准确率结论。</p><p><strong>本视频逐指标计算就绪：</strong>{calculated_indicator_count}/{calculation_target_count or loop_indicator_count} 项至少有一个 required-feature 完整的候选实例；{_e(calculation_instance_text)}。未达到的指标、测量失败原因和补拍/真值动作见 <a href="/v1/jobs/{_e(job_id)}/artifacts/calculation-readiness.json">calculation-readiness.json</a>；正式评分阻断与 F2 特征计算分开记录。</p><p><strong>可直接消费的代表测量：</strong>{portfolio_measured_count}/{portfolio_target_count or loop_indicator_count} 项。<a href="/v1/jobs/{_e(job_id)}/artifacts/indicator-measurement-portfolio.json">indicator-measurement-portfolio.json</a> 按观测质量而非运动表现，为每项选择一个代表候选并输出实际特征值、单位、置信度与证据帧；它不是 A～E，也不会从不同模型挑值。</p><p><strong>同一次动作周期：</strong>已闭合 {linked_cycle_count} 组 FS01→FS02→FS09，其中 {complete_cycle_count} 组在同一周期内 13 项全部可测；当前最佳周期为 {best_cycle_measured_count}/{portfolio_target_count or loop_indicator_count} 项特征完整、{best_cycle_scoring_gate_count}/{portfolio_target_count or loop_indicator_count} 项评分上下文通过。<a href="/v1/jobs/{_e(job_id)}/artifacts/scoring-cycle-measurement.json">scoring-cycle-measurement.json</a> 禁止跨动作拼接，仍不代表事件真值或正式等级。</p><details><summary>闭环追溯元数据</summary><p><b>注册表：</b>{_e(registry_trace)}</p><p><b>推理/事件/特征来源：</b>{_e(source_trace)}</p></details><ol>{priority_items}</ol></section>

<section><span class="kicker">06 · ALL INDICATORS</span><h2>{total_indicator_count} 项静态卡与 {loop_indicator_count} 项闭环指标实时状态</h2><p>{loop_indicator_count} 项运行时目标指标显示版本化成熟度、测量和标定状态；其余 {outside_loop_count} 项只保留静态结构审计并明确标记为“不在本轮闭环”。“证据就绪”仍不等于评分准确。</p>
<div class="filters"><input id="search" placeholder="搜索指标编号、名称或阻断原因"><select id="domain"><option value="">全部域</option><option>GS</option><option>FS</option></select>
<select id="status"><option value="">全部证据状态</option><option value="ready">证据就绪</option><option value="partial">部分可用</option><option value="blocked">证据不足</option></select>
<select id="granularity"><option value="">全部颗粒度</option><option value="supported">结构支持</option><option value="partial">部分支持</option><option value="unsupported">结构不支持</option></select><span id="visible-count"></span></div>
<div style="overflow:auto"><table><thead><tr><th>指标</th><th>本视频证据</th><th>模型颗粒度</th><th>测量 / 评分状态</th><th>阻断原因</th><th>规则详情</th></tr></thead><tbody id="indicators">{''.join(indicator_rows)}</tbody></table></div></section>

<section class="artifacts"><span class="kicker">07 · ARTIFACTS</span><h2>原始产物</h2><a href="/v1/jobs/{_e(job_id)}/artifacts/scoring-loop-report.html">版本化 Pose 指标报告</a><a href="/v1/jobs/{_e(job_id)}/artifacts/calculation-readiness.json">逐指标计算就绪</a><a href="/v1/jobs/{_e(job_id)}/artifacts/indicator-measurement-portfolio.json">13 项代表测量</a><a href="/v1/jobs/{_e(job_id)}/artifacts/scoring-cycle-measurement.json">同周期13项测量</a><a href="/v1/jobs/{_e(job_id)}/artifacts/indicator-features.jsonl">指标特征</a><a href="/v1/jobs/{_e(job_id)}/artifacts/scores.jsonl">安全评分状态</a><a href="/v1/jobs/{_e(job_id)}/artifacts/summary.json">summary.json</a><a href="/v1/jobs/{_e(job_id)}/artifacts/scoring-readiness.json">{total_indicator_count} 项静态审计</a><a href="/v1/jobs/{_e(job_id)}/artifacts/frames.jsonl">frames.jsonl</a><a href="/v1/jobs/{_e(job_id)}/artifacts/annotated.mp4">annotated.mp4</a></section>
<footer>报告使用 measured / calibration_required / unavailable / scored 语义；不将未评价显示成零分，不将覆盖率冒充准确率。</footer>
</main><script>
const rows=[...document.querySelectorAll('tr.indicator')], search=document.querySelector('#search'), domain=document.querySelector('#domain'), status=document.querySelector('#status'), granularity=document.querySelector('#granularity'), count=document.querySelector('#visible-count');
function filter(){{const q=search.value.trim().toLowerCase();let visible=0;rows.forEach(row=>{{const show=(!q||row.dataset.search.includes(q))&&(!domain.value||row.dataset.domain===domain.value)&&(!status.value||row.dataset.status===status.value)&&(!granularity.value||row.dataset.granularity===granularity.value);row.hidden=!show;if(show)visible++;}});count.textContent=`显示 ${{visible}} / ${{rows.length}} 项`;}}
[search,domain,status,granularity].forEach(node=>node.addEventListener('input',filter));filter();
</script></body></html>"""


def write_analysis_report(
    summary: dict[str, Any], readiness: dict[str, Any], output_path: str | Path
) -> Path:
    path = Path(output_path)
    path.write_text(
        build_analysis_report_html(summary, readiness), encoding="utf-8"
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate RallyMate HTML analysis report")
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    readiness = json.loads(args.readiness.read_text(encoding="utf-8"))
    write_analysis_report(summary, readiness, args.output)
    summary.setdefault("artifacts", {})["analysis_report_html"] = args.output.name
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output)


if __name__ == "__main__":
    main()
