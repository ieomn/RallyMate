"""Deterministic, scene-free 2-D motion references from observed pose artifacts.

The mannequin's surface is illustrative. Observed trajectories and labelled
short-gap display repairs drive it; it never corrects technique or invents depth.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from collections import deque
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from rallymate_vision.display_continuity import one_display_identity, iter_display_frames
from rallymate_vision.display_score import load_display_score_policy, recover_rtmpose_display_points

from rallymate_vision.pose_playback import (
    MAX_DISPLAY_AGE_MS,
    MIN_KEYPOINT_CONFIDENCE,
    PosePlaybackError,
    build_pose_playback,
    _keypoints,
    _primary_rows,
    _rows,
    _select_pose,
    _display_identity_evidence,
    MAX_ARTIFACT_BYTES,
)


MOTION_REPLAY_VERSION = "observed-white-mannequin-v1.1.0"
CORE_JOINTS = tuple(f"{side}_{joint}" for side in ("left", "right")
                    for joint in ("shoulder", "elbow", "wrist", "hip", "knee", "ankle"))
LIMBS = tuple((f"{side}_{a}", f"{side}_{b}") for side in ("left", "right")
              for a, b in (("shoulder", "elbow"), ("elbow", "wrist"),
                           ("hip", "knee"), ("knee", "ankle")))
ARTIFACTS = {"reference_video": "reference.mp4", "first_frame": "first.png",
             "middle_frame": "middle.png", "last_frame": "last.png",
             "trajectory": "trajectory.json", "manifest": "manifest.json"}


class MotionReplayError(ValueError):
    """The source cannot provide a faithful, usable motion reference."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _verified_source_times(path: Path, indexes: set[int]) -> bool:
    """Check original metadata without retaining the entire source video."""
    remaining = set(indexes)
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            frame = row.get("frame", {})
            index = frame.get("index")
            if index in remaining:
                if frame.get("timestamp_source") != "decoder_pts":
                    return False
                remaining.remove(index)
                if not remaining:
                    return True
    return False


def _selection_observations(frames_path: Path, primary_path: Path | None, scan_limit_ms: int):
    """Stream the same raw/display inputs without retaining the whole artifact."""
    primary_iterator = _primary_rows(primary_path, allow_partial=False) if primary_path else iter(())
    current_primary = next(primary_iterator, None)
    score_policy = load_display_score_policy(frames_path)
    first_time, previous_time, previous_processed, previous_index, previous_track, epoch = None, None, -1, -1, None, 0
    try:
        for record in _rows(frames_path, allow_partial=False, maximum_bytes=MAX_ARTIFACT_BYTES):
            frame = record.get("frame", {})
            timestamp, processed, index = frame.get("timestamp_ms"), frame.get("processed_index"), frame.get("index")
            if (not _number(timestamp) or timestamp < 0 or not isinstance(processed, int) or isinstance(processed, bool)
                    or not isinstance(index, int) or isinstance(index, bool) or processed <= previous_processed or index <= previous_index
                    or previous_time is not None and timestamp <= previous_time):
                raise MotionReplayError("motion_replay_window_selection_source_invalid")
            if first_time is None:
                first_time = timestamp
            if timestamp - first_time > scan_limit_ms:
                break
            while current_primary is not None and current_primary["processed_index"] < processed:
                current_primary = next(primary_iterator, None)
            primary = current_primary if current_primary and current_primary["processed_index"] == processed else None
            pose, selection, reason = _select_pose(record, frame, primary, primary_path is not None)
            width, height = frame.get("width"), frame.get("height")
            valid = (isinstance(width, int) and not isinstance(width, bool) and 0 < width <= 65536
                     and isinstance(height, int) and not isinstance(height, bool) and 0 < height <= 65536)
            points = _keypoints(pose, width, height) if pose is not None and valid else []
            verified = frame.get("timestamp_source") == "decoder_pts"
            if not verified:
                points, reason = [], "source_timestamp_unverified"
            elif not valid:
                points, reason = [], "source_dimensions_unavailable"
            elif pose is not None and not points:
                reason = "confident_in_frame_keypoints_unavailable"
            track = pose["person_track_id"] if pose is not None else None
            if track != previous_track or previous_time is not None and timestamp - previous_time > MAX_DISPLAY_AGE_MS or processed > previous_processed + 1:
                epoch += 1
            row = {"frame_index": index, "processed_index": processed, "timestamp_ms": timestamp,
                   "valid_until_ms": timestamp + MAX_DISPLAY_AGE_MS, "person_track_id": track,
                   "selection_epoch": epoch, "selection_status": selection, "width": width if valid else None,
                   "height": height if valid else None, "keypoints": points, "reason_code": reason}
            recovered = recover_rtmpose_display_points(pose, width, height, frame_job_id=record.get("job_id"), policy=score_policy) if pose is not None and valid and verified else []
            yield row, verified, recovered, _display_identity_evidence(record, primary)
            previous_time, previous_processed, previous_index, previous_track = timestamp, processed, index, track
    finally:
        close = getattr(primary_iterator, "close", None)
        if close:
            close()


