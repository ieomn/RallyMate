from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from rallymate_vision.utils import safe_float


@dataclass
class VideoMetadata:
    width: int
    height: int
    fps: float
    frame_count: int
    duration_ms: int


def probe_video(path: Path) -> VideoMetadata:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open video: {path}")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    if width <= 0 or height <= 0 or fps <= 0 or frame_count <= 0:
        raise ValueError(
            f"invalid video metadata: {width}x{height}, fps={fps}, frames={frame_count}"
        )
    return VideoMetadata(
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
        duration_ms=int(frame_count / fps * 1000),
    )


def analyze_frame(frame: np.ndarray) -> dict:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    contrast = float(gray.std())
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    warnings: list[str] = []
    if brightness < 45:
        warnings.append("too_dark")
    elif brightness > 220:
        warnings.append("overexposed")
    if contrast < 25:
        warnings.append("low_contrast")
    if blur_score < 45:
        warnings.append("blurry")
    return {
        "brightness": safe_float(brightness, 3),
        "contrast": safe_float(contrast, 3),
        "blur_score": safe_float(blur_score, 3),
        "status": "warning" if warnings else "ok",
        "warnings": warnings,
    }

def metadata_warnings(metadata: VideoMetadata) -> list[str]:
    warnings: list[str] = []
    if metadata.width < 1280 or metadata.height < 720:
        warnings.append("resolution_below_720p")
    if metadata.fps < 24:
        warnings.append("frame_rate_below_24fps")
    if metadata.fps < 50:
        warnings.append("frame_rate_below_recommended_50fps_for_ball_tracking")
    return warnings


def aggregate_quality(samples: list[dict], metadata: VideoMetadata) -> dict:
    if not samples:
        return {
            "status": "failed",
            "metadata_warnings": metadata_warnings(metadata),
            "sample_count": 0,
        }
    brightness = [sample["brightness"] for sample in samples]
    contrast = [sample["contrast"] for sample in samples]
    blur = [sample["blur_score"] for sample in samples]
    warning_counts: dict[str, int] = {}
    for sample in samples:
        for warning in sample["warnings"]:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
    warnings = metadata_warnings(metadata)
    warnings.extend(
        name
        for name, count in warning_counts.items()
        if count / len(samples) >= 0.25
    )
    return {
        "status": "warning" if warnings else "ok",
        "metadata_warnings": warnings,
        "sample_count": len(samples),
        "brightness_median": safe_float(float(np.median(brightness)), 3),
        "contrast_median": safe_float(float(np.median(contrast)), 3),
        "blur_score_median": safe_float(float(np.median(blur)), 3),
    }
