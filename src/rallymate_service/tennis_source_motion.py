"""Bind projected joint trajectories to the original background camera reference.

These are stabilized image-plane measurements, never metric world coordinates.
Missing camera observations are not filled with a convenient identity transform.
"""
from collections import Counter
from collections.abc import Mapping
import math

import numpy as np

from rallymate_vision.sam3d_evaluation import COORDINATES_2D, _binding_error, _time


def source_motion_by_frame(records, frames, sha):
    counts = Counter(row.get("frame", {}).get("index") for row in frames)
    by_frame = {row.get("frame", {}).get("index"): row for row in frames}
    output = {}
    origins = [float(_time(r) * 1000) - r["relative_timestamp_ms"] for r in records
               if _binding_error(r) is None and type(r.get("relative_timestamp_ms")) in (int, float)
               and math.isfinite(r["relative_timestamp_ms"])]
    origin = float(np.median(origins)) if origins else 0.
    for native in records:
        index = native.get("frame_index")
        source = by_frame.get(index, {})
        frame, camera = source.get("frame", {}), source.get("camera_motion", {})
        if (counts[index] != 1 or _binding_error(native) is not None
                or native.get("status") != "ok" or native.get("coordinate_system_2d") != COORDINATES_2D
                or str(native.get("video_sha256", "")).lower() != sha
                or str(frame.get("video_sha256", "")).lower() != sha
                or any(native.get(k) != frame.get(k) for k in ("pts", "time_base_num", "time_base_den"))
                or camera.get("compensation_valid") is not True
                or camera.get("reference_epoch") != native.get("camera_reference_epoch")):
            continue
        try:
            matrix = np.asarray(camera.get("matrix_to_reference"), dtype=float)
            width, height = native["image_size"]
        except (TypeError, ValueError, KeyError):
            continue
        if matrix.shape != (2, 3) or not np.isfinite(matrix).all() or min(width, height) <= 0:
            continue
        linear = matrix[:, :2]
        # Background compensation must be a proper nonsingular similarity.
        gram = linear.T @ linear
        if np.linalg.det(linear) <= 0 or not np.allclose(gram, np.eye(2) * np.trace(gram) / 2, rtol=.02, atol=1e-5):
            continue
        points = {}
        for name, point in native.get("keypoints_2d", {}).items():
            if (isinstance(point, (list, tuple)) and len(point) == 2
                    and all(type(v) in (float, int) and math.isfinite(v) for v in point)
                    and 0 <= point[0] <= width and 0 <= point[1] <= height):
                points[name] = (matrix @ np.array([*point, 1.])).tolist()
        time = float(_time(native) * 1000) - origin
        key = (index, native.get("person_track_id"), native.get("selection_epoch"))
        output[key] = {"time": time, "frame": index, "points": points, "track": native["person_track_id"],
                       "selection_epoch": native["selection_epoch"], "camera_reference_epoch": native["camera_reference_epoch"],
                       "video_sha256": sha, "camera_reference_verified": True,
                       "coordinate_system": "fixed_camera_image_plane",
                       "image_to_reference_linear": linear.tolist(),
                       "source_pts": native["pts"], "time_base_num": native["time_base_num"], "time_base_den": native["time_base_den"]}
    return output


def window_source_motion(rows, observations, sha):
    selected = []
    for row in rows:
        observation = observations.get((row["frame"], row["track"], row["reference"][0]))
        if (observation is None or abs(observation["time"] - row["time"]) > 1.01
                or observation["camera_reference_epoch"] != row["reference"][1]):
            return [], {"camera_reference_verified": False, "reason": "continuous_camera_reference_missing"}
        selected.append(observation)
    scales = []
    for row in selected:
        p = row["points"]
        if not all(k in p for k in ("left_hip", "right_hip", "left_shoulder", "right_shoulder")):
            return [], {"camera_reference_verified": False, "reason": "projected_torso_missing"}
        hip = (np.array(p["left_hip"]) + p["right_hip"]) / 2
        shoulder = (np.array(p["left_shoulder"]) + p["right_shoulder"]) / 2
        scales.append(float(np.linalg.norm(shoulder - hip)))
    if not selected or min(scales) < 12 or len({r["camera_reference_epoch"] for r in selected}) != 1:
        return [], {"camera_reference_verified": False, "reason": "projected_scale_or_reference_invalid"}
    first = selected[0]
    return selected, {"coordinate_system": "fixed_camera_image_plane", "camera_reference_verified": True,
                      "source_binding_verified": True,
                      "scale": float(np.median(scales)), "video_sha256": sha,
                      "track": first["track"], "selection_epoch": first["selection_epoch"],
                      "camera_reference_epoch": first["camera_reference_epoch"]}


def verified_action_context(document, episode, sha):
    """Optional reviewed stroke/direction context, exactly bound to a candidate.

    Human or agent visual review is explicit provenance, not expert calibration.
    The geometric measurement cannot manufacture its own target direction.
    """
    if not isinstance(document, Mapping) or document.get("version") != "tennis-action-context-v1" or str(document.get("video_sha256", "")).lower() != sha:
        return None
    matches = [r for r in document.get("actions", []) if isinstance(r, Mapping) and r.get("episode_id") == episode.get("episode_id")]
    if len(matches) != 1:
        return None
    row = matches[0]
    if (row.get("status") != "reviewed" or row.get("method") not in {"human_visual_review", "agent_visual_review"}
            or not row.get("reviewer") or not row.get("evidence")
            or any(row.get(k) != episode.get(k) for k in ("person_track_id", "start_ms", "peak_ms", "end_ms"))):
        return None
    continuity = episode.get("continuity", {})
    for key, source_key in (("source_selection_epoch", "source_selection_epoch"),
                            ("native_selection_epoch", "source_selection_epoch"),
                            ("camera_reference_epoch", "camera_reference_epoch")):
        if key in row and row[key] != continuity.get(source_key):
            return None
    return dict(row)


