from __future__ import annotations

import numpy as np


def angle_three_points_deg(
    first: np.ndarray,
    vertex: np.ndarray,
    third: np.ndarray,
) -> np.ndarray:
    a = np.asarray(first, dtype=np.float64) - np.asarray(vertex, dtype=np.float64)
    b = np.asarray(third, dtype=np.float64) - np.asarray(vertex, dtype=np.float64)
    denominator = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
    result = np.full(a.shape[0], np.nan)
    valid = np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1) & (denominator > 1e-12)
    cosine = np.full(a.shape[0], np.nan)
    cosine[valid] = np.clip(
        np.sum(a[valid] * b[valid], axis=1) / denominator[valid], -1.0, 1.0
    )
    result[valid] = np.degrees(np.arccos(cosine[valid]))
    return result


def orientation_deg(vector: np.ndarray) -> np.ndarray:
    values = np.asarray(vector, dtype=np.float64)
    result = np.degrees(np.arctan2(-values[:, 1], values[:, 0]))
    result[~np.isfinite(values).all(axis=1)] = np.nan
    return result


def wrap_axis_angle_deg(angle: np.ndarray) -> np.ndarray:
    values = np.asarray(angle, dtype=np.float64)
    return ((values + 90.0) % 180.0) - 90.0


def unwrap_axis_angle_deg(angle: np.ndarray) -> np.ndarray:
    values = np.asarray(angle, dtype=np.float64)
    result = np.full(values.shape, np.nan)
    finite = np.flatnonzero(np.isfinite(values))
    if finite.size:
        result[finite] = np.degrees(
            np.unwrap(np.radians(values[finite] * 2.0))
        ) / 2.0
    return result
