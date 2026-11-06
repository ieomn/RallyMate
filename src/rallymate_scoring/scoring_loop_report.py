from __future__ import annotations

import html
import io
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator


SCORING_LOOP_REPORT_VERSION = "scoring-loop-report-v0.4.0"
_GRADES = frozenset({"A", "B", "C", "D", "E"})
MAX_REPORT_RECORD_BYTES = 64 * 1024 * 1024


class ReportRecordLimit(ValueError):
    """A single report fact exceeds the supported, explicit memory budget."""


def _h(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json_text(value: Any, *, max_chars: int = 40_000) -> str:
    """Project before serializing: truncating a giant serialized object is too late."""
    remaining = max(1, max_chars)
    omitted = False

    def project(item: Any, depth: int = 0) -> Any:
        nonlocal remaining, omitted
        if remaining <= 0 or depth >= 24:
            omitted = True
            return "…"
        remaining -= 1
        if isinstance(item, str):
            keep = min(len(item), max(0, remaining))
            remaining -= keep
            if keep < len(item):
                omitted = True
                return item[:keep] + "…"
            return item
        if item is None or isinstance(item, (bool, int, float)):
            remaining -= 24
            return item
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if remaining <= 0:
                    omitted = True
                    break
                # JSON artifacts have string keys. Avoid copying/sorting all
                # keys from an arbitrarily large diagnostics dictionary.
                projected_key = project(key if isinstance(key, str) else str(key), depth + 1)
                result[projected_key] = project(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            result = []
            for child in item:
                if remaining <= 0:
                    omitted = True
                    break
                result.append(project(child, depth + 1))
            return result
        omitted = True
        return f"<{type(item).__name__}>"

    rendered = json.dumps(project(value), ensure_ascii=False, sort_keys=True, indent=2)
    if omitted or len(rendered) > max_chars:
        return rendered[:max_chars] + "\n… [摘要截断；完整事实保留在机器产物中]"
    return rendered


def _number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "—"
        return f"{value:.8g}"
    return str(value)


def _series_summary(value: list[dict[str, Any]]) -> str:
    numeric_count = 0
    first_numeric: float | None = None
    last_numeric: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    first_timestamp: int | float | None = None
    last_timestamp: int | float | None = None
    for item in value:
        timestamp = item.get("timestamp_ms")
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            if first_timestamp is None:
                first_timestamp = timestamp
            last_timestamp = timestamp
        sample = item.get("value")
        if (
            not isinstance(sample, (int, float))
            or isinstance(sample, bool)
            or not math.isfinite(float(sample))
        ):
            continue
        numeric = float(sample)
        if first_numeric is None:
            first_numeric = numeric
        last_numeric = numeric
        minimum = numeric if minimum is None else min(minimum, numeric)
        maximum = numeric if maximum is None else max(maximum, numeric)
        numeric_count += 1
    parts = [f"series(n={len(value)}, numeric={numeric_count})"]
    if first_timestamp is not None:
        parts.append(f"t={_number(first_timestamp)}…{_number(last_timestamp)} ms")
    if first_numeric is not None:
        parts.append(
            "value="
            f"{_number(first_numeric)}…{_number(last_numeric)}, "
            f"min={_number(minimum)}, max={_number(maximum)}"
        )
    return "; ".join(parts)


def _payload_summary(value: Any, *, depth: int = 0) -> str:
    """Return a bounded, deterministic raw/smoothed feature summary."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        text = _number(value)
        return text if len(text) <= 180 else f"{text[:180]}…"
    if isinstance(value, list):
        if all(isinstance(item, dict) and "value" in item for item in value):
            return _series_summary(value)
        if depth >= 3:
            return f"array(n={len(value)})"
        shown = value[:6]
        rendered = ", ".join(_payload_summary(item, depth=depth + 1) for item in shown)
        suffix = f", … (+{len(value) - len(shown)})" if len(value) > len(shown) else ""
        return f"[{rendered}{suffix}]"
    if isinstance(value, dict):
        if depth >= 3:
            return f"object(keys={len(value)})"
        shown = list(islice(value.items(), 10))
        rendered = "; ".join(
            f"{key}: {_payload_summary(item, depth=depth + 1)}"
            for key, item in shown
        )
        suffix = f"; … (+{len(value) - len(shown)} keys)" if len(value) > len(shown) else ""
        return f"{{{rendered}{suffix}}}"
    return _payload_summary(str(value), depth=depth)


def _list_text(values: Any, *, empty: str = "无") -> str:
    if not isinstance(values, list) or not values:
        return empty
    return "、".join(str(value) for value in values)


def _key_value_table(payload: dict[str, Any], *, css_class: str = "facts") -> str:
    if not payload:
        return '<p class="muted">未提供。</p>'
    rows = "".join(
        "<tr>"
        f"<th>{_h(key)}</th>"
        f"<td><code>{_h(_payload_summary(value))}</code></td>"
        "</tr>"
        for key, value in payload.items()
    )
    return f'<table class="{_h(css_class)}"><tbody>{rows}</tbody></table>'


def _event_css_class(event_code: Any) -> str:
    normalized = str(event_code).lower()
    return normalized if normalized in {"fs01", "fs02", "fs09"} else "event-generic"


def _event_details(event: dict[str, Any]) -> str:
    phases = event.get("key_phases_ms")
    if isinstance(phases, dict) and phases:
        phase_rows = "".join(
            "<tr>"
            f"<th>{_h(name)}</th>"
            f"<td><code>{_h(_number(value))}</code>{' ms' if value is not None else '（未定位）'}</td>"
            "</tr>"
            for name, value in phases.items()
        )
        phase_table = f'<table class="facts"><tbody>{phase_rows}</tbody></table>'
    else:
        phase_table = '<p class="warn">未提供关键阶段候选。</p>'

    flags = event.get("quality_flags", [])
    diagnostics = event.get("track_diagnostics")
    diagnostic_html = (
        _key_value_table(diagnostics)
        if isinstance(diagnostics, dict)
        else '<p class="warn">Track diagnostics 未提供。</p>'
    )
    return (
        '<div class="event-facts">'
        '<p>'
        f'person_track_id <code>{_h(event.get("person_track_id", "—"))}</code> · '
        f'confidence <code>{_h(_number(event.get("confidence")))}</code> · '
        'boundary uncertainty '
        f'<code>{_h(_number(event.get("boundary_uncertainty_ms")))}</code> ms'
        '</p>'
        f'<p>quality flags：<code>{_h(_list_text(flags))}</code></p>'
        '<h4>全部关键阶段</h4>'
        f'{phase_table}'
        '<h4>Track diagnostics</h4>'
        f'{diagnostic_html}'
        '</div>'
    )


def _timeline(events: list[dict[str, Any]], *, bounds: tuple[int, int] | None = None) -> str:
    if not events:
        return '<p class="warn">未定位到满足质量门禁的候选事件。</p>'
    ordered = sorted(
        events,
        key=lambda item: (
            int(item.get("start_ms", 0)),
            int(item.get("end_ms", 0)),
            str(item.get("event_code", "")),
            str(item.get("event_id", "")),
        ),
    )
    start = bounds[0] if bounds is not None else min(int(item["start_ms"]) for item in ordered)
    end = bounds[1] if bounds is not None else max(int(item["end_ms"]) for item in ordered)
    span = max(end - start, 1)
    rows = []
    for event in ordered:
        event_start = int(event["start_ms"])
        event_end = int(event["end_ms"])
        left = 100 * (event_start - start) / span
        width = max(0.8, 100 * (event_end - event_start) / span)
        event_code = event.get("event_code", "unknown")
        event_id = event.get("event_id", "unknown")
        summary = (
            '<span class="event-row">'
            f'<span><strong>{_h(event_code)}</strong></span>'
            '<div class="rail">'
            f'<i class="bar {_event_css_class(event_code)}" '
            f'style="left:{left:.3f}%;width:{width:.3f}%" '
            f'title="{_h(event_id)}"></i></div>'
            f'<code>{event_start}–{event_end} ms</code>'
            '</span>'
        )
        rows.append(
            '<details class="event-card">'
            f'<summary>{summary}<small>{_h(event_id)} · '
            f'track {_h(event.get("person_track_id", "—"))} · '
            f'confidence {_h(_number(event.get("confidence")))} · '
            f'boundary ±{_h(_number(event.get("boundary_uncertainty_ms")))} ms · '
            f'quality flags {len(event.get("quality_flags", [])) if isinstance(event.get("quality_flags"), list) else "—"}'
            '</small></summary>'
            f'{_event_details(event)}'
            '</details>'
        )
    return "".join(rows)


def _evidence_frames(
    features: Iterable[dict[str, Any]], score: dict[str, Any]
) -> list[int]:
    frames: set[int] = set()
    for feature in features:
        for frame in feature.get("source_frames", []):
            if isinstance(frame, int) and not isinstance(frame, bool) and frame >= 0:
                frames.add(frame)
    for evidence in score.get("evidence", []):
        if not isinstance(evidence, dict):
            continue
        for frame in evidence.get("source_frames", []):
            if isinstance(frame, int) and not isinstance(frame, bool) and frame >= 0:
                frames.add(frame)
        frame = evidence.get("source_frame")
        if isinstance(frame, int) and not isinstance(frame, bool) and frame >= 0:
            frames.add(frame)
    return sorted(frames)


def _typed_features(
    record: dict[str, Any],
    feature_records_by_key: Any,
) -> Iterator[tuple[str, dict[str, Any]]]:
    required = record.get("features", [])
    measurement_names = {
        item.get("feature_name") for item in required if isinstance(item, dict)
    }
    scoring_only = [
        item
        for item in record.get("scoring_features", [])
        if isinstance(item, dict) and item.get("feature_name") not in measurement_names
    ]
    supplemental = record.get("supplemental_features", [])
    event_id = str(record.get("event_id", ""))

    def expanded(item: dict[str, Any]) -> dict[str, Any]:
        canonical = feature_records_by_key.get(
            (event_id, str(item.get("feature_name", ""))),
            {},
        )
        # The compact indicator record remains authoritative for score-facing
        # value/validity. The canonical feature record contributes the full
        # raw/smoothed evidence omitted by that intentionally compact contract.
        merged = {**canonical, **item}
        for field in ("raw_value", "smoothed_value", "value"):
            if field in item:
                merged.pop(f"_report_{field}", None)
        return merged

    for kind, items in (("required", required), ("scoring-context", scoring_only), ("supplemental", supplemental)):
        for item in items:
            if isinstance(item, dict):
                yield kind, expanded(item)


def _feature_row(kind: str, feature: dict[str, Any]) -> str:
    valid = bool(feature.get("valid"))
    raw_summary = feature.get("_report_raw_value", (
        _payload_summary(feature["raw_value"])
        if "raw_value" in feature
        else "独立目标方向上下文；无逐帧原始序列"
        if kind == "scoring-context"
        else "contract field missing"
    ))
    smoothed_summary = feature.get("_report_smoothed_value", (
        _payload_summary(feature["smoothed_value"])
        if "smoothed_value" in feature
        else "上下文标量未进行时序平滑"
        if kind == "scoring-context"
        else "contract field missing"
    ))
    return (
        "<tr>"
        f'<td>{_h(kind)}</td>'
        f'<td><code>{_h(feature.get("feature_name", "—"))}</code><br>'
        f'<small>{_h(feature.get("feature_version", "—"))}</small></td>'
        f'<td><strong>{_h(feature.get("_report_value", _payload_summary(feature.get("value"))))}</strong> '
        f'{_h(feature.get("unit", ""))}</td>'
        f'<td><code>{_h(_number(feature.get("confidence")))}</code></td>'
        f'<td><span class="{"ok" if valid else "bad"}">{"valid" if valid else "invalid"}</span><br>'
        f'<code>{_h(feature.get("reason", "—"))}</code></td>'
        f'<td><code>{_h(raw_summary)}</code></td>'
        f'<td><code>{_h(smoothed_summary)}</code></td>'
        f'<td><code>{_h(feature.get("source_frames", []))}</code></td>'
        "</tr>"
    )


_FEATURE_TABLE_START = (
    '<div class="table-scroll"><table class="feature-table"><thead><tr>'
    '<th>类型</th><th>特征 / 版本</th><th>值 / 单位</th><th>置信度</th>'
    '<th>有效性 / 原因</th><th>raw 摘要</th><th>smoothed 摘要</th>'
    '<th>source frames</th></tr></thead><tbody>'
)
_FEATURE_TABLE_END = '</tbody></table></div>'


def _feature_table(
    record: dict[str, Any],
    feature_records_by_key: Any,
) -> tuple[str, list[dict[str, Any]]]:
    typed_features = list(_typed_features(record, feature_records_by_key))
    if not typed_features:
        return '<p class="warn">未提供特征事实。</p>', []
    table = _FEATURE_TABLE_START + "".join(_feature_row(kind, feature) for kind, feature in typed_features) + _FEATURE_TABLE_END
    return table, [feature for _, feature in typed_features]


def _legal_event_grade(
    indicator: dict[str, Any], record: dict[str, Any], score: dict[str, Any]
) -> str | None:
    status = score.get("status", record.get("scoring_status"))
    grade = score.get("grade", record.get("grade"))
    if (
        status == "scored"
        and grade in _GRADES
    ):
        return str(grade)
    return None


def _indicator_instance_parts(
    indicator: dict[str, Any],
    record: dict[str, Any],
    score: dict[str, Any],
    event: dict[str, Any] | None,
) -> tuple[str, str]:
    status = score.get("status", record.get("scoring_status", "unavailable"))
    grade = _legal_event_grade(indicator, record, score)
    reasons = score.get("reason_codes", record.get("reason_codes", []))
    quality_gate = score.get("quality_gate", record.get("quality_gate"))
    interval = ""
    if event is not None:
        interval = (
            f' · {int(event.get("start_ms", 0))}–{int(event.get("end_ms", 0))} ms'
        )
    gate_html = (
        _key_value_table(quality_gate)
        if isinstance(quality_gate, dict)
        else '<p class="warn">quality gate 未提供。</p>'
    )
    evidence = score.get("evidence", [])
    model_versions = score.get("model_versions", {})
    return (
        '<details class="instance-card">'
        '<summary>'
        f'<code>{_h(record.get("event_id", "—"))}</code>{interval} · '
        f'measurement feature <strong>{_h(record.get("feature_status", "—"))}</strong> · '
        f'scoring feature <strong>{_h(record.get("scoring_feature_status", record.get("feature_status", "—")))}</strong> · '
        f'score <strong>{_h(status)}</strong> · '
        f'grade <strong>{_h(grade or "—")}</strong>'
        '</summary>'
        '<div class="instance-body">'
        '<p>'
        f'event <code>{_h(record.get("event_code", "—"))}</code> · '
        f'person_track_id <code>{_h(record.get("person_track_id", "—"))}</code> · '
        f'score confidence <code>{_h(_number(score.get("confidence")))}</code> · '
        f'threshold/model version <code>{_h(score.get("threshold_version") or "—")}</code>'
        '</p>'
        f'<p>reason codes：<code>{_h(_list_text(reasons))}</code></p>'
        f'<p>feedback：{_h(score.get("feedback", "—"))}</p>',
        '<h5>Quality gate</h5>'
        f'{gate_html}'
        '<details><summary>评分 evidence 与模型版本</summary>'
        f'<h5>evidence</h5><pre>{_h(score.get("_report_evidence", _json_text(evidence, max_chars=8_000)))}</pre>'
        f'<h5>model versions</h5><pre>{_h(score.get("_report_model_versions", _json_text(model_versions, max_chars=8_000)))}</pre>'
        '</details>'
        '</div></details>'
    )


def _indicator_instance(
    indicator: dict[str, Any],
    record: dict[str, Any],
    score: dict[str, Any],
    event: dict[str, Any] | None,
    feature_records_by_key: Any,
) -> str:
    feature_table, all_features = _feature_table(record, feature_records_by_key)
    frames = _evidence_frames(all_features, score)
    opening, closing = _indicator_instance_parts(indicator, record, score, event)
    return opening + f'<p>合并证据帧：<code>{_h(frames)}</code></p>' + feature_table + closing


def _metric_value(stats: dict[str, Any], name: str) -> str:
    return _h(_number(stats.get(name)))


def _view_items(by_view: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(by_view, dict):
        return [
            (str(name), stats)
            for name, stats in by_view.items()
            if isinstance(stats, dict)
        ]
    if isinstance(by_view, list):
        output = []
        for index, stats in enumerate(by_view):
            if not isinstance(stats, dict):
                continue
            name = stats.get("view_group", stats.get("view", f"view-{index + 1}"))
            output.append((str(name), stats))
        return output
    return []


def _feature_error_section(feature_eval: dict[str, Any]) -> str:
    status = feature_eval.get("status", "ground_truth_required")
    feature_metrics = feature_eval.get("feature_metrics")
    metrics = (
        feature_metrics
        if isinstance(feature_metrics, dict)
        else {}
    )
    metric_rows = []
    for feature_name, metric in metrics.items():
        if not isinstance(metric, dict) or not isinstance(metric.get("overall"), dict):
            continue
        unit = metric.get("unit", "—")
        overall = metric["overall"]
        metric_rows.append(
            "<tr>"
            f'<td><code>{_h(feature_name)}</code></td><td>overall</td>'
            f'<td>{_h(unit)}</td><td>{_metric_value(overall, "mae")}</td>'
            f'<td>{_metric_value(overall, "p95")}</td>'
            f'<td>{_metric_value(overall, "bias")}</td>'
            f'<td>{_metric_value(overall, "valid_count")}/'
            f'{_metric_value(overall, "eligible_count")}</td>'
            f'<td>{_metric_value(overall, "valid_rate")}</td>'
            "</tr>"
        )
        for view_name, view in _view_items(metric.get("by_view")):
            metric_rows.append(
                "<tr>"
                f'<td><code>{_h(feature_name)}</code></td>'
                f'<td>view: <code>{_h(view_name)}</code></td>'
                f'<td>{_h(unit)}</td><td>{_metric_value(view, "mae")}</td>'
                f'<td>{_metric_value(view, "p95")}</td>'
                f'<td>{_metric_value(view, "bias")}</td>'
                f'<td>{_metric_value(view, "valid_count")}/'
                f'{_metric_value(view, "eligible_count")}</td>'
                f'<td>{_metric_value(view, "valid_rate")}</td>'
                "</tr>"
            )
    coverage = feature_eval.get("ground_truth_coverage")
    coverage_html = (
        f'<p>真值覆盖：<code>{_h(_payload_summary(coverage))}</code></p>'
        if isinstance(coverage, dict)
        else ""
    )
    if metric_rows and status != "ground_truth_required":
        metrics_html = (
            '<p><strong>已导入人工关键点/事件真值并完成特征误差计算。</strong></p>'
            '<div class="table-scroll"><table><thead><tr>'
            '<th>特征</th><th>范围 / 视角</th><th>单位</th><th>MAE</th>'
            '<th>P95</th><th>Bias</th><th>有效 / eligible</th><th>有效率</th>'
            f'</tr></thead><tbody>{"".join(metric_rows)}</tbody></table></div>'
        )
    else:
        metrics_html = (
            '<p class="warn"><strong>truth missing：</strong>'
            '尚无可核验的人工校正关键点与人工事件边界，MAE、P95、Bias、'
            '有效率和分视角结果不可计算；空值不是数字 0。</p>'
        )
    budget = feature_eval.get("error_budget", {})
    return (
        f'<p>状态：<code>{_h(status)}</code></p>'
        f'{coverage_html}{metrics_html}'
        '<details><summary>评分误差预算（Pose / 事件边界 / 平滑 / 缺失值）</summary>'
        f'<pre>{_h(_json_text(budget))}</pre></details>'
    )


def build_scoring_loop_report_html(result: dict[str, Any]) -> str:
    summary = result["summary"]
    events = result["events"]
    records = result["indicator_records"]
    scores = result.get("scores", [])
    feature_records = result.get("feature_records", [])
    registry = result["feasibility"]
    scores_by_key = {
        (item["indicator_id"], item["event_id"]): item for item in scores
    }
    events_by_id = {item.get("event_id"): item for item in events}
    feature_records_by_key = {
        (str(item.get("event_id", "")), str(item.get("feature_name", ""))): item
        for item in feature_records
        if isinstance(item, dict)
    }
    records_by_indicator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_indicator[record["indicator_id"]].append(record)

    legal_grade_counts: Counter[str] = Counter()
    indicator_rows = []
    for indicator in registry["indicators"]:
        indicator_id = indicator["indicator_id"]
        samples = sorted(
            records_by_indicator[indicator_id],
            key=lambda record: (
                int(events_by_id.get(record.get("event_id"), {}).get("start_ms", 0)),
                str(record.get("event_id", "")),
            ),
        )
        valid = sum(item.get("feature_status") == "measured" for item in samples)
        measurement_states = Counter(
            str(item.get("feature_status", "unavailable")) for item in samples
        )
        scoring_feature_states = Counter(
            str(item.get("scoring_feature_status", item.get("feature_status", "unavailable")))
            for item in samples
        )
        scoring_states: Counter[str] = Counter()
        grade_states: Counter[str] = Counter()
        instance_html = []
        for record in samples:
            score = scores_by_key.get(
                (record["indicator_id"], record["event_id"]), record
            )
            scoring_states[
                str(score.get("status", record.get("scoring_status", "unavailable")))
            ] += 1
            legal_grade = _legal_event_grade(indicator, record, score)
            if legal_grade is not None:
                grade_states[legal_grade] += 1
                legal_grade_counts[legal_grade] += 1
            instance_html.append(
                _indicator_instance(
                    indicator,
                    record,
                    score,
                    events_by_id.get(record.get("event_id")),
                    feature_records_by_key,
                )
            )
        blockers = sorted({reason for sample in samples for reason in sample.get("reason_codes", [])})
        if isinstance(blockers, list):
            blocker_text = "；".join(str(item) for item in blockers) or "无"
        else:
            blocker_text = str(blockers)
        instances = (
            '<details class="all-instances">'
            f'<summary>查看全部 {len(samples)} 个事件实例</summary>'
            f'{"".join(instance_html)}</details>'
            if samples
            else "无候选事件"
        )
        indicator_rows.append(
            "<tr>"
            f'<td><strong>{_h(indicator_id)}</strong><br>{_h(indicator.get("name_zh", "—"))}</td>'
            f'<td><span class="level">{_h(indicator.get("feasibility_level", "—"))}</span></td>'
            f'<td><strong>{"measured" if valid else "unavailable"}</strong><br>'
            f'{valid}/{len(samples)} 个候选事件实例有效<br>'
            f'<small>measurement feature_status {_h(dict(measurement_states))}</small><br>'
            f'<small>scoring_feature_status {_h(dict(scoring_feature_states))}</small></td>'
            f'<td>score.status <code>{_h(dict(scoring_states))}</code><br>'
            f'<small>按明确规则计算的事件级等级分布 '
            f'{_h(dict(grade_states) if grade_states else "无")}</small></td>'
            f'<td>{instances}</td>'
            f'<td>{_h(blocker_text)}</td>'
            "</tr>"
        )

    opening, middle, closing = _document_parts(result, legal_grade_counts)
    return opening + _timeline(events) + middle + "".join(indicator_rows) + closing


def _document_parts(result: dict[str, Any], legal_grade_counts: Counter[str]) -> tuple[str, str, str]:
    summary = result["summary"]
    registry = result["feasibility"]
    score_counts = summary.get("score_status_counts", {})
    result_state = summary.get("result_state", {})
    event_eval = summary.get("event_evaluation", {})
    feature_eval = summary.get("feature_error_evaluation", {})
    scoring_reference_context = summary.get("scoring_reference_context", {})
    if legal_grade_counts:
        scoring_explanation = (
            "具有所需有效输入和明确数学规则的事件实例输出 A～E；"
            "其余实例仍为 calibration_required 或 unavailable。"
            "等级是事件级单指标结果，本轮不设计总分。"
        )
    else:
        scoring_explanation = (
            "有效特征只进入 calibration_required；无效特征进入 unavailable。"
            "没有数学等级规则时保留测量，不擅自生成 A～E，未评价不显示成数字 0。"
        )
    phase_error_details = event_eval.get("phase_boundary_by_name", {})
    return (f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="rallymate-report-version" content="{_h(SCORING_LOOP_REPORT_VERSION)}">
<title>RallyMate 最小可行评分闭环</title><style>
body{{font:14px/1.55 system-ui,sans-serif;max-width:1440px;margin:28px auto;padding:0 18px;color:#172019;background:#f6f6f1}}
h1,h2,h3,h4,h5{{line-height:1.25}}section{{background:white;border:1px solid #c9cec9;padding:18px;margin:14px 0}}code,pre{{font:12px/1.5 ui-monospace,monospace}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d8ddd8;padding:8px;vertical-align:top;text-align:left}}th{{background:#edf0ec}}.facts th{{width:260px}}.level{{font-weight:800;background:#d9ff70;padding:2px 7px}}.warn,.bad{{color:#8a3d16}}.ok{{color:#176b32}}.muted,small{{color:#59635c}}.event-row{{display:grid;grid-template-columns:58px minmax(140px,1fr) 160px;gap:8px;align-items:center;margin:6px 0}}.rail{{position:relative;height:18px;background:#edf0ec}}.bar{{position:absolute;height:100%;min-width:2px}}.fs01{{background:#2563eb}}.fs02{{background:#16a34a}}.fs09{{background:#dc6b15}}.event-generic{{background:#64748b}}details{{margin:7px 0}}summary{{cursor:pointer}}.event-card>summary{{border:1px solid #d8ddd8;padding:6px;list-style-position:inside}}.event-card>summary .event-row{{display:inline-grid;width:calc(100% - 20px);vertical-align:middle}}.event-card>summary small{{display:block;margin-left:24px}}.event-facts,.instance-body{{padding:8px 14px 12px;border:1px solid #d8ddd8;border-top:0}}.instance-card{{border:1px solid #d8ddd8;padding:6px;margin:8px 0}}.instance-card>.instance-body{{border:0}}.all-instances>summary{{font-weight:700}}.table-scroll{{overflow:auto;max-width:100%}}.feature-table{{min-width:1180px}}.feature-table td code{{overflow-wrap:anywhere}}
</style></head><body>
<h1>RallyMate 最小可行评分闭环</h1>
<p>视频/任务：<code>{_h(summary["video_id"])}</code> · 闭环版本 <code>{_h(summary["loop_version"])}</code> · 报告版本 <code>{_h(SCORING_LOOP_REPORT_VERSION)}</code> · 当前状态 <code>{_h(summary["status"])}</code></p>
<section><h2>测量与评分状态</h2><p><strong>{_h(result_state.get("ui_message_zh", "结果不可解释为零分"))}</strong></p>
<p>特征测量：<code>{_h(result_state.get("measurement_status"))}</code> · 已测指标定义：<code>{_h(result_state.get("measured_indicator_count"))}/{_h(result_state.get("target_indicator_count"))}</code> · 正式评分：<code>{_h(result_state.get("scoring_status"))}</code> · 按明确规则计算的事件级 A～E 分布：<code>{_h(dict(legal_grade_counts) if legal_grade_counts else "无")}</code> · 总等级：<code>未设计</code>。</p>
<p>事件实例状态：<code>{_h(score_counts)}</code>。{_h(scoring_explanation)}</p></section>
<section><h2>评分参考上下文</h2><p>目标方向参考状态：<code>{_h(scoring_reference_context.get("status", "not_provided"))}</code> · FS02 目标事件：<code>{_h(scoring_reference_context.get("target_event_count", 0))}</code> · 可计算方向对齐误差：<code>{_h(scoring_reference_context.get("available_alignment_count", 0))}</code>。</p>
<p>该层接受显式给出的独立目标方向上下文，并计算人体运动方向相对目标方向的角度误差；不会从人体运动反推“目标”，也不会生成等级或 A～E 阈值。</p><details><summary>上下文审计字段</summary><pre>{_h(_json_text(scoring_reference_context, max_chars=8_000))}</pre></details></section>
<section><h2>事件时间轴（规则候选，非真值）</h2>""", f"""
<p>Event F1：<code>{_h(event_eval.get("event_f1"))}</code>；Segment IoU：<code>{_h(event_eval.get("mean_segment_iou"))}</code>；起止 Boundary MAE / P95：<code>{_h(event_eval.get("boundary_mae_ms"))}</code> / <code>{_h(event_eval.get("boundary_p95_ms"))}</code>；关键阶段 Boundary MAE / P95 / 有效率：<code>{_h(event_eval.get("phase_boundary_mae_ms"))}</code> / <code>{_h(event_eval.get("phase_boundary_p95_ms"))}</code> / <code>{_h(event_eval.get("phase_boundary_valid_rate"))}</code>。人工事件真值缺失时这些字段保持空值。</p>
<details><summary>按关键阶段的边界误差</summary><pre>{_h(_json_text(phase_error_details, max_chars=12_000))}</pre></details></section>
<section><h2>{_h(summary.get("scope", {}).get("indicator_count", len(registry["indicators"])))}个指标成熟度、全部事件实例、特征与证据</h2><p><strong>特征测量与正式评分分别统计：</strong><code>feature_status=measured</code> 仅代表该事件特征有效，不能解释成 <code>score.status=calibration_required</code> 或已评分。</p><table><thead><tr><th>指标</th><th>F0–F4</th><th>特征测量状态</th><th>评分状态</th><th>全部事件实例</th><th>阻断原因</th></tr></thead><tbody>""", f"""</tbody></table></section>
<section><h2>特征误差与评分误差预算</h2>{_feature_error_section(feature_eval)}</section>
<section><h2>可追溯版本</h2><pre>{_h(_json_text(summary.get("model_versions", {}), max_chars=16_000))}</pre><p>机器产物：<code>events.jsonl</code>、<code>features.jsonl</code>、<code>indicator-features.jsonl</code>、<code>scores.jsonl</code>、<code>event-feature-errors.json</code>。</p></section>
</body></html>""")


def _load_jsonl(path: Path, *, max_record_bytes: int = MAX_REPORT_RECORD_BYTES) -> list[dict[str, Any]]:
    """Compatibility reader for small callers; production rendering uses an iterator."""
    records = []
    line_number = 0
    with path.open("rb") as handle:
        while physical_bytes := handle.readline(max_record_bytes + 1):
            if len(physical_bytes) > max_record_bytes:
                raise ReportRecordLimit(f"report record exceeds {max_record_bytes} bytes at {path}:{line_number + 1}")
            physical_line = physical_bytes.decode("utf-8")
            # Keep str.splitlines' existing separator/line-number semantics,
            # without retaining the complete file text and all its line copies.
            for line in physical_line.splitlines():
                line_number += 1
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSONL report fact at {path}:{line_number}"
                    ) from exc
                if not isinstance(record, dict):
                    raise ValueError(
                        f"JSONL report fact must be an object at {path}:{line_number}"
                    )
                records.append(record)
    return records


