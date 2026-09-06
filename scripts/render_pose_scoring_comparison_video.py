from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np


BACKENDS = (
    "yolo",
    "rtmpose-s-halpe26-256x192",
    "rtmpose-m-halpe26-256x192",
    "rtmpose-m-halpe26-384x288",
)
LABELS = {
    "yolo": "YOLO COCO17",
    "rtmpose-s-halpe26-256x192": "RTMPose-S Halpe26 256x192",
    "rtmpose-m-halpe26-256x192": "RTMPose-M Halpe26 256x192",
    "rtmpose-m-halpe26-384x288": "RTMPose-M Halpe26 384x288",
}
COLORS = {
    "yolo": (235, 90, 255),
    "rtmpose-s-halpe26-256x192": (90, 235, 100),
    "rtmpose-m-halpe26-256x192": (80, 190, 255),
    "rtmpose-m-halpe26-384x288": (255, 155, 60),
}
BODY_CONNECTIONS = (
    ("left_shoulder", "right_shoulder"),
    ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
    ("left_shoulder", "left_hip"), ("right_shoulder", "right_hip"),
    ("left_hip", "right_hip"),
    ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
    ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
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
    if backend == "yolo":
        return root / "runs" / "full-test" / video_id / "frames.jsonl"
    return root / "runs" / "pose-ab" / backend / video_id / "frames.jsonl"


def _point_map(pose: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {point["name"]: point for point in pose.get("keypoints", [])} if pose else {}


def _valid(point: dict[str, Any] | None, threshold: float = 0.25) -> bool:
    return point is not None and float(point.get("confidence", 0.0)) >= threshold


def _xy(point: dict[str, Any]) -> tuple[int, int]:
    return int(round(point["x_px"])), int(round(point["y_px"]))


def _ankle_angle(points: dict[str, dict[str, Any]], side: str) -> float | None:
    names = (f"{side}_knee", f"{side}_ankle", f"{side}_big_toe", f"{side}_small_toe")
    selected = [points.get(name) for name in names]
    if not all(_valid(point) for point in selected):
        return None
    knee, ankle, big_toe, small_toe = [np.asarray(_xy(point), dtype=float) for point in selected]
    forefoot = (big_toe + small_toe) / 2.0
    first = knee - ankle
    second = forefoot - ankle
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator <= 1e-9:
        return None
    cosine = float(np.clip(np.dot(first, second) / denominator, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _selected_pose(record: dict[str, Any], timeline_item: dict[str, Any] | None) -> dict[str, Any] | None:
    if timeline_item is None:
        return None
    source_track = timeline_item.get("source_track_id")
    return next((pose for pose in record.get("poses", []) if pose.get("person_track_id") == source_track), None)


def _draw_pose(frame: np.ndarray, pose: dict[str, Any] | None, color: tuple[int, int, int]) -> tuple[np.ndarray, dict[str, dict[str, Any]]]:
    canvas = frame.copy()
    points = _point_map(pose)
    for first, second in BODY_CONNECTIONS:
        a, b = points.get(first), points.get(second)
        if _valid(a) and _valid(b):
            cv2.line(canvas, _xy(a), _xy(b), color, 3, cv2.LINE_AA)
    for first, second in FOOT_CONNECTIONS:
        a, b = points.get(first), points.get(second)
        if _valid(a) and _valid(b):
            cv2.line(canvas, _xy(a), _xy(b), (255, 255, 0), 3, cv2.LINE_AA)
    for name, point in points.items():
        if not _valid(point):
            continue
        foot = any(token in name for token in ("toe", "heel"))
        cv2.circle(canvas, _xy(point), 5 if foot else 4, (255, 255, 0) if foot else (255, 255, 255), -1, cv2.LINE_AA)
        if foot:
            cv2.circle(canvas, _xy(point), 7, (20, 20, 20), 1, cv2.LINE_AA)
    return canvas, points


def _letterbox(image: np.ndarray, width: int, height: int) -> np.ndarray:
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(image, (max(1, int(image.shape[1] * scale)), max(1, int(image.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    output = np.zeros((height, width, 3), dtype=np.uint8)
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    output[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return output


def _crop_focus(image: np.ndarray, bbox: list[float] | None, state: dict[str, float], aspect: float = 4 / 3) -> np.ndarray:
    height, width = image.shape[:2]
    if bbox is None:
        return image
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    crop_h = max((y2 - y1) * 1.45, 220.0)
    crop_w = crop_h * aspect
    if crop_w > width:
        crop_w = float(width)
        crop_h = crop_w / aspect
    if crop_h > height:
        crop_h = float(height)
        crop_w = crop_h * aspect
    alpha = 0.12
    for key, value in (("cx", cx), ("cy", cy), ("w", crop_w), ("h", crop_h)):
        state[key] = value if key not in state else (1 - alpha) * state[key] + alpha * value
    crop_w, crop_h = state["w"], state["h"]
    x1 = max(0, min(width - crop_w, state["cx"] - crop_w / 2))
    y1 = max(0, min(height - crop_h, state["cy"] - crop_h / 2))
    return image[int(y1):int(y1 + crop_h), int(x1):int(x1 + crop_w)]


def _active_event(events: list[dict[str, Any]], timestamp_ms: int) -> dict[str, Any] | None:
    active = [event for event in events if int(event["start_ms"]) <= timestamp_ms <= int(event["end_ms"])]
    if not active:
        return None
    order = {"FS01": 0, "FS02": 1, "FS09": 2}
    return sorted(active, key=lambda item: (order[item["event_code"]], item["start_ms"]))[-1]


def _selected_bbox(record: dict[str, Any], timeline_item: dict[str, Any] | None) -> list[float] | None:
    if timeline_item is None:
        return None
    track = timeline_item.get("source_track_id")
    detection = next((item for item in record.get("detections", []) if item.get("track_id") == track), None)
    return detection.get("bbox_px") if detection else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Render synchronized YOLO/RTMPose pose and scoring comparison video")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args()
    root = Path.cwd()
    source = root / "FULL-TEST" / f"{args.video_id}.mp4"
    capture = cv2.VideoCapture(str(source))
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if args.start_frame < 0:
        raise ValueError("--start-frame must be non-negative")
    capture.set(cv2.CAP_PROP_POS_FRAMES, args.start_frame)
    landscape = source_width / max(source_height, 1) >= 1.2
    cell_w, cell_h = 640, 480
    args.output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(args.output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (cell_w * 2, cell_h * 2))
    if not writer.isOpened():
        raise RuntimeError("could not initialize MP4 writer")
    report = json.loads((root / "reports" / "pose-scoring-ab" / "pose-scoring-ab.json").read_text(encoding="utf-8"))
    aggregate = {item["backend"]: item for item in report["models"]}
    data: dict[str, dict[str, Any]] = {}
    for backend in BACKENDS:
        frame_records = _load_jsonl(_frames_path(root, backend, args.video_id))
        by_index = {int(item["frame"]["processed_index"]): item for item in frame_records}
        score_dir = root / "reports" / "pose-scoring-ab" / backend / args.video_id
        timeline = {int(item["processed_index"]): item for item in _load_jsonl(score_dir / "primary-player.jsonl")}
        events = _load_jsonl(score_dir / "events.jsonl")
        scores = _load_jsonl(score_dir / "scores.jsonl")
        scores_by_event: dict[str, list[dict[str, Any]]] = {}
        for score in scores:
            scores_by_event.setdefault(score["event_id"], []).append(score)
        status_counts = aggregate[backend]["score_status_counts"]
        total = sum(status_counts.values())
        unavailable_rate = status_counts.get("unavailable", 0) / max(total, 1) * 100
        data[backend] = {
            "frames": by_index,
            "timeline": timeline,
            "events": events,
            "scores": scores_by_event,
            "unavailable_rate": unavailable_rate,
        }
    crop_state: dict[str, float] = {}
    rendered = 0
    source_index = args.start_frame
    while True:
        ok, source_frame = capture.read()
        if not ok or (args.max_frames is not None and rendered >= args.max_frames):
            break
        reference = data["yolo"]["frames"].get(source_index)
        if reference is None:
            break
        timestamp_ms = int(reference["frame"]["timestamp_ms"])
        reference_timeline = data["yolo"]["timeline"].get(source_index)
        bbox = _selected_bbox(reference, reference_timeline)
        cells = []
        for backend in BACKENDS:
            record = data[backend]["frames"].get(source_index, reference)
            timeline_item = data[backend]["timeline"].get(source_index)
            pose = _selected_pose(record, timeline_item)
            drawn, points = _draw_pose(source_frame, pose, COLORS[backend])
            visual = _crop_focus(drawn, bbox, crop_state) if landscape else drawn
            cell = _letterbox(visual, cell_w, cell_h)
            cv2.rectangle(cell, (0, 0), (cell_w, 86), (0, 0, 0), -1)
            cv2.putText(cell, LABELS[backend], (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.70, COLORS[backend], 2, cv2.LINE_AA)
            left_angle = _ankle_angle(points, "left")
            right_angle = _ankle_angle(points, "right")
            angle_text = (
                f"ankle 2D L={left_angle:.1f} R={right_angle:.1f} deg"
                if left_angle is not None and right_angle is not None
                else "ankle 2D: unavailable (no valid forefoot points)"
            )
            cv2.putText(cell, angle_text, (14, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255, 255, 0) if left_angle is not None else (150, 150, 255), 1, cv2.LINE_AA)
            cv2.putText(cell, f"overall unavailable={data[backend]['unavailable_rate']:.2f}%", (14, 77), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (235, 235, 235), 1, cv2.LINE_AA)
            event = _active_event(data[backend]["events"], timestamp_ms)
            if event:
                scores = data[backend]["scores"].get(event["event_id"], [])
                measured = sum(item["status"] == "calibration_required" for item in scores)
                event_text = f"{event['event_code']}  features valid {measured}/{len(scores)}  grade=--"
            else:
                event_text = "event=--  grade=--"
            cv2.rectangle(cell, (0, cell_h - 36), (cell_w, cell_h), (0, 0, 0), -1)
            cv2.putText(cell, event_text, (14, cell_h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (230, 230, 230), 1, cv2.LINE_AA)
            cells.append(cell)
        composite = np.vstack((np.hstack((cells[0], cells[1])), np.hstack((cells[2], cells[3]))))
        cv2.putText(composite, f"t={timestamp_ms / 1000:.2f}s", (1120, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
        writer.write(composite)
        rendered += 1
        source_index += 1
    capture.release()
    writer.release()
    check = cv2.VideoCapture(str(args.output))
    validated_frames = int(check.get(cv2.CAP_PROP_FRAME_COUNT))
    check.release()
    if validated_frames != rendered:
        raise RuntimeError(f"rendered frame count mismatch: expected {rendered}, got {validated_frames}")
    print(json.dumps({"output": str(args.output), "start_frame": args.start_frame, "frames": rendered, "fps": fps, "duration_seconds": round(rendered / fps, 3), "layout": "2x2_1280x960"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
