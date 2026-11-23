from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from rallymate_evaluation.ground_truth import validate_keypoint_annotation


PACK_VERSION = "small-roi-keypoint-truth-pack-v1.0.0"
EVALUATOR_VERSION = "small-roi-keypoint-error-v1.0.0"
KEYPOINT_CONFIDENCE_MIN = 0.25
REQUIRED_ANNOTATORS = 2
JOINTS = (
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
ANNOTATION_FIELDS = (
    "annotation_id",
    "task_id",
    "annotator_id",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "annotated_at",
)
ADJUDICATION_FIELDS = (
    "adjudication_id",
    "task_id",
    "source_annotation_ids",
    "reviewer_id",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "adjudicated_at",
    "status",
)


class SmallRoiTruthError(ValueError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SmallRoiTruthError(f"invalid JSONL line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise SmallRoiTruthError(f"JSONL line {line_number} must be an object")
            rows.append(value)
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_csv(path: Path, fields: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(fields):
            raise SmallRoiTruthError(
                f"{path.name} headers must exactly match {list(fields)}"
            )
        return [{key: value or "" for key, value in row.items()} for row in reader]


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def _frame_index(record: dict[str, Any]) -> int:
    frame = record["frame"]
    return int(frame.get("source_frame_index", frame.get("index")))


def _pose_for_track(record: dict[str, Any], track_id: Any) -> dict[str, Any] | None:
    return next(
        (
            pose
            for pose in record.get("poses", [])
            if pose.get("person_track_id") == track_id
        ),
        None,
    )


def _detection_for_track(record: dict[str, Any], track_id: Any) -> dict[str, Any] | None:
    return next(
        (
            detection
            for detection in record.get("detections", [])
            if detection.get("class_name") == "player"
            and detection.get("track_id") == track_id
        ),
        None,
    )


def _valid_keypoint(point: dict[str, Any]) -> bool:
    return (
        point.get("in_frame") is not False
        and isinstance(point.get("confidence"), (int, float))
        and float(point["confidence"]) >= KEYPOINT_CONFIDENCE_MIN
        and all(
            isinstance(point.get(name), (int, float))
            and math.isfinite(float(point[name]))
            for name in ("x_px", "y_px")
        )
    )


def _quantile_edges(values: list[float]) -> list[float]:
    if not values:
        raise SmallRoiTruthError("cannot stratify an empty frame set")
    return [round(float(np.quantile(values, q)), 6) for q in (0.25, 0.5, 0.75)]


def _stratum(value: float, edges: list[float]) -> str:
    if value <= edges[0]:
        return "Q1_smallest"
    if value <= edges[1]:
        return "Q2"
    if value <= edges[2]:
        return "Q3"
    return "Q4_largest"


def validate_pack_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != "1.0.0":
        raise SmallRoiTruthError("unsupported small ROI truth pack schema_version")
    if manifest.get("pack_version") != PACK_VERSION:
        raise SmallRoiTruthError("unsupported small ROI truth pack version")
    if manifest.get("status") != "annotation_required":
        raise SmallRoiTruthError("generated pack must start as annotation_required")
    scope = manifest.get("scope", {})
    if tuple(scope.get("joint_names", [])) != JOINTS:
        raise SmallRoiTruthError("truth pack joint contract does not match scoring joints")
    if int(scope.get("required_independent_annotators", -1)) != REQUIRED_ANNOTATORS:
        raise SmallRoiTruthError("truth pack must require two independent annotators")
    if int(scope.get("frame_count", -1)) <= 0:
        raise SmallRoiTruthError("truth pack must contain frames")
    if int(scope.get("joint_task_count", -1)) != int(scope["frame_count"]) * len(JOINTS):
        raise SmallRoiTruthError("truth pack task count is inconsistent")
    safety = manifest.get("safety", {})
    required_false = (
        "model_keypoints_embedded_in_annotation_ui",
        "truth_prefilled_from_model",
        "accuracy_claim",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    )
    if any(safety.get(field) is not False for field in required_false):
        raise SmallRoiTruthError("truth pack contains an unsafe claim")
    if safety.get("independent_adjudication_required") is not True:
        raise SmallRoiTruthError("truth pack must require independent adjudication")


def build_small_roi_truth_pack(
    *,
    experiment_report_path: str | Path,
    experimental_frames_path: str | Path,
    primary_timeline_path: str | Path,
    video_path: str | Path,
    gap_audit_path: str | Path,
    comparison_video_path: str | Path,
    output_dir: str | Path,
    asset_directory: str | Path,
) -> dict[str, Any]:
    experiment_report_path = Path(experiment_report_path).resolve()
    experimental_frames_path = Path(experimental_frames_path).resolve()
    primary_timeline_path = Path(primary_timeline_path).resolve()
    video_path = Path(video_path).resolve()
    gap_audit_path = Path(gap_audit_path).resolve()
    comparison_video_path = Path(comparison_video_path).resolve()
    output_dir = Path(output_dir).resolve()
    asset_directory = Path(asset_directory).resolve()
    if output_dir.exists():
        raise SmallRoiTruthError(f"output directory already exists: {output_dir}")
    for path in (
        experiment_report_path,
        experimental_frames_path,
        primary_timeline_path,
        video_path,
        gap_audit_path,
        comparison_video_path,
    ):
        if not path.is_file():
            raise SmallRoiTruthError(f"required source does not exist: {path}")
    report = json.loads(experiment_report_path.read_text(encoding="utf-8"))
    safety = report.get("safety", {})
    if safety.get("production_enabled") is not False or safety.get("accuracy_claim") is not False:
        raise SmallRoiTruthError("source experiment is not a safe non-production report")
    if report.get("artifacts", {}).get("experimental_frames", {}).get("sha256", "").upper() != sha256_file(
        experimental_frames_path
    ):
        raise SmallRoiTruthError("experimental frames hash does not match experiment report")
    recovered_indices = {
        int(value) for value in report.get("inference", {}).get("recovered_processed_indices", [])
    }
    if not recovered_indices:
        raise SmallRoiTruthError("experiment has no recovered frame indices")
    frame_rows = _read_jsonl(experimental_frames_path)
    timeline_rows = {
        int(row["source_frame_index"]): row for row in _read_jsonl(primary_timeline_path)
    }
    gap = json.loads(gap_audit_path.read_text(encoding="utf-8"))
    gap_events = gap.get("event_failures", [])
    frames: list[dict[str, Any]] = []
    for record in frame_rows:
        frame = record["frame"]
        processed_index = int(frame["processed_index"])
        if processed_index not in recovered_indices:
            continue
        source_frame_index = _frame_index(record)
        timeline = timeline_rows.get(source_frame_index)
        if timeline is None:
            raise SmallRoiTruthError(f"timeline misses recovered frame {source_frame_index}")
        track_id = timeline.get("source_track_id")
        detection = _detection_for_track(record, track_id)
        pose = _pose_for_track(record, track_id)
        if detection is None or pose is None:
            raise SmallRoiTruthError(f"recovered frame lacks detection or pose: {source_frame_index}")
        bbox = [float(value) for value in detection["bbox_px"]]
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        points = {str(point["name"]): point for point in pose.get("keypoints", [])}
        valid_scoring = sum(
            1 for joint in JOINTS if joint in points and _valid_keypoint(points[joint])
        )
        valid_native = sum(1 for point in pose.get("keypoints", []) if _valid_keypoint(point))
        timestamp_ms = int(frame["timestamp_ms"])
        affected = sorted(
            str(event["event_id"])
            for event in gap_events
            if int(event["start_ms"]) <= timestamp_ms <= int(event["end_ms"])
        )
        frames.append(
            {
                "source_frame_index": source_frame_index,
                "processed_index": processed_index,
                "timestamp_ms": timestamp_ms,
                "width": int(frame["width"]),
                "height": int(frame["height"]),
                "person_track_id": track_id,
                "bbox_px": [round(value, 6) for value in bbox],
                "bbox_width_px": round(width, 6),
                "bbox_height_px": round(height, 6),
                "bbox_long_side_px": round(max(width, height), 6),
                "experimental_valid_scoring_joint_count": valid_scoring,
                "experimental_valid_native_keypoint_count": valid_native,
                "affected_event_ids": affected,
            }
        )
    frames.sort(key=lambda item: item["source_frame_index"])
    if {item["processed_index"] for item in frames} != recovered_indices:
        missing = sorted(recovered_indices - {item["processed_index"] for item in frames})
        raise SmallRoiTruthError(f"not all recovered frames were materialized: {missing[:10]}")
    edges = _quantile_edges([float(item["bbox_long_side_px"]) for item in frames])
    for item in frames:
        item["bbox_size_stratum"] = _stratum(float(item["bbox_long_side_px"]), edges)
    video_id = video_path.stem
    tasks = [
        {
            "schema_version": "1.0.0",
            "task_id": f"{video_id}:{frame['source_frame_index']}:{joint}",
            "video_id": video_id,
            "source_frame_index": frame["source_frame_index"],
            "processed_index": frame["processed_index"],
            "timestamp_ms": frame["timestamp_ms"],
            "frame_width": frame["width"],
            "frame_height": frame["height"],
            "person_track_id": frame["person_track_id"],
            "joint_name": joint,
            "bbox_px": frame["bbox_px"],
            "bbox_long_side_px": frame["bbox_long_side_px"],
            "bbox_size_stratum": frame["bbox_size_stratum"],
            "affected_event_ids": frame["affected_event_ids"],
            "truth_status": "pending",
        }
        for frame in frames
        for joint in JOINTS
    ]
    workbench_source = Path(
        os.path.relpath(video_path, output_dir)
    ).as_posix()
    comparison_source = Path(
        os.path.relpath(comparison_video_path, output_dir)
    ).as_posix()
    manifest = {
        "schema_version": "1.0.0",
        "pack_version": PACK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "annotation_required",
        "video_id": video_id,
        "sources": {
            "video": _source(video_path),
            "experiment_report": _source(experiment_report_path),
            "experimental_frames": _source(experimental_frames_path),
            "primary_timeline": _source(primary_timeline_path),
            "gap_audit": _source(gap_audit_path),
            "comparison_video": _source(comparison_video_path),
        },
        "scope": {
            "frame_selection": "all_recovered_frames_from_small_roi_experiment",
            "frame_count": len(frames),
            "joint_names": list(JOINTS),
            "joint_task_count": len(tasks),
            "required_independent_annotators": REQUIRED_ANNOTATORS,
            "required_independent_reviewer": 1,
            "keypoint_confidence_min_for_model_evaluation": KEYPOINT_CONFIDENCE_MIN,
            "bbox_size_stratification": {
                "method": "empirical_quartiles_descriptive_only",
                "long_side_edges_px": edges,
                "not_an_acceptance_threshold": True,
            },
        },
        "frames": frames,
        "workbench": {
            "source_video": workbench_source,
            "comparison_video": comparison_source,
            "annotation_csv": "annotations.csv",
            "adjudication_csv": "adjudications.csv",
            "compiled_keypoints": "compiled/manual-keypoints.jsonl",
            "validation_report": "compiled/validation-report.json",
        },
        "safety": {
            "model_keypoints_embedded_in_annotation_ui": False,
            "truth_prefilled_from_model": False,
            "independent_adjudication_required": True,
            "accuracy_claim": False,
            "production_enabled": False,
            "automatic_profile_fallback_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
            "partial_annotations_change_runtime": False,
        },
    }
    validate_pack_manifest(manifest)
    output_dir.mkdir(parents=True)
    _write_jsonl(output_dir / "tasks.jsonl", tasks)
    _write_csv(output_dir / "annotations.csv", ANNOTATION_FIELDS, [])
    _write_csv(output_dir / "adjudications.csv", ADJUDICATION_FIELDS, [])
    for name in ("small-roi-truth-workbench.js", "small-roi-truth-workbench.css"):
        shutil.copyfile(asset_directory / name, output_dir / name)
    bootstrap = {
        "pack_version": PACK_VERSION,
        "video_id": video_id,
        "video_source": workbench_source,
        "comparison_video_source": comparison_source,
        "frames": frames,
        "joint_names": list(JOINTS),
        "tasks": tasks,
        "annotation_fields": list(ANNOTATION_FIELDS),
        "adjudication_fields": list(ADJUDICATION_FIELDS),
        "required_annotators": REQUIRED_ANNOTATORS,
    }
    html = _workbench_html(bootstrap)
    (output_dir / "review.html").write_text(html, encoding="utf-8")
    manifest["artifacts"] = {
        name: _source(output_dir / name)
        for name in (
            "tasks.jsonl",
            "small-roi-truth-workbench.js",
            "small-roi-truth-workbench.css",
            "review.html",
        )
    }
    manifest["task_contract_sha256"] = _canonical_sha256(tasks)
    _write_json(output_dir / "manifest.json", manifest)
    compile_small_roi_truth_pack(output_dir)
    return manifest


def _workbench_html(bootstrap: dict[str, Any]) -> str:
    data = json.dumps(bootstrap, ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RallyMate 小 ROI 关键点盲审</title><link rel="stylesheet" href="small-roi-truth-workbench.css"></head><body>
<main><h1>小 ROI 关键点人工真值盲审</h1><p class="warning">这里只播放无模型骨架的原视频。模型坐标没有预填；至少两名独立标注者分别导出 CSV，再由未参与标注的 reviewer 导入两份 CSV 并逐点裁决。完成不会自动切换 8px 或生成 A～E。</p>
<section class="toolbar"><label>模式<select id="mode"><option value="annotate">独立标注</option><option value="adjudicate">独立裁决</option></select></label><label>annotator / reviewer ID<input id="identity" autocomplete="off"></label><label>帧<select id="frame"></select></label><label>关节<select id="joint"></select></label><button id="previous">上一点</button><button id="next">下一点</button></section>
<section class="stage"><video id="video" controls playsinline preload="metadata"></video><canvas id="canvas"></canvas></section>
<section class="editor"><div><strong id="task-label"></strong><div id="frame-meta"></div><div id="status"></div></div><button id="mark-invisible">标记不可见</button><input id="reason" placeholder="不可见原因"><button id="clear">清除当前点</button></section>
<section><h2>导入与导出</h2><p>独立标注模式：每位标注者单独完成并导出，不要互看。裁决模式：选择至少两份独立 annotation CSV，画面只显示人工点，不显示模型点。</p><input id="import" type="file" accept=".csv" multiple><button id="export-annotations">导出当前标注 CSV</button><button id="export-adjudications">导出裁决 CSV</button><button id="reset">清空浏览器草稿</button><p id="progress"></p><p><a href="{bootstrap['comparison_video_source']}" target="_blank">另开模型可观测性 A/B（只用于完成裁决后复核，不是真值）</a></p></section>
<section><h2>Python 门禁</h2><pre>python scripts/compile_small_roi_keypoint_truth_pack.py --pack &lt;本目录&gt;
python scripts/evaluate_small_roi_keypoint_truth.py --pack &lt;本目录&gt; --output error-report.json</pre></section></main>
<script id="small-roi-truth-bootstrap" type="application/json">{data}</script><script src="small-roi-truth-workbench.js"></script></body></html>'''


def _parse_bool(value: str, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise SmallRoiTruthError(f"{field} must be true or false")
    return normalized == "true"


def _parse_point(row: dict[str, str]) -> tuple[bool, float | None, float | None, str | None]:
    visible = _parse_bool(row["visible"], "visible")
    reason = row.get("visibility_reason", "").strip() or None
    if visible:
        try:
            x = float(row["x_normalized"])
            y = float(row["y_normalized"])
        except ValueError as exc:
            raise SmallRoiTruthError("visible point requires numeric coordinates") from exc
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in (x, y)):
            raise SmallRoiTruthError("visible coordinates must be finite normalized values")
        return True, x, y, reason
    if row.get("x_normalized", "").strip() or row.get("y_normalized", "").strip():
        raise SmallRoiTruthError("invisible point must keep coordinates empty")
    if not reason:
        raise SmallRoiTruthError("invisible point requires visibility_reason")
    return False, None, None, reason


def _verify_pack_sources(pack_dir: Path, manifest: dict[str, Any]) -> None:
    validate_pack_manifest(manifest)
    for name, source in manifest.get("sources", {}).items():
        path = Path(source["path"])
        if not path.is_file() or sha256_file(path) != str(source["sha256"]).upper():
            raise SmallRoiTruthError(f"source hash mismatch: {name}")
    for name, source in manifest.get("artifacts", {}).items():
        path = Path(source["path"])
        if not path.is_file() or sha256_file(path) != str(source["sha256"]).upper():
            raise SmallRoiTruthError(f"pack artifact hash mismatch: {name}")
    tasks = _read_jsonl(pack_dir / "tasks.jsonl")
    if _canonical_sha256(tasks) != manifest.get("task_contract_sha256"):
        raise SmallRoiTruthError("task contract hash mismatch")


def _verify_compilation_lineage(pack_dir: Path, validation: dict[str, Any]) -> None:
    expected_sources = {
        "manifest": pack_dir / "manifest.json",
        "tasks": pack_dir / "tasks.jsonl",
        "annotations": pack_dir / "annotations.csv",
        "adjudications": pack_dir / "adjudications.csv",
    }
    source_records = validation.get("sources", {})
    for name, expected_path in expected_sources.items():
        source = source_records.get(name)
        if not isinstance(source, dict):
            raise SmallRoiTruthError(f"compiled validation misses source binding: {name}")
        bound_path = Path(str(source.get("path", ""))).resolve()
        if bound_path != expected_path.resolve():
            raise SmallRoiTruthError(f"compiled source path mismatch: {name}")
        if not expected_path.is_file() or sha256_file(expected_path) != str(
            source.get("sha256", "")
        ).upper():
            raise SmallRoiTruthError(f"compiled source hash mismatch: {name}")
    manual_path = pack_dir / "compiled" / "manual-keypoints.jsonl"
    manual_source = validation.get("artifacts", {}).get("manual_keypoints")
    if not isinstance(manual_source, dict):
        raise SmallRoiTruthError("compiled validation misses manual keypoint binding")
    if Path(str(manual_source.get("path", ""))).resolve() != manual_path.resolve():
        raise SmallRoiTruthError("compiled manual keypoint path mismatch")
    if not manual_path.is_file() or sha256_file(manual_path) != str(
        manual_source.get("sha256", "")
    ).upper():
        raise SmallRoiTruthError("compiled manual keypoint hash mismatch")


def compile_small_roi_truth_pack(pack_dir: str | Path) -> dict[str, Any]:
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
    annotations_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row_number, row in enumerate(annotations_rows, start=2):
        try:
            annotation_id = row["annotation_id"].strip()
            task_id = row["task_id"].strip()
            annotator_id = row["annotator_id"].strip()
            if not annotation_id or annotation_id in annotations:
                raise SmallRoiTruthError("annotation_id must be unique and non-empty")
            if task_id not in task_by_id:
                raise SmallRoiTruthError("annotation task_id is not in the immutable task set")
            if not annotator_id:
                raise SmallRoiTruthError("annotator_id is required")
            visible, x, y, reason = _parse_point(row)
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
            if not record["annotated_at"]:
                raise SmallRoiTruthError("annotated_at is required")
            if any(item["annotator_id"] == annotator_id for item in annotations_by_task[task_id]):
                raise SmallRoiTruthError("one annotator may submit only one opinion per task")
            annotations[annotation_id] = record
            annotations_by_task[task_id].append(record)
        except Exception as exc:
            errors.append(f"annotations.csv row {row_number}: {exc}")
    adjudications: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(adjudication_rows, start=2):
        if row.get("status", "").strip().lower() != "accepted":
            continue
        try:
            task_id = row["task_id"].strip()
            if task_id not in task_by_id:
                raise SmallRoiTruthError("adjudication task_id is not in the immutable task set")
            if task_id in adjudications:
                raise SmallRoiTruthError("only one accepted adjudication is allowed per task")
            source_ids = sorted(
                {value.strip() for value in row["source_annotation_ids"].split(";") if value.strip()}
            )
            if len(source_ids) < REQUIRED_ANNOTATORS or any(value not in annotations for value in source_ids):
                raise SmallRoiTruthError("accepted adjudication requires at least two valid source annotations")
            sources = [annotations[value] for value in source_ids]
            if any(item["task_id"] != task_id for item in sources):
                raise SmallRoiTruthError("adjudication sources must belong to the same task")
            annotators = {item["annotator_id"] for item in sources}
            if len(annotators) < REQUIRED_ANNOTATORS:
                raise SmallRoiTruthError("adjudication sources must come from independent annotators")
            reviewer_id = row["reviewer_id"].strip()
            if not reviewer_id or reviewer_id in annotators:
                raise SmallRoiTruthError("reviewer must be independent from source annotators")
            visible, x, y, reason = _parse_point(row)
            if not row["adjudication_id"].strip() or not row["adjudicated_at"].strip():
                raise SmallRoiTruthError("adjudication_id and adjudicated_at are required")
            adjudications[task_id] = {
                "adjudication_id": row["adjudication_id"].strip(),
                "task_id": task_id,
                "source_annotation_ids": source_ids,
                "source_annotator_ids": sorted(annotators),
                "reviewer_id": reviewer_id,
                "visible": visible,
                "x_normalized": x,
                "y_normalized": y,
                "visibility_reason": reason,
                "adjudicated_at": row["adjudicated_at"].strip(),
            }
        except Exception as exc:
            errors.append(f"adjudications.csv row {row_number}: {exc}")
    frame_groups: dict[tuple[int, str], dict[str, Any]] = {}
    for task_id, adjudication in sorted(adjudications.items()):
        task = task_by_id[task_id]
        key = (int(task["source_frame_index"]), adjudication["reviewer_id"])
        record = frame_groups.setdefault(
            key,
            {
                "schema_version": "1.0.0",
                "video_id": task["video_id"],
                "source_frame_index": int(task["source_frame_index"]),
                "timestamp_ms": int(task["timestamp_ms"]),
                "primary_player_id": 1,
                "annotator_id": "adjudicated-independent-small-roi",
                "reviewer_id": adjudication["reviewer_id"],
                "adjudication_status": "accepted",
                "view_group": "fixed-camera-small-roi",
                "joints": {},
                "provenance": {
                    "pack_version": PACK_VERSION,
                    "task_contract_sha256": manifest.get("task_contract_sha256"),
                    "source_annotation_ids_by_joint": {},
                },
            },
        )
        joint = str(task["joint_name"])
        record["joints"][joint] = {
            "visible": adjudication["visible"],
            "x_normalized": adjudication["x_normalized"],
            "y_normalized": adjudication["y_normalized"],
            "visibility_reason": adjudication["visibility_reason"],
        }
        record["provenance"]["source_annotation_ids_by_joint"][joint] = adjudication[
            "source_annotation_ids"
        ]
    manual_records = sorted(
        frame_groups.values(), key=lambda item: (item["source_frame_index"], item["reviewer_id"])
    )
    for record in manual_records:
        try:
            validate_keypoint_annotation(record)
        except Exception as exc:
            errors.append(f"compiled keypoint frame {record.get('source_frame_index')}: {exc}")
    task_count = len(tasks)
    independent_task_count = sum(
        len({item["annotator_id"] for item in items}) >= REQUIRED_ANNOTATORS
        for items in annotations_by_task.values()
    )
    accepted_count = len(adjudications)
    complete = (
        not errors
        and task_count > 0
        and independent_task_count == task_count
        and accepted_count == task_count
    )
    status = "invalid_annotations" if errors else "ready_for_keypoint_error_evaluation" if complete else "annotation_required"
    output_dir = pack_dir / "compiled"
    output_dir.mkdir(exist_ok=True)
    manual_path = output_dir / "manual-keypoints.jsonl"
    _write_jsonl(manual_path, manual_records)
    report = {
        "schema_version": "1.0.0",
        "pack_version": PACK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "counts": {
            "frames": int(manifest.get("scope", {}).get("frame_count", 0)),
            "joint_tasks": task_count,
            "raw_annotations": len(annotations),
            "tasks_with_two_independent_annotators": independent_task_count,
            "accepted_adjudications": accepted_count,
            "compiled_keypoint_records": len(manual_records),
            "compiled_joint_values": sum(len(item["joints"]) for item in manual_records),
        },
        "errors": errors,
        "readiness": {
            "two_independent_annotators_per_task": independent_task_count == task_count and task_count > 0,
            "independent_adjudication_per_task": accepted_count == task_count and task_count > 0,
            "full_task_coverage": complete,
            "production_profile_change_allowed": False,
        },
        "sources": {
            "manifest": _source(pack_dir / "manifest.json"),
            "tasks": _source(pack_dir / "tasks.jsonl"),
            "annotations": _source(pack_dir / "annotations.csv"),
            "adjudications": _source(pack_dir / "adjudications.csv"),
        },
        "artifacts": {"manual_keypoints": _source(manual_path)},
        "safety": {
            "ground_truth_complete": complete,
            "model_values_used_as_truth": False,
            "partial_annotations_change_runtime": False,
            "production_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    _write_json(output_dir / "validation-report.json", report)
    return report


def _aggregate_errors(rows: list[dict[str, Any]]) -> dict[str, Any]:
    visible = [row for row in rows if row["truth_visible"]]
    localized = [row for row in visible if row["predicted_available"]]
    if not visible:
        return {
            "visible_truth_count": 0,
            "predicted_available_count": 0,
            "valid_rate": None,
            "mean_euclidean_error_px": None,
            "p95_euclidean_error_px": None,
            "mean_euclidean_error_bbox_long_side": None,
            "p95_euclidean_error_bbox_long_side": None,
            "bias_x_px": None,
            "bias_y_px": None,
        }
    result: dict[str, Any] = {
        "visible_truth_count": len(visible),
        "predicted_available_count": len(localized),
        "valid_rate": round(len(localized) / len(visible), 8),
        "mean_euclidean_error_px": None,
        "p95_euclidean_error_px": None,
        "mean_euclidean_error_bbox_long_side": None,
        "p95_euclidean_error_bbox_long_side": None,
        "bias_x_px": None,
        "bias_y_px": None,
    }
    if localized:
        errors_px = np.asarray([row["error_px"] for row in localized], dtype=np.float64)
        errors_normalized = np.asarray(
            [row["error_bbox_long_side"] for row in localized], dtype=np.float64
        )
        result.update(
            {
                "mean_euclidean_error_px": round(float(np.mean(errors_px)), 8),
                "p95_euclidean_error_px": round(float(np.quantile(errors_px, 0.95)), 8),
                "mean_euclidean_error_bbox_long_side": round(
                    float(np.mean(errors_normalized)), 8
                ),
                "p95_euclidean_error_bbox_long_side": round(
                    float(np.quantile(errors_normalized, 0.95)), 8
                ),
                "bias_x_px": round(float(np.mean([row["dx_px"] for row in localized])), 8),
                "bias_y_px": round(float(np.mean([row["dy_px"] for row in localized])), 8),
            }
        )
    return result


def evaluate_small_roi_keypoints(pack_dir: str | Path) -> dict[str, Any]:
    pack_dir = Path(pack_dir).resolve()
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    _verify_pack_sources(pack_dir, manifest)
    validation_path = pack_dir / "compiled" / "validation-report.json"
    if not validation_path.is_file():
        raise SmallRoiTruthError("compile the truth pack before evaluation")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    _verify_compilation_lineage(pack_dir, validation)
    if validation.get("errors"):
        raise SmallRoiTruthError("truth pack contains invalid annotations")
    manual_rows = _read_jsonl(pack_dir / "compiled" / "manual-keypoints.jsonl")
    tasks = _read_jsonl(pack_dir / "tasks.jsonl")
    task_by_key = {
        (int(item["source_frame_index"]), str(item["joint_name"])): item for item in tasks
    }
    truth: dict[tuple[int, str], dict[str, Any]] = {}
    for record in manual_rows:
        validate_keypoint_annotation(record)
        for joint, value in record["joints"].items():
            key = (int(record["source_frame_index"]), str(joint))
            if key in truth:
                raise SmallRoiTruthError(f"duplicate compiled truth: {key}")
            truth[key] = value
    frames_path = Path(manifest["sources"]["experimental_frames"]["path"])
    frame_by_index = {_frame_index(row): row for row in _read_jsonl(frames_path)}
    detail_rows: list[dict[str, Any]] = []
    for key, truth_value in sorted(truth.items()):
        task = task_by_key.get(key)
        if task is None:
            raise SmallRoiTruthError(f"compiled truth is outside immutable task set: {key}")
        frame = frame_by_index[int(task["source_frame_index"])]
        pose = _pose_for_track(frame, task["person_track_id"])
        point = next(
            (
                item
                for item in (pose or {}).get("keypoints", [])
                if item.get("name") == task["joint_name"]
            ),
            None,
        )
        predicted_available = bool(point is not None and _valid_keypoint(point))
        visible = truth_value["visible"] is True
        width, height = int(task["frame_width"]), int(task["frame_height"])
        dx = dy = error_px = error_normalized = None
        if visible and predicted_available and point is not None:
            truth_x = float(truth_value["x_normalized"]) * width
            truth_y = float(truth_value["y_normalized"]) * height
            dx = float(point["x_px"]) - truth_x
            dy = float(point["y_px"]) - truth_y
            error_px = math.hypot(dx, dy)
            error_normalized = error_px / float(task["bbox_long_side_px"])
        detail_rows.append(
            {
                "task_id": task["task_id"],
                "source_frame_index": task["source_frame_index"],
                "timestamp_ms": task["timestamp_ms"],
                "joint_name": task["joint_name"],
                "bbox_long_side_px": task["bbox_long_side_px"],
                "bbox_size_stratum": task["bbox_size_stratum"],
                "truth_visible": visible,
                "predicted_available": predicted_available,
                "prediction_confidence": (
                    round(float(point["confidence"]), 8) if point is not None else None
                ),
                "dx_px": round(dx, 8) if dx is not None else None,
                "dy_px": round(dy, 8) if dy is not None else None,
                "error_px": round(error_px, 8) if error_px is not None else None,
                "error_bbox_long_side": (
                    round(error_normalized, 8) if error_normalized is not None else None
                ),
            }
        )
    ready = validation.get("status") == "ready_for_keypoint_error_evaluation"
    metrics = None
    per_joint = None
    per_bbox_stratum = None
    if ready:
        if len(detail_rows) != len(tasks):
            raise SmallRoiTruthError("ready pack does not cover every immutable task")
        metrics = _aggregate_errors(detail_rows)
        per_joint = {
            joint: _aggregate_errors([row for row in detail_rows if row["joint_name"] == joint])
            for joint in JOINTS
        }
        strata = sorted({str(row["bbox_size_stratum"]) for row in detail_rows})
        per_bbox_stratum = {
            stratum: _aggregate_errors(
                [row for row in detail_rows if row["bbox_size_stratum"] == stratum]
            )
            for stratum in strata
        }
    return {
        "schema_version": "1.0.0",
        "evaluator_version": EVALUATOR_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "evaluated_pending_external_acceptance_protocol" if ready else "annotation_required",
        "pack": {
            "pack_version": PACK_VERSION,
            "manifest_sha256": sha256_file(pack_dir / "manifest.json"),
            "validation_report_sha256": sha256_file(validation_path),
            "task_contract_sha256": manifest["task_contract_sha256"],
        },
        "counts": {
            "required_frames": manifest["scope"]["frame_count"],
            "required_joint_tasks": manifest["scope"]["joint_task_count"],
            "accepted_joint_truth": len(detail_rows),
            "visible_joint_truth": sum(row["truth_visible"] for row in detail_rows),
            "invisible_joint_truth": sum(not row["truth_visible"] for row in detail_rows),
        },
        "metrics": metrics,
        "per_joint": per_joint,
        "per_bbox_size_stratum": per_bbox_stratum,
        "details": detail_rows if ready else [],
        "pck": {
            "value": None,
            "threshold": None,
            "status": "external_preregistered_threshold_required",
        },
        "routing_decision": {
            "status": "external_preregistered_acceptance_protocol_required",
            "production_min_roi_size_px": 32,
            "experimental_min_roi_size_px": 8,
            "switch_allowed": False,
        },
        "safety": {
            "ground_truth_provided": bool(detail_rows),
            "full_ground_truth_coverage": ready,
            "metrics_are_pose_localization_accuracy": ready,
            "model_values_used_as_truth": False,
            "acceptance_threshold_generated": False,
            "production_enabled": False,
            "automatic_profile_fallback_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
