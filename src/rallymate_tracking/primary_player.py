from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_vision.utils import box_iou, safe_float
from rallymate_tracking.subject_lock import SUBJECT_IDENTITY_VERSION, SubjectLock, valid_subject_identity


PRIMARY_PLAYER_ALGORITHM_VERSION = "primary-player-v0.4.0"
MODEL_SELECTION_EVIDENCE_VERSION = "sam-source-geometry-identity-guard-v1.0.0"
KEYPOINT_CONFIDENCE_MIN = 0.20
# This is only a floating-point equality tolerance for detecting tied selector
# scores.  It is not an identity-confidence or scoring-grade threshold.
SELECTION_SCORE_TIE_ABS_TOL = 1e-9
SELECTION_SCORE_TIE_REL_TOL = 1e-12
TARGET_JOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
SYMMETRIC_PAIRS = (
    ("left_shoulder", "right_shoulder"),
    ("left_elbow", "right_elbow"),
    ("left_wrist", "right_wrist"),
    ("left_hip", "right_hip"),
    ("left_knee", "right_knee"),
    ("left_ankle", "right_ankle"),
)


def primary_timeline_algorithm_version(
    timeline: list[dict[str, Any]],
    *,
    expected_version: str | None = None,
    require_declared: bool = False,
) -> str:
    """Resolve the algorithm version actually declared by a timeline.

    A current registry must never be paired with a legacy or mixed-version
    primary-player artifact while the scoring summary merely reports the
    current library constant.  Legacy timelines remain readable only for
    registries that do not declare a required primary-player version.
    """

    if not isinstance(timeline, list) or not timeline:
        raise ValueError("primary-player timeline must be a non-empty list")
    declared = [
        item.get("selection_algorithm_version")
        for item in timeline
        if isinstance(item.get("selection_algorithm_version"), str)
        and item["selection_algorithm_version"]
    ]
    if declared and len(declared) != len(timeline):
        raise ValueError(
            "primary-player timeline mixes declared and undeclared algorithm versions" #注意重新修改报错提醒
        )
    versions = set(declared)
    if len(versions) > 1:
        raise ValueError("primary-player timeline mixes algorithm versions")
    if not versions:
        if require_declared or expected_version is not None:
            raise ValueError(
                "primary-player timeline does not declare the registry-required "
                "algorithm version"
            )
        return "not_declared_legacy_timeline"
    actual = next(iter(versions))
    if expected_version is not None and actual != expected_version:
        raise ValueError(
            "primary-player timeline version does not match feasibility registry: "
            f"expected {expected_version}, got {actual}"
        )
    return actual


def registry_required_primary_player_version(
    indicators: list[dict[str, Any]],
) -> str | None:
    """Return the single primary-player version declared by a registry scope."""

    versions = {
        version
        for indicator in indicators
        if isinstance(indicator.get("versions"), dict)
        for version in [indicator["versions"].get("primary_player")]
        if isinstance(version, str) and version
    }
    if len(versions) > 1:
        raise ValueError("feasibility registry mixes primary-player algorithm versions")
    return next(iter(versions)) if versions else None


