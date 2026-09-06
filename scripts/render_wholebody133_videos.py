from __future__ import annotations

import argparse
import json
import math
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
)


DEFAULT_VIDEO_ID = "850cb0006b406c7176eeda8d711cd065"
DEFAULT_START_FRAME = 930
DEFAULT_MAX_FRAMES = 600
HALPE_BACKEND = "rtmpose-m-halpe26-256x192"
WHOLEBODY_DIR = "pose-wholebody133"
GROUP_RANGES = {
    "body": (0, 17),
    "foot": (17, 23),
    "face": (23, 91),
    "left_hand": (91, 112),
    "right_hand": (112, 133),
}
GROUP_LABELS = {
    "body": "BODY",
    "foot": "FOOT",
    "face": "FACE",
    "left_hand": "LEFT HAND",
    "right_hand": "RIGHT HAND",
}
GROUP_COLORS = {
    "body": (255, 220, 90),
    "foot": (0, 225, 255),
    "face": (255, 70, 235),
    "left_hand": (80, 245, 115),
    "right_hand": (80, 165, 255),
}
HALPE_CENTER_COLOR = (180, 120, 255)
BODY_CONNECTIONS = (
    (0, 1),
    (0, 2),
    (1, 3),
    (2, 4),
    (5, 6),
    (5, 7),
    (7, 9),
    (6, 8),
    (8, 10),
    (5, 11),
    (6, 12),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
)
WHOLEBODY_FOOT_CONNECTIONS = (
    (15, 17),
    (15, 18),
    (15, 19),
    (16, 20),
    (16, 21),
    (16, 22),
)
FACE_CHAINS = (
    (range(23, 40), False),
    (range(40, 45), False),
    (range(45, 50), False),
    (range(50, 54), False),
    (range(54, 59), False),
    (range(59, 65), True),
    (range(65, 71), True),
    (range(71, 83), True),
    (range(83, 91), True),
)


def _valid(point: dict[str, Any] | None, threshold: float) -> bool:
    return (
        point is not None
        and point.get("in_frame") is not False
        and float(point.get("confidence", 0.0)) >= threshold
    )


def _xy(point: dict[str, Any]) -> tuple[float, float]:
    return float(point["x_px"]), float(point["y_px"])


def _point_map(pose: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        point["name"]: point for point in pose.get("keypoints", [])
    } if pose else {}


def _group_counts(
    pose: dict[str, Any] | None, threshold: float
) -> dict[str, tuple[int, int]]:
    points = pose.get("keypoints", []) if pose else []
    return {
        group: (
            sum(_valid(point, threshold) for point in points[start:end]),
            end - start,
        )
        for group, (start, end) in GROUP_RANGES.items()
    }


def _body_box(
    bbox: list[float] | None,
    frame_shape: tuple[int, int, int],
    state: dict[str, float],
    prefix: str,
    aspect: float,
) -> tuple[float, float, float, float]:
    frame_h, frame_w = frame_shape[:2]
    if bbox is None:
        return 0.0, 0.0, float(frame_w), float(frame_h)
    x1, y1, x2, y2 = (float(value) for value in bbox)
    height = max(260.0, y2 - y1)
    crop_h = min(float(frame_h), height * 1.32)
    crop_w = min(float(frame_w), crop_h * aspect)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    return _smooth_box(
        (cx - crop_w / 2.0, cy - crop_h / 2.0, cx + crop_w / 2.0, cy + crop_h / 2.0),
        state,
        prefix,
        alpha=0.16,
    )


def _detail_box(
    points: list[dict[str, Any]],
    threshold: float,
    fallback: tuple[float, float],
    *,
    output_aspect: float,
    minimum_size: float,
    state: dict[str, float],
    prefix: str,
) -> tuple[float, float, float, float]:
    coordinates = np.asarray(
        [_xy(point) for point in points if _valid(point, threshold)],
        dtype=np.float64,
    )
    if coordinates.size:
        low = coordinates.min(axis=0)
        high = coordinates.max(axis=0)
        center = (low + high) / 2.0
        width = max(minimum_size, float(high[0] - low[0]) * 1.65)
        height = max(minimum_size, float(high[1] - low[1]) * 1.65)
    else:
        center = np.asarray(fallback, dtype=np.float64)
        width = height = minimum_size
    if width / height < output_aspect:
        width = height * output_aspect
    else:
        height = width / output_aspect
    box = (
        float(center[0] - width / 2.0),
        float(center[1] - height / 2.0),
        float(center[0] + width / 2.0),
        float(center[1] + height / 2.0),
    )
    return _smooth_box(box, state, prefix, alpha=0.22)


