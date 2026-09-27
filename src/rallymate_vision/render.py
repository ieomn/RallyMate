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
    *,
    show_detections: bool = False,
    show_pose: bool = False,
    show_status: bool = False,
) -> np.ndarray:
    """Return a clean replay frame unless diagnostic overlays are requested.

    Overlay choices affect only rendered pixels. Detections, poses and scoring
    evidence are preserved by the inference pipeline regardless of this view.
    """
    canvas = frame.copy()
    for detection in detections if show_detections else []:
        # ``player`` boxes are inference plumbing for the pose ROI.  Showing
        # them in the exported video makes the result look like a YOLO pose
        # render and obscures the RTMPose skeleton the user is evaluating.
        if detection.get("class_name") == "player" or detection.get("confidence", 0) < 0.6:
            continue
        color = COLORS.get(detection["class_name"], (255, 255, 255))
        center = detection.get("center_px")
        if not isinstance(center, list) or len(center) != 2:
            continue
        cx, cy = int(center[0]), int(center[1])
        cv2.circle(canvas, (cx, cy), 5, color, -1, cv2.LINE_AA)
        label = f'{detection["class_name"]} #{detection["track_id"]}'
        cv2.putText(
            canvas,
            label,
            (cx + 8, max(18, cy - 7)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )

    for pose in poses if show_pose else []:
        points = pose["keypoints"]
        for first, second in POSE_CONNECTIONS:
            if (
                first >= len(points)
                or second >= len(points)
                or points[first]["confidence"] < 0.5
                or points[second]["confidence"] < 0.5
            ):
                continue
            point_a = (int(points[first]["x_px"]), int(points[first]["y_px"]))
            point_b = (int(points[second]["x_px"]), int(points[second]["y_px"]))
            cv2.line(canvas, point_a, point_b, (255, 90, 210), 2, cv2.LINE_AA)
        for point in points:
            if point["confidence"] < 0.5:
                continue
            cv2.circle(
                canvas,
                (int(point["x_px"]), int(point["y_px"])),
                3,
                (255, 255, 255),
                -1,
                cv2.LINE_AA,
            )

    if not show_status:
        return canvas

    quality_color = (0, 220, 0) if quality["status"] == "ok" else (0, 180, 255)
    status_text = (
        f"frame={frame_index} t={timestamp_ms / 1000:.2f}s "
        f"quality={quality['status']} pose=RTMPose"
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
