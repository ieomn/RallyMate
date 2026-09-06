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
    flat_in = array.reshape(array.shape[0], -1)
    flat_out = output.reshape(output.shape[0], -1)
    # Fixed-camera video timestamps are ordered.  Build the local window once
    # per output sample, then aggregate every feature column with vectorized
    # finite-value weights.  This preserves the previous timestamp-weighted
    # definition while avoiding a Python loop over each scalar column.
    for index, timestamp in enumerate(timestamps):
        left = int(np.searchsorted(timestamps, timestamp - radius_ms, side="left"))
        right = int(np.searchsorted(timestamps, timestamp + radius_ms, side="right"))
        local_timestamps = timestamps[left:right]
        distances = np.abs(local_timestamps - timestamp)
        weights = np.maximum(1.0 - distances / max(radius_ms, 1), 1e-6)
        candidates = flat_in[left:right]
        finite = np.isfinite(candidates)
        counts = finite.sum(axis=0)
        weighted = np.where(finite, candidates, 0.0) * weights[:, None]
        denominators = (finite * weights[:, None]).sum(axis=0)
        eligible = (counts >= min_samples) & (denominators > 0.0)
        flat_out[index, eligible] = (
            weighted[:, eligible].sum(axis=0) / denominators[eligible]
        )
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
