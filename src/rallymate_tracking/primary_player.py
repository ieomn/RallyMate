from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_vision.utils import box_iou, safe_float


PRIMARY_PLAYER_ALGORITHM_VERSION = "primary-player-v0.3.0"
KEYPOINT_CONFIDENCE_MIN = 0.25
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
            "primary-player timeline mixes declared and undeclared algorithm versions"
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
    if pose is None:
        return {}
    return {
        point["name"]: (
            float(point["x_normalized"]),
            float(point["y_normalized"]),
            float(point["confidence"]),
        )
        for point in pose.get("keypoints", [])
        if isinstance(point.get("name"), str) and point.get("in_frame") is not False
    }


def _valid_fraction(pose: dict | None) -> float:
    points = _pose_points(pose)
    return sum(
        points.get(name, (0.0, 0.0, 0.0))[2] >= KEYPOINT_CONFIDENCE_MIN
        for name in TARGET_JOINTS
    ) / len(TARGET_JOINTS)


def _frame_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    frame = record["frame"]
    width = float(frame["width"])
    height = float(frame["height"])
    poses = {
        pose["person_track_id"]: pose
        for pose in record.get("poses", [])
        if isinstance(pose.get("person_track_id"), int)
    }
    candidates = []
    for detection in record.get("detections", []):
        if detection.get("class_name") != "player":
            continue
        x1, y1, x2, y2 = [float(value) for value in detection["bbox_px"]]
        track_id = int(detection["track_id"])
        pose = poses.get(track_id)
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
                "pose_valid_fraction": _valid_fraction(pose),
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


def _track_priors(frames: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
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
            "median_area_fraction": areas[track_id],
            "mean_motion_normalized": activity[track_id],
            "prior_score": (
                0.50 * coverage[track_id]
                + 0.25 * pose_quality[track_id]
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
    frames: list[dict[str, Any]], priors: dict[int, dict[str, float]]
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
                + 0.70 * candidate["pose_valid_fraction"]
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


def select_primary_player_timeline(records: list[dict[str, Any]]) -> dict[str, Any]:
    frames = [
        {
            "processed_index": int(record["frame"]["processed_index"]),
            "source_frame_index": int(record["frame"]["index"]),
            "timestamp_ms": int(record["frame"]["timestamp_ms"]),
            "candidates": _frame_candidates(record),
        }
        for record in records
    ]
    priors = _track_priors(frames)
    selected_by_index: dict[int, dict[str, Any]] = {}
    segment: list[dict[str, Any]] = []
    for frame in frames + [{"candidates": []}]:
        if frame["candidates"]:
            segment.append(frame)
            continue
        if segment:
            selected = _select_segment(segment, priors)
            for segment_frame, selection in zip(segment, selected):
                selected_by_index[segment_frame["processed_index"]] = selection
            segment = []
    timeline = []
    for frame in frames:
        selection = selected_by_index.get(frame["processed_index"])
        selected = selection["candidate"] if selection is not None else None
        timeline.append(
            {
                "schema_version": "1.0.0",
                "processed_index": frame["processed_index"],
                "source_frame_index": frame["source_frame_index"],
                "timestamp_ms": frame["timestamp_ms"],
                "primary_player_id": 1,
                "selection_status": "selected" if selected is not None else "missing",
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
                "selection_prior_score": (
                    safe_float(
                        priors[selected["source_track_id"]]["prior_score"], 6
                    )
                    if selected is not None
                    else None
                ),
                "selection_candidate_count": (
                    selection["candidate_count"] if selection is not None else 0
                ),
                "selection_best_score": (
                    safe_float(selection["best_score"], 9)
                    if selection is not None
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
            "jump_and_swap_evidence_is_joint_scoped_for_indicator_quality_gates"
        ),
    }


def build_primary_player_artifacts(
    frames_path: str | Path,
    timeline_path: str | Path,
    summary_path: str | Path,
) -> dict[str, Any]:
    frames_file = Path(frames_path)
    records = [
        json.loads(line)
        for line in frames_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    result = select_primary_player_timeline(records)
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
            str(track_id): {key: safe_float(value, 6) for key, value in values.items()}
            for track_id, values in result["track_priors"].items()
        },
    }
    Path(summary_path).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