def select_motion_replay_window(
    frames_path: Path,
    primary_timeline_path: Path | None = None,
    duration_ms: int = 6000,
    *,
    scan_limit_ms: int = 180_000,
) -> dict[str, Any]:
    """Select an active continuous display window using bounded source context.

    Candidate quality uses only existing 2-D observations. The returned motion
    extent is a selection diagnostic, never a calibrated technique measurement.
    At most one window plus its boundary observation is retained while scanning.
    """
    if (not isinstance(duration_ms, int) or isinstance(duration_ms, bool)
            or not 2000 <= duration_ms <= 10_000 or not isinstance(scan_limit_ms, int)
            or isinstance(scan_limit_ms, bool) or scan_limit_ms < duration_ms):
        raise MotionReplayError("motion_replay_invalid_selection_window")
    primary_path = Path(primary_timeline_path) if primary_timeline_path is not None else None
    if primary_path is not None and not primary_path.is_file():
        raise MotionReplayError("motion_replay_primary_timeline_missing")
    pending: deque[dict[str, Any]] = deque()
    previous = None
    best = None
    observations = _selection_observations(Path(frames_path), primary_path, scan_limit_ms)
    try:
        for raw, display in iter_display_frames(observations):
            timestamp, processed = display["timestamp_ms"], display["processed_index"]
            width, height = display["width"], display["height"]
            points = {point["name"]: np.array([point["x"] * width, point["y"] * height])
                      for point in display["keypoints"]}
            if not all(name in points for name in CORE_JOINTS):
                pending.clear()
                previous = None
                continue
            identity = (display["person_track_id"], width, height)
            if previous is not None and (identity != previous["identity"]
                    or not 0 < timestamp - previous["timestamp"] <= MAX_DISPLAY_AGE_MS
                    or processed != previous["processed"] + 1
                    or display["frame_index"] != previous["display"]["frame_index"] + 1
                    or not one_display_identity([previous["display"], display])):
                pending.clear()
            torso = float(np.linalg.norm((points["left_shoulder"] + points["right_shoulder"]
                                          - points["left_hip"] - points["right_hip"]) / 2))
            if torso < 2:
                pending.clear()
                previous = None
                continue
            current = {"timestamp": timestamp, "processed": processed, "identity": identity,
                       "torso": torso, "points": points, "display": display,
                       "raw_core_fraction": sum(any(point["name"] == name for point in raw["keypoints"]) for name in CORE_JOINTS) / len(CORE_JOINTS)}
            pending.append(current)
            previous = current
            if len(pending) > 1001:
                pending.popleft()
            if timestamp - pending[0]["timestamp"] < duration_ms:
                continue
            start = int(pending[0]["timestamp"])
            window = [row for row in pending if row["timestamp"] < start + duration_ms]
            if len(window) <= 600:
                scale = float(np.median([row["torso"] for row in window]))
                extents = []
                for joint in ("left_wrist", "right_wrist", "left_ankle", "right_ankle"):
                    series = np.array([row["points"][joint] for row in window])
                    extents.append(float(np.linalg.norm(np.ptp(series, axis=0))) / scale)
                extent = max(extents)
                if extent >= .2 and (best is None or extent > best["motion_extent_body"]):
                    best = {"start_ms": start, "duration_ms": duration_ms,
                            "person_track_id": identity[0], "core_joint_coverage_fraction": float(np.mean([row["raw_core_fraction"] for row in window])),
                            "display_core_joint_coverage_fraction": 1.0,
                            "motion_extent_body": extent, "selection_method": "continuous_display_active_window",
                            "scanned_time_limit_ms": scan_limit_ms}
            while pending and pending[0]["timestamp"] < start + 1000:
                pending.popleft()
    except (PosePlaybackError, OSError, ValueError, TypeError, KeyError) as exc:
        raise MotionReplayError("motion_replay_window_selection_source_invalid") from exc
    finally:
        close = getattr(observations, "close", None)
        if close is not None:
            close()
    if best is None:
        raise MotionReplayError("motion_replay_no_continuous_active_window")
    return best


