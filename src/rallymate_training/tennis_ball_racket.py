"""3-class tennis ball, racket, and racket-head fine-tuning config, validator, and pseudo-label bootstrapper."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from rallymate_training.config import QualityGates, TrainingConfigError, load_train_config
from rallymate_training.dataset import DatasetError, audit_labels, load_manifest
from rallymate_vision.ball_racket_tracker import KalmanBallTracker


TENNIS_DETECTION_CLASSES = ("tennis_ball", "tennis_racket", "racket_head")
BOOTSTRAP_LABEL_ORIGIN = "bootstrap_pseudo_label_unverified"

_BALL_INPUT_ALIASES = frozenset({"ball", "sports ball", "tennis_ball"})
_RACKET_INPUT_ALIASES = frozenset({"racket", "tennis racket", "tennis_racket"})
_RACKET_HEAD_INPUT_ALIASES = frozenset({"racket_head"})


class TennisBallRacketError(ValueError):
    """Raised when tennis ball/racket configuration, manifest, or bootstrap input is invalid."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class TennisBallRacketAugmentations:
    mosaic: float = 1.0
    mixup: float = 0.10
    scale: float = 0.50
    hsv_h: float = 0.015
    hsv_s: float = 0.70
    hsv_v: float = 0.40
    degrees: float = 12.0
    translate: float = 0.10
    fliplr: float = 0.50
    copy_paste: float = 0.15
    close_mosaic: int = 10
    motion_blur_prob: float = 0.35
    motion_blur_kernel_range: tuple[int, int] = (3, 9)


@dataclass(frozen=True)
class TennisBallRacketConfig:
    name: str
    task: str
    base_model: Path
    data: Path
    output_dir: Path
    registry_dir: Path
    classes: tuple[str, ...] = TENNIS_DETECTION_CLASSES
    epochs: int = 120
    image_size: int = 1280
    batch: int = 8
    device: str = "0"
    workers: int = 4
    patience: int = 25
    seed: int = 42
    freeze: int = 0
    optimizer: str = "auto"
    learning_rate: float | None = 0.002
    model_name: str = "rallymate-tennis-ball-racket"
    license: str = "internal-evaluation-only"
    multi_scale: bool = True
    min_ball_box_px: int = 4
    max_ball_aspect_ratio: float = 4.5
    augmentations: TennisBallRacketAugmentations = TennisBallRacketAugmentations()
    quality_gates: QualityGates = QualityGates(map50_95=0.42, precision=0.80, recall=0.75)
    config_path: Path | None = None
    config_sha256: str = ""


