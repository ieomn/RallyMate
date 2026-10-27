"""Source-local paired wrist geometry, independent of handedness and scores.

These are image-plane movement proposals, not contact or grip ownership.
Thresholds for coupling, vector agreement and motion reuse existing candidate /
two-hand classification gates. The arc route adds a separate sufficient pattern
for broad two-wrist sweeps whose follow-through is not screen-horizontal.
"""
from __future__ import annotations

import math
from .pose_observations import sample_source_reference

PHASE_VERSION = "paired-wrist-phase-v1"
ARC_VERSION = "paired-wrist-arc-v1"
COUPLING_DISTANCE = .45
MIN_COUPLED_FRACTION = .7
MIN_DIRECTION_COSINE = .7
MIN_DISPLACEMENT_RATIO = .5
MIN_WRIST_SPEED = 1.5
ARC_RULES = {"minimum_arc_radians": math.pi, "minimum_radius_torso": .3,
             "minimum_diameter_torso": .9, "maximum_angle_variation_to_net": 2.5,
             "maximum_single_step_radians": math.pi / 2,
             "duration_ms": [200, 1200], "minimum_racket_observations": 2,
             "minimum_shoulder_wrist_range_torso": .2}


def _window(samples, start_ms, end_ms):
    return [s for s in samples if start_ms <= s.time <= end_ms]


def paired_wrist_phase_evidence(samples, *, start_ms, end_ms, motion_wrist):
    from .temporal_recognition import samples_are_continuous, wrist_kinematics
    from .vector_kinematics import WristKinematicsUnavailable
    pair = "right" if motion_wrist == "left" else "left"
    result = {"version": PHASE_VERSION, "status": "unavailable", "reason_code": "paired_observations_unavailable",
              "motion_wrist": motion_wrist, "paired_wrist": pair, "start_ms": start_ms, "end_ms": end_ms,
              "source": {}, "sample_binding": [], "stats": {}}
    local = _window(samples, start_ms, end_ms)
    if (motion_wrist not in {"left", "right"} or len(local) < 3
            or local[0].time != start_ms or local[-1].time != end_ms
            or any(not samples_are_continuous(a, b) for a, b in zip(local, local[1:]))):
        return result
    first = local[0]
    result["source"] = {"person_track_id": first.track, "selection_epoch": first.selection_epoch,
                        "camera_reference_epoch": getattr(first, "camera_reference_epoch", None),
                        "normalization_basis": getattr(first, "normalization_basis", "full_torso"),
                        **sample_source_reference(first)}
    result["sample_binding"] = [{"frame_index": s.frame_index, "timestamp_ms": s.time,
                                 "source_binding": dict(getattr(s, "source_binding", {}))} for s in local]
    try:
        left, ls = wrist_kinematics(local, "left")
        right, rs = wrist_kinematics(local, "right")
    except (WristKinematicsUnavailable, ValueError, KeyError):
        return result
    gaps = [math.dist(a, b) for a, b in zip(left, right)]
    dl = tuple(b-a for a,b in zip(left[0], left[-1]))
    dr = tuple(b-a for a,b in zip(right[0], right[-1]))
    nl, nr = math.hypot(*dl), math.hypot(*dr)
    cosine = sum(a*b for a,b in zip(dl,dr)) / (nl*nr) if min(nl,nr) > 1e-8 else None
    ratio = min(nl,nr) / max(nl,nr) if max(nl,nr) > 1e-8 else 0.
    coupled = sum(g < COUPLING_DISTANCE for g in gaps) / len(gaps)
    result["stats"] = {"coupled_fraction": coupled, "max_wrist_gap": max(gaps),
        "net_displacement_cosine": cosine, "displacement_ratio": ratio,
        "left_peak_speed_torso_per_s": max(ls), "right_peak_speed_torso_per_s": max(rs),
        "left_displacement_torso": nl, "right_displacement_torso": nr}
    if (coupled >= MIN_COUPLED_FRACTION and cosine is not None and cosine >= MIN_DIRECTION_COSINE
            and ratio >= MIN_DISPLACEMENT_RATIO and min(max(ls), max(rs)) >= MIN_WRIST_SPEED):
        result.update(status="supported", reason_code="source_local_paired_wrist_motion")
    else:
        result["reason_code"] = "paired_motion_not_supported"
    return result