def _artifact_path(
    result: dict[str, Any], output_path: Path, artifact_key: str, fallback: str
) -> Path:
    artifacts = result.get("summary", {}).get("artifacts", {})
    configured = artifacts.get(artifact_key, fallback) if isinstance(artifacts, dict) else fallback
    path = Path(str(configured))
    return path if path.is_absolute() else output_path.parent / path


def _report_feature(item: dict[str, Any]) -> dict[str, Any]:
    """Keep exactly the fields rendered, never copy large model provenance."""
    result = {key: item[key] for key in (
        "feature_name", "feature_version", "unit", "valid", "confidence", "reason", "source_frames"
    ) if key in item}
    for key in ("value", "raw_value", "smoothed_value"):
        if key in item:
            result[f"_report_{key}"] = _payload_summary(item[key])
    return result


def _report_record(item: dict[str, Any], *, indicator: bool = False) -> dict[str, Any]:
    result = {key: item[key] for key in (
        "indicator_id", "event_id", "event_code", "person_track_id", "feature_status",
        "scoring_feature_status", "scoring_status", "status", "grade", "confidence",
        "threshold_version", "feedback", "reason_codes", "quality_gate",
    ) if key in item}
    if "evidence" in item:
        result["_report_evidence"] = _json_text(item["evidence"], max_chars=8_000)
        result["evidence"] = [
            {key: evidence[key] for key in ("source_frame", "source_frames") if key in evidence}
            for evidence in item["evidence"] if isinstance(evidence, dict)
        ]
    if "model_versions" in item:
        result["_report_model_versions"] = _json_text(item["model_versions"], max_chars=8_000)
    if indicator:
        for key in ("features", "scoring_features", "supplemental_features"):
            if key in item:
                result[key] = [_report_feature(feature) for feature in item[key] if isinstance(feature, dict)]
    return result


