"""Fail-closed MMPose/RTMPose fine-tune planning and execution.

The adapter is intentionally separate from the existing Ultralytics training
engine.  It accepts only the deterministic RallyMate Halpe26 COCO export,
verifies all byte bindings and split-isolation gates, writes a new run
directory, and defaults to a dry run.  ``mmengine.Runner`` is imported only for
an explicit, fully gated ``execute=True`` invocation.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import cv2

from rallymate_training.pose_finetune_readiness import (
    GOVERNANCE_FIELDS,
    HALPE26_JOINTS,
    load_m95_sealed_holdout_guard,
)


PLAN_VERSION = "rallymate-rtmpose-finetune-plan-v1.0.0"
DATASET_MANIFEST_VERSION = "rallymate-mmpose-halpe26-dataset-v1.0.0"
APPROVED_BASE_CONFIG_RELATIVE_PATH = (
    "runtime/rtmpose/.venv/Lib/site-packages/mmpose/.mim/configs/"
    "body_2d_keypoint/rtmpose/body8/"
    "rtmpose-x_8xb256-700e_body8-halpe26-384x288.py"
)
APPROVED_BASE_CONFIG_SHA256 = (
    "961E5704E4983F27173BC008C17F08E8C2C5907FDB104EA8669AD06C4C68B678"
)
APPROVED_CHECKPOINT_RELATIVE_PATH = (
    "models/rtmpose/rtmpose-x_halpe26_384x288.pth"
)
APPROVED_CHECKPOINT_SHA256 = (
    "7FB6E239601082A06CA8442FEB7A9774B8D77A1C37911B2AD0F27C1E84BE4D21"
)
APPROVED_CHECKPOINT_BYTES = 200_397_852
TEMPLATE_RELATIVE_PATH = (
    "training/configs/rtmpose_x_halpe26_384x288_rallymate.py"
)
RUNTIME_PYTHON_RELATIVE_PATH = "runtime/rtmpose/.venv/Scripts/python.exe"
RUN_RECORD_NAME = "finetune-run-record.json"
RESOLVED_CONFIG_NAME = "resolved-config.py"
_READY_FLAGS = (
    "dataset_export_complete",
    "train_split_nonempty",
    "val_split_nonempty",
    "subject_session_leakage_free",
    "source_hashes_verified",
    "schema_binding_verified",
    "train_split_all_halpe26_joints_supervised",
    "source_path_sha_cross_split_leakage_free",
    "sealed_holdout_registry_binding_valid",
    "mmpose_training_allowed",
)
_MANIFEST_FIELDS = {
    "schema_version",
    "manifest_version",
    "status",
    "purpose",
    "input_readiness",
    "topology",
    "sealed_holdout_guard",
    "images",
    "splits",
    "image_manifest",
    "governance",
    "truth_packs",
    "source_videos",
    "readiness",
    "artifacts",
    "safety",
}
_SPLIT_FIELDS = {
    "annotation_file",
    "annotation_sha256",
    "image_root",
    "image_prefix",
    "image_count",
    "annotation_count",
    "visible_keypoint_count",
    "image_ids",
    "video_ids",
    "subject_ids",
    "session_ids",
}
_COCO_INFO_FIELDS = {
    "description",
    "version",
    "keypoint_format",
    "keypoint_schema_sha256",
    "accuracy_claim",
    "holdout_test_used",
    "production_enabled",
    "model_promoted",
    "bbox_ground_truth_claim",
    "model_values_used_as_keypoint_truth",
}
_COCO_IMAGE_FIELDS = {
    "id",
    "file_name",
    "width",
    "height",
    "video_id",
    "source_frame_index",
    "timestamp_ms",
    "subject_id",
    "session_id",
    "camera_id",
}
_COCO_ANNOTATION_FIELDS = {
    "id",
    "image_id",
    "category_id",
    "bbox",
    "area",
    "iscrowd",
    "segmentation",
    "keypoints",
    "num_keypoints",
    "keypoint_states",
    "keypoint_visibility_reasons",
    "immutable_task_ids_by_joint",
    "bbox_provenance",
}
_IMAGE_MANIFEST_FIELDS = {
    "schema_version",
    "split",
    "image_id",
    "file_name",
    "sha256",
    "size_bytes",
    "width",
    "height",
    "video_id",
    "source_frame_index",
    "source_video_sha256",
    "subject_id",
    "session_id",
    "camera_id",
    "accuracy_claim",
    "holdout_test_used",
    "bbox_ground_truth_claim",
    "model_values_used_as_keypoint_truth",
}


class MMPoseFineTuneError(ValueError):
    """Raised when a fine-tune input fails a reproducibility/safety gate."""

    def __init__(
        self,
        message: str,
        *,
        training_started: bool = False,
        status: str = "rejected",
    ) -> None:
        super().__init__(message)
        self.training_started = training_started
        self.status = status


def _require_exact_keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise MMPoseFineTuneError(f"{label} fields do not match schema")


def _workspace_root(workspace: str | Path | None) -> Path:
    if workspace is not None:
        return Path(workspace).resolve()
    return Path(__file__).resolve().parents[2]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise MMPoseFineTuneError(f"{label} must be a 64-character SHA-256")
    normalized = value.strip().upper()
    if len(normalized) != 64 or any(c not in "0123456789ABCDEF" for c in normalized):
        raise MMPoseFineTuneError(f"{label} must be a 64-character SHA-256")
    return normalized


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MMPoseFineTuneError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MMPoseFineTuneError(f"{label} must contain a JSON object")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MMPoseFineTuneError(f"cannot read {label}: {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MMPoseFineTuneError(
                f"{label} line {line_number} is invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(row, dict):
            raise MMPoseFineTuneError(
                f"{label} line {line_number} must be a JSON object"
            )
        rows.append(row)
    return rows


def _path_within(root: Path, raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise MMPoseFineTuneError(f"{label} must be a non-empty relative path")
    if "\\" in raw:
        raise MMPoseFineTuneError(f"{label} must use canonical POSIX separators")
    posix = PurePosixPath(raw)
    if (
        posix.is_absolute()
        or posix.as_posix() != raw
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise MMPoseFineTuneError(f"{label} must be a canonical relative POSIX path")
    relative = Path(*posix.parts)
    if relative.is_absolute():
        raise MMPoseFineTuneError(f"{label} must be relative to the dataset root")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MMPoseFineTuneError(f"{label} escapes the dataset root") from exc
    return resolved


def _bound_external_path(base: Path, binding: Any, label: str) -> tuple[Path, str]:
    if not isinstance(binding, dict):
        raise MMPoseFineTuneError(f"{label} binding is missing")
    raw = binding.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise MMPoseFineTuneError(f"{label}.path must be non-empty")
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise MMPoseFineTuneError(f"{label} file is missing: {path}")
    expected = _require_sha256(binding.get("sha256"), f"{label}.sha256")
    observed = _sha256_file(path)
    if observed != expected:
        raise MMPoseFineTuneError(f"{label} SHA-256 mismatch")
    return path, observed


def _read_governance(path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(GOVERNANCE_FIELDS):
                raise MMPoseFineTuneError("governance headers do not match schema")
            for line_number, raw in enumerate(reader, start=2):
                if set(raw) != set(GOVERNANCE_FIELDS) or any(
                    not isinstance(raw.get(key), str) for key in GOVERNANCE_FIELDS
                ):
                    raise MMPoseFineTuneError(
                        f"governance row {line_number} fields do not match schema"
                    )
                row = {key: (value or "").strip() for key, value in raw.items()}
                video_id = row["video_id"]
                if not video_id or video_id in rows:
                    raise MMPoseFineTuneError(
                        f"governance row {line_number} has duplicate/blank video_id"
                    )
                if (
                    row["split"] not in {"train", "val"}
                    or row["consent"].lower()
                    not in {"1", "approved", "granted", "true", "yes"}
                    or not row["license"]
                    or any(
                        not row[field]
                        for field in ("subject_id", "session_id", "camera_id")
                    )
                ):
                    raise MMPoseFineTuneError(
                        f"governance row {line_number} is not training-authorized"
                    )
                rows[video_id] = row
    except OSError as exc:
        raise MMPoseFineTuneError(f"cannot read governance: {exc}") from exc
    if not rows:
        raise MMPoseFineTuneError("governance contains no rows")
    return rows


def _validate_exact_model_inputs(root: Path) -> dict[str, Any]:
    base_config = (root / APPROVED_BASE_CONFIG_RELATIVE_PATH).resolve()
    checkpoint = (root / APPROVED_CHECKPOINT_RELATIVE_PATH).resolve()
    template = (root / TEMPLATE_RELATIVE_PATH).resolve()
    runtime_python = (root / RUNTIME_PYTHON_RELATIVE_PATH).resolve()
    for path, label in (
        (base_config, "approved base config"),
        (checkpoint, "approved checkpoint"),
        (template, "RallyMate config template"),
        (runtime_python, "bundled MMPose Python"),
    ):
        if not path.is_file():
            raise MMPoseFineTuneError(f"{label} is missing: {path}")
    base_hash = _sha256_file(base_config)
    checkpoint_hash = _sha256_file(checkpoint)
    if base_hash != APPROVED_BASE_CONFIG_SHA256:
        raise MMPoseFineTuneError("approved base config SHA-256 drift")
    if checkpoint_hash != APPROVED_CHECKPOINT_SHA256:
        raise MMPoseFineTuneError("approved checkpoint SHA-256 drift")
    if checkpoint.stat().st_size != APPROVED_CHECKPOINT_BYTES:
        raise MMPoseFineTuneError("approved checkpoint byte size drift")
    return {
        "base_config": base_config,
        "base_config_sha256": base_hash,
        "checkpoint": checkpoint,
        "checkpoint_sha256": checkpoint_hash,
        "checkpoint_bytes": checkpoint.stat().st_size,
        "template": template,
        "template_sha256": _sha256_file(template),
        "runtime_python": runtime_python,
        "runtime_python_sha256": _sha256_file(runtime_python),
    }


def _int_id(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MMPoseFineTuneError(f"{label} must be an integer")
    return value


def _positive_int(value: Any, label: str) -> int:
    result = _int_id(value, label)
    if result < 1:
        raise MMPoseFineTuneError(f"{label} must be positive")
    return result


def _nonempty_ids(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MMPoseFineTuneError(f"{label} must be a non-empty list")
    if any(not isinstance(item, str) for item in value):
        raise MMPoseFineTuneError(f"{label} values must be strings")
    normalized = [item.strip() for item in value]
    if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
        raise MMPoseFineTuneError(f"{label} contains blank or duplicate values")
    if normalized != sorted(normalized):
        raise MMPoseFineTuneError(f"{label} must be sorted")
    return normalized


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MMPoseFineTuneError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise MMPoseFineTuneError(f"{label} must be finite")
    return number


def _decoded_pixel_sha256(image: Any) -> str:
    digest = hashlib.sha256()
    digest.update(str(tuple(image.shape)).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(image.dtype).encode("ascii"))
    digest.update(b"\0")
    digest.update(image.tobytes(order="C"))
    return digest.hexdigest().upper()


def _validate_coco_split(
    dataset_root: Path,
    images_root: Path,
    split_name: str,
    declared: Any,
    artifact_paths: dict[Path, dict[str, Any]],
) -> dict[str, Any]:
    _require_exact_keys(declared, _SPLIT_FIELDS, f"splits.{split_name}")
    annotation = _path_within(
        dataset_root,
        declared.get("annotation_file"),
        f"splits.{split_name}.annotation_file",
    )
    if not annotation.is_file():
        raise MMPoseFineTuneError(f"{split_name} annotation file is missing")
    expected_annotation_hash = _require_sha256(
        declared.get("annotation_sha256"),
        f"splits.{split_name}.annotation_sha256",
    )
    observed_annotation_hash = _sha256_file(annotation)
    if observed_annotation_hash != expected_annotation_hash:
        raise MMPoseFineTuneError(f"{split_name} annotation SHA-256 mismatch")
    if annotation not in artifact_paths:
        raise MMPoseFineTuneError(f"{split_name} annotation lacks artifact binding")

    if declared.get("image_root") != "images":
        raise MMPoseFineTuneError(f"splits.{split_name}.image_root must be images")
    if declared.get("image_prefix") != split_name:
        raise MMPoseFineTuneError(
            f"splits.{split_name}.image_prefix must be {split_name}"
        )
    coco = _read_object(annotation, f"{split_name} COCO annotation")
    _require_exact_keys(
        coco, {"info", "images", "annotations", "categories"}, f"{split_name} COCO"
    )
    info = coco.get("info")
    _require_exact_keys(info, _COCO_INFO_FIELDS, f"{split_name} COCO info")
    if (
        not isinstance(info, dict)
        or info.get("description")
        != "RallyMate adjudicated Halpe26 development data"
        or info.get("version") != "1.0.0"
        or info.get("keypoint_format") != "halpe26"
        or info.get("accuracy_claim") is not False
        or info.get("holdout_test_used") is not False
        or info.get("production_enabled") is not False
        or info.get("model_promoted") is not False
        or info.get("bbox_ground_truth_claim") is not False
        or info.get("model_values_used_as_keypoint_truth") is not False
    ):
        raise MMPoseFineTuneError(f"{split_name} COCO info safety binding is invalid")
    info_topology_hash = _require_sha256(
        info.get("keypoint_schema_sha256"),
        f"{split_name} COCO info.keypoint_schema_sha256",
    )
    images = coco.get("images")
    annotations = coco.get("annotations")
    categories = coco.get("categories")
    if not isinstance(images, list) or not images:
        raise MMPoseFineTuneError(f"{split_name} COCO images must be non-empty")
    if not isinstance(annotations, list) or not annotations:
        raise MMPoseFineTuneError(f"{split_name} COCO annotations must be non-empty")
    if not isinstance(categories, list) or len(categories) != 1:
        raise MMPoseFineTuneError(f"{split_name} COCO must have one category")
    category = categories[0]
    _require_exact_keys(
        category,
        {"id", "name", "supercategory", "keypoints", "skeleton"},
        f"{split_name} COCO category",
    )
    if (
        _int_id(category.get("id"), f"{split_name} category.id") != 1
        or category.get("name") != "person"
        or category.get("supercategory") != "person"
        or tuple(category.get("keypoints", ())) != tuple(HALPE26_JOINTS)
        or category.get("skeleton") != []
    ):
        raise MMPoseFineTuneError(f"{split_name} COCO category is not Halpe26 person")

    image_by_id: dict[int, dict[str, Any]] = {}
    file_names: set[str] = set()
    for image in images:
        _require_exact_keys(image, _COCO_IMAGE_FIELDS, f"{split_name} COCO image")
        image_id = _int_id(image.get("id"), f"{split_name} image.id")
        file_name = image.get("file_name")
        width = _int_id(image.get("width"), f"{split_name} image.width")
        height = _int_id(image.get("height"), f"{split_name} image.height")
        if image_id < 1 or width < 1 or height < 1:
            raise MMPoseFineTuneError(
                f"{split_name} image id/width/height must be positive"
            )
        if image_id in image_by_id:
            raise MMPoseFineTuneError(f"{split_name} duplicate image id: {image_id}")
        if not isinstance(file_name, str):
            raise MMPoseFineTuneError(f"{split_name} image file_name is missing")
        canonical_image_path = _path_within(
            images_root, file_name, f"{split_name} image.file_name"
        )
        file_parts = PurePosixPath(file_name).parts
        if len(file_parts) < 2 or file_parts[0] != split_name:
            raise MMPoseFineTuneError(f"{split_name} image file_name has wrong prefix")
        if file_name in file_names:
            raise MMPoseFineTuneError(f"{split_name} duplicate image file_name")
        for field in ("video_id", "subject_id", "session_id", "camera_id"):
            if not isinstance(image.get(field), str) or not image[field].strip():
                raise MMPoseFineTuneError(f"{split_name} image.{field} is missing")
        frame_index = _int_id(
            image.get("source_frame_index"),
            f"{split_name} image.source_frame_index",
        )
        timestamp_ms = _number(
            image.get("timestamp_ms"), f"{split_name} image.timestamp_ms"
        )
        if frame_index < 0 or timestamp_ms < 0:
            raise MMPoseFineTuneError(
                f"{split_name} frame index/timestamp must be non-negative"
            )
        image_by_id[image_id] = image
        image["_resolved_path"] = canonical_image_path
        file_names.add(file_name)

    annotation_ids: set[int] = set()
    annotated_image_ids: set[int] = set()
    visible_keypoint_count = 0
    masked_keypoint_count = 0
    accepted_keypoint_count = 0
    visible_counts_by_joint = [0] * 26
    for annotation_row in annotations:
        _require_exact_keys(
            annotation_row,
            _COCO_ANNOTATION_FIELDS,
            f"{split_name} COCO annotation",
        )
        annotation_id = _int_id(
            annotation_row.get("id"), f"{split_name} annotation.id"
        )
        image_id = _int_id(
            annotation_row.get("image_id"), f"{split_name} annotation.image_id"
        )
        if annotation_id in annotation_ids:
            raise MMPoseFineTuneError(f"{split_name} duplicate annotation id")
        if image_id not in image_by_id:
            raise MMPoseFineTuneError(f"{split_name} annotation references unknown image")
        if _int_id(
            annotation_row.get("category_id"),
            f"{split_name} annotation.category_id",
        ) != 1:
            raise MMPoseFineTuneError(f"{split_name} annotation category_id must be 1")
        keypoints = annotation_row.get("keypoints")
        states = annotation_row.get("keypoint_states")
        reasons = annotation_row.get("keypoint_visibility_reasons")
        task_ids = annotation_row.get("immutable_task_ids_by_joint")
        if not isinstance(keypoints, list) or len(keypoints) != 26 * 3:
            raise MMPoseFineTuneError(f"{split_name} keypoints must contain 78 values")
        if not isinstance(states, list) or len(states) != 26:
            raise MMPoseFineTuneError(f"{split_name} keypoint_states must contain 26 values")
        if not isinstance(reasons, list) or len(reasons) != 26:
            raise MMPoseFineTuneError(
                f"{split_name} keypoint_visibility_reasons must contain 26 values"
            )
        if not isinstance(task_ids, list) or len(task_ids) != 26:
            raise MMPoseFineTuneError(
                f"{split_name} immutable_task_ids_by_joint must contain 26 values"
            )
        present = 0
        visible_points: list[tuple[float, float]] = []
        for index in range(26):
            x = _number(keypoints[index * 3], f"{split_name} keypoint x")
            y = _number(keypoints[index * 3 + 1], f"{split_name} keypoint y")
            visibility = _number(
                keypoints[index * 3 + 2], f"{split_name} keypoint visibility"
            )
            if visibility not in (0.0, 2.0):
                raise MMPoseFineTuneError(
                    f"{split_name} keypoint visibility must be 0 or 2"
                )
            state = states[index]
            if state == "adjudicated_visible_coordinate":
                if (
                    visibility != 2.0
                    or not (0.0 <= x < float(image_by_id[image_id]["width"]))
                    or not (0.0 <= y < float(image_by_id[image_id]["height"]))
                    or not isinstance(task_ids[index], str)
                    or not task_ids[index].strip()
                    or reasons[index] is not None
                ):
                    raise MMPoseFineTuneError(
                        f"{split_name} visible keypoint state/mask/task binding mismatch"
                    )
                present += 1
                visible_points.append((x, y))
                accepted_keypoint_count += 1
                visible_counts_by_joint[index] += 1
            elif state == "adjudicated_invisible_no_coordinate":
                if (
                    visibility != 0.0
                    or x != 0.0
                    or y != 0.0
                    or not isinstance(task_ids[index], str)
                    or not task_ids[index].strip()
                    or not isinstance(reasons[index], str)
                    or not reasons[index].strip()
                ):
                    raise MMPoseFineTuneError(
                        f"{split_name} invisible keypoint state/mask/reason mismatch"
                    )
                masked_keypoint_count += 1
                accepted_keypoint_count += 1
            elif state == "not_annotated":
                if (
                    visibility != 0.0
                    or x != 0.0
                    or y != 0.0
                    or task_ids[index] is not None
                    or reasons[index] is not None
                ):
                    raise MMPoseFineTuneError(
                        f"{split_name} unannotated keypoint state/mask/task mismatch"
                    )
                masked_keypoint_count += 1
            else:
                raise MMPoseFineTuneError(f"{split_name} keypoint state is unsupported")
        if _int_id(
            annotation_row.get("num_keypoints"),
            f"{split_name} annotation.num_keypoints",
        ) != present or present == 0:
            raise MMPoseFineTuneError(
                f"{split_name} num_keypoints must equal positive visibility count"
            )
        bbox = annotation_row.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise MMPoseFineTuneError(f"{split_name} bbox must contain four values")
        bbox_values = [_number(item, f"{split_name} bbox") for item in bbox]
        image_width = float(image_by_id[image_id]["width"])
        image_height = float(image_by_id[image_id]["height"])
        if (
            bbox_values[0] < 0
            or bbox_values[1] < 0
            or bbox_values[2] <= 0
            or bbox_values[3] <= 0
            or bbox_values[0] + bbox_values[2] > image_width
            or bbox_values[1] + bbox_values[3] > image_height
        ):
            raise MMPoseFineTuneError(f"{split_name} bbox is outside the image")
        bbox_x, bbox_y, bbox_width, bbox_height = bbox_values
        if any(
            not (
                bbox_x <= x < bbox_x + bbox_width
                and bbox_y <= y < bbox_y + bbox_height
            )
            for x, y in visible_points
        ):
            raise MMPoseFineTuneError(
                f"{split_name} visible keypoint is outside its annotation bbox"
            )
        if not math.isclose(
            _number(annotation_row.get("area"), f"{split_name} area"),
            bbox_values[2] * bbox_values[3],
            rel_tol=1e-9,
            abs_tol=1e-6,
        ) or _int_id(
            annotation_row.get("iscrowd"), f"{split_name} annotation.iscrowd"
        ) != 0:
            raise MMPoseFineTuneError(f"{split_name} area/iscrowd binding mismatch")
        provenance = annotation_row.get("bbox_provenance")
        expected_provenance = {
            "immutable_task_field": "bbox_px",
            "role": "annotation_crop",
            "origin": "detector_derived",
            "ground_truth": False,
        }
        _require_exact_keys(
            provenance, set(expected_provenance), f"{split_name} bbox_provenance"
        )
        if (
            provenance.get("ground_truth") is not False
            or any(
                provenance.get(key) != value
                for key, value in expected_provenance.items()
                if key != "ground_truth"
            )
        ):
            raise MMPoseFineTuneError(
                f"{split_name} bbox_provenance must deny ground-truth status"
            )
        if annotation_row.get("segmentation") != []:
            raise MMPoseFineTuneError(f"{split_name} segmentation must be empty")
        annotation_ids.add(annotation_id)
        annotated_image_ids.add(image_id)
        visible_keypoint_count += present
    if annotated_image_ids != set(image_by_id) or len(annotations) != len(images):
        raise MMPoseFineTuneError(f"{split_name} does not annotate every image")

    declared_image_ids = declared.get("image_ids")
    if not isinstance(declared_image_ids, list):
        raise MMPoseFineTuneError(f"splits.{split_name}.image_ids must be a list")
    validated_image_ids = [
        _positive_int(item, f"splits.{split_name}.image_ids")
        for item in declared_image_ids
    ]
    if validated_image_ids != sorted(image_by_id):
        raise MMPoseFineTuneError(f"splits.{split_name}.image_ids mismatch")
    counts = {
        "image_count": len(images),
        "annotation_count": len(annotations),
        "visible_keypoint_count": visible_keypoint_count,
    }
    for field, observed in counts.items():
        if _positive_int(declared.get(field), f"splits.{split_name}.{field}") != observed:
            raise MMPoseFineTuneError(f"splits.{split_name}.{field} mismatch")
    dimensions = {
        "subject_ids": {str(item["subject_id"]) for item in images},
        "session_ids": {str(item["session_id"]) for item in images},
        "video_ids": {str(item["video_id"]) for item in images},
    }
    for field, values in dimensions.items():
        if _nonempty_ids(declared.get(field), f"splits.{split_name}.{field}") != sorted(values):
            raise MMPoseFineTuneError(f"splits.{split_name}.{field} mismatch")
    return {
        "annotation": annotation,
        "annotation_sha256": observed_annotation_hash,
        "image_by_id": image_by_id,
        "image_ids": set(image_by_id),
        "annotation_ids": annotation_ids,
        "file_names": file_names,
        "image_paths": {
            item["_resolved_path"] for item in image_by_id.values()
        },
        "masked_keypoint_count": masked_keypoint_count,
        "accepted_keypoint_count": accepted_keypoint_count,
        "visible_counts_by_joint": visible_counts_by_joint,
        "topology_registry_sha256": info_topology_hash,
        **dimensions,
        **counts,
    }


def _artifact_index(dataset_root: Path, value: Any) -> dict[Path, dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise MMPoseFineTuneError("artifacts must be a non-empty list")
    result: dict[Path, dict[str, Any]] = {}
    for index, binding in enumerate(value):
        if not isinstance(binding, dict):
            raise MMPoseFineTuneError(f"artifacts[{index}] must be an object")
        if set(binding) != {"path", "sha256", "size_bytes", "media_type"}:
            raise MMPoseFineTuneError(f"artifacts[{index}] fields do not match schema")
        path = _path_within(dataset_root, binding.get("path"), f"artifacts[{index}].path")
        if path in result:
            raise MMPoseFineTuneError(f"duplicate artifact binding: {path}")
        if not path.is_file():
            raise MMPoseFineTuneError(f"bound artifact is missing: {path}")
        expected_hash = _require_sha256(
            binding.get("sha256"), f"artifacts[{index}].sha256"
        )
        if _sha256_file(path) != expected_hash:
            raise MMPoseFineTuneError(f"artifact SHA-256 mismatch: {path}")
        if _positive_int(
            binding.get("size_bytes"), f"artifacts[{index}].size_bytes"
        ) != path.stat().st_size:
            raise MMPoseFineTuneError(f"artifact byte size mismatch: {path}")
        if path.name == "image-manifest.jsonl":
            expected_media_type = "application/x-ndjson"
        elif path.suffix == ".json":
            expected_media_type = "application/json"
        elif path.suffix == ".jpg":
            expected_media_type = "image/jpeg"
        else:
            raise MMPoseFineTuneError(f"unsupported artifact type: {path}")
        if binding.get("media_type") != expected_media_type:
            raise MMPoseFineTuneError(f"artifact media type mismatch: {path}")
        result[path] = binding
    return result


def _validate_dataset_manifest(
    manifest_path: Path, expected_sha256: str
) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise MMPoseFineTuneError(f"dataset manifest is missing: {manifest_path}")
    observed_manifest_hash = _sha256_file(manifest_path)
    if observed_manifest_hash != _require_sha256(
        expected_sha256, "expected dataset manifest SHA-256"
    ):
        raise MMPoseFineTuneError("dataset manifest SHA-256 mismatch")
    dataset_root = manifest_path.parent.resolve()
    manifest = _read_object(manifest_path, "dataset manifest")
    _require_exact_keys(manifest, _MANIFEST_FIELDS, "dataset manifest")
    if manifest.get("schema_version") != "1.0.0":
        raise MMPoseFineTuneError("dataset schema_version must be 1.0.0")
    if manifest.get("manifest_version") != DATASET_MANIFEST_VERSION:
        raise MMPoseFineTuneError("unsupported dataset manifest_version")
    if manifest.get("status") != "ready_for_mmpose_training":
        raise MMPoseFineTuneError("dataset is not ready_for_mmpose_training")
    if manifest.get("purpose") != "development_finetune":
        raise MMPoseFineTuneError("dataset purpose must be development_finetune")
    readiness = manifest.get("readiness")
    _require_exact_keys(readiness, set(_READY_FLAGS), "readiness")
    if not isinstance(readiness, dict) or any(
        readiness.get(flag) is not True for flag in _READY_FLAGS
    ):
        raise MMPoseFineTuneError("dataset readiness gates are not all true")
    input_readiness = manifest.get("input_readiness")
    _require_exact_keys(
        input_readiness,
        {"report_version", "status", "input_fingerprint_sha256"},
        "input_readiness",
    )
    if (
        not isinstance(input_readiness, dict)
        or input_readiness.get("status") != "ready_for_dataset_export"
        or not isinstance(input_readiness.get("report_version"), str)
        or not input_readiness["report_version"].strip()
    ):
        raise MMPoseFineTuneError("input readiness report is not export-ready")
    _require_sha256(
        input_readiness.get("input_fingerprint_sha256"),
        "input_readiness.input_fingerprint_sha256",
    )
    safety = manifest.get("safety")
    expected_safety = {
        "accuracy_claim": False,
        "holdout_test_used": False,
        "production_enabled": False,
        "model_promoted": False,
        "bbox_ground_truth_claim": False,
        "model_values_used_as_keypoint_truth": False,
    }
    _require_exact_keys(safety, set(expected_safety), "safety")
    if any(
        safety.get(key) is not value for key, value in expected_safety.items()
    ):
        raise MMPoseFineTuneError("dataset safety flags are invalid")
    try:
        sealed_holdout_guard = load_m95_sealed_holdout_guard()
    except (OSError, TypeError, ValueError) as exc:
        raise MMPoseFineTuneError(
            f"cannot verify sealed-holdout registry binding: {exc}"
        ) from exc
    manifest_holdout = manifest.get("sealed_holdout_guard")
    _require_exact_keys(
        manifest_holdout, set(sealed_holdout_guard), "sealed_holdout_guard"
    )
    if (
        manifest_holdout.get("use_during_development") is not False
        or any(
            manifest_holdout.get(field) != sealed_holdout_guard[field]
            for field in sealed_holdout_guard
            if field != "use_during_development"
        )
    ):
        raise MMPoseFineTuneError("sealed-holdout registry binding mismatch")

    topology = manifest.get("topology")
    _require_exact_keys(
        topology,
        {"format", "count", "ordered_keypoints", "registry_path", "registry_sha256"},
        "topology",
    )
    if (
        not isinstance(topology, dict)
        or topology.get("format") != "halpe26"
        or topology.get("count") != 26
        or tuple(topology.get("ordered_keypoints", ())) != tuple(HALPE26_JOINTS)
    ):
        raise MMPoseFineTuneError("dataset topology is not exact Halpe26")
    topology_path, topology_hash = _bound_external_path(
        dataset_root,
        {
            "path": topology.get("registry_path"),
            "sha256": topology.get("registry_sha256"),
        },
        "topology registry",
    )
    registry = _read_object(topology_path, "topology registry")
    halpe = (registry.get("formats") or {}).get("halpe26")
    registry_names = (
        [item.get("name") for item in halpe.get("keypoints", [])]
        if isinstance(halpe, dict)
        else []
    )
    if not isinstance(halpe, dict) or halpe.get("count") != 26 or tuple(registry_names) != tuple(HALPE26_JOINTS):
        raise MMPoseFineTuneError("bound topology registry Halpe26 definition drift")

    governance_binding = manifest.get("governance")
    _require_exact_keys(governance_binding, {"path", "sha256"}, "governance")
    governance_path, governance_hash = _bound_external_path(
        dataset_root, governance_binding, "governance"
    )
    governance_rows = _read_governance(governance_path)
    truth_packs = manifest.get("truth_packs")
    if not isinstance(truth_packs, list) or not truth_packs:
        raise MMPoseFineTuneError("truth_packs must be non-empty")
    truth_video_ids: set[str] = set()
    truth_frame_count = 0
    truth_joint_value_count = 0
    for index, pack in enumerate(truth_packs):
        _require_exact_keys(
            pack,
            {
                "path",
                "pack_version",
                "validation_status",
                "video_ids",
                "task_count",
                "accepted_frame_count",
                "accepted_joint_value_count",
            },
            f"truth_packs[{index}]",
        )
        if not isinstance(pack.get("path"), str) or not pack["path"].strip():
            raise MMPoseFineTuneError(f"truth_packs[{index}].path must be non-empty")
        pack_path = Path(pack["path"])
        if not pack_path.is_absolute():
            pack_path = dataset_root / pack_path
        pack_path = pack_path.resolve()
        if (
            not isinstance(pack, dict)
            or not pack_path.is_dir()
            or not isinstance(pack.get("pack_version"), str)
            or not pack["pack_version"].strip()
            or pack.get("validation_status")
            != "ready_for_keypoint_error_evaluation"
        ):
            raise MMPoseFineTuneError(f"truth_packs[{index}] is not accepted truth")
        task_count = _positive_int(pack.get("task_count"), f"truth_packs[{index}].task_count")
        frame_count = _positive_int(
            pack.get("accepted_frame_count"),
            f"truth_packs[{index}].accepted_frame_count",
        )
        joint_value_count = _positive_int(
            pack.get("accepted_joint_value_count"),
            f"truth_packs[{index}].accepted_joint_value_count",
        )
        truth_video_ids.update(
            _nonempty_ids(pack.get("video_ids"), f"truth_packs[{index}].video_ids")
        )
        if task_count < joint_value_count:
            raise MMPoseFineTuneError(
                f"truth_packs[{index}] accepted values exceed tasks"
            )
        truth_frame_count += frame_count
        truth_joint_value_count += joint_value_count

    source_videos = manifest.get("source_videos")
    if not isinstance(source_videos, list) or not source_videos:
        raise MMPoseFineTuneError("source_videos must be non-empty")
    source_video_hashes: dict[str, str] = {}
    source_video_paths: dict[str, Path] = {}
    for index, source in enumerate(source_videos):
        _require_exact_keys(
            source, {"video_id", "path", "sha256"}, f"source_videos[{index}]"
        )
        raw_video_id = source.get("video_id")
        if not isinstance(raw_video_id, str):
            raise MMPoseFineTuneError("source_videos video_id must be a string")
        video_id = raw_video_id.strip()
        if not video_id or video_id in source_video_hashes:
            raise MMPoseFineTuneError("source_videos has a duplicate or blank video_id")
        path, observed = _bound_external_path(
            dataset_root,
            {"path": source.get("path"), "sha256": source.get("sha256")},
            f"source_videos[{index}]",
        )
        source_video_hashes[video_id] = observed
        source_video_paths[video_id] = path
    if len(set(source_video_hashes.values())) != len(source_video_hashes):
        raise MMPoseFineTuneError("source video SHA-256 is duplicated across video ids")
    if len(set(source_video_paths.values())) != len(source_video_paths):
        raise MMPoseFineTuneError("source video path is duplicated across video ids")
    if any(
        video_id == sealed_holdout_guard["video_id"]
        or str(source_video_paths[video_id]).casefold()
        == str(sealed_holdout_guard["video_path"]).casefold()
        or source_video_hashes[video_id] == sealed_holdout_guard["video_sha256"]
        for video_id in source_video_hashes
    ):
        raise MMPoseFineTuneError("sealed holdout appears in development dataset")
    images_declared = manifest.get("images")
    _require_exact_keys(images_declared, {"root", "manifest"}, "images")
    if (
        not isinstance(images_declared, dict)
        or images_declared.get("root") != "images"
        or images_declared.get("manifest") != "image-manifest.jsonl"
    ):
        raise MMPoseFineTuneError("images.root must be images")
    images_root = _path_within(dataset_root, "images", "images.root")
    if not images_root.is_dir():
        raise MMPoseFineTuneError("images root is missing")

    artifacts = _artifact_index(dataset_root, manifest.get("artifacts"))
    splits = manifest.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "val"}:
        raise MMPoseFineTuneError("dataset splits must be exactly train and val")
    split_results = {
        name: _validate_coco_split(dataset_root, images_root, name, splits[name], artifacts)
        for name in ("train", "val")
    }
    if any(
        result["topology_registry_sha256"] != topology_hash
        for result in split_results.values()
    ):
        raise MMPoseFineTuneError("COCO topology registry hash binding mismatch")
    if any(count < 1 for count in split_results["train"]["visible_counts_by_joint"]):
        raise MMPoseFineTuneError(
            "train split lacks visible supervision for one or more Halpe26 joints"
        )
    for dimension in (
        "image_ids",
        "annotation_ids",
        "file_names",
        "image_paths",
        "subject_ids",
        "session_ids",
        "video_ids",
    ):
        overlap = split_results["train"][dimension] & split_results["val"][dimension]
        if overlap:
            raise MMPoseFineTuneError(f"train/val {dimension} leakage: {sorted(overlap)!r}")
    split_video_ids = set().union(
        split_results["train"]["video_ids"], split_results["val"]["video_ids"]
    )
    if set(governance_rows) != split_video_ids:
        raise MMPoseFineTuneError(
            "governance and dataset split video ids differ"
        )
    for split_name, result in split_results.items():
        for image in result["image_by_id"].values():
            governance_row = governance_rows[str(image["video_id"])]
            if governance_row["split"] != split_name or any(
                governance_row[field] != image[field]
                for field in ("subject_id", "session_id", "camera_id")
            ):
                raise MMPoseFineTuneError(
                    f"{split_name} COCO metadata disagrees with governance"
                )
    if set(source_video_hashes) != split_video_ids or truth_video_ids != split_video_ids:
        raise MMPoseFineTuneError(
            "truth-pack, source-video, and dataset split video ids differ"
        )
    if truth_frame_count != sum(
        result["image_count"] for result in split_results.values()
    ) or truth_joint_value_count != sum(
        result["accepted_keypoint_count"] for result in split_results.values()
    ):
        raise MMPoseFineTuneError("truth-pack counts disagree with exported COCO truth")
    train_source_hashes = {
        source_video_hashes[video_id]
        for video_id in split_results["train"]["video_ids"]
    }
    val_source_hashes = {
        source_video_hashes[video_id]
        for video_id in split_results["val"]["video_ids"]
    }
    if train_source_hashes & val_source_hashes:
        raise MMPoseFineTuneError("train/val source-video SHA-256 leakage")

    image_manifest_declared = manifest.get("image_manifest")
    _require_exact_keys(
        image_manifest_declared,
        {"path", "sha256", "row_count"},
        "image_manifest",
    )
    image_manifest_path = _path_within(
        dataset_root, image_manifest_declared.get("path"), "image_manifest.path"
    )
    if image_manifest_path not in artifacts:
        raise MMPoseFineTuneError("image manifest lacks artifact binding")
    expected_image_manifest_hash = _require_sha256(
        image_manifest_declared.get("sha256"), "image_manifest.sha256"
    )
    if _sha256_file(image_manifest_path) != expected_image_manifest_hash:
        raise MMPoseFineTuneError("image manifest SHA-256 mismatch")
    image_rows = _read_jsonl(image_manifest_path, "image manifest")
    if _positive_int(
        image_manifest_declared.get("row_count"), "image_manifest.row_count"
    ) != len(image_rows):
        raise MMPoseFineTuneError("image_manifest.row_count mismatch")
    expected_rows = sum(item["image_count"] for item in split_results.values())
    if len(image_rows) != expected_rows:
        raise MMPoseFineTuneError("image manifest does not cover every COCO image")
    seen_rows: set[tuple[str, int]] = set()
    image_hashes_by_split: dict[str, set[str]] = {"train": set(), "val": set()}
    pixel_hashes_by_split: dict[str, set[str]] = {"train": set(), "val": set()}
    required_artifacts = {image_manifest_path}
    for item in split_results.values():
        required_artifacts.add(item["annotation"])
    for row in image_rows:
        _require_exact_keys(row, _IMAGE_MANIFEST_FIELDS, "image manifest row")
        if row.get("schema_version") != "1.0.0":
            raise MMPoseFineTuneError("image manifest schema_version must be 1.0.0")
        split_name = row.get("split")
        if split_name not in split_results:
            raise MMPoseFineTuneError("image manifest contains non-development split")
        image_id = _int_id(row.get("image_id"), "image manifest image_id")
        row_width = _positive_int(row.get("width"), "image manifest width")
        row_height = _positive_int(row.get("height"), "image manifest height")
        row_frame_index = _int_id(
            row.get("source_frame_index"), "image manifest source_frame_index"
        )
        if row_frame_index < 0:
            raise MMPoseFineTuneError(
                "image manifest source_frame_index must be non-negative"
            )
        key = (split_name, image_id)
        if key in seen_rows or image_id not in split_results[split_name]["image_by_id"]:
            raise MMPoseFineTuneError("image manifest image binding is invalid")
        coco_image = split_results[split_name]["image_by_id"][image_id]
        for field in (
            "file_name",
            "width",
            "height",
            "video_id",
            "source_frame_index",
            "subject_id",
            "session_id",
            "camera_id",
        ):
            if row.get(field) != coco_image.get(field):
                raise MMPoseFineTuneError(f"image manifest {field} disagrees with COCO")
        for flag in (
            "accuracy_claim",
            "holdout_test_used",
            "bbox_ground_truth_claim",
            "model_values_used_as_keypoint_truth",
        ):
            if row.get(flag) is not False:
                raise MMPoseFineTuneError(f"image manifest safety flag is invalid: {flag}")
        row_source_sha = _require_sha256(
            row.get("source_video_sha256"), "source_video_sha256"
        )
        if row_source_sha != source_video_hashes.get(str(row.get("video_id"))):
            raise MMPoseFineTuneError("image manifest source-video hash mismatch")
        image_path = _path_within(
            images_root, row.get("file_name"), "image manifest file_name"
        )
        if not image_path.is_file() or image_path not in artifacts:
            raise MMPoseFineTuneError("image file lacks artifact binding")
        image_hash = _require_sha256(row.get("sha256"), "image manifest sha256")
        if _sha256_file(image_path) != image_hash:
            raise MMPoseFineTuneError("image SHA-256 mismatch")
        if _require_sha256(
            artifacts[image_path].get("sha256"), "image artifact sha256"
        ) != image_hash:
            raise MMPoseFineTuneError("image artifact and manifest hashes disagree")
        if _positive_int(row.get("size_bytes"), "image manifest size_bytes") != image_path.stat().st_size:
            raise MMPoseFineTuneError("image manifest byte size mismatch")
        # Match MMPose LoadImage's default three-channel color decode exactly;
        # hashing IMREAD_UNCHANGED would let grayscale/color encodings of the
        # same training pixels evade cross-split duplicate detection.
        decoded = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if decoded is None:
            raise MMPoseFineTuneError("image artifact cannot be decoded")
        actual_height, actual_width = decoded.shape[:2]
        if row_width != actual_width or row_height != actual_height:
            raise MMPoseFineTuneError("image pixel dimensions disagree with manifest")
        image_hashes_by_split[split_name].add(image_hash)
        pixel_hashes_by_split[split_name].add(_decoded_pixel_sha256(decoded))
        required_artifacts.add(image_path)
        seen_rows.add(key)
    if image_hashes_by_split["train"] & image_hashes_by_split["val"]:
        raise MMPoseFineTuneError("train/val JPEG content SHA-256 leakage")
    if pixel_hashes_by_split["train"] & pixel_hashes_by_split["val"]:
        raise MMPoseFineTuneError("train/val decoded-pixel leakage")
    if set(artifacts) != required_artifacts:
        raise MMPoseFineTuneError("artifacts must exactly bind annotations, image manifest, and images")

    return {
        "root": dataset_root,
        "manifest_path": manifest_path,
        "manifest_sha256": observed_manifest_hash,
        "image_root_relative": "images/",
        "train_annotation_relative": str(
            split_results["train"]["annotation"].relative_to(dataset_root)
        ).replace("\\", "/"),
        "val_annotation_relative": str(
            split_results["val"]["annotation"].relative_to(dataset_root)
        ).replace("\\", "/"),
        "input_fingerprint_sha256": input_readiness["input_fingerprint_sha256"].upper(),
        "topology_registry_path": topology_path,
        "topology_registry_sha256": topology_hash,
        "governance_path": governance_path,
        "governance_sha256": governance_hash,
        "image_manifest_path": image_manifest_path,
        "image_manifest_sha256": expected_image_manifest_hash,
        "artifact_count": len(artifacts),
        "sealed_holdout_guard": sealed_holdout_guard,
        "source_videos": [
            {
                "video_id": video_id,
                "path": source_video_paths[video_id],
                "sha256": source_video_hashes[video_id],
            }
            for video_id in sorted(source_video_hashes)
        ],
        "splits": {
            name: {
                "image_count": result["image_count"],
                "annotation_count": result["annotation_count"],
                "visible_keypoint_count": result["visible_keypoint_count"],
                "masked_keypoint_count": result["masked_keypoint_count"],
                "subject_ids": sorted(result["subject_ids"]),
                "session_ids": sorted(result["session_ids"]),
                "video_ids": sorted(result["video_ids"]),
            }
            for name, result in split_results.items()
        },
    }


def _resolved_config_text(inputs: dict[str, Any], dataset: dict[str, Any], output: Path) -> str:
    template = str(inputs["template"]).replace("\\", "/")
    dataset_root = str(dataset["root"]).replace("\\", "/") + "/"
    checkpoint = str(inputs["checkpoint"]).replace("\\", "/")
    work_dir = str(output / "work").replace("\\", "/")
    return (
        "# Generated by rallymate_training.mmpose_finetune; do not edit.\n"
        f"_base_ = [{template!r}]\n"
        f"rallymate_dataset_root = {dataset_root!r}\n"
        f"rallymate_train_annotation = {dataset['train_annotation_relative']!r}\n"
        f"rallymate_val_annotation = {dataset['val_annotation_relative']!r}\n"
        f"rallymate_image_root = {dataset['image_root_relative']!r}\n"
        f"load_from = {checkpoint!r}\n"
        f"work_dir = {work_dir!r}\n"
        "train_dataloader = dict(dataset=dict(\n"
        "    data_root=rallymate_dataset_root,\n"
        "    ann_file=rallymate_train_annotation,\n"
        "    data_prefix=dict(img=rallymate_image_root)))\n"
        "val_dataloader = dict(dataset=dict(\n"
        "    data_root=rallymate_dataset_root,\n"
        "    ann_file=rallymate_val_annotation,\n"
        "    data_prefix=dict(img=rallymate_image_root)))\n"
        "test_dataloader = dict(dataset=dict(\n"
        "    data_root=rallymate_dataset_root,\n"
        "    ann_file=rallymate_val_annotation,\n"
        "    data_prefix=dict(img=rallymate_image_root)))\n"
    )


def _runtime_config_audit(
    runtime_python: Path,
    config_path: Path,
    *,
    expected_train_count: int,
    expected_val_count: int,
    expected_train_masked_count: int,
) -> dict[str, Any]:
    probe = r"""
