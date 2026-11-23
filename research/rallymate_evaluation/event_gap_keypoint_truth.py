from __future__ import annotations

import json
import math
import os
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_evaluation.event_bounded_pose_gaps import (
    validate_event_bounded_pose_gap_report_sources,
)
from rallymate_evaluation.ground_truth import validate_keypoint_annotation
from rallymate_evaluation.small_roi_keypoint_truth import (
    ADJUDICATION_FIELDS,
    ANNOTATION_FIELDS,
    SmallRoiTruthError,
    _canonical_sha256,
    _parse_point,
    _read_csv,
    _read_jsonl,
    _source,
    _write_csv,
    _write_json,
    _write_jsonl,
    sha256_file,
)


PACK_VERSION = "event-gap-keypoint-truth-pack-v1.0.0"
EVALUATOR_VERSION = "event-gap-keypoint-error-v1.0.0"
REQUIRED_ANNOTATORS = 2


class EventGapTruthError(SmallRoiTruthError):
    pass


def _frame_index(record: dict[str, Any]) -> int:
    frame = record["frame"]
    return int(frame.get("source_frame_index", frame.get("index")))


def _valid_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    bbox = [float(item) for item in value]
    if not all(math.isfinite(item) for item in bbox) or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _annotation_bbox(
    timeline: dict[int, dict[str, Any]], processed_index: int
) -> tuple[list[float], dict[str, Any]]:
    current = timeline[processed_index]
    current_bbox = _valid_bbox(current.get("bbox_px"))
    if current_bbox is not None:
        return current_bbox, {
            "method": "current_primary_timeline_bbox",
            "source_processed_indices": [processed_index],
            "source_track_ids": [current.get("source_track_id")],
            "identity_truth_claim": False,
        }
    previous = next(
        (
            timeline[index]
            for index in range(processed_index - 1, max(-1, processed_index - 6), -1)
            if index in timeline and _valid_bbox(timeline[index].get("bbox_px")) is not None
        ),
        None,
    )
    following = next(
        (
            timeline[index]
            for index in range(processed_index + 1, processed_index + 6)
            if index in timeline and _valid_bbox(timeline[index].get("bbox_px")) is not None
        ),
        None,
    )
    if previous is None or following is None:
        raise EventGapTruthError("M75 missing frame lacks two-sided annotation crop evidence")
    span_ms = int(following["timestamp_ms"] - previous["timestamp_ms"])
    if span_ms > 160:
        raise EventGapTruthError("M75 two-sided annotation crop evidence exceeds 160 ms")
    left = _valid_bbox(previous["bbox_px"])
    right = _valid_bbox(following["bbox_px"])
    assert left is not None and right is not None
    union = [
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    ]
    return union, {
        "method": "nearest_two_sided_primary_timeline_bbox_union_for_annotation_view_only",
        "source_processed_indices": [int(previous["processed_index"]), int(following["processed_index"])],
        "source_track_ids": [previous.get("source_track_id"), following.get("source_track_id")],
        "bounding_observation_span_ms": span_ms,
        "identity_truth_claim": False,
    }


def _source_records(manifest: dict[str, Any]) -> list[dict[str, str]]:
    sources = manifest.get("sources", {})
    records = [sources.get("m74_report")]
    for item in sources.get("per_video", []):
        records.extend(
            item.get(name)
            for name in ("video", "m73_report", "frames", "primary_timeline", "events")
        )
    return [record for record in records if isinstance(record, dict)]