def _encoded_record(item: dict[str, Any], limit: int, check: Callable[[], None]) -> bytes:
    # iterencode avoids constructing the full document before checking the cap.
    with io.BytesIO() as buffer:
        for count, part in enumerate(json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(item)):
            if count % 1024 == 0:
                check()
            encoded = part.encode("utf-8")
            if buffer.tell() + len(encoded) > limit:
                raise ReportRecordLimit(f"report record exceeds {limit} bytes")
            buffer.write(encoded)
        return buffer.getvalue()


class _ReportIndex:
    """Disposable, disk-backed joins. No list grows with the number of events."""

    def __init__(self, path: Path, *, check: Callable[[], None], record_limit: int):
        self.db = sqlite3.connect(path)
        self.check = check
        self.record_limit = record_limit
        self.db.executescript("""
            PRAGMA temp_store=FILE;
            PRAGMA cache_size=-2048;
            PRAGMA mmap_size=0;
            PRAGMA journal_mode=OFF;
            CREATE TABLE events(seq INTEGER PRIMARY KEY, event_id TEXT, start INTEGER,
                                end INTEGER, code TEXT, payload TEXT);
            CREATE INDEX events_identity ON events(event_id, seq DESC);
            CREATE INDEX events_order ON events(start, end, code, event_id, seq);
            CREATE TABLE features(event_id TEXT, name TEXT, payload TEXT,
                                  PRIMARY KEY(event_id,name));
            CREATE TABLE scores(indicator_id TEXT, event_id TEXT, payload TEXT,
                                PRIMARY KEY(indicator_id,event_id));
            CREATE TABLE records(seq INTEGER PRIMARY KEY, indicator_id TEXT, event_id TEXT,
                                 start INTEGER, payload TEXT);
            CREATE INDEX records_order ON records(indicator_id,start,event_id,seq);
            CREATE TABLE stats(indicator_id TEXT, kind TEXT, value TEXT, n INTEGER,
                               ordinal INTEGER, PRIMARY KEY(indicator_id,kind,value));
            CREATE TABLE blockers(indicator_id TEXT, reason TEXT, PRIMARY KEY(indicator_id,reason));
            CREATE TABLE instance_frames(frame INTEGER PRIMARY KEY);
        """)
        # Long SQL sorts/joins must still observe cancellation, not just Python loops.
        self.sql_cancelled: BaseException | None = None

        def sql_progress() -> int:
            try:
                self.check()
                return 0
            except BaseException as exc:
                self.sql_cancelled = exc
                return 1

        self.db.set_progress_handler(sql_progress, 10000)

    def close(self) -> None:
        self.db.close()

    def get(self, key: tuple[str, str], default: Any = None) -> Any:
        self.check()
        row = self.db.execute("SELECT payload FROM features WHERE event_id=? AND name=?", key).fetchone()
        return json.loads(row[0]) if row is not None else default

    def event(self, event_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT payload FROM events WHERE event_id=? ORDER BY seq DESC LIMIT 1", (event_id,)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def samples(self, indicator_id: str) -> Iterator[tuple[dict, dict, dict | None]]:
        for (payload,) in self.db.execute(
            "SELECT payload FROM records WHERE indicator_id=? ORDER BY start,event_id,seq", (indicator_id,)
        ):
            self.check()
            record = json.loads(payload)
            row = self.db.execute("SELECT payload FROM scores WHERE indicator_id=? AND event_id=?",
                                  (indicator_id, record["event_id"])).fetchone()
            yield record, json.loads(row[0]) if row is not None else record, self.event(record.get("event_id", ""))

    def counts(self, indicator_id: str, kind: str) -> dict[str, int]:
        result = {}
        for value, count in self.db.execute(
            "SELECT value,n FROM stats WHERE indicator_id=? AND kind=? ORDER BY ordinal", (indicator_id, kind)
        ):
            self.check()
            # These are schema enum values, not one arbitrary group per event.
            if len(result) >= 1024:
                raise ReportRecordLimit("report has too many distinct status values")
            result[value] = count
        return result

    def add_count(self, indicator_id: str, kind: str, value: str, ordinal: int) -> None:
        self.db.execute("INSERT INTO stats VALUES(?,?,?,1,?) ON CONFLICT(indicator_id,kind,value) DO UPDATE SET n=n+1",
                        (indicator_id, kind, value, ordinal))


def _iter_report_records(result: dict[str, Any], output: Path, key: str,
                         artifact: str, fallback: str, *, check: Callable[[], None], limit: int) -> Iterator[dict]:
    if key in result:
        for record in result[key]:
            check()
            if not isinstance(record, dict):
                raise ValueError(f"report {key} record must be an object")
            yield record
        return
    path = _artifact_path(result, output, artifact, fallback)
    if path.is_file():
        from rallymate_scoring.artifact_evidence import iter_artifact_records
        summary = result.get("summary", {})
        yield from iter_artifact_records(path, cancellation_callback=check, max_record_bytes=limit,
                                        expected_video_id=summary.get("video_id"),
                                        expected_source_id=summary.get("provenance", {}).get("pipeline_job_id"))


def _fill_report_index(index: _ReportIndex, result: dict, output: Path,
                       notify: Callable[[str, int], None]) -> Counter[str]:
    sources = (
        ("events", "events_jsonl", "events.jsonl"),
        ("feature_records", "features_jsonl", "features.jsonl"),
        ("scores", "scores_jsonl", "scores.jsonl"),
        ("indicator_records", "indicator_features_jsonl", "indicator-features.jsonl"),
    )
    for key, artifact, filename in sources:
        count = 0
        for count, record in enumerate(_iter_report_records(result, output, key, artifact, filename,
                                                           check=index.check, limit=index.record_limit), start=1):
            if key == "events":
                index.db.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (
                    count, str(record.get("event_id", "")), int(record.get("start_ms", 0)),
                    int(record.get("end_ms", 0)), str(record.get("event_code", "")),
                    _encoded_record(record, index.record_limit, index.check)))
            elif key == "feature_records":
                index.db.execute("INSERT OR REPLACE INTO features VALUES(?,?,?)", (
                    str(record.get("event_id", "")), str(record.get("feature_name", "")),
                    _encoded_record(_report_feature(record), index.record_limit, index.check)))
            elif key == "scores":
                index.db.execute("INSERT OR REPLACE INTO scores VALUES(?,?,?)", (
                    record["indicator_id"], record["event_id"],
                    _encoded_record(_report_record(record), index.record_limit, index.check)))
            else:
                event = index.event(record.get("event_id", "")) or {}
                index.db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (
                    count, record["indicator_id"], record.get("event_id", ""), int(event.get("start_ms", 0)),
                    _encoded_record(_report_record(record, indicator=True), index.record_limit, index.check)))
                for reason in record.get("reason_codes", []):
                    index.check()
                    index.db.execute("INSERT OR IGNORE INTO blockers VALUES(?,?)", (record["indicator_id"], str(reason)))
            if count % 128 == 0:
                index.db.commit()
                notify(f"indexing_{key}", count)
        index.db.commit()
        notify(f"indexing_{key}", count)

    legal_grades: Counter[str] = Counter()
    for indicator in result["feasibility"]["indicators"]:
        identity = indicator["indicator_id"]
        for ordinal, (record, score, _) in enumerate(index.samples(identity)):
            for kind, value in (
                ("measurement", str(record.get("feature_status", "unavailable"))),
                ("scoring_feature", str(record.get("scoring_feature_status", record.get("feature_status", "unavailable")))),
                ("score", str(score.get("status", record.get("scoring_status", "unavailable")))),
            ):
                index.add_count(identity, kind, value, ordinal)
            grade = _legal_event_grade(indicator, record, score)
            if grade is not None:
                legal_grades[grade] += 1
                index.add_count(identity, "grade", grade, ordinal)
        index.db.commit()
    return legal_grades


