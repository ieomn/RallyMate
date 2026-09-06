from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def box_iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def box_intersection_over_min_area(
    a: list[float],
    b: list[float],
) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    smaller_area = min(area_a, area_b)
    return intersection / smaller_area if smaller_area > 0 else 0.0


def box_center(box: list[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def normalized_center_distance(
    a: list[float], b: list[float], frame_width: int, frame_height: int
) -> float:
    acx, acy = box_center(a)
    bcx, bcy = box_center(b)
    diagonal = math.hypot(frame_width, frame_height)
    return math.hypot(acx - bcx, acy - bcy) / max(diagonal, 1.0)


def normalize_box(box: list[float], width: int, height: int) -> list[float]:
    return [
        clamp(box[0] / width, 0.0, 1.0),
        clamp(box[1] / height, 0.0, 1.0),
        clamp(box[2] / width, 0.0, 1.0),
        clamp(box[3] / height, 0.0, 1.0),
    ]


def expand_box(
    box: list[float],
    width: int,
    height: int,
    margin: float = 0.15,
) -> list[int]:
    x1, y1, x2, y2 = box
    box_width = x2 - x1
    box_height = y2 - y1
    return [
        int(clamp(x1 - box_width * margin, 0, width - 1)),
        int(clamp(y1 - box_height * margin, 0, height - 1)),
        int(clamp(x2 + box_width * margin, 1, width)),
        int(clamp(y2 + box_height * margin, 1, height)),
    ]


def resolve_device(requested: str) -> str:
    requested = requested.strip().lower()
    if requested != "auto":
        return requested
    try:
        import torch

        return "0" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def safe_float(value: Any, digits: int = 6) -> float:
    return round(float(value), digits)