def validate_event_gap_truth_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != "1.0.0" or manifest.get("pack_version") != PACK_VERSION:
        raise EventGapTruthError("unsupported M75 truth pack contract")
    if manifest.get("status") != "annotation_required":
        raise EventGapTruthError("M75 pack must start annotation_required")
    scope = manifest.get("scope", {})
    if (
        scope.get("video_count") != 2
        or scope.get("frame_count") != 54
        or scope.get("joint_task_count") != 142
        or scope.get("required_independent_annotators") != REQUIRED_ANNOTATORS
        or scope.get("required_independent_reviewer") != 1
    ):
        raise EventGapTruthError("M75 truth task scope drifted")
    safety = manifest.get("safety", {})
    for name in (
        "prediction_coordinates_embedded_in_annotation_ui",
        "truth_prefilled_from_prediction",
        "model_values_used_as_truth",
        "accuracy_claim",
        "production_enabled",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if safety.get(name) is not False:
            raise EventGapTruthError(f"unsafe M75 truth manifest: {name}")
    if safety.get("independent_adjudication_required") is not True:
        raise EventGapTruthError("M75 requires independent adjudication")


def _candidate_points_from_m74(report: dict[str, Any]) -> dict[tuple[str, str, int, str], dict[str, Any]]:
    validate_event_bounded_pose_gap_report_sources(report)
    if report["counterfactual_summary"]["unique_event_joint_observation_count"] != 142:
        raise EventGapTruthError("M74 unique task count drifted")
    points: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for item in report["items"]:
        for point in item["gap_audit"]["interpolated_points"]:
            key = (
                str(item["video_id"]),
                str(item["event_id"]),
                int(point["source_frame"]),
                str(point["joint_name"]),
            )
            prediction = {
                "x_normalized": float(point["x_normalized"]),
                "y_normalized": float(point["y_normalized"]),
                "effective_confidence": float(point["effective_confidence"]),
                "left_boundary_timestamp_ms": int(point["left_boundary_timestamp_ms"]),
                "left_boundary_source_frame": int(point["left_boundary_source_frame"]),
                "left_boundary_confidence": float(point["left_boundary_confidence"]),
                "right_boundary_timestamp_ms": int(point["right_boundary_timestamp_ms"]),
                "right_boundary_source_frame": int(point["right_boundary_source_frame"]),
                "right_boundary_confidence": float(point["right_boundary_confidence"]),
                "bounding_observation_span_ms": int(point["bounding_observation_span_ms"]),
            }
            if key not in points:
                points[key] = {
                    "video_id": key[0],
                    "event_id": key[1],
                    "event_code": str(item["event_code"]),
                    "source_frame_index": key[2],
                    "joint_name": key[3],
                    "timestamp_ms": int(point["timestamp_ms"]),
                    "indicator_ids": [],
                    "original_classifications": [],
                    "prediction": prediction,
                }
            elif points[key]["prediction"] != prediction:
                raise EventGapTruthError("duplicate M74 task has inconsistent prediction")
            indicator_id = str(item["indicator_id"])
            if indicator_id not in points[key]["indicator_ids"]:
                points[key]["indicator_ids"].append(indicator_id)
            classification = str(item["original_classification"])
            if classification not in points[key]["original_classifications"]:
                points[key]["original_classifications"].append(classification)
    if len(points) != 142:
        raise EventGapTruthError("M75 task de-duplication is not exactly 142")
    return points


def _verify_m74_task_replay(
    manifest: dict[str, Any], tasks: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> None:
    report_binding = manifest["sources"]["m74_report"]
    report = json.loads(Path(report_binding["path"]).read_text(encoding="utf-8"))
    points = _candidate_points_from_m74(report)
    task_by_id = {str(row.get("task_id")): row for row in tasks}
    prediction_by_id = {str(row.get("task_id")): row for row in predictions}
    if len(task_by_id) != 142 or set(task_by_id) != set(prediction_by_id):
        raise EventGapTruthError("M75 task/prediction identifiers do not replay to M74")
    expected_task_ids: set[str] = set()
    for point in points.values():
        task_id = (
            f"m75:{point['video_id']}:{point['event_id']}:"
            f"{point['source_frame_index']}:{point['joint_name']}"
        )
        expected_task_ids.add(task_id)
        task = task_by_id.get(task_id, {})
        expected_task_fields = {
            "video_id": point["video_id"],
            "event_id": point["event_id"],
            "event_code": point["event_code"],
            "source_frame_index": point["source_frame_index"],
            "joint_name": point["joint_name"],
            "timestamp_ms": point["timestamp_ms"],
            "indicator_ids": sorted(point["indicator_ids"]),
            "original_classifications": sorted(point["original_classifications"]),
        }
        if any(task.get(name) != value for name, value in expected_task_fields.items()):
            raise EventGapTruthError(f"M75 task does not replay bound M74 evidence: {task_id}")
        prediction = prediction_by_id.get(task_id, {})
        expected_prediction_fields = {
            "schema_version": "1.0.0",
            "prediction_id": f"pred-{task_id}",
            "task_id": task_id,
            "video_id": point["video_id"],
            "event_id": point["event_id"],
            "source_frame_index": point["source_frame_index"],
            "timestamp_ms": point["timestamp_ms"],
            "joint_name": point["joint_name"],
            **point["prediction"],
            "prediction_status": "experimental_interpolation_not_truth",
        }
        if prediction != expected_prediction_fields:
            raise EventGapTruthError(
                f"M75 sealed prediction does not replay bound M74 evidence: {task_id}"
            )
    if set(task_by_id) != expected_task_ids:
        raise EventGapTruthError("M75 task set differs from bound M74 evidence")
    source_by_video = {
        str(item["video_id"]): item for item in manifest["sources"]["per_video"]
    }
    point_video_ids = {point[0] for point in points}
    expected_sources = {
        str(item["video_id"]): item
        for item in report["sources"]["per_video"]
        if str(item["video_id"]) in point_video_ids
    }
    if set(source_by_video) != point_video_ids or set(source_by_video) != set(expected_sources):
        raise EventGapTruthError("M75 per-video source scope differs from M74")
    for video_id, expected in expected_sources.items():
        actual = source_by_video[video_id]
        for name in ("m73_report", "frames", "primary_timeline", "events"):
            if actual.get(name) != expected.get(name):
                raise EventGapTruthError(f"M75 {video_id} {name} binding differs from M74")


def _verify_pack_sources(pack_dir: Path, manifest: dict[str, Any]) -> None:
    validate_event_gap_truth_manifest(manifest)
    expected_source_count = 1 + int(manifest["scope"]["video_count"]) * 5
    sources = _source_records(manifest)
    if len(sources) != expected_source_count:
        raise EventGapTruthError("M75 source bindings are incomplete")
    for source in sources:
        path = Path(str(source.get("path", ""))).resolve()
        if not path.is_file() or sha256_file(path) != str(source.get("sha256", "")).upper():
            raise EventGapTruthError(f"M75 source SHA mismatch: {path}")
    for name, source in manifest.get("artifacts", {}).items():
        path = Path(str(source.get("path", ""))).resolve()
        if not path.is_file() or sha256_file(path) != str(source.get("sha256", "")).upper():
            raise EventGapTruthError(f"M75 immutable artifact SHA mismatch: {name}")
    tasks = _read_jsonl(pack_dir / "tasks.jsonl")
    predictions = _read_jsonl(pack_dir / "sealed-interpolation-predictions.jsonl")
    if _canonical_sha256(tasks) != manifest.get("task_contract_sha256"):
        raise EventGapTruthError("M75 task contract SHA mismatch")
    if _canonical_sha256(predictions) != manifest.get("prediction_contract_sha256"):
        raise EventGapTruthError("M75 sealed prediction contract SHA mismatch")
    _verify_m74_task_replay(manifest, tasks, predictions)


def _workbench_html(bootstrap: dict[str, Any]) -> str:
    data = json.dumps(bootstrap, ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RallyMate M75 短缺口关键点盲审</title><link rel="stylesheet" href="event-gap-truth-workbench.css"></head><body>
<main><h1>M75 事件内短缺口关键点人工真值</h1><p class="warning">工作台只播放无骨架原视频，不加载密封插值坐标。两名标注者必须分别独立作答；第三名 reviewer 导入两份 CSV 后裁决。完成标注不会自动启用插值、评分等级或阈值。</p>
<section class="toolbar"><label>模式<select id="mode"><option value="annotate">独立标注</option><option value="adjudicate">独立裁决</option></select></label><label>annotator / reviewer ID<input id="identity" autocomplete="off"></label><label>任务<select id="task"></select></label><button id="previous">上一点</button><button id="next">下一点</button></section>
<section class="stage"><video id="video" controls playsinline preload="metadata"></video><canvas id="canvas" width="960" height="540"></canvas></section>
<section class="editor"><div><strong id="task-label"></strong><div id="frame-meta"></div><div id="status"></div></div><button id="mark-invisible">标记不可见</button><input id="reason" placeholder="不可见原因"><button id="clear">清除当前点</button></section>
<section><h2>导入与导出</h2><p>每位标注者单独导出 annotation CSV。裁决者导入至少两名标注者的 CSV；裁决画面只显示人工点，不显示插值预测。</p><input id="import" type="file" accept=".csv" multiple><button id="export-annotations">导出当前标注 CSV</button><button id="export-adjudications">导出裁决 CSV</button><button id="reset">清空浏览器草稿</button><p id="progress"></p></section>
<section><h2>Python 门禁</h2><pre>python scripts/compile_m75_event_gap_keypoint_truth.py --pack &lt;本目录&gt;
python scripts/evaluate_m75_event_gap_keypoint_truth.py --pack &lt;本目录&gt; --output interpolation-error.json</pre></section></main>
<script id="event-gap-truth-bootstrap" type="application/json">{data}</script><script src="event-gap-truth-workbench.js"></script></body></html>'''


def build_event_gap_truth_pack(
    *,
    m74_report_path: str | Path,
    output_dir: str | Path,
    asset_directory: str | Path,
) -> dict[str, Any]:
    report_path = Path(m74_report_path).resolve()
    final_output_dir = Path(output_dir).resolve()
    output_dir = final_output_dir.with_name(f".{final_output_dir.name}.building")
    asset_directory = Path(asset_directory).resolve()
    if final_output_dir.exists() or output_dir.exists():
        raise EventGapTruthError(
            f"output or staging directory already exists: {final_output_dir}"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    points = _candidate_points_from_m74(report)
    per_video_sources: list[dict[str, Any]] = []
    videos_for_ui: dict[str, str] = {}
    task_context: dict[tuple[str, int], dict[str, Any]] = {}
    source_by_video = {
        str(item["video_id"]): item for item in report["sources"]["per_video"]
    }
    for video_id in sorted({key[0] for key in points}):
        source = source_by_video[video_id]
        m73_path = Path(source["m73_report"]["path"]).resolve()
        m73 = json.loads(m73_path.read_text(encoding="utf-8"))
        video_path = Path(m73["sources"]["video"]["path"]).resolve()
        frames_path = Path(source["frames"]["path"]).resolve()
        timeline_path = Path(source["primary_timeline"]["path"]).resolve()
        events_path = Path(source["events"]["path"]).resolve()
        frames = {_frame_index(row): row for row in _read_jsonl(frames_path)}
        timeline = {
            int(row["processed_index"]): row for row in _read_jsonl(timeline_path)
        }
        for _, _, frame_index, _ in [key for key in points if key[0] == video_id]:
            if (video_id, frame_index) in task_context:
                continue
            frame_row = frames.get(frame_index)
            if frame_row is None:
                raise EventGapTruthError("M75 frame is absent from bound Pose rows")
            processed_index = int(frame_row["frame"]["processed_index"])
            timeline_row = timeline.get(processed_index)
            if timeline_row is None:
                raise EventGapTruthError("M75 frame is absent from primary timeline")
            bbox, bbox_provenance = _annotation_bbox(timeline, processed_index)
            width = int(frame_row["frame"]["width"])
            height = int(frame_row["frame"]["height"])
            task_context[(video_id, frame_index)] = {
                "processed_index": processed_index,
                "frame_width": width,
                "frame_height": height,
                "primary_player_id": 1,
                "bbox_px": [round(value, 6) for value in bbox],
                "bbox_width_px": round(bbox[2] - bbox[0], 6),
                "bbox_height_px": round(bbox[3] - bbox[1], 6),
                "bbox_long_side_px": round(max(bbox[2] - bbox[0], bbox[3] - bbox[1]), 6),
                "annotation_bbox_provenance": bbox_provenance,
            }
        videos_for_ui[video_id] = Path(os.path.relpath(video_path, output_dir)).as_posix()
        per_video_sources.append(
            {
                "video_id": video_id,
                "view_group": f"fixed-camera-{video_id[:8]}",
                "video": _source(video_path),
                "m73_report": _source(m73_path),
                "frames": _source(frames_path),
                "primary_timeline": _source(timeline_path),
                "events": _source(events_path),
            }
        )

    tasks: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    view_by_video = {item["video_id"]: item["view_group"] for item in per_video_sources}
    for key, point in sorted(points.items()):
        context = task_context[(point["video_id"], point["source_frame_index"])]
        task_id = (
            f"m75:{point['video_id']}:{point['event_id']}:"
            f"{point['source_frame_index']}:{point['joint_name']}"
        )
        tasks.append(
            {
                "schema_version": "1.0.0",
                "task_id": task_id,
                "video_id": point["video_id"],
                "view_group": view_by_video[point["video_id"]],
                "event_id": point["event_id"],
                "event_code": point["event_code"],
                "indicator_ids": sorted(point["indicator_ids"]),
                "original_classifications": sorted(point["original_classifications"]),
                "source_frame_index": point["source_frame_index"],
                "processed_index": context["processed_index"],
                "timestamp_ms": point["timestamp_ms"],
                "frame_width": context["frame_width"],
                "frame_height": context["frame_height"],
                "primary_player_id": context["primary_player_id"],
                "joint_name": point["joint_name"],
                "bbox_px": context["bbox_px"],
                "bbox_width_px": context["bbox_width_px"],
                "bbox_height_px": context["bbox_height_px"],
                "bbox_long_side_px": context["bbox_long_side_px"],
                "annotation_bbox_provenance": context["annotation_bbox_provenance"],
                "truth_status": "pending",
            }
        )
        predictions.append(
            {
                "schema_version": "1.0.0",
                "prediction_id": f"pred-{task_id}",
                "task_id": task_id,
                "video_id": point["video_id"],
                "event_id": point["event_id"],
                "source_frame_index": point["source_frame_index"],
                "timestamp_ms": point["timestamp_ms"],
                "joint_name": point["joint_name"],
                **point["prediction"],
                "prediction_status": "experimental_interpolation_not_truth",
            }
        )

    output_dir.mkdir(parents=True)
    _write_jsonl(output_dir / "tasks.jsonl", tasks)
    _write_jsonl(output_dir / "sealed-interpolation-predictions.jsonl", predictions)
    _write_csv(output_dir / "annotations.csv", ANNOTATION_FIELDS, [])
    _write_csv(output_dir / "adjudications.csv", ADJUDICATION_FIELDS, [])
    for name in ("event-gap-truth-workbench.js", "event-gap-truth-workbench.css"):
        shutil.copyfile(asset_directory / name, output_dir / name)
    bootstrap = {
        "schema_version": "1.0.0",
        "pack_version": PACK_VERSION,
        "videos": videos_for_ui,
        "tasks": tasks,
        "annotation_fields": list(ANNOTATION_FIELDS),
        "adjudication_fields": list(ADJUDICATION_FIELDS),
        "required_annotators": REQUIRED_ANNOTATORS,
    }
    (output_dir / "review.html").write_text(_workbench_html(bootstrap), encoding="utf-8")
    manifest = {
        "schema_version": "1.0.0",
        "pack_version": PACK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "annotation_required",
        "sources": {
            "m74_report": _source(report_path),
            "per_video": per_video_sources,
        },
        "scope": {
            "video_count": len(per_video_sources),
            "video_ids": sorted(videos_for_ui),
            "view_groups": sorted(view_by_video.values()),
            "frame_count": len({(item["video_id"], item["source_frame_index"]) for item in tasks}),
            "joint_task_count": len(tasks),
            "joint_names": sorted({item["joint_name"] for item in tasks}),
            "event_codes": sorted({item["event_code"] for item in tasks}),
            "indicator_ids": sorted({value for item in tasks for value in item["indicator_ids"]}),
            "required_independent_annotators": REQUIRED_ANNOTATORS,
            "required_independent_reviewer": 1,
            "prediction_confidence_is_not_model_confidence": True,
        },
        "workbench": {
            "entry": "review.html",
            "annotation_csv": "annotations.csv",
            "adjudication_csv": "adjudications.csv",
            "compiled_keypoints": "compiled/manual-keypoints.jsonl",
            "validation_report": "compiled/validation-report.json",
            "sealed_predictions_not_loaded_by_ui": True,
        },
        "task_contract_sha256": _canonical_sha256(tasks),
        "prediction_contract_sha256": _canonical_sha256(predictions),
        "artifacts": {
            name: {
                "path": str((final_output_dir / name).resolve()),
                "sha256": sha256_file(output_dir / name),
            }
            for name in (
                "tasks.jsonl",
                "sealed-interpolation-predictions.jsonl",
                "event-gap-truth-workbench.js",
                "event-gap-truth-workbench.css",
                "review.html",
            )
        },
        "safety": {
            "prediction_coordinates_embedded_in_annotation_ui": False,
            "truth_prefilled_from_prediction": False,
            "model_values_used_as_truth": False,
            "independent_adjudication_required": True,
            "accuracy_claim": False,
            "partial_annotations_change_runtime": False,
            "production_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    try:
        validate_event_gap_truth_manifest(manifest)
        _write_json(output_dir / "manifest.json", manifest)
        output_dir.replace(final_output_dir)
        compile_event_gap_truth_pack(final_output_dir)
    except Exception:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        if final_output_dir.exists():
            shutil.rmtree(final_output_dir)
        raise
    return manifest


def compile_event_gap_truth_pack(pack_dir: str | Path) -> dict[str, Any]:
    pack_dir = Path(pack_dir).resolve()
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    try:
        _verify_pack_sources(pack_dir, manifest)
        tasks = _read_jsonl(pack_dir / "tasks.jsonl")
        annotations_rows = _read_csv(pack_dir / "annotations.csv", ANNOTATION_FIELDS)
        adjudication_rows = _read_csv(pack_dir / "adjudications.csv", ADJUDICATION_FIELDS)
    except Exception as exc:
        tasks, annotations_rows, adjudication_rows = [], [], []
        errors.append(str(exc))
    task_by_id = {str(task["task_id"]): task for task in tasks}
    if len(task_by_id) != len(tasks):
        errors.append("tasks.jsonl contains duplicate task_id")
    annotations: dict[str, dict[str, Any]] = {}
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row_number, row in enumerate(annotations_rows, start=2):
        try:
            annotation_id = row["annotation_id"].strip()
            task_id = row["task_id"].strip()
            annotator_id = row["annotator_id"].strip()
            if not annotation_id or annotation_id in annotations:
                raise EventGapTruthError("annotation_id must be unique and non-empty")
            if task_id not in task_by_id or not annotator_id:
                raise EventGapTruthError("annotation task/annotator is invalid")
            if any(item["annotator_id"] == annotator_id for item in by_task[task_id]):
                raise EventGapTruthError("one annotator may submit once per task")
            visible, x, y, reason = _parse_point(row)
            if not row["annotated_at"].strip():
                raise EventGapTruthError("annotated_at is required")
            record = {
                "annotation_id": annotation_id,
                "task_id": task_id,
                "annotator_id": annotator_id,
                "visible": visible,
                "x_normalized": x,
                "y_normalized": y,
                "visibility_reason": reason,
                "annotated_at": row["annotated_at"].strip(),
            }
            annotations[annotation_id] = record
            by_task[task_id].append(record)
        except Exception as exc:
            errors.append(f"annotations.csv row {row_number}: {exc}")
    accepted: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(adjudication_rows, start=2):
        if row.get("status", "").strip().lower() != "accepted":
            continue
        try:
            task_id = row["task_id"].strip()
            if task_id not in task_by_id or task_id in accepted:
                raise EventGapTruthError("accepted adjudication task is invalid or duplicate")
            source_ids = sorted({value.strip() for value in row["source_annotation_ids"].split(";") if value.strip()})
            if len(source_ids) < REQUIRED_ANNOTATORS or any(value not in annotations for value in source_ids):
                raise EventGapTruthError("accepted adjudication requires two source annotations")
            sources = [annotations[value] for value in source_ids]
            if any(item["task_id"] != task_id for item in sources):
                raise EventGapTruthError("adjudication sources must match task")
            annotators = {item["annotator_id"] for item in sources}
            reviewer = row["reviewer_id"].strip()
            if len(annotators) < REQUIRED_ANNOTATORS or not reviewer or reviewer in annotators:
                raise EventGapTruthError("reviewer must be independent from two annotators")
            visible, x, y, reason = _parse_point(row)
            if not row["adjudication_id"].strip() or not row["adjudicated_at"].strip():
                raise EventGapTruthError("adjudication id/time is required")
            accepted[task_id] = {
                "task_id": task_id,
                "source_annotation_ids": source_ids,
                "source_annotator_ids": sorted(annotators),
                "reviewer_id": reviewer,
                "visible": visible,
                "x_normalized": x,
                "y_normalized": y,
                "visibility_reason": reason,
            }
        except Exception as exc:
            errors.append(f"adjudications.csv row {row_number}: {exc}")

    grouped: dict[tuple[str, int, str], dict[str, Any]] = {}
    for task_id, decision in sorted(accepted.items()):
        task = task_by_id[task_id]
        key = (str(task["video_id"]), int(task["source_frame_index"]), decision["reviewer_id"])
        record = grouped.setdefault(
            key,
            {
                "schema_version": "1.0.0",
                "video_id": task["video_id"],
                "source_frame_index": int(task["source_frame_index"]),
                "timestamp_ms": int(task["timestamp_ms"]),
                "primary_player_id": 1,
                "annotator_id": "adjudicated-independent-event-gap",
                "reviewer_id": decision["reviewer_id"],
                "adjudication_status": "accepted",
                "view_group": task["view_group"],
                "joints": {},
                "provenance": {
                    "pack_version": PACK_VERSION,
                    "task_contract_sha256": manifest.get("task_contract_sha256"),
                    "source_annotation_ids_by_joint": {},
                },
            },
        )
        joint = str(task["joint_name"])
        if joint in record["joints"]:
            errors.append(f"duplicate compiled joint {key}/{joint}")
            continue
        record["joints"][joint] = {
            "visible": decision["visible"],
            "x_normalized": decision["x_normalized"],
            "y_normalized": decision["y_normalized"],
            "visibility_reason": decision["visibility_reason"],
        }
        record["provenance"]["source_annotation_ids_by_joint"][joint] = decision["source_annotation_ids"]
    manual_records = sorted(grouped.values(), key=lambda item: (item["video_id"], item["source_frame_index"]))
    for record in manual_records:
        try:
            validate_keypoint_annotation(record)
        except Exception as exc:
            errors.append(f"compiled keypoint {record.get('video_id')}/{record.get('source_frame_index')}: {exc}")
    task_count = len(tasks)
    independent_count = sum(
        len({item["annotator_id"] for item in values}) >= REQUIRED_ANNOTATORS
        for values in by_task.values()
    )
    complete = not errors and task_count == 142 and independent_count == task_count and len(accepted) == task_count
    status = "invalid_annotations" if errors else "ready_for_interpolation_error_evaluation" if complete else "annotation_required"
    compiled_dir = pack_dir / "compiled"
    compiled_dir.mkdir(exist_ok=True)
    manual_path = compiled_dir / "manual-keypoints.jsonl"
    _write_jsonl(manual_path, manual_records)
    validation = {
        "schema_version": "1.0.0",
        "pack_version": PACK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "counts": {
            "joint_tasks": task_count,
            "raw_annotations": len(annotations),
            "tasks_with_two_independent_annotators": independent_count,
            "accepted_adjudications": len(accepted),
            "compiled_keypoint_records": len(manual_records),
            "compiled_joint_values": sum(len(item["joints"]) for item in manual_records),
        },
        "errors": errors,
        "readiness": {
            "two_independent_annotators_per_task": independent_count == task_count and task_count > 0,
            "independent_adjudication_per_task": len(accepted) == task_count and task_count > 0,
            "full_task_coverage": complete,
            "production_interpolation_change_allowed": False,
        },
        "sources": {
            "manifest": _source(pack_dir / "manifest.json"),
            "tasks": _source(pack_dir / "tasks.jsonl"),
            "sealed_predictions": _source(pack_dir / "sealed-interpolation-predictions.jsonl"),
            "annotations": _source(pack_dir / "annotations.csv"),
            "adjudications": _source(pack_dir / "adjudications.csv"),
        },
        "artifacts": {"manual_keypoints": _source(manual_path)},
        "safety": {
            "ground_truth_complete": complete,
            "prediction_values_used_as_truth": False,
            "partial_annotations_change_runtime": False,
            "production_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    _write_json(compiled_dir / "validation-report.json", validation)
    return validation


def _verify_compilation(pack_dir: Path, validation: dict[str, Any]) -> None:
    paths = {
        "manifest": pack_dir / "manifest.json",
        "tasks": pack_dir / "tasks.jsonl",
        "sealed_predictions": pack_dir / "sealed-interpolation-predictions.jsonl",
        "annotations": pack_dir / "annotations.csv",
        "adjudications": pack_dir / "adjudications.csv",
    }
    for name, path in paths.items():
        source = validation.get("sources", {}).get(name)
        if (
            not isinstance(source, dict)
            or Path(str(source.get("path", ""))).resolve() != path.resolve()
            or not path.is_file()
            or sha256_file(path) != str(source.get("sha256", "")).upper()
        ):
            raise EventGapTruthError(f"compiled M75 lineage mismatch: {name}")
    manual = pack_dir / "compiled" / "manual-keypoints.jsonl"
    bound = validation.get("artifacts", {}).get("manual_keypoints", {})
    if (
        Path(str(bound.get("path", ""))).resolve() != manual.resolve()
        or not manual.is_file()
        or sha256_file(manual) != str(bound.get("sha256", "")).upper()
    ):
        raise EventGapTruthError("compiled M75 manual truth lineage mismatch")


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    visible = [row for row in rows if row["truth_visible"]]
    localized = [row for row in visible if row["prediction_available"]]
    result: dict[str, Any] = {
        "task_count": len(rows),
        "visible_truth_count": len(visible),
        "invisible_truth_count": len(rows) - len(visible),
        "predicted_available_count": sum(row["prediction_available"] for row in rows),
        "prediction_available_on_invisible_truth_count": sum(
            row["prediction_available"] and not row["truth_visible"] for row in rows
        ),
        "valid_rate": round(len(localized) / len(visible), 8) if visible else None,
        "mae_euclidean_px": None,
        "p95_euclidean_px": None,
        "mae_euclidean_bbox_long_side": None,
        "p95_euclidean_bbox_long_side": None,
        "bias_x_px": None,
        "bias_y_px": None,
        "bias_x_normalized": None,
        "bias_y_normalized": None,
    }
    if localized:
        errors_px = np.asarray([row["error_px"] for row in localized], dtype=np.float64)
        errors_body = np.asarray([row["error_bbox_long_side"] for row in localized], dtype=np.float64)
        result.update(
            {
                "mae_euclidean_px": round(float(np.mean(errors_px)), 8),
                "p95_euclidean_px": round(float(np.quantile(errors_px, 0.95)), 8),
                "mae_euclidean_bbox_long_side": round(float(np.mean(errors_body)), 8),
                "p95_euclidean_bbox_long_side": round(float(np.quantile(errors_body, 0.95)), 8),
                "bias_x_px": round(float(np.mean([row["dx_px"] for row in localized])), 8),
                "bias_y_px": round(float(np.mean([row["dy_px"] for row in localized])), 8),
                "bias_x_normalized": round(float(np.mean([row["dx_normalized"] for row in localized])), 8),
                "bias_y_normalized": round(float(np.mean([row["dy_normalized"] for row in localized])), 8),
            }
        )
    return result


def evaluate_event_gap_keypoints(pack_dir: str | Path) -> dict[str, Any]:
    pack_dir = Path(pack_dir).resolve()
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    _verify_pack_sources(pack_dir, manifest)
    validation_path = pack_dir / "compiled" / "validation-report.json"
    if not validation_path.is_file():
        raise EventGapTruthError("compile M75 truth pack before evaluation")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    _verify_compilation(pack_dir, validation)
    if validation.get("errors"):
        raise EventGapTruthError("M75 truth pack contains invalid annotations")
    ready = validation.get("status") == "ready_for_interpolation_error_evaluation"
    tasks = {row["task_id"]: row for row in _read_jsonl(pack_dir / "tasks.jsonl")}
    predictions = {
        row["task_id"]: row
        for row in _read_jsonl(pack_dir / "sealed-interpolation-predictions.jsonl")
    }
    if set(tasks) != set(predictions) or len(tasks) != 142:
        raise EventGapTruthError("M75 sealed prediction set differs from task set")
    truth: dict[tuple[str, int, str], dict[str, Any]] = {}
    for record in _read_jsonl(pack_dir / "compiled" / "manual-keypoints.jsonl"):
        validate_keypoint_annotation(record)
        for joint, value in record["joints"].items():
            key = (str(record["video_id"]), int(record["source_frame_index"]), str(joint))
            if key in truth:
                raise EventGapTruthError("duplicate compiled M75 truth")
            truth[key] = value
    details: list[dict[str, Any]] = []
    if ready:
        for task_id, task in sorted(tasks.items()):
            key = (str(task["video_id"]), int(task["source_frame_index"]), str(task["joint_name"]))
            if key not in truth:
                raise EventGapTruthError("ready M75 pack misses task truth")
            value = truth[key]
            prediction = predictions[task_id]
            visible = value["visible"] is True
            available = all(
                isinstance(prediction.get(name), (int, float))
                and math.isfinite(float(prediction[name]))
                for name in ("x_normalized", "y_normalized")
            )
            dx_norm = dy_norm = dx_px = dy_px = error_px = error_bbox = None
            if visible and available:
                dx_norm = float(prediction["x_normalized"]) - float(value["x_normalized"])
                dy_norm = float(prediction["y_normalized"]) - float(value["y_normalized"])
                dx_px = dx_norm * int(task["frame_width"])
                dy_px = dy_norm * int(task["frame_height"])
                error_px = math.hypot(dx_px, dy_px)
                error_bbox = error_px / float(task["bbox_long_side_px"])
            details.append(
                {
                    "task_id": task_id,
                    "video_id": task["video_id"],
                    "view_group": task["view_group"],
                    "event_id": task["event_id"],
                    "event_code": task["event_code"],
                    "indicator_ids": task["indicator_ids"],
                    "source_frame_index": task["source_frame_index"],
                    "timestamp_ms": task["timestamp_ms"],
                    "joint_name": task["joint_name"],
                    "truth_visible": visible,
                    "prediction_available": available,
                    "effective_confidence": prediction["effective_confidence"],
                    "bounding_observation_span_ms": prediction["bounding_observation_span_ms"],
                    "dx_normalized": round(dx_norm, 8) if dx_norm is not None else None,
                    "dy_normalized": round(dy_norm, 8) if dy_norm is not None else None,
                    "dx_px": round(dx_px, 8) if dx_px is not None else None,
                    "dy_px": round(dy_px, 8) if dy_px is not None else None,
                    "error_px": round(error_px, 8) if error_px is not None else None,
                    "error_bbox_long_side": round(error_bbox, 8) if error_bbox is not None else None,
                }
            )
    metrics = _aggregate(details) if ready else None
    per_joint = (
        {name: _aggregate([row for row in details if row["joint_name"] == name]) for name in sorted({row["joint_name"] for row in details})}
        if ready
        else None
    )
    per_view = (
        {name: _aggregate([row for row in details if row["view_group"] == name]) for name in sorted({row["view_group"] for row in details})}
        if ready
        else None
    )
    per_event_code = (
        {name: _aggregate([row for row in details if row["event_code"] == name]) for name in sorted({row["event_code"] for row in details})}
        if ready
        else None
    )
    return {
        "schema_version": "1.0.0",
        "evaluator_version": EVALUATOR_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "evaluated_external_acceptance_protocol_required" if ready else "annotation_required",
        "pack": {
            "pack_version": PACK_VERSION,
            "manifest_sha256": sha256_file(pack_dir / "manifest.json"),
            "validation_report_sha256": sha256_file(validation_path),
            "task_contract_sha256": manifest["task_contract_sha256"],
            "prediction_contract_sha256": manifest["prediction_contract_sha256"],
        },
        "counts": {
            "required_joint_tasks": 142,
            "accepted_joint_truth": len(details),
            "visible_joint_truth": sum(row["truth_visible"] for row in details),
            "invisible_joint_truth": sum(not row["truth_visible"] for row in details),
        },
        "metrics": metrics,
        "per_joint": per_joint,
        "per_view_group": per_view,
        "per_event_code": per_event_code,
        "details": details,
        "feature_error_followup": {
            "status": "manual_keypoint_truth_required" if not ready else "manual_pose_replay_required",
            "automatic_feature_gate_change_allowed": False,
        },
        "acceptance": {
            "status": "external_preregistered_protocol_required",
            "thresholds": None,
            "production_interpolation_allowed": False,
        },
        "safety": {
            "ground_truth_provided": bool(details),
            "full_ground_truth_coverage": ready,
            "metrics_are_interpolation_localization_error": ready,
            "prediction_values_used_as_truth": False,
            "acceptance_threshold_generated": False,
            "production_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
