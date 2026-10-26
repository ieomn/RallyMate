"""Coordinate-equivariant 2D smoothing and observed-interval velocity.

The same time weights fit every coordinate. No per-axis order statistic,
invented sample, endpoint zero or derivative across missing data is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from collections.abc import Sequence

VERSION = "time-weighted-vector-kinematics-v1.0.0"
HALF_WINDOW_MS = 80.0
MAX_GAP_MS = 160.0


class WristKinematicsUnavailable(ValueError):
    """A complete wrist measurement is unavailable, not a backend failure.

    Callers may cancel this candidate without bridging missing observations.
    Malformed inputs and unrelated calculation errors remain ordinary errors.
    """

    reason_code = "wrist_observation_discontinuity"


@dataclass(frozen=True)
class VectorTrajectory:
    points: list[tuple[float, float] | None]
    interval_velocity: list[tuple[float, float] | None]
    interval_speed: list[float | None]
    interval_midpoint_ms: list[float]
    segment_ids: list[int | None]


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def vector_trajectory(timestamp_ms: Sequence[float], positions: Sequence,
                      *, continuous: Sequence[bool] | None = None,
                      frame_indices: Sequence[int] | None = None) -> VectorTrajectory:
    """Smooth at observed times, then measure adjacent interval-average speed.

    Interior samples use triangular time weights in +/-80 ms and a local
    linear fit. Its intercept is the smoothed point at the original time.
    First/last points of each continuous run stay observed and unfiltered.
    Missing positions, source breaks and gaps split runs; they are not filled.
    This filter can attenuate sharp acceleration and retains endpoint noise.
    It estimates image-plane motion, not anatomical or instantaneous speed.
    """
    times = list(timestamp_ms)
    count = len(times)
    if len(positions) != count or not all(_finite(t) for t in times):
        raise ValueError("Aligned finite timestamps are required")
    if continuous is not None and (len(continuous) != max(0, count-1) or any(type(v) is not bool for v in continuous)):
        raise ValueError("One boolean continuity decision per interval is required")
    if frame_indices is not None and (len(frame_indices) != count or any(type(v) is not int or v < 0 for v in frame_indices)):
        raise ValueError("Aligned original frame indices are required")
    points = [tuple(float(v) for v in p) if isinstance(p, (list, tuple)) and len(p) == 2 and all(_finite(v) for v in p) else None
              for p in positions]
    positive_dt = [b-a for a,b in zip(times,times[1:]) if b>a]
    gap = min(MAX_GAP_MS, statistics.median(positive_dt)*1.8) if positive_dt else MAX_GAP_MS
    steps = [b-a for a,b in zip(frame_indices,frame_indices[1:]) if b>a] if frame_indices is not None else []
    frame_gap = statistics.median(steps)*1.8 if steps else None
    segments: list[list[int]] = []
    for i,p in enumerate(points):
        connected = (i>0 and points[i-1] is not None and 0<times[i]-times[i-1]<=gap
                     and (continuous is None or continuous[i-1])
                     and (frame_indices is None or frame_gap is not None and 0<frame_indices[i]-frame_indices[i-1]<=frame_gap))
        if p is None:
            continue
        if not connected:
            segments.append([])
        segments[-1].append(i)
    filtered = points[:]
    ids: list[int | None] = [None]*count
    for identifier, indexes in enumerate(segments):
        for i in indexes:
            ids[i] = identifier
        for i in indexes[1:-1]:
            neighborhood = [j for j in indexes if abs(times[j]-times[i]) < HALF_WINDOW_MS]
            if len(neighborhood)<3 or not any(j<i for j in neighborhood) or not any(j>i for j in neighborhood):
                continue
            offsets = [(times[j]-times[i])/1000.0 for j in neighborhood]
            weights = [1-abs(times[j]-times[i])/HALF_WINDOW_MS for j in neighborhood]
            s0 = sum(weights)
            mean_t = sum(w*t for w,t in zip(weights,offsets))/s0
            variance = sum(w*(t-mean_t)**2 for w,t in zip(weights,offsets))
            if variance<=0:
                continue
            # Identical scalar coefficients act on x and y: rotations,
            # translations and uniform coordinate scaling commute with it.
            coefficients = [w/s0 - mean_t*w*(t-mean_t)/variance for w,t in zip(weights,offsets)]
            origin = points[i]
            filtered[i] = tuple(origin[axis] + sum(a*(points[j][axis]-origin[axis]) for a,j in zip(coefficients,neighborhood))
                                for axis in (0,1))
    velocity, speed, midpoint = [], [], []
    for i in range(count-1):
        midpoint.append((times[i]+times[i+1])/2)
        if ids[i] is None or ids[i]!=ids[i+1]:
            velocity.append(None); speed.append(None)
            continue
        dt = (times[i+1]-times[i])/1000.0
        v = tuple((filtered[i+1][axis]-filtered[i][axis])/dt for axis in (0,1))
        velocity.append(v); speed.append(math.hypot(*v))
    return VectorTrajectory(filtered, velocity, speed, midpoint, ids)


def measurement_contract() -> dict:
    return {"version": VERSION, "smoothing": "shared_time_weighted_local_linear_vector_fit",
            "half_window_ms": HALF_WINDOW_MS, "endpoint_policy": "preserve_observed_segment_endpoints",
            "speed": "norm_of_adjacent_filtered_position_difference_over_actual_elapsed_seconds",
            "speed_time": "source_interval_midpoint", "coordinate_unit": "input_coordinate_unit",
            "interpolation_used": False, "is_instantaneous_or_3d_speed": False}
