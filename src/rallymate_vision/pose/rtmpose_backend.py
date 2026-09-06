from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from typing import Sequence

import numpy as np

from rallymate_vision.pose.base import PoseBackendOutput, PoseModelMetadata
from rallymate_vision.pose.metadata import keypoint_schema, sha256_file


class RtmposePoseBackend:
    """MMPose RTMPose backend for an explicitly registered keypoint topology."""

    def __init__(
        self,
        model_path: Path,
        config_path: Path,
        *,
        device: str,
        runtime: str = "pytorch",
        profile: str = "realtime",
        native_keypoint_format: str = "halpe26",
    ) -> None:
        self.model_path = Path(model_path)
        self.config_path = Path(config_path)
        self.device = self._mmpose_device(device)
        self.runtime = runtime
        self.profile = profile
        self.native_keypoint_format = native_keypoint_format
        self._schema = keypoint_schema(native_keypoint_format)
        self.model = None
        self._inference_topdown = None
        self._load_seconds = 0.0
        self._input_size_hw = (0, 0)
        self._model_sha256 = ""
        self._config_sha256 = ""
        self.load()

    @staticmethod
    def _mmpose_device(device: str) -> str:
        value = str(device).strip().lower()
        if value == "cpu":
            return "cpu"
        if value.startswith("cuda:"):
            return value
        if value.isdigit():
            return f"cuda:{value}"
        raise ValueError(f"unsupported MMPose device: {device!r}")

    @staticmethod
    def _install_mmcv_lite_compatibility() -> None:
        """Skip optional EDPose registration; RTMPose does not use mmcv ops."""

        module_name = "mmpose.models.heads.transformer_heads"
        if module_name not in sys.modules:
            stub = types.ModuleType(module_name)
            stub.EDPoseHead = None
            sys.modules[module_name] = stub

    def load(self) -> None:
        if self.model is not None:
            return
        if self.runtime != "pytorch":
            raise ValueError(
                "RTMPose milestone P3 supports runtime=pytorch in the isolated "
                "MMPose environment; ONNX/TensorRT export is not yet promoted"
            )
        if not self.model_path.exists():
            raise FileNotFoundError(f"RTMPose checkpoint is missing: {self.model_path}")
        if not self.config_path.exists():
            raise FileNotFoundError(f"RTMPose config is missing: {self.config_path}")
        self._model_sha256 = sha256_file(self.model_path)
        self._config_sha256 = sha256_file(self.config_path)
        try:
            import torch
            from mmengine.config import Config

            self._install_mmcv_lite_compatibility()
            from mmpose.apis import inference_topdown, init_model
        except ImportError as exc:
            raise RuntimeError(
                "RTMPose dependencies are unavailable. Launch the worker with "
                "runtime/rtmpose/.venv/Scripts/python.exe."
            ) from exc

        cfg = Config.fromfile(self.config_path)
        # MMPose includes the same CSPNeXt implementation used by this model.
        # Selecting it avoids compiled MMDetection ops that RTMPose does not use.
        cfg.model.backbone.pop("_scope_", None)
        cfg.default_scope = "mmpose"
        cfg.model.setdefault("test_cfg", {})["flip_test"] = self.profile == "analysis"
        config_input = tuple(int(value) for value in cfg.codec.input_size)
        self._input_size_hw = (config_input[1], config_input[0])

        original_torch_load = torch.load

        def trusted_checkpoint_load(*args, **kwargs):
            # PyTorch 2.6+ defaults weights_only=True, while official MMPose
            # checkpoints include metadata. The checkpoint hash is recorded.
            kwargs.setdefault("weights_only", False)
            return original_torch_load(*args, **kwargs)

        started = time.perf_counter()
        torch.load = trusted_checkpoint_load
        try:
            self.model = init_model(
                cfg,
                str(self.model_path),
                device=self.device,
            )
        finally:
            torch.load = original_torch_load
        self._load_seconds = time.perf_counter() - started
        self._inference_topdown = inference_topdown

    def infer(
        self,
        rois: Sequence[np.ndarray],
        *,
        timestamps_ms: Sequence[int] | None = None,
    ) -> list[PoseBackendOutput | None]:
        del timestamps_ms
        assert self.model is not None and self._inference_topdown is not None
        outputs: list[PoseBackendOutput | None] = []
        for roi in rois:
            results = self._inference_topdown(self.model, roi, bboxes=None)
            if not results:
                outputs.append(None)
                continue
            instances = results[0].pred_instances
            xy = np.asarray(instances.keypoints[0], dtype=np.float32)
            scores = np.asarray(instances.keypoint_scores[0], dtype=np.float32)
            expected = int(self._schema["count"])
            if xy.shape != (expected, 2) or scores.shape != (expected,):
                raise ValueError(
                    f"RTMPose {self.native_keypoint_format} returned "
                    f"xy={xy.shape}, scores={scores.shape}; expected {expected} points"
                )
            outputs.append(
                PoseBackendOutput(
                    keypoints_xy=xy,
                    keypoint_scores=scores,
                    pose_score=float(np.mean(scores)),
                )
            )
        return outputs

    def metadata(self) -> PoseModelMetadata:
        schema = self._schema
        return PoseModelMetadata(
            backend="rtmpose",
            runtime=self.runtime,
            profile=self.profile,
            model_name=self.model_path.stem,
            model_path=str(self.model_path),
            model_sha256=self._model_sha256,
            input_size=self._input_size_hw,
            native_keypoint_format=self.native_keypoint_format,
            native_keypoint_count=int(schema["count"]),
            keypoint_schema_version="1.0.0",
            load_seconds=round(self._load_seconds, 6),
            load_semantics="mmpose_init_model_checkpoint_materialized_on_selected_device",
            config_path=str(self.config_path),
            config_sha256=self._config_sha256,
            pose_score_semantics=(
                f"mean_{self.native_keypoint_format}_keypoint_confidence"
            ),
        )
