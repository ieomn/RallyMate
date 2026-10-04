from __future__ import annotations

import numpy as np


def irregular_derivative(
    timestamp_ms: np.ndarray,
    values: np.ndarray,
) -> np.ndarray:
    timestamps = np.asarray(timestamp_ms, dtype=np.float64) / 1000.0
    array = np.asarray(values, dtype=np.float64)
    if array.shape[0] != timestamps.size:
        raise ValueError("values first dimension must match timestamp_ms")
    output = np.full_like(array, np.nan, dtype=np.float64)
    if timestamps.size < 2:
        return output
    flat_in = array.reshape(array.shape[0], -1)
    flat_out = output.reshape(output.shape[0], -1)
    for column in range(flat_in.shape[1]):
        series = flat_in[:, column]
        finite_indexes = np.flatnonzero(np.isfinite(series))
        if finite_indexes.size < 2:
            continue
        # Keep exactly the previous finite-neighbour stencil, including its
        # one-sided endpoints and per-column gaps. Batch the arithmetic rather
        # than revisiting every frame in Python for every event feature.
        left = np.concatenate((finite_indexes[:1], finite_indexes[:-1]))
        right = np.concatenate((finite_indexes[1:], finite_indexes[-1:]))
        dt = timestamps[right] - timestamps[left]
        valid = (right != left) & (dt > 0)
        flat_out[finite_indexes[valid], column] = (
            series[right[valid]] - series[left[valid]]
        ) / dt[valid]
    return output


def speed(timestamp_ms: np.ndarray, positions: np.ndarray) -> np.ndarray:
    velocity = irregular_derivative(timestamp_ms, positions)
    return np.linalg.norm(velocity, axis=1)


def scalar_acceleration(timestamp_ms: np.ndarray, speed_values: np.ndarray) -> np.ndarray:
    return irregular_derivative(timestamp_ms, np.asarray(speed_values)[:, None])[:, 0]