import json, platform, sys
import mmcv, mmdet, mmengine, mmpose, torch
from mmengine.config import Config
c = Config.fromfile(sys.argv[1])
payload = {
    'python_executable': sys.executable,
    'python_version': platform.python_version(),
    'mmengine': mmengine.__version__,
    'mmcv': mmcv.__version__,
    'mmpose': mmpose.__version__,
    'mmdet': mmdet.__version__,
    'torch': torch.__version__,
    'torch_cuda': torch.version.cuda,
    'cuda_available': torch.cuda.is_available(),
    'cuda_device_count': torch.cuda.device_count(),
    'cuda_device_name': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    'cuda_device_total_memory': (
        torch.cuda.get_device_properties(0).total_memory
        if torch.cuda.is_available() else None),
    'train_dataset_type': c.train_dataloader.dataset.type,
    'val_dataset_type': c.val_dataloader.dataset.type,
    'test_dataset_type': c.test_dataloader.dataset.type,
    'train_ann_file': c.train_dataloader.dataset.ann_file,
    'val_ann_file': c.val_dataloader.dataset.ann_file,
    'test_ann_file': c.test_dataloader.dataset.ann_file,
    'train_data_root': c.train_dataloader.dataset.data_root,
    'val_data_root': c.val_dataloader.dataset.data_root,
    'test_data_root': c.test_dataloader.dataset.data_root,
    'train_image_root': c.train_dataloader.dataset.data_prefix.img,
    'val_image_root': c.val_dataloader.dataset.data_prefix.img,
    'test_image_root': c.test_dataloader.dataset.data_prefix.img,
    'train_batch_size': c.train_dataloader.batch_size,
    'val_batch_size': c.val_dataloader.batch_size,
    'optim_wrapper_type': c.optim_wrapper.type,
    'random_seed': c.randomness.seed,
    'deterministic': c.randomness.deterministic,
    'load_from': c.load_from,
    'work_dir': c.work_dir,
    'default_scope': c.default_scope,
    'backbone_scope': c.model.backbone.get('_scope_'),
    'backbone_init_cfg': c.model.backbone.get('init_cfg'),
    'execution_runtime_ready': False,
    'dataset_construction_valid': False,
    'execution_blocker': None,
}
try:
    import types
    module_name = 'mmpose.models.heads.transformer_heads'
    if module_name not in sys.modules:
        stub = types.ModuleType(module_name)
        stub.EDPoseHead = None
        sys.modules[module_name] = stub
    from mmpose.registry import DATASETS
    from mmpose.utils import register_all_modules
    register_all_modules(init_default_scope=True)
    train_dataset = DATASETS.build(c.train_dataloader.dataset)
    val_dataset = DATASETS.build(c.val_dataloader.dataset)
    test_dataset = DATASETS.build(c.test_dataloader.dataset)
    raw_mask_counts = [
        int((train_dataset.get_data_info(index)['keypoints_visible'] == 0).sum())
        for index in range(len(train_dataset))
    ]
    masked_sample_index = next(
        (index for index, count in enumerate(raw_mask_counts) if count), 0)
    train_sample = train_dataset[masked_sample_index]
    weights = train_sample['data_samples'].gt_instance_labels.keypoint_weights
    payload.update({
        'execution_runtime_ready': bool(torch.cuda.is_available()),
        'dataset_construction_valid': True,
        'train_dataset_length': len(train_dataset),
        'val_dataset_length': len(val_dataset),
        'test_dataset_length': len(test_dataset),
        'train_sample_keypoint_weight_count': int(
            weights.numel() if hasattr(weights, 'numel') else weights.size),
        'train_sample_zero_weight_count': int((weights == 0).sum()),
        'train_raw_masked_keypoint_count': sum(raw_mask_counts),
        'selected_train_raw_masked_count': raw_mask_counts[masked_sample_index],
    })
    if not torch.cuda.is_available():
        payload['execution_blocker'] = 'CUDA GPU is required for local RTMPose-X AMP fine-tuning'
