from __future__ import annotations

import argparse
import json
import statistics
from collections import deque
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from render_pose_keypoint_focus_video import (
    _load_jsonl,
    _put_text,
    _render_crop,
    _selected_bbox,
    _selected_pose,
    _smooth_box,
    _valid,
    _xy,
)


BACKEND = "rtmpose-m-halpe26-256x192"
COMMON_COLOR = (242, 242, 242)
CENTRAL_ADDED_COLOR = (80, 190, 255)
DERIVED_COLOR = (110, 245, 125)
EXTRA_COLORS = {
    "big_toe": (255, 0, 255),
    "small_toe": (255, 255, 0),
    "heel": (0, 220, 255),
}
COMMON_CONNECTIONS = (
    ("nose", "left_eye"),
    ("nose", "right_eye"),
    ("left_eye", "left_ear"),
    ("right_eye", "right_ear"),
    ("left_shoulder", "right_shoulder"),
    ("left_shoulder", "left_elbow"),
    ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"),
    ("right_elbow", "right_wrist"),
    ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"),
    ("left_hip", "right_hip"),
    ("left_hip", "left_knee"),
    ("left_knee", "left_ankle"),
    ("right_hip", "right_knee"),
    ("right_knee", "right_ankle"),
)
CENTRAL_CONNECTIONS = (
    ("head", "nose"),
    ("head", "neck"),
    ("neck", "left_shoulder"),
    ("neck", "right_shoulder"),
    ("neck", "hip"),
    ("hip", "left_hip"),
    ("hip", "right_hip"),
)
FOOT_CONNECTIONS = tuple(
    (f"{side}_{first}", f"{side}_{second}")
    for side in ("left", "right")
    for first, second in (
        ("ankle", "heel"),
        ("ankle", "big_toe"),
        ("ankle", "small_toe"),
        ("heel", "big_toe"),
        ("big_toe", "small_toe"),
    )
)
LABEL_OFFSETS = {
    "nose": (12, 4),
    "left_eye": (-35, -10),
    "right_eye": (12, -10),
    "left_ear": (-40, 12),
    "right_ear": (12, 14),
    "head": (12, -18),
    "neck": (12, -10),
    "hip": (12, -8),
}


def _point_color(name: str, index: int) -> tuple[int, int, int]:
    if index <= 16:
        return COMMON_COLOR
    if name in {"head", "neck", "hip"}:
        return CENTRAL_ADDED_COLOR
    token = next((item for item in EXTRA_COLORS if item in name), None)
    return EXTRA_COLORS[token] if token else CENTRAL_ADDED_COLOR


def _body_box(
    bbox: list[float] | None,
    frame_shape: tuple[int, int, int],
    state: dict[str, float],
) -> tuple[float, float, float, float]:
    frame_h, frame_w = frame_shape[:2]
    if bbox is None:
        return 0.0, 0.0, float(frame_w), float(frame_h)
    x1, y1, x2, y2 = (float(value) for value in bbox)
    height = max(280.0, y2 - y1)
    crop_h = min(float(frame_h), height * 1.30)
    crop_w = min(float(frame_w), crop_h * (820.0 / 960.0))
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    return _smooth_box(
        (cx - crop_w / 2.0, cy - crop_h / 2.0, cx + crop_w / 2.0, cy + crop_h / 2.0),
        state,
        "full_body_26",
        alpha=0.16,
    )


def _draw_line(
    canvas: np.ndarray,
    points: dict[str, dict[str, Any]],
    mapper: Callable[[tuple[float, float]], tuple[int, int]],
    first: str,
    second: str,
    color: tuple[int, int, int],
    thickness: int,
) -> None:
    a, b = points.get(first), points.get(second)
    if _valid(a) and _valid(b):
        cv2.line(canvas, mapper(_xy(a)), mapper(_xy(b)), color, thickness, cv2.LINE_AA)


