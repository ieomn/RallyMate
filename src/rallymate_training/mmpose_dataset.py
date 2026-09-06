from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import cv2

from rallymate_training.pose_finetune_readiness import (
    GOVERNANCE_FIELDS,
    PoseFineTuneReadinessError,
    audit_pose_finetune_readiness,
    load_m95_sealed_holdout_guard,
)
from rallymate_vision.pose.metadata import (
    SCHEMA_DATA_PATH,
    keypoint_schema,
    sha256_file,
)


MANIFEST_VERSION = "rallymate-mmpose-halpe26-dataset-v1.0.0"
_DEVELOPMENT_SPLITS = ("train", "val")
_SAFE_VIDEO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class MMPoseDatasetExportError(ValueError):
    """Raised when a fail-closed MMPose dataset export cannot proceed."""


def _sha256(path: Path) -> str:
    return sha256_file(path)


def _decoded_pixel_sha256(image: Any) -> str:
    """Match the adapter's IMREAD_COLOR pixel identity exactly."""

    digest = hashlib.sha256()
    digest.update(str(tuple(image.shape)).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(image.dtype).encode("ascii"))
    digest.update(b"\0")
    digest.update(image.tobytes(order="C"))
    return digest.hexdigest().upper()


def _json_bytes(value: Any, *, pretty: bool = True) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise MMPoseDatasetExportError(
                        f"{path} line {line_number} must be a JSON object"
                    )
                rows.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise MMPoseDatasetExportError(f"cannot read {path}: {exc}") from exc
    return rows


