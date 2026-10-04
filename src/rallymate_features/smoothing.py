from __future__ import annotations

import numpy as np


def interpolate_short_gaps(
    timestamp_ms: np.ndarray,
    values: np.ndarray,
    *,
    max_gap_ms: int = 160,
) -> np.ndarray:
    timestamps = np.asarray(timestamp_ms, dtype=np.int64)
    array = np.asarray(values, dtype=np.float64)
    if array.shape[0] != timestamps.size:
        raise ValueError("values first dimension must match timestamp_ms")
    output = array.copy()
    flat = output.reshape(output.shape[0], -1)
    for column in range(flat.shape[1]):
        series = flat[:, column]
        finite = np.isfinite(series)
        valid_indexes = np.flatnonzero(finite)
        for left, right in zip(valid_indexes, valid_indexes[1:]):
            if right <= left + 1:
                continue
            if int(timestamps[right] - timestamps[left]) > max_gap_ms:
                continue
            alpha = (timestamps[left + 1 : right] - timestamps[left]) / (
                timestamps[right] - timestamps[left]
            )
            series[left + 1 : right] = (
                series[left] + alpha * (series[right] - series[left])
            )
    return output


def time_weighted_smooth(
    timestamp_ms: np.ndarray,
    values: np.ndarray,
    *,
    radius_ms: int = 100,
    min_samples: int = 2,
) -> np.ndarray:
    timestamps = np.asarray(timestamp_ms, dtype=np.int64)
    array = np.asarray(values, dtype=np.float64)
    if array.shape[0] != timestamps.size:
        raise ValueError("values first dimension must match timestamp_ms")
    output = np.full_like(array, np.nan, dtype=np.float64)
    if timestamps.size == 0:
        return output
    flat_in = array.reshape(array.shape[0], -1)
    flat_out = output.reshape(output.shape[0], -1)
    left = np.searchsorted(timestamps, timestamps - radius_ms, side="left")
    right = np.searchsorted(timestamps, timestamps + radius_ms, side="right")
    widths = np.maximum(right - left, 0)
    max_width = int(widths.max(initial=0))
    if max_width == 0 or flat_in.shape[1] == 0:
        return output
    # Only materialize bounded local windows, never a T x T distance matrix.
    # The triangular weights, inclusive radius, finite counts, and 1e-6
    # boundary weight are identical to the original per-frame definition.
    batch_size = max(1, min(4096, 262144 // max(max_width * flat_in.shape[1], 1)))
    for start in range(0, timestamps.size, batch_size):
        stop = min(start + batch_size, timestamps.size)
        width = int(widths[start:stop].max(initial=0))
        indexes = left[start:stop, None] + np.arange(width)[None, :]
        included = indexes < right[start:stop, None]
        indexes = np.minimum(indexes, timestamps.size - 1)
        distances = np.abs(timestamps[indexes] - timestamps[start:stop, None])
        weights = np.maximum(1.0 - distances / max(radius_ms, 1), 1e-6)
        candidates = flat_in[indexes]
        finite = np.isfinite(candidates) & included[:, :, None]
        counts = finite.sum(axis=1)
        weighted = np.where(finite, candidates, 0.0) * weights[:, :, None]
        denominators = (finite * weights[:, :, None]).sum(axis=1)
        eligible = (counts >= min_samples) & (denominators > 0.0)
        np.divide(weighted.sum(axis=1), denominators, out=flat_out[start:stop], where=eligible)
    return output


def smooth_series(
    timestamp_ms: np.ndarray,
    values: np.ndarray,
    *,
    max_gap_ms: int = 160,
    radius_ms: int = 100,
) -> np.ndarray:
    interpolated = interpolate_short_gaps(
        timestamp_ms, values, max_gap_ms=max_gap_ms
    )
    return time_weighted_smooth(timestamp_ms, interpolated, radius_ms=radius_ms)