def _draw_derived_body_center(
    canvas: np.ndarray,
    points: dict[str, dict[str, Any]],
    mapper: Callable[[tuple[float, float]], tuple[int, int]],
) -> None:
    required = ("left_shoulder", "right_shoulder", "hip")
    if not all(_valid(points.get(name)) for name in required):
        return
    left_shoulder = np.asarray(_xy(points["left_shoulder"]), dtype=float)
    right_shoulder = np.asarray(_xy(points["right_shoulder"]), dtype=float)
    hip = np.asarray(_xy(points["hip"]), dtype=float)
    shoulder_center = (left_shoulder + right_shoulder) / 2.0
    body_center = 0.4 * shoulder_center + 0.6 * hip
    center = mapper((float(body_center[0]), float(body_center[1])))
    diamond = np.asarray(
        [(center[0], center[1] - 9), (center[0] + 9, center[1]), (center[0], center[1] + 9), (center[0] - 9, center[1])],
        dtype=np.int32,
    )
    cv2.fillPoly(canvas, [diamond], DERIVED_COLOR, cv2.LINE_AA)
    _put_text(canvas, "D BODY CENTER PROXY", (center[0] + 14, center[1] + 5), 0.40, DERIVED_COLOR, 1)


def _draw_pose(
    canvas: np.ndarray,
    points: dict[str, dict[str, Any]],
    mapper: Callable[[tuple[float, float]], tuple[int, int]],
    point_defs: list[dict[str, Any]],
    trails: dict[str, deque[tuple[float, float]]],
) -> None:
    for first, second in COMMON_CONNECTIONS:
        _draw_line(canvas, points, mapper, first, second, (220, 220, 220), 3)
    for first, second in CENTRAL_CONNECTIONS:
        _draw_line(canvas, points, mapper, first, second, CENTRAL_ADDED_COLOR, 3)
    for first, second in FOOT_CONNECTIONS:
        token = next((item for item in EXTRA_COLORS if item in second), None)
        _draw_line(canvas, points, mapper, first, second, EXTRA_COLORS.get(token, (245, 245, 245)), 3)

    for definition in point_defs:
        index, name = int(definition["index"]), definition["name"]
        point = points.get(name)
        if not _valid(point):
            continue
        mapped = mapper(_xy(point))
        color = _point_color(name, index)
        if index >= 17:
            history = [mapper(item) for item in trails[name]]
            if len(history) >= 2:
                cv2.polylines(canvas, [np.asarray(history, dtype=np.int32)], False, color, 2, cv2.LINE_AA)
        cv2.circle(canvas, mapped, 11 if index >= 17 else 9, (7, 7, 7), -1, cv2.LINE_AA)
        cv2.circle(canvas, mapped, 8 if index >= 17 else 6, color, -1, cv2.LINE_AA)
        default_x = -35 if name.startswith("left_") else 12
        offset_x, offset_y = LABEL_OFFSETS.get(name, (default_x, 5))
        _put_text(canvas, str(index), (mapped[0] + offset_x, mapped[1] + offset_y), 0.52, color, 2)
    _draw_derived_body_center(canvas, points, mapper)


