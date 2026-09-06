from __future__ import annotations

import json
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PoseDeploymentPreset:
    preset_id: str
    role: str
    pose_backend: str
    pose_runtime: str
    pose_profile: str
    model_path: Path
    config_path: Path | None
    native_keypoint_format: str
    input_size_hw: tuple[int, int]
    promotion_status: str
    registry_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "role": self.role,
            "pose_backend": self.pose_backend,
            "pose_runtime": self.pose_runtime,
            "pose_profile": self.pose_profile,
            "model_path": str(self.model_path),
            "config_path": str(self.config_path) if self.config_path else None,
            "native_keypoint_format": self.native_keypoint_format,
            "input_size_hw": list(self.input_size_hw),
            "promotion_status": self.promotion_status,
            "registry_version": self.registry_version,
        }


def deployment_preset_registry(workspace: str | Path | None = None) -> dict[str, Any]:
    root = Path(workspace) if workspace is not None else Path(__file__).resolve().parents[3]
    path = root / "models" / "rtmpose" / "deployment-presets.json"
    return json.loads(path.read_text(encoding="utf-8"))


def default_pose_deployment_preset_id(
    workspace: str | Path | None = None,
) -> str:
    registry = deployment_preset_registry(workspace)
    preset_id = registry.get("default_preset")
    available = {
        str(item.get("preset_id")) for item in registry.get("presets", [])
    }
    if not isinstance(preset_id, str) or preset_id not in available:
        raise ValueError("pose deployment registry has an invalid default_preset")
    return preset_id


def _resolve_config_path(root: Path, payload: dict[str, Any]) -> Path | None:
    relative = payload.get("config_runtime_relative_path")
    if relative is None:
        return None
    workspace_path = (root / relative).resolve()
    if workspace_path.exists():
        return workspace_path
    package_relative = payload.get("config_package_relative_path")
    if not isinstance(package_relative, str) or not package_relative.strip():
        return workspace_path
    spec = importlib.util.find_spec("mmpose")
    locations = list(spec.submodule_search_locations or []) if spec is not None else []
    if not locations:
        return workspace_path
    return (Path(locations[0]) / package_relative).resolve()


def resolve_pose_deployment_preset(
    preset_id: str,
    workspace: str | Path | None = None,
    *,
    require_files: bool = True,
) -> PoseDeploymentPreset:
    root = (Path(workspace) if workspace is not None else Path(__file__).resolve().parents[3]).resolve()
    registry = deployment_preset_registry(root)
    payload = next(
        (item for item in registry["presets"] if item["preset_id"] == preset_id),
        None,
    )
    if payload is None:
        available = ", ".join(item["preset_id"] for item in registry["presets"])
        raise ValueError(f"unknown pose deployment preset {preset_id!r}; available: {available}")
    model_path = (root / payload["model_relative_path"]).resolve()
    config_path = _resolve_config_path(root, payload)
    if require_files and not model_path.exists():
        raise FileNotFoundError(f"pose preset model is missing: {model_path}")
    if require_files and config_path is not None and not config_path.exists():
        raise FileNotFoundError(
            f"pose preset config is missing: {config_path}; run scripts/prepare_rtmpose_runtime.ps1"
        )
    return PoseDeploymentPreset(
        preset_id=payload["preset_id"],
        role=payload["role"],
        pose_backend=payload["pose_backend"],
        pose_runtime=payload["pose_runtime"],
        pose_profile=payload["pose_profile"],
        model_path=model_path,
        config_path=config_path,
        native_keypoint_format=payload["native_keypoint_format"],
        input_size_hw=tuple(int(value) for value in payload["input_size_hw"]),
        promotion_status=payload["promotion_status"],
        registry_version=registry["registry_version"],
    )
