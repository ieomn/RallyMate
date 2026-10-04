from __future__ import annotations

from typing import Any

import numpy as np


MAX_CONTIGUOUS_GAP_MS = 160
CONTIGUOUS_DURATION_VERSION = "continuous-duration-v1.0.0"


def longest_contiguous_duration(
    timestamp_ms: np.ndarray,
    indexes: np.ndarray,
    mask: np.ndarray,
    *,
    max_gap_ms: int = MAX_CONTIGUOUS_GAP_MS,
) -> tuple[int | None, tuple[int, ...], dict[str, Any]]:
    """Choose observed support by elapsed time, never bridging missing samples.

    Retain the feature contract's one-median-interval endpoint support. Only
    adjacent samples within the allowed gap contribute to that cadence; a
    missing-video interval cannot inflate either the run or its endpoint.
    """

    timestamps = np.asarray(timestamp_ms, dtype=np.float64)
    selected = np.asarray(indexes)
    active = np.asarray(mask, dtype=bool)
    if (
        timestamps.ndim != 1
        or not np.isfinite(timestamps).all()
        or np.any(np.diff(timestamps) <= 0)
        or active.shape != timestamps.shape
        or selected.ndim != 1
        or not np.issubdtype(selected.dtype, np.integer)
        or np.any(selected < 0)
        or np.any(selected >= timestamps.size)
        or np.any(np.diff(selected) <= 0)
        or max_gap_ms <= 0
    ):
        raise ValueError("duration inputs must have ordered timestamps and aligned event indexes")
    selected = selected.astype(np.int64, copy=False)
    gaps = np.diff(timestamps[selected])
    adjacent = (np.diff(selected) == 1) & (gaps <= max_gap_ms)
    cadence = gaps[adjacent]
    median_dt = int(np.median(cadence)) if cadence.size else 0
    diagnostics = {
        "duration_algorithm_version": CONTIGUOUS_DURATION_VERSION,
        "max_contiguous_gap_ms": max_gap_ms,
        "median_dt_ms": median_dt,
        "continuity_break_count": int((~adjacent).sum()),
        "duration_support": "observed_timestamp_span_plus_median_adjacent_interval",
    }
    longest: tuple[int, int] | None = None
    longest_span = -1.0
    start: int | None = None
    for position in range(selected.size + 1):
        is_active = position < selected.size and active[selected[position]]
        interrupted = position > 0 and position < selected.size and not adjacent[position - 1]
        if start is not None and (not is_active or interrupted):
            end = position - 1
            span = timestamps[selected[end]] - timestamps[selected[start]]
            if span > longest_span:
                longest = (int(selected[start]), int(selected[end]))
                longest_span = float(span)
            start = None
        if is_active and start is None:
            start = position
    if longest is None:
        return None, (), diagnostics
    diagnostics["observed_span_ms"] = int(longest_span)
    return int(longest_span) + median_dt, longest, diagnostics
