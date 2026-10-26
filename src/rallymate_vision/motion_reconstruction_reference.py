"""Export a fixed crop of original pixels, independently of pose rendering.

Landmarks determine only a conservative, clip-wide crop. They never replace,
draw over, or interpolate the filmed person. The caller must bind the original
video to its analysis provenance with an expected SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import cv2


REFERENCE_VERSION = "original-person-crop-v1.2.0"
MAX_TRAJECTORY_BYTES = 64 * 1024 * 1024
ARTIFACT_NAMES = ("reference.mp4", "first.png", "middle.png", "last.png", "manifest.json")


class ReconstructionReferenceError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def build_reference_plan(trajectory: dict, *, source_width: int, source_height: int,
                         source_fps: float, source_frame_count: int,
                         crop: tuple[int, int, int, int] | None = None,
                         horizontal_margin_px: int = 100, vertical_margin_px: int = 80) -> dict:
    """Validate a six-second, one-person mapping and choose one fixed crop.

    A skipped/duplicated source frame is rejected rather than silently changing
    speed. Repaired landmarks may enlarge the crop but never synthesize pixels.
    The margins reserve some racket space; they are not a racket detector.
    """
    if (not isinstance(trajectory, dict) or trajectory.get("result_kind") != "deterministic_2d_motion_replay"
            or "synthetic_perturbation" in trajectory):
        raise ReconstructionReferenceError("reference_invalid_trajectory")
    source, render = trajectory.get("source"), trajectory.get("render")
    if not isinstance(source, dict) or not isinstance(render, dict):
        raise ReconstructionReferenceError("reference_invalid_trajectory")
    if (source.get("width") != source_width or source.get("height") != source_height
            or source.get("timestamp_source") != "decoder_pts"):
        raise ReconstructionReferenceError("reference_source_geometry_mismatch")
    fps, start, duration = render.get("fps"), source.get("start_ms"), source.get("duration_ms")
    if (not _number(fps) or not _number(source_fps) or not 1 <= source_fps <= 120
            or abs(fps - source_fps) > .001 or not _number(start) or start < 0 or duration != 6000):
        raise ReconstructionReferenceError("reference_timebase_mismatch")
    if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000
           for value in (horizontal_margin_px, vertical_margin_px)):
        raise ReconstructionReferenceError("reference_invalid_margin")
    rows_key = "display_frames" if "display_frames" in trajectory else "frames"
    rows = trajectory.get(rows_key)
    if not isinstance(rows, list) or not rows or len(rows) != round(duration * fps / 1000):
        raise ReconstructionReferenceError("reference_frame_mapping_incomplete")
    track = source.get("person_track_id")
    if isinstance(track, bool) or not isinstance(track, int) or track <= 0:
        raise ReconstructionReferenceError("reference_identity_unavailable")
    positions, frame_map = [], []
    first_index = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("person_track_id") != track:
            raise ReconstructionReferenceError("reference_identity_changed")
        source_index, timestamp = row.get("source_frame_index"), row.get("source_timestamp_ms")
        if (isinstance(source_index, bool) or not isinstance(source_index, int)
                or not 0 <= source_index < source_frame_count or not _number(timestamp)
                or row.get("output_frame_index") != index or not _number(row.get("output_timestamp_ms"))):
            raise ReconstructionReferenceError("reference_frame_mapping_invalid")
        if first_index is None:
            first_index = source_index
        if (source_index != first_index + index or abs(timestamp - source_index * 1000 / source_fps) > 1.0
                or abs(row["output_timestamp_ms"] - index * 1000 / fps) > 1.0
                or abs(timestamp - start - index * 1000 / fps) > 1.0):
            raise ReconstructionReferenceError("reference_frame_mapping_changes_timing")
        points = row.get("keypoints")
        if not isinstance(points, list) or not points:
            raise ReconstructionReferenceError("reference_target_bounds_unavailable")
        for point in points:
            if (not isinstance(point, dict) or not _number(point.get("x")) or not _number(point.get("y"))
                    or not 0 <= point["x"] <= 1 or not 0 <= point["y"] <= 1):
                raise ReconstructionReferenceError("reference_target_bounds_invalid")
            positions.append((point["x"] * source_width, point["y"] * source_height))
        frame_map.append({"output_frame_index": index, "output_timestamp_ms": round(index * 1000 / fps, 6),
                          "source_frame_index": source_index, "source_timestamp_ms": timestamp,
                          "person_track_id": track})
    left, top = min(x for x, _ in positions), min(y for _, y in positions)
    right, bottom = max(x for x, _ in positions), max(y for _, y in positions)
    if crop is None:
        x0, y0 = max(0, math.floor(left - horizontal_margin_px)), max(0, math.floor(top - vertical_margin_px))
        x1, y1 = min(source_width, math.ceil(right + horizontal_margin_px)), min(source_height, math.ceil(bottom + vertical_margin_px))
        # Expand symmetrically to provider-friendly dimensions without resizing
        # the person, keeping the fixed crop completely inside source pixels.
        for axis, lower, upper, limit in ((0, x0, x1, source_width), (1, y0, y1, source_height)):
            target = max(256, upper - lower)
            target += target % 2
            if target > limit:
                raise ReconstructionReferenceError("reference_source_too_small")
            lower = max(0, min(limit - target, (lower + upper - target) // 2))
            if axis == 0:
                x0, x1 = lower, lower + target
            else:
                y0, y1 = lower, lower + target
        # Keep provider limits without changing any source pixels. Expand the
        # shorter axis only; shrinking could cut off the athlete or racket.
        width, height = x1 - x0, y1 - y0
        if width > 2.5 * height:
            target = math.ceil(width / 2.5)
            target += target % 2
            if target > source_height:
                raise ReconstructionReferenceError("reference_geometry_unsupported")
            y0 = max(0, min(source_height - target, (y0 + y1 - target) // 2))
            y1 = y0 + target
        elif width < .4 * height:
            target = math.ceil(.4 * height)
            target += target % 2
            if target > source_width:
                raise ReconstructionReferenceError("reference_geometry_unsupported")
            x0 = max(0, min(source_width - target, (x0 + x1 - target) // 2))
            x1 = x0 + target
        crop = (x0, y0, x1 - x0, y1 - y0)
    if (not isinstance(crop, (tuple, list)) or len(crop) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in crop)):
        raise ReconstructionReferenceError("reference_invalid_crop")
    x, y, width, height = crop
    if (x < 0 or y < 0 or width < 256 or height < 256 or width % 2 or height % 2
            or x + width > source_width or y + height > source_height):
        raise ReconstructionReferenceError("reference_crop_out_of_bounds")
    if width > 5760 or height > 5760 or not .4 <= width / height <= 2.5:
        raise ReconstructionReferenceError("reference_geometry_unsupported")
    if not all(x <= px <= x + width - 1 and y <= py <= y + height - 1 for px, py in positions):
        raise ReconstructionReferenceError("reference_crop_excludes_target")
    first_points = rows[0]["keypoints"]
    first_box = [min(p["x"] * source_width for p in first_points), min(p["y"] * source_height for p in first_points),
                 max(p["x"] * source_width for p in first_points), max(p["y"] * source_height for p in first_points)]
    target_locator = {"strategy": "first_frame_crop_bbox_v1", "source_frame_index": first_index,
                      "person_track_id": track, "coordinate_space": "normalized_reference_crop_0_1",
                      "bbox_xyxy": [round((first_box[0] - x) / width, 6), round((first_box[1] - y) / height, 6),
                                    round((first_box[2] - x) / width, 6), round((first_box[3] - y) / height, 6)],
                      "semantics": "identity_location_only_not_a_segmentation_or_pose_constraint"}
    return {"fps": float(fps), "frame_count": len(rows), "start_ms": start, "duration_ms": duration,
            "person_track_id": track, "mapping_source": rows_key, "frame_map": frame_map,
            "target_locator": target_locator,
            "crop": {"x": x, "y": y, "width": width, "height": height, "mode": "fixed_entire_window",
                     "target_landmark_bounds_px": [left, top, right, bottom],
                     "requested_horizontal_margin_px": horizontal_margin_px,
                     "requested_vertical_margin_px": vertical_margin_px,
                     "minimum_landmark_margin_px": min(left - x, top - y, x + width - 1 - right, y + height - 1 - bottom),
                     "all_supplied_target_landmarks_inside": True,
                     "racket_extent_verified": False, "other_people_excluded": False}}


def _encoder(path: str | Path | None) -> str:
    if path is not None:
        if not Path(path).is_file():
            raise ReconstructionReferenceError("reference_encoder_unavailable")
        return str(path)
    installed = shutil.which("ffmpeg")
    if installed:
        return installed
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        raise ReconstructionReferenceError("reference_encoder_unavailable") from None


def export_motion_reconstruction_reference(video_path: Path, trajectory_path: Path, output_dir: Path, *,
                                            expected_source_sha256: str,
                                            crop: tuple[int, int, int, int] | None = None,
                                            horizontal_margin_px: int = 100, vertical_margin_px: int = 80,
                                            ffmpeg_path: str | Path | None = None) -> dict:
    """Export original-pixel reference MP4 and auditable source/crop metadata.

    Existing artifacts are never overwritten. No service state, default replay,
    scoring evidence or model endpoint is touched by this independent exporter.
    """
    video_path, trajectory_path, output_dir = Path(video_path), Path(trajectory_path), Path(output_dir)
    if (not isinstance(expected_source_sha256, str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_source_sha256)
            or not video_path.is_file() or _sha256(video_path) != expected_source_sha256.lower()):
        raise ReconstructionReferenceError("reference_source_digest_mismatch")
    if not trajectory_path.is_file() or trajectory_path.stat().st_size > MAX_TRAJECTORY_BYTES:
        raise ReconstructionReferenceError("reference_invalid_trajectory")
    trajectory_sha = _sha256(trajectory_path)
    try:
        trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ReconstructionReferenceError("reference_invalid_trajectory") from None
    output_dir.mkdir(parents=True, exist_ok=True)
    if any((output_dir / name).exists() for name in ARTIFACT_NAMES):
        raise ReconstructionReferenceError("reference_output_already_exists")
    encoder = _encoder(ffmpeg_path)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise ReconstructionReferenceError("reference_source_decode_failed")
    try:
        width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps, source_frames = float(capture.get(cv2.CAP_PROP_FPS)), int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        plan = build_reference_plan(trajectory, source_width=width, source_height=height,
                                    source_fps=fps, source_frame_count=source_frames, crop=crop,
                                    horizontal_margin_px=horizontal_margin_px, vertical_margin_px=vertical_margin_px)
        x, y, w, h = (plan["crop"][name] for name in ("x", "y", "width", "height"))
        first_index, last_index = plan["frame_map"][0]["source_frame_index"], plan["frame_map"][-1]["source_frame_index"]
        with tempfile.TemporaryDirectory(prefix=".reference-", dir=output_dir) as temporary:
            staging = Path(temporary)
            command = [encoder, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                       "-s:v", f"{w}x{h}", "-r", str(plan["fps"]), "-i", "pipe:0", "-an",
                       "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p",
                       "-movflags", "+faststart", str(staging / "reference.mp4")]
            keyframes = {0: "first.png", plan["frame_count"] // 2: "middle.png", plan["frame_count"] - 1: "last.png"}
            process = None
            try:
                with tempfile.TemporaryFile() as errors:
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
                    deadline = time.monotonic() + 180
                    for source_index in range(last_index + 1):
                        ok, frame = capture.read()
                        if not ok or frame.shape[:2] != (height, width) or time.monotonic() > deadline:
                            raise ReconstructionReferenceError("reference_source_decode_failed")
                        if source_index < first_index:
                            continue
                        index = source_index - first_index
                        decoded_pts = capture.get(cv2.CAP_PROP_POS_MSEC)
                        expected_pts = plan["frame_map"][index]["source_timestamp_ms"]
                        if not math.isfinite(decoded_pts) or abs(decoded_pts - expected_pts) > 2.0:
                            raise ReconstructionReferenceError("reference_source_timestamp_mismatch")
                        plan["frame_map"][index]["decoded_source_timestamp_ms"] = round(decoded_pts, 6)
                        pixels = frame[y:y + h, x:x + w].copy()
                        process.stdin.write(pixels.tobytes())
                        if index in keyframes:
                            ok, png = cv2.imencode(".png", pixels)
                            if not ok:
                                raise ReconstructionReferenceError("reference_keyframe_encode_failed")
                            (staging / keyframes[index]).write_bytes(png.tobytes())
                    process.stdin.close()
                    if process.wait(timeout=60) != 0:
                        raise ReconstructionReferenceError("reference_video_encode_failed")
            except (OSError, subprocess.SubprocessError):
                raise ReconstructionReferenceError("reference_video_encode_failed") from None
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()
            verify = cv2.VideoCapture(str(staging / "reference.mp4"))
            decoded = 0
            try:
                while True:
                    ok, frame = verify.read()
                    if not ok:
                        break
                    if frame.shape[:2] != (h, w):
                        raise ReconstructionReferenceError("reference_output_decode_failed")
                    decoded += 1
                if decoded != plan["frame_count"] or abs(verify.get(cv2.CAP_PROP_FPS) - plan["fps"]) > .001:
                    raise ReconstructionReferenceError("reference_output_decode_failed")
            finally:
                verify.release()
            if _sha256(video_path) != expected_source_sha256.lower() or _sha256(trajectory_path) != trajectory_sha:
                raise ReconstructionReferenceError("reference_input_changed_during_export")
            manifest = {"version": REFERENCE_VERSION, "result_kind": "original_rgb_person_reference",
                        "source": {"filename": video_path.name, "sha256": expected_source_sha256.lower(),
                                   "trajectory_sha256": trajectory_sha, "width": width, "height": height,
                                   "start_ms": plan["start_ms"], "duration_ms": plan["duration_ms"],
                                   "person_track_id": plan["person_track_id"], "mapping_source": plan["mapping_source"]},
                        "crop": plan["crop"], "frame_map": plan["frame_map"], "target_locator": plan["target_locator"],
                        "video": {"width": w, "height": h, "fps": plan["fps"], "frame_count": decoded,
                                  "duration_ms": 1000 * decoded / plan["fps"], "codec": "h264", "audio_included": False,
                                  "geometric_resize": False, "visual_interpolation": False, "overlays": False,
                                  "pixel_operation": "fixed_crop_only_before_lossy_video_encoding"},
                        "semantics": {"pose_used_for": "crop_bounds_and_frame_identity_only", "body_reconstruction": False,
                                      "other_people_removed": False, "missing_pixels_invented": False},
                        "artifact_checksums": {name: _sha256(staging / name) for name in ARTIFACT_NAMES if name != "manifest.json"}}
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            for name in ARTIFACT_NAMES:
                if (output_dir / name).exists():
                    raise ReconstructionReferenceError("reference_output_already_exists")
            for name in ARTIFACT_NAMES:
                os.replace(staging / name, output_dir / name)
            return manifest
    finally:
        capture.release()