def _read_governance(path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(GOVERNANCE_FIELDS):
                raise MMPoseDatasetExportError(
                    "governance headers changed after readiness audit"
                )
            for line_number, raw in enumerate(reader, start=2):
                row = {key: (value or "").strip() for key, value in raw.items()}
                video_id = row["video_id"]
                if not video_id or video_id in rows:
                    raise MMPoseDatasetExportError(
                        f"governance row {line_number} has a duplicate or empty video_id"
                    )
                rows[video_id] = row
    except OSError as exc:
        raise MMPoseDatasetExportError(f"cannot read governance CSV: {exc}") from exc
    return rows


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise MMPoseDatasetExportError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MMPoseDatasetExportError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise MMPoseDatasetExportError(f"{label} must be a finite number")
    return result


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise MMPoseDatasetExportError(f"{label} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MMPoseDatasetExportError(
            f"{label} must be a non-negative integer"
        ) from exc
    if result < 0 or str(value).strip() not in {str(result), f"{result}.0"}:
        raise MMPoseDatasetExportError(f"{label} must be a non-negative integer")
    return result


def _positive_int(value: Any, *, label: str) -> int:
    result = _nonnegative_int(value, label=label)
    if result <= 0:
        raise MMPoseDatasetExportError(f"{label} must be positive")
    return result


def _task_bbox(task: dict[str, Any], *, label: str) -> tuple[float, float, float, float]:
    raw = task.get("bbox_px")
    if not isinstance(raw, list) or len(raw) != 4:
        raise MMPoseDatasetExportError(f"{label}.bbox_px must contain x1,y1,x2,y2")
    bbox = tuple(
        _finite_number(value, label=f"{label}.bbox_px[{index}]")
        for index, value in enumerate(raw)
    )
    width = _positive_int(task.get("frame_width"), label=f"{label}.frame_width")
    height = _positive_int(task.get("frame_height"), label=f"{label}.frame_height")
    x1, y1, x2, y2 = bbox
    if not (0.0 <= x1 < x2 <= width and 0.0 <= y1 < y2 <= height):
        raise MMPoseDatasetExportError(
            f"{label}.bbox_px is outside its immutable frame dimensions"
        )
    return bbox


def _manual_point(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MMPoseDatasetExportError(f"{label} must be an object")
    visible = value.get("visible")
    reason = value.get("visibility_reason")
    reason = "" if reason is None else str(reason).strip()
    if visible is True:
        x = _finite_number(value.get("x_normalized"), label=f"{label}.x_normalized")
        y = _finite_number(value.get("y_normalized"), label=f"{label}.y_normalized")
        if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0):
            raise MMPoseDatasetExportError(
                f"{label} coordinates must use half-open normalized bounds [0,1)"
            )
        if reason:
            raise MMPoseDatasetExportError(
                f"{label} visible point cannot have a visibility reason"
            )
        return {"visible": True, "x": x, "y": y, "reason": None}
    if visible is False:
        if value.get("x_normalized") is not None or value.get("y_normalized") is not None:
            raise MMPoseDatasetExportError(
                f"{label} invisible point must have null coordinates"
            )
        if not reason:
            raise MMPoseDatasetExportError(
                f"{label} invisible point requires a visibility reason"
            )
        return {"visible": False, "x": None, "y": None, "reason": reason}
    raise MMPoseDatasetExportError(f"{label}.visible must be a boolean")


def _source_videos(readiness: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for pack in readiness["packs"]:
        for item in pack["source_videos"]:
            video_id = str(item["video_id"])
            candidate = {
                "path": str(Path(item["path"]).resolve()),
                "sha256": str(item["observed_sha256"]).upper(),
            }
            previous = sources.get(video_id)
            if previous is not None and previous != candidate:
                raise MMPoseDatasetExportError(
                    f"conflicting source video bindings across packs: {video_id}"
                )
            path = Path(candidate["path"])
            if not path.is_file() or _sha256(path) != candidate["sha256"]:
                raise MMPoseDatasetExportError(
                    f"source video changed after readiness audit: {video_id}"
                )
            sources[video_id] = candidate
    return sources


def _assert_holdout_and_split_isolation(
    videos: dict[str, dict[str, Any]],
    governance: dict[str, dict[str, str]],
    sealed_holdout: dict[str, Any],
) -> None:
    """Recheck holdout aliases and global source-identity uniqueness."""

    grouped: dict[tuple[str, str], set[str]] = {}
    for video_id, source in videos.items():
        path = str(Path(source["path"]).resolve())
        sha256 = str(source["sha256"]).upper()
        if (
            video_id == sealed_holdout["video_id"]
            or path.casefold() == str(sealed_holdout["video_path"]).casefold()
            or sha256 == sealed_holdout["video_sha256"]
        ):
            raise MMPoseDatasetExportError(
                f"development export refuses sealed holdout source: {video_id}"
            )
        for dimension, value in (
            ("resolved path", path.casefold()),
            ("SHA-256", sha256),
        ):
            grouped.setdefault((dimension, value), set()).add(video_id)
    for (dimension, _), video_ids in grouped.items():
        if len(video_ids) > 1:
            raise MMPoseDatasetExportError(
                f"source video {dimension} is duplicated across video ids: "
                + ", ".join(sorted(video_ids))
            )


def _load_supervision(
    pack_dirs: list[Path],
    joint_names: tuple[str, ...],
) -> dict[tuple[str, int], dict[str, Any]]:
    tasks: dict[tuple[str, int, str], dict[str, Any]] = {}
    task_ids: set[str] = set()
    frames: dict[tuple[str, int], dict[str, Any]] = {}
    frame_pack_owners: dict[tuple[str, int], Path] = {}
    compiled_keys: set[tuple[str, int, str]] = set()

    for pack_dir in pack_dirs:
        task_rows = _read_jsonl(pack_dir / "tasks.jsonl")
        for index, task in enumerate(task_rows, start=1):
            label = f"{pack_dir}/tasks.jsonl row {index}"
            task_id = str(task.get("task_id", "")).strip()
            video_id = str(task.get("video_id", "")).strip()
            joint_name = str(task.get("joint_name", "")).strip()
            if not task_id or task_id in task_ids:
                raise MMPoseDatasetExportError(
                    f"{label} has a duplicate or empty task_id"
                )
            if not _SAFE_VIDEO_ID.fullmatch(video_id):
                raise MMPoseDatasetExportError(f"{label} has an unsafe video_id")
            if joint_name not in joint_names:
                raise MMPoseDatasetExportError(f"{label} joint is not in Halpe26")
            frame_index = _nonnegative_int(
                task.get("source_frame_index"), label=f"{label}.source_frame_index"
            )
            key = (video_id, frame_index, joint_name)
            if key in tasks:
                raise MMPoseDatasetExportError(
                    "duplicate video/frame/joint across truth packs: "
                    f"{video_id}/{frame_index}/{joint_name}"
                )
            width = _positive_int(task.get("frame_width"), label=f"{label}.frame_width")
            height = _positive_int(task.get("frame_height"), label=f"{label}.frame_height")
            bbox = _task_bbox(task, label=label)
            timestamp_ms = _nonnegative_int(
                task.get("timestamp_ms"), label=f"{label}.timestamp_ms"
            )
            frame_key = (video_id, frame_index)
            frame_pack_owner = frame_pack_owners.setdefault(frame_key, pack_dir)
            if frame_pack_owner != pack_dir:
                raise MMPoseDatasetExportError(
                    "video/frame supervision is split across truth packs: "
                    f"{video_id}/{frame_index}"
                )
            frame_contract = {
                "video_id": video_id,
                "source_frame_index": frame_index,
                "timestamp_ms": timestamp_ms,
                "width": width,
                "height": height,
                "bbox_xyxy": bbox,
                "joints": {},
                "task_ids": {},
            }
            previous = frames.setdefault(frame_key, frame_contract)
            for field in ("timestamp_ms", "width", "height", "bbox_xyxy"):
                if previous[field] != frame_contract[field]:
                    raise MMPoseDatasetExportError(
                        f"immutable task frame contract differs within {video_id}/{frame_index}: {field}"
                    )
            previous["task_ids"][joint_name] = task_id
            tasks[key] = task
            task_ids.add(task_id)

        manual_rows = _read_jsonl(pack_dir / "compiled" / "manual-keypoints.jsonl")
        for record_index, record in enumerate(manual_rows, start=1):
            record_label = (
                f"{pack_dir}/compiled/manual-keypoints.jsonl row {record_index}"
            )
            video_id = str(record.get("video_id", "")).strip()
            frame_index = _nonnegative_int(
                record.get("source_frame_index"),
                label=f"{record_label}.source_frame_index",
            )
            if record.get("adjudication_status") != "accepted":
                raise MMPoseDatasetExportError(
                    f"{record_label} is not accepted adjudicated truth"
                )
            joints = record.get("joints")
            if not isinstance(joints, dict) or not joints:
                raise MMPoseDatasetExportError(f"{record_label} has no joints")
            for joint_name, raw_point in sorted(joints.items()):
                key = (video_id, frame_index, str(joint_name))
                if key in compiled_keys:
                    raise MMPoseDatasetExportError(
                        "duplicate video/frame/joint in compiled truth: "
                        f"{video_id}/{frame_index}/{joint_name}"
                    )
                task = tasks.get(key)
                if task is None:
                    raise MMPoseDatasetExportError(
                        f"compiled truth does not match an immutable task: {key}"
                    )
                task_timestamp = _nonnegative_int(
                    task.get("timestamp_ms"), label=f"task {task['task_id']}.timestamp_ms"
                )
                record_timestamp = _nonnegative_int(
                    record.get("timestamp_ms"), label=f"{record_label}.timestamp_ms"
                )
                if task_timestamp != record_timestamp:
                    raise MMPoseDatasetExportError(
                        f"task/compiled timestamp mismatch: {video_id}/{frame_index}/{joint_name}"
                    )
                point = _manual_point(
                    raw_point,
                    label=f"compiled {video_id}/{frame_index}/{joint_name}",
                )
                frames[(video_id, frame_index)]["joints"][joint_name] = point
                compiled_keys.add(key)

    if set(tasks) != compiled_keys:
        missing = sorted(set(tasks) - compiled_keys)
        extra = sorted(compiled_keys - set(tasks))
        raise MMPoseDatasetExportError(
            "task/compiled supervision mismatch: "
            f"missing={missing[:3]}, extra={extra[:3]}"
        )
    return frames


def _extract_video_frames(
    video_path: Path,
    requests: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Extract exact requested full frames in one forward pass through a video."""

    if not requests:
        return {}
    by_index = {int(item["source_frame_index"]): item for item in requests}
    if len(by_index) != len(requests):
        raise MMPoseDatasetExportError("duplicate extraction request for one video frame")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise MMPoseDatasetExportError(f"cannot open source video: {video_path}")
    extracted: dict[int, dict[str, Any]] = {}
    try:
        last_index = max(by_index)
        for frame_index in range(last_index + 1):
            ok, frame = capture.read()
            if not ok or frame is None:
                raise MMPoseDatasetExportError(
                    f"source video ended before frame {frame_index}: {video_path}"
                )
            request = by_index.get(frame_index)
            if request is None:
                continue
            observed_height, observed_width = frame.shape[:2]
            expected_width = int(request["width"])
            expected_height = int(request["height"])
            if (observed_width, observed_height) != (
                expected_width,
                expected_height,
            ):
                raise MMPoseDatasetExportError(
                    "source frame dimensions differ from immutable task: "
                    f"{video_path}/{frame_index} expected "
                    f"{expected_width}x{expected_height}, observed "
                    f"{observed_width}x{observed_height}"
                )
            output_path = Path(request["output_path"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            written = cv2.imwrite(
                str(output_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]
            )
            if not written or not output_path.is_file():
                raise MMPoseDatasetExportError(
                    f"failed to encode extracted JPEG: {output_path}"
                )
            check = cv2.imread(str(output_path), cv2.IMREAD_COLOR)
            if check is None or check.shape[:2] != frame.shape[:2]:
                raise MMPoseDatasetExportError(
                    f"extracted JPEG failed dimension verification: {output_path}"
                )
            extracted[frame_index] = {
                "sha256": _sha256(output_path),
                "size_bytes": output_path.stat().st_size,
            }
    finally:
        capture.release()
    if set(extracted) != set(by_index):
        raise MMPoseDatasetExportError(
            f"not every requested frame was extracted from {video_path}"
        )
    return extracted


def _artifact(path: Path, root: Path, media_type: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "media_type": media_type,
    }


def export_mmpose_halpe26_dataset(
    truth_pack_dirs: Iterable[str | Path],
    *,
    governance_csv: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Export adjudicated Halpe26 truth as a development-only MMPose dataset.

    The function deliberately refuses test/holdout data.  It calls the strict
    readiness audit before reading labels and again before publishing the
    staged directory, so source or hash drift cannot silently enter an export.
    """

    pack_dirs = sorted({Path(path).resolve() for path in truth_pack_dirs}, key=str)
    governance_path = Path(governance_csv).resolve()
    destination = Path(output_dir).resolve()
    if not pack_dirs:
        raise MMPoseDatasetExportError("at least one truth-pack directory is required")
    if destination.exists():
        raise MMPoseDatasetExportError(f"refusing to overwrite output: {destination}")
    if any(destination.is_relative_to(pack_dir) for pack_dir in pack_dirs):
        raise MMPoseDatasetExportError(
            "output must be outside every source truth-pack directory"
        )
    try:
        readiness = audit_pose_finetune_readiness(
            pack_dirs, governance_csv=governance_path
        )
        sealed_holdout = load_m95_sealed_holdout_guard()
    except (PoseFineTuneReadinessError, OSError, TypeError, ValueError) as exc:
        raise MMPoseDatasetExportError(f"readiness audit failed: {exc}") from exc
    if readiness.get("status") != "ready_for_dataset_export":
        blockers = readiness.get("blockers") or []
        detail = "; ".join(str(item) for item in blockers[:5])
        raise MMPoseDatasetExportError(
            "readiness status must be ready_for_dataset_export"
            + (f": {detail}" if detail else "")
        )
    if readiness.get("sealed_holdout_guard") != sealed_holdout:
        raise MMPoseDatasetExportError(
            "readiness sealed-holdout registry binding is inconsistent"
        )

    schema_path = SCHEMA_DATA_PATH.resolve()
    schema_sha = _sha256(schema_path)
    schema = keypoint_schema("halpe26")
    joint_names = tuple(str(item["name"]) for item in schema["keypoints"])
    indexes = tuple(item.get("index") for item in schema["keypoints"])
    if schema.get("count") != 26 or indexes != tuple(range(26)):
        raise MMPoseDatasetExportError("Halpe26 metadata registry is not contiguous")
    registry_payload = json.loads(schema_path.read_text(encoding="utf-8"))
    registry_halpe = registry_payload.get("formats", {}).get("halpe26", {})
    registry_names = tuple(
        str(item.get("name")) for item in registry_halpe.get("keypoints", [])
    )
    if registry_names != joint_names:
        raise MMPoseDatasetExportError(
            "loaded Halpe26 order differs from the bound metadata registry"
        )

    governance = _read_governance(governance_path)
    test_videos = sorted(
        video_id for video_id, row in governance.items() if row["split"] == "test"
    )
    if test_videos:
        raise MMPoseDatasetExportError(
            "development export refuses holdout/test videos: " + ", ".join(test_videos)
        )
    unsupported = sorted(
        video_id
        for video_id, row in governance.items()
        if row["split"] not in _DEVELOPMENT_SPLITS
    )
    if unsupported:
        raise MMPoseDatasetExportError(
            "development export only accepts train/val governance splits: "
            + ", ".join(unsupported)
        )

    videos = _source_videos(readiness)
    _assert_holdout_and_split_isolation(videos, governance, sealed_holdout)
    frames = _load_supervision(pack_dirs, joint_names)
    frame_video_ids = {video_id for video_id, _ in frames}
    if set(governance) != frame_video_ids or set(videos) != frame_video_ids:
        raise MMPoseDatasetExportError(
            "governance, source-video and accepted-frame video sets differ"
        )

    split_frames: dict[str, list[tuple[tuple[str, int], dict[str, Any]]]] = {
        split: [] for split in _DEVELOPMENT_SPLITS
    }
    for key, frame in sorted(frames.items()):
        split_frames[governance[key[0]]["split"]].append((key, frame))
    empty_splits = [split for split, values in split_frames.items() if not values]
    if empty_splits:
        raise MMPoseDatasetExportError(
            "development export refuses an empty split: " + ", ".join(empty_splits)
        )
    train_visible_joints = {
        joint_name
        for _, frame in split_frames["train"]
        for joint_name, point in frame["joints"].items()
        if point["visible"]
    }
    missing_train_joints = [
        joint_name for joint_name in joint_names if joint_name not in train_visible_joints
    ]
    if missing_train_joints:
        raise MMPoseDatasetExportError(
            "train split lacks visible Halpe26 supervision: "
            + ", ".join(missing_train_joints)
        )

    # Keep the exporter's ready status aligned with the downstream adapter.
    # MMPose top-down samples must contain at least one supervised coordinate,
    # and every coordinate must remain inside both the half-open image plane
    # and the immutable detector-derived crop used for this sample.
    for split, values in split_frames.items():
        for (video_id, frame_index), frame in values:
            visible_points = [
                (joint_name, point)
                for joint_name, point in frame["joints"].items()
                if point["visible"]
            ]
            if not visible_points:
                raise MMPoseDatasetExportError(
                    f"{split} frame has no visible coordinate supervision: "
                    f"{video_id}/{frame_index}"
                )
            width = float(frame["width"])
            height = float(frame["height"])
            x1, y1, x2, y2 = frame["bbox_xyxy"]
            for joint_name, point in visible_points:
                x = point["x"] * width
                y = point["y"] * height
                if not (0.0 <= x < width and 0.0 <= y < height):
                    raise MMPoseDatasetExportError(
                        f"visible coordinate maps outside half-open frame bounds: "
                        f"{video_id}/{frame_index}/{joint_name}"
                    )
                if not (x1 <= x < x2 and y1 <= y < y2):
                    raise MMPoseDatasetExportError(
                        f"visible coordinate is outside task bbox: "
                        f"{video_id}/{frame_index}/{joint_name}"
                    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}-", suffix=".tmp", dir=destination.parent
        )
    ).resolve()
    try:
        (staging / "annotations").mkdir()
        (staging / "images").mkdir()
        extraction_by_video: dict[str, list[dict[str, Any]]] = defaultdict(list)
        frame_descriptors: dict[tuple[str, int], dict[str, Any]] = {}
        generated_names: set[str] = set()
        next_image_id = 1
        for split in _DEVELOPMENT_SPLITS:
            (staging / "images" / split).mkdir()
            for key, frame in split_frames[split]:
                video_id, frame_index = key
                file_name = f"{split}/{video_id}__frame_{frame_index:08d}.jpg"
                collision_key = file_name.casefold()
                if collision_key in generated_names:
                    raise MMPoseDatasetExportError(
                        f"generated image path collides case-insensitively: {file_name}"
                    )
                generated_names.add(collision_key)
                descriptor = {
                    "image_id": next_image_id,
                    "split": split,
                    "file_name": file_name,
                    "output_path": staging / "images" / file_name,
                    "source_frame_index": frame_index,
                    "width": frame["width"],
                    "height": frame["height"],
                }
                frame_descriptors[key] = descriptor
                extraction_by_video[video_id].append(descriptor)
                next_image_id += 1

        extracted_by_frame: dict[tuple[str, int], dict[str, Any]] = {}
        for video_id in sorted(extraction_by_video):
            video_path = Path(videos[video_id]["path"])
            extracted = _extract_video_frames(
                video_path, sorted(extraction_by_video[video_id], key=lambda item: item["source_frame_index"])
            )
            for frame_index, artifact in extracted.items():
                extracted_by_frame[(video_id, frame_index)] = artifact

        expected_frame_keys = set(frame_descriptors)
        if set(extracted_by_frame) != expected_frame_keys:
            raise MMPoseDatasetExportError(
                "frame extraction result does not exactly cover requested frames"
            )
        image_hashes_by_split: dict[str, set[str]] = {
            split: set() for split in _DEVELOPMENT_SPLITS
        }
        pixel_hashes_by_split: dict[str, set[str]] = {
            split: set() for split in _DEVELOPMENT_SPLITS
        }
        for key, descriptor in frame_descriptors.items():
            artifact = extracted_by_frame[key]
            image_path = Path(descriptor["output_path"])
            if not image_path.is_file():
                raise MMPoseDatasetExportError(
                    f"extracted image is missing: {descriptor['file_name']}"
                )
            observed_sha = _sha256(image_path)
            observed_size = image_path.stat().st_size
            if (
                artifact.get("sha256") != observed_sha
                or artifact.get("size_bytes") != observed_size
            ):
                raise MMPoseDatasetExportError(
                    f"extracted image binding mismatch: {descriptor['file_name']}"
                )
            decoded = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if decoded is None:
                raise MMPoseDatasetExportError(
                    f"extracted image cannot be decoded: {descriptor['file_name']}"
                )
            actual_height, actual_width = decoded.shape[:2]
            if (
                actual_width != descriptor["width"]
                or actual_height != descriptor["height"]
            ):
                raise MMPoseDatasetExportError(
                    f"extracted image dimensions differ from immutable task: "
                    f"{descriptor['file_name']}"
                )
            split = descriptor["split"]
            image_hashes_by_split[split].add(observed_sha)
            pixel_hashes_by_split[split].add(_decoded_pixel_sha256(decoded))
        if image_hashes_by_split["train"] & image_hashes_by_split["val"]:
            raise MMPoseDatasetExportError(
                "train/val JPEG content SHA-256 leakage"
            )
        if pixel_hashes_by_split["train"] & pixel_hashes_by_split["val"]:
            raise MMPoseDatasetExportError("train/val decoded-pixel leakage")

        image_manifest_rows: list[dict[str, Any]] = []
        split_summaries: dict[str, dict[str, Any]] = {}
        for split in _DEVELOPMENT_SPLITS:
            images: list[dict[str, Any]] = []
            annotations: list[dict[str, Any]] = []
            visible_count = 0
            for key, frame in split_frames[split]:
                video_id, frame_index = key
                descriptor = frame_descriptors[key]
                image_id = descriptor["image_id"]
                group = governance[video_id]
                images.append(
                    {
                        "id": image_id,
                        "file_name": descriptor["file_name"],
                        "width": frame["width"],
                        "height": frame["height"],
                        "video_id": video_id,
                        "source_frame_index": frame_index,
                        "timestamp_ms": frame["timestamp_ms"],
                        "subject_id": group["subject_id"],
                        "session_id": group["session_id"],
                        "camera_id": group["camera_id"],
                    }
                )
                keypoints: list[float | int] = []
                states: list[str] = []
                reasons: list[str | None] = []
                frame_visible = 0
                for joint_name in joint_names:
                    point = frame["joints"].get(joint_name)
                    if point is None:
                        keypoints.extend((0.0, 0.0, 0))
                        states.append("not_annotated")
                        reasons.append(None)
                    elif point["visible"]:
                        x = point["x"] * frame["width"]
                        y = point["y"] * frame["height"]
                        if not (
                            math.isfinite(x)
                            and math.isfinite(y)
                            and 0.0 <= x < frame["width"]
                            and 0.0 <= y < frame["height"]
                        ):
                            raise MMPoseDatasetExportError(
                                f"pixel coordinate is out of range: {video_id}/{frame_index}/{joint_name}"
                            )
                        keypoints.extend((x, y, 2))
                        states.append("adjudicated_visible_coordinate")
                        reasons.append(None)
                        frame_visible += 1
                    else:
                        keypoints.extend((0.0, 0.0, 0))
                        states.append("adjudicated_invisible_no_coordinate")
                        reasons.append(point["reason"])
                visible_count += frame_visible
                x1, y1, x2, y2 = frame["bbox_xyxy"]
                bbox = [x1, y1, x2 - x1, y2 - y1]
                annotations.append(
                    {
                        "id": image_id,
                        "image_id": image_id,
                        "category_id": 1,
                        "bbox": bbox,
                        "area": bbox[2] * bbox[3],
                        "iscrowd": 0,
                        "segmentation": [],
                        "keypoints": keypoints,
                        "num_keypoints": frame_visible,
                        "keypoint_states": states,
                        "keypoint_visibility_reasons": reasons,
                        "immutable_task_ids_by_joint": [
                            frame["task_ids"].get(name) for name in joint_names
                        ],
                        "bbox_provenance": {
                            "immutable_task_field": "bbox_px",
                            "role": "annotation_crop",
                            "origin": "detector_derived",
                            "ground_truth": False,
                        },
                    }
                )
                image_artifact = extracted_by_frame[key]
                image_manifest_rows.append(
                    {
                        "schema_version": "1.0.0",
                        "split": split,
                        "image_id": image_id,
                        "file_name": descriptor["file_name"],
                        "sha256": image_artifact["sha256"],
                        "size_bytes": image_artifact["size_bytes"],
                        "width": frame["width"],
                        "height": frame["height"],
                        "video_id": video_id,
                        "source_frame_index": frame_index,
                        "source_video_sha256": videos[video_id]["sha256"],
                        "subject_id": group["subject_id"],
                        "session_id": group["session_id"],
                        "camera_id": group["camera_id"],
                        "accuracy_claim": False,
                        "holdout_test_used": False,
                        "bbox_ground_truth_claim": False,
                        "model_values_used_as_keypoint_truth": False,
                    }
                )
            payload = {
                "info": {
                    "description": "RallyMate adjudicated Halpe26 development data",
                    "version": "1.0.0",
                    "keypoint_format": "halpe26",
                    "keypoint_schema_sha256": schema_sha,
                    "accuracy_claim": False,
                    "holdout_test_used": False,
                    "production_enabled": False,
                    "model_promoted": False,
                    "bbox_ground_truth_claim": False,
                    "model_values_used_as_keypoint_truth": False,
                },
                "images": images,
                "annotations": annotations,
                "categories": [
                    {
                        "id": 1,
                        "name": "person",
                        "supercategory": "person",
                        "keypoints": list(joint_names),
                        "skeleton": [],
                    }
                ],
            }
            coco_path = staging / "annotations" / f"{split}.json"
            coco_path.write_bytes(_json_bytes(payload))
            split_summaries[split] = {
                "annotation_file": f"annotations/{split}.json",
                "annotation_sha256": _sha256(coco_path),
                "image_root": "images",
                "image_prefix": split,
                "image_count": len(images),
                "annotation_count": len(annotations),
                "visible_keypoint_count": visible_count,
                "image_ids": [item["id"] for item in images],
                "video_ids": sorted({item["video_id"] for item in images}),
                "subject_ids": sorted({item["subject_id"] for item in images}),
                "session_ids": sorted({item["session_id"] for item in images}),
            }

        image_manifest_path = staging / "image-manifest.jsonl"
        image_manifest_path.write_bytes(
            b"".join(_json_bytes(row, pretty=False) for row in image_manifest_rows)
        )
        artifacts = [
            _artifact(staging / "annotations" / f"{split}.json", staging, "application/json")
            for split in _DEVELOPMENT_SPLITS
        ]
        artifacts.append(
            _artifact(image_manifest_path, staging, "application/x-ndjson")
        )
        for row in image_manifest_rows:
            artifacts.append(
                _artifact(staging / "images" / row["file_name"], staging, "image/jpeg")
            )
        artifacts.sort(key=lambda item: item["path"])

        try:
            second_readiness = audit_pose_finetune_readiness(
                pack_dirs, governance_csv=governance_path
            )
        except (PoseFineTuneReadinessError, OSError, TypeError, ValueError) as exc:
            raise MMPoseDatasetExportError(
                f"readiness audit failed after extraction: {exc}"
            ) from exc
        if (
            second_readiness.get("status") != "ready_for_dataset_export"
            or second_readiness.get("inputs", {}).get("input_fingerprint_sha256")
            != readiness.get("inputs", {}).get("input_fingerprint_sha256")
        ):
            raise MMPoseDatasetExportError(
                "truth-pack or governance sources changed during export"
            )
        if not schema_path.is_file() or _sha256(schema_path) != schema_sha:
            raise MMPoseDatasetExportError(
                "Halpe26 metadata registry changed during export"
            )

        manifest = {
            "schema_version": "1.0.0",
            "manifest_version": MANIFEST_VERSION,
            "status": "ready_for_mmpose_training",
            "purpose": "development_finetune",
            "input_readiness": {
                "report_version": readiness["report_version"],
                "status": readiness["status"],
                "input_fingerprint_sha256": readiness["inputs"][
                    "input_fingerprint_sha256"
                ],
            },
            "topology": {
                "format": "halpe26",
                "count": len(joint_names),
                "ordered_keypoints": list(joint_names),
                "registry_path": str(schema_path),
                "registry_sha256": schema_sha,
            },
            "sealed_holdout_guard": sealed_holdout,
            "images": {"root": "images", "manifest": "image-manifest.jsonl"},
            "splits": split_summaries,
            "image_manifest": {
                "path": "image-manifest.jsonl",
                "sha256": _sha256(image_manifest_path),
                "row_count": len(image_manifest_rows),
            },
            "governance": {
                "path": str(governance_path),
                "sha256": _sha256(governance_path),
            },
            "truth_packs": [
                {
                    "path": pack["path"],
                    "pack_version": pack["pack_version"],
                    "validation_status": pack["validation_status"],
                    "video_ids": pack["video_ids"],
                    "task_count": pack["counts"]["tasks"],
                    "accepted_frame_count": pack["counts"]["accepted_frames"],
                    "accepted_joint_value_count": pack["counts"][
                        "accepted_joint_values"
                    ],
                }
                for pack in readiness["packs"]
            ],
            "source_videos": [
                {"video_id": video_id, **videos[video_id]}
                for video_id in sorted(videos)
            ],
            "readiness": {
                "dataset_export_complete": True,
                "train_split_nonempty": True,
                "val_split_nonempty": True,
                "subject_session_leakage_free": True,
                "source_hashes_verified": True,
                "schema_binding_verified": True,
                "train_split_all_halpe26_joints_supervised": True,
                "source_path_sha_cross_split_leakage_free": True,
                "sealed_holdout_registry_binding_valid": True,
                "mmpose_training_allowed": True,
            },
            "artifacts": artifacts,
            "safety": {
                "accuracy_claim": False,
                "holdout_test_used": False,
                "production_enabled": False,
                "model_promoted": False,
                "bbox_ground_truth_claim": False,
                "model_values_used_as_keypoint_truth": False,
            },
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_bytes(_json_bytes(manifest))
        if destination.exists():
            raise MMPoseDatasetExportError(
                f"refusing to overwrite output created during export: {destination}"
            )
        staging.rename(destination)
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


__all__ = [
    "MANIFEST_VERSION",
    "MMPoseDatasetExportError",
    "export_mmpose_halpe26_dataset",
]