def _pose_points(pose: dict | None) -> dict[str, tuple[float, float, float]]:
    if pose is None or pose.get("source") == "sam3d" or pose.get("keypoint_format") == "mhr70":
        # SAM has no native confidence. Even a malformed numeric value must
        # not enter the detector confidence path or identity-anchor score.
        return {}
    points: dict[str, tuple[float, float, float]] = {}
    for point in pose.get("keypoints", []):
        name = point.get("name")
        if not isinstance(name, str) or point.get("in_frame") is False:
            continue
        try:
            values = (
                float(point["x_normalized"]),
                float(point["y_normalized"]),
                float(point["confidence"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        # Invalid coordinates must not become pose evidence just because the
        # confidence exceeds the permissive primary-player selection threshold.
        if all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values):
            points[name] = values
    return points


def _valid_fraction(pose: dict | None) -> float:
    points = _pose_points(pose)
    return sum(
        points.get(name, (0.0, 0.0, 0.0))[2] >= KEYPOINT_CONFIDENCE_MIN
        for name in TARGET_JOINTS
    ) / len(TARGET_JOINTS)


def _source_bound_model_candidate(record: dict, track: int):
    """Return independently bound model geometry, never synthetic confidence."""
    from rallymate_scoring.pose_observations import sam_source_bound_points

    poses = [pose for pose in record.get("poses", []) if pose.get("person_track_id") == track]
    if len(poses) != 1:
        return None, "selected_pose_missing_or_conflicting"
    if poses[0].get("source") != "sam3d":
        return None, "selected_pose_is_not_sam3d"
    if len([item for item in record.get("detections", [])
            if item.get("class_name") == "player" and item.get("track_id") == track]) != 1:
        return None, "selected_detection_missing_or_conflicting"
    frame, camera = record["frame"], record.get("camera_motion")
    result = sam_source_bound_points(poses[0], {**frame, "camera_motion": camera})
    if result is None:
        return None, "sam_source_binding_or_joint_contract_invalid"
    points, binding = result
    video_sha = frame.get("video_sha256")
    if (not isinstance(video_sha, str) or video_sha.lower() != binding["video_sha256"].lower()
            or any(type(frame.get(key)) is not int or frame[key] != binding[key]
                   for key in ("pts", "time_base_num", "time_base_den"))
            or not isinstance(camera, dict) or type(camera.get("reference_epoch")) is not int
            or camera["reference_epoch"] != binding["camera_reference_epoch"]):
        return None, "decoder_video_or_camera_binding_mismatch"
    ledger = record.get("sam3d_requests")
    requests = [request for request in ledger if isinstance(request, dict)
                and type(request.get("person_track_id")) is int
                and request["person_track_id"] == track] if isinstance(ledger, list) else []
    if len(requests) != 1:
        return None, "sam_request_ledger_missing_or_conflicting"
    request = requests[0]
    optional_fields = ("video_sha256", "frame_index", "processed_index", "pts",
                       "time_base_num", "time_base_den", "timestamp_ms", "camera_reference_epoch")
    if (request.get("status") != "ok" or type(request.get("selection_epoch")) is not int
            or request["selection_epoch"] != binding["selection_epoch"]
            or any(key in request and (type(request[key]) is not type(binding[key]) or request[key] != binding[key])
                   for key in optional_fields)):
        return None, "sam_request_ledger_binding_mismatch"
    return (points, binding, request), None


def _frame_candidates(record: dict[str, Any], *, unique_source: bool = True) -> list[dict[str, Any]]:
    frame = record["frame"]
    width = float(frame["width"])
    height = float(frame["height"])
    poses = {
        pose["person_track_id"]: pose
        for pose in record.get("poses", [])
        if isinstance(pose.get("person_track_id"), int)
    }
    model_frame = any(item.get("source") == "sam3d" or item.get("keypoint_format") == "mhr70"
                      for item in record.get("poses", [])) or bool(record.get("sam3d_requests"))
    candidates = []
    for detection in record.get("detections", []):
        if detection.get("class_name") != "player":
            continue
        x1, y1, x2, y2 = [float(value) for value in detection["bbox_px"]]
        track_id = int(detection["track_id"])
        pose = poses.get(track_id)
        valid_fraction = _valid_fraction(pose)
        is_model = (pose is None and model_frame) or any(item.get("person_track_id") == track_id
                    and (item.get("source") == "sam3d" or item.get("keypoint_format") == "mhr70")
                    for item in record.get("poses", []))
        model_geometry = None
        model_reason = None
        if is_model:
            bound, model_reason = (_source_bound_model_candidate(record, track_id) if unique_source
                                   else (None, "duplicate_source_frame"))
            if bound is not None:
                model_geometry = sum(name in bound[0] for name in TARGET_JOINTS) / len(TARGET_JOINTS)
        # Reuse the existing target-joint support slot and weights. Model
        # geometry has its own semantics and never changes detector confidence.
        selection_fraction = (model_geometry if model_geometry is not None else 0.) if is_model else valid_fraction
        candidates.append(
            {
                "source_track_id": track_id,
                "bbox_px": [x1, y1, x2, y2],
                "center_normalized": [
                    (x1 + x2) / 2.0 / width,
                    (y1 + y2) / 2.0 / height,
                ],
                "area_fraction": max(x2 - x1, 0.0) * max(y2 - y1, 0.0) / (width * height),
                "detection_confidence": float(detection.get("confidence", 0.0)),
                "pose": pose,
                "pose_valid_fraction": valid_fraction,
                "pose_selection_fraction": selection_fraction,
                "pose_selection_semantics": ("model_geometry_coverage" if is_model else "confidence_qualified_target_joint_coverage"),
                "model_geometry_coverage": model_geometry,
                "model_geometry_reason": model_reason,
                "model_source_binding": bound[1] if is_model and bound is not None else None,
            }
        )
    # Detector output order is not a semantic signal.  Canonical ordering also
    # makes all later tie breaks reproducible when two people have equal scores.
    return sorted(
        candidates,
        key=lambda item: (
            item["source_track_id"],
            *item["bbox_px"],
        ),
    )


def _normalize(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if high - low <= 1e-12:
        return {key: 1.0 for key in values}
    return {key: (value - low) / (high - low) for key, value in values.items()}


def _track_priors(frames: list[dict[str, Any]], *, model_geometry: bool = True) -> dict[int, dict[str, float]]:
    observations: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for frame in frames:
        for candidate in frame["candidates"]:
            observations[candidate["source_track_id"]].append(candidate)
    counts = {track_id: len(items) for track_id, items in observations.items()}
    coverage = {
        track_id: len(items) / max(len(frames), 1)
        for track_id, items in observations.items()
    }
    pose_quality = {
        track_id: statistics.mean(item["pose_valid_fraction"] for item in items)
        for track_id, items in observations.items()
    }
    selection_support = {
        track_id: statistics.mean(item["pose_selection_fraction" if model_geometry else "pose_valid_fraction"] for item in items)
        for track_id, items in observations.items()
    }
    areas = {
        track_id: statistics.median(item["area_fraction"] for item in items)
        for track_id, items in observations.items()
    }
    activity = {}
    for track_id, items in observations.items():
        centers = [np.asarray(item["center_normalized"], dtype=np.float64) for item in items]
        activity[track_id] = sum(
            float(np.linalg.norm(current - previous))
            for previous, current in zip(centers, centers[1:])
        ) / max(len(centers) - 1, 1)
    area_norm = _normalize(areas)
    activity_norm = _normalize(activity)
    return {
        track_id: {
            "observation_count": counts[track_id],
            "coverage_fraction": coverage[track_id],
            "mean_pose_valid_fraction": pose_quality[track_id],
            "mean_pose_selection_fraction": selection_support[track_id],
            "pose_selection_semantics": sorted({item["pose_selection_semantics"] for item in observations[track_id]}),
            "median_area_fraction": areas[track_id],
            "mean_motion_normalized": activity[track_id],
            "prior_score": (
                0.50 * coverage[track_id]
                + 0.25 * selection_support[track_id]
                + 0.10 * area_norm[track_id]
                + 0.15 * activity_norm[track_id]
            ),
        }
        for track_id in sorted(observations)
    }


def _transition(previous: dict[str, Any], current: dict[str, Any]) -> float:
    if previous["source_track_id"] == current["source_track_id"]:
        return 1.5
    distance = float(
        np.linalg.norm(
            np.asarray(previous["center_normalized"])
            - np.asarray(current["center_normalized"])
        )
    )
    return -0.40 - 3.0 * distance + 0.50 * box_iou(
        previous["bbox_px"], current["bbox_px"]
    )


def _argmax_first(values: list[float]) -> int:
    """Return the first maximum in the already-canonical candidate order."""

    return max(range(len(values)), key=lambda index: values[index])


def _select_segment(
    frames: list[dict[str, Any]], priors: dict[int, dict[str, float]], *, model_geometry: bool = True,
) -> list[dict[str, Any]]:
    emissions: list[list[float]] = []
    scores: list[list[float]] = []
    backpointers: list[list[int | None]] = []
    for frame_index, frame in enumerate(frames):
        candidates = frame["candidates"]
        current_emissions = []
        current_scores = []
        current_backpointers: list[int | None] = []
        for candidate in candidates:
            prior = priors[candidate["source_track_id"]]["prior_score"]
            emission = (
                2.0 * prior
                + 0.70 * candidate["pose_selection_fraction" if model_geometry else "pose_valid_fraction"]
                + 0.20 * candidate["detection_confidence"]
            )
            current_emissions.append(emission)
            if frame_index == 0:
                current_scores.append(emission)
                current_backpointers.append(None)
                continue
            choices = [
                previous_score + _transition(previous, candidate)
                for previous_score, previous in zip(
                    scores[-1], frames[frame_index - 1]["candidates"]
                )
            ]
            best_previous = _argmax_first(choices)
            current_scores.append(emission + choices[best_previous])
            current_backpointers.append(best_previous)
        emissions.append(current_emissions)
        scores.append(current_scores)
        backpointers.append(current_backpointers)

    # Compute max-marginal scores so best/second-best diagnostics at every frame
    # describe complete segment paths, rather than detector-list ordering or an
    # online prefix that may later lose to another path.
    suffix_scores = [
        [0.0 for _ in frame["candidates"]]
        for frame in frames
    ]
    for frame_index in range(len(frames) - 2, -1, -1):
        current_candidates = frames[frame_index]["candidates"]
        next_candidates = frames[frame_index + 1]["candidates"]
        for current_index, current in enumerate(current_candidates):
            choices = [
                _transition(current, following)
                + emissions[frame_index + 1][following_index]
                + suffix_scores[frame_index + 1][following_index]
                for following_index, following in enumerate(next_candidates)
            ]
            suffix_scores[frame_index][current_index] = choices[
                _argmax_first(choices)
            ]

    selected_indexes = [0] * len(frames)
    selected_indexes[-1] = _argmax_first(scores[-1])
    for frame_index in range(len(frames) - 1, 0, -1):
        selected_indexes[frame_index - 1] = int(
            backpointers[frame_index][selected_indexes[frame_index]]
        )

    selections = []
    for frame_index, (frame, selected_index) in enumerate(
        zip(frames, selected_indexes)
    ):
        candidate_scores = [
            prefix + suffix
            for prefix, suffix in zip(
                scores[frame_index], suffix_scores[frame_index]
            )
        ]
        best_score = candidate_scores[selected_index]
        global_best_score = max(candidate_scores)
        if not math.isclose(
            best_score,
            global_best_score,
            rel_tol=SELECTION_SCORE_TIE_REL_TOL,
            abs_tol=SELECTION_SCORE_TIE_ABS_TOL,
        ):
            raise RuntimeError("Viterbi backtrack did not select a max-marginal path")
        competitor_indexes = [
            index for index in range(len(candidate_scores)) if index != selected_index
        ]
        if competitor_indexes:
            second_index = max(
                competitor_indexes,
                key=lambda index: (
                    candidate_scores[index],
                    -index,
                ),
            )
            second_score = candidate_scores[second_index]
            margin = max(best_score - second_score, 0.0)
            ambiguous = math.isclose(
                best_score,
                second_score,
                rel_tol=SELECTION_SCORE_TIE_REL_TOL,
                abs_tol=SELECTION_SCORE_TIE_ABS_TOL,
            )
            ambiguity_status = (
                "ambiguous_score_tie" if ambiguous else "distinct_score"
            )
            competing_track_id = frame["candidates"][second_index][
                "source_track_id"
            ]
        else:
            second_score = None
            margin = None
            ambiguous = False
            ambiguity_status = "single_candidate"
            competing_track_id = None
        selections.append(
            {
                "candidate": frame["candidates"][selected_index],
                "candidate_count": len(candidate_scores),
                "best_score": best_score,
                "second_best_score": second_score,
                "score_margin": margin,
                "competing_source_track_id": competing_track_id,
                "identity_ambiguous": ambiguous,
                "identity_ambiguity_status": ambiguity_status,
            }
        )
    return selections


def _same_model_source(a: dict, b: dict) -> bool:
    left, right = a.get("model_source_binding"), b.get("model_source_binding")
    return bool(left and right and left["video_sha256"].lower() == right["video_sha256"].lower()
                and left["camera_reference_epoch"] == right["camera_reference_epoch"])


def _guard_model_identity(frames: list[dict], proposed: list[dict], anchors: list[dict]) -> list[dict]:
    """Geometry coverage cannot replace a spatially separate existing subject.

    A changed track is eligible only at a neighboring anchor's overlapping
    tracklet transition. This is conservative association, not re-identification.
    """
    result = []
    for index, (frame, proposal, anchor) in enumerate(zip(frames, proposed, anchors)):
        if not any(c["pose_selection_semantics"] == "model_geometry_coverage" for c in frame["candidates"]):
            result.append(proposal)
            continue
        candidate, previous = proposal["candidate"], anchor["candidate"]
        same_track = candidate["source_track_id"] == previous["source_track_id"]
        adjacent_tracklet = any(
            0 <= nearby < len(anchors)
            and abs(frames[nearby]["timestamp_ms"] - frame["timestamp_ms"]) <= 100
            and anchors[nearby]["candidate"]["source_track_id"] == candidate["source_track_id"]
            and _same_model_source(candidate, anchors[nearby]["candidate"])
            for nearby in (index - 1, index + 1)
        )
        overlap = (_same_model_source(candidate, previous) and adjacent_tracklet
                   and box_iou(candidate["bbox_px"], previous["bbox_px"]) >= .5)
        accepted = same_track or overlap
        result.append({**(proposal if accepted else anchor),
                       "identity_guard": "same_detected_track" if same_track else "overlapping_anchor_tracklet" if overlap else "retained_identity_anchor",
                       "score_uses_model_geometry": accepted})
    return result


def _short_model_dropouts(frames: list[dict], selected: dict[int, dict]) -> set[int]:
    """Leave brief missing-target intervals empty instead of borrowing a bystander."""
    missing = set()
    for index, frame in enumerate(frames[:-2]):
        before = selected.get(frame["processed_index"])
        if before is None or frame["processed_index"] in missing:
            continue
        anchor = before["candidate"]
        if not anchor.get("model_source_binding"):
            continue
        track = anchor["source_track_id"]
        for following in range(index + 1, len(frames)):
            later = frames[following]
            if not 0 < later["timestamp_ms"] - frame["timestamp_ms"] <= 150:
                break
            if any(candidate["source_track_id"] == track for candidate in later["candidates"]):
                after = selected.get(later["processed_index"])
                if (following > index + 1 and after is not None
                        and after["candidate"]["source_track_id"] == track
                        and _same_model_source(anchor, after["candidate"])):
                    missing.update(row["processed_index"] for row in frames[index + 1:following])
                break
    return missing


def _source_images(records: list[dict], source_video_path: str | Path | None):
    """Read source-bound pixels for clothing evidence; never use annotated video."""
    if source_video_path is None:
        yield from (None for _ in records)
        return
    import cv2
    import hashlib

    source = Path(source_video_path)
    hasher = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    declared = {str(record["frame"].get("video_sha256", "")).lower() for record in records}
    if declared != {digest}:
        raise ValueError("subject appearance source video SHA does not match frame records")
    capture = cv2.VideoCapture(str(source))
    next_index = 0
    try:
        for record in records:
            frame = record["frame"]
            target = frame["index"]
            if target < next_index:
                # Duplicate/nonmonotonic sources are not new identity evidence.
                yield None
                continue
            while next_index < target:
                if not capture.grab():
                    break
                next_index += 1
            ok, image = capture.read()
            next_index += 1
            if not ok or image.shape[:2] != (frame["height"], frame["width"]):
                yield None
            else:
                yield image
    finally:
        capture.release()


def select_primary_player_timeline(records: list[dict[str, Any]], *,
                                   source_video_path: str | Path | None = None) -> dict[str, Any]:
    source_counts = Counter((str(record["frame"].get("video_sha256", "")).lower(),
                             record["frame"].get("index")) for record in records)
    frames = [
        {
            "processed_index": int(record["frame"]["processed_index"]),
            "source_frame_index": int(record["frame"]["index"]),
            "timestamp_ms": int(record["frame"]["timestamp_ms"]),
            "candidates": _frame_candidates(record, unique_source=source_counts[(
                str(record["frame"].get("video_sha256", "")).lower(), record["frame"].get("index"))] == 1),
        }
        for record in records
    ]
    priors = _track_priors(frames)
    anchor_priors = _track_priors(frames, model_geometry=False)
    selected_by_index: dict[int, dict[str, Any]] = {}
    segment: list[dict[str, Any]] = []
    for frame in frames + [{"candidates": []}]:
        if frame["candidates"]:
            segment.append(frame)
            continue
        if segment:
            selected = _guard_model_identity(segment, _select_segment(segment, priors),
                                            _select_segment(segment, anchor_priors, model_geometry=False))
            for segment_frame, selection in zip(segment, selected):
                selected_by_index[segment_frame["processed_index"]] = selection
            segment = []
    short_dropouts = _short_model_dropouts(frames, selected_by_index)
    for index in short_dropouts:
        selected_by_index.pop(index, None)
    lock = SubjectLock()
    timeline = []
    # Native pipeline decisions take precedence over a later heuristic path.
    # An old artifact can be repaired from the exact source pixels, without
    # changing any native request's original track ID or selection epoch.
    need_pixels = any(not isinstance(record.get("subject_identity"), dict)
                      or record["subject_identity"].get("version") != SUBJECT_IDENTITY_VERSION for record in records)
    images = _source_images(records, source_video_path if need_pixels else None)
    for record, frame, image in zip(records, frames, images):
        selection = selected_by_index.get(frame["processed_index"])
        proposal = selection["candidate"] if selection is not None else None
        saved_identity = record.get("subject_identity")
        if isinstance(saved_identity, dict) and saved_identity.get("version") == SUBJECT_IDENTITY_VERSION:
            identity = (dict(saved_identity) if valid_subject_identity(saved_identity, {c["source_track_id"] for c in frame["candidates"]})
                        else lock.unknown("saved_subject_identity_invalid"))
        elif source_video_path is not None and image is None:
            identity = lock.unknown("source_pixels_unavailable")
        else:
            identity = lock.update(record.get("detections", []), timestamp_ms=frame["timestamp_ms"],
                                   width=record["frame"]["width"], height=record["frame"]["height"], image=image,
                                   camera_epoch=record.get("camera_motion", {}).get("reference_epoch"),
                                   camera_motion=record.get("camera_motion"),
                                   preferred_track_id=proposal["source_track_id"] if proposal else None,
                                   initial_ambiguous=bool(selection and selection["identity_ambiguous"]))
        matching = [candidate for candidate in frame["candidates"]
                    if candidate["source_track_id"] == identity.get("source_track_id")]
        selected = (matching[0] if len(matching) == 1 and identity.get("subject_id")
                    and identity.get("status") in {"locked", "reidentified"} else None)
        if selected is None and identity.get("subject_id"):
            identity = lock.unknown("saved_subject_detection_missing_or_conflicting")
        if selected is not None and (proposal is None or proposal["source_track_id"] != selected["source_track_id"]):
            # The max-marginal score describes a different path. Do not expose
            # it as confidence in the independently locked subject.
            selection = {"candidate": selected, "candidate_count": len(frame["candidates"]),
                         "best_score": None, "second_best_score": None, "score_margin": None,
                         "competing_source_track_id": None, "identity_ambiguous": False,
                         "identity_ambiguity_status": "single_candidate" if len(frame["candidates"]) == 1 else "distinct_score"}
        timeline.append(
            {
                "schema_version": "1.0.0",
                "processed_index": frame["processed_index"],
                "source_frame_index": frame["source_frame_index"],
                "timestamp_ms": frame["timestamp_ms"],
                "primary_player_id": 1,
                "subject_identity": identity,
                # These are copied only from the independently checked native
                # pose/request ledger; source-track changes never invent epochs.
                "selection_epoch": (selected["model_source_binding"]["selection_epoch"]
                                    if selected and selected.get("model_source_binding") else None),
                "camera_reference_epoch": (selected["model_source_binding"]["camera_reference_epoch"]
                                           if selected and selected.get("model_source_binding") else None),
                "selection_status": "selected" if selected is not None else "missing",
                "selection_reason_code": ("short_target_detection_dropout" if frame["processed_index"] in short_dropouts
                                          else identity.get("reason_code")),
                "selection_identity_guard": selection.get("identity_guard") if selection else None,
                "selection_score_uses_model_geometry": selection.get("score_uses_model_geometry", False) if selection else False,
                "source_track_id": (
                    selected["source_track_id"] if selected is not None else None
                ),
                "bbox_px": selected["bbox_px"] if selected is not None else None,
                "pose_present": bool(selected and selected["pose"] is not None),
                "keypoint_valid_fraction": (
                    safe_float(selected["pose_valid_fraction"], 6)
                    if selected is not None
                    else None
                ),
                "pose_selection_fraction": safe_float(selected["pose_selection_fraction"], 6) if selected else None,
                "pose_selection_semantics": selected["pose_selection_semantics"] if selected else None,
                "model_geometry_coverage": selected["model_geometry_coverage"] if selected else None,
                "model_geometry_reason": selected["model_geometry_reason"] if selected else None,
                "selection_evidence_version": (MODEL_SELECTION_EVIDENCE_VERSION
                    if any(candidate["pose_selection_semantics"] == "model_geometry_coverage" for candidate in frame["candidates"])
                    else "confidence-qualified-target-joints-v1.0.0"),
                "selection_prior_score": (
                    safe_float(
                        (priors if selection.get("score_uses_model_geometry", False) else anchor_priors)[selected["source_track_id"]]["prior_score"], 6
                    )
                    if selected is not None
                    else None
                ),
                "selection_candidate_count": (
                    selection["candidate_count"] if selection is not None else 0
                ),
                "selection_best_score": (
                    safe_float(selection["best_score"], 9)
                    if selection is not None and selection["best_score"] is not None
                    else None
                ),
                "selection_second_best_score": (
                    safe_float(selection["second_best_score"], 9)
                    if selection is not None
                    and selection["second_best_score"] is not None
                    else None
                ),
                "selection_score_margin": (
                    safe_float(selection["score_margin"], 9)
                    if selection is not None and selection["score_margin"] is not None
                    else None
                ),
                "selection_competing_source_track_id": (
                    selection["competing_source_track_id"]
                    if selection is not None
                    else None
                ),
                "identity_ambiguous": (
                    selection["identity_ambiguous"]
                    if selection is not None
                    else None
                ),
                "identity_ambiguity_status": (
                    selection["identity_ambiguity_status"]
                    if selection is not None
                    else "missing"
                ),
                "selection_algorithm_version": PRIMARY_PLAYER_ALGORITHM_VERSION,
            }
        )
    return {"timeline": timeline, "track_priors": priors}


def _point(pose: dict[str, Any], name: str) -> np.ndarray | None:
    point = pose["points"].get(name)
    if point is None or point[2] < KEYPOINT_CONFIDENCE_MIN:
        return None
    return np.asarray(point[:2], dtype=np.float64)


def _body_scale(pose: dict[str, Any]) -> float | None:
    ls, rs = _point(pose, "left_shoulder"), _point(pose, "right_shoulder")
    lh, rh = _point(pose, "left_hip"), _point(pose, "right_hip")
    values = []
    if ls is not None and rs is not None:
        values.append(float(np.linalg.norm(ls - rs)))
    if lh is not None and rh is not None:
        values.append(float(np.linalg.norm(lh - rh)))
    if all(point is not None for point in (ls, rs, lh, rh)):
        values.append(float(np.linalg.norm((ls + rs) / 2.0 - (lh + rh) / 2.0)))
    values = [value for value in values if value > 1e-6]
    return statistics.median(values) if values else None


def _longest_missing(values: list[bool], timestamps: list[int]) -> tuple[int, int]:
    best_count = current_count = 0
    best_ms = current_start = 0
    median_dt = int(statistics.median(np.diff(timestamps))) if len(timestamps) > 1 else 0
    for index, present in enumerate(values):
        if present:
            current_count = 0
            continue
        if current_count == 0:
            current_start = index
        current_count += 1
        if current_count > best_count:
            best_count = current_count
            best_ms = timestamps[index] - timestamps[current_start] + median_dt
    return best_count, max(best_ms, 0)


def _model_estimate_geometry(joined: list[tuple[dict, dict, dict | None]]) -> dict:
    """Count source-bound SAM geometry without changing confidence or source data.

    Every independent source frame remains in the denominator, including missing or
    rejected poses. This is finite in-frame geometry coverage, not visibility,
    keypoint confidence, identity accuracy, or a technique score.
    """
    from rallymate_vision.pose.metadata import keypoint_schema

    names = [point["name"] for point in keypoint_schema("mhr70")["keypoints"]
             if point["downstream_joint_id"] is not None]
    has_sam = any(pose.get("source") == "sam3d" for record, _, _ in joined
                  for pose in record.get("poses", []))
    # One record represents one source frame even if it contains several poses.
    # Changing the selected track cannot make a duplicate decoded frame new.
    source_counts = Counter((str(record["frame"].get("video_sha256", "")).lower(),
                             record["frame"].get("index")) for record, _, _ in joined)
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    evidence = []
    bound_frames = any_point_frames = all_point_frames = sam_pose_frames = 0
    for record, selected, _ in joined if has_sam else []:
        frame = record["frame"]
        track = selected.get("source_track_id")
        poses = [pose for pose in record.get("poses", []) if pose.get("person_track_id") == track]
        sam_pose_frames += int(any(pose.get("source") == "sam3d" for pose in poses))
        row = {"frame_index": frame["index"], "processed_index": frame["processed_index"],
               "timestamp_ms": frame["timestamp_ms"], "person_track_id": track,
               "status": "unavailable", "joint_names": []}
        reason = None
        if any(selected.get(key) != frame.get(frame_key) for key, frame_key in (
            ("processed_index", "processed_index"), ("source_frame_index", "index"),
            ("timestamp_ms", "timestamp_ms"),
        )):
            reason = "primary_frame_binding_mismatch"
        elif selected.get("selection_status") != "selected" or selected.get("identity_ambiguous") is not False:
            reason = "primary_identity_unverified"
        elif source_counts[(str(frame.get("video_sha256", "")).lower(), frame.get("index"))] != 1:
            reason = "duplicate_source_frame"
        elif len(poses) != 1:
            reason = "selected_pose_missing_or_conflicting"
        elif poses[0].get("source") != "sam3d":
            reason = "selected_pose_is_not_sam3d"
        elif len([item for item in record.get("detections", [])
                  if item.get("class_name") == "player" and item.get("track_id") == track]) != 1:
            reason = "selected_detection_missing_or_conflicting"
        if reason is None:
            bound, reason = _source_bound_model_candidate(record, track)
            if bound is not None:
                points, binding, request = bound
                bound_frames += 1
                present = [name for name in names if name in points]
                counts.update(present)
                any_point_frames += bool(present)
                all_point_frames += len(present) == len(names)
                row.update(status="ok", joint_names=present, source_binding=binding,
                           request_ledger={key: request[key] for key in
                                           ("person_track_id", "selection_epoch", "status")})
        if reason is not None:
            reasons[reason] += 1
            row["reason"] = reason
        evidence.append(row)
    frame_count = len(joined)
    source_frame_count = len(source_counts)

    def fraction(numerator: int, denominator: int) -> float | None:
        return safe_float(numerator / denominator, 6) if has_sam and denominator else None

    return {
        "version": "sam-primary-geometry-v1.1",
        "status": ("not_applicable" if not has_sam else "unavailable" if not bound_frames
                   else "available" if bound_frames == source_frame_count else "partial"),
        "observation_semantics": "source_bound_in_frame_monocular_model_estimate",
        "confidence_available": False, "visibility_verified": False,
        "accuracy_validated": False, "scoring_allowed": False,
        "used_for_primary_selection": any(selected.get("selection_evidence_version") == MODEL_SELECTION_EVIDENCE_VERSION
                                          for _, selected, _ in joined),
        "interval_frame_count": frame_count, "sam_pose_frame_count": sam_pose_frames,
        "independent_source_frame_count": source_frame_count,
        "duplicate_source_frame_count": sum(count > 1 for count in source_counts.values()),
        "source_bound_frame_count": bound_frames,
        "source_bound_frame_fraction": fraction(bound_frames, source_frame_count),
        "frames_with_any_mapped_joint": any_point_frames,
        "frames_with_all_mapped_joints": all_point_frames,
        "mapped_joint_names": names, "mapped_joint_count": len(names),
        "in_frame_joint_count": sum(counts.values()),
        "geometry_coverage_fraction": fraction(sum(counts.values()), source_frame_count * len(names)),
        "target_joint_names": list(TARGET_JOINTS),
        "target_joint_geometry_fraction": fraction(sum(counts[name] for name in TARGET_JOINTS),
                                                   source_frame_count * len(TARGET_JOINTS)),
        "coverage_denominator": "independent_video_sha256_and_frame_index_times_joint_count_including_missing_and_rejected_frames",
        "joint_coverage": {name: {"in_frame_count": counts[name],
                                   "fraction_over_interval": fraction(counts[name], source_frame_count)} for name in names},
        "reason_counts": dict(reasons), "frame_evidence": evidence,
    }


def diagnose_primary_timeline(
    records: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict[str, Any]:
    timeline_version = primary_timeline_algorithm_version(timeline)
    joined = []
    timeline_by_index = {item["processed_index"]: item for item in timeline}
    for record in records:
        timestamp = int(record["frame"]["timestamp_ms"])
        if start_ms is not None and timestamp < start_ms:
            continue
        if end_ms is not None and timestamp > end_ms:
            continue
        item = timeline_by_index[int(record["frame"]["processed_index"])]
        pose = next(
            (
                value
                for value in record.get("poses", [])
                if value.get("person_track_id") == item["source_track_id"]
            ),
            None,
        )
        joined.append((record, item, pose))
    timestamps = [int(record["frame"]["timestamp_ms"]) for record, _, _ in joined]
    selected = [item["selection_status"] == "selected" for _, item, _ in joined]
    poses_present = [pose is not None for _, _, pose in joined]
    source_ids = [item["source_track_id"] for _, item, _ in joined]
    ambiguity_assessed = any("identity_ambiguous" in item for _, item, _ in joined)
    ambiguous_frames = sorted(
        item["processed_index"]
        for _, item, _ in joined
        if item.get("identity_ambiguous") is True
    )
    assessed_ambiguity_frames = sum(
        isinstance(item.get("identity_ambiguous"), bool)
        for _, item, _ in joined
    )
    score_margins = [
        float(item["selection_score_margin"])
        for _, item, _ in joined
        if isinstance(item.get("selection_score_margin"), (int, float))
        and not isinstance(item.get("selection_score_margin"), bool)
    ]
    switches = sum(
        previous is not None and current is not None and previous != current
        for previous, current in zip(source_ids, source_ids[1:])
    )
    pose_sequence = []
    for record, item, pose in joined:
        if pose is None:
            continue
        pose_sequence.append(
            {
                "processed_index": int(record["frame"]["processed_index"]),
                "timestamp_ms": int(record["frame"]["timestamp_ms"]),
                "points": _pose_points(pose),
            }
        )
    scales = [value for pose in pose_sequence if (value := _body_scale(pose)) is not None]
    scale = statistics.median(scales) if scales else None
    swap_frames: set[int] = set()
    swap_frames_by_pair: dict[tuple[str, str], set[int]] = defaultdict(set)
    jump_by_joint: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    if scale is not None:
        for previous, current in zip(pose_sequence, pose_sequence[1:]):
            if current["processed_index"] != previous["processed_index"] + 1:
                continue
            dt = (current["timestamp_ms"] - previous["timestamp_ms"]) / 1000.0
            if dt <= 0:
                continue
            for name in TARGET_JOINTS:
                p0, p1 = _point(previous, name), _point(current, name)
                if p0 is None or p1 is None:
                    continue
                step = float(np.linalg.norm(p1 - p0) / scale)
                jump_by_joint[name].append((current["processed_index"], step, step / dt))
            for left, right in SYMMETRIC_PAIRS:
                pl, pr = _point(previous, left), _point(previous, right)
                cl, cr = _point(current, left), _point(current, right)
                if any(value is None for value in (pl, pr, cl, cr)):
                    continue
                direct = float(np.linalg.norm(cl - pl) + np.linalg.norm(cr - pr)) / scale
                swapped = float(np.linalg.norm(cl - pr) + np.linalg.norm(cr - pl)) / scale
                if swapped < direct * 0.80 and direct - swapped >= 0.05:
                    swap_frames.add(current["processed_index"])
                    swap_frames_by_pair[(left, right)].add(
                        current["processed_index"]
                    )
    jump_frames: set[int] = set()
    jump_frames_by_joint: dict[str, list[int]] = {}
    for joint_name, values in jump_by_joint.items():
        speeds = np.asarray([value[2] for value in values], dtype=np.float64)
        if speeds.size == 0:
            continue
        median = float(np.median(speeds))
        mad = float(np.median(np.abs(speeds - median)))
        threshold = median + 6.0 * 1.4826 * mad
        joint_frames = sorted(
            {
                frame
                for frame, step, speed in values
                if step >= 0.20 and speed > threshold
            }
        )
        if joint_frames:
            jump_frames_by_joint[joint_name] = joint_frames
            jump_frames.update(joint_frames)
    # The timeline may be reused across pose backends. Recompute validity from
    # the current frame artifact so diagnostics never inherit another model's
    # keypoint coverage value (for example Halpe26 while evaluating WholeBody).
    valid_values = [_valid_fraction(pose) for _, _, pose in joined if pose is not None]
    missing_frames, missing_ms = _longest_missing(poses_present, timestamps)
    quality_flags = []
    coverage = sum(selected) / max(len(joined), 1)
    pose_coverage = sum(poses_present) / max(len(joined), 1)
    if coverage < 0.80:
        quality_flags.append("primary_track_coverage_low")
    if pose_coverage < 0.75:
        quality_flags.append("primary_pose_coverage_low")
    if switches:
        quality_flags.append("source_track_switch_candidates_present")
    if swap_frames:
        quality_flags.append("left_right_swap_candidates_present")
    if jump_frames:
        quality_flags.append("keypoint_jump_candidates_present")
    if ambiguous_frames:
        quality_flags.append("primary_identity_ambiguous")
    if ambiguous_frames:
        ambiguity_status = "score_tie_detected"
        primary_identity_ambiguous: bool | None = True
    elif ambiguity_assessed:
        ambiguity_status = "no_score_tie_detected"
        primary_identity_ambiguous = False
    else:
        # Old 1.0.0 timeline records remain diagnosable.  Absence of the new
        # fields must not be silently interpreted as a proven unique identity.
        ambiguity_status = "not_assessed_legacy_timeline"
        primary_identity_ambiguous = None
    return {
        "schema_version": "1.0.0",
        "algorithm_version": timeline_version,
        "interval": {
            "start_ms": timestamps[0] if timestamps else start_ms,
            "end_ms": timestamps[-1] if timestamps else end_ms,
            "frame_count": len(joined),
        },
        "primary_player_id": 1,
        "track_coverage_fraction": safe_float(coverage, 6),
        "pose_coverage_fraction": safe_float(pose_coverage, 6),
        "source_track_ids": sorted({value for value in source_ids if value is not None}),
        "source_track_switch_candidate_count": switches,
        "confirmed_id_switch_count": None,
        "confirmed_id_switch_status": "ground_truth_required",
        "primary_identity_ambiguous": primary_identity_ambiguous,
        "primary_identity_ambiguity_status": ambiguity_status,
        "primary_identity_ambiguous_frames": ambiguous_frames,
        "identity_ambiguity_assessed_frame_count": assessed_ambiguity_frames,
        "selection_score_compared_frame_count": len(score_margins),
        "selection_score_margin_min": (
            safe_float(min(score_margins), 9) if score_margins else None
        ),
        "selection_score_margin_median": (
            safe_float(statistics.median(score_margins), 9)
            if score_margins
            else None
        ),
        "selection_score_tie_abs_tolerance": SELECTION_SCORE_TIE_ABS_TOL,
        "keypoint_valid_fraction": (
            safe_float(statistics.mean(valid_values), 6) if valid_values else None
        ),
        "keypoint_valid_fraction_semantics": "confidence_qualified_target_joints_sam_confidence_unavailable_not_geometry_coverage",
        "model_estimate_geometry": _model_estimate_geometry(joined),
        "left_right_swap_candidate_frames": sorted(swap_frames),
        "left_right_swap_candidate_joint_pairs": [
            {
                "left_joint": left,
                "right_joint": right,
                "frames": sorted(frames),
            }
            for (left, right), frames in sorted(swap_frames_by_pair.items())
        ],
        "keypoint_jump_candidate_frames": sorted(jump_frames),
        "keypoint_jump_candidate_joints": sorted(jump_frames_by_joint),
        "keypoint_jump_candidate_frames_by_joint": {
            joint_name: frames
            for joint_name, frames in sorted(jump_frames_by_joint.items())
        },
        "longest_pose_missing_frames": missing_frames,
        "longest_pose_missing_ms": missing_ms,
        "quality_flags": quality_flags,
        "diagnostic_semantics": (
            "score_tie_is_selector_ambiguity_not_identity_accuracy;"
            "heuristic_candidates_not_identity_or_keypoint_accuracy;"
            "jump_and_swap_evidence_is_joint_scoped_for_indicator_quality_gates;"
            "jump_and_swap_require_numeric_confidence_sam_not_assessed;"
            "model_estimate_geometry_is_coverage_not_confidence_visibility_or_accuracy"
        ),
    }


def build_primary_player_artifacts(
    frames_path: str | Path,
    timeline_path: str | Path,
    summary_path: str | Path,
    *,
    source_video_path: str | Path | None = None,
) -> dict[str, Any]:
    frames_file = Path(frames_path)
    records = [
        json.loads(line)
        for line in frames_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    result = select_primary_player_timeline(records, source_video_path=source_video_path)
    timeline = result["timeline"]
    timeline_file = Path(timeline_path)
    timeline_file.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in timeline),
        encoding="utf-8",
    )
    diagnostics = diagnose_primary_timeline(records, timeline)
    summary = {
        "schema_version": "1.0.0",
        "algorithm_version": PRIMARY_PLAYER_ALGORITHM_VERSION,
        "timeline": str(timeline_file),
        "diagnostics": diagnostics,
        "track_priors": {
            str(track_id): {
                **{key: safe_float(values[key], 6) for key in (
                    "observation_count", "coverage_fraction", "mean_pose_valid_fraction",
                    "mean_pose_selection_fraction", "median_area_fraction", "mean_motion_normalized", "prior_score")},
                "pose_selection_semantics": list(values["pose_selection_semantics"]),
            }
            for track_id, values in result["track_priors"].items()
        },
    }
    Path(summary_path).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