def bind_reviewed_directions(reviewed, observations, context):
    """Rotate a reviewed source-frame vector into the measured camera reference.

    Candidate/phase binding remains the caller's responsibility. This helper
    additionally requires the exact reviewed source frame, decoder-bound source
    observations, matching subject/reference identities, and its actual camera
    transform. A true flag or an epoch number alone never establishes direction.
    Translation is intentionally omitted from vector transformations.
    """
    result = {"directions": {}, "evidence": {"status": "not_measured", "reason": None}}
    def missing(reason):
        result["evidence"]["reason"] = reason
        return result
    if not isinstance(reviewed, Mapping) or not isinstance(context, Mapping):
        return missing("direction_review_missing")
    basis = reviewed.get("direction_basis")
    time = reviewed.get("direction_reference_timestamp_ms")
    if (reviewed.get("status") != "reviewed" or reviewed.get("method") not in {"human_visual_review", "agent_visual_review"}
            or not isinstance(reviewed.get("reviewer"), str) or not reviewed["reviewer"] or not reviewed.get("evidence")
            or not isinstance(basis, Mapping) or not basis.get("basis")
            or basis.get("coordinate_system") != COORDINATES_2D
            or type(time) not in (int, float) or not math.isfinite(time) or time < 0):
        return missing("source_image_direction_reference_unverified")
    if (context.get("coordinate_system") != "fixed_camera_image_plane" or context.get("camera_reference_verified") is not True
            or context.get("source_binding_verified") is not True
            or basis.get("camera_reference_epoch") != context.get("camera_reference_epoch")
            or reviewed.get("camera_reference_epoch") != context.get("camera_reference_epoch")):
        return missing("direction_camera_reference_mismatch")
    proof_frames = basis.get("frames")
    if not isinstance(proof_frames, list):
        return missing("direction_reference_frame_evidence_missing")
    evidence_frames = [frame for frame in proof_frames if isinstance(frame, Mapping)
                       and type(frame.get("timestamp_ms")) in (int, float) and math.isfinite(frame["timestamp_ms"])
                       and abs(frame["timestamp_ms"] - time) <= 1.01]
    if len(evidence_frames) != 1:
        return missing("direction_reference_frame_evidence_missing_or_ambiguous")
    source = list(observations.values()) if isinstance(observations, Mapping) else list(observations)
    matches = [row for row in source if isinstance(row, Mapping) and type(row.get("time")) in (int, float)
               and math.isfinite(row["time"]) and abs(row["time"] - time) <= 1.01
               and all(row.get(key) == context.get(key) for key in ("track", "selection_epoch", "camera_reference_epoch"))]
    if len(matches) != 1:
        return missing("direction_reference_observation_missing_or_ambiguous")
    observation = matches[0]
    sha = context.get("video_sha256")
    if (not isinstance(sha, str) or len(sha) != 64
            or str(observation.get("video_sha256", "")).lower() != sha.lower()
            or "video_sha256" in reviewed and str(reviewed["video_sha256"]).lower() != sha.lower()
            or observation.get("coordinate_system") != "fixed_camera_image_plane"
            or observation.get("camera_reference_verified") is not True
            or "source_frame_index" in evidence_frames[0] and evidence_frames[0]["source_frame_index"] != observation.get("frame")):
        return missing("direction_reference_source_mismatch")
    try:
        linear = np.asarray(observation.get("image_to_reference_linear"), dtype=float)
    except (ValueError, TypeError):
        return missing("direction_reference_transform_invalid")
    if linear.shape != (2, 2) or not np.isfinite(linear).all():
        return missing("direction_reference_transform_invalid")
    gram = linear.T @ linear
    if np.linalg.det(linear) <= 0 or not np.allclose(gram, np.eye(2) * np.trace(gram) / 2, rtol=.02, atol=1e-5):
        return missing("direction_reference_transform_invalid")
    fields = {}
    for field in ("forward_direction_2d", "up_direction_2d"):
        if reviewed.get(field + "_verified") is not True:
            fields[field] = {"status": "not_measured", "reason": "direction_not_reviewed"}
            continue
        try:
            vector = np.asarray(reviewed.get(field), dtype=float)
        except (ValueError, TypeError):
            vector = np.asarray([])
        if vector.shape != (2,) or not np.isfinite(vector).all() or np.linalg.norm(vector) < 1e-8:
            fields[field] = {"status": "not_measured", "reason": "source_image_direction_invalid"}
            continue
        normalized = vector / np.linalg.norm(vector)
        transformed = linear @ normalized
        length = float(np.linalg.norm(transformed))
        if length <= 1e-8:
            fields[field] = {"status": "not_measured", "reason": "direction_reference_transform_degenerate"}
            continue
        direction = (transformed / length).tolist()
        result["directions"].update({field: direction, field + "_verified": True})
        fields[field] = {"status": "bound", "source_image_direction": normalized.tolist(), "fixed_reference_direction": direction}
    result["evidence"] = {"status": "bound" if result["directions"] else "not_measured", "reason": None if result["directions"] else "no_valid_reviewed_direction",
                          "method": "source_frame_vector_transformed_by_background_similarity", "classification_method": reviewed["method"],
                          "source_video_sha256": sha, "source_frame_index": observation["frame"], "source_timestamp_ms": observation["time"],
                          "camera_reference_epoch": observation["camera_reference_epoch"], "image_to_reference_linear": linear.tolist(),
                          "source_pts": observation.get("source_pts"), "time_base_num": observation.get("time_base_num"),
                          "time_base_den": observation.get("time_base_den"), "fields": fields}
    return result