def _stream_instance(index: _ReportIndex, indicator: dict, record: dict, score: dict,
                     event: dict | None) -> Iterator[str]:
    opening, closing = _indicator_instance_parts(indicator, record, score, event)
    yield opening
    index.db.execute("DELETE FROM instance_frames")

    def add_frame(frame: Any) -> None:
        if isinstance(frame, int) and not isinstance(frame, bool) and frame >= 0:
            index.db.execute("INSERT OR IGNORE INTO instance_frames VALUES(?)", (frame,))

    for _, feature in _typed_features(record, index):
        index.check()
        for frame in feature.get("source_frames", []):
            add_frame(frame)
    for evidence in score.get("evidence", []):
        index.check()
        if isinstance(evidence, dict):
            for frame in evidence.get("source_frames", []):
                add_frame(frame)
            add_frame(evidence.get("source_frame"))
    yield '<p>合并证据帧：<code>['
    separator = ""
    for (frame,) in index.db.execute("SELECT frame FROM instance_frames ORDER BY frame"):
        index.check()
        yield separator + str(frame)
        separator = ", "
    yield ']</code></p>'
    features_present = False
    for kind, feature in _typed_features(record, index):
        index.check()
        if not features_present:
            yield _FEATURE_TABLE_START
            features_present = True
        yield _feature_row(kind, feature)
    yield _FEATURE_TABLE_END if features_present else '<p class="warn">未提供特征事实。</p>'
    yield closing


