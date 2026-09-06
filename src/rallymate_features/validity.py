from __future__ import annotations

import numpy as np


DEFAULT_KEYPOINT_CONFIDENCE_MIN = 0.25


def valid_point_mask(
    xy: np.ndarray,
    confidence: np.ndarray,
    *,
    confidence_min: float = DEFAULT_KEYPOINT_CONFIDENCE_MIN,
) -> np.ndarray:
    coordinates = np.asarray(xy, dtype=np.float64)
    scores = np.asarray(confidence, dtype=np.float64)
    if coordinates.ndim != 2 or coordinates.shape[1] != 2:
        raise ValueError("xy must have shape [T, 2]")
    if scores.shape != (coordinates.shape[0],):
        raise ValueError("confidence must have shape [T]")
    return np.isfinite(coordinates).all(axis=1) & np.isfinite(scores) & (scores >= confidence_min)


def combined_valid_mask(*masks: np.ndarray) -> np.ndarray:
    if not masks:
        return np.asarray([], dtype=bool)
    result = np.asarray(masks[0], dtype=bool).copy()
    for mask in masks[1:]:
        current = np.asarray(mask, dtype=bool)
        if current.shape != result.shape:
            raise ValueError("validity masks must have matching shape")
        result &= current
    return result


def valid_fraction(mask: np.ndarray) -> float:
    values = np.asarray(mask, dtype=bool)
    return float(values.mean()) if values.size else 0.0


def longest_false_run(mask: np.ndarray, timestamp_ms: np.ndarray) -> tuple[int, int]:
    values = np.asarray(mask, dtype=bool)
    timestamps = np.asarray(timestamp_ms, dtype=np.int64)
    if values.shape != timestamps.shape:
        raise ValueError("mask and timestamp_ms must have matching shape")
    median_dt = int(np.median(np.diff(timestamps))) if timestamps.size > 1 else 0
    longest_count = current_count = 0
    longest_ms = 0
    current_start = 0
    for index, valid in enumerate(values):
        if valid:
            current_count = 0
            continue
        if current_count == 0:
            current_start = index
        current_count += 1
        if current_count > longest_count:
            longest_count = current_count
            longest_ms = int(timestamps[index] - timestamps[current_start] + median_dt)
    return longest_count, max(longest_ms, 0)