def load_tennis_ball_racket_config(path: str | Path) -> TennisBallRacketConfig:
    """Load and validate a 3-class tennis ball + racket + racket_head training config."""
    config_path = Path(path).resolve()
    try:
        base_cfg = load_train_config(config_path)
    except TrainingConfigError as exc:
        raise TennisBallRacketError(str(exc)) from exc
    if base_cfg.task != "detect":
        raise TennisBallRacketError("tennis ball/racket config task must be detect")

    raw_text = config_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text) or {}
    classes = tuple(str(c).strip() for c in (raw.get("classes") or TENNIS_DETECTION_CLASSES))
    if classes != TENNIS_DETECTION_CLASSES:
        raise TennisBallRacketError(
            f"classes must exactly match {list(TENNIS_DETECTION_CLASSES)}, got {list(classes)}"
        )

    aug_raw = raw.get("augmentations") or {}
    blur_range = aug_raw.get("motion_blur_kernel_range", [3, 9])
    if not isinstance(blur_range, (list, tuple)) or len(blur_range) != 2:
        raise TennisBallRacketError("augmentations.motion_blur_kernel_range must have 2 integers")

    aug = TennisBallRacketAugmentations(
        mosaic=float(aug_raw.get("mosaic", 1.0)),
        mixup=float(aug_raw.get("mixup", 0.10)),
        scale=float(aug_raw.get("scale", 0.50)),
        hsv_h=float(aug_raw.get("hsv_h", 0.015)),
        hsv_s=float(aug_raw.get("hsv_s", 0.70)),
        hsv_v=float(aug_raw.get("hsv_v", 0.40)),
        degrees=float(aug_raw.get("degrees", 12.0)),
        translate=float(aug_raw.get("translate", 0.10)),
        fliplr=float(aug_raw.get("fliplr", 0.50)),
        copy_paste=float(aug_raw.get("copy_paste", 0.15)),
        close_mosaic=int(aug_raw.get("close_mosaic", 10)),
        motion_blur_prob=float(aug_raw.get("motion_blur_prob", 0.35)),
        motion_blur_kernel_range=(int(blur_range[0]), int(blur_range[1])),
    )
    for field_name in ("mosaic", "mixup", "scale", "hsv_h", "hsv_s", "hsv_v", "translate", "fliplr", "copy_paste", "motion_blur_prob"):
        val = getattr(aug, field_name)
        if not 0.0 <= val <= 1.0:
            raise TennisBallRacketError(f"augmentations.{field_name} must be in [0, 1], got {val}")
    if base_cfg.image_size < 640:
        raise TennisBallRacketError("tennis_ball_racket image_size must be >= 640 for tiny ball recall")

    tiny = raw.get("tiny_ball_tuning") or {}
    return TennisBallRacketConfig(
        name=base_cfg.name,
        task=base_cfg.task,
        base_model=base_cfg.base_model,
        data=base_cfg.data,
        output_dir=base_cfg.output_dir,
        registry_dir=base_cfg.registry_dir,
        classes=classes,
        epochs=base_cfg.epochs,
        image_size=base_cfg.image_size,
        batch=base_cfg.batch,
        device=base_cfg.device,
        workers=base_cfg.workers,
        patience=base_cfg.patience,
        seed=base_cfg.seed,
        freeze=base_cfg.freeze,
        optimizer=base_cfg.optimizer,
        learning_rate=base_cfg.learning_rate,
        model_name=base_cfg.model_name,
        license=base_cfg.license,
        multi_scale=bool(tiny.get("multi_scale", True)),
        min_ball_box_px=int(tiny.get("min_ball_box_px", 4)),
        max_ball_aspect_ratio=float(tiny.get("max_ball_aspect_ratio", 4.5)),
        augmentations=aug,
        quality_gates=base_cfg.quality_gates,
        config_path=config_path,
        config_sha256=_sha256_file(config_path),
    )


