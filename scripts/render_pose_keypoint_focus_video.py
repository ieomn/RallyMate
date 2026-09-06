from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np


YOLO = "yolo"
RTMPOSE = "rtmpose-m-halpe26-256x192"
BACKENDS = (YOLO, RTMPOSE)
MODEL_COLORS = {YOLO: (235, 90, 255), RTMPOSE: (80, 190, 255)}
MODEL_TITLES = {YOLO: "YOLO11 POSE  |  COCO-17", RTMPOSE: "RTMPOSE-M  |  HALPE-26"}
EXTRA_POINTS = (
    "left_big_toe",
    "right_big_toe",
    "left_small_toe",
    "right_small_toe",
    "left_heel",
    "right_heel",
)
EXTRA_COLORS = {
    "big_toe": (255, 0, 255),
    "small_toe": (255, 255, 0),
    "heel": (0, 220, 255),
}
BODY_CONNECTIONS = (
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


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _frames_path(root: Path, backend: str, video_id: str) -> Path:
    if backend == YOLO:
        return root / "runs" / "full-test" / video_id / "frames.jsonl"
    return root / "runs" / "pose-ab" / backend / video_id / "frames.jsonl"


def _point_map(pose: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {point["name"]: point for point in pose.get("keypoints", [])} if pose else {}


def _valid(point: dict[str, Any] | None, threshold: float = 0.25) -> bool:
    return point is not None and float(point.get("confidence", 0.0)) >= threshold


def _xy(point: dict[str, Any]) -> tuple[float, float]:
    return float(point["x_px"]), float(point["y_px"])


def _selected_pose(record: dict[str, Any], timeline_item: dict[str, Any] | None) -> dict[str, Any] | None:
    if timeline_item is None:
        return None
    source_track = timeline_item.get("source_track_id")
    return next((pose for pose in record.get("poses", []) if pose.get("person_track_id") == source_track), None)


def _selected_bbox(record: dict[str, Any], timeline_item: dict[str, Any] | None) -> list[float] | None:
    if timeline_item is None:
        return None
    source_track = timeline_item.get("source_track_id")
    detection = next((item for item in record.get("detections", []) if item.get("track_id") == source_track), None)
    return detection.get("bbox_px") if detection else None


def _smooth_box(
    box: tuple[float, float, float, float],
    state: dict[str, float],
    prefix: str,
    alpha: float = 0.16,
) -> tuple[float, float, float, float]:
    values = dict(zip(("x1", "y1", "x2", "y2"), box))
    result: list[float] = []
    for name in ("x1", "y1", "x2", "y2"):
        key = f"{prefix}_{name}"
        value = values[name]
        state[key] = value if key not in state else (1.0 - alpha) * state[key] + alpha * value
        result.append(state[key])
    return tuple(result)  # type: ignore[return-value]


def _full_body_box(
    bbox: list[float] | None,
    frame_shape: tuple[int, int, int],
    state: dict[str, float],
) -> tuple[float, float, float, float]:
    frame_h, frame_w = frame_shape[:2]
    if bbox is None:
        return 0.0, 0.0, float(frame_w), float(frame_h)
    x1, y1, x2, y2 = (float(value) for value in bbox)
    height = max(y2 - y1, 260.0)
    crop_h = min(float(frame_h), height * 1.30)
    crop_w = min(float(frame_w), crop_h * (640.0 / 444.0))
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    box = (cx - crop_w / 2.0, cy - crop_h / 2.0, cx + crop_w / 2.0, cy + crop_h / 2.0)
    return _smooth_box(box, state, "body")


def _foot_box(
    points: dict[str, dict[str, Any]],
    side: str,
    bbox: list[float] | None,
    frame_shape: tuple[int, int, int],
    state: dict[str, float],
) -> tuple[float, float, float, float]:
    frame_h, frame_w = frame_shape[:2]
    names = (f"{side}_ankle", f"{side}_big_toe", f"{side}_small_toe", f"{side}_heel")
    available = [_xy(points[name]) for name in names if _valid(points.get(name))]
    if available:
        cx = sum(point[0] for point in available) / len(available)
        cy = sum(point[1] for point in available) / len(available) - 14.0
    elif bbox is not None:
        x1, y1, x2, y2 = (float(value) for value in bbox)
        cx = x1 + (0.35 if side == "left" else 0.65) * (x2 - x1)
        cy = y1 + 0.88 * (y2 - y1)
    else:
        cx, cy = frame_w / 2.0, frame_h * 0.8
    bbox_width = float(bbox[2] - bbox[0]) if bbox is not None else 140.0
    crop_w = max(105.0, min(190.0, bbox_width * 0.90))
    crop_h = crop_w * 1.20
    box = (cx - crop_w / 2.0, cy - crop_h / 2.0, cx + crop_w / 2.0, cy + crop_h / 2.0)
    return _smooth_box(box, state, f"foot_{side}", alpha=0.24)


def _render_crop(
    frame: np.ndarray,
    box: tuple[float, float, float, float],
    output_width: int,
    output_height: int,
) -> tuple[np.ndarray, Callable[[tuple[float, float]], tuple[int, int]]]:
    frame_h, frame_w = frame.shape[:2]
    x1, y1, x2, y2 = box
    crop_w = min(x2 - x1, float(frame_w))
    crop_h = min(y2 - y1, float(frame_h))
    x1 = max(0.0, min(float(frame_w) - crop_w, x1))
    y1 = max(0.0, min(float(frame_h) - crop_h, y1))
    x2, y2 = x1 + crop_w, y1 + crop_h
    ix1, iy1 = int(math.floor(x1)), int(math.floor(y1))
    ix2, iy2 = int(math.ceil(x2)), int(math.ceil(y2))
    crop = frame[iy1:iy2, ix1:ix2]
    scale = min(output_width / max(crop.shape[1], 1), output_height / max(crop.shape[0], 1))
    resized_w = max(1, int(round(crop.shape[1] * scale)))
    resized_h = max(1, int(round(crop.shape[0] * scale)))
    resized = cv2.resize(crop, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((output_height, output_width, 3), (12, 14, 18), dtype=np.uint8)
    offset_x = (output_width - resized_w) // 2
    offset_y = (output_height - resized_h) // 2
    canvas[offset_y:offset_y + resized_h, offset_x:offset_x + resized_w] = resized

    def mapper(point: tuple[float, float]) -> tuple[int, int]:
        return (
            int(round((point[0] - ix1) * scale + offset_x)),
            int(round((point[1] - iy1) * scale + offset_y)),
        )

    return canvas, mapper


def _inside(canvas: np.ndarray, point: tuple[int, int], margin: int = 3) -> bool:
    return margin <= point[0] < canvas.shape[1] - margin and margin <= point[1] < canvas.shape[0] - margin


def _put_text(
    canvas: np.ndarray,
    text: str,
    origin: tuple[int, int],
    scale: float,
    color: tuple[int, int, int],
    thickness: int = 1,
) -> None:
    cv2.putText(canvas, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, (5, 5, 5), thickness + 3, cv2.LINE_AA)
    cv2.putText(canvas, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def _draw_pose(
    canvas: np.ndarray,
    points: dict[str, dict[str, Any]],
    mapper: Callable[[tuple[float, float]], tuple[int, int]],
    backend: str,
    trails: dict[str, deque[tuple[float, float]]],
    foot_side: str | None = None,
) -> None:
    body_connections = BODY_CONNECTIONS if foot_side is None else (
        (f"{foot_side}_knee", f"{foot_side}_ankle"),
    )
    for first, second in body_connections:
        first_point, second_point = points.get(first), points.get(second)
        if _valid(first_point) and _valid(second_point):
            a, b = mapper(_xy(first_point)), mapper(_xy(second_point))
            if _inside(canvas, a) or _inside(canvas, b):
                cv2.line(canvas, a, b, MODEL_COLORS[backend], 3, cv2.LINE_AA)
    if backend == RTMPOSE:
        for first, second in FOOT_CONNECTIONS:
            if foot_side is not None and not first.startswith(f"{foot_side}_"):
                continue
            first_point, second_point = points.get(first), points.get(second)
            if _valid(first_point) and _valid(second_point):
                a, b = mapper(_xy(first_point)), mapper(_xy(second_point))
                if _inside(canvas, a) or _inside(canvas, b):
                    cv2.line(canvas, a, b, (245, 245, 245), 2, cv2.LINE_AA)
    names = points.keys() if foot_side is None else (
        f"{foot_side}_knee",
        f"{foot_side}_ankle",
        f"{foot_side}_big_toe",
        f"{foot_side}_small_toe",
        f"{foot_side}_heel",
    )
    for name in names:
        point = points.get(name)
        if not _valid(point):
            continue
        mapped = mapper(_xy(point))
        if not _inside(canvas, mapped):
            continue
        extra_type = next((token for token in EXTRA_COLORS if token in name), None)
        color = EXTRA_COLORS[extra_type] if extra_type else (255, 255, 255)
        radius = 9 if extra_type and foot_side is not None else (7 if extra_type else 4)
        cv2.circle(canvas, mapped, radius + 3, (8, 8, 8), -1, cv2.LINE_AA)
        cv2.circle(canvas, mapped, radius, color, -1, cv2.LINE_AA)
        if extra_type and backend == RTMPOSE:
            history = [mapper(item) for item in trails[name]]
            history = [item for item in history if _inside(canvas, item)]
            if len(history) >= 2:
                cv2.polylines(canvas, [np.asarray(history, dtype=np.int32)], False, color, 2, cv2.LINE_AA)
            if foot_side is not None:
                label = {"big_toe": "BIG TOE", "small_toe": "SMALL TOE", "heel": "HEEL"}[extra_type]
                confidence = float(point.get("confidence", 0.0))
                text_x = 166
                text_y = {"heel": 49, "small_toe": 72, "big_toe": 95}[extra_type]
                cv2.line(canvas, mapped, (text_x - 7, text_y - 5), color, 1, cv2.LINE_AA)
                _put_text(canvas, f"{label} {confidence:.2f}", (text_x, text_y), 0.38, color, 1)


def _ankle_angle(points: dict[str, dict[str, Any]], side: str) -> float | None:
    names = (f"{side}_knee", f"{side}_ankle", f"{side}_big_toe", f"{side}_small_toe")
    selected = [points.get(name) for name in names]
    if not all(_valid(point) for point in selected):
        return None
    knee, ankle, big_toe, small_toe = [np.asarray(_xy(point), dtype=float) for point in selected]  # type: ignore[arg-type]
    forefoot = (big_toe + small_toe) / 2.0
    first, second = knee - ankle, forefoot - ankle
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator <= 1e-9:
        return None
    cosine = float(np.clip(np.dot(first, second) / denominator, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _draw_header(canvas: np.ndarray, backend: str, points: dict[str, dict[str, Any]], timestamp_ms: int) -> None:
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], canvas.shape[0]), (18, 21, 26), -1)
    _put_text(canvas, MODEL_TITLES[backend], (16, 29), 0.70, MODEL_COLORS[backend], 2)
    if backend == YOLO:
        _put_text(canvas, "Topology output: 17 points  |  heel/toe samples: 0 / 6", (16, 57), 0.48, (230, 230, 230), 1)
        _put_text(canvas, "2D ankle angle: UNAVAILABLE (no forefoot direction)", (16, 82), 0.47, (130, 170, 255), 1)
    else:
        valid_extra = sum(_valid(points.get(name)) for name in EXTRA_POINTS)
        left_angle, right_angle = _ankle_angle(points, "left"), _ankle_angle(points, "right")
        _put_text(canvas, f"Topology output: 26 points  |  heel/toe samples valid: {valid_extra} / 6", (16, 57), 0.48, (230, 230, 230), 1)
        angle_text = (
            f"2D ankle diagnostic: L {left_angle:.1f} deg  |  R {right_angle:.1f} deg"
            if left_angle is not None and right_angle is not None
            else "2D ankle diagnostic: unavailable in this frame"
        )
        _put_text(canvas, angle_text, (16, 82), 0.47, (255, 255, 0), 1)
    _put_text(canvas, f"t={timestamp_ms / 1000.0:.2f}s", (535, 29), 0.52, (255, 255, 255), 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a two-model dynamic video focused on Halpe26 foot keypoints")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args()
    if args.start_frame < 0:
        raise ValueError("--start-frame must be non-negative")

    root = Path.cwd()
    source = root / "FULL-TEST" / f"{args.video_id}.mp4"
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise FileNotFoundError(source)
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    capture.set(cv2.CAP_PROP_POS_FRAMES, args.start_frame)
    output_width, output_height = 1280, 960
    header_h, full_h, feet_h, footer_h = 96, 444, 350, 70
    args.output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(args.output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (output_width, output_height)
    )
    if not writer.isOpened():
        raise RuntimeError("could not initialize MP4 writer")

    data: dict[str, dict[str, Any]] = {}
    for backend in BACKENDS:
        records = _load_jsonl(_frames_path(root, backend, args.video_id))
        timeline_path = root / "reports" / "pose-scoring-ab" / backend / args.video_id / "primary-player.jsonl"
        data[backend] = {
            "frames": {int(item["frame"]["processed_index"]): item for item in records},
            "timeline": {int(item["processed_index"]): item for item in _load_jsonl(timeline_path)},
        }

    crop_state: dict[str, float] = {}
    trails = {
        backend: {name: deque(maxlen=12) for name in EXTRA_POINTS}
        for backend in BACKENDS
    }
    rendered = 0
    frames_with_pose = 0
    frames_with_six_extra = 0
    valid_extra_total = 0
    source_index = args.start_frame
    while True:
        ok, source_frame = capture.read()
        if not ok or (args.max_frames is not None and rendered >= args.max_frames):
            break
        records = {backend: data[backend]["frames"].get(source_index) for backend in BACKENDS}
        if records[YOLO] is None or records[RTMPOSE] is None:
            break
        timestamp_ms = int(records[YOLO]["frame"]["timestamp_ms"])
        timeline_items = {backend: data[backend]["timeline"].get(source_index) for backend in BACKENDS}
        poses = {
            backend: _selected_pose(records[backend], timeline_items[backend])
            for backend in BACKENDS
        }
        points = {backend: _point_map(poses[backend]) for backend in BACKENDS}
        reference_bbox = _selected_bbox(records[YOLO], timeline_items[YOLO])
        if reference_bbox is None:
            reference_bbox = _selected_bbox(records[RTMPOSE], timeline_items[RTMPOSE])
        body_box = _full_body_box(reference_bbox, source_frame.shape, crop_state)
        foot_boxes = {
            side: _foot_box(points[RTMPOSE], side, reference_bbox, source_frame.shape, crop_state)
            for side in ("left", "right")
        }
        valid_extra = sum(_valid(points[RTMPOSE].get(name)) for name in EXTRA_POINTS)
        if points[RTMPOSE]:
            frames_with_pose += 1
            valid_extra_total += valid_extra
            frames_with_six_extra += valid_extra == len(EXTRA_POINTS)
        for backend in BACKENDS:
            for name in EXTRA_POINTS:
                point = points[backend].get(name)
                if _valid(point):
                    trails[backend][name].append(_xy(point))

        columns: list[np.ndarray] = []
        for backend in BACKENDS:
            column = np.full((output_height - footer_h, 640, 3), (12, 14, 18), dtype=np.uint8)
            _draw_header(column[0:header_h], backend, points[backend], timestamp_ms)
            full_pane, full_mapper = _render_crop(source_frame, body_box, 640, full_h)
            _draw_pose(full_pane, points[backend], full_mapper, backend, trails[backend])
            cv2.rectangle(full_pane, (0, 0), (150, 27), (10, 10, 10), -1)
            _put_text(full_pane, "FULL BODY", (10, 20), 0.48, (235, 235, 235), 1)
            column[header_h:header_h + full_h] = full_pane
            for side_index, side in enumerate(("left", "right")):
                foot_pane, foot_mapper = _render_crop(source_frame, foot_boxes[side], 320, feet_h)
                _draw_pose(foot_pane, points[backend], foot_mapper, backend, trails[backend], foot_side=side)
                cv2.rectangle(foot_pane, (0, 0), (136, 27), (10, 10, 10), -1)
                _put_text(foot_pane, f"{side.upper()} FOOT", (8, 20), 0.44, (235, 235, 235), 1)
                if backend == YOLO:
                    cv2.rectangle(foot_pane, (10, feet_h - 43), (310, feet_h - 8), (10, 10, 10), -1)
                    _put_text(foot_pane, "NO HEEL / TOE OUTPUT", (25, feet_h - 19), 0.48, (130, 170, 255), 1)
                start_x = side_index * 320
                column[header_h + full_h:header_h + full_h + feet_h, start_x:start_x + 320] = foot_pane
            columns.append(column)

        composite = np.full((output_height, output_width, 3), (12, 14, 18), dtype=np.uint8)
        composite[:output_height - footer_h, :640] = columns[0]
        composite[:output_height - footer_h, 640:] = columns[1]
        cv2.line(composite, (640, 0), (640, output_height - footer_h), (230, 230, 230), 2, cv2.LINE_AA)
        cv2.rectangle(composite, (0, output_height - footer_h), (output_width, output_height), (18, 21, 26), -1)
        _put_text(
            composite,
            "MAGENTA=BIG TOE   CYAN=SMALL TOE   YELLOW=HEEL   WHITE=COCO JOINT",
            (20, output_height - 40),
            0.54,
            (235, 235, 235),
            1,
        )
        _put_text(
            composite,
            "VISUAL OUTPUT CHECK ONLY - extra keypoints do not prove scoring accuracy; grade remains calibration_required",
            (20, output_height - 13),
            0.47,
            (130, 190, 255),
            1,
        )
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
    metadata = {
        "schema_version": "1.0.0",
        "renderer_version": "pose-keypoint-focus-video/1.0.0",
        "video_id": args.video_id,
        "comparison": [YOLO, RTMPOSE],
        "start_frame": args.start_frame,
        "frames": rendered,
        "fps": fps,
        "duration_seconds": round(rendered / fps, 3),
        "resolution": [validated_width, validated_height],
        "topology_points": {YOLO: 17, RTMPOSE: 26},
        "added_foot_points": list(EXTRA_POINTS),
        "selected_primary_pose_frames": frames_with_pose,
        "six_added_points_valid_rate": round(frames_with_six_extra / max(frames_with_pose, 1), 6),
        "added_points_valid_rate": round(valid_extra_total / max(frames_with_pose * len(EXTRA_POINTS), 1), 6),
        "interpretation": "visual_output_check_only",
        "scoring_status": "calibration_required",
        "output": str(args.output),
    }
    if args.metadata_output:
        args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
        args.metadata_output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
