from __future__ import annotations

import csv
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


class DatasetError(ValueError):
    """Raised when data cannot be trained safely or reproducibly."""


REQUIRED_COLUMNS = {
    "image_path",
    "session_id",
    "subject_id",
    "camera_id",
    "consent",
    "license",
}
VALID_SPLITS = {"train", "val", "test"}
YES_VALUES = {"1", "true", "yes", "y", "已授权"}


@dataclass(frozen=True)
class DatasetRow:
    image_path: Path
    label_path: Path
    session_id: str
    subject_id: str
    camera_id: str
    consent: str
    license: str
    split: str | None


def _inferred_label_path(image_path: Path) -> Path:
    parts = list(image_path.parts)
    image_indexes = [index for index, part in enumerate(parts) if part == "images"]
    if not image_indexes:
        raise DatasetError(
            f"image path must contain an 'images' directory: {image_path}"
        )
    parts[image_indexes[-1]] = "labels"
    return Path(*parts).with_suffix(".txt")


def load_manifest(path: str | Path, require_files: bool = True) -> list[DatasetRow]:
    manifest_path = Path(path).resolve()
    if not manifest_path.exists():
        raise DatasetError(f"dataset manifest does not exist: {manifest_path}")
    base = manifest_path.parent
    rows: list[DatasetRow] = []
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise DatasetError(
                f"dataset manifest is missing columns: {sorted(missing)}"
            )
        for line_number, raw in enumerate(reader, start=2):
            image_value = (raw.get("image_path") or "").strip()
            if not image_value:
                raise DatasetError(f"line {line_number}: image_path is empty")
            image_path = Path(image_value)
            if not image_path.is_absolute():
                image_path = (base / image_path).resolve()
            inferred_label = _inferred_label_path(image_path)
            label_value = (raw.get("label_path") or "").strip()
            label_path = Path(label_value) if label_value else inferred_label
            if not label_path.is_absolute():
                label_path = (base / label_path).resolve()
            if label_path != inferred_label:
                raise DatasetError(
                    f"line {line_number}: label_path must mirror images/ as labels/: "
                    f"{inferred_label}"
                )
            split = (raw.get("split") or "").strip().lower() or None
            if split is not None and split not in VALID_SPLITS:
                raise DatasetError(
                    f"line {line_number}: split must be train, val or test"
                )
            values = {
                key: (raw.get(key) or "").strip()
                for key in ("session_id", "subject_id", "camera_id", "consent", "license")
            }
            if any(not value for value in values.values()):
                raise DatasetError(
                    f"line {line_number}: governance fields must not be empty"
                )
            if values["consent"].lower() not in YES_VALUES:
                raise DatasetError(
                    f"line {line_number}: media has no affirmative consent"
                )
            if require_files and (not image_path.exists() or not label_path.exists()):
                raise DatasetError(
                    f"line {line_number}: image or label is missing: "
                    f"{image_path}, {label_path}"
                )
            rows.append(
                DatasetRow(
                    image_path=image_path,
                    label_path=label_path,
                    split=split,
                    **values,
                )
            )
    if not rows:
        raise DatasetError("dataset manifest contains no samples")
    return rows


def _assigned_split(group_id: str, seed: int, train: float, val: float) -> str:
    digest = hashlib.sha256(f"{seed}:{group_id}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") / float(2**64)
    if value < train:
        return "train"
    if value < train + val:
        return "val"
    return "test"


def assign_group_splits(
    rows: Iterable[DatasetRow],
    seed: int = 42,
    train: float = 0.70,
    val: float = 0.15,
    test: float = 0.15,
) -> dict[str, list[DatasetRow]]:
    rows = list(rows)
    if not math.isclose(train + val + test, 1.0, abs_tol=1e-8):
        raise DatasetError("train + val + test ratios must equal 1")
    if min(train, val, test) <= 0:
        raise DatasetError("all split ratios must be greater than zero")

    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for row in rows:
        union(f"session:{row.session_id}", f"subject:{row.subject_id}")

    grouped: dict[str, list[DatasetRow]] = defaultdict(list)
    for row in rows:
        grouped[find(f"session:{row.session_id}")].append(row)
    result = {split: [] for split in ("train", "val", "test")}
    for group_id, group_rows in sorted(grouped.items()):
        explicit = {row.split for row in group_rows if row.split is not None}
        if len(explicit) > 1:
            raise DatasetError(
                "connected session/subject group appears in multiple explicit splits: "
                f"{group_id!r}"
            )
        split = next(iter(explicit)) if explicit else _assigned_split(
            group_id, seed, train, val
        )
        result[split].extend(group_rows)
    return result