def _draw_connection(
    canvas: np.ndarray,
    points: list[dict[str, Any]],
    mapper: Callable[[tuple[float, float]], tuple[int, int]],
    first: int,
    second: int,
    color: tuple[int, int, int],
    threshold: float,
    thickness: int,
) -> None:
    if first >= len(points) or second >= len(points):
        return
    a, b = points[first], points[second]
    if _valid(a, threshold) and _valid(b, threshold):
        cv2.line(canvas, mapper(_xy(a)), mapper(_xy(b)), color, thickness, cv2.LINE_AA)


def _draw_chain(
    canvas: np.ndarray,
    points: list[dict[str, Any]],
    mapper: Callable[[tuple[float, float]], tuple[int, int]],
    indexes: list[int],
    color: tuple[int, int, int],
    threshold: float,
    closed: bool,
) -> None:
    for first, second in zip(indexes, indexes[1:]):
        _draw_connection(canvas, points, mapper, first, second, color, threshold, 1)
    if closed and len(indexes) > 2:
        _draw_connection(canvas, points, mapper, indexes[-1], indexes[0], color, threshold, 1)


def _hand_connections(root: int) -> list[tuple[int, int]]:
    result = []
    for offset in (1, 5, 9, 13, 17):
        result.append((root, root + offset))
        result.extend(
            (root + value, root + value + 1)
            for value in range(offset, offset + 3)
        )
    return result


def _draw_wholebody(
    canvas: np.ndarray,
    pose: dict[str, Any] | None,
    mapper: Callable[[tuple[float, float]], tuple[int, int]],
    threshold: float,
    *,
    groups: set[str] | None = None,
    detail: bool = False,
) -> None:
    points = pose.get("keypoints", []) if pose else []
    if len(points) != 133:
        return
    selected = groups or set(GROUP_RANGES)
    if "body" in selected:
        for first, second in BODY_CONNECTIONS:
            _draw_connection(canvas, points, mapper, first, second, GROUP_COLORS["body"], threshold, 3)
    if "foot" in selected:
        for first, second in WHOLEBODY_FOOT_CONNECTIONS:
            _draw_connection(canvas, points, mapper, first, second, GROUP_COLORS["foot"], threshold, 2)
    if "face" in selected:
        for chain, closed in FACE_CHAINS:
            _draw_chain(canvas, points, mapper, list(chain), GROUP_COLORS["face"], threshold, closed)
    for group, root in (("left_hand", 91), ("right_hand", 112)):
        if group in selected:
            for first, second in _hand_connections(root):
                _draw_connection(canvas, points, mapper, first, second, GROUP_COLORS[group], threshold, 2)
    for group, (start, end) in GROUP_RANGES.items():
        if group not in selected:
            continue
        radius = (
            5 if detail and group in {"left_hand", "right_hand", "foot"}
            else 4 if detail
            else 4 if group in {"body", "foot"}
            else 2
        )
        for point in points[start:end]:
            if _valid(point, threshold):
                mapped = mapper(_xy(point))
                cv2.circle(canvas, mapped, radius + 2, (8, 8, 8), -1, cv2.LINE_AA)
                cv2.circle(canvas, mapped, radius, GROUP_COLORS[group], -1, cv2.LINE_AA)


def _draw_named_pose(
    canvas: np.ndarray,
    pose: dict[str, Any] | None,
    mapper: Callable[[tuple[float, float]], tuple[int, int]],
    threshold: float,
) -> None:
    point_map = _point_map(pose)
    body_names = (
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
    for first, second in body_names:
        a, b = point_map.get(first), point_map.get(second)
        if _valid(a, threshold) and _valid(b, threshold):
            cv2.line(canvas, mapper(_xy(a)), mapper(_xy(b)), GROUP_COLORS["body"], 3, cv2.LINE_AA)
    foot_names = (
        ("left_ankle", "left_big_toe"),
        ("left_ankle", "left_small_toe"),
        ("left_ankle", "left_heel"),
        ("right_ankle", "right_big_toe"),
        ("right_ankle", "right_small_toe"),
        ("right_ankle", "right_heel"),
    )
    for first, second in foot_names:
        a, b = point_map.get(first), point_map.get(second)
        if _valid(a, threshold) and _valid(b, threshold):
            cv2.line(canvas, mapper(_xy(a)), mapper(_xy(b)), GROUP_COLORS["foot"], 2, cv2.LINE_AA)
    for point in point_map.values():
        if not _valid(point, threshold):
            continue
        name = point["name"]
        if any(token in name for token in ("toe", "heel")):
            color, radius = GROUP_COLORS["foot"], 5
        elif name in {"head", "neck", "hip"}:
            color, radius = HALPE_CENTER_COLOR, 5
        else:
            color, radius = GROUP_COLORS["body"], 4
        mapped = mapper(_xy(point))
        cv2.circle(canvas, mapped, radius + 2, (8, 8, 8), -1, cv2.LINE_AA)
        cv2.circle(canvas, mapped, radius, color, -1, cv2.LINE_AA)


def _header(
    canvas: np.ndarray,
    title: str,
    lines: list[str],
    timestamp_ms: int,
    color: tuple[int, int, int],
) -> None:
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 105), (14, 17, 22), -1)
    _put_text(canvas, title, (16, 31), 0.66, color, 2)
    _put_text(canvas, lines[0], (16, 59), 0.43, (240, 240, 240), 1)
    if len(lines) > 1:
        _put_text(canvas, lines[1], (16, 83), 0.40, (220, 220, 220), 1)
    _put_text(canvas, f"t={timestamp_ms / 1000.0:.2f}s", (canvas.shape[1] - 110, 31), 0.43, (255, 255, 255), 1)