def _stream_report(index: _ReportIndex, result: dict, legal_grades: Counter[str],
                   notify: Callable[[str, int], None]) -> Iterator[str]:
    opening, middle, closing = _document_parts(result, legal_grades)
    yield opening
    bounds = index.db.execute("SELECT MIN(start),MAX(end) FROM events").fetchone()
    if bounds[0] is None:
        yield '<p class="warn">未定位到满足质量门禁的候选事件。</p>'
    else:
        for count, (payload,) in enumerate(index.db.execute("SELECT payload FROM events ORDER BY start,end,code,event_id,seq"), start=1):
            index.check()
            yield _timeline([json.loads(payload)], bounds=bounds)
            if count % 128 == 0:
                notify("writing_events", count)
    yield middle
    completed = 0
    for indicator in result["feasibility"]["indicators"]:
        index.check()
        identity = indicator["indicator_id"]
        measurement = index.counts(identity, "measurement")
        scoring_features = index.counts(identity, "scoring_feature")
        scores = index.counts(identity, "score")
        grades = index.counts(identity, "grade")
        count = sum(measurement.values())
        valid = measurement.get("measured", 0)
        yield (
            '<tr>'
            f'<td><strong>{_h(identity)}</strong><br>{_h(indicator.get("name_zh", "—"))}</td>'
            f'<td><span class="level">{_h(indicator.get("feasibility_level", "—"))}</span></td>'
            f'<td><strong>{"measured" if valid else "unavailable"}</strong><br>'
            f'{valid}/{count} 个候选事件实例有效<br>'
            f'<small>measurement feature_status {_h(measurement)}</small><br>'
            f'<small>scoring_feature_status {_h(scoring_features)}</small></td>'
            f'<td>score.status <code>{_h(scores)}</code><br>'
            f'<small>按明确规则计算的事件级等级分布 {_h(grades if grades else "无")}</small></td><td>'
        )
        if count:
            yield f'<details class="all-instances"><summary>查看全部 {count} 个事件实例</summary>'
            for record, score, event in index.samples(identity):
                yield from _stream_instance(index, indicator, record, score, event)
                completed += 1
                if completed % 128 == 0:
                    index.db.commit()
                    notify("writing_instances", completed)
            yield '</details>'
        else:
            yield "无候选事件"
        yield '</td><td>'
        separator = ""
        for (reason,) in index.db.execute("SELECT reason FROM blockers WHERE indicator_id=? ORDER BY reason", (identity,)):
            index.check()
            yield separator + _h(reason)
            separator = "；"
        if not separator:
            yield "无"
        yield '</td></tr>'
    notify("writing_instances", completed)
    yield closing


