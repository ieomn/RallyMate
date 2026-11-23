from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.ground_truth import validate_keypoint_annotation
from rallymate_events.schemas import validate_event_record
from rallymate_scoring.calibration import (
    compute_annotator_agreement,
    validate_coach_label,
)
from rallymate_vision.pose.metadata import sha256_file


TRUTH_PACK_VERSION = "scoring-truth-pack-v0.3.0"
TRUTH_WORKBENCH_VERSION = "truth-workbench-v1.0.1"
READINESS_MATRIX_VERSION = "truth-readiness-matrix-v1.0.0"
DEFAULT_CANDIDATE_EVENTS_TEMPLATE = (
    "reports/pose-scoring-ab/rtmpose-m-halpe26-256x192/{video_id}/events.jsonl"
)
MIN_EVENT_REVIEW_ANNOTATORS = 2
MIN_COACH_ANNOTATORS_PER_ITEM = 2
VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)
EVENT_CODES = ("FS01", "FS02", "FS09")
INDICATORS_BY_EVENT = {
    "FS01": ("FS01-M02", "FS01-M03", "FS01-M04", "FS01-M05"),
    "FS02": ("FS02-M02", "FS02-M03", "FS02-M04", "FS02-M05"),
    "FS09": ("FS09-M01", "FS09-M02", "FS09-M03", "FS09-M04", "FS09-M05"),
}
KEYPOINT_JOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
    "left_big_toe",
    "right_big_toe",
    "left_small_toe",
    "right_small_toe",
    "left_heel",
    "right_heel",
)
SCORING_CORE_KEYPOINT_JOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
EVENT_PHASES = (
    "preload_ms",
    "takeoff_proxy_ms",
    "landing_proxy_ms",
    "redistribution_ms",
    "initiation_ms",
    "direction_conversion_ms",
    "support_extension_proxy_ms",
    "lead_foot_motion_onset_proxy_ms",
    "first_step_slowdown_proxy_ms",
    "peak_speed_ms",
    "deceleration_peak_ms",
    "restabilization_onset_ms",
    "stable_control_onset_ms",
)

# Readiness is deliberately defined in code, rather than trusted from a pack's
# editable manifest.  Otherwise a partially annotated pack could shrink its own
# scope and incorrectly become ready.
REQUIRED_PHASES_BY_INDICATOR = {
    "FS01-M02": ("preload_ms",),
    "FS01-M03": ("takeoff_proxy_ms",),
    "FS01-M04": ("landing_proxy_ms",),
    "FS01-M05": ("redistribution_ms", "initiation_ms"),
    "FS02-M02": ("direction_conversion_ms",),
    "FS02-M03": ("support_extension_proxy_ms", "lead_foot_motion_onset_proxy_ms"),
    "FS02-M04": ("lead_foot_motion_onset_proxy_ms",),
    "FS02-M05": ("first_step_slowdown_proxy_ms",),
    "FS09-M01": (),
    "FS09-M02": (),
    "FS09-M03": ("peak_speed_ms", "deceleration_peak_ms"),
    "FS09-M04": ("restabilization_onset_ms",),
    "FS09-M05": ("stable_control_onset_ms",),
}
REQUIRED_PHASES_BY_EVENT = {
    event_code: tuple(
        dict.fromkeys(
            phase
            for indicator_id in INDICATORS_BY_EVENT[event_code]
            for phase in REQUIRED_PHASES_BY_INDICATOR[indicator_id]
        )
    )
    for event_code in EVENT_CODES
}

# These are manual observations that cannot be represented by one keyframe.
# An accepted record may explicitly say observable=false, but only with a
# non-empty null_reason.  That records an honest limitation without fabricating
# direction, side, contact or camera-motion truth.
SEMANTIC_REQUIREMENTS_BY_INDICATOR = {
    "FS01-M02": (),
    "FS01-M03": ("bilateral_foot_contact_state",),
    "FS01-M04": (
        "left_foot_contact_or_settle",
        "right_foot_contact_or_settle",
        "post_landing_stable_interval",
    ),
    "FS01-M05": (),
    "FS02-M02": ("target_direction",),
    "FS02-M03": ("support_side",),
    "FS02-M04": ("launch_side", "launch_foot_contact_state"),
    "FS02-M05": (
        "launch_side",
        "first_step_contact_or_settle",
        "post_step_direction_interval",
    ),
    "FS09-M01": ("inertia_direction", "camera_motion_audit"),
    "FS09-M02": ("braking_side", "braking_foot_touchdown"),
    "FS09-M03": (),
    "FS09-M04": ("stable_interval",),
    "FS09-M05": ("stable_control_interval",),
}
SEMANTIC_TYPE_BY_KEY = {
    "bilateral_foot_contact_state": "contact_state",
    "left_foot_contact_or_settle": "timestamp",
    "right_foot_contact_or_settle": "timestamp",
    "post_landing_stable_interval": "interval",
    "target_direction": "direction",
    "support_side": "side",
    "launch_side": "side",
    "launch_foot_contact_state": "contact_state",
    "first_step_contact_or_settle": "timestamp",
    "post_step_direction_interval": "interval",
    "inertia_direction": "direction",
    "camera_motion_audit": "camera_audit",
    "braking_side": "side",
    "braking_foot_touchdown": "timestamp",
    "stable_interval": "interval",
    "stable_control_interval": "interval",
}
ALL_INDICATORS = tuple(
    indicator_id
    for event_code in EVENT_CODES
    for indicator_id in INDICATORS_BY_EVENT[event_code]
)

