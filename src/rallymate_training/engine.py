from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rallymate_training.config import TrainConfig


def _metric_value(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0.0


def _extract_metrics(results: Any) -> dict:
    results_dict = getattr(results, "results_dict", {}) or {}
    return {
        "map50_95": _metric_value(
            results_dict.get("metrics/mAP50-95(B)")
            or results_dict.get("metrics/mAP50-95(P)")
        ),
        "map50": _metric_value(
            results_dict.get("metrics/mAP50(B)")
            or results_dict.get("metrics/mAP50(P)")
        ),
        "precision": _metric_value(
            results_dict.get("metrics/precision(B)")
            or results_dict.get("metrics/precision(P)")
        ),
        "recall": _metric_value(
            results_dict.get("metrics/recall(B)")
            or results_dict.get("metrics/recall(P)")
        ),
    }


def train(config: TrainConfig) -> Path:
    if not config.base_model.exists():
        raise FileNotFoundError(f"base model is missing: {config.base_model}")
    if not config.data.exists():
        raise FileNotFoundError(f"dataset YAML is missing: {config.data}")
    from ultralytics import YOLO

    kwargs = {
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
        "exist_ok": False,
    }
    if config.learning_rate is not None:
        kwargs["lr0"] = config.learning_rate
    YOLO(str(config.base_model)).train(**kwargs)
    best = config.output_dir / config.name / "weights" / "best.pt"
    if not best.exists():
        raise RuntimeError(f"training completed without best.pt: {best}")
    return best


def evaluate(config: TrainConfig, weights: str | Path, split: str = "test") -> dict:
    weights_path = Path(weights).resolve()
    if not weights_path.exists():
        raise FileNotFoundError(f"model weights are missing: {weights_path}")
    from ultralytics import YOLO

    results = YOLO(str(weights_path)).val(
        data=str(config.data),
        split=split,
        imgsz=config.image_size,
        batch=config.batch,
        device=config.device,
        workers=config.workers,
        plots=True,
    )
    metrics = _extract_metrics(results)
    output = config.output_dir / config.name
    output.mkdir(parents=True, exist_ok=True)
    (output / f"evaluation-{split}.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metrics


def export_model(
    weights: str | Path,
    format_name: str,
    image_size: int,
    device: str,
) -> Path:
    if format_name not in {"onnx", "engine"}:
        raise ValueError("export format must be onnx or engine")
    weights_path = Path(weights).resolve()
    if not weights_path.exists():
        raise FileNotFoundError(f"model weights are missing: {weights_path}")
    from ultralytics import YOLO

    exported = YOLO(str(weights_path)).export(
        format=format_name,
        imgsz=image_size,
        device=device,
        dynamic=format_name == "onnx",
        simplify=format_name == "onnx",
        half=format_name == "engine",
        workspace=4 if format_name == "engine" else None,
    )
    return Path(exported).resolve()
