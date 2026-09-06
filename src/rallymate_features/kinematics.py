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
        for position, index in enumerate(finite_indexes):
            if position == 0:
                left, right = index, finite_indexes[position + 1] if len(finite_indexes) > 1 else index
            elif position == len(finite_indexes) - 1:
                left, right = finite_indexes[position - 1], index
            else:
                left, right = finite_indexes[position - 1], finite_indexes[position + 1]
            dt = timestamps[right] - timestamps[left]
            if right != left and dt > 0:
                flat_out[index, column] = (series[right] - series[left]) / dt
    return output


def speed(timestamp_ms: np.ndarray, positions: np.ndarray) -> np.ndarray:
    velocity = irregular_derivative(timestamp_ms, positions)
    return np.linalg.norm(velocity, axis=1)


def scalar_acceleration(timestamp_ms: np.ndarray, speed_values: np.ndarray) -> np.ndarray:
    return irregular_derivative(timestamp_ms, np.asarray(speed_values)[:, None])[:, 0]