def build_motion_replay(
    frames_path: Path,
    *,
    primary_timeline_path: Path | None = None,
    start_ms: int = 0,
    duration_ms: int = 6000,
    fps: float = 24,
    width: int = 1280,
    height: int = 720,
) -> dict[str, Any]:
    """Build a bounded trajectory and exact output-to-observation frame map.

    One uniform scale and translation fit the complete observed movement. No
    per-frame recentering, smoothing or retiming is applied. Display-only short
    joint gaps use the same explicit, bounded repairs as the skeleton overlay.
    Constant-rate output holds the latest still-valid observation for at most
    100 ms, preserving the original presentation timestamps in the frame map.
    """
    if (not isinstance(start_ms, int) or isinstance(start_ms, bool) or start_ms < 0
            or not isinstance(duration_ms, int) or isinstance(duration_ms, bool)
            or not 2000 <= duration_ms <= 10_000):
        raise MotionReplayError("motion_replay_invalid_time_window")
    if not _number(fps) or not 23.976 <= fps <= 60:
        raise MotionReplayError("motion_replay_fps_must_be_23.976_to_60")
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 256 or n > 2048 or n % 2
           for n in (width, height)) or width * height > 2_073_600:
        raise MotionReplayError("motion_replay_invalid_output_dimensions")
    frames_path = Path(frames_path)
    primary = Path(primary_timeline_path) if primary_timeline_path is not None else None
    if primary is not None and not primary.is_file():
        raise MotionReplayError("motion_replay_primary_timeline_missing")
    try:
        preview = build_pose_playback(frames_path, primary_timeline_path=primary,
                                      start_ms=start_ms, duration_ms=duration_ms, sample_limit=600)
    except (PosePlaybackError, OSError, ValueError) as exc:
        raise MotionReplayError("motion_replay_source_invalid") from exc
    observations = preview["frames"]
    display_observations = preview["display_frames"]
    if not observations or preview["source"]["is_sampled"]:
        raise MotionReplayError("motion_replay_empty_or_sampled_source")
    if not _verified_source_times(frames_path, {row["frame_index"] for row in observations}):
        raise MotionReplayError("motion_replay_source_timestamp_unverified")
    if not one_display_identity(display_observations):
        raise MotionReplayError("motion_replay_requires_one_continuous_identity")
    display_track = next(row["person_track_id"] for row in display_observations if row["keypoints"])
    source_width, source_height = preview["source"]["width"], preview["source"]["height"]
    if not source_width or not source_height:
        raise MotionReplayError("motion_replay_source_dimensions_unavailable")
    raw_core_fractions = [sum(any(point["name"] == name for point in row["keypoints"]) for name in CORE_JOINTS) / len(CORE_JOINTS)
                          for row in observations]
    core_fractions, torso_lengths, all_points = [], [], []
    for row in display_observations:
        points = {p["name"]: np.array([p["x"] * source_width, p["y"] * source_height])
                  for p in row["keypoints"]}
        core_fractions.append(sum(name in points for name in CORE_JOINTS) / len(CORE_JOINTS))
        all_points.extend(points.values())
        names = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
        if all(name in points for name in names):
            shoulders = (points[names[0]] + points[names[1]]) / 2
            hips = (points[names[2]] + points[names[3]]) / 2
            torso_lengths.append(float(np.linalg.norm(shoulders - hips)))
    if not all_points or not torso_lengths or np.median(torso_lengths) < 2:
        raise MotionReplayError("motion_replay_body_scale_unavailable")
    if np.mean(core_fractions) < .85:
        raise MotionReplayError("motion_replay_insufficient_core_joint_coverage")
    body_scale = float(np.median(torso_lengths))
    xy = np.asarray(all_points)
    lower, upper = xy.min(axis=0), xy.max(axis=0)
    # Padding includes the illustrative head and capsule radii, not new joints.
    padding = max(body_scale * .25, 2.0)
    lower, upper = lower - padding, upper + padding
    extent = upper - lower
    if not np.isfinite(extent).all() or min(extent) <= 0:
        raise MotionReplayError("motion_replay_body_extent_unavailable")
    scale = min(width * .86 / extent[0], height * .86 / extent[1])
    offset = np.array([width, height]) / 2 - (lower + upper) / 2 * scale
    frame_count = int(math.ceil(duration_ms * float(fps) / 1000 - 1e-9))
    timestamps = [row["timestamp_ms"] for row in observations]
    frame_map, rendered_frames, display_rendered_frames, projection_errors = [], [], [], []
    for index in range(frame_count):
        output_ms = index * 1000 / float(fps)
        source_ms = start_ms + output_ms
        selected = bisect.bisect_right(timestamps, source_ms + .5 + 1e-8) - 1
        row = observations[selected] if selected >= 0 else None
        display_row = display_observations[selected] if selected >= 0 else None
        if row is not None and (source_ms >= row["valid_until_ms"] or not row["keypoints"]):
            row = None
        if display_row is not None and (source_ms >= display_row["valid_until_ms"] or not display_row["keypoints"]):
            display_row = None
        points = []
        if row is not None:
            for point in row["keypoints"]:
                source_xy = np.array([point["x"] * source_width, point["y"] * source_height])
                mapped = source_xy * scale + offset
                projection_errors.append(float(np.linalg.norm(np.rint(mapped) - mapped)))
                points.append({**point, "x_px": round(float(mapped[0]), 6),
                               "y_px": round(float(mapped[1]), 6)})
        mapping = {"output_frame_index": index, "output_timestamp_ms": round(output_ms, 6),
                   "requested_source_timestamp_ms": round(source_ms, 6),
                   "source_frame_index": row["frame_index"] if row else None,
                   "source_timestamp_ms": row["timestamp_ms"] if row else None,
                   "person_track_id": row["person_track_id"] if row else None,
                   "selection_epoch": row["selection_epoch"] if row else None,
                   "observation_age_ms": round(max(0, source_ms - row["timestamp_ms"]), 6) if row else None,
                   "source_timestamp_offset_ms": round(row["timestamp_ms"] - source_ms, 6) if row else None,
                   "keypoint_count": len(points)}
        frame_map.append(mapping)
        rendered_frames.append({**mapping, "keypoints": points})
        display_points = []
        if display_row is not None:
            for point in display_row["keypoints"]:
                mapped = np.array([point["x"] * source_width, point["y"] * source_height]) * scale + offset
                display_points.append({**point, "x_px": round(float(mapped[0]), 6), "y_px": round(float(mapped[1]), 6)})
        display_mapping = {**mapping, "person_track_id": display_row["person_track_id"] if display_row else None,
                           "selection_epoch": display_row["selection_epoch"] if display_row else None,
                           "source_frame_index": display_row["frame_index"] if display_row else None,
                           "source_timestamp_ms": display_row["timestamp_ms"] if display_row else None,
                           "observation_age_ms": round(max(0, source_ms - display_row["timestamp_ms"]), 6) if display_row else None,
                           "source_timestamp_offset_ms": round(display_row["timestamp_ms"] - source_ms, 6) if display_row else None,
                           "keypoint_count": len(display_points)}
        if display_row is not None:
            for key in ("observed_person_track_id", "observed_selection_epoch", "identity_repair"):
                if key in display_row:
                    display_mapping[key] = display_row[key]
        display_rendered_frames.append({**display_mapping, "keypoints": display_points})
    visible_fraction = sum(bool(row["keypoints"]) for row in display_rendered_frames) / frame_count
    if visible_fraction < .95:
        raise MotionReplayError("motion_replay_insufficient_output_coverage")
    missing_joints = {name: sum(not any(p["name"] == name for p in row["keypoints"])
                                 for row in observations) for name in CORE_JOINTS}
    return {
        "schema_version": "1.0.0", "version": MOTION_REPLAY_VERSION,
        "result_kind": "deterministic_2d_motion_replay",
        "source": {"frames_file": frames_path.name, "frames_sha256": _sha256(frames_path),
                   "primary_timeline_file": primary.name if primary else None,
                   "primary_timeline_sha256": _sha256(primary) if primary else None,
                   "start_ms": start_ms, "duration_ms": duration_ms,
                   "width": source_width, "height": source_height,
                   "timestamp_source": "decoder_pts", "observed_frame_count": len(observations),
                   "selection_source": preview["source"]["selection_source"],
                   "person_track_id": display_track},
        "render": {"width": width, "height": height, "fps": float(fps),
                   "frame_count": frame_count, "encoded_duration_ms": frame_count * 1000 / float(fps),
                   "style": "white_capsule_mannequin_on_neutral_background",
                   "coordinate_space": "output_pixels", "source_coordinate_space": "normalized_frame_0_1",
                   "transform": {"type": "fixed_isotropic_scale_translation",
                                 "scale": float(scale), "offset_x_px": float(offset[0]),
                                 "offset_y_px": float(offset[1])},
                   "body_scale_source_px": body_scale,
                   "interpolation": "display_only_bounded_short_gaps", "smoothing": "none",
                   "display_continuity": preview["display_continuity"],
                   "time_mapping": "original_timestamps_with_half_millisecond_quantization_tolerance",
                   "max_display_age_ms": MAX_DISPLAY_AGE_MS,
                   "timestamp_rounding_tolerance_ms": .5,
                   "min_keypoint_confidence": MIN_KEYPOINT_CONFIDENCE,
                   "is_3d_reconstruction": False},
        "quality": {"status": "usable_observed_reference",
                    "core_joint_coverage_fraction": round(float(np.mean(raw_core_fractions)), 6),
                    "raw_core_joint_coverage_fraction": round(float(np.mean(raw_core_fractions)), 6),
                    "display_core_joint_coverage_fraction": round(float(np.mean(core_fractions)), 6),
                    "fully_observed_core_frame_fraction": round(sum(n == 1 for n in raw_core_fractions) / len(raw_core_fractions), 6),
                    "fully_displayed_core_frame_fraction": round(sum(n == 1 for n in core_fractions) / len(core_fractions), 6),
                    "visible_output_frame_fraction": round(visible_fraction, 6),
                    "missing_core_joint_frames": missing_joints,
                    "max_raster_rounding_error_px": round(max(projection_errors, default=0), 6),
                    "max_observation_age_ms": max(row["observation_age_ms"] or 0 for row in frame_map),
                    "source_accuracy_verified": False,
                    "semantics": "observed_2d_estimates_with_explicit_display_only_repairs_not_verified_ground_truth"},
        "observations": observations, "frames": rendered_frames, "display_frames": display_rendered_frames,
        "display_observations": display_observations, "frame_map": frame_map,
    }


