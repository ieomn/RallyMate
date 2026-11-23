"""SAM 3D Body tennis-domain LoRA fine-tuning, physics losses, and SHA-256 provenance."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import yaml

from rallymate_vision.pose.metadata import MHR70_NAMES


SAM3D_LORA_FORMAT_VERSION = "rallymate-sam3d-lora-v1"
MHR70_RIGHT_WRIST_INDEX = MHR70_NAMES.index("right_wrist")
MHR70_LEFT_WRIST_INDEX = MHR70_NAMES.index("left_wrist")
MHR70_WRIST_INDICES = (MHR70_RIGHT_WRIST_INDEX, MHR70_LEFT_WRIST_INDEX)
MHR70_ANKLE_INDICES = (
    MHR70_NAMES.index("left_ankle"),
    MHR70_NAMES.index("right_ankle"),
)
DEFAULT_TARGET_PATTERNS = (
    "decoder.layers.*.self_attn.q_proj",
    "decoder.layers.*.self_attn.k_proj",
    "decoder.layers.*.self_attn.v_proj",
    "decoder.layers.*.self_attn.proj",
    "decoder.layers.*.cross_attn.q_proj",
    "decoder.layers.*.cross_attn.k_proj",
    "decoder.layers.*.cross_attn.v_proj",
    "decoder.layers.*.cross_attn.proj",
    "decoder.layers.*.ffn.*",
    "head_pose.proj.*",
    "head_camera.proj.*",
)


class Sam3dLoraError(ValueError):
    """Raised when SAM 3D LoRA configuration, injection, or provenance fails."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _require_sha256_hex(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise Sam3dLoraError(f"{label} must be a 64-character SHA-256 hex digest")
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise Sam3dLoraError(f"{label} must be a 64-character SHA-256 hex digest")
    return normalized


def _state_dict_sha256(state_dict: Mapping[str, torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for key in sorted(state_dict):
        tensor = state_dict[key].detach().cpu().contiguous()
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(tuple(tensor.shape)).encode("ascii"))
        digest.update(b"\0")
        # Normalize to raw bytes via uint8 view so non-float16/bf16/float32 all hash identically across runs.
        raw_bytes = tensor.view(torch.uint8).numpy().tobytes(order="C")
        digest.update(raw_bytes)
    return digest.hexdigest().lower()


class LoRALinear(nn.Module):
    """Low-Rank Adaptation wrapper for ``nn.Linear`` with algebraic zero-overhead merge.

    Computes ``y = W x + b + (alpha / rank) * B (A (dropout(x)))``.
    ``lora_A`` uses Kaiming uniform initialization and ``lora_B`` is zero-initialized
    so initial behavior exactly matches the frozen pretrained base weight.
    """

    def __init__(
        self,
        base_layer: nn.Linear,
        rank: int = 16,
        alpha: float = 32.0,
        dropout: float = 0.0,
    ) -> None:
        super().__init__()
        if not isinstance(base_layer, nn.Linear):
            raise Sam3dLoraError(f"LoRALinear requires nn.Linear, got {type(base_layer).__name__}")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise Sam3dLoraError("rank must be an integer >= 1")
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not math.isfinite(alpha) or alpha <= 0:
            raise Sam3dLoraError("alpha must be a finite number > 0")
        if isinstance(dropout, bool) or not isinstance(dropout, (int, float)) or not 0.0 <= float(dropout) < 1.0:
            raise Sam3dLoraError("dropout must be in [0.0, 1.0)")

        self.base_layer = base_layer
        self.in_features = base_layer.in_features
        self.out_features = base_layer.out_features
        self.rank = int(rank)
        self.alpha = float(alpha)
        self.dropout_p = float(dropout)
        self.scaling = self.alpha / float(self.rank)
        self.merged = False

        device = base_layer.weight.device
        dtype = base_layer.weight.dtype
        self.lora_A = nn.Parameter(torch.empty((self.rank, self.in_features), device=device, dtype=dtype))
        self.lora_B = nn.Parameter(torch.zeros((self.out_features, self.rank), device=device, dtype=dtype))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))
        self.lora_dropout: nn.Module = nn.Dropout(p=self.dropout_p) if self.dropout_p > 0.0 else nn.Identity()

    def delta_weight(self) -> torch.Tensor:
        """Return ``(B @ A) * (alpha / rank)`` in the base weight dtype/device."""
        delta = (self.lora_B.float() @ self.lora_A.float()) * self.scaling
        return delta.to(dtype=self.base_layer.weight.dtype, device=self.base_layer.weight.device)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        base_out = self.base_layer(x)
        if self.merged:
            return base_out
        dropped = self.lora_dropout(x)
        adapted = F.linear(F.linear(dropped.to(self.lora_A.dtype), self.lora_A), self.lora_B) * self.scaling
        return base_out + adapted.to(dtype=base_out.dtype)

    def merge(self) -> nn.Linear:
        """Algebraically fold ``(B @ A) * (alpha / rank)`` into ``base_layer.weight``."""
        if not self.merged:
            with torch.no_grad():
                self.base_layer.weight.data.add_(self.delta_weight())
            self.merged = True
        return self.base_layer


def _pattern_matches(module_name: str, pattern: str) -> bool:
    if pattern.startswith("re:"):
        return re.search(pattern[3:], module_name) is not None
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(module_name, pattern)
    return (
        module_name == pattern
        or module_name.endswith(f".{pattern}")
        or module_name.startswith(f"{pattern}.")
        or f".{pattern}." in module_name
    )


def _set_submodule(root: nn.Module, dotted_name: str, replacement: nn.Module) -> None:
    parts = dotted_name.split(".")
    parent = root
    for part in parts[:-1]:
        parent = getattr(parent, part)
    setattr(parent, parts[-1], replacement)


def inject_lora_adapters(
    model: nn.Module,
    target_module_patterns: str | Sequence[str],
    rank: int = 16,
    alpha: float = 32.0,
    dropout: float = 0.0,
) -> list[str]:
    """Wrap matching ``nn.Linear`` submodules inside ``model`` with ``LoRALinear``.

    Mutates ``model`` in place and returns the ordered list of adapted module names.
    """
    if isinstance(target_module_patterns, str):
        patterns = [target_module_patterns.strip()]
    else:
        patterns = [str(item).strip() for item in target_module_patterns]
    if not patterns or any(not item for item in patterns):
        raise Sam3dLoraError("target_module_patterns must contain at least one non-empty pattern")

    existing_lora_prefixes = {
        name for name, mod in model.named_modules() if isinstance(mod, LoRALinear)
    }
    candidates: list[tuple[str, nn.Linear]] = []
    for name, mod in list(model.named_modules()):
        if not name:
            continue
        if name in existing_lora_prefixes or any(
            name.startswith(f"{prefix}.") for prefix in existing_lora_prefixes
        ):
            continue
        if isinstance(mod, nn.Linear) and any(_pattern_matches(name, pattern) for pattern in patterns):
            candidates.append((name, mod))

    if not candidates:
        raise Sam3dLoraError(
            f"no nn.Linear modules matched target_module_patterns: {patterns!r}"
        )

    adapted: list[str] = []
    for name, linear in candidates:
        wrapper = LoRALinear(linear, rank=rank, alpha=alpha, dropout=dropout)
        _set_submodule(model, name, wrapper)
        adapted.append(name)
    return adapted


def freeze_non_lora_parameters(model: nn.Module) -> dict[str, int | float]:
    """Freeze every parameter except LoRA ``lora_A`` / ``lora_B`` weights."""
    for param in model.parameters():
        param.requires_grad_(False)
    for module in model.modules():
        if isinstance(module, LoRALinear):
            module.lora_A.requires_grad_(True)
            module.lora_B.requires_grad_(True)
    return count_trainable_parameters(model)


def count_trainable_parameters(model: nn.Module) -> dict[str, int | float]:
    """Return trainable vs total parameter statistics for ``model``."""
    total = sum(param.numel() for param in model.parameters())
    trainable = sum(param.numel() for param in model.parameters() if param.requires_grad)
    frozen = total - trainable
    return {
        "trainable_params": int(trainable),
        "total_params": int(total),
        "frozen_params": int(frozen),
        "trainable_fraction": round(float(trainable) / float(total), 6) if total > 0 else 0.0,
    }


def extract_lora_state_dict(model: nn.Module) -> dict[str, torch.Tensor]:
    """Return a CPU-cloned state dict containing only LoRA ``lora_A`` / ``lora_B`` tensors."""
    extracted: dict[str, torch.Tensor] = {}
    for name, module in model.named_modules():
        if isinstance(module, LoRALinear):
            prefix = f"{name}." if name else ""
            extracted[f"{prefix}lora_A"] = module.lora_A.detach().cpu().clone()
            extracted[f"{prefix}lora_B"] = module.lora_B.detach().cpu().clone()
    return extracted