def _draw_panel(
    panel: np.ndarray,
    point_defs: list[dict[str, Any]],
    points: dict[str, dict[str, Any]],
    timestamp_ms: int,
) -> None:
    panel[:] = (18, 21, 26)
    valid_count = sum(_valid(points.get(item["name"])) for item in point_defs)
    _put_text(panel, "ACTUAL MODEL OUTPUT", (18, 31), 0.66, CENTRAL_ADDED_COLOR, 2)
    _put_text(panel, f"RTMPose-M Halpe26  |  VALID {valid_count}/26", (18, 59), 0.50, (238, 238, 238), 1)
    _put_text(panel, f"same primary player  |  t={timestamp_ms / 1000.0:.2f}s", (18, 83), 0.43, (200, 200, 200), 1)

    _put_text(panel, "0-16  COCO-17 COMMON POINTS", (18, 111), 0.47, COMMON_COLOR, 1)
    y = 135
    for definition in point_defs[:17]:
        index, name = int(definition["index"]), definition["name"]
        point = points.get(name)
        confidence = float(point.get("confidence", 0.0)) if point else 0.0
        color = COMMON_COLOR if _valid(point) else (120, 120, 120)
        _put_text(panel, f"{index:02d}  {name:<16}  {confidence:.2f}", (22, y), 0.38, color, 1)
        y += 21

    y += 7
    _put_text(panel, "17-25  ADDED BY HALPE26", (18, y), 0.48, CENTRAL_ADDED_COLOR, 1)
    y += 25
    for definition in point_defs[17:]:
        index, name = int(definition["index"]), definition["name"]
        point = points.get(name)
        confidence = float(point.get("confidence", 0.0)) if point else 0.0
        color = _point_color(name, index) if _valid(point) else (120, 120, 120)
        _put_text(panel, f"{index:02d}  {name:<16}  {confidence:.2f}", (22, y), 0.38, color, 1)
        y += 21

    y += 6
    _put_text(panel, "GREEN DIAMOND = DERIVED, NOT A MODEL POINT", (18, y), 0.39, DERIVED_COLOR, 1)
    y += 27
    _put_text(panel, "STILL NOT OUTPUT BY HALPE26", (18, y), 0.45, (120, 170, 255), 1)
    y += 23
    _put_text(panel, "- thoracic / lumbar spine segments", (22, y), 0.38, (220, 220, 220), 1)
    y += 20
    _put_text(panel, "- palms, fingers, thumb and grip geometry", (22, y), 0.38, (220, 220, 220), 1)
    y += 20
    _put_text(panel, "- midfoot, sole pressure/contact and foot arch", (22, y), 0.38, (220, 220, 220), 1)
    y += 20
    _put_text(panel, "- depth, true 3D rotation and true gaze", (22, y), 0.38, (220, 220, 220), 1)
    cv2.rectangle(panel, (12, panel.shape[0] - 52), (panel.shape[1] - 12, panel.shape[0] - 10), (35, 45, 58), -1)
    _put_text(panel, "SCORING STATUS: calibration_required", (24, panel.shape[0] - 25), 0.48, (130, 190, 255), 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render all actual Halpe26 body points with an explicit coverage legend")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args()
    if args.start_frame < 0:
        raise ValueError("--start-frame must be non-negative")

    root = Path.cwd()
    point_defs = json.loads(
        (root / "src" / "rallymate_vision" / "pose" / "data" / "keypoint_schemas.json").read_text(encoding="utf-8")
    )["formats"]["halpe26"]["keypoints"]
    records = _load_jsonl(root / "runs" / "pose-ab" / BACKEND / args.video_id / "frames.jsonl")
    by_index = {int(item["frame"]["processed_index"]): item for item in records}
    timeline = {
        int(item["processed_index"]): item
        for item in _load_jsonl(root / "reports" / "pose-scoring-ab" / BACKEND / args.video_id / "primary-player.jsonl")
    }
    source = root / "FULL-TEST" / f"{args.video_id}.mp4"
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise FileNotFoundError(source)
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    capture.set(cv2.CAP_PROP_POS_FRAMES, args.start_frame)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_width, output_height = 1280, 960
    writer = cv2.VideoWriter(str(args.output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (output_width, output_height))
    if not writer.isOpened():
        raise RuntimeError("could not initialize MP4 writer")

    trails = {item["name"]: deque(maxlen=10) for item in point_defs[17:]}
    crop_state: dict[str, float] = {}
    confidence_samples: dict[str, list[float]] = {item["name"]: [] for item in point_defs}
    rendered = 0
    pose_frames = 0
    all_valid_frames = 0
    source_index = args.start_frame
    while True:
        ok, frame = capture.read()
        if not ok or (args.max_frames is not None and rendered >= args.max_frames):
            break
        record = by_index.get(source_index)
        timeline_item = timeline.get(source_index)
        if record is None:
            break
        pose = _selected_pose(record, timeline_item)
        points = {item["name"]: item for item in pose.get("keypoints", [])} if pose else {}
        bbox = _selected_bbox(record, timeline_item)
        if points:
            pose_frames += 1
            all_valid_frames += all(_valid(points.get(item["name"])) for item in point_defs)
            for definition in point_defs:
                point = points.get(definition["name"])
                if point is not None:
                    confidence_samples[definition["name"]].append(float(point.get("confidence", 0.0)))
            for definition in point_defs[17:]:
                point = points.get(definition["name"])
                if _valid(point):
                    trails[definition["name"]].append(_xy(point))

        body_box = _body_box(bbox, frame.shape, crop_state)
        body, mapper = _render_crop(frame, body_box, 820, output_height)
        _draw_pose(body, points, mapper, point_defs, trails)
        cv2.rectangle(body, (0, 0), (820, 83), (12, 14, 18), -1)
        _put_text(body, "FULL BODY - ALL 26 ACTUAL HALPE KEYPOINTS", (18, 31), 0.70, CENTRAL_ADDED_COLOR, 2)
        _put_text(body, "number labels map to the live confidence list; trails appear only on the 9 added points", (18, 60), 0.45, (235, 235, 235), 1)
        _put_text(body, "green diamond is explicitly derived and is not counted in 26", (18, 80), 0.40, DERIVED_COLOR, 1)
        panel = np.empty((output_height, 460, 3), dtype=np.uint8)
        timestamp_ms = int(record["frame"]["timestamp_ms"])
        _draw_panel(panel, point_defs, points, timestamp_ms)
        composite = np.hstack((body, panel))
        cv2.line(composite, (820, 0), (820, output_height), (230, 230, 230), 2, cv2.LINE_AA)
        writer.write(composite)
        rendered += 1
        source_index += 1

    capture.release()
    writer.release()
    check = cv2.VideoCapture(str(args.output))
    validated_frames = int(check.get(cv2.CAP_PROP_FRAME_COUNT))
    validated_width = int(check.get(cv2.CAP_PROP_FRAME_WIDTH))
    validated_height = int(check.get(cv2.CAP_PROP_FRAME_HEIGHT))
    check.release()
    if rendered <= 0 or validated_frames != rendered:
        raise RuntimeError(f"rendered frame count mismatch: expected {rendered}, got {validated_frames}")

    per_point = {}
    for definition in point_defs:
        name = definition["name"]
        samples = confidence_samples[name]
        per_point[name] = {
            "index": int(definition["index"]),
            "valid_rate": round(sum(value >= 0.25 for value in samples) / max(len(samples), 1), 6),
            "median_confidence": round(statistics.median(samples), 6) if samples else None,
        }
    metadata = {
        "schema_version": "1.0.0",
        "renderer_version": "full-body-keypoint-video/1.0.0",
        "video_id": args.video_id,
        "pose_backend": BACKEND,
        "keypoint_format": "halpe26",
        "start_frame": args.start_frame,
        "frames": rendered,
        "fps": fps,
        "duration_seconds": round(rendered / fps, 3),
        "resolution": [validated_width, validated_height],
        "selected_primary_pose_frames": pose_frames,
        "all_26_points_valid_rate": round(all_valid_frames / max(pose_frames, 1), 6),
        "per_point": per_point,
        "coverage_audit": "reports/pose-scoring-ab/full-body-keypoint-coverage.json",
        "claim": "complete_actual_halpe26_output_not_complete_scoring_topology",
        "scoring_status": "calibration_required",
        "output": str(args.output),
    }
    if args.metadata_output:
        args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
        args.metadata_output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