def _parse_label_line(
    line: str,
    task: str,
    class_count: int,
    keypoint_count: int,
    keypoint_dims: int,
) -> None:
    try:
        values = [float(value) for value in line.split()]
    except ValueError as exc:
        raise DatasetError(f"label contains a non-numeric value: {line!r}") from exc
    expected = 5 if task == "detect" else 5 + keypoint_count * keypoint_dims
    if len(values) != expected:
        raise DatasetError(
            f"{task} label requires {expected} values but got {len(values)}"
        )
    class_id = values[0]
    if class_id != int(class_id) or not 0 <= int(class_id) < class_count:
        raise DatasetError(f"class id is out of range: {class_id}")
    if any(not math.isfinite(value) for value in values):
        raise DatasetError("label contains a non-finite value")
    if any(not 0.0 <= value <= 1.0 for value in values[1:5]):
        raise DatasetError("bounding box coordinates must be normalized to 0..1")
    if task == "pose":
        keypoints = values[5:]
        for index in range(0, len(keypoints), keypoint_dims):
            x, y = keypoints[index : index + 2]
            if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
                raise DatasetError("keypoint coordinates must be normalized to 0..1")
            if keypoint_dims == 3 and not 0.0 <= keypoints[index + 2] <= 2.0:
                raise DatasetError("keypoint visibility must be in the 0..2 range")


def audit_labels(
    rows: Iterable[DatasetRow],
    task: str,
    class_names: list[str],
    keypoint_count: int = 17,
    keypoint_dims: int = 3,
) -> dict:
    rows = list(rows)
    if task not in {"detect", "pose"}:
        raise DatasetError("task must be detect or pose")
    if not class_names or any(not str(name).strip() for name in class_names):
        raise DatasetError("class_names must contain at least one non-empty name")
    label_count = 0
    empty_labels = 0
    for row in rows:
        content = row.label_path.read_text(encoding="utf-8").strip()
        if not content:
            empty_labels += 1
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            try:
                _parse_label_line(
                    line,
                    task,
                    len(class_names),
                    keypoint_count,
                    keypoint_dims,
                )
            except DatasetError as exc:
                raise DatasetError(
                    f"{row.label_path}:{line_number}: {exc}"
                ) from exc
            label_count += 1
    return {
        "samples": len(rows),
        "labels": label_count,
        "empty_label_files": empty_labels,
        "task": task,
        "classes": class_names,
        "keypoint_shape": (
            [keypoint_count, keypoint_dims] if task == "pose" else None
        ),
    }


def prepare_dataset(
    manifest: str | Path,
    output_dir: str | Path,
    task: str,
    class_names: list[str],
    seed: int = 42,
    require_files: bool = True,
    keypoint_count: int = 17,
    keypoint_dims: int = 3,
) -> dict:
    rows = load_manifest(manifest, require_files=require_files)
    audit = (
        audit_labels(
            rows,
            task,
            class_names,
            keypoint_count=keypoint_count,
            keypoint_dims=keypoint_dims,
        )
        if require_files
        else {"skipped": True}
    )
    splits = assign_group_splits(rows, seed=seed)
    empty_splits = [split for split, split_rows in splits.items() if not split_rows]
    if empty_splits:
        raise DatasetError(
            "dataset is too small or too connected to create all splits; "
            f"empty splits: {empty_splits}. Add independent subjects/sessions or "
            "assign explicit splits."
        )
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for split, split_rows in splits.items():
        (output / f"{split}.txt").write_text(
            "\n".join(str(row.image_path) for row in split_rows) + "\n",
            encoding="utf-8",
        )
    dataset_yaml = {
        "path": str(output),
        "train": str(output / "train.txt"),
        "val": str(output / "val.txt"),
        "test": str(output / "test.txt"),
        "names": {index: name for index, name in enumerate(class_names)},
    }
    if task == "pose":
        dataset_yaml["kpt_shape"] = [keypoint_count, keypoint_dims]
    yaml_path = output / "dataset.yaml"
    yaml_path.write_text(
        yaml.safe_dump(dataset_yaml, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    sessions_by_split = {
        split: sorted({row.session_id for row in split_rows})
        for split, split_rows in splits.items()
    }
    overlap = (
        set(sessions_by_split["train"]) & set(sessions_by_split["val"])
        | set(sessions_by_split["train"]) & set(sessions_by_split["test"])
        | set(sessions_by_split["val"]) & set(sessions_by_split["test"])
    )
    if overlap:
        raise DatasetError(f"session leakage detected: {sorted(overlap)}")
    subjects_by_split = {
        split: {row.subject_id for row in split_rows}
        for split, split_rows in splits.items()
    }
    subject_overlap = (
        subjects_by_split["train"] & subjects_by_split["val"]
        | subjects_by_split["train"] & subjects_by_split["test"]
        | subjects_by_split["val"] & subjects_by_split["test"]
    )
    if subject_overlap:
        raise DatasetError(f"subject leakage detected: {sorted(subject_overlap)}")
    return {
        "dataset_yaml": str(yaml_path),
        "sample_counts": {key: len(value) for key, value in splits.items()},
        "session_counts": {
            key: len(value) for key, value in sessions_by_split.items()
        },
        "subject_count": len({row.subject_id for row in rows}),
        "camera_count": len({row.camera_id for row in rows}),
        "audit": audit,
        "leakage_check": "session_and_subject_passed",
    }
