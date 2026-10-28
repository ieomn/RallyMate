"""Conservative box/limb association, independent of stroke type or score.

A detector box is not a handle localization. These tests expose conflicting
head/forearm geometry; they do not infer grip order or repair model joints.
"""
from __future__ import annotations

import math

VERSION = "racket-box-limb-association-v1"
HEAD_LANDMARKS = ("nose", "left_eye", "right_eye", "left_ear", "right_ear")


def inside(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def forward_ray_intersects_box(elbow, wrist, box):
    """Intersect the closed box with elbow->wrist continued beyond the wrist.

    This is only a disambiguator when both wrists meet the existing proximity
    gate. A flexed wrist can contradict this proxy, so no intersection is an
    unavailable association, never evidence that the player holds no racket.
    """
    direction = (wrist[0] - elbow[0], wrist[1] - elbow[1])
    if math.hypot(*direction) <= 1e-9:
        return None
    lo, hi = 0.0, math.inf
    for axis in (0, 1):
        if abs(direction[axis]) <= 1e-9:
            if not box[axis] <= wrist[axis] <= box[axis + 2]:
                return False
            continue
        a = (box[axis] - wrist[axis]) / direction[axis]
        b = (box[axis + 2] - wrist[axis]) / direction[axis]
        lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
    return hi >= lo


def box_limb_evidence(raw_points, box, near_sides):
    head = {name: raw_points[name] for name in HEAD_LANDMARKS if name in raw_points}
    wrists = [raw_points.get(side + "_wrist") for side in ("left", "right")]
    # Three head landmarks, including eye and ear groups, can expose a detector
    # box on a profile head/hair even when the nose lies outside the box. A lone
    # nose/eye is insufficient. This flags ambiguity, not a false-object label.
    contained = [name for name, point in head.items() if inside(point, box)]
    head_supported = len(head) >= 3 and any("eye" in k for k in head) and any("ear" in k for k in head)
    mixed_cluster = (len(contained) >= 3 and any("eye" in k for k in contained)
                     and any("ear" in k for k in contained))
    profile_head_span = (all(name in head for name in HEAD_LANDMARKS)
                         and all(name in contained for name in ("left_ear", "right_ear")))
    encloses_head = (head_supported and (mixed_cluster or profile_head_span)
                     and all(p is not None and not inside(p, box) for p in wrists))
    rays = {}
    if len(near_sides) == 2:
        for side in ("left", "right"):
            elbow, wrist = (raw_points.get(side + "_" + joint) for joint in ("elbow", "wrist"))
            if elbow is not None and wrist is not None:
                rays[side] = forward_ray_intersects_box(elbow, wrist, box)
    available = len(rays) == 2 and all(value is not None for value in rays.values())
    supported = [side for side, value in rays.items() if value is True] if available else None
    return {"version": VERSION, "head_geometry_available": head_supported,
            "head_cluster_overlap_without_wrist": bool(encloses_head),
            "contained_head_landmarks": contained,
            "profile_head_span_supported": profile_head_span,
            "forearm_ray_box_intersection": rays,
            "supported_motion_wrists": [] if encloses_head else supported,
            "semantics": "object_motion_association_not_handle_or_primary_grip"}
