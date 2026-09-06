from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class TrainingConfigError(ValueError):
    """Raised when a training configuration is incomplete or unsafe."""


@dataclass(frozen=True)
class QualityGates:
    map50_95: float = 0.35
    precision: float = 0.70
    recall: float = 0.60


@dataclass(frozen=True)
class TrainConfig:
    name: str
    task: str
    base_model: Path
    data: Path
    output_dir: Path
    registry_dir: Path
    epochs: int = 100
    image_size: int = 960
    batch: int = 8
    device: str = "0"
    workers: int = 4
    patience: int = 25
    seed: int = 42
    freeze: int = 0
    optimizer: str = "auto"
    learning_rate: float | None = None
    model_name: str = "rallymate-perception"
    license: str = "internal-evaluation-only"
    quality_gates: QualityGates = field(default_factory=QualityGates)


def _resolve(base: Path, value: Any, field_name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise TrainingConfigError(f"{field_name} must be a non-empty path")
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _positive_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise TrainingConfigError(f"{key} must be an integer >= 1")
    return value


def load_train_config(path: str | Path) -> TrainConfig:
    config_path = Path(path).resolve()
    if not config_path.exists():
        raise TrainingConfigError(f"training config does not exist: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise TrainingConfigError(f"training config is invalid YAML: {exc}") from exc
    if raw.get("schema_version") != 1:
        raise TrainingConfigError("schema_version must be 1")

    base = config_path.parent
    task = str(raw.get("task", "")).strip().lower()
    if task not in {"detect", "pose"}:
        raise TrainingConfigError("task must be detect or pose")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise TrainingConfigError("name must be a non-empty string")

    gates_raw = raw.get("quality_gates", {}) or {}
    gate_values = {
        "map50_95": float(gates_raw.get("map50_95", 0.35)),
        "precision": float(gates_raw.get("precision", 0.70)),
        "recall": float(gates_raw.get("recall", 0.60)),
    }
    if any(not 0.0 <= value <= 1.0 for value in gate_values.values()):
        raise TrainingConfigError("quality gate values must be in the 0..1 range")

    learning_rate = raw.get("learning_rate")
    if learning_rate is not None:
        learning_rate = float(learning_rate)
        if learning_rate <= 0:
            raise TrainingConfigError("learning_rate must be greater than zero")

    return TrainConfig(
        name=name,
        task=task,
        base_model=_resolve(base, raw.get("base_model"), "base_model"),
        data=_resolve(base, raw.get("data"), "data"),
        output_dir=_resolve(base, raw.get("output_dir"), "output_dir"),
        registry_dir=_resolve(base, raw.get("registry_dir"), "registry_dir"),
        epochs=_positive_int(raw, "epochs", 100),
        image_size=_positive_int(raw, "image_size", 960),
        batch=_positive_int(raw, "batch", 8),
        device=str(raw.get("device", "0")),
        workers=int(raw.get("workers", 4)),
        patience=int(raw.get("patience", 25)),
        seed=int(raw.get("seed", 42)),
        freeze=max(0, int(raw.get("freeze", 0))),
        optimizer=str(raw.get("optimizer", "auto")),
        learning_rate=learning_rate,
        model_name=str(raw.get("model_name", name)).strip(),
        license=str(raw.get("license", "internal-evaluation-only")).strip(),
        quality_gates=QualityGates(**gate_values),
    )
