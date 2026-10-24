"""Pure, same-frame pose/person association; no motion or identity inference."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from itertools import combinations


POLICY_VERSION = "pose-person-geometric-association-v1.0.0"
MIN_TARGET_IOU = 0.5
MIN_IOU_MARGIN = 0.1
DUPLICATE_JOINT_CONFIDENCE = 0.35
DUPLICATE_TORSO_FRACTION = 0.05
DUPLICATE_MIN_DISTANCE_PX = 2.0
DUPLICATE_JOINTS = (
    "left_shoulder", "right_shoulder", "left_hip", "right_hip",
    "left_ankle", "right_ankle",
)


def _number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid_size(size: object) -> bool:
    return isinstance(size, (list, tuple)) and len(size) == 2 and all(isinstance(v, int) and _number(v) and v > 0 for v in size)


def valid_box(box: object, size: tuple[int, int] | None = None) -> bool:
    if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(_number(v) for v in box):
        return False
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return False
    return size is None or (_valid_size(size) and 0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1])


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if math.isfinite(intersection) and math.isfinite(union) and union > 0 else 0.0


def select_target_pose_candidate(
    candidate_boxes: Sequence[Sequence[float]],
    target_box: Sequence[float] | None,
    *,
    roi_size: tuple[int, int],
) -> dict:
    """Select a unique IoU match. Candidate confidence never breaks a tie."""
    result = {
        "policy_version": POLICY_VERSION, "selected_index": None,
        "reason_code": None, "target_box_xyxy": list(target_box) if target_box is not None else None,
        "roi_size": list(roi_size), "candidate_count": len(candidate_boxes),
        "minimum_iou": MIN_TARGET_IOU, "minimum_iou_margin": MIN_IOU_MARGIN,
        "candidate_ious": [],
    }
    if not _valid_size(roi_size):
        result["reason_code"] = "pose_roi_size_invalid"
    elif target_box is not None and not valid_box(target_box, roi_size):
        result["reason_code"] = "pose_target_box_invalid"
    elif any(not valid_box(box, roi_size) for box in candidate_boxes):
        result["reason_code"] = "pose_candidate_box_invalid"
    elif not candidate_boxes:
        result["reason_code"] = "pose_candidates_missing"
    elif target_box is None:
        if len(candidate_boxes) == 1:
            result["selected_index"] = 0
            result["reason_code"] = "pose_single_candidate_without_target"
        else:
            result["reason_code"] = "pose_multiple_candidates_without_target"
    else:
        ious = [_iou(box, target_box) for box in candidate_boxes]
        result["candidate_ious"] = ious
        order = sorted(range(len(ious)), key=lambda index: (-ious[index], index))
        best = order[0]
        margin = ious[best] - (ious[order[1]] if len(order) > 1 else 0.0)
        result["best_iou"] = ious[best]
        result["iou_margin"] = margin
        if ious[best] < MIN_TARGET_IOU:
            result["reason_code"] = "pose_target_iou_below_minimum"
        elif margin + 1e-12 < MIN_IOU_MARGIN:
            result["reason_code"] = "pose_target_candidate_ambiguous"
        else:
            result["selected_index"] = best
            result["reason_code"] = "pose_target_unique_iou_match"
    return result


def person_box_ownership(candidate_box: Sequence[float], players: Sequence[Mapping], *, frame_size: tuple[int, int]) -> dict:
    """Check a selected global person box against every original detection."""
    entries = []
    for player in players:
        track = player.get("track_id")
        box = player.get("bbox_px")
        if isinstance(track, bool) or not isinstance(track, int) or track < 0 or not valid_box(box, frame_size):
            return {"preferred_person_track_id": None, "reason_code": "pose_person_detection_invalid", "ious": []}
        entries.append({"person_track_id": track, "iou": _iou(candidate_box, box) if valid_box(candidate_box, frame_size) else 0.0})
    if len({row["person_track_id"] for row in entries}) != len(entries):
        return {"preferred_person_track_id": None, "reason_code": "pose_person_track_id_duplicate", "ious": entries}
    entries.sort(key=lambda row: (-row["iou"], row["person_track_id"]))
    owner = None
    if entries and entries[0]["iou"] >= MIN_TARGET_IOU and entries[0]["iou"] - (entries[1]["iou"] if len(entries) > 1 else 0.0) + 1e-12 >= MIN_IOU_MARGIN:
        owner = entries[0]["person_track_id"]
    return {"preferred_person_track_id": owner, "reason_code": "pose_person_box_unique_owner" if owner is not None else "pose_person_box_owner_unresolved", "ious": entries}


def _pose_geometry(pose: Mapping, size: tuple[int, int]) -> dict | None:
    if pose.get("coordinate_space") != "original_frame":
        return None
    points = {}
    all_valid = []
    for row in pose.get("keypoints", []):
        if not isinstance(row, Mapping):
            return None
        name = row.get("name")
        x, y, confidence = row.get("x_px"), row.get("y_px"), row.get("confidence")
        if not all(_number(v) for v in (x, y, confidence)) or not (0 <= x <= size[0] and 0 <= y <= size[1] and DUPLICATE_JOINT_CONFIDENCE <= confidence <= 1):
            continue
        if row.get("in_frame") is False or row.get("confidence_in_range") is False:
            continue
        if name in points:
            return None
        points[name] = (float(x), float(y))
        all_valid.append((float(x), float(y)))
    if any(joint not in points for joint in DUPLICATE_JOINTS):
        return None
    shoulder = tuple((points["left_shoulder"][i] + points["right_shoulder"][i]) / 2 for i in (0, 1))
    hip = tuple((points["left_hip"][i] + points["right_hip"][i]) / 2 for i in (0, 1))
    torso = math.dist(shoulder, hip)
    if not math.isfinite(torso) or torso <= 0:
        return None
    return {"points": points, "torso_px": torso, "bbox_px": [min(p[0] for p in all_valid), min(p[1] for p in all_valid), max(p[0] for p in all_valid), max(p[1] for p in all_valid)]}


def audit_pose_person_associations(
    poses: Sequence[Mapping],
    player_detections: Sequence[Mapping],
    *,
    frame_size: tuple[int, int] | None = None,
) -> dict:
    """Detect near-identical bodies assigned to different tracks in one frame.

    Six named, high-confidence joints must all agree. Bbox ownership is only
    supporting evidence after a duplicate is established. No temporal speed,
    track-switch claim, repair, or missing-joint interpolation is used.
    """
    result = {"policy_version": POLICY_VERSION, "status": "audited", "reason_code": None, "rejected_person_track_ids": [], "conflicts": [], "unassessed_person_track_ids": []}
    if not _valid_size(frame_size):
        result.update(status="unavailable", reason_code="pose_association_frame_size_missing_or_invalid")
        return result
    prepared = []
    for pose in poses:
        if not isinstance(pose, Mapping):
            continue
        track = pose.get("person_track_id")
        if isinstance(track, bool) or not isinstance(track, int) or track < 0:
            continue
        geometry = _pose_geometry(pose, frame_size)
        if geometry is None:
            result["unassessed_person_track_ids"].append(track)
        else:
            prepared.append((track, geometry))
    rejected = set()
    for (a, ga), (b, gb) in combinations(prepared, 2):
        if a == b:
            continue
        distances = {joint: math.dist(ga["points"][joint], gb["points"][joint]) for joint in DUPLICATE_JOINTS}
        tolerance = max(DUPLICATE_MIN_DISTANCE_PX, DUPLICATE_TORSO_FRACTION * min(ga["torso_px"], gb["torso_px"]))
        if any(distance > tolerance for distance in distances.values()):
            continue
        owners = [person_box_ownership(g["bbox_px"], player_detections, frame_size=frame_size) for g in (ga, gb)]
        owner = owners[0]["preferred_person_track_id"]
        if owner not in (a, b) or owner != owners[1]["preferred_person_track_id"]:
            owner = None
        rejected_here = sorted({a, b} - ({owner} if owner is not None else set()))
        rejected.update(rejected_here)
        result["conflicts"].append({
            "reason_code": "pose_duplicate_body_different_tracks",
            "track_ids": [a, b], "joint_names": list(DUPLICATE_JOINTS),
            "per_joint_distance_px": distances, "tolerance_px": tolerance,
            "torso_lengths_px": [ga["torso_px"], gb["torso_px"]],
            "geometry_bbox_px": [ga["bbox_px"], gb["bbox_px"]],
            "pose_geometry_bbox_ownership": owners,
            "preferred_person_track_id": owner,
            "rejected_person_track_ids": rejected_here,
            "semantics": "same_frame_duplicate_geometry_not_track_switch_truth",
        })
    result["rejected_person_track_ids"] = sorted(rejected)
    result["unassessed_person_track_ids"] = sorted(set(result["unassessed_person_track_ids"]))
    return result