EVENT_CSV_FIELDS = (
    "video_id",
    "event_id",
    "event_code",
    "person_track_id",
    "start_ms",
    "end_ms",
    *EVENT_PHASES,
    "annotation_confidence",
    "boundary_uncertainty_ms",
    "view_group",
    "annotator_id",
    "reviewer_id",
    "adjudication_status",
    "quality_flags",
)
KEYPOINT_CSV_FIELDS = (
    "video_id",
    "source_frame_index",
    "timestamp_ms",
    "source_clip_ids",
    "primary_player_id",
    "joint_name",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "view_group",
    "annotator_id",
    "reviewer_id",
    "adjudication_status",
)
SEMANTIC_CSV_FIELDS = (
    "annotation_id",
    "video_id",
    "event_id",
    "candidate_event_id",
    "blind_clip_id",
    "indicator_id",
    "semantic_key",
    "semantic_type",
    "observable",
    "direction_deg",
    "coordinate_frame",
    "side",
    "timestamp_ms",
    "interval_start_ms",
    "interval_end_ms",
    "contact_state",
    "camera_motion_observed",
    "camera_audit_method",
    "null_reason",
    "annotation_confidence",
    "annotator_id",
    "reviewer_id",
    "adjudication_status",
)
COACH_CSV_FIELDS = (
    "annotation_id",
    "video_id",
    "event_id",
    "candidate_event_id",
    "blind_clip_id",
    "indicator_id",
    "annotator_id",
    "label_type",
    "grade",
    "rank_group_id",
    "rank",
)
REVIEW_CSV_FIELDS = (
    "video_id",
    "annotator_id",
    "full_video_review_completed",
    "reviewed_at",
    "notes",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _select_evenly(records: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    ordered = sorted(records, key=lambda item: (item["start_ms"], item["event_id"]))
    if count >= len(ordered):
        return ordered
    if count == 1:
        return [ordered[len(ordered) // 2]]
    indexes = [round(index * (len(ordered) - 1) / (count - 1)) for index in range(count)]
    return [ordered[index] for index in sorted(set(indexes))]


def _frame_metadata(root: Path, video_id: str) -> list[dict[str, int]]:
    frames_path = root / "runs" / "full-test" / video_id / "frames.jsonl"
    records = _read_jsonl(frames_path)
    return [
        {
            "source_frame_index": int(record["frame"]["index"]),
            "timestamp_ms": int(record["frame"]["timestamp_ms"]),
        }
        for record in records
    ]


def _json_for_html(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _write_workbench_assets(output_dir: Path) -> None:
    asset_dir = Path(__file__).with_name("assets")
    for name in ("truth-workbench.css", "truth-workbench.js"):
        source = asset_dir / name
        if not source.exists():
            raise FileNotFoundError(f"truth workbench asset is missing: {source}")
        (output_dir / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _review_html(manifest: dict[str, Any], output_dir: Path) -> str:
    # The browser bootstrap deliberately omits manifest.videos[*].path.  The
    # workbench uses pack-relative media URLs so review.html stays portable and
    # does not disclose an annotator machine's absolute path.
    browser_manifest = {
        "pack_version": manifest["pack_version"],
        "status": manifest["status"],
        "videos": [
            {
                "video_id": item["video_id"],
                "sha256": item["sha256"],
                "candidate_event_count": item["candidate_event_count"],
                "workbench_source": f"../../../FULL-TEST/{item['video_id']}.mp4",
            }
            for item in manifest["videos"]
        ],
        "pilot_keypoint_events": manifest["pilot_keypoint_events"],
        "safety": manifest["safety"],
    }
    bootstrap = {
        "workbench_version": TRUTH_WORKBENCH_VERSION,
        "manifest": browser_manifest,
        "event_codes": list(EVENT_CODES),
        "required_phases_by_event": {
            key: list(value) for key, value in REQUIRED_PHASES_BY_EVENT.items()
        },
        "keypoint_joints": list(KEYPOINT_JOINTS),
        "headers": {
            "events": list(EVENT_CSV_FIELDS),
            "keypoints": list(KEYPOINT_CSV_FIELDS),
            "semantics": list(SEMANTIC_CSV_FIELDS),
            "reviews": list(REVIEW_CSV_FIELDS),
        },
        "rows": {
            "events": _read_csv(output_dir / "event-annotations.csv"),
            "keypoints": _read_csv(output_dir / "keypoint-annotations.csv"),
            "semantics": _read_csv(output_dir / "semantic-annotations.csv"),
            "reviews": _read_csv(output_dir / "full-video-review-completion.csv"),
        },
        "safety": {
            "candidate_events_are_truth": False,
            "model_keypoints_embedded": False,
            "grades_or_thresholds_supported": False,
            "blank_is_missing_not_zero": True,
            "annotation_execution_authorized": False,
        },
    }
    return rf'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>RallyMate 离线真值标注工作台</title>
  <link rel="stylesheet" href="truth-workbench.css">
  <script id="truth-workbench-bootstrap" type="application/json">{_json_for_html(bootstrap)}</script>
  <script src="truth-workbench.js" defer></script>
</head>
<body>
<main class="shell">
  <header class="topbar">
    <h1>RallyMate FS01 / FS02 / FS09 离线真值标注工作台</h1>
    <p>工作台 <code id="workbench-version"></code> · 真值包 <code>{manifest["pack_version"]}</code></p>
    <div class="safety-badges">
      <span class="badge">不预填模型坐标</span><span class="badge">候选事件≠真值</span>
      <span class="badge">不生成 A～E</span><span class="badge">不生成阈值</span><span class="badge">0 是合法坐标</span>
    </div>
    <div class="identity-grid">
      <label>annotator_id<input id="identity-annotator" autocomplete="off" placeholder="稳定匿名 ID"></label>
      <label>reviewer_id<input id="identity-reviewer" autocomplete="off" placeholder="独立复核者 ID"></label>
      <label>view_group<input id="identity-view" autocomplete="off" placeholder="fixed-rear"></label>
      <div><strong id="active-video-id" class="mono"></strong><div id="workbench-status" class="status-line">工作台草稿保存在当前浏览器。</div></div>
    </div>
  </header>

  <div class="notice"><strong>第一遍盲化原则：</strong>先完整观看原视频，仅在“事件与阶段”页签独立建立人工 FS01/FS02/FS09。候选 ID/边界只在“密集关键点”页签显示，仅用于试点抽样和定位。</div>

  <section class="video-panel">
    <div class="video-toolbar">
      <label>原视频<select id="video-select"></select></label>
      <button id="play-pause" type="button">播放/暂停</button>
      <button type="button" data-step-ms="-100">-100 ms</button><button type="button" data-step-ms="-33">-33 ms</button>
      <span id="time-readout" class="time-readout">0 ms</span>
      <button type="button" data-step-ms="33">+33 ms</button><button type="button" data-step-ms="100">+100 ms</button>
    </div>
    <div id="video-stage" class="video-stage"><video id="workbench-video" controls preload="metadata"></video><canvas id="keypoint-canvas"></canvas></div>
  </section>

  <nav class="tabs" aria-label="标注阶段">
    <button type="button" data-tab="events">① 事件与阶段</button>
    <button type="button" data-tab="keypoints">② 密集关键点</button>
    <button type="button" data-tab="semantics">③ 事件语义</button>
    <button type="button" data-tab="export">④ 校验与导出</button>
  </nav>

  <section class="panel" data-panel="events">
    <div class="two-column">
      <div>
        <div class="card">
          <h3>在当前视频新建人工事件</h3>
          <div class="form-grid">
            <label>event_id<input id="new-event-id" placeholder="manual-fs01-001"></label>
            <label>event_code<select id="new-event-code"><option>FS01</option><option>FS02</option><option>FS09</option></select></label>
            <label>start_ms<input id="new-event-start" type="number" min="0"><button id="mark-new-start" type="button">当前时间作为 start</button></label>
            <label>end_ms<input id="new-event-end" type="number" min="0"><button id="mark-new-end" type="button">当前时间作为 end</button></label>
          </div>
          <div class="actions"><button id="add-event" type="button" class="primary">新建人工草稿</button></div>
        </div>
        <div class="card">
          <h3>人工事件 <span id="event-count" class="small"></span></h3>
          <div class="table-wrap"><table><thead><tr><th>ID</th><th>代码</th><th>start</th><th>end</th><th>状态</th></tr></thead><tbody id="event-list-body"></tbody></table></div>
        </div>
        <div class="card">
          <h3>全视频完整审阅声明</h3>
          <label><input id="review-confirm" type="checkbox" style="width:auto;min-height:auto"> 我已从头到尾审阅当前视频，而不是只看候选窗口。</label>
          <label>备注<textarea id="review-notes"></textarea></label>
          <div class="actions"><button id="mark-review-complete" type="button">记录完整审阅</button></div>
          <div class="table-wrap"><table><thead><tr><th>annotator</th><th>completed</th><th>reviewed_at</th><th>notes</th></tr></thead><tbody id="review-list-body"></tbody></table></div>
        </div>
      </div>
      <div class="card">
        <h3>事件边界、必需 phase 与裁决</h3>
        <p id="event-editor-empty" class="small">在左侧新建或选中一条人工事件。</p>
        <div id="event-editor" class="hidden">
          <div class="form-grid">
            <label>event_id<input id="event-edit-id"></label><label>event_code<select id="event-edit-code"><option>FS01</option><option>FS02</option><option>FS09</option></select></label>
            <label>person_track_id<input id="event-edit-track" type="number" min="1"></label><label>adjudication_status<select id="event-edit-status"><option>draft</option><option>accepted</option><option>rejected</option></select></label>
            <label>start_ms<input id="event-edit-start" type="number"></label><div class="actions"><button id="event-mark-start" type="button">用当前时间</button><button id="event-seek-start" type="button">定位</button></div>
            <label>end_ms<input id="event-edit-end" type="number"></label><div class="actions"><button id="event-mark-end" type="button">用当前时间</button><button id="event-seek-end" type="button">定位</button></div>
            <label>annotation_confidence (0..1)<input id="event-edit-confidence" type="number" min="0" max="1" step="0.01"></label>
            <label>boundary_uncertainty_ms<input id="event-edit-uncertainty" type="number" min="0"></label>
            <label>view_group<input id="event-edit-view"></label><label>annotator_id<input id="event-edit-annotator"></label>
            <label>reviewer_id<input id="event-edit-reviewer"></label><label>quality_flags (; 分隔)<input id="event-edit-flags"></label>
          </div>
          <h3>该事件族必需 phase</h3><div id="event-phases"></div>
          <div class="actions"><button id="save-event" type="button" class="primary">保存事件草稿</button><button id="delete-event" type="button" class="danger">删除草稿</button></div>
        </div>
      </div>
    </div>
  </section>

  <section class="panel" data-panel="keypoints">
    <div class="candidate-warning"><strong>第二阶段才可使用：</strong>下方候选边界是模型规则输出，不是真值。工作台不显示、不导入也不预填模型关键点。</div>
    <div class="form-grid"><label class="span-2">试点片段<select id="keypoint-clip"></select></label><label class="span-2">逐帧任务<select id="keypoint-frame"></select></label></div>
    <p id="candidate-context" class="small"></p><div id="keypoint-progress" class="progress"></div>
    <div class="two-column" style="margin-top:12px">
      <div class="card">
        <h3>人工坐标操作</h3>
        <ol><li>选择帧和关节，视频会 seek 到原始 timestamp。</li><li>可见：直接点击画面中的解剖学关节中心。</li><li>不可见：显式标记并填原因，不得点在模型推测位置。</li><li>复核后才设 accepted。</li></ol>
        <div class="actions"><button id="joint-prev" type="button">上一个任务</button><button id="joint-next" type="button">下一个任务</button></div>
      </div>
      <div class="card">
        <p id="keypoint-editor-empty" class="small">当前没有关键点任务。</p>
        <div id="keypoint-editor" class="hidden">
          <div id="joint-name" class="joint-name"></div><div id="joint-frame-meta" class="small mono"></div>
          <div class="form-grid">
            <label>visible<select id="joint-visible"><option value="">未标注</option><option value="true">true</option><option value="false">false</option></select></label>
            <label>x_normalized<input id="joint-x" type="number" min="0" max="1" step="0.000001"></label><label>y_normalized<input id="joint-y" type="number" min="0" max="1" step="0.000001"></label>
            <label>adjudication_status<select id="joint-status"><option>draft</option><option>accepted</option><option>rejected</option></select></label>
            <label class="span-4">visibility_reason<textarea id="joint-reason" placeholder="occluded_by_body / outside_frame / motion_blur ..."></textarea></label>
            <label>view_group<input id="joint-view"></label><label>annotator_id<input id="joint-annotator"></label><label>reviewer_id<input id="joint-reviewer"></label>
          </div>
          <div class="actions"><button id="joint-invisible" type="button">标记不可见（清空坐标）</button><button id="apply-frame-identity" type="button">将页顶身份应用到当前帧</button><button id="accept-frame" type="button" class="primary">检查并 accepted 整帧</button></div>
        </div>
      </div>
    </div>
  </section>

  <section class="panel" data-panel="semantics">
    <div class="notice">语义真值必须绑定到已裁决的人工 event_id。无法观察时选 <code>observable=false</code>，值列会清空，必须填 <code>null_reason</code>。</div>
    <div id="semantic-progress" class="progress"></div>
    <div class="two-column" style="margin-top:12px">
      <div class="card"><div class="table-wrap"><table><thead><tr><th>指标</th><th>semantic_key</th><th>人工事件</th><th>observable</th><th>状态</th></tr></thead><tbody id="semantic-list-body"></tbody></table></div></div>
      <div class="card">
        <p id="semantic-editor-empty" class="small">选择一个语义任务。</p>
        <div id="semantic-editor" class="hidden">
          <h3 id="semantic-title"></h3><p id="semantic-meta" class="small"></p>
          <div class="form-grid">
            <label class="span-2">event_id<select id="semantic-event"></select></label><label>observable<select id="semantic-observable"><option value="">未标注</option><option value="true">true</option><option value="false">false</option></select></label>
            <label>adjudication_status<select id="semantic-status"><option>draft</option><option>accepted</option><option>rejected</option></select></label>
            <label>annotation_confidence<input id="semantic-confidence" type="number" min="0" max="1" step="0.01"></label><label>annotator_id<input id="semantic-annotator"></label><label>reviewer_id<input id="semantic-reviewer"></label>
          </div>
          <div class="semantic-value-fields">
            <div data-semantic-group="direction"><div class="form-grid"><label>direction_deg<input data-semantic-field="direction_deg" type="number" min="-180" max="180" step="0.1"></label><label>coordinate_frame<select data-semantic-field="coordinate_frame"><option value=""></option><option>image_plane</option><option>court_plane</option></select></label></div></div>
            <div data-semantic-group="side"><label>side<select data-semantic-field="side"><option value=""></option><option>left</option><option>right</option><option>bilateral</option></select></label></div>
            <div data-semantic-group="timestamp"><div class="value-row"><label>timestamp_ms<input data-semantic-field="timestamp_ms" type="number"></label><button type="button" data-mark-semantic="timestamp_ms">用当前时间</button></div></div>
            <div data-semantic-group="interval"><div class="value-row"><label>interval_start_ms<input data-semantic-field="interval_start_ms" type="number"></label><button type="button" data-mark-semantic="interval_start_ms">用当前时间</button></div><div class="value-row"><label>interval_end_ms<input data-semantic-field="interval_end_ms" type="number"></label><button type="button" data-mark-semantic="interval_end_ms">用当前时间</button></div></div>
            <div data-semantic-group="contact_state"><label>contact_state<select data-semantic-field="contact_state"><option value=""></option><option>contact</option><option>airborne</option><option>settled</option></select></label></div>
            <div data-semantic-group="camera_audit"><div class="form-grid"><label>camera_motion_observed<select data-semantic-field="camera_motion_observed"><option value=""></option><option>true</option><option>false</option></select></label><label class="span-2">camera_audit_method<input data-semantic-field="camera_audit_method"></label></div></div>
            <div id="semantic-null-group"><label>null_reason<textarea id="semantic-null-reason" placeholder="not_observable_in_fixed_2d_video"></textarea></label></div>
          </div>
          <div class="actions"><button id="apply-semantic-identity" type="button">应用页顶身份</button><button id="semantic-prev" type="button">上一项</button><button id="semantic-next" type="button">下一项</button></div>
        </div>
      </div>
    </div>
  </section>

  <section class="panel" data-panel="export">
    <h2>校验、保存与编译</h2>
    <p>浏览器草稿不会自动修改仓库。使用以下任一方式显式保存四份 CSV；它们与现有 Python CSV→JSONL 编译器直接兼容。</p>
    <div class="export-grid">
      <button id="validate-workbench" type="button">快速检查 accepted 记录</button>
      <button id="write-directory" type="button" class="primary">选择真值包目录并写入 4 CSV</button>
      <button id="download-all" type="button">下载全部 4 CSV</button>
      <label>导入已有 CSV 继续标注<input id="import-csv" type="file" accept=".csv,text/csv" multiple></label>
    </div>
    <div class="actions">
      <button type="button" data-download="event-annotations.csv">下载 event CSV</button><button type="button" data-download="keypoint-annotations.csv">下载 keypoint CSV</button>
      <button type="button" data-download="semantic-annotations.csv">下载 semantic CSV</button><button type="button" data-download="full-video-review-completion.csv">下载 review CSV</button>
      <button id="reset-workbench" type="button" class="danger">清空本地草稿并恢复内嵌模板</button>
    </div>
    <ul id="validation-list" class="validation-list"></ul>
    <div class="notice"><strong>最终校验必须在仓库根目录的终端运行：</strong><br><code>$env:PYTHONPATH="$PWD\src"</code><br><code>python .\scripts\compile_scoring_truth_pack.py --pack .\data\annotations\scoring-truth-pack-v1</code><br>浏览器快速检查不代替 schema/就绪矩阵校验。编译器才生成 <code>compiled/*.jsonl</code>。</div>
  </section>

  <footer class="footer">工作台不包含模型关键点、教练等级或评分阈值。候选边界仅为关键点试点抽样元数据。
  </footer>
</main>
</body>
</html>'''


def build_truth_pack(
    root: str | Path,
    output_dir: str | Path,
    *,
    events_per_code_per_video: int = 1,
    coach_slots: int = 3,
    candidate_events_template: str = DEFAULT_CANDIDATE_EVENTS_TEMPLATE,
) -> dict[str, Any]:
    root = Path(root).resolve()
    output_dir = Path(output_dir).resolve()
    if events_per_code_per_video < 1 or coach_slots < 2:
        raise ValueError("truth pack requires >=1 pilot event and >=2 coach slots")
    if "{video_id}" not in candidate_events_template:
        raise ValueError("candidate_events_template must contain {video_id}")
    if output_dir.exists():
        raise FileExistsError(
            "truth pack output already exists; choose a new immutable path: "
            f"{output_dir}"
        )
    output_dir.mkdir(parents=True)

    selected_events: list[dict[str, Any]] = []
    videos = []
    candidate_sources = []
    dense_frames: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    for video_id in VIDEO_IDS:
        video_path = root / "FULL-TEST" / f"{video_id}.mp4"
        rendered_events_path = Path(
            candidate_events_template.format(video_id=video_id)
        )
        events_path = (
            rendered_events_path
            if rendered_events_path.is_absolute()
            else root / rendered_events_path
        ).resolve()
        events = _read_jsonl(events_path)
        detector_versions = sorted(
            {
                str(event.get("provenance", {}).get("detector_version"))
                for event in events
                if event.get("provenance", {}).get("detector_version")
            }
        )
        source_ids = sorted(
            {
                str(event.get("provenance", {}).get("source_id"))
                for event in events
                if event.get("provenance", {}).get("source_id")
            }
        )
        candidate_source = {
            "video_id": video_id,
            "events_path": str(events_path),
            "events_sha256": sha256_file(events_path),
            "event_count": len(events),
            "detector_versions": detector_versions,
            "source_ids": source_ids,
            "semantics": "pose_event_candidates_only_not_manual_truth",
        }
        candidate_sources.append(candidate_source)
        frames = _frame_metadata(root, video_id)
        videos.append(
            {
                "video_id": video_id,
                "path": str(video_path),
                "sha256": sha256_file(video_path),
                "full_video_event_annotation_required": True,
                "candidate_event_count": len(events),
                "candidate_events_sha256": candidate_source["events_sha256"],
            }
        )
        for event_code in EVENT_CODES:
            choices = _select_evenly(
                [event for event in events if event["event_code"] == event_code],
                events_per_code_per_video,
            )
            for event in choices:
                blind_clip_id = f"pilot-{len(selected_events) + 1:03d}"
                selected_events.append(
                    {
                        "blind_clip_id": blind_clip_id,
                        "video_id": video_id,
                        "candidate_event_id": event["event_id"],
                        "event_code": event_code,
                        "candidate_start_ms": int(event["start_ms"]),
                        "candidate_end_ms": int(event["end_ms"]),
                        "candidate_source": "versioned_pose_event_bundle_not_truth",
                        "candidate_events_sha256": candidate_source["events_sha256"],
                        "candidate_detector_version": event.get("provenance", {}).get(
                            "detector_version"
                        ),
                        "candidate_source_id": event.get("provenance", {}).get(
                            "source_id"
                        ),
                        "quality_flags": event.get("quality_flags", []),
                    }
                )
                for frame in frames:
                    if int(event["start_ms"]) <= frame["timestamp_ms"] <= int(event["end_ms"]):
                        dense_frames[
                            (video_id, frame["source_frame_index"], frame["timestamp_ms"])
                        ].add(blind_clip_id)

    _write_csv(output_dir / "event-annotations.csv", list(EVENT_CSV_FIELDS), [])

    semantic_rows = []
    for event in selected_events:
        for indicator_id in INDICATORS_BY_EVENT[event["event_code"]]:
            for semantic_key in SEMANTIC_REQUIREMENTS_BY_INDICATOR[indicator_id]:
                semantic_rows.append(
                    {
                        "annotation_id": (
                            f"{event['blind_clip_id']}-{indicator_id}-{semantic_key}"
                        ),
                        "video_id": event["video_id"],
                        "event_id": "",
                        "candidate_event_id": event["candidate_event_id"],
                        "blind_clip_id": event["blind_clip_id"],
                        "indicator_id": indicator_id,
                        "semantic_key": semantic_key,
                        "semantic_type": SEMANTIC_TYPE_BY_KEY[semantic_key],
                        "observable": "",
                        "direction_deg": "",
                        "coordinate_frame": "",
                        "side": "",
                        "timestamp_ms": "",
                        "interval_start_ms": "",
                        "interval_end_ms": "",
                        "contact_state": "",
                        "camera_motion_observed": "",
                        "camera_audit_method": "",
                        "null_reason": "",
                        "annotation_confidence": "",
                        "annotator_id": "",
                        "reviewer_id": "",
                        "adjudication_status": "",
                    }
                )
    _write_csv(
        output_dir / "semantic-annotations.csv",
        list(SEMANTIC_CSV_FIELDS),
        semantic_rows,
    )

    keypoint_rows = []
    for (video_id, frame, timestamp_ms), clip_ids in sorted(dense_frames.items()):
        for joint in KEYPOINT_JOINTS:
            keypoint_rows.append(
                {
                    "video_id": video_id,
                    "source_frame_index": frame,
                    "timestamp_ms": timestamp_ms,
                    "source_clip_ids": ";".join(sorted(clip_ids)),
                    "primary_player_id": 1,
                    "joint_name": joint,
                    "visible": "",
                    "x_normalized": "",
                    "y_normalized": "",
                    "visibility_reason": "",
                    "view_group": "",
                    "annotator_id": "",
                    "reviewer_id": "",
                    "adjudication_status": "",
                }
            )
    _write_csv(
        output_dir / "keypoint-annotations.csv",
        list(KEYPOINT_CSV_FIELDS),
        keypoint_rows,
    )

    coach_rows = []
    for event in selected_events:
        for indicator_id in INDICATORS_BY_EVENT[event["event_code"]]:
            for slot in range(1, coach_slots + 1):
                for label_type in ("grade", "ranking"):
                    coach_rows.append(
                        {
                            "annotation_id": f"{event['blind_clip_id']}-{indicator_id}-coach-{slot}-{label_type}",
                            "video_id": event["video_id"],
                            "event_id": "",
                            "candidate_event_id": event["candidate_event_id"],
                            "blind_clip_id": event["blind_clip_id"],
                            "indicator_id": indicator_id,
                            "annotator_id": f"coach-{slot}",
                            "label_type": label_type,
                            "grade": "",
                            "rank_group_id": f"pilot-{indicator_id}",
                            "rank": "",
                        }
                    )
    _write_csv(
        output_dir / "coach-labels.csv",
        list(COACH_CSV_FIELDS),
        coach_rows,
    )
    _write_csv(
        output_dir / "full-video-review-completion.csv",
        list(REVIEW_CSV_FIELDS),
        [
            {
                "video_id": video_id,
                "annotator_id": f"event-annotator-{slot}",
                "full_video_review_completed": "false",
                "reviewed_at": "",
                "notes": "",
            }
            for video_id in VIDEO_IDS
            for slot in range(1, 3)
        ],
    )

    manifest = {
        "schema_version": "1.0.0",
        "pack_version": TRUTH_PACK_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "annotation_required_no_truth_or_thresholds_generated",
        "scope": {
            "events": list(EVENT_CODES),
            "indicators": sorted(
                indicator
                for values in INDICATORS_BY_EVENT.values()
                for indicator in values
            ),
            "pose_candidate": "rtmpose-m-halpe26-online",
            "pose_models_for_error_comparison": [
                "rtmpose-m-halpe26-256x192",
                "rtmpose-m-coco-wholebody133-256x192",
            ],
        },
        "candidate_source_binding": {
            "events_path_template": candidate_events_template,
            "sources": candidate_sources,
            "candidate_events_are_truth": False,
        },
        "protocol": {
            "event_pass": "independent full-video annotation before candidate review",
            "event_candidates_are_truth": False,
            "keypoint_pass": "dense per-frame annotation on pilot intervals; accepted adjudication only",
            "semantic_pass": "adjudicated manual observation or explicit observable=false plus null_reason",
            "coach_pass": "at least two independent coaches on every video-indicator cell; A-E or within-indicator ranking",
            "readiness_matrix": READINESS_MATRIX_VERSION,
            "missing_policy": "null/blank means missing; zero is a valid coordinate and never a missing sentinel",
            "promotion": "manual review required; this pack cannot promote F2/F3/F4 automatically",
            "workbench": (
                f"{TRUTH_WORKBENCH_VERSION}; browser draft only; explicit CSV export "
                "and Python compilation required"
            ),
        },
        "videos": videos,
        "pilot_keypoint_events": selected_events,
        "counts": {
            "pilot_events": len(selected_events),
            "dense_keypoint_frames": len(dense_frames),
            "keypoint_joint_rows": len(keypoint_rows),
            "semantic_annotation_rows": len(semantic_rows),
            "coach_label_rows": len(coach_rows),
        },
        "contracts": {
            "events": "contracts/events.schema.json",
            "keypoints": "contracts/keypoint-ground-truth.schema.json",
            "semantics": "contracts/semantic-ground-truth.schema.json",
            "coach_labels": "contracts/coach-label.schema.json",
        },
        "safety": {
            "generated_event_truth": False,
            "generated_keypoint_truth": False,
            "generated_coach_labels": False,
            "generated_thresholds": False,
            "model_keypoints_embedded_in_workbench": False,
            "private_authority_template_only": True,
            "annotation_execution_authorized": False,
            "external_protocol_receipt_verified": False,
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_workbench_assets(output_dir)
    (output_dir / "review.html").write_text(
        _review_html(manifest, output_dir), encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# RallyMate 评分真值私有权威模板\n\n"
        "**HARD STOP：本目录含模型候选边界和私有源谱系，不得分发、不得作为 HTTP 根，"
        "也不得让 A/B/C 或教练直接在此开始标注。**\n\n"
        "`review.html` 只供中央技术 QA；没有外部协议回执和独立 authorized handoff，"
        "所有标注、接受、导入与导出控件均应禁用。M89 已提供与本目录隔离的三视频全片"
        " technical handoff，但它仍不授权标注；未来人工数据必须来自角色隔离、"
        "版本化的公开授权包，并通过原子 intake 建立新的私有不可变会话。\n\n"
        "不得用构建命令覆盖本目录，也不得直接覆盖五份 CSV 或原地重编译。"
        "本模板当前不包含任何人工真值、A～E 标签或评分阈值。\n",
        encoding="utf-8",
    )
    return manifest


def _number(value: str, kind: type[int] | type[float]) -> int | float | None:
    stripped = str(value).strip()
    return kind(stripped) if stripped else None


def _required_boolean(value: str, field: str) -> bool:
    stripped = str(value).strip().lower()
    if stripped not in {"true", "false"}:
        raise ValueError(f"{field} must be true or false")
    return stripped == "true"


def _indicator_event_code(indicator_id: str) -> str:
    for event_code, indicator_ids in INDICATORS_BY_EVENT.items():
        if indicator_id in indicator_ids:
            return event_code
    raise ValueError(f"indicator_id is outside the fixed 13-indicator scope: {indicator_id}")


def _semantic_value_from_row(
    row: dict[str, str],
    *,
    semantic_type: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    if semantic_type == "direction":
        direction = _number(row.get("direction_deg", ""), float)
        coordinate_frame = row.get("coordinate_frame", "").strip()
        if direction is None or not -180 <= float(direction) <= 180:
            raise ValueError("observable direction requires direction_deg in -180..180")
        if coordinate_frame not in {"image_plane", "court_plane"}:
            raise ValueError(
                "observable direction requires coordinate_frame=image_plane or court_plane"
            )
        return {
            "direction_deg": float(direction),
            "coordinate_frame": coordinate_frame,
        }
    if semantic_type == "side":
        side = row.get("side", "").strip().lower()
        if side not in {"left", "right", "bilateral"}:
            raise ValueError("observable side requires left, right or bilateral")
        return {"side": side}
    if semantic_type == "timestamp":
        timestamp_ms = _number(row.get("timestamp_ms", ""), int)
        if timestamp_ms is None or not event["start_ms"] <= timestamp_ms <= event["end_ms"]:
            raise ValueError("observable timestamp must be inside the accepted manual event")
        return {"timestamp_ms": timestamp_ms}
    if semantic_type == "interval":
        start_ms = _number(row.get("interval_start_ms", ""), int)
        end_ms = _number(row.get("interval_end_ms", ""), int)
        if (
            start_ms is None
            or end_ms is None
            or not event["start_ms"] <= start_ms < end_ms <= event["end_ms"]
        ):
            raise ValueError("observable interval must be ordered and inside the manual event")
        return {"start_ms": start_ms, "end_ms": end_ms}
    if semantic_type == "contact_state":
        state = row.get("contact_state", "").strip().lower()
        if state not in {"contact", "airborne", "settled"}:
            raise ValueError(
                "observable contact_state requires contact, airborne or settled"
            )
        return {"contact_state": state}
    if semantic_type == "camera_audit":
        motion = _required_boolean(
            row.get("camera_motion_observed", ""), "camera_motion_observed"
        )
        method = row.get("camera_audit_method", "").strip()
        if not method:
            raise ValueError("observable camera_audit requires camera_audit_method")
        return {"camera_motion_observed": motion, "audit_method": method}
    raise ValueError(f"unsupported semantic_type: {semantic_type}")


def _row_has_semantic_value(row: dict[str, str]) -> bool:
    return any(
        row.get(field, "").strip()
        for field in (
            "direction_deg",
            "coordinate_frame",
            "side",
            "timestamp_ms",
            "interval_start_ms",
            "interval_end_ms",
            "contact_state",
            "camera_motion_observed",
            "camera_audit_method",
        )
    )


def _has_exact_string_members(value: Any, expected: tuple[str, ...]) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) for item in value)
        and len(value) == len(expected)
        and set(value) == set(expected)
    )


def compile_truth_pack(pack_dir: str | Path) -> dict[str, Any]:
    pack_dir = Path(pack_dir).resolve()
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    output_dir = pack_dir / "compiled"
    errors: list[str] = []

    manifest_videos = manifest.get("videos")
    if not isinstance(manifest_videos, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("video_id"), str)
        for item in manifest_videos or []
    ):
        raise ValueError("truth-pack manifest.videos must be an array of video objects")
    declared_video_ids = tuple(item["video_id"] for item in manifest_videos)
    if manifest.get("pack_version") == TRUTH_PACK_VERSION:
        scope = manifest.get("scope")
        if not isinstance(scope, dict):
            errors.append("current truth-pack manifest.scope is missing")
        else:
            declared_events = scope.get("events")
            if not _has_exact_string_members(declared_events, EVENT_CODES):
                errors.append(
                    "current truth-pack event scope must exactly match EVENT_CODES"
                )
            declared_indicators = scope.get("indicators")
            if not _has_exact_string_members(declared_indicators, ALL_INDICATORS):
                errors.append(
                    "current truth-pack indicator scope must exactly match ALL_INDICATORS"
                )
        if not _has_exact_string_members(list(declared_video_ids), VIDEO_IDS):
            errors.append(
                "current truth-pack video scope must exactly match VIDEO_IDS"
            )
        # A current-version pack cannot make readiness easier by editing its own
        # manifest.  Continue the diagnostic matrix against the canonical scope
        # even when the declared scope is invalid.
        expected_videos = set(VIDEO_IDS)
    else:
        # Synthetic/legacy fixtures retain their explicitly declared scope.
        expected_videos = set(declared_video_ids)

    event_records = []
    non_adjudicated_event_rows = 0
    accepted_event_keys: set[tuple[str, str]] = set()
    for row_number, row in enumerate(_read_csv(pack_dir / "event-annotations.csv"), start=2):
        if not row.get("event_id", "").strip():
            continue
        if row.get("adjudication_status", "").strip().lower() != "accepted":
            non_adjudicated_event_rows += 1
            continue
        try:
            start_ms = int(row["start_ms"])
            end_ms = int(row["end_ms"])
            phases = {
                phase: _number(row.get(phase, ""), int) for phase in EVENT_PHASES
            }
            record = {
                "schema_version": "1.0.0",
                "video_id": row["video_id"].strip(),
                "event_id": row["event_id"].strip(),
                "person_track_id": int(row.get("person_track_id") or 1),
                "event_code": row["event_code"].strip(),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "key_phases_ms": phases,
                "confidence": float(row["annotation_confidence"]),
                "boundary_uncertainty_ms": int(row["boundary_uncertainty_ms"]),
                "quality_flags": [
                    item for item in row.get("quality_flags", "").split(";") if item
                ],
                "view_group": row["view_group"].strip(),
                "annotation_source": "manual",
                "annotator_id": row["annotator_id"].strip(),
                "reviewer_id": row.get("reviewer_id", "").strip(),
                "adjudication_status": "accepted",
                "provenance": {
                    "truth_pack_version": manifest["pack_version"],
                    "annotation_method": "independent_full_video_manual_review",
                },
            }
            if record["video_id"] not in expected_videos:
                raise ValueError("event video_id is not declared in manifest.videos")
            event_key = (record["video_id"], record["event_id"])
            if event_key in accepted_event_keys:
                raise ValueError("duplicate accepted event_id; adjudicate to one final event")
            accepted_event_keys.add(event_key)
            validate_event_record(record, annotation=True)
            event_records.append(record)
        except Exception as exc:
            errors.append(f"event-annotations.csv row {row_number}: {exc}")

    keypoint_rows = _read_csv(pack_dir / "keypoint-annotations.csv")
    pending_keypoint_rows = 0
    accepted_groups: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    accepted_joint_keys: set[tuple[str, int, str]] = set()
    for row_number, row in enumerate(keypoint_rows, start=2):
        if row.get("adjudication_status", "").strip().lower() != "accepted":
            pending_keypoint_rows += 1
            continue
        try:
            video_id = row["video_id"].strip()
            frame = int(row["source_frame_index"])
            joint = row["joint_name"].strip()
            duplicate_key = (video_id, frame, joint)
            if duplicate_key in accepted_joint_keys:
                raise ValueError("duplicate accepted joint; adjudicate to one truth value")
            accepted_joint_keys.add(duplicate_key)
            visible_text = row["visible"].strip().lower()
            if visible_text not in {"true", "false"}:
                raise ValueError("visible must be true or false")
            visible = visible_text == "true"
            x = _number(row.get("x_normalized", ""), float)
            y = _number(row.get("y_normalized", ""), float)
            annotator_id = row["annotator_id"].strip()
            view_group = row["view_group"].strip()
            key = (video_id, frame, annotator_id, view_group)
            record = accepted_groups.setdefault(
                key,
                {
                    "schema_version": "1.0.0",
                    "video_id": video_id,
                    "source_frame_index": frame,
                    "timestamp_ms": int(row["timestamp_ms"]),
                    "primary_player_id": 1,
                    "annotator_id": annotator_id,
                    "view_group": view_group,
                    "joints": {},
                    "reviewer_id": row.get("reviewer_id", "").strip(),
                    "adjudication_status": "accepted",
                },
            )
            record["joints"][joint] = {
                "visible": visible,
                "x_normalized": x if visible else None,
                "y_normalized": y if visible else None,
                "visibility_reason": row.get("visibility_reason", "").strip() or None,
            }
        except Exception as exc:
            errors.append(f"keypoint-annotations.csv row {row_number}: {exc}")
    keypoint_records = []
    for record in accepted_groups.values():
        try:
            validate_keypoint_annotation(record)
            if record["video_id"] not in expected_videos:
                raise ValueError("keypoint video_id is not declared in manifest.videos")
            keypoint_records.append(record)
        except Exception as exc:
            errors.append(
                f"keypoint frame {record.get('video_id')}:{record.get('source_frame_index')}: {exc}"
            )

    accepted_event_by_id = {
        (record["video_id"], record["event_id"]): record for record in event_records
    }
    semantic_rows = _read_csv(pack_dir / "semantic-annotations.csv")
    pending_semantic_rows = 0
    semantic_records = []
    accepted_semantic_keys: set[tuple[str, str, str, str]] = set()
    for row_number, row in enumerate(semantic_rows, start=2):
        if row.get("adjudication_status", "").strip().lower() != "accepted":
            pending_semantic_rows += 1
            continue
        try:
            video_id = row["video_id"].strip()
            event_id = row["event_id"].strip()
            indicator_id = row["indicator_id"].strip()
            event = accepted_event_by_id.get((video_id, event_id))
            if event is None:
                raise ValueError(
                    "semantic event_id must reference an accepted manual event"
                )
            if _indicator_event_code(indicator_id) != event["event_code"]:
                raise ValueError(
                    "semantic indicator_id must match the referenced event_code"
                )
            semantic_key = row["semantic_key"].strip()
            if semantic_key not in SEMANTIC_REQUIREMENTS_BY_INDICATOR[indicator_id]:
                raise ValueError(
                    "semantic_key is not required by the referenced indicator"
                )
            semantic_type = row["semantic_type"].strip()
            if semantic_type != SEMANTIC_TYPE_BY_KEY[semantic_key]:
                raise ValueError("semantic_type does not match semantic_key")
            observable = _required_boolean(row.get("observable", ""), "observable")
            null_reason = row.get("null_reason", "").strip() or None
            if observable:
                if null_reason is not None:
                    raise ValueError(
                        "observable semantic truth must not include null_reason"
                    )
                value = _semantic_value_from_row(
                    row, semantic_type=semantic_type, event=event
                )
            else:
                if null_reason is None:
                    raise ValueError(
                        "observable=false requires a non-empty null_reason"
                    )
                if _row_has_semantic_value(row):
                    raise ValueError(
                        "observable=false must not retain a semantic value"
                    )
                value = None
            annotation_confidence = float(row["annotation_confidence"])
            if not 0 <= annotation_confidence <= 1:
                raise ValueError("annotation_confidence must be in 0..1")
            annotator_id = row["annotator_id"].strip()
            reviewer_id = row["reviewer_id"].strip()
            annotation_id = row["annotation_id"].strip()
            if not annotation_id or not annotator_id or not reviewer_id:
                raise ValueError(
                    "accepted semantic truth requires annotation_id, annotator_id and reviewer_id"
                )
            record_key = (video_id, event_id, indicator_id, semantic_key)
            if record_key in accepted_semantic_keys:
                raise ValueError(
                    "duplicate accepted semantic truth; adjudicate to one final value"
                )
            accepted_semantic_keys.add(record_key)
            semantic_records.append(
                {
                    "schema_version": "1.0.0",
                    "annotation_id": annotation_id,
                    "video_id": video_id,
                    "event_id": event_id,
                    "indicator_id": indicator_id,
                    "semantic_key": semantic_key,
                    "semantic_type": semantic_type,
                    "observable": observable,
                    "value": value,
                    "null_reason": null_reason,
                    "annotation_confidence": annotation_confidence,
                    "annotator_id": annotator_id,
                    "reviewer_id": reviewer_id,
                    "adjudication_status": "accepted",
                }
            )
        except Exception as exc:
            errors.append(f"semantic-annotations.csv row {row_number}: {exc}")

    coach_records = []
    coach_annotation_ids: set[str] = set()
    coach_item_keys: set[tuple[str, str, str, str, str]] = set()
    for row_number, row in enumerate(_read_csv(pack_dir / "coach-labels.csv"), start=2):
        label_type = row.get("label_type", "").strip()
        label_value = row.get("grade", "").strip() if label_type == "grade" else row.get("rank", "").strip()
        if not label_value:
            continue
        try:
            record = {
                "schema_version": "1.0.0",
                "annotation_id": row["annotation_id"].strip(),
                "video_id": row["video_id"].strip(),
                "event_id": row["event_id"].strip(),
                "indicator_id": row["indicator_id"].strip(),
                "annotator_id": row["annotator_id"].strip(),
                "label_type": label_type,
            }
            event = accepted_event_by_id.get((record["video_id"], record["event_id"]))
            if event is None:
                raise ValueError(
                    "coach event_id must reference an accepted manual event, not a candidate event"
                )
            if _indicator_event_code(record["indicator_id"]) != event["event_code"]:
                raise ValueError(
                    "coach indicator_id must match the referenced event_code"
                )
            if label_type == "grade":
                record["grade"] = row["grade"].strip()
            else:
                record["rank_group_id"] = row["rank_group_id"].strip()
                record["rank"] = int(row["rank"])
            validate_coach_label(record)
            if record["annotation_id"] in coach_annotation_ids:
                raise ValueError("duplicate coach annotation_id")
            item_key = (
                record["video_id"],
                record["event_id"],
                record["indicator_id"],
                record["annotator_id"],
                record["label_type"],
            )
            if item_key in coach_item_keys:
                raise ValueError(
                    "duplicate coach item for one annotator and label_type"
                )
            coach_annotation_ids.add(record["annotation_id"])
            coach_item_keys.add(item_key)
            coach_records.append(record)
        except Exception as exc:
            errors.append(f"coach-labels.csv row {row_number}: {exc}")

    review_rows = _read_csv(pack_dir / "full-video-review-completion.csv")
    review_annotators_by_video: dict[str, set[str]] = defaultdict(set)
    for row_number, row in enumerate(review_rows, start=2):
        completed = row.get("full_video_review_completed", "").strip().lower()
        if completed not in {"", "true", "false"}:
            errors.append(
                "full-video-review-completion.csv row "
                f"{row_number}: full_video_review_completed must be true or false"
            )
            continue
        if completed != "true":
            continue
        video_id = row.get("video_id", "").strip()
        annotator_id = row.get("annotator_id", "").strip()
        if video_id not in expected_videos:
            errors.append(
                "full-video-review-completion.csv row "
                f"{row_number}: video_id is not declared in manifest.videos"
            )
            continue
        if not annotator_id:
            errors.append(
                "full-video-review-completion.csv row "
                f"{row_number}: completed review requires annotator_id"
            )
            continue
        review_annotators_by_video[video_id].add(annotator_id)
    review_coverage = []
    for video_id in sorted(expected_videos):
        annotator_ids = sorted(review_annotators_by_video[video_id])
        review_coverage.append(
            {
                "video_id": video_id,
                "required_annotator_count": MIN_EVENT_REVIEW_ANNOTATORS,
                "annotator_ids": annotator_ids,
                "annotator_count": len(annotator_ids),
                "covered": len(annotator_ids) >= MIN_EVENT_REVIEW_ANNOTATORS,
            }
        )
    full_video_review_ready = bool(review_coverage) and all(
        item["covered"] for item in review_coverage
    )

    events_by_video_code: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in event_records:
        events_by_video_code[(event["video_id"], event["event_code"])].append(event)
    event_phase_coverage = []
    phase_complete_events_by_cell: dict[tuple[str, str], set[str]] = defaultdict(set)
    for video_id in sorted(expected_videos):
        for event_code in EVENT_CODES:
            events = events_by_video_code[(video_id, event_code)]
            required_phases = REQUIRED_PHASES_BY_EVENT[event_code]
            event_details = []
            for event in events:
                missing_phases = [
                    phase
                    for phase in required_phases
                    if event["key_phases_ms"].get(phase) is None
                ]
                if not missing_phases:
                    phase_complete_events_by_cell[(video_id, event_code)].add(
                        event["event_id"]
                    )
                event_details.append(
                    {
                        "event_id": event["event_id"],
                        "missing_required_phases": missing_phases,
                        "covered": not missing_phases,
                    }
                )
            event_phase_coverage.append(
                {
                    "video_id": video_id,
                    "event_code": event_code,
                    "required_phases": list(required_phases),
                    "accepted_event_count": len(events),
                    "phase_complete_event_count": sum(
                        1 for item in event_details if item["covered"]
                    ),
                    "events": event_details,
                    "covered": bool(events)
                    and all(item["covered"] for item in event_details),
                }
            )
    event_boundary_ready = bool(event_phase_coverage) and all(
        item["accepted_event_count"] > 0 for item in event_phase_coverage
    )
    phase_ready = bool(event_phase_coverage) and all(
        item["covered"] for item in event_phase_coverage
    )
    event_ready = full_video_review_ready and event_boundary_ready and phase_ready

    keypoint_coverage = []
    for video_id in sorted(expected_videos):
        video_keypoints = [
            record for record in keypoint_records if record["video_id"] == video_id
        ]
        for event_code in EVENT_CODES:
            fully_phased_events = [
                event
                for event in events_by_video_code[(video_id, event_code)]
                if event["event_id"]
                in phase_complete_events_by_cell[(video_id, event_code)]
            ]
            complete_frame_ids = set()
            observable_core_frame_ids = set()
            covered_event_ids = set()
            for record in video_keypoints:
                timestamp_ms = record["timestamp_ms"]
                matching_events = [
                    event
                    for event in fully_phased_events
                    if event["start_ms"] <= timestamp_ms <= event["end_ms"]
                ]
                if not matching_events:
                    continue
                all_required_joints_recorded = set(KEYPOINT_JOINTS) <= set(
                    record["joints"]
                )
                if all_required_joints_recorded:
                    complete_frame_ids.add(record["source_frame_index"])
                if all_required_joints_recorded and all(
                    record["joints"].get(joint, {}).get("visible") is True
                    for joint in SCORING_CORE_KEYPOINT_JOINTS
                ):
                    observable_core_frame_ids.add(record["source_frame_index"])
                    covered_event_ids.update(event["event_id"] for event in matching_events)
            keypoint_coverage.append(
                {
                    "video_id": video_id,
                    "event_code": event_code,
                    "phase_complete_event_ids": [
                        event["event_id"] for event in fully_phased_events
                    ],
                    "covered_event_ids": sorted(covered_event_ids),
                    "complete_joint_frame_count": len(complete_frame_ids),
                    "observable_core_frame_count": len(observable_core_frame_ids),
                    "covered": bool(covered_event_ids),
                }
            )
    expected_counts = manifest.get("counts", {})
    expected_dense_frames = int(expected_counts.get("dense_keypoint_frames") or 0)
    expected_joint_rows = int(expected_counts.get("keypoint_joint_rows") or 0)
    accepted_joint_rows = sum(len(record["joints"]) for record in keypoint_records)
    keypoint_count_ready = (
        len(keypoint_records) >= expected_dense_frames
        and accepted_joint_rows >= expected_joint_rows
    )
    keypoint_ready = (
        bool(keypoint_coverage)
        and all(item["covered"] for item in keypoint_coverage)
        and pending_keypoint_rows == 0
        and keypoint_count_ready
    )

    semantics_by_item: dict[
        tuple[str, str, str], dict[str, dict[str, Any]]
    ] = defaultdict(dict)
    for record in semantic_records:
        semantics_by_item[
            (record["video_id"], record["event_id"], record["indicator_id"])
        ][record["semantic_key"]] = record
    semantic_coverage = []
    for video_id in sorted(expected_videos):
        for indicator_id in ALL_INDICATORS:
            event_code = _indicator_event_code(indicator_id)
            required_keys = SEMANTIC_REQUIREMENTS_BY_INDICATOR[indicator_id]
            eligible_events = events_by_video_code[(video_id, event_code)]
            complete_event_ids = []
            fully_observable_event_ids = []
            event_details = []
            for event in eligible_events:
                records = semantics_by_item.get(
                    (video_id, event["event_id"], indicator_id), {}
                )
                missing_keys = [key for key in required_keys if key not in records]
                unobservable_keys = [
                    key
                    for key in required_keys
                    if key in records and not records[key]["observable"]
                ]
                if not missing_keys:
                    complete_event_ids.append(event["event_id"])
                    if not unobservable_keys:
                        fully_observable_event_ids.append(event["event_id"])
                event_details.append(
                    {
                        "event_id": event["event_id"],
                        "missing_semantic_keys": missing_keys,
                        "unobservable_semantic_keys": unobservable_keys,
                    }
                )
            covered = not required_keys or bool(complete_event_ids)
            semantic_coverage.append(
                {
                    "video_id": video_id,
                    "event_code": event_code,
                    "indicator_id": indicator_id,
                    "required_semantic_keys": list(required_keys),
                    "complete_event_ids": complete_event_ids,
                    "fully_observable_event_ids": fully_observable_event_ids,
                    "events": event_details,
                    "covered": covered,
                    "observable_for_evaluation": (
                        True if not required_keys else bool(fully_observable_event_ids)
                    ),
                }
            )
    semantic_ready = (
        bool(semantic_coverage)
        and all(item["covered"] for item in semantic_coverage)
        and pending_semantic_rows == 0
    )

    labels_by_item_type: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for record in coach_records:
        labels_by_item_type[
            (
                record["video_id"],
                record["event_id"],
                record["indicator_id"],
                record["label_type"],
            )
        ].add(record["annotator_id"])
    coach_coverage = []
    for video_id in sorted(expected_videos):
        for indicator_id in ALL_INDICATORS:
            event_code = _indicator_event_code(indicator_id)
            eligible_events = events_by_video_code[(video_id, event_code)]
            overlapping_items = []
            for event in eligible_events:
                for label_type in ("grade", "ranking"):
                    annotators = sorted(
                        labels_by_item_type[
                            (video_id, event["event_id"], indicator_id, label_type)
                        ]
                    )
                    if len(annotators) >= MIN_COACH_ANNOTATORS_PER_ITEM:
                        overlapping_items.append(
                            {
                                "event_id": event["event_id"],
                                "label_type": label_type,
                                "annotator_ids": annotators,
                                "annotator_count": len(annotators),
                            }
                        )
            coach_coverage.append(
                {
                    "video_id": video_id,
                    "event_code": event_code,
                    "indicator_id": indicator_id,
                    "required_annotator_count_per_item": MIN_COACH_ANNOTATORS_PER_ITEM,
                    "overlapping_items": overlapping_items,
                    "covered": bool(overlapping_items),
                }
            )
    agreement = compute_annotator_agreement(coach_records)
    agreement_by_indicator = {
        indicator_id: compute_annotator_agreement(
            [
                record
                for record in coach_records
                if record["indicator_id"] == indicator_id
            ]
        )
        for indicator_id in ALL_INDICATORS
    }
    indicator_agreement_ready = all(
        result["status"] == "evaluated"
        and result["annotator_count"] >= MIN_COACH_ANNOTATORS_PER_ITEM
        for result in agreement_by_indicator.values()
    )
    coach_ready = (
        bool(coach_coverage)
        and all(item["covered"] for item in coach_coverage)
        and indicator_agreement_ready
    )

    event_coverage_by_cell = {
        (item["video_id"], item["event_code"]): item
        for item in event_phase_coverage
    }
    keypoint_coverage_by_cell = {
        (item["video_id"], item["event_code"]): item
        for item in keypoint_coverage
    }
    semantic_coverage_by_cell = {
        (item["video_id"], item["indicator_id"]): item
        for item in semantic_coverage
    }
    coach_coverage_by_cell = {
        (item["video_id"], item["indicator_id"]): item
        for item in coach_coverage
    }
    review_coverage_by_video = {item["video_id"]: item for item in review_coverage}
    indicator_readiness_matrix = []
    for video_id in sorted(expected_videos):
        for indicator_id in ALL_INDICATORS:
            event_code = _indicator_event_code(indicator_id)
            event_cell = event_coverage_by_cell[(video_id, event_code)]
            keypoint_cell = keypoint_coverage_by_cell[(video_id, event_code)]
            semantic_cell = semantic_coverage_by_cell[(video_id, indicator_id)]
            coach_cell = coach_coverage_by_cell[(video_id, indicator_id)]
            required_phases = REQUIRED_PHASES_BY_INDICATOR[indicator_id]
            indicator_phase_event_ids = [
                item["event_id"]
                for item in event_cell["events"]
                if not any(
                    phase in item["missing_required_phases"]
                    for phase in required_phases
                )
            ]
            missing_reasons = []
            if not review_coverage_by_video[video_id]["covered"]:
                missing_reasons.append("insufficient_independent_full_video_reviews")
            if event_cell["accepted_event_count"] == 0:
                missing_reasons.append("manual_event_missing")
            if not indicator_phase_event_ids:
                missing_reasons.append("required_event_phase_missing")
            if not keypoint_cell["covered"]:
                missing_reasons.append("dense_keypoint_truth_missing")
            if pending_keypoint_rows > 0:
                missing_reasons.append("pending_keypoint_adjudication_rows")
            if not keypoint_count_ready:
                missing_reasons.append("keypoint_template_count_incomplete")
            if not semantic_cell["covered"]:
                missing_reasons.append("semantic_truth_completion_missing")
            if (
                SEMANTIC_REQUIREMENTS_BY_INDICATOR[indicator_id]
                and pending_semantic_rows > 0
            ):
                missing_reasons.append("pending_semantic_adjudication_rows")
            if not coach_cell["covered"]:
                missing_reasons.append("multi_coach_item_overlap_missing")
            if agreement_by_indicator[indicator_id]["status"] != "evaluated":
                missing_reasons.append("per_indicator_agreement_not_evaluable")
            indicator_readiness_matrix.append(
                {
                    "video_id": video_id,
                    "event_code": event_code,
                    "indicator_id": indicator_id,
                    "required_phases": list(required_phases),
                    "phase_complete_event_ids": indicator_phase_event_ids,
                    "full_video_review_covered": review_coverage_by_video[video_id][
                        "covered"
                    ],
                    "event_review_annotator_ids": review_coverage_by_video[video_id][
                        "annotator_ids"
                    ],
                    "keypoint_truth_covered": keypoint_cell["covered"],
                    "semantic_truth_covered": semantic_cell["covered"],
                    "semantic_truth_observable": semantic_cell[
                        "observable_for_evaluation"
                    ],
                    "coach_overlap_covered": coach_cell["covered"],
                    "coach_overlapping_items": coach_cell["overlapping_items"],
                    "missing_reasons": missing_reasons,
                    "covered": not missing_reasons,
                }
            )

    status = (
        "invalid_annotations"
        if errors
        else "ready_for_evaluation_and_calibration_review"
        if event_ready and keypoint_ready and semantic_ready and coach_ready
        else "annotation_required"
    )
    by_video_outputs = {}
    by_video_records = {}
    for video_id in sorted(expected_videos):
        video_dir = output_dir / "by-video" / video_id
        video_events = [record for record in event_records if record["video_id"] == video_id]
        video_keypoints = [
            record for record in keypoint_records if record["video_id"] == video_id
        ]
        video_semantics = [
            record for record in semantic_records if record["video_id"] == video_id
        ]
        video_labels = [record for record in coach_records if record["video_id"] == video_id]
        by_video_records[video_id] = {
            "manual_events": video_events,
            "manual_keypoints": video_keypoints,
            "manual_semantics": video_semantics,
            "coach_labels": video_labels,
        }
        by_video_outputs[video_id] = {
            "manual_events": str(video_dir / "manual-events.jsonl"),
            "manual_keypoints": str(video_dir / "manual-keypoints.jsonl"),
            "manual_semantics": str(video_dir / "manual-semantics.jsonl"),
            "coach_labels": str(video_dir / "coach-labels.jsonl"),
        }
    report = {
        "schema_version": "1.0.0",
        "pack_version": manifest["pack_version"],
        "status": status,
        "candidate_source_binding": manifest.get("candidate_source_binding"),
        "scoring_source_binding": manifest.get("scoring_source_binding"),
        "counts": {
            "manual_events": len(event_records),
            "non_adjudicated_event_rows": non_adjudicated_event_rows,
            "accepted_keypoint_frames": len(keypoint_records),
            "accepted_keypoint_joint_rows": accepted_joint_rows,
            "pending_keypoint_joint_rows": pending_keypoint_rows,
            "semantic_truth_records": len(semantic_records),
            "pending_semantic_rows": pending_semantic_rows,
            "coach_labels": len(coach_records),
        },
        "readiness": {
            "matrix_version": READINESS_MATRIX_VERSION,
            "full_video_event_truth": event_ready,
            "full_video_review_coverage": full_video_review_ready,
            "required_event_code_coverage": event_boundary_ready,
            "required_event_phase_coverage": phase_ready,
            "dense_adjudicated_keypoint_truth": keypoint_ready,
            "semantic_annotation_completion": semantic_ready,
            "multi_coach_overlap": coach_ready,
            "per_indicator_agreement_evaluable": indicator_agreement_ready,
            "reviewed_video_ids": sorted(
                video_id
                for video_id, annotators in review_annotators_by_video.items()
                if annotators
            ),
            "fully_reviewed_video_ids": sorted(
                item["video_id"] for item in review_coverage if item["covered"]
            ),
            "missing_full_video_reviews": sorted(
                item["video_id"] for item in review_coverage if not item["covered"]
            ),
            "expected_dense_keypoint_frames": expected_dense_frames,
            "expected_keypoint_joint_rows": expected_joint_rows,
            "keypoint_template_count_coverage": keypoint_count_ready,
            "review_coverage": review_coverage,
            "event_phase_coverage": event_phase_coverage,
            "keypoint_coverage": keypoint_coverage,
            "semantic_coverage": semantic_coverage,
            "coach_indicator_coverage": coach_coverage,
            "indicator_readiness_matrix": indicator_readiness_matrix,
            "missing_event_phase_cells": [
                {
                    "video_id": item["video_id"],
                    "event_code": item["event_code"],
                }
                for item in event_phase_coverage
                if not item["covered"]
            ],
            "missing_keypoint_cells": [
                {
                    "video_id": item["video_id"],
                    "event_code": item["event_code"],
                }
                for item in keypoint_coverage
                if not item["covered"]
            ],
            "missing_semantic_cells": [
                {
                    "video_id": item["video_id"],
                    "indicator_id": item["indicator_id"],
                    "required_semantic_keys": item["required_semantic_keys"],
                }
                for item in semantic_coverage
                if not item["covered"]
            ],
            "unobservable_semantic_cells": [
                {
                    "video_id": item["video_id"],
                    "indicator_id": item["indicator_id"],
                }
                for item in semantic_coverage
                if item["covered"] and not item["observable_for_evaluation"]
            ],
            "missing_coach_indicator_cells": [
                {
                    "video_id": item["video_id"],
                    "indicator_id": item["indicator_id"],
                }
                for item in coach_coverage
                if not item["covered"]
            ],
        },
        "annotator_agreement": agreement,
        "annotator_agreement_by_indicator": agreement_by_indicator,
        "errors": errors,
        "outputs": {
            "manual_events": str(output_dir / "manual-events.jsonl"),
            "manual_keypoints": str(output_dir / "manual-keypoints.jsonl"),
            "manual_semantics": str(output_dir / "manual-semantics.jsonl"),
            "coach_labels": str(output_dir / "coach-labels.jsonl"),
            "by_video": by_video_outputs,
        },
        "safety": {
            "candidate_boundaries_promoted_to_truth": False,
            "blank_labels_interpreted_as_grade_E_or_zero": False,
            "unobservable_semantics_filled_with_guessed_values": False,
            "generated_thresholds": False,
            "automatic_F3_or_F4_promotion": False,
        },
    }
    if not errors:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(output_dir / "manual-events.jsonl", event_records)
        _write_jsonl(output_dir / "manual-keypoints.jsonl", keypoint_records)
        _write_jsonl(output_dir / "manual-semantics.jsonl", semantic_records)
        _write_jsonl(output_dir / "coach-labels.jsonl", coach_records)
        for video_id, records in by_video_records.items():
            video_dir = output_dir / "by-video" / video_id
            video_dir.mkdir(parents=True, exist_ok=True)
            for name, values in records.items():
                filename = {
                    "manual_events": "manual-events.jsonl",
                    "manual_keypoints": "manual-keypoints.jsonl",
                    "manual_semantics": "manual-semantics.jsonl",
                    "coach_labels": "coach-labels.jsonl",
                }[name]
                _write_jsonl(video_dir / filename, values)
        (output_dir / "validation-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report