def _wholebody_lines(counts: dict[str, tuple[int, int]]) -> list[str]:
    return [
        "  ".join(
            f"{GROUP_LABELS[group]} {counts[group][0]}/{counts[group][1]}"
            for group in ("body", "foot", "face")
        ),
        "  ".join(
            f"{GROUP_LABELS[group]} {counts[group][0]}/{counts[group][1]}"
            for group in ("left_hand", "right_hand")
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render real COCO-WholeBody133 outputs and a synchronized 17/26/133 comparison"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--video-id", default=DEFAULT_VIDEO_ID)
    parser.add_argument("--start-frame", type=int, default=DEFAULT_START_FRAME)
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)
    parser.add_argument("--confidence-threshold", type=float, default=0.25)
    parser.add_argument("--wholebody-output", type=Path, required=True)
    parser.add_argument("--comparison-output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    args = parser.parse_args()
    if args.start_frame < 0 or args.max_frames <= 0:
        raise ValueError("invalid frame interval")

    root = args.root.resolve()
    video_id = args.video_id
    wholebody_artifact_dir = (
        root
        / "runs"
        / WHOLEBODY_DIR
        / f"{video_id}-{args.start_frame}-{args.start_frame + args.max_frames}"
    )
    paths = {
        "yolo": root / "runs" / "full-test" / video_id / "frames.jsonl",
        "halpe26": root / "runs" / "pose-ab" / HALPE_BACKEND / video_id / "frames.jsonl",
        "wholebody133": wholebody_artifact_dir / "frames.jsonl",
    }
    records = {
        backend: {
            int(item["frame"]["processed_index"]): item
            for item in _load_jsonl(path)
        }
        for backend, path in paths.items()
    }
    timeline_path = (
        root
        / "reports"
        / "pose-scoring-ab"
        / HALPE_BACKEND
        / video_id
        / "primary-player.jsonl"
    )
    timeline = {
        int(item["processed_index"]): item for item in _load_jsonl(timeline_path)
    }
    source = root / "FULL-TEST" / f"{video_id}.mp4"
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise FileNotFoundError(source)
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    capture.set(cv2.CAP_PROP_POS_FRAMES, args.start_frame)

    for path in (args.wholebody_output, args.comparison_output, args.metadata_output):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite output: {path}")
    wholebody_writer = cv2.VideoWriter(
        str(args.wholebody_output),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (1280, 960),
    )
    comparison_writer = cv2.VideoWriter(
        str(args.comparison_output),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (1920, 960),
    )
    if not wholebody_writer.isOpened() or not comparison_writer.isOpened():
        raise RuntimeError("could not initialize MP4 writers")

    crop_state: dict[str, float] = {}
    rendered = 0
    validity_totals = {
        group: 0 for group in GROUP_RANGES
    }
    source_index = args.start_frame
    try:
        while rendered < args.max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            frame_records = {
                backend: values.get(source_index)
                for backend, values in records.items()
            }
            if any(value is None for value in frame_records.values()):
                break
            timeline_item = timeline.get(source_index)
            poses = {
                backend: _selected_pose(frame_records[backend], timeline_item)
                for backend in frame_records
            }
            reference_bbox = _selected_bbox(frame_records["halpe26"], timeline_item)
            if reference_bbox is None:
                reference_bbox = _selected_bbox(frame_records["yolo"], timeline_item)
            timestamp_ms = int(frame_records["wholebody133"]["frame"]["timestamp_ms"])
            counts = _group_counts(poses["wholebody133"], args.confidence_threshold)
            for group, (valid_count, _) in counts.items():
                validity_totals[group] += valid_count

            # WholeBody133 showcase: full-body pane plus face, hands and feet zooms.
            showcase = np.full((960, 1280, 3), (12, 14, 18), dtype=np.uint8)
            full_box = _body_box(reference_bbox, frame.shape, crop_state, "showcase_body", 800 / 960)
            full_pane, full_mapper = _render_crop(frame, full_box, 800, 960)
            _draw_wholebody(full_pane, poses["wholebody133"], full_mapper, args.confidence_threshold)
            _header(
                full_pane,
                "RTMPose-M | COCO-WHOLEBODY 133 | REAL OUTPUT",
                _wholebody_lines(counts),
                timestamp_ms,
                GROUP_COLORS["face"],
            )
            _put_text(full_pane, "ALL VALID POINTS ON FULL BODY", (18, 938), 0.45, (245, 245, 245), 1)
            showcase[:, :800] = full_pane

            wb_points = poses["wholebody133"].get("keypoints", []) if poses["wholebody133"] else []
            bbox = reference_bbox or [0.0, 0.0, float(frame.shape[1]), float(frame.shape[0])]
            x1, y1, x2, y2 = (float(value) for value in bbox)
            fallback = {
                "face": ((x1 + x2) / 2.0, y1 + 0.16 * (y2 - y1)),
                "left_hand": (x1 + 0.20 * (x2 - x1), y1 + 0.55 * (y2 - y1)),
                "right_hand": (x1 + 0.80 * (x2 - x1), y1 + 0.55 * (y2 - y1)),
                "foot": ((x1 + x2) / 2.0, y1 + 0.90 * (y2 - y1)),
            }
            detail_specs = (
                ("face", 800, 0, 480, 280, {"face"}),
                ("left_hand", 800, 280, 240, 280, {"left_hand"}),
                ("right_hand", 1040, 280, 240, 280, {"right_hand"}),
                ("foot", 800, 560, 480, 240, {"foot", "body"}),
            )
            for group, dx, dy, dw, dh, selected_groups in detail_specs:
                start, end = GROUP_RANGES[group]
                selected_points = wb_points[start:end]
                if group == "foot":
                    selected_points = wb_points[13:23]
                detail_box = _detail_box(
                    selected_points,
                    args.confidence_threshold,
                    fallback[group],
                    output_aspect=dw / dh,
                    minimum_size=max(70.0, (y2 - y1) * (0.24 if group == "face" else 0.30)),
                    state=crop_state,
                    prefix=f"showcase_{group}",
                )
                pane, mapper = _render_crop(frame, detail_box, dw, dh)
                _draw_wholebody(
                    pane,
                    poses["wholebody133"],
                    mapper,
                    args.confidence_threshold,
                    groups=selected_groups,
                    detail=True,
                )
                cv2.rectangle(pane, (0, 0), (dw, 34), (10, 12, 16), -1)
                if group == "foot":
                    valid_count = counts["foot"][0]
                    total = counts["foot"][1]
                else:
                    valid_count, total = counts[group]
                _put_text(
                    pane,
                    f"{GROUP_LABELS[group]} {valid_count}/{total} VALID",
                    (10, 24),
                    0.48 if dw >= 400 else 0.38,
                    GROUP_COLORS[group],
                    1,
                )
                showcase[dy:dy + dh, dx:dx + dw] = pane
            cv2.rectangle(showcase, (800, 800), (1279, 959), (18, 21, 27), -1)
            _put_text(showcase, "GROUP COLORS", (816, 829), 0.50, (245, 245, 245), 1)
            y = 854
            for group in GROUP_RANGES:
                valid_count, total = counts[group]
                _put_text(
                    showcase,
                    f"{GROUP_LABELS[group]:<11} {valid_count:>2}/{total:<3}",
                    (816 + (240 if y > 910 else 0), y if y <= 910 else y - 57),
                    0.41,
                    GROUP_COLORS[group],
                    1,
                )
                y += 28
            _put_text(showcase, "FILTER >= 0.25 | COVERAGE != ACCURACY", (816, 943), 0.38, (150, 195, 255), 1)
            wholebody_writer.write(showcase)

            # Same source frame, same selected player and identical crop across all models.
            comparison = np.full((960, 1920, 3), (12, 14, 18), dtype=np.uint8)
            compare_box = _body_box(reference_bbox, frame.shape, crop_state, "comparison_body", 640 / 960)
            for column, backend in enumerate(("yolo", "halpe26", "wholebody133")):
                pane, mapper = _render_crop(frame, compare_box, 640, 960)
                if backend == "wholebody133":
                    _draw_wholebody(pane, poses[backend], mapper, args.confidence_threshold)
                    title = "RTMPose-M | WHOLEBODY-133"
                    lines = _wholebody_lines(counts)
                    color = GROUP_COLORS["face"]
                else:
                    _draw_named_pose(pane, poses[backend], mapper, args.confidence_threshold)
                    point_map = _point_map(poses[backend])
                    body_valid = sum(
                        _valid(point_map.get(name), args.confidence_threshold)
                        for name in (
                            "nose", "left_eye", "right_eye", "left_ear", "right_ear",
                            "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
                            "left_wrist", "right_wrist", "left_hip", "right_hip",
                            "left_knee", "right_knee", "left_ankle", "right_ankle",
                        )
                    )
                    if backend == "yolo":
                        title = "YOLO POSE | COCO-17"
                        lines = [f"BODY {body_valid}/17", "FOOT 0/6  FACE 0/68  HANDS 0/42"]
                        color = (235, 90, 255)
                    else:
                        foot_valid = sum(
                            _valid(point_map.get(name), args.confidence_threshold)
                            for name in (
                                "left_big_toe", "left_small_toe", "left_heel",
                                "right_big_toe", "right_small_toe", "right_heel",
                            )
                        )
                        center_valid = sum(
                            _valid(point_map.get(name), args.confidence_threshold)
                            for name in ("head", "neck", "hip")
                        )
                        title = "RTMPose-M | HALPE-26"
                        lines = [
                            f"BODY {body_valid}/17  CENTER {center_valid}/3  FOOT {foot_valid}/6",
                            "FACE 0/68  HANDS 0/42",
                        ]
                        color = (80, 190, 255)
                _header(pane, title, lines, timestamp_ms, color)
                _put_text(pane, "SAME VIDEO | SAME FRAME | SAME PLAYER ROI", (18, 938), 0.39, (240, 240, 240), 1)
                comparison[:, column * 640:(column + 1) * 640] = pane
            cv2.line(comparison, (640, 0), (640, 960), (235, 235, 235), 2, cv2.LINE_AA)
            cv2.line(comparison, (1280, 0), (1280, 960), (235, 235, 235), 2, cv2.LINE_AA)
            comparison_writer.write(comparison)

            rendered += 1
            source_index += 1
            if rendered % 100 == 0:
                print(json.dumps({"rendered": rendered}), flush=True)
    finally:
        capture.release()
        wholebody_writer.release()
        comparison_writer.release()

    if rendered <= 0:
        raise RuntimeError("no frames rendered")
    validation = {}
    for name, path, expected_resolution in (
        ("wholebody133", args.wholebody_output, [1280, 960]),
        ("comparison", args.comparison_output, [1920, 960]),
    ):
        check = cv2.VideoCapture(str(path))
        validation[name] = {
            "path": str(path.resolve()),
            "frames": int(check.get(cv2.CAP_PROP_FRAME_COUNT)),
            "fps": float(check.get(cv2.CAP_PROP_FPS)),
            "resolution": [
                int(check.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(check.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            ],
            "expected_resolution": expected_resolution,
        }
        check.release()
        if validation[name]["frames"] != rendered or validation[name]["resolution"] != expected_resolution:
            raise RuntimeError(f"video validation failed for {name}: {validation[name]}")
    pose_frames = sum(
        _selected_pose(records["wholebody133"].get(index), timeline.get(index)) is not None
        for index in range(args.start_frame, args.start_frame + rendered)
    )
    metadata = {
        "schema_version": "1.0.0",
        "renderer_version": "wholebody133-videos/1.0.0",
        "video_id": video_id,
        "start_processed_frame": args.start_frame,
        "end_processed_frame_exclusive": args.start_frame + rendered,
        "frames": rendered,
        "fps": fps,
        "duration_seconds": round(rendered / fps, 3),
        "confidence_threshold": args.confidence_threshold,
        "invalid_points_drawn": False,
        "real_inference_artifact": str(
            (wholebody_artifact_dir / "inference-metadata.json").resolve()
        ),
        "source_artifacts": {key: str(value.resolve()) for key, value in paths.items()},
        "wholebody_validity": {
            group: {
                "valid_points": validity_totals[group],
                "eligible_points": pose_frames * (end - start),
                "valid_rate": round(
                    validity_totals[group] / max(pose_frames * (end - start), 1), 6
                ),
            }
            for group, (start, end) in GROUP_RANGES.items()
        },
        "validation": validation,
        "claims": {
            "outputs_are_real_model_points": True,
            "coverage_is_accuracy": False,
            "scoring_promoted": False,
        },
    }
    args.metadata_output.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
