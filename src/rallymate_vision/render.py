from __future__ import annotations

import cv2
import numpy as np


COLORS = {
    "player": (69, 181, 84),
    "ball": (40, 220, 255),
    "racket": (255, 140, 40),
}

POSE_CONNECTIONS = [
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
]


def annotate_frame(
    frame: np.ndarray,
    detections: list[dict],
    poses: list[dict],
    court: dict,
    frame_index: int,
    timestamp_ms: int,
    quality: dict,
) -> np.ndarray:
    canvas = frame.copy()
    court_is_usable = court.get("status") in {"detected", "calibrated"}
    if court_is_usable:
        for segment in court.get("line_segments_px", []):
            x1, y1, x2, y2 = [int(value) for value in segment]
            cv2.line(
                canvas,
                (x1, y1),
                (x2, y2),
                (180, 180, 180),
                1,
                cv2.LINE_AA,
            )
    polygon = court.get("polygon_px", [])
    if len(polygon) >= 4 and court_is_usable:
        points = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(
            canvas,
            [points],
            True,
            (170, 80, 255),
            2,
            cv2.LINE_AA,
        )

    for detection in detections:
        x1, y1, x2, y2 = [int(value) for value in detection["bbox_px"]]
        color = COLORS.get(detection["class_name"], (255, 255, 255))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        label = (
            f'{detection["class_name"]} #{detection["track_id"]} '
            f'{detection["confidence"]:.2f}'
        )
        cv2.putText(
            canvas,
            label,
            (x1, max(18, y1 - 7)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )

    for pose in poses:
        points = pose["keypoints"]
        for first, second in POSE_CONNECTIONS:
            if (
                first >= len(points)
                or second >= len(points)
                or points[first]["confidence"] < 0.2
                or points[second]["confidence"] < 0.2
            ):
                continue
            point_a = (int(points[first]["x_px"]), int(points[first]["y_px"]))
            point_b = (int(points[second]["x_px"]), int(points[second]["y_px"]))
            cv2.line(canvas, point_a, point_b, (255, 90, 210), 2, cv2.LINE_AA)
        for point in points:
            if point["confidence"] < 0.2:
                continue
            cv2.circle(
                canvas,
                (int(point["x_px"]), int(point["y_px"])),
                3,
                (255, 255, 255),
                -1,
                cv2.LINE_AA,
            )

    quality_color = (0, 220, 0) if quality["status"] == "ok" else (0, 180, 255)
    status_text = (
        f"frame={frame_index} t={timestamp_ms / 1000:.2f}s "
        f"quality={quality['status']} court={court.get('status', 'unknown')}"
    )
    cv2.rectangle(canvas, (0, 0), (min(canvas.shape[1], 760), 38), (0, 0, 0), -1)
    cv2.putText(
        canvas,
        status_text,
        (12, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        quality_color,
        2,
        cv2.LINE_AA,
    )
    return canvas