except Exception as exc:
    payload['execution_blocker'] = type(exc).__name__ + ': ' + str(exc)
print('RALLYMATE_CONFIG_AUDIT=' + json.dumps(payload, sort_keys=True))
"""
    try:
        result = subprocess.run(
            [
                str(runtime_python),
                "-c",
                probe,
                str(config_path),
                str(expected_train_count),
                str(expected_val_count),
                str(expected_train_masked_count),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MMPoseFineTuneError(f"MMPose config audit failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise MMPoseFineTuneError(f"MMPose config does not parse: {detail}")
    marker = "RALLYMATE_CONFIG_AUDIT="
    line = next((item for item in result.stdout.splitlines() if item.startswith(marker)), None)
    if line is None:
        raise MMPoseFineTuneError("MMPose config audit returned no fingerprint")
    payload = json.loads(line[len(marker) :])
    if {payload.get(f"{split}_dataset_type") for split in ("train", "val", "test")} != {"CocoDataset"}:
        raise MMPoseFineTuneError("resolved dataloaders are not single CocoDataset loaders")
    if payload.get("train_ann_file") == payload.get("val_ann_file"):
        raise MMPoseFineTuneError("resolved train and val annotations are not isolated")
    if payload.get("test_ann_file") != payload.get("val_ann_file"):
        raise MMPoseFineTuneError("resolved test loader must be the explicit val alias")
    if payload.get("optim_wrapper_type") != "AmpOptimWrapper":
        raise MMPoseFineTuneError("resolved config does not enable AMP")
    if payload.get("train_batch_size", 999) > 4 or payload.get("val_batch_size", 999) > 4:
        raise MMPoseFineTuneError("resolved config batch size is not locally bounded")
    if payload.get("random_seed") != 20260904 or payload.get("deterministic") is not True:
        raise MMPoseFineTuneError("resolved config seed/determinism drift")
    if (
        payload.get("default_scope") != "mmpose"
        or payload.get("backbone_scope") != "mmpose"
        or payload.get("backbone_init_cfg") is not None
    ):
        raise MMPoseFineTuneError("resolved config may import remote/MMDetection backbone")
    if payload.get("execution_runtime_ready"):
        if (
            payload.get("train_dataset_length") != expected_train_count
            or payload.get("val_dataset_length") != expected_val_count
            or payload.get("test_dataset_length")
            != expected_val_count
            or payload.get("train_sample_keypoint_weight_count") != 26
            or payload.get("train_raw_masked_keypoint_count")
            != expected_train_masked_count
            or payload.get("train_sample_zero_weight_count", 0)
            < payload.get("selected_train_raw_masked_count", 0)
        ):
            raise MMPoseFineTuneError(
                "resolved MMPose dataset construction/mask audit failed"
            )
    payload["host_platform"] = platform.platform()
    payload["config_parse_valid"] = True
    return payload


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _transition_run_record(
    record_path: Path,
    *,
    status: str,
    training_started: bool,
    error: str | None = None,
) -> dict[str, Any]:
    record = _read_object(record_path, "fine-tune run record")
    record["status"] = status
    record["training_started"] = training_started
    record["training_completed"] = status == "training_completed"
    event: dict[str, Any] = {
        "status": status,
        "at": _utc_now(),
        "training_started": training_started,
    }
    if error is not None:
        event["error"] = error
        record["last_error"] = error
    record.setdefault("lifecycle", []).append(event)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=record_path.parent,
        prefix=f".{record_path.name}.",
        suffix=".tmp",
    ) as handle:
        json.dump(
            record,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(record_path)
    return record


def _validate_execute_environment(runtime_python: Path) -> None:
    if Path(sys.executable).resolve() != runtime_python.resolve():
        raise MMPoseFineTuneError(
            "--execute must run under runtime/rtmpose/.venv/Scripts/python.exe"
        )


def prepare_or_run_rtmpose_finetune(
    dataset_manifest: str | Path,
    dataset_manifest_sha256: str,
    output_dir: str | Path,
    *,
    execute: bool = False,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Validate, materialize, and optionally execute one immutable run plan."""

    root = _workspace_root(workspace)
    output = Path(output_dir).resolve()
    if output.exists():
        raise MMPoseFineTuneError(f"output directory already exists: {output}")
    inputs = _validate_exact_model_inputs(root)
    dataset = _validate_dataset_manifest(
        Path(dataset_manifest).resolve(), dataset_manifest_sha256
    )
    if execute:
        _validate_execute_environment(inputs["runtime_python"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.staging-", dir=output.parent
    ) as temp:
        staging = Path(temp)
        staged_config = staging / RESOLVED_CONFIG_NAME
        staged_config.write_text(
            _resolved_config_text(inputs, dataset, output), encoding="utf-8"
        )
        runtime = _runtime_config_audit(
            inputs["runtime_python"],
            staged_config,
            expected_train_count=dataset["splits"]["train"]["annotation_count"],
            expected_val_count=dataset["splits"]["val"]["annotation_count"],
            expected_train_masked_count=dataset["splits"]["train"][
                "masked_keypoint_count"
            ],
        )
        if (
            Path(str(runtime.get("load_from"))).resolve() != inputs["checkpoint"]
            or Path(str(runtime.get("work_dir"))).resolve() != (output / "work").resolve()
            or runtime.get("train_ann_file") != dataset["train_annotation_relative"]
            or runtime.get("val_ann_file") != dataset["val_annotation_relative"]
            or runtime.get("test_ann_file") != dataset["val_annotation_relative"]
            or any(
                Path(str(runtime.get(f"{split}_data_root"))).resolve()
                != dataset["root"]
                for split in ("train", "val", "test")
            )
            or any(
                runtime.get(f"{split}_image_root") != dataset["image_root_relative"]
                for split in ("train", "val", "test")
            )
        ):
            raise MMPoseFineTuneError("resolved dataset/checkpoint/work-dir binding drift")
        runtime_ready = bool(runtime.get("execution_runtime_ready"))
        resolved_hash = _sha256_file(staged_config)
        initial_status = (
            "validated_ready_to_start"
            if execute and runtime_ready
            else "execution_blocked"
            if execute
            else "dry_run_validated"
            if runtime_ready
            else "dry_run_blocked"
        )
        run_record: dict[str, Any] = {
            "schema_version": "1.0.0",
            "plan_version": PLAN_VERSION,
            "status": initial_status,
            "mode": "execute" if execute else "dry_run",
            "training_started": False,
            "training_completed": False,
            "accuracy_claim": False,
            "promotion_claim": False,
            "production_enabled": False,
            "immutable_output": True,
            "gates": {
                "dataset_ready": True,
                "source_hashes_verified": True,
                "halpe26_topology_verified": True,
                "train_val_isolation_verified": True,
                "sealed_holdout_used": False,
                "base_config_exact": True,
                "checkpoint_exact": True,
                "resolved_config_parses": True,
                "runtime_execution_ready": runtime_ready,
                "all_training_gates_ready": runtime_ready,
            },
            "inputs": {
                "dataset_manifest": str(dataset["manifest_path"]),
                "dataset_manifest_sha256": dataset["manifest_sha256"],
                "dataset_input_fingerprint_sha256": dataset[
                    "input_fingerprint_sha256"
                ],
                "topology_registry": str(dataset["topology_registry_path"]),
                "topology_registry_sha256": dataset[
                    "topology_registry_sha256"
                ],
                "governance": str(dataset["governance_path"]),
                "governance_sha256": dataset["governance_sha256"],
                "image_manifest": str(dataset["image_manifest_path"]),
                "image_manifest_sha256": dataset["image_manifest_sha256"],
                "artifact_count": dataset["artifact_count"],
                "sealed_holdout_guard": dataset["sealed_holdout_guard"],
                "source_videos": [
                    {
                        **item,
                        "path": str(item["path"]),
                    }
                    for item in dataset["source_videos"]
                ],
                "base_config": str(inputs["base_config"]),
                "base_config_sha256": inputs["base_config_sha256"],
                "checkpoint": str(inputs["checkpoint"]),
                "checkpoint_sha256": inputs["checkpoint_sha256"],
                "checkpoint_bytes": inputs["checkpoint_bytes"],
                "config_template": str(inputs["template"]),
                "config_template_sha256": inputs["template_sha256"],
            },
            "dataset": {
                "topology": "halpe26",
                "partial_keypoint_mask": "COCO_v0_zero_SimCC_target_weight",
                "splits": dataset["splits"],
            },
            "configuration": {
                "resolved_config": str(output / RESOLVED_CONFIG_NAME),
                "resolved_config_sha256": resolved_hash,
                "model": "RTMPose-X",
                "input_size_hw": [384, 288],
                "train_batch_size": runtime["train_batch_size"],
                "val_batch_size": runtime["val_batch_size"],
                "amp": True,
                "seed": runtime["random_seed"],
                "test_split_policy": "validation_alias_no_sealed_holdout",
            },
            "runtime": {
                **runtime,
                "runtime_python_sha256": inputs["runtime_python_sha256"],
            },
            "lifecycle": [
                {
                    "status": initial_status,
                    "at": _utc_now(),
                    "training_started": False,
                }
            ],
        }
        _write_json(staging / RUN_RECORD_NAME, run_record)
        try:
            staging.rename(output)
        except FileExistsError as exc:
            raise MMPoseFineTuneError(
                f"output directory appeared during creation: {output}"
            ) from exc

    if execute:
        if not runtime_ready:
            raise MMPoseFineTuneError(
                f"execution runtime is not ready: {runtime.get('execution_blocker')}",
                status="execution_blocked",
            )
        return _run_mmengine(
            output / RESOLVED_CONFIG_NAME,
            inputs["runtime_python"],
            output / RUN_RECORD_NAME,
        )
    return run_record


def _build_mmengine_runner(config_path: Path) -> tuple[Any, type, Any]:
    import torch
    from mmengine.config import Config
    from mmengine.hooks import Hook
    from mmengine.runner import Runner

    config = Config.fromfile(str(config_path))
    checkpoint = Path(str(config.load_from)).resolve()
    if (
        not checkpoint.is_file()
        or _sha256_file(checkpoint) != APPROVED_CHECKPOINT_SHA256
        or checkpoint.stat().st_size != APPROVED_CHECKPOINT_BYTES
    ):
        raise MMPoseFineTuneError("resolved execution checkpoint binding drift")
    if (
        config.default_scope != "mmpose"
        or config.model.backbone.get("_scope_") != "mmpose"
        or config.model.backbone.get("init_cfg") is not None
    ):
        raise MMPoseFineTuneError("resolved execution backbone binding drift")

    import types

    module_name = "mmpose.models.heads.transformer_heads"
    if module_name not in sys.modules:
        stub = types.ModuleType(module_name)
        stub.EDPoseHead = None
        sys.modules[module_name] = stub
    from mmpose.utils import register_all_modules

    original_torch_load = torch.load

    def trusted_checkpoint_load(*args: Any, **kwargs: Any) -> Any:
        # PyTorch >=2.6 otherwise rejects metadata in this already hash-pinned
        # official checkpoint. Unsafe pickle loading is permitted only for the
        # approved file and is revalidated at the exact moment it is opened.
        source = args[0] if args else kwargs.get("f")
        try:
            source_path = Path(source).resolve()
        except (TypeError, ValueError, OSError):
            source_path = None
        if source_path == checkpoint:
            if (
                not checkpoint.is_file()
                or _sha256_file(checkpoint) != APPROVED_CHECKPOINT_SHA256
                or checkpoint.stat().st_size != APPROVED_CHECKPOINT_BYTES
            ):
                raise MMPoseFineTuneError(
                    "approved checkpoint changed immediately before loading"
                )
            kwargs["weights_only"] = False
        else:
            if kwargs.get("weights_only") is False:
                raise MMPoseFineTuneError(
                    "unsafe torch.load denied for an unapproved artifact"
                )
            kwargs.setdefault("weights_only", True)
        return original_torch_load(*args, **kwargs)

    torch.load = trusted_checkpoint_load
    try:
        register_all_modules(init_default_scope=True)
        runner = Runner.from_cfg(config)
    except BaseException:
        torch.load = original_torch_load
        raise

    def restore_torch_load() -> None:
        torch.load = original_torch_load

    return runner, Hook, restore_torch_load


def _run_mmengine(
    config_path: Path,
    runtime_python: Path,
    record_path: Path,
) -> dict[str, Any]:
    _validate_execute_environment(runtime_python)
    try:
        runner, hook_base, restore_torch_load = _build_mmengine_runner(config_path)
    except BaseException as exc:
        message = f"{type(exc).__name__}: {exc}"
        interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
        status = (
            "runner_initialization_interrupted"
            if interrupted
            else "runner_initialization_failed"
        )
        _transition_run_record(
            record_path,
            status=status,
            training_started=False,
            error=message,
        )
        if interrupted:
            raise
        raise MMPoseFineTuneError(
            f"mmengine Runner initialization failed: {exc}",
            status=status,
        ) from exc

    try:
        _transition_run_record(
            record_path,
            status="runner_initialized",
            training_started=False,
        )

        class TrainingStateHook(hook_base):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:
                self.started = False

            def before_train(self, runner: Any) -> None:
                _transition_run_record(
                    record_path,
                    status="training_running",
                    training_started=True,
                )
                self.started = True

        state_hook = TrainingStateHook()
        try:
            runner.register_hook(state_hook, priority="HIGHEST")
            runner.train()
        except BaseException as exc:
            message = f"{type(exc).__name__}: {exc}"
            interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
            status = (
                "training_interrupted"
                if interrupted and state_hook.started
                else "runner_start_interrupted"
                if interrupted
                else "training_failed"
                if state_hook.started
                else "runner_start_failed"
            )
            _transition_run_record(
                record_path,
                status=status,
                training_started=state_hook.started,
                error=message,
            )
            if interrupted:
                raise
            raise MMPoseFineTuneError(
                f"mmengine training failed: {exc}",
                training_started=state_hook.started,
                status=status,
            ) from exc
        return _transition_run_record(
            record_path,
            status="training_completed",
            training_started=True,
        )
    finally:
        restore_torch_load()


__all__ = [
    "APPROVED_BASE_CONFIG_SHA256",
    "APPROVED_CHECKPOINT_SHA256",
    "DATASET_MANIFEST_VERSION",
    "MMPoseFineTuneError",
    "PLAN_VERSION",
    "prepare_or_run_rtmpose_finetune",
]