def paired_arc_sweep_evidence(samples, *, start_ms, acceleration_start_ms, peak_ms, end_ms, motion_wrist):
    """A complete broad arc by both wrists; no ideal joint activation order.

    A straight raise, one-arm balance, disconnected phases and a small setup
    cannot supply this independent alternative to a horizontal crossing.
    Image rotation and mirroring preserve distances and absolute angular arc.
    """
    from .temporal_recognition import samples_are_continuous, wrist_kinematics
    from .vector_kinematics import WristKinematicsUnavailable
    acceleration = paired_wrist_phase_evidence(samples, start_ms=acceleration_start_ms,
                                               end_ms=peak_ms, motion_wrist=motion_wrist)
    result = {"version": ARC_VERSION, "status": "unavailable", "reason_code": "paired_arc_not_supported",
              "start_ms": start_ms, "peak_ms": peak_ms, "end_ms": end_ms,
              "motion_wrist": motion_wrist, "acceleration": acceleration, "stats": {}}
    local = _window(samples, start_ms, end_ms)
    if (acceleration["status"] != "supported" or len(local) < 7
            or local[0].time != start_ms or local[-1].time != end_ms
            or not ARC_RULES["duration_ms"][0] <= end_ms-start_ms <= ARC_RULES["duration_ms"][1]
            or any(not samples_are_continuous(a,b) for a,b in zip(local,local[1:]))):
        return result
    if any(not all(k in s.points for k in ("left_shoulder","right_shoulder")) for s in local):
        return result
    try:
        left, _ = wrist_kinematics(local,"left"); right, _ = wrist_kinematics(local,"right")
    except (WristKinematicsUnavailable, ValueError, KeyError):
        return result
    centers = [tuple((a[j]+b[j]-s.points["left_shoulder"][j]-s.points["right_shoulder"][j])/2
                     for j in (0,1)) for s,a,b in zip(local,left,right)]
    coupled = sum(math.dist(a,b) < COUPLING_DISTANCE for a,b in zip(left,right)) / len(local)
    radii = [math.hypot(*p) for p in centers]
    angles = [math.atan2(y,x) for x,y in centers]
    turns = [math.atan2(math.sin(b-a),math.cos(b-a)) for a,b in zip(angles,angles[1:])]
    net, variation = abs(sum(turns)), sum(abs(v) for v in turns)
    diameter = max(math.dist(a,b) for a in centers for b in centers)
    support = [s.time for s in local if s.racket_sides]
    extensions = {side: [math.dist(p, s.points[side+"_shoulder"]) for p,s in zip(points,local)]
                  for side,points in (("left",left),("right",right))}
    extension_ranges = {side:max(values)-min(values) for side,values in extensions.items()}
    result["stats"] = {"coupled_fraction":coupled,"minimum_radius_torso":min(radii),
        "arc_radians":net,"angular_variation_radians":variation,"maximum_step_radians":max(abs(v) for v in turns),
        "diameter_torso":diameter,"racket_observation_ms":support,
        "shoulder_wrist_range_torso":extension_ranges}
    if (coupled >= MIN_COUPLED_FRACTION and min(radii) >= ARC_RULES["minimum_radius_torso"]
            and net >= ARC_RULES["minimum_arc_radians"] and diameter >= ARC_RULES["minimum_diameter_torso"]
            and variation <= net*ARC_RULES["maximum_angle_variation_to_net"]
            and max(abs(v) for v in turns) <= ARC_RULES["maximum_single_step_radians"]
            and len(support) >= ARC_RULES["minimum_racket_observations"]
            and min(extension_ranges.values()) >= ARC_RULES["minimum_shoulder_wrist_range_torso"]):
        result.update(status="supported",reason_code="source_local_paired_arc_sweep")
    return result