def merge_lora_weights(model: nn.Module) -> nn.Module:
    """Fold every ``LoRALinear`` adapter into its base ``nn.Linear`` and unwrap for zero-overhead inference."""
    if isinstance(model, LoRALinear):
        return model.merge()
    lora_nodes = [
        (name, mod)
        for name, mod in list(model.named_modules())
        if isinstance(mod, LoRALinear) and name
    ]
    for name, lora_mod in lora_nodes:
        merged_linear = lora_mod.merge()
        _set_submodule(model, name, merged_linear)
    return model


def _collect_lora_specs(model: nn.Module) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for name, module in model.named_modules():
        if isinstance(module, LoRALinear):
            specs[name] = {
                "in_features": module.in_features,
                "out_features": module.out_features,
                "rank": module.rank,
                "alpha": module.alpha,
                "dropout": module.dropout_p,
            }
    return specs


def _resolve_checkpoint_paths(path: str | Path) -> tuple[Path, Path]:
    target = Path(path).resolve()
    if target.suffix in {".pt", ".pth", ".bin"}:
        return target, target.with_suffix(".manifest.json")
    return target / "lora_weights.pt", target / "lora_manifest.json"


def save_lora_checkpoint(
    model: nn.Module,
    checkpoint_path: str | Path,
    *,
    base_checkpoint_sha256: str,
    config_sha256: str,
    dataset_digest: str,
    base_checkpoint_path: str | Path | None = None,
    target_module_patterns: Sequence[str] | None = None,
    extra_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Save LoRA weights alongside a tamper-evident SHA-256 provenance manifest."""
    lora_state = extract_lora_state_dict(model)
    if not lora_state:
        raise Sam3dLoraError("cannot save LoRA checkpoint: model contains no LoRALinear adapters")
    for key, tensor in lora_state.items():
        if not torch.isfinite(tensor).all():
            raise Sam3dLoraError(f"cannot save non-finite LoRA tensor: {key}")

    specs = _collect_lora_specs(model)
    ranks = {spec["rank"] for spec in specs.values()}
    alphas = {spec["alpha"] for spec in specs.values()}
    dropouts = {spec["dropout"] for spec in specs.values()}
    if len(ranks) != 1 or len(alphas) != 1 or len(dropouts) != 1:
        raise Sam3dLoraError("heterogeneous LoRA rank/alpha/dropout across modules is not supported in manifest")

    base_sha = _require_sha256_hex(base_checkpoint_sha256, "base_checkpoint_sha256")
    cfg_sha = _require_sha256_hex(config_sha256, "config_sha256")
    data_sha = _require_sha256_hex(dataset_digest, "dataset_digest")

    weights_path, manifest_path = _resolve_checkpoint_paths(checkpoint_path)
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "format": SAM3D_LORA_FORMAT_VERSION,
        "lora_state_dict": lora_state,
        "adapter_specs": specs,
    }
    torch.save(payload, weights_path)
    weights_sha = _sha256_file(weights_path)
    state_sha = _state_dict_sha256(lora_state)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "format": SAM3D_LORA_FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "weights_file": weights_path.name,
        "weights_sha256": weights_sha,
        "state_dict_sha256": state_sha,
        "base_checkpoint_path": str(base_checkpoint_path) if base_checkpoint_path is not None else None,
        "base_checkpoint_sha256": base_sha,
        "config_sha256": cfg_sha,
        "dataset_digest": data_sha,
        "rank": next(iter(ranks)),
        "alpha": next(iter(alphas)),
        "dropout": next(iter(dropouts)),
        "target_module_patterns": list(target_module_patterns or sorted(specs)),
        "adapted_modules": sorted(specs),
        "trainable_parameters": count_trainable_parameters(model),
        "extra_metadata": dict(extra_metadata or {}),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "weights_path": str(weights_path),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
    }


def load_lora_checkpoint(
    checkpoint_path: str | Path,
    model: nn.Module | None = None,
    *,
    verify_provenance: bool = True,
    expected_base_checkpoint_sha256: str | None = None,
    expected_config_sha256: str | None = None,
    expected_dataset_digest: str | None = None,
    map_location: str | torch.device = "cpu",
) -> dict[str, Any]:
    """Load LoRA weights and verify all SHA-256 provenance bindings fail-closed."""
    weights_path, manifest_path = _resolve_checkpoint_paths(checkpoint_path)
    if not weights_path.is_file():
        raise Sam3dLoraError(f"LoRA weights file is missing: {weights_path}")
    if not manifest_path.is_file():
        raise Sam3dLoraError(f"LoRA provenance manifest is missing: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Sam3dLoraError(f"invalid LoRA manifest JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != SAM3D_LORA_FORMAT_VERSION:
        raise Sam3dLoraError("unsupported LoRA manifest format")

    if verify_provenance:
        observed_weights_sha = _sha256_file(weights_path)
        expected_weights_sha = _require_sha256_hex(manifest.get("weights_sha256"), "manifest.weights_sha256")
        if observed_weights_sha != expected_weights_sha:
            raise Sam3dLoraError("LoRA weights file SHA-256 mismatch against provenance manifest")
        if expected_base_checkpoint_sha256 is not None:
            if _require_sha256_hex(expected_base_checkpoint_sha256, "expected_base_checkpoint_sha256") != _require_sha256_hex(
                manifest.get("base_checkpoint_sha256"), "manifest.base_checkpoint_sha256"
            ):
                raise Sam3dLoraError("LoRA base checkpoint SHA-256 mismatch")
        if expected_config_sha256 is not None:
            if _require_sha256_hex(expected_config_sha256, "expected_config_sha256") != _require_sha256_hex(
                manifest.get("config_sha256"), "manifest.config_sha256"
            ):
                raise Sam3dLoraError("LoRA config SHA-256 mismatch")
        if expected_dataset_digest is not None:
            if _require_sha256_hex(expected_dataset_digest, "expected_dataset_digest") != _require_sha256_hex(
                manifest.get("dataset_digest"), "manifest.dataset_digest"
            ):
                raise Sam3dLoraError("LoRA dataset digest mismatch")

    payload = torch.load(weights_path, map_location=map_location, weights_only=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("lora_state_dict"), dict):
        raise Sam3dLoraError("LoRA checkpoint payload is missing lora_state_dict")
    lora_state: dict[str, torch.Tensor] = payload["lora_state_dict"]
    if verify_provenance:
        expected_state_sha = _require_sha256_hex(manifest.get("state_dict_sha256"), "manifest.state_dict_sha256")
        if _state_dict_sha256(lora_state) != expected_state_sha:
            raise Sam3dLoraError("LoRA state_dict SHA-256 mismatch")

    loaded_keys: list[str] = []
    if model is not None:
        existing_lora = {name: mod for name, mod in model.named_modules() if isinstance(mod, LoRALinear)}
        if not existing_lora:
            inject_lora_adapters(
                model,
                manifest.get("adapted_modules") or manifest["target_module_patterns"],
                rank=int(manifest["rank"]),
                alpha=float(manifest["alpha"]),
                dropout=float(manifest.get("dropout", 0.0)),
            )
            existing_lora = {name: mod for name, mod in model.named_modules() if isinstance(mod, LoRALinear)}

        expected_rank = int(manifest["rank"])
        expected_alpha = float(manifest["alpha"])
        expected_modules = set(manifest.get("adapted_modules", []))
        if expected_modules and expected_modules != set(existing_lora):
            raise Sam3dLoraError("model LoRA modules do not match checkpoint adapted_modules")
        for mod_name, lora_mod in existing_lora.items():
            if lora_mod.rank != expected_rank or not math.isclose(lora_mod.alpha, expected_alpha, rel_tol=1e-6):
                raise Sam3dLoraError(f"LoRA rank/alpha mismatch on {mod_name}")
            prefix = f"{mod_name}." if mod_name else ""
            for suffix in ("lora_A", "lora_B"):
                key = f"{prefix}{suffix}"
                if key not in lora_state:
                    raise Sam3dLoraError(f"missing LoRA parameter in checkpoint: {key}")
                param = getattr(lora_mod, suffix)
                src = lora_state[key].to(device=param.device, dtype=param.dtype)
                if src.shape != param.shape:
                    raise Sam3dLoraError(f"LoRA parameter shape mismatch for {key}: {src.shape} vs {param.shape}")
                with torch.no_grad():
                    param.copy_(src)
                loaded_keys.append(key)

    return {
        "weights_path": str(weights_path),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "state_dict": lora_state,
        "loaded_keys": loaded_keys,
    }


def temporal_kinematic_smoothness_loss(
    joints_3d: torch.Tensor,
    dt_s: torch.Tensor | float,
    *,
    joint_weights: torch.Tensor | Sequence[float] | None = None,
    wrist_indices: Sequence[int] | None = None,
    wrist_weight: float = 0.25,
    huber_delta: float = 0.5,
) -> torch.Tensor:
    """VFR-aware 2nd-order acceleration smoothness loss using Pseudo-Huber penalty.

    Penalizes non-uniform discrete acceleration ``(v2 - v1) / ((dt1 + dt2) / 2)``
    with a Pseudo-Huber norm so small frame-to-frame jitter is damped quadratically
    while high-impulse wrist whip grows only linearly and is weighted down at distal
    wrist joints.
    """
    if joints_3d.ndim == 3:
        seq = joints_3d.unsqueeze(0)
    elif joints_3d.ndim == 4:
        seq = joints_3d
    else:
        raise Sam3dLoraError(f"joints_3d must have shape (T, J, 3) or (B, T, J, 3), got {tuple(joints_3d.shape)}")
    if seq.shape[-1] != 3:
        raise Sam3dLoraError("joints_3d trailing dimension must be 3")

    batch_size, num_frames, num_joints, _ = seq.shape
    if num_frames < 3:
        return (seq * 0.0).sum()

    if isinstance(dt_s, (int, float)):
        dt_tensor = torch.full((batch_size, num_frames - 1), float(dt_s), device=seq.device, dtype=seq.dtype)
    else:
        dt_tensor = torch.as_tensor(dt_s, device=seq.device, dtype=seq.dtype)
        if dt_tensor.ndim == 0:
            dt_tensor = dt_tensor.view(1, 1).expand(batch_size, num_frames - 1)
        elif dt_tensor.ndim == 1:
            if dt_tensor.shape[0] != num_frames - 1:
                raise Sam3dLoraError(f"1D dt_s must have length T-1 ({num_frames - 1}), got {dt_tensor.shape[0]}")
            dt_tensor = dt_tensor.unsqueeze(0).expand(batch_size, num_frames - 1)
        elif dt_tensor.ndim == 2:
            if dt_tensor.shape != (batch_size, num_frames - 1):
                raise Sam3dLoraError(f"2D dt_s must have shape ({batch_size}, {num_frames - 1}), got {tuple(dt_tensor.shape)}")
        elif dt_tensor.ndim == 3 and dt_tensor.shape == (batch_size, num_frames - 1, 1):
            dt_tensor = dt_tensor.squeeze(-1)
        else:
            raise Sam3dLoraError(f"unsupported dt_s shape: {tuple(dt_tensor.shape)}")

    if not torch.isfinite(dt_tensor).all() or (dt_tensor <= 0).any():
        raise Sam3dLoraError("dt_s must contain strictly positive finite intervals")
    dt_clamped = dt_tensor.clamp(min=1e-4)
    dt1 = dt_clamped[:, :-1].unsqueeze(-1).unsqueeze(-1)
    dt2 = dt_clamped[:, 1:].unsqueeze(-1).unsqueeze(-1)
    dt_mid = 0.5 * (dt1 + dt2)

    vel1 = (seq[:, 1:-1] - seq[:, :-2]) / dt1
    vel2 = (seq[:, 2:] - seq[:, 1:-1]) / dt2
    acc = (vel2 - vel1) / dt_mid

    delta = max(float(huber_delta), 1e-4)
    acc_sq = (acc ** 2).sum(dim=-1)
    # Exact 0 at acc == 0, C^inf smooth everywhere, quadratic near 0, linear at high whip.
    per_joint = (delta ** 2) * (torch.sqrt(1.0 + acc_sq / (delta ** 2)) - 1.0)

    if joint_weights is not None:
        weights = torch.as_tensor(joint_weights, device=seq.device, dtype=seq.dtype)
        if weights.shape != (num_joints,):
            raise Sam3dLoraError(f"joint_weights must have shape ({num_joints},), got {tuple(weights.shape)}")
    else:
        weights = torch.ones((num_joints,), device=seq.device, dtype=seq.dtype)
        active_wrists = (
            tuple(wrist_indices)
            if wrist_indices is not None
            else (MHR70_WRIST_INDICES if num_joints == len(MHR70_NAMES) else ())
        )
        for idx in active_wrists:
            if 0 <= idx < num_joints:
                weights[idx] = float(wrist_weight)

    weight_sum = weights.sum().clamp(min=1e-6)
    weighted = (per_joint * weights.view(1, 1, num_joints)).sum(dim=-1) / weight_sum
    return weighted.mean()


def reprojection_2d_consistency_loss(
    joints_3d: torch.Tensor,
    camera_t: torch.Tensor,
    focal: torch.Tensor | float,
    target_2d: torch.Tensor,
    visibility_mask: torch.Tensor,
    *,
    principal_point: torch.Tensor | Sequence[float] | None = None,
    image_size: Sequence[float] | torch.Tensor | None = None,
    huber_beta: float = 5.0,
    min_depth: float = 0.1,
) -> torch.Tensor:
    """Perspective projection loss aligning 3D joints with visible 2D keypoints/pseudo-labels.

    Mirrors SAM 3D Body's perspective model:
    ``j3d_cam = joints_3d + camera_t``, ``uv = f * (xy / z) + c``.
    """
    if joints_3d.shape[-1] != 3:
        raise Sam3dLoraError("joints_3d trailing dimension must be 3")
    if target_2d.shape[:-1] != joints_3d.shape[:-1] or target_2d.shape[-1] != 2:
        raise Sam3dLoraError(
            f"target_2d shape {tuple(target_2d.shape)} incompatible with joints_3d shape {tuple(joints_3d.shape)}"
        )
    if visibility_mask.shape != joints_3d.shape[:-1]:
        raise Sam3dLoraError(
            f"visibility_mask shape {tuple(visibility_mask.shape)} must match {tuple(joints_3d.shape[:-1])}"
        )

    lead_shape = joints_3d.shape[:-2]
    cam_t = torch.as_tensor(camera_t, device=joints_3d.device, dtype=joints_3d.dtype)
    if cam_t.shape != (*lead_shape, 3):
        raise Sam3dLoraError(f"camera_t must have shape {(*lead_shape, 3)}, got {tuple(cam_t.shape)}")
    j3d_cam = joints_3d + cam_t.unsqueeze(-2)

    focal_t = torch.as_tensor(focal, device=joints_3d.device, dtype=joints_3d.dtype)
    if focal_t.ndim == 0:
        fx = fy = focal_t.expand(*lead_shape).unsqueeze(-1)
    elif focal_t.shape == lead_shape:
        fx = fy = focal_t.unsqueeze(-1)
    elif focal_t.shape == (*lead_shape, 1):
        fx = fy = focal_t
    elif focal_t.shape == (*lead_shape, 2):
        fx = focal_t[..., 0:1]
        fy = focal_t[..., 1:2]
    else:
        raise Sam3dLoraError(f"unsupported focal shape {tuple(focal_t.shape)} for batch shape {lead_shape}")

    if principal_point is not None:
        pp = torch.as_tensor(principal_point, device=joints_3d.device, dtype=joints_3d.dtype)
        if pp.ndim == 1 and pp.shape[0] == 2:
            cx, cy = pp[0], pp[1]
        elif pp.shape == (*lead_shape, 2):
            cx, cy = pp[..., 0:1], pp[..., 1:2]
        else:
            raise Sam3dLoraError(f"unsupported principal_point shape: {tuple(pp.shape)}")
    elif image_size is not None:
        sz = torch.as_tensor(image_size, device=joints_3d.device, dtype=joints_3d.dtype)
        if sz.ndim == 1 and sz.shape[0] == 2:
            cx, cy = sz[0] * 0.5, sz[1] * 0.5
        elif sz.shape == (*lead_shape, 2):
            cx, cy = sz[..., 0:1] * 0.5, sz[..., 1:2] * 0.5
        else:
            raise Sam3dLoraError(f"unsupported image_size shape: {tuple(sz.shape)}")
    else:
        cx = cy = 0.0

    z = j3d_cam[..., 2].clamp(min=float(min_depth))
    proj_x = fx * (j3d_cam[..., 0] / z) + cx
    proj_y = fy * (j3d_cam[..., 1] / z) + cy
    proj_2d = torch.stack((proj_x, proj_y), dim=-1)

    diff = proj_2d - target_2d.to(device=joints_3d.device, dtype=joints_3d.dtype)
    beta = max(float(huber_beta), 1e-4)
    err_sq = (diff ** 2).sum(dim=-1)
    per_joint_loss = (beta ** 2) * (torch.sqrt(1.0 + err_sq / (beta ** 2)) - 1.0)

    weights = visibility_mask.to(device=joints_3d.device, dtype=joints_3d.dtype).clamp(min=0.0)
    total_weight = weights.sum()
    if float(total_weight.detach().item()) <= 0.0:
        return (joints_3d * 0.0).sum()
    return (per_joint_loss * weights).sum() / total_weight.clamp(min=1e-6)


def ground_plane_contact_prior_loss(
    ankle_3d: torch.Tensor,
    foot_contact_mask: torch.Tensor,
    ground_normal: torch.Tensor | Sequence[float],
    ground_offset: torch.Tensor | float,
    *,
    slip_weight: float = 0.0,
) -> torch.Tensor:
    """Stabilize monocular Z-depth drift when stance feet contact the ground plane.

    Enforces ``dot(unit(ground_normal), ankle_3d) + ground_offset == 0`` for
    every joint/frame where ``foot_contact_mask > 0``. When ``slip_weight > 0``
    on 4D ``(B, T, K, 3)`` sequences, also penalizes frame-to-frame motion of
    continuously planted feet.
    """
    if ankle_3d.shape[-1] != 3:
        raise Sam3dLoraError("ankle_3d trailing dimension must be 3")
    if foot_contact_mask.shape != ankle_3d.shape[:-1]:
        raise Sam3dLoraError(
            f"foot_contact_mask shape {tuple(foot_contact_mask.shape)} must match {tuple(ankle_3d.shape[:-1])}"
        )

    normal = torch.as_tensor(ground_normal, device=ankle_3d.device, dtype=ankle_3d.dtype)
    if normal.shape[-1] != 3:
        raise Sam3dLoraError("ground_normal trailing dimension must be 3")
    unit_normal = normal / normal.norm(dim=-1, keepdim=True).clamp(min=1e-6)
    while unit_normal.ndim < ankle_3d.ndim:
        unit_normal = unit_normal.unsqueeze(-2) if unit_normal.ndim > 1 else unit_normal.unsqueeze(0)

    offset = torch.as_tensor(ground_offset, device=ankle_3d.device, dtype=ankle_3d.dtype)
    proj = (ankle_3d * unit_normal).sum(dim=-1)
    while offset.ndim > 0 and offset.ndim < proj.ndim:
        offset = offset.unsqueeze(-1)
    signed_dist = proj + offset

    mask = foot_contact_mask.to(device=ankle_3d.device, dtype=ankle_3d.dtype).clamp(min=0.0)
    mask_sum = mask.sum()
    if float(mask_sum.detach().item()) <= 0.0:
        return (ankle_3d * 0.0).sum()

    plane_loss = ((signed_dist ** 2) * mask).sum() / mask_sum.clamp(min=1e-6)
    if slip_weight > 0.0 and ankle_3d.ndim == 4 and ankle_3d.shape[1] >= 2:
        pair_mask = mask[:, 1:] * mask[:, :-1]
        pair_sum = pair_mask.sum()
        if float(pair_sum.detach().item()) > 0.0:
            disp_sq = ((ankle_3d[:, 1:] - ankle_3d[:, :-1]) ** 2).sum(dim=-1)
            slip_loss = (disp_sq * pair_mask).sum() / pair_sum.clamp(min=1e-6)
            plane_loss = plane_loss + float(slip_weight) * slip_loss
    return plane_loss


def _linear_motion_kernel(length: int, angle_deg: float) -> np.ndarray:
    size = max(3, int(length) | 1)
    kernel = np.zeros((size, size), dtype=np.float32)
    center = size // 2
    rad = math.radians(angle_deg)
    dx = math.cos(rad)
    dy = math.sin(rad)
    half = (size - 1) * 0.5
    steps = max(size * 2, 16)
    for step in np.linspace(-half, half, steps):
        x = int(round(center + step * dx))
        y = int(round(center + step * dy))
        if 0 <= x < size and 0 <= y < size:
            kernel[y, x] += 1.0
    total = float(kernel.sum())
    if total <= 0.0:
        kernel[center, center] = 1.0
    else:
        kernel /= total
    return kernel


class TennisHardCaseAugmenter:
    """Domain hard-case augmenter for far-court small ROIs, swing blur, and arm occlusion.

    Supports both NumPy arrays (``(H, W, C)`` or ``(B, H, W, C)``) and PyTorch
    tensors (``(C, H, W)`` or ``(B, C, H, W)``), preserving shape, dtype, and device.
    """

    def __init__(
        self,
        *,
        small_roi_prob: float = 0.35,
        min_roi_height: int = 64,
        max_roi_height: int = 80,
        motion_blur_prob: float = 0.40,
        motion_blur_kernel_range: tuple[int, int] = (5, 13),
        occlusion_prob: float = 0.30,
        occlusion_boxes: tuple[int, int] = (1, 2),
        seed: int = 42,
    ) -> None:
        if min_roi_height < 16 or max_roi_height < min_roi_height:
            raise Sam3dLoraError("invalid small-ROI height bounds")
        self.small_roi_prob = float(small_roi_prob)
        self.min_roi_height = int(min_roi_height)
        self.max_roi_height = int(max_roi_height)
        self.motion_blur_prob = float(motion_blur_prob)
        self.motion_blur_kernel_range = (int(motion_blur_kernel_range[0]), int(motion_blur_kernel_range[1]))
        self.occlusion_prob = float(occlusion_prob)
        self.occlusion_boxes = (int(occlusion_boxes[0]), int(occlusion_boxes[1]))
        self.rng = np.random.default_rng(seed)
        self.last_applied: list[str] = []

    def _augment_hwc_numpy(
        self,
        image: np.ndarray,
        *,
        arm_anchors_xy: Sequence[tuple[float, float]] | None = None,
    ) -> tuple[np.ndarray, list[str]]:
        if image.ndim != 3:
            raise Sam3dLoraError(f"expected 3D HWC image, got shape {image.shape}")
        height, width, _ = image.shape
        orig_dtype = image.dtype
        work = image.astype(np.float32, copy=True)
        applied: list[str] = []

        if self.rng.random() < self.small_roi_prob and height > self.min_roi_height:
            target_h = int(self.rng.integers(self.min_roi_height, min(self.max_roi_height, height) + 1))
            target_w = max(16, int(round(width * (target_h / float(height)))))
            down = cv2.resize(work, (target_w, target_h), interpolation=cv2.INTER_AREA)
            work = cv2.resize(down, (width, height), interpolation=cv2.INTER_LINEAR)
            applied.append("small_roi_downscale")

        if self.rng.random() < self.motion_blur_prob:
            k_min, k_max = self.motion_blur_kernel_range
            length = int(self.rng.integers(max(3, k_min), max(k_min, k_max) + 1))
            angle = float(self.rng.uniform(0.0, 180.0))
            kernel = _linear_motion_kernel(length, angle)
            work = cv2.filter2D(work, ddepth=-1, kernel=kernel, borderType=cv2.BORDER_REPLICATE)
            applied.append("swing_motion_blur")

        if self.rng.random() < self.occlusion_prob:
            n_min, n_max = self.occlusion_boxes
            count = int(self.rng.integers(max(1, n_min), max(n_min, n_max) + 1))
            fill_value = float(work.mean())
            for idx in range(count):
                box_h = max(4, int(round(height * float(self.rng.uniform(0.10, 0.24)))))
                box_w = max(4, int(round(width * float(self.rng.uniform(0.10, 0.24)))))
                if arm_anchors_xy and idx < len(arm_anchors_xy):
                    ax, ay = arm_anchors_xy[idx]
                    cx = int(round(ax if ax > 1.0 else ax * width))
                    cy = int(round(ay if ay > 1.0 else ay * height))
                    x0 = max(0, min(width - box_w, cx - box_w // 2))
                    y0 = max(0, min(height - box_h, cy - box_h // 2))
                else:
                    # Focus occlusion around upper/mid torso arm swing corridor [15%..75%] height.
                    x0 = int(self.rng.integers(0, max(1, width - box_w + 1)))
                    y_low = int(0.15 * height)
                    y_high = max(y_low + 1, int(0.75 * height) - box_h + 1)
                    y0 = int(self.rng.integers(y_low, min(max(y_low + 1, height - box_h + 1), y_high)))
                work[y0 : y0 + box_h, x0 : x0 + box_w] = fill_value
            applied.append("racket_arm_occlusion")

        if np.issubdtype(orig_dtype, np.integer):
            info = np.iinfo(orig_dtype)
            out = np.clip(np.rint(work), info.min, info.max).astype(orig_dtype)
        else:
            out = work.astype(orig_dtype)
        return out, applied

    def __call__(
        self,
        sample: np.ndarray | torch.Tensor,
        *,
        arm_anchors_xy: Sequence[tuple[float, float]] | None = None,
    ) -> np.ndarray | torch.Tensor:
        self.last_applied = []
        if isinstance(sample, np.ndarray):
            if sample.ndim == 3:
                out, applied = self._augment_hwc_numpy(sample, arm_anchors_xy=arm_anchors_xy)
                self.last_applied = applied
                return out
            if sample.ndim == 4:
                items = []
                applied_all: list[str] = []
                for idx in range(sample.shape[0]):
                    out, applied = self._augment_hwc_numpy(sample[idx], arm_anchors_xy=arm_anchors_xy)
                    items.append(out)
                    applied_all.extend(applied)
                self.last_applied = sorted(set(applied_all))
                return np.stack(items, axis=0)
            raise Sam3dLoraError(f"unsupported numpy sample ndim: {sample.ndim}")

        if isinstance(sample, torch.Tensor):
            device = sample.device
            dtype = sample.dtype
            cpu = sample.detach().cpu()
            if cpu.ndim == 3:
                hwc = cpu.permute(1, 2, 0).numpy()
                aug, applied = self._augment_hwc_numpy(hwc, arm_anchors_xy=arm_anchors_xy)
                self.last_applied = applied
                return torch.from_numpy(aug).permute(2, 0, 1).to(device=device, dtype=dtype)
            if cpu.ndim == 4:
                items = []
                applied_all = []
                for idx in range(cpu.shape[0]):
                    hwc = cpu[idx].permute(1, 2, 0).numpy()
                    aug, applied = self._augment_hwc_numpy(hwc, arm_anchors_xy=arm_anchors_xy)
                    items.append(torch.from_numpy(aug).permute(2, 0, 1))
                    applied_all.extend(applied)
                self.last_applied = sorted(set(applied_all))
                return torch.stack(items, dim=0).to(device=device, dtype=dtype)
            raise Sam3dLoraError(f"unsupported torch sample ndim: {sample.ndim}")

        raise Sam3dLoraError(f"unsupported sample type: {type(sample).__name__}")


@dataclass(frozen=True)
class Sam3dLoraConfig:
    name: str
    base_checkpoint: Path
    model_config: Path
    mhr_model: Path
    data_manifest: Path
    output_dir: Path
    rank: int = 16
    alpha: float = 32.0
    dropout: float = 0.05
    target_module_patterns: tuple[str, ...] = DEFAULT_TARGET_PATTERNS
    epochs: int = 20
    batch_size: int = 8
    learning_rate: float = 1e-4
    weight_decay: float = 1e-2
    max_grad_norm: float = 1.0
    seed: int = 42
    device: str = "auto"
    sequence_length: int = 8
    merge_after_training: bool = True
    weight_3d_joints: float = 1.0
    weight_temporal_smoothness: float = 0.15
    weight_reprojection_2d: float = 0.25
    weight_ground_contact: float = 0.10
    temporal_huber_delta: float = 0.5
    wrist_smoothness_weight: float = 0.25
    reprojection_huber_beta: float = 5.0
    small_roi_prob: float = 0.35
    min_roi_height: int = 64
    max_roi_height: int = 80
    motion_blur_prob: float = 0.40
    motion_blur_kernel_range: tuple[int, int] = (5, 13)
    occlusion_prob: float = 0.30
    occlusion_boxes: tuple[int, int] = (1, 2)
    license: str = "internal-evaluation-only"
    require_base_checkpoint_sha256: bool = True
    allow_synthetic_smoke: bool = True
    config_path: Path | None = None
    config_sha256: str = ""


def _resolve_path(base: Path, raw: Any, field_name: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise Sam3dLoraError(f"{field_name} must be a non-empty path")
    path = Path(raw)
    return path if path.is_absolute() else (base / path).resolve()


def load_sam3d_lora_config(path: str | Path) -> Sam3dLoraConfig:
    """Load and validate a ``Sam3dLoraConfig`` from YAML."""
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise Sam3dLoraError(f"SAM 3D LoRA config does not exist: {config_path}")
    raw_text = config_path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        raise Sam3dLoraError(f"SAM 3D LoRA config is invalid YAML: {exc}") from exc
    if raw.get("schema_version") != 1:
        raise Sam3dLoraError("schema_version must be 1")

    name = str(raw.get("name", "")).strip()
    if not name:
        raise Sam3dLoraError("name must be a non-empty string")
    base = config_path.parent
    lora = raw.get("lora") or {}
    train_sec = raw.get("training") or {}
    losses = raw.get("losses") or {}
    aug = raw.get("augmentation") or {}
    prov = raw.get("provenance") or {}

    rank = int(lora.get("rank", 16))
    alpha = float(lora.get("alpha", 32.0))
    dropout = float(lora.get("dropout", 0.05))
    if rank < 1 or alpha <= 0.0 or not 0.0 <= dropout < 1.0:
        raise Sam3dLoraError("invalid LoRA rank/alpha/dropout parameters")

    patterns = tuple(str(item).strip() for item in (lora.get("target_module_patterns") or DEFAULT_TARGET_PATTERNS) if str(item).strip())
    if not patterns:
        raise Sam3dLoraError("lora.target_module_patterns must be non-empty")

    epochs = int(train_sec.get("epochs", 20))
    batch_size = int(train_sec.get("batch_size", 8))
    learning_rate = float(train_sec.get("learning_rate", 1e-4))
    if epochs < 1 or batch_size < 1 or learning_rate <= 0:
        raise Sam3dLoraError("epochs, batch_size, and learning_rate must be positive")

    blur_range = aug.get("motion_blur_kernel_range", [5, 13])
    occ_boxes = aug.get("occlusion_boxes", [1, 2])

    return Sam3dLoraConfig(
        name=name,
        base_checkpoint=_resolve_path(base, raw.get("base_checkpoint"), "base_checkpoint"),
        model_config=_resolve_path(base, raw.get("model_config"), "model_config"),
        mhr_model=_resolve_path(base, raw.get("mhr_model"), "mhr_model"),
        data_manifest=_resolve_path(base, raw.get("data_manifest"), "data_manifest"),
        output_dir=_resolve_path(base, raw.get("output_dir"), "output_dir"),
        rank=rank,
        alpha=alpha,
        dropout=dropout,
        target_module_patterns=patterns,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        weight_decay=float(train_sec.get("weight_decay", 1e-2)),
        max_grad_norm=float(train_sec.get("max_grad_norm", 1.0)),
        seed=int(train_sec.get("seed", 42)),
        device=str(train_sec.get("device", "auto")),
        sequence_length=max(3, int(train_sec.get("sequence_length", 8))),
        merge_after_training=bool(train_sec.get("merge_after_training", True)),
        weight_3d_joints=float(losses.get("weight_3d_joints", 1.0)),
        weight_temporal_smoothness=float(losses.get("weight_temporal_smoothness", 0.15)),
        weight_reprojection_2d=float(losses.get("weight_reprojection_2d", 0.25)),
        weight_ground_contact=float(losses.get("weight_ground_contact", 0.10)),
        temporal_huber_delta=float(losses.get("temporal_huber_delta", 0.5)),
        wrist_smoothness_weight=float(losses.get("wrist_smoothness_weight", 0.25)),
        reprojection_huber_beta=float(losses.get("reprojection_huber_beta", 5.0)),
        small_roi_prob=float(aug.get("small_roi_prob", 0.35)),
        min_roi_height=int(aug.get("min_roi_height", 64)),
        max_roi_height=int(aug.get("max_roi_height", 80)),
        motion_blur_prob=float(aug.get("motion_blur_prob", 0.40)),
        motion_blur_kernel_range=(int(blur_range[0]), int(blur_range[1])),
        occlusion_prob=float(aug.get("occlusion_prob", 0.30)),
        occlusion_boxes=(int(occ_boxes[0]), int(occ_boxes[1])),
        license=str(prov.get("license", "internal-evaluation-only")).strip(),
        require_base_checkpoint_sha256=bool(prov.get("require_base_checkpoint_sha256", True)),
        allow_synthetic_smoke=bool(prov.get("allow_synthetic_smoke", True)),
        config_path=config_path,
        config_sha256=_sha256_bytes(raw_text.encode("utf-8")),
    )


class _ProxyAttention(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.q_proj = nn.Linear(dim, dim)
        self.k_proj = nn.Linear(dim, dim)
        self.v_proj = nn.Linear(dim, dim)
        self.proj = nn.Linear(dim, dim)

    def forward(self, q: torch.Tensor, k: torch.Tensor, v: torch.Tensor) -> torch.Tensor:
        scale = math.sqrt(float(q.shape[-1]))
        attn = torch.softmax((self.q_proj(q) @ self.k_proj(k).transpose(-2, -1)) / scale, dim=-1)
        return self.proj(attn @ self.v_proj(v))


class _ProxyDecoderLayer(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.self_attn = _ProxyAttention(dim)
        self.cross_attn = _ProxyAttention(dim)
        self.ffn = nn.Sequential(
            nn.Linear(dim, dim * 2),
            nn.GELU(),
            nn.Linear(dim * 2, dim),
        )
        self.norm1 = nn.LayerNorm(dim)
        self.norm2 = nn.LayerNorm(dim)

    def forward(self, tokens: torch.Tensor, context: torch.Tensor) -> torch.Tensor:
        tokens = self.norm1(tokens + self.self_attn(tokens, tokens, tokens))
        tokens = self.norm2(tokens + self.cross_attn(tokens, context, context))
        return tokens + self.ffn(tokens)


class _ProxyDecoder(nn.Module):
    def __init__(self, dim: int, depth: int = 2) -> None:
        super().__init__()
        self.layers = nn.ModuleList([_ProxyDecoderLayer(dim) for _ in range(depth)])


class _ProxyHead(nn.Module):
    def __init__(self, dim: int, out_dim: int) -> None:
        super().__init__()
        self.proj = nn.Sequential(
            nn.Linear(dim, dim),
            nn.GELU(),
            nn.Linear(dim, out_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)


class MiniSam3dDecoderProxy(nn.Module):
    """Lightweight Transformer-decoder proxy matching SAM 3D Body's module hierarchy.

    Used by ``--smoke-synthetic`` to exercise LoRA injection, freezing, tennis physics
    losses, backward steps, SHA-256 checkpointing, and zero-overhead merging without
    requiring the 4 GB official checkpoint.
    """

    def __init__(self, *, embed_dim: int = 64, num_joints: int = 70, depth: int = 2) -> None:
        super().__init__()
        self.num_joints = num_joints
        self.backbone = nn.Sequential(
            nn.Conv2d(3, 16, kernel_size=3, stride=2, padding=1),
            nn.GELU(),
            nn.Conv2d(16, embed_dim, kernel_size=3, stride=2, padding=1),
            nn.GELU(),
        )
        self.init_to_token_mhr = nn.Linear(embed_dim, embed_dim)
        self.prev_to_token_mhr = nn.Linear(embed_dim, embed_dim)
        self.joint_queries = nn.Parameter(torch.randn(1, num_joints, embed_dim) * 0.02)
        self.decoder = _ProxyDecoder(embed_dim, depth=depth)
        self.head_pose = _ProxyHead(embed_dim, 3)
        self.head_camera = _ProxyHead(embed_dim, 3)

    def forward(self, frames: torch.Tensor) -> dict[str, torch.Tensor]:
        """Forward pass on ``(B, T, C, H, W)`` image clips."""
        bsz, t_steps, channels, height, width = frames.shape
        flat = frames.reshape(bsz * t_steps, channels, height, width)
        feat = self.backbone(flat)  # (B*T, D, h', w')
        context = feat.flatten(2).transpose(1, 2)  # (B*T, N_ctx, D)
        pooled = context.mean(dim=1, keepdim=True)
        base_token = self.init_to_token_mhr(pooled) + 0.1 * self.prev_to_token_mhr(pooled)
        tokens = self.joint_queries.expand(bsz * t_steps, -1, -1) + base_token
        for layer in self.decoder.layers:
            tokens = layer(tokens, context)
        joints_3d = self.head_pose(tokens).reshape(bsz, t_steps, self.num_joints, 3)
        cam_raw = self.head_camera(tokens.mean(dim=1)).reshape(bsz, t_steps, 3)
        # Keep Z positive and realistic (~4.5m in camera frame).
        camera_t = torch.stack(
            (cam_raw[..., 0] * 0.25, cam_raw[..., 1] * 0.25, 4.5 + torch.tanh(cam_raw[..., 2])),
            dim=-1,
        )
        return {"joints_3d": joints_3d, "camera_t": camera_t}


def _resolve_training_device(requested: str) -> torch.device:
    value = requested.strip().lower()
    if value == "auto":
        return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    if value.isdigit():
        return torch.device(f"cuda:{value}" if torch.cuda.is_available() else "cpu")
    return torch.device(value)


def _generate_synthetic_tennis_batch(
    *,
    batch_size: int,
    seq_len: int,
    image_size: int = 96,
    num_joints: int = 70,
    seed: int = 42,
    augmenter: TennisHardCaseAugmenter,
) -> dict[str, torch.Tensor]:
    rng = np.random.default_rng(seed)
    # Non-uniform VFR frame deltas (28ms..44ms)
    dt_np = rng.uniform(0.028, 0.044, size=(batch_size, seq_len - 1)).astype(np.float32)
    t_cum = np.concatenate(
        [np.zeros((batch_size, 1), dtype=np.float32), np.cumsum(dt_np, axis=1)],
        axis=1,
    )

    # Rest pose skeleton centered at y=0, ankles on ground y=+0.95 (camera Y-down).
    base_joints = np.zeros((num_joints, 3), dtype=np.float32)
    angles = np.linspace(0.0, 2.0 * math.pi, num_joints, endpoint=False, dtype=np.float32)
    base_joints[:, 0] = 0.25 * np.cos(angles)
    base_joints[:, 1] = 0.55 * np.sin(angles)
    base_joints[:, 2] = 0.10 * np.sin(2.0 * angles)
    for ankle_idx in MHR70_ANKLE_INDICES:
        if ankle_idx < num_joints:
            base_joints[ankle_idx] = np.array([0.15 if ankle_idx == MHR70_ANKLE_INDICES[1] else -0.15, 0.95, 0.0], dtype=np.float32)

    target_3d = np.broadcast_to(base_joints, (batch_size, seq_len, num_joints, 3)).copy()
    # Add smooth forehand wrist whip + subtle body rotation.
    for b in range(batch_size):
        phase = float(rng.uniform(0.0, math.pi))
        for t in range(seq_len):
            tau = float(t_cum[b, t])
            target_3d[b, t, :, 0] += 0.12 * math.sin(2.0 * tau + phase)
            if MHR70_RIGHT_WRIST_INDEX < num_joints:
                target_3d[b, t, MHR70_RIGHT_WRIST_INDEX, 0] += 0.45 * math.sin(8.0 * tau + phase)
                target_3d[b, t, MHR70_RIGHT_WRIST_INDEX, 1] -= 0.30 * math.cos(8.0 * tau + phase)

    camera_t = np.zeros((batch_size, seq_len, 3), dtype=np.float32)
    camera_t[..., 2] = 4.5
    focal = np.full((batch_size, seq_len), float(image_size * 1.4), dtype=np.float32)
    principal = np.full((batch_size, seq_len, 2), float(image_size) * 0.5, dtype=np.float32)

    j3d_cam = target_3d + camera_t[:, :, None, :]
    target_2d = focal[:, :, None, None] * (j3d_cam[..., :2] / j3d_cam[..., 2:3]) + principal[:, :, None, :]
    visibility_mask = np.ones((batch_size, seq_len, num_joints), dtype=np.float32)
    visibility_mask[:, :, ::7] = 0.0  # Simulate partial occlusion

    # Ground plane: y_cam = 4.5*0 + 0.95 -> normal [0, 1, 0], offset -0.95
    ground_normal = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    ground_offset = -0.95
    foot_contact_mask = np.ones((batch_size, seq_len, len(MHR70_ANKLE_INDICES)), dtype=np.float32)

    # Render textured synthetic player ROI crops and pass through TennisHardCaseAugmenter.
    frames = np.zeros((batch_size, seq_len, 3, image_size, image_size), dtype=np.float32)
    for b in range(batch_size):
        for t in range(seq_len):
            canvas = np.full((image_size, image_size, 3), 0.25, dtype=np.float32)
            for j in range(0, num_joints, 4):
                u = int(np.clip(round(target_2d[b, t, j, 0]), 2, image_size - 3))
                v = int(np.clip(round(target_2d[b, t, j, 1]), 2, image_size - 3))
                canvas[v - 2 : v + 3, u - 2 : u + 3] = np.array([0.85, 0.65, 0.35], dtype=np.float32)
            aug = augmenter(canvas)
            frames[b, t] = np.transpose(aug, (2, 0, 1))

    return {
        "frames": torch.from_numpy(frames),
        "target_3d": torch.from_numpy(target_3d),
        "dt_s": torch.from_numpy(dt_np),
        "focal": torch.from_numpy(focal),
        "principal": torch.from_numpy(principal),
        "target_2d": torch.from_numpy(target_2d),
        "visibility_mask": torch.from_numpy(visibility_mask),
        "ground_normal": torch.from_numpy(ground_normal),
        "ground_offset": torch.tensor(ground_offset, dtype=torch.float32),
        "foot_contact_mask": torch.from_numpy(foot_contact_mask),
    }


def _load_real_manifest_records(manifest_path: Path) -> tuple[list[dict[str, Any]], str]:
    if not manifest_path.is_file():
        raise Sam3dLoraError(f"SAM 3D training manifest does not exist: {manifest_path}")
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Sam3dLoraError(f"{manifest_path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise Sam3dLoraError(f"{manifest_path}:{line_no}: record must be a JSON object")
        records.append(row)
    if not records:
        raise Sam3dLoraError(f"SAM 3D training manifest is empty: {manifest_path}")
    return records, _sha256_file(manifest_path)


def _run_smoke_synthetic_training(
    config: Sam3dLoraConfig,
    *,
    output_dir: Path,
    epochs: int,
    merge_weights: bool,
    device: torch.device,
) -> dict[str, Any]:
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)

    model = MiniSam3dDecoderProxy(embed_dim=64, num_joints=70, depth=2).to(device)
    base_state_sha = _state_dict_sha256(model.state_dict())

    smoke_patterns = (
        "decoder.layers.*.self_attn.*",
        "decoder.layers.*.cross_attn.*",
        "decoder.layers.*.ffn.*",
        "head_pose.proj.*",
        "head_camera.proj.*",
    )
    adapted_modules = inject_lora_adapters(
        model,
        smoke_patterns,
        rank=config.rank,
        alpha=config.alpha,
        dropout=config.dropout,
    )
    param_stats = freeze_non_lora_parameters(model)
    if param_stats["trainable_params"] <= 0 or param_stats["trainable_params"] >= param_stats["total_params"]:
        raise Sam3dLoraError("smoke LoRA freeze invariant failed")

    augmenter = TennisHardCaseAugmenter(
        small_roi_prob=config.small_roi_prob,
        min_roi_height=config.min_roi_height,
        max_roi_height=config.max_roi_height,
        motion_blur_prob=config.motion_blur_prob,
        motion_blur_kernel_range=config.motion_blur_kernel_range,
        occlusion_prob=config.occlusion_prob,
        occlusion_boxes=config.occlusion_boxes,
        seed=config.seed,
    )
    batch = _generate_synthetic_tennis_batch(
        batch_size=min(config.batch_size, 2),
        seq_len=min(config.sequence_length, 6),
        image_size=96,
        num_joints=70,
        seed=config.seed,
        augmenter=augmenter,
    )
    dataset_digest = _canonical_json_sha256(
        {
            "mode": "synthetic_tennis_smoke_v1",
            "seed": config.seed,
            "frames_shape": list(batch["frames"].shape),
            "dt_s_sum": round(float(batch["dt_s"].sum()), 6),
        }
    )

    frames = batch["frames"].to(device)
    target_3d = batch["target_3d"].to(device)
    dt_s = batch["dt_s"].to(device)
    focal = batch["focal"].to(device)
    principal = batch["principal"].to(device)
    target_2d = batch["target_2d"].to(device)
    vis_mask = batch["visibility_mask"].to(device)
    ground_normal = batch["ground_normal"].to(device)
    ground_offset = batch["ground_offset"].to(device)
    contact_mask = batch["foot_contact_mask"].to(device)

    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=max(config.learning_rate, 2e-3),
        weight_decay=config.weight_decay,
    )

    model.train()
    loss_history: list[float] = []
    for _ in range(max(1, epochs)):
        optimizer.zero_grad(set_to_none=True)
        out = model(frames)
        pred_3d = out["joints_3d"]
        camera_t = out["camera_t"]

        loss_3d = F.mse_loss(pred_3d, target_3d)
        loss_temp = temporal_kinematic_smoothness_loss(
            pred_3d,
            dt_s,
            wrist_weight=config.wrist_smoothness_weight,
            huber_delta=config.temporal_huber_delta,
        )
        loss_reproj = reprojection_2d_consistency_loss(
            pred_3d,
            camera_t,
            focal,
            target_2d,
            vis_mask,
            principal_point=principal,
            huber_beta=config.reprojection_huber_beta,
        )
        ankles_3d = pred_3d[:, :, list(MHR70_ANKLE_INDICES), :]
        loss_ground = ground_plane_contact_prior_loss(
            ankles_3d,
            contact_mask,
            ground_normal,
            ground_offset,
        )
        total_loss = (
            config.weight_3d_joints * loss_3d
            + config.weight_temporal_smoothness * loss_temp
            + config.weight_reprojection_2d * loss_reproj
            + config.weight_ground_contact * loss_ground
        )
        total_loss.backward()
        torch.nn.utils.clip_grad_norm_(
            [p for p in model.parameters() if p.requires_grad],
            config.max_grad_norm,
        )
        optimizer.step()
        loss_history.append(round(float(total_loss.detach().item()), 6))

    config_sha = config.config_sha256 or _canonical_json_sha256(
        {
            "name": config.name,
            "rank": config.rank,
            "alpha": config.alpha,
            "dropout": config.dropout,
            "patterns": list(smoke_patterns),
        }
    )

    run_dir = (output_dir / config.name / "smoke_synthetic").resolve()
    checkpoint_info = save_lora_checkpoint(
        model,
        run_dir / "checkpoint",
        base_checkpoint_sha256=base_state_sha,
        config_sha256=config_sha,
        dataset_digest=dataset_digest,
        base_checkpoint_path="synthetic://mini-sam3d-decoder-proxy",
        target_module_patterns=smoke_patterns,
        extra_metadata={
            "mode": "smoke_synthetic",
            "epochs": epochs,
            "initial_loss": loss_history[0],
            "final_loss": loss_history[-1],
        },
    )

    # Verify zero-overhead algebraic merge equivalence on eval() forward pass.
    model.eval()
    with torch.no_grad():
        unmerged_out = model(frames)["joints_3d"].clone()
    max_merge_diff = 0.0
    if merge_weights:
        merge_lora_weights(model)
        with torch.no_grad():
            merged_out = model(frames)["joints_3d"]
        max_merge_diff = float((unmerged_out - merged_out).abs().max().item())

    return {
        "mode": "smoke_synthetic",
        "device": str(device),
        "epochs": epochs,
        "adapted_module_count": len(adapted_modules),
        "trainable_parameters": param_stats,
        "initial_loss": loss_history[0],
        "final_loss": loss_history[-1],
        "loss_history": loss_history,
        "merged_weights": merge_weights,
        "merge_max_abs_diff": max_merge_diff,
        "checkpoint_dir": str(run_dir / "checkpoint"),
        "manifest_path": checkpoint_info["manifest_path"],
        "weights_sha256": checkpoint_info["manifest"]["weights_sha256"],
        "base_checkpoint_sha256": base_state_sha,
        "config_sha256": config_sha,
        "dataset_digest": dataset_digest,
    }


def run_sam3d_lora_training(
    config: Sam3dLoraConfig,
    *,
    smoke_synthetic: bool = False,
    output_dir: str | Path | None = None,
    epochs: int | None = None,
    merge_weights: bool | None = None,
) -> dict[str, Any]:
    """Run SAM 3D Body tennis LoRA fine-tuning or deterministic ``--smoke-synthetic`` validation."""
    resolved_output = Path(output_dir).resolve() if output_dir is not None else config.output_dir
    resolved_epochs = int(epochs) if epochs is not None else config.epochs
    resolved_merge = config.merge_after_training if merge_weights is None else bool(merge_weights)
    device = _resolve_training_device(config.device)

    if smoke_synthetic:
        if not config.allow_synthetic_smoke:
            raise Sam3dLoraError("synthetic smoke mode is disabled by config provenance")
        return _run_smoke_synthetic_training(
            config,
            output_dir=resolved_output,
            epochs=max(1, min(resolved_epochs, 10) if epochs is None else resolved_epochs),
            merge_weights=resolved_merge,
            device=device,
        )

    for path, label in (
        (config.base_checkpoint, "base_checkpoint"),
        (config.model_config, "model_config"),
        (config.mhr_model, "mhr_model"),
        (config.data_manifest, "data_manifest"),
    ):
        if not path.is_file():
            raise Sam3dLoraError(f"{label} is missing: {path}")

    base_sha = _sha256_file(config.base_checkpoint)
    config_sha = config.config_sha256 or (
        _sha256_file(config.config_path) if config.config_path and config.config_path.is_file() else _canonical_json_sha256(asdict(config))
    )
    records, dataset_digest = _load_real_manifest_records(config.data_manifest)

    sam_repo = Path(__file__).resolve().parents[2] / "runtime" / "sam3d" / "repo"
    if str(sam_repo) not in sys.path:
        sys.path.insert(0, str(sam_repo))
    try:
        from sam_3d_body import load_sam_3d_body
        from sam_3d_body.data.transforms import Compose, GetBBoxCenterScale, TopdownAffine, VisionTransformWrapper
        from sam_3d_body.data.utils.prepare_batch import prepare_batch
        from sam_3d_body.models.modules.geometry_utils import get_cam_intrinsics
        from sam_3d_body.utils import recursive_to
        from torchvision.transforms import ToTensor
    except ImportError as exc:
        raise Sam3dLoraError(
            "official sam_3d_body package is required for non-smoke training; "
            "run with --smoke-synthetic to verify the LoRA pipeline without 4 GB weights"
        ) from exc

    model, model_cfg = load_sam_3d_body(
        checkpoint_path=str(config.base_checkpoint),
        mhr_path=str(config.mhr_model),
        device=str(device),
    )
    adapted_modules = inject_lora_adapters(
        model,
        config.target_module_patterns,
        rank=config.rank,
        alpha=config.alpha,
        dropout=config.dropout,
    )
    param_stats = freeze_non_lora_parameters(model)
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    augmenter = TennisHardCaseAugmenter(
        small_roi_prob=config.small_roi_prob,
        min_roi_height=config.min_roi_height,
        max_roi_height=config.max_roi_height,
        motion_blur_prob=config.motion_blur_prob,
        motion_blur_kernel_range=config.motion_blur_kernel_range,
        occlusion_prob=config.occlusion_prob,
        occlusion_boxes=config.occlusion_boxes,
        seed=config.seed,
    )
    transform = Compose(
        [
            GetBBoxCenterScale(),
            TopdownAffine(input_size=model_cfg.MODEL.IMAGE_SIZE, use_udp=False),
            VisionTransformWrapper(ToTensor()),
        ]
    )

    model.train()
    loss_history: list[float] = []
    for _epoch in range(resolved_epochs):
        epoch_losses: list[float] = []
        for seq_record in records:
            seq_frames = seq_record.get("frames") or []
            if not seq_frames:
                continue
            optimizer.zero_grad(set_to_none=True)
            pred_3d_steps: list[torch.Tensor] = []
            cam_t_steps: list[torch.Tensor] = []
            focal_steps: list[torch.Tensor] = []
            principal_steps: list[torch.Tensor] = []
            target_2d_steps: list[torch.Tensor] = []
            vis_mask_steps: list[torch.Tensor] = []
            target_3d_steps: list[torch.Tensor] = []
            contact_steps: list[torch.Tensor] = []
            timestamps_ms: list[float] = []
            has_3d = True

            for item in seq_frames[: config.sequence_length]:
                img_bgr = cv2.imread(str(item["image_path"]), cv2.IMREAD_COLOR)
                if img_bgr is None:
                    raise Sam3dLoraError(f"failed to load training frame: {item['image_path']}")
                img_rgb = np.ascontiguousarray(img_bgr[:, :, ::-1])
                bbox = np.asarray(item["bbox_px"], dtype=np.float32).reshape(1, 4)
                x1, y1, x2, y2 = [int(round(v)) for v in bbox[0]]
                x1 = max(0, min(img_rgb.shape[1] - 1, x1))
                y1 = max(0, min(img_rgb.shape[0] - 1, y1))
                x2 = max(x1 + 1, min(img_rgb.shape[1], x2))
                y2 = max(y1 + 1, min(img_rgb.shape[0], y2))
                img_rgb[y1:y2, x1:x2] = augmenter(img_rgb[y1:y2, x1:x2])

                cam_int = torch.tensor(
                    get_cam_intrinsics(max(img_rgb.shape[:2])),
                    dtype=torch.float32,
                ).unsqueeze(0)
                batch = prepare_batch(img_rgb, transform, bbox, cam_int=cam_int)
                batch = recursive_to(batch, str(device))
                mhr_out = model.forward_step(batch, decoder_type="body")["mhr"]

                pred_3d_steps.append(mhr_out["pred_keypoints_3d"])
                cam_t_steps.append(mhr_out["pred_cam_t"])
                focal_steps.append(mhr_out["focal_length"].reshape(-1))
                principal_steps.append(cam_int[:, :2, 2].to(device))
                target_2d_steps.append(
                    torch.tensor(item["keypoints_2d"], dtype=torch.float32, device=device).unsqueeze(0)
                )
                vis_mask_steps.append(
                    torch.tensor(item["visibility_mask"], dtype=torch.float32, device=device).unsqueeze(0)
                )
                if "keypoints_3d" in item and item["keypoints_3d"] is not None:
                    target_3d_steps.append(
                        torch.tensor(item["keypoints_3d"], dtype=torch.float32, device=device).unsqueeze(0)
                    )
                else:
                    has_3d = False
                contact_steps.append(
                    torch.tensor(
                        item.get("foot_contact_mask", [0.0, 0.0]),
                        dtype=torch.float32,
                        device=device,
                    ).unsqueeze(0)
                )
                timestamps_ms.append(float(item["timestamp_ms"]))

            if not pred_3d_steps:
                continue
            pred_3d_seq = torch.stack(pred_3d_steps, dim=1)
            cam_t_seq = torch.stack(cam_t_steps, dim=1)
            focal_seq = torch.stack(focal_steps, dim=1)
            principal_seq = torch.stack(principal_steps, dim=1)
            target_2d_seq = torch.stack(target_2d_steps, dim=1)
            vis_mask_seq = torch.stack(vis_mask_steps, dim=1)
            contact_seq = torch.stack(contact_steps, dim=1)

            loss = reprojection_2d_consistency_loss(
                pred_3d_seq,
                cam_t_seq,
                focal_seq,
                target_2d_seq,
                vis_mask_seq,
                principal_point=principal_seq,
                huber_beta=config.reprojection_huber_beta,
            ) * config.weight_reprojection_2d

            if len(pred_3d_steps) >= 3:
                dt_vals = [
                    max((timestamps_ms[i + 1] - timestamps_ms[i]) / 1000.0, 1e-3)
                    for i in range(len(timestamps_ms) - 1)
                ]
                dt_tensor = torch.tensor(dt_vals, dtype=torch.float32, device=device)
                loss = loss + config.weight_temporal_smoothness * temporal_kinematic_smoothness_loss(
                    pred_3d_seq,
                    dt_tensor,
                    wrist_weight=config.wrist_smoothness_weight,
                    huber_delta=config.temporal_huber_delta,
                )

            if has_3d and target_3d_steps:
                target_3d_seq = torch.stack(target_3d_steps, dim=1)
                loss = loss + config.weight_3d_joints * F.mse_loss(pred_3d_seq, target_3d_seq)

            if "ground_normal" in seq_record and "ground_offset" in seq_record:
                ankles = pred_3d_seq[:, :, list(MHR70_ANKLE_INDICES), :]
                loss = loss + config.weight_ground_contact * ground_plane_contact_prior_loss(
                    ankles,
                    contact_seq,
                    seq_record["ground_normal"],
                    float(seq_record["ground_offset"]),
                )

            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                [p for p in model.parameters() if p.requires_grad],
                config.max_grad_norm,
            )
            optimizer.step()
            epoch_losses.append(float(loss.detach().item()))
        if epoch_losses:
            loss_history.append(round(sum(epoch_losses) / len(epoch_losses), 6))

    run_dir = (resolved_output / config.name).resolve()
    checkpoint_info = save_lora_checkpoint(
        model,
        run_dir / "checkpoint",
        base_checkpoint_sha256=base_sha,
        config_sha256=config_sha,
        dataset_digest=dataset_digest,
        base_checkpoint_path=config.base_checkpoint,
        target_module_patterns=config.target_module_patterns,
        extra_metadata={
            "sample_count": len(records),
            "epochs_completed": resolved_epochs,
            "loss_history": loss_history,
        },
    )
    if resolved_merge:
        merge_lora_weights(model)

    return {
        "mode": "real_checkpoint",
        "device": str(device),
        "sample_count": len(records),
        "epochs": resolved_epochs,
        "loss_history": loss_history,
        "adapted_module_count": len(adapted_modules),
        "trainable_parameters": param_stats,
        "merged_weights": resolved_merge,
        "checkpoint_dir": str(run_dir / "checkpoint"),
        "manifest_path": checkpoint_info["manifest_path"],
        "base_checkpoint_sha256": base_sha,
        "config_sha256": config_sha,
        "dataset_digest": dataset_digest,
    }