def render_motion_frame(frame: dict[str, Any], render: dict[str, Any]) -> np.ndarray:
    """Draw an opaque mannequin, hiding any limb with a missing endpoint."""
    width, height = render["width"], render["height"]
    canvas = np.full((height, width, 3), (47, 43, 40), dtype=np.uint8)
    points = {p["name"]: (int(round(p["x_px"])), int(round(p["y_px"]))) for p in frame["keypoints"]}
    if not points:
        return canvas
    body_size = render["body_scale_source_px"] * render["transform"]["scale"]
    radius = max(3, int(round(body_size * .078)))
    shadow, edge, white = (26, 25, 24), (167, 172, 175), (234, 237, 239)

    def capsule(a: tuple[int, int], b: tuple[int, int], r: int) -> None:
        cv2.line(canvas, (a[0] + 3, a[1] + 4), (b[0] + 3, b[1] + 4), shadow, 2 * r + 5, cv2.LINE_AA)
        for color, size in ((edge, r + 1), (white, r)):
            cv2.line(canvas, a, b, color, 2 * size, cv2.LINE_AA)
            cv2.circle(canvas, a, size, color, -1, cv2.LINE_AA)
            cv2.circle(canvas, b, size, color, -1, cv2.LINE_AA)
        cv2.line(canvas, (a[0] - max(1, r // 3), a[1]),
                 (b[0] - max(1, r // 3), b[1]), (250, 251, 251), max(1, r // 3), cv2.LINE_AA)

    for a, b in LIMBS:
        if a in points and b in points:
            capsule(points[a], points[b], radius if "hip" not in a else int(radius * 1.2))
    torso = ("left_shoulder", "right_shoulder", "right_hip", "left_hip")
    if all(name in points for name in torso):
        polygon = np.array([points[name] for name in torso], dtype=np.int32)
        cv2.fillPoly(canvas, [polygon + (3, 4)], shadow, cv2.LINE_AA)
        cv2.fillPoly(canvas, [polygon], white, cv2.LINE_AA)
        cv2.polylines(canvas, [polygon], True, edge, 2, cv2.LINE_AA)
    if "neck" in points and "head" in points:
        capsule(points["neck"], points["head"], max(3, int(radius * .75)))
    head = points.get("head") or points.get("nose")
    if head is not None:
        axes = (max(5, int(body_size * .12)), max(6, int(body_size * .16)))
        cv2.ellipse(canvas, (head[0] + 3, head[1] + 4), axes, 0, 0, 360, shadow, -1, cv2.LINE_AA)
        cv2.ellipse(canvas, head, axes, 0, 0, 360, white, -1, cv2.LINE_AA)
        cv2.ellipse(canvas, head, axes, 0, 0, 360, edge, 1, cv2.LINE_AA)
    for side in ("left", "right"):
        ankle = f"{side}_ankle"
        foot = [points[n] for n in (f"{side}_heel", f"{side}_big_toe", f"{side}_small_toe") if n in points]
        if len(foot) == 3:
            cv2.fillPoly(canvas, [np.array(foot, dtype=np.int32)], white, cv2.LINE_AA)
        for name in (f"{side}_heel", f"{side}_big_toe", f"{side}_small_toe"):
            if ankle in points and name in points:
                capsule(points[ankle], points[name], max(2, radius // 2))
    for name in CORE_JOINTS:
        if name in points:
            cv2.circle(canvas, points[name], max(3, int(radius * .82)), (208, 216, 221), -1, cv2.LINE_AA)
            cv2.circle(canvas, points[name], 2, (249, 250, 251), -1, cv2.LINE_AA)
    return canvas


def _find_ffmpeg(explicit: str | Path | None) -> str:
    if explicit is not None:
        if Path(explicit).is_file():
            return str(explicit)
        raise MotionReplayError("motion_replay_ffmpeg_missing")
    result = shutil.which("ffmpeg")
    if result:
        return result
    try:
        import imageio_ffmpeg
        result = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(result).is_file():
            return result
    except (ImportError, OSError, RuntimeError, ValueError):
        pass
    raise MotionReplayError("motion_replay_h264_encoder_unavailable")


def export_motion_replay(
    frames_path: Path,
    output_dir: Path,
    *,
    primary_timeline_path: Path | None = None,
    start_ms: int = 0,
    duration_ms: int = 6000,
    fps: float = 24,
    width: int = 1280,
    height: int = 720,
    ffmpeg_path: str | Path | None = None,
) -> dict[str, Any]:
    """Export H.264, three keyframes, source trajectory and an auditable manifest.

    A failed H.264 encode is an error, never a successful empty reference or a
    silently substituted codec. Output uses basenames, not host/private paths.
    """
    encoder = _find_ffmpeg(ffmpeg_path)
    trajectory = build_motion_replay(frames_path, primary_timeline_path=primary_timeline_path,
                                     start_ms=start_ms, duration_ms=duration_ms, fps=fps,
                                     width=width, height=height)
    return _export_motion_assets(trajectory, output_dir, encoder)


def export_perturbed_motion_replay(
    trajectory_path: Path,
    output_dir: Path,
    *,
    joint: str = "right_wrist",
    start_ms: int = 2500,
    duration_ms: int = 300,
    offset_x_px: float = 50,
    offset_y_px: float = 0,
    ffmpeg_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a conspicuously labelled synthetic A/B control, never source data.

    A sin-squared pulse moves one joint inside the original fixed transform.
    Its phase uses the selected source observation time, not output frame time.
    """
    trajectory_path = Path(trajectory_path)
    if trajectory_path.stat().st_size > 64 * 1024 * 1024:
        raise MotionReplayError("motion_replay_trajectory_too_large")
    original = json.loads(trajectory_path.read_text(encoding="utf-8"))
    if original.get("result_kind") != "deterministic_2d_motion_replay" or joint not in CORE_JOINTS:
        raise MotionReplayError("motion_replay_invalid_perturbation_source")
    if (not isinstance(start_ms, int) or isinstance(start_ms, bool) or start_ms < 0
            or not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms <= 0
            or start_ms + duration_ms > original["source"]["duration_ms"]
            or not all(_number(n) for n in (offset_x_px, offset_y_px))
            or math.hypot(offset_x_px, offset_y_px) <= 0):
        raise MotionReplayError("motion_replay_invalid_perturbation")
    trajectory = copy.deepcopy(original)
    transform = trajectory["render"]["transform"]
    scale, source = transform["scale"], trajectory["source"]

    def offset(timestamp: float) -> tuple[float, float]:
        phase = (timestamp - source["start_ms"] - start_ms) / duration_ms
        weight = math.sin(math.pi * phase) ** 2 if 0 < phase < 1 else 0.0
        return offset_x_px * weight, offset_y_px * weight

    affected = []
    for observation in [*trajectory["observations"], *trajectory.get("display_observations", [])]:
        dx, dy = offset(observation["timestamp_ms"])
        for point in observation["keypoints"]:
            if point["name"] == joint:
                point["x"] += dx / scale / source["width"]
                point["y"] += dy / scale / source["height"]
                if not 0 <= point["x"] <= 1 or not 0 <= point["y"] <= 1:
                    raise MotionReplayError("motion_replay_perturbation_out_of_source_frame")
    for frame in [*trajectory["frames"], *trajectory.get("display_frames", [])]:
        if frame["source_timestamp_ms"] is None:
            continue
        dx, dy = offset(frame["source_timestamp_ms"])
        for point in frame["keypoints"]:
            if point["name"] == joint:
                point["x"] += dx / scale / source["width"]
                point["y"] += dy / scale / source["height"]
                point["x_px"] += dx
                point["y_px"] += dy
                if not 0 <= point["x_px"] < trajectory["render"]["width"] or not 0 <= point["y_px"] < trajectory["render"]["height"]:
                    raise MotionReplayError("motion_replay_perturbation_out_of_output_frame")
                if dx or dy:
                    if not any(item["output_frame_index"] == frame["output_frame_index"] for item in affected):
                        affected.append({"output_frame_index": frame["output_frame_index"],
                                         "dx_px": dx, "dy_px": dy})
    if not affected:
        raise MotionReplayError("motion_replay_perturbation_joint_unobserved")
    trajectory["result_kind"] = "synthetic_motion_reference_test"
    trajectory["synthetic_perturbation"] = {
        "is_personal_motion_record": False, "baseline_trajectory_sha256": _sha256(trajectory_path),
        "joint": joint, "start_ms": start_ms, "duration_ms": duration_ms,
        "offset_x_px": offset_x_px, "offset_y_px": offset_y_px,
        "envelope": "sin_squared_source_observation_time", "fixed_transform_preserved": True,
        "affected_output_frames": affected,
    }
    trajectory["quality"]["status"] = "synthetic_perturbation_test_only"
    trajectory["quality"]["semantics"] = "explicit_synthetic_control_not_personal_motion_evidence"
    return _export_motion_assets(trajectory, output_dir, _find_ffmpeg(ffmpeg_path))


def _export_motion_assets(trajectory: dict[str, Any], output_dir: Path, encoder: str) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = [name for name in ARTIFACTS.values() if (output_dir / name).exists()]
    if existing:
        raise MotionReplayError("motion_replay_output_already_exists")
    render = trajectory["render"]
    video_frames = trajectory.get("display_frames", trajectory["frames"])
    width, height, fps = render["width"], render["height"], render["fps"]
    keyframes = {0: "first.png", len(video_frames) // 2: "middle.png",
                 len(video_frames) - 1: "last.png"}
    with tempfile.TemporaryDirectory(prefix=".motion-replay-", dir=output_dir) as temporary:
        staging = Path(temporary)
        command = [encoder, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                   "-pix_fmt", "bgr24", "-s:v", f"{width}x{height}", "-r", str(fps),
                   "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                   "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(staging / "reference.mp4")]
        process = None
        try:
            with tempfile.TemporaryFile() as error_stream:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=error_stream)
                assert process.stdin is not None
                for index, frame in enumerate(video_frames):
                    pixels = render_motion_frame(frame, render)
                    process.stdin.write(pixels.tobytes())
                    if index in keyframes:
                        ok, encoded = cv2.imencode(".png", pixels)
                        if not ok:
                            raise MotionReplayError("motion_replay_keyframe_encode_failed")
                        (staging / keyframes[index]).write_bytes(encoded.tobytes())
                process.stdin.close()
                if process.wait(timeout=90) != 0:
                    raise MotionReplayError("motion_replay_h264_encode_failed")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MotionReplayError("motion_replay_h264_encode_failed") from exc
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if process is not None and process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        capture = cv2.VideoCapture(str(staging / "reference.mp4"))
        try:
            ok, decoded = capture.read()
            count = round(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            encoded_fps = capture.get(cv2.CAP_PROP_FPS)
            codec_number = int(capture.get(cv2.CAP_PROP_FOURCC))
            codec = "".join(chr((codec_number >> 8 * n) & 255) for n in range(4))
            if (not ok or decoded.shape[:2] != (height, width) or count != render["frame_count"]
                    or abs(encoded_fps - fps) > .02 or codec not in {"avc1", "h264", "H264"}):
                raise MotionReplayError("motion_replay_encoded_video_verification_failed")
        finally:
            capture.release()
        (staging / "trajectory.json").write_text(json.dumps(trajectory, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        manifest = {key: value for key, value in trajectory.items()
                    if key not in {"observations", "frames", "display_observations", "display_frames"}}
        manifest["artifacts"] = dict(ARTIFACTS)
        manifest["video"] = {"codec": "h264", "pixel_format": "yuv420p", "fps": encoded_fps,
                             "width": width, "height": height, "frame_count": count,
                             "duration_ms": count * 1000 / encoded_fps, "audio": False}
        manifest["artifact_checksums"] = {name: _sha256(staging / name) for name in ARTIFACTS.values() if name != "manifest.json"}
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        for name in ARTIFACTS.values():
            (staging / name).replace(output_dir / name)
    return manifest
