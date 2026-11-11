"""Source-window joint kinematics, expressed in the subject's body frame.

Inputs are the continuous, identity-bound native rows already validated by the
tennis assessor. No pixel distance is promoted to metres and no joint is filled
from another person or from an unobserved neighbouring frame.
"""
from __future__ import annotations

import math
import numpy as np

VERSION = "body-joint-kinematics-v1"
MIN_COVERAGE = .70


def joint_angle(a, b, c):
    if any(p is None for p in (a, b, c)):
        return None
    a, b, c = (np.asarray(p, dtype=float) for p in (a, b, c))
    if any(p.shape != (3,) or not np.isfinite(p).all() for p in (a, b, c)):
        return None
    u, v = a - b, c - b
    denominator = float(np.linalg.norm(u) * np.linalg.norm(v))
    return float(np.degrees(np.arccos(np.clip(u @ v / denominator, -1, 1)))) if denominator > 1e-10 else None


def joint_series(rows):
    series = {f"{side}_{joint}_{kind}_deg": [] for side in ("left", "right")
              for joint, kind in (("knee", "flexion"), ("elbow", "flexion"), ("shoulder", "elevation"))}
    series["shoulder_hip_separation_deg"] = []
    series["stance_width_shoulders"] = []
    for row in rows:
        points = row["local"]
        for side in ("left", "right"):
            for joint, a, b, c in (("knee", "hip", "knee", "ankle"), ("elbow", "shoulder", "elbow", "wrist")):
                angle = joint_angle(*(points.get(f"{side}_{name}") for name in (a, b, c)))
                series[f"{side}_{joint}_flexion_deg"].append(None if angle is None else 180 - angle)
            angle = joint_angle(points.get(f"{side}_hip"), points.get(f"{side}_shoulder"), points.get(f"{side}_elbow"))
            series[f"{side}_shoulder_elevation_deg"].append(angle)
        series["shoulder_hip_separation_deg"].append(row.get("signed_separation"))
        ankles = [points.get(f"{s}_ankle") for s in ("left", "right")]
        shoulders = [points.get(f"{s}_shoulder") for s in ("left", "right")]
        width = None
        if all(p is not None for p in ankles + shoulders):
            denominator = float(np.linalg.norm(shoulders[0] - shoulders[1]))
            if denominator > 1e-10:
                width = float(np.linalg.norm(ankles[0] - ankles[1]) / denominator)
        series["stance_width_shoulders"].append(width)
    return series


def summarize_series(values, times_ms, frames, *, unit, circular=False):
    values = np.asarray([np.nan if v is None else v for v in values], dtype=float)
    times = np.asarray(times_ms, dtype=float)
    valid = np.isfinite(values)
    if circular:
        # Unwrap separately inside each observed contiguous segment. A missing
        # joint cannot establish the direction/number of turns across the gap.
        starts = np.flatnonzero(valid & ~np.r_[False, valid[:-1]])
        for start in starts:
            stop = int(start)
            while stop < len(values) and valid[stop]:
                stop += 1
            values[start:stop] = np.degrees(np.unwrap(np.radians(values[start:stop])))
    base = {"unit": unit, "coverage": round(float(valid.mean()), 6) if len(valid) else 0.,
            "observation_count": int(valid.sum()), "source_frames": [int(f) for f, good in zip(frames, valid) if good]}
    if not len(values) or valid.mean() < MIN_COVERAGE:
        return {**base, "status": "not_measured", "reason": "insufficient_joint_observations"}
    measured = values[valid]
    if circular:
        # Summary extent is the smallest observed arc, not an inferred number
        # of rotations across missing frames. Temporal derivatives above still
        # require adjacent measurements.
        wrapped = np.sort(measured % 360)
        gaps = np.diff(np.r_[wrapped, wrapped[0] + 360])
        origin = wrapped[(int(np.argmax(gaps)) + 1) % len(wrapped)]
        measured = origin + (measured - origin) % 360
        measured -= 360 * np.round(np.median(measured) / 360)
    result = {**base, "status": "measured", "median": round(float(np.median(measured)), 5),
              "min": round(float(np.min(measured)), 5), "max": round(float(np.max(measured)), 5),
              "p10": round(float(np.quantile(measured, .1)), 5), "p90": round(float(np.quantile(measured, .9)), 5),
              "range": round(float(np.ptp(measured)), 5)}
    # Derivatives only use adjacent original observations and the original PTS.
    if unit == "deg" and len(values) > 1:
        dt = np.diff(times) / 1000
        adjacent = valid[:-1] & valid[1:] & (dt > 0) & (dt <= .16)
        if adjacent.any():
            speed = np.diff(values)[adjacent] / dt[adjacent]
            result["angular_speed_p90_deg_s"] = round(float(np.quantile(abs(speed), .9)), 5)
    return result


def measure_joint_kinematics(rows, phases=()):
    if not rows:
        return {"version": VERSION, "metrics": {}, "phases": []}
    times = [r["time"] for r in rows]
    if any(not math.isfinite(t) for t in times) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("joint kinematics requires strictly increasing original timestamps")
    frames = [r["frame"] for r in rows]
    series = joint_series(rows)
    def summarize(indices):
        return {name: summarize_series([values[i] for i in indices], [times[i] for i in indices],
                                       [frames[i] for i in indices], unit="deg" if name.endswith("_deg") else "shoulder_width",
                                       circular=name == "shoulder_hip_separation_deg")
                for name, values in series.items()}
    phase_output = []
    for phase in phases:
        if "source_start_frame" in phase and "source_end_frame" in phase:
            indices = [i for i, frame in enumerate(frames) if phase["source_start_frame"] <= frame <= phase["source_end_frame"]]
        else:
            indices = [i for i, t in enumerate(times) if phase["start_ms"] <= t <= phase["end_ms"]]
        phase_output.append({**phase, "metrics": summarize(indices)})
    return {"version": VERSION, "coordinate_system": "body_relative_native_3d", "measurement_semantics": "monocular_model_estimate",
            "minimum_joint_coverage": MIN_COVERAGE, "metric_units": "degrees_or_body_proportions_not_world_metres",
            "metrics": summarize(list(range(len(rows)))), "phases": phase_output}
