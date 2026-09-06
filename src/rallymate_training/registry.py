from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from rallymate_training.config import TrainConfig


class RegistryError(ValueError):
    """Raised when a model version is invalid or would be overwritten."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gates_passed(metrics: dict, config: TrainConfig) -> tuple[bool, dict]:
    thresholds = asdict(config.quality_gates)
    checks = {
        name: {
            "actual": float(metrics.get(name, 0.0)),
            "required": required,
            "passed": float(metrics.get(name, 0.0)) >= required,
        }
        for name, required in thresholds.items()
    }
    return all(check["passed"] for check in checks.values()), checks


def register_model(
    artifact: str | Path,
    version: str,
    config: TrainConfig,
    metrics: dict,
    dataset_fingerprint: str,
    promote: bool = False,
) -> dict:
    source = Path(artifact).resolve()
    if not source.exists() or not source.is_file():
        raise RegistryError(f"model artifact does not exist: {source}")
    if not version.strip() or any(char in version for char in r'\/:*?"<>|'):
        raise RegistryError("version is empty or contains an invalid path character")
    passed, checks = gates_passed(metrics, config)
    if promote and not passed:
        raise RegistryError("model cannot be promoted because quality gates failed")

    target_dir = config.registry_dir / config.model_name / version
    if target_dir.exists():
        raise RegistryError(f"model version is immutable and already exists: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=False)
    artifact_target = target_dir / source.name
    shutil.copy2(source, artifact_target)
    manifest = {
        "schema_version": 1,
        "model_name": config.model_name,
        "version": version,
        "task": config.task,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact": artifact_target.name,
        "sha256": sha256_file(artifact_target),
        "base_model": str(config.base_model),
        "dataset": str(config.data),
        "dataset_fingerprint": dataset_fingerprint,
        "metrics": metrics,
        "quality_gates": checks,
        "quality_gates_passed": passed,
        "license": config.license,
        "deployment_status": "production" if promote else "candidate",
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if promote:
        aliases = config.registry_dir / "aliases"
        aliases.mkdir(parents=True, exist_ok=True)
        (aliases / f"{config.model_name}-production.json").write_text(
            json.dumps(
                {
                    "model_name": config.model_name,
                    "version": version,
                    "manifest": str(target_dir / "manifest.json"),
                    "sha256": manifest["sha256"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return manifest