def write_scoring_loop_report(
    result: dict[str, Any], output_path: str | Path, *,
    cancellation_callback: Callable[[], None] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    max_record_bytes: int = MAX_REPORT_RECORD_BYTES,
) -> Path:
    """Render all facts with bounded joins and publish only a complete HTML file.

    ``build_scoring_loop_report_html`` remains the string API for small fixtures.
    Progress callbacks receive ``phase``, ``completed_items`` and a stage message;
    a raised cancellation or callback exception preserves the previous report.
    """
    if not isinstance(max_record_bytes, int) or isinstance(max_record_bytes, bool) or max_record_bytes <= 0:
        raise ValueError("max_record_bytes must be a positive integer")
    output_path = Path(output_path)

    def check() -> None:
        if cancellation_callback is not None:
            cancellation_callback()

    def notify(stage: str, count: int) -> None:
        check()
        if progress_callback is not None:
            progress_callback({"phase": "finalizing", "completed_items": count,
                               "message": "正在生成训练报告", "report_stage": stage})

    check()
    with tempfile.TemporaryDirectory(prefix=".scoring-report-", dir=output_path.parent) as directory:
        temporary = Path(directory)
        index = _ReportIndex(temporary / "facts.sqlite3", check=check, record_limit=max_record_bytes)
        try:
            legal_grades = _fill_report_index(index, result, output_path, notify)
            staged = temporary / "report.html"
            with staged.open("w", encoding="utf-8", newline="") as handle:
                for fragment in _stream_report(index, result, legal_grades, notify):
                    check()
                    handle.write(fragment)
                handle.flush()
                os.fsync(handle.fileno())
            check()
            os.replace(staged, output_path)
        except sqlite3.OperationalError:
            if index.sql_cancelled is not None:
                raise index.sql_cancelled
            raise
        finally:
            index.close()
    return output_path