def validate_tennis_ball_racket_manifest(
    manifest_path: str | Path,
    *,
    require_files: bool = True,
    allow_unverified_bootstrap: bool = False,
) -> dict[str, Any]:
    """Validate a governed CSV manifest, YOLO dataset.yaml, or pseudo-label JSONL."""
    resolved = Path(manifest_path).resolve()
    if not resolved.is_file():
        raise TennisBallRacketError(f"manifest file does not exist: {resolved}")

    suffix = resolved.suffix.lower()
    if suffix == ".csv":
        try:
            rows = load_manifest(resolved, require_files=require_files)
            audit = (
                audit_labels(rows, "detect", list(TENNIS_DETECTION_CLASSES))
                if require_files
                else {"skipped": True}
            )
        except DatasetError as exc:
            raise TennisBallRacketError(str(exc)) from exc
        return {
            "manifest_type": "governed_csv",
            "path": str(resolved),
            "sample_count": len(rows),
            "classes": list(TENNIS_DETECTION_CLASSES),
            "audit": audit,
            "verified_ground_truth": True,
        }

    if suffix in {".yaml", ".yml"}:
        try:
            data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise TennisBallRacketError(f"invalid dataset YAML: {exc}") from exc
        if not allow_unverified_bootstrap and data.get("label_origin") == BOOTSTRAP_LABEL_ORIGIN:
            raise TennisBallRacketError(
                "dataset YAML carries unverified bootstrap pseudo-labels; human verification required"
            )
        names = data.get("names")
        if isinstance(names, dict):
            try:
                ordered = [str(names[idx]) for idx in sorted(names, key=lambda key: int(key))]
            except (TypeError, ValueError):
                ordered = [str(names[idx]) for idx in sorted(names)]
        elif isinstance(names, list):
            ordered = [str(item) for item in names]
        else:
            raise TennisBallRacketError("dataset YAML must declare names for the 3 tennis classes")
        if tuple(ordered) != TENNIS_DETECTION_CLASSES:
            raise TennisBallRacketError(
                f"dataset YAML names must match {list(TENNIS_DETECTION_CLASSES)}, got {ordered}"
            )
        base_root = Path(str(data["path"])) if data.get("path") else resolved.parent
        if not base_root.is_absolute():
            base_root = (resolved.parent / base_root).resolve()
        for split in ("train", "val"):
            if split not in data:
                raise TennisBallRacketError(f"dataset YAML is missing required split: {split}")
            if require_files:
                split_path = Path(str(data[split]))
                if not split_path.is_absolute():
                    split_path = (base_root / split_path).resolve()
                if not split_path.exists():
                    raise TennisBallRacketError(f"dataset YAML {split} path does not exist: {split_path}")
        return {
            "manifest_type": "yolo_dataset_yaml",
            "path": str(resolved),
            "classes": list(TENNIS_DETECTION_CLASSES),
            "label_origin": data.get("label_origin", "governed_dataset"),
            "splits": [k for k in ("train", "val", "test") if k in data],
        }

    if suffix == ".jsonl":
        records: list[dict[str, Any]] = []
        for line_no, line in enumerate(resolved.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TennisBallRacketError(f"{resolved}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise TennisBallRacketError(f"{resolved}:{line_no}: entry must be a JSON object")
            if not allow_unverified_bootstrap and item.get("label_origin") == BOOTSTRAP_LABEL_ORIGIN:
                raise TennisBallRacketError(
                    f"{resolved}:{line_no}: unverified bootstrap pseudo-labels require human verification"
                )
            records.append(item)
        if not records:
            raise TennisBallRacketError(f"manifest JSONL is empty: {resolved}")
        return {
            "manifest_type": "jsonl_manifest",
            "path": str(resolved),
            "sample_count": len(records),
            "classes": list(TENNIS_DETECTION_CLASSES),
        }

    raise TennisBallRacketError(f"unsupported manifest format: {resolved.suffix}")


def _normalized_xywh(det: Mapping[str, Any], width: int, height: int) -> tuple[float, float, float, float] | None:
    box_norm = det.get("bbox_normalized")
    if isinstance(box_norm, (list, tuple)) and len(box_norm) == 4:
        vals = [float(v) for v in box_norm]
        if all(math.isfinite(v) for v in vals) and vals[2] > vals[0] and vals[3] > vals[1]:
            x1, y1, x2, y2 = (_clamp01(v) for v in vals)
            w = x2 - x1
            h = y2 - y1
            if w > 0.0 and h > 0.0:
                return _clamp01(0.5 * (x1 + x2)), _clamp01(0.5 * (y1 + y2)), w, h

    box_px = det.get("bbox_px")
    if isinstance(box_px, (list, tuple)) and len(box_px) == 4 and width > 0 and height > 0:
        vals = [float(v) for v in box_px]
        if all(math.isfinite(v) for v in vals) and vals[2] > vals[0] and vals[3] > vals[1]:
            x1 = _clamp01(vals[0] / float(width))
            y1 = _clamp01(vals[1] / float(height))
            x2 = _clamp01(vals[2] / float(width))
            y2 = _clamp01(vals[3] / float(height))
            w = x2 - x1
            h = y2 - y1
            if w > 0.0 and h > 0.0:
                return _clamp01(0.5 * (x1 + x2)), _clamp01(0.5 * (y1 + y2)), w, h
    return None


def _racket_class_and_id(raw_name: str) -> tuple[str, int] | None:
    normalized = raw_name.strip().lower()
    if normalized in _RACKET_HEAD_INPUT_ALIASES:
        return "racket_head", 2
    if normalized in _RACKET_INPUT_ALIASES:
        return "tennis_racket", 1
    return None


def bootstrap_pseudo_labels_from_frames(
    frames_jsonl_path: str | Path,
    output_dir: str | Path,
    *,
    min_ball_confidence: float = 0.35,
    min_racket_confidence: float = 0.35,
    min_track_length: int = 3,
    max_coast_frames: int = 4,
) -> dict[str, Any]:
    """Mine multi-frame consistent ball & racket tracks from ``frames.jsonl`` into YOLO starter labels.

    All generated labels and manifests are strictly tagged with
    ``label_origin="bootstrap_pseudo_label_unverified"`` and ``verified_ground_truth=False``
    for human spot-check and active-learning curation.
    """
    frames_path = Path(frames_jsonl_path).resolve()
    if not frames_path.is_file():
        raise TennisBallRacketError(f"frames.jsonl does not exist: {frames_path}")
    if min_track_length < 1:
        raise TennisBallRacketError("min_track_length must be >= 1")
    if not 0.0 <= min_ball_confidence <= 1.0 or not 0.0 <= min_racket_confidence <= 1.0:
        raise TennisBallRacketError("confidence thresholds must be in [0, 1]")

    raw_frames: list[dict[str, Any]] = []
    for line_no, line in enumerate(frames_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TennisBallRacketError(f"{frames_path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("frame"), Mapping):
            raise TennisBallRacketError(f"{frames_path}:{line_no}: missing frame envelope")
        raw_frames.append(row)

    if not raw_frames:
        raise TennisBallRacketError(f"frames.jsonl contains no frames: {frames_path}")

    raw_frames.sort(key=lambda r: (int(r["frame"].get("index", 0)), int(r["frame"].get("timestamp_ms", 0))))
    source_sha = _sha256_file(frames_path)

    # 1) Track confident ball detections with 2D constant-acceleration Kalman filter.
    tracker = KalmanBallTracker(max_coast_frames=max_coast_frames)
    ball_points_by_track: dict[int, list[dict[str, Any]]] = defaultdict(list)
    racket_points_by_group: dict[tuple[str, Any], list[dict[str, Any]]] = defaultdict(list)
    frame_meta_by_index: dict[int, dict[str, Any]] = {}

    for seq_idx, record in enumerate(raw_frames):
        frame_meta = record["frame"]
        f_idx = int(frame_meta.get("index", seq_idx))
        width = int(frame_meta["width"])
        height = int(frame_meta["height"])
        ts_ms = frame_meta.get("timestamp_ms")
        frame_meta_by_index[f_idx] = {
            "frame_index": f_idx,
            "timestamp_ms": ts_ms,
            "width": width,
            "height": height,
        }

        dets = record.get("detections") or []
        confident_balls: list[Mapping[str, Any]] = []
        for det in dets:
            if not isinstance(det, Mapping):
                continue
            cls_name = str(det.get("class_name", "")).strip().lower()
            conf = float(det.get("confidence") or 0.0)
            if cls_name in _BALL_INPUT_ALIASES:
                if conf >= min_ball_confidence and _normalized_xywh(det, width, height) is not None:
                    confident_balls.append(det)
            else:
                mapped = _racket_class_and_id(cls_name)
                if mapped is not None and conf >= min_racket_confidence:
                    mapped_name, class_id = mapped
                    xywh = _normalized_xywh(det, width, height)
                    if xywh is not None:
                        track_key = det.get("track_id") if det.get("track_id") is not None else "untracked"
                        racket_points_by_group[(mapped_name, track_key)].append(
                            {
                                "frame_index": f_idx,
                                "timestamp_ms": ts_ms,
                                "class_id": class_id,
                                "class_name": mapped_name,
                                "xywh": xywh,
                                "confidence": conf,
                                "track_id": det.get("track_id"),
                            }
                        )

        tracked_balls = tracker.update(
            confident_balls,
            width,
            height,
            frame_index=f_idx,
            timestamp_ms=int(ts_ms) if ts_ms is not None else None,
            emit_coasted=False,
        )
        for pt in tracked_balls:
            if pt.get("observed"):
                xywh = _normalized_xywh({"bbox_px": pt["bbox_px"]}, width, height)
                if xywh is not None:
                    ball_points_by_track[int(pt["track_id"])].append(
                        {
                            "frame_index": f_idx,
                            "timestamp_ms": ts_ms,
                            "class_id": 0,
                            "class_name": "tennis_ball",
                            "xywh": xywh,
                            "confidence": pt["confidence"],
                            "track_id": pt["track_id"],
                        }
                    )

    # 2) Retain only multi-frame consistent tracks (>= min_track_length observations).
    labels_by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
    retained_tracks: dict[str, int] = Counter()

    for track_id, pts in ball_points_by_track.items():
        distinct_frames = {p["frame_index"] for p in pts}
        if len(distinct_frames) >= min_track_length:
            retained_tracks["tennis_ball"] += 1
            for item in pts:
                labels_by_frame[item["frame_index"]].append(item)

    for (cls_name, track_key), pts in racket_points_by_group.items():
        if track_key == "untracked":
            # Cluster untracked racket detections into temporally contiguous segments (<= max_coast_frames gap).
            pts_sorted = sorted(pts, key=lambda p: p["frame_index"])
            segments: list[list[dict[str, Any]]] = []
            curr: list[dict[str, Any]] = []
            for item in pts_sorted:
                if curr and item["frame_index"] - curr[-1]["frame_index"] > max_coast_frames + 1:
                    segments.append(curr)
                    curr = []
                curr.append(item)
            if curr:
                segments.append(curr)
            for seg in segments:
                if len({p["frame_index"] for p in seg}) >= min_track_length:
                    retained_tracks[cls_name] += 1
                    for item in seg:
                        labels_by_frame[item["frame_index"]].append(item)
        else:
            if len({p["frame_index"] for p in pts}) >= min_track_length:
                retained_tracks[cls_name] += 1
                for item in pts:
                    labels_by_frame[item["frame_index"]].append(item)

    # 3) Write YOLO labels + unverified provenance manifest.
    out_root = Path(output_dir).resolve()
    labels_dir = out_root / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)

    (out_root / "classes.txt").write_text(
        "\n".join(TENNIS_DETECTION_CLASSES) + "\n",
        encoding="utf-8",
    )

    class_label_counts: Counter[str] = Counter()
    manifest_rows: list[dict[str, Any]] = []
    for f_idx in sorted(labels_by_frame):
        items = sorted(labels_by_frame[f_idx], key=lambda item: (item["class_id"], -(item["confidence"] or 0.0)))
        lines = []
        annotations_meta = []
        for item in items:
            cx, cy, bw, bh = item["xywh"]
            lines.append(f"{item['class_id']} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            class_label_counts[item["class_name"]] += 1
            annotations_meta.append(
                {
                    "class_id": item["class_id"],
                    "class_name": item["class_name"],
                    "bbox_xywh_normalized": [round(cx, 6), round(cy, 6), round(bw, 6), round(bh, 6)],
                    "confidence": item["confidence"],
                    "track_id": item["track_id"],
                    "label_origin": BOOTSTRAP_LABEL_ORIGIN,
                }
            )
        label_file = labels_dir / f"frame_{f_idx:08d}.txt"
        label_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        meta = frame_meta_by_index[f_idx]
        manifest_rows.append(
            {
                "schema_version": 1,
                "frame_index": f_idx,
                "timestamp_ms": meta["timestamp_ms"],
                "width": meta["width"],
                "height": meta["height"],
                "label_file": str(label_file.relative_to(out_root).as_posix()),
                "label_origin": BOOTSTRAP_LABEL_ORIGIN,
                "verified_ground_truth": False,
                "requires_human_spot_check": True,
                "annotations": annotations_meta,
            }
        )

    manifest_path = out_root / "pseudo_label_manifest.jsonl"
    manifest_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in manifest_rows) + ("\n" if manifest_rows else ""),
        encoding="utf-8",
    )

    dataset_yaml = {
        "path": str(out_root),
        "train": "images/train",
        "val": "images/val",
        "names": {idx: name for idx, name in enumerate(TENNIS_DETECTION_CLASSES)},
        "label_origin": BOOTSTRAP_LABEL_ORIGIN,
        "verified_ground_truth": False,
        "requires_human_spot_check": True,
    }
    dataset_yaml_path = out_root / "dataset.yaml"
    dataset_yaml_path.write_text(
        yaml.safe_dump(dataset_yaml, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    summary = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_frames_jsonl": str(frames_path),
        "source_frames_jsonl_sha256": source_sha,
        "output_dir": str(out_root),
        "label_origin": BOOTSTRAP_LABEL_ORIGIN,
        "verified_ground_truth": False,
        "requires_human_spot_check": True,
        "classes": list(TENNIS_DETECTION_CLASSES),
        "total_input_frames": len(raw_frames),
        "labeled_frames": len(manifest_rows),
        "retained_track_counts": dict(retained_tracks),
        "label_counts": dict(class_label_counts),
        "manifest_path": str(manifest_path),
        "dataset_yaml_path": str(dataset_yaml_path),
    }
    (out_root / "bootstrap_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def run_tennis_ball_racket_training(
    config: TennisBallRacketConfig,
    *,
    dry_run: bool = False,
    validate_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate config/manifest and launch 3-class Ultralytics YOLO ball+racket fine-tuning."""
    manifest_audit = (
        validate_tennis_ball_racket_manifest(validate_manifest_path, require_files=True)
        if validate_manifest_path is not None
        else None
    )
    if dry_run:
        data_audit = (
            validate_tennis_ball_racket_manifest(config.data, require_files=False)
            if config.data.is_file()
            else {"status": "data_yaml_not_materialized_yet", "path": str(config.data)}
        )
        return {
            "mode": "dry_run",
            "config_name": config.name,
            "classes": list(config.classes),
            "image_size": config.image_size,
            "augmentations": asdict(config.augmentations),
            "data_audit": data_audit,
            "manifest_audit": manifest_audit,
        }

    if not config.base_model.is_file():
        raise FileNotFoundError(f"base model is missing: {config.base_model}")
    if not config.data.is_file():
        raise FileNotFoundError(f"dataset YAML is missing: {config.data}")
    validate_tennis_ball_racket_manifest(config.data, require_files=True)

    from ultralytics import YOLO

    aug = config.augmentations
    kwargs: dict[str, Any] = {
        "data": str(config.data),
        "project": str(config.output_dir),
        "name": config.name,
        "epochs": config.epochs,
        "imgsz": config.image_size,
        "batch": config.batch,
        "device": config.device,
        "workers": config.workers,
        "patience": config.patience,
        "seed": config.seed,
        "deterministic": True,
        "freeze": config.freeze,
        "optimizer": config.optimizer,
        "mosaic": aug.mosaic,
        "mixup": aug.mixup,
        "scale": aug.scale,
        "hsv_h": aug.hsv_h,
        "hsv_s": aug.hsv_s,
        "hsv_v": aug.hsv_v,
        "degrees": aug.degrees,
        "translate": aug.translate,
        "fliplr": aug.fliplr,
        "copy_paste": aug.copy_paste,
        "close_mosaic": aug.close_mosaic,
        "multi_scale": config.multi_scale,
        "exist_ok": False,
    }
    if config.learning_rate is not None:
        kwargs["lr0"] = config.learning_rate
    YOLO(str(config.base_model)).train(**kwargs)
    best = config.output_dir / config.name / "weights" / "best.pt"
    if not best.exists():
        raise RuntimeError(f"training completed without best.pt: {best}")
    return {
        "mode": "train",
        "best_weights": str(best),
        "classes": list(config.classes),
        "image_size": config.image_size,
    }
