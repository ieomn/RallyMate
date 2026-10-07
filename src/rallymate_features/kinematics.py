from __future__ import annotations

import numpy as np

from rallymate_features.temporal import MAX_CONTIGUOUS_GAP_MS


CONTIGUOUS_DERIVATIVE_VERSION = "contiguous-irregular-derivative-candidate-v1.0.0"


def contiguous_irregular_derivative(
    timestamp_ms: np.ndarray,
    values: np.ndarray,
    *,
    max_gap_ms: int = MAX_CONTIGUOUS_GAP_MS,
) -> np.ndarray:
    """Opt-in candidate derivative within finite, temporally adjacent runs.

    Keep the legacy secant stencil on each continuous run, with one-sided
    endpoints. A missing value or an interval beyond the existing 160 ms
    observation-continuity bound starts a new run; isolated observations have
    no derivative. This does not interpolate points or establish their truth.
    The legacy production primitive below deliberately remains unchanged until
    this candidate has a separately published feature/event registry contract.
    """
    timestamps = np.asarray(timestamp_ms, dtype=np.float64)
    array = np.asarray(values, dtype=np.float64)
    if (timestamps.ndim != 1 or not np.isfinite(timestamps).all()
            or np.any(np.diff(timestamps) <= 0)
            or array.ndim < 1 or array.shape[0] != timestamps.size
            or isinstance(max_gap_ms, bool) or not isinstance(max_gap_ms, (int, float))
            or not np.isfinite(max_gap_ms) or max_gap_ms <= 0):
        raise ValueError("derivative requires finite ordered times, aligned values and a positive gap limit")
    output = np.full_like(array, np.nan, dtype=np.float64)
    if timestamps.size < 2 or array.size == 0:
        return output
    flat_in = array.reshape(array.shape[0], -1)
    flat_out = output.reshape(output.shape[0], -1)
    seconds = timestamps / 1000.0
    for column in range(flat_in.shape[1]):
        series = flat_in[:, column]
        indexes = np.flatnonzero(np.isfinite(series))
        if indexes.size < 2:
            continue
        adjacent = (np.diff(indexes) == 1) & (np.diff(timestamps[indexes]) <= max_gap_ms)
        left = np.where(np.r_[False, adjacent], np.r_[indexes[:1], indexes[:-1]], indexes)
        right = np.where(np.r_[adjacent, False], np.r_[indexes[1:], indexes[-1:]], indexes)
        valid = left != right
        flat_out[indexes[valid], column] = (
            series[right[valid]] - series[left[valid]]
        ) / (seconds[right[valid]] - seconds[left[valid]])
    return output


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
