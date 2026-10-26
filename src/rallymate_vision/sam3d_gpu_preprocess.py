"""Instance-scoped uint8 upload for the pinned official SAM body transform.

OpenCV computes the unchanged affine crop. Only ToTensor's layout/float/255
work moves to the CUDA device; the official prepare_batch remains in use.
No model or optional numerical packages are imported at module import time.
"""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path


OFFICIAL_TRANSFORMS_SHA256 = "e6e1f95880ee0ab8074342e38762ead9293eba1b75dcbb4d40848fde1c3e3634"
OFFICIAL_PREPARE_BATCH_SHA256 = "993d62352ff67d42b2c6d6a707a45818992422cfe863e584702a2deb7fe09257"
OFFICIAL_ESTIMATOR_SHA256 = "9690d55273db4a8e56660407a2366cc398b09b98a7f9d4fcf7772d13497c04d8"


def _source_sha256(target):
    return hashlib.sha256(Path(inspect.getsourcefile(target)).read_bytes()).hexdigest()


class _DeviceToTensor:
    """Transfer compact crops and normalize with verified uint8 rounding.

    A single reusable pinned allocation is bounded by the current crop shape.
    An event protects it from reuse before an asynchronous transfer completes.
    CUDA output tensors are newly allocated on every call and never reused.
    The CPU device exists only for tests; the public binding requires CUDA.
    """

    def __init__(self, original, device, receipt):
        import torch

        self.torch, self.original = torch, original
        self.device = torch.device(device)
        self.receipt = receipt
        self._pinned = None
        self._copy_done = None
        # ToTensor's uint8 case uses the current default floating dtype. The
        # optimized path is limited to float32; other defaults use the original.
        values = torch.arange(256, dtype=torch.uint8).reshape(16, 16, 1)
        reference = original(values.numpy()).flatten().contiguous()
        if reference.dtype != torch.float32:
            raise ValueError("GPU preprocessing requires the official float32 ToTensor default")
        self._reference = reference.to(self.device)
        candidate = values.flatten().to(self.device).float().div(255)
        self._direct_division_exact = bool(torch.equal(reference, candidate.cpu()))
        receipt.update(
            normalization_policy=("gpu_division_verified_all_256_values" if self._direct_division_exact
                                  else "gpu_lookup_of_official_cpu_float32_values"),
            uint8_normalization_all_values_verified=True,
            optimized_calls=0, original_fallback_calls=0, uploaded_image_bytes=0,
        )

    def _wait_for_copy(self):
        if self._copy_done is not None:
            self._copy_done.synchronize()
            self._copy_done = None

    def __call__(self, pixels):
        import numpy as np

        torch = self.torch
        if (not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8
                or pixels.ndim != 3 or pixels.shape[2] != 3
                or not pixels.flags.c_contiguous
                or torch.get_default_dtype() != torch.float32):
            self.receipt["original_fallback_calls"] += 1
            return self.original(pixels)

        cpu = torch.from_numpy(pixels)
        if self.device.type == "cuda":
            self._wait_for_copy()
            if self._pinned is None or tuple(self._pinned.shape) != tuple(cpu.shape):
                self._pinned = torch.empty(cpu.shape, dtype=torch.uint8, pin_memory=True)
            self._pinned.copy_(cpu)
            uploaded = self._pinned.to(self.device, non_blocking=True)
            self._copy_done = torch.cuda.Event()
            self._copy_done.record(torch.cuda.current_stream(self.device))
        else:
            uploaded = cpu.to(self.device, copy=True)

        # The official transform creates contiguous CHW before converting to
        # float. Preserve that layout, order and output dtype on the device.
        chw = uploaded.permute(2, 0, 1).contiguous()
        if self._direct_division_exact:
            result = chw.float().div(255)
        else:
            result = self._reference[chw.long()]
        self.receipt["optimized_calls"] += 1
        self.receipt["uploaded_image_bytes"] += int(pixels.nbytes)
        return result

    def close(self):
        self._wait_for_copy()
        self._pinned = None
        self._reference = None


class GPUPreprocessHandle:
    def __init__(self, estimator, original, replacement, converter, receipt):
        self._estimator, self._original = estimator, original
        self._replacement, self._converter = replacement, converter
        self.receipt = receipt
        self._restored = False

    def restore(self):
        if self._restored:
            return
        # Do not overwrite a later replacement from another owner.
        if self._estimator.transform is not self._replacement:
            raise RuntimeError("SAM body transform changed while GPU preprocessing was installed")
        self._estimator.transform = self._original
        self._restored = True
        self._converter.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.restore()


def apply_gpu_preprocess(estimator, *, inference_type="body"):
    """Replace only this estimator's body ToTensor; return a restoring handle.

    The caller owns the isolated estimator, must select body inference, and
    restores the handle before destroying the CUDA runtime. Unsupported image
    types/layouts retain the exact original ToTensor path.
    """
    import torch
    from torchvision.transforms import ToTensor

    if inference_type != "body":
        raise ValueError("GPU preprocessing is validated only for body inference")
    if estimator.model.training:
        raise ValueError("GPU preprocessing requires eval mode")
    device = torch.device(estimator.device)
    if device.type != "cuda":
        raise ValueError("GPU preprocessing requires a CUDA estimator")
    source = inspect.getmodule(type(estimator))
    if (source is None or _source_sha256(type(estimator)) != OFFICIAL_ESTIMATOR_SHA256
            or _source_sha256(source.prepare_batch) != OFFICIAL_PREPARE_BATCH_SHA256):
        raise ValueError("Official estimator or prepare_batch source identity differs")
    transform = estimator.transform
    if (type(transform).__name__ != "Compose"
            or _source_sha256(type(transform)) != OFFICIAL_TRANSFORMS_SHA256):
        raise ValueError("Official body Compose source identity differs")
    items = transform.transforms
    if (len(items) != 3 or type(items[0]).__name__ != "GetBBoxCenterScale"
            or type(items[1]).__name__ != "TopdownAffine"
            or type(items[2]).__name__ != "VisionTransformWrapper"
            or any(_source_sha256(type(item)) != OFFICIAL_TRANSFORMS_SHA256 for item in items)
            or type(items[2].transform) is not ToTensor):
        raise ValueError("Official body crop/ToTensor composition differs")

    receipt = {
        "candidate": "official_crop_uint8_cuda_to_tensor_v1",
        "official_estimator_sha256": OFFICIAL_ESTIMATOR_SHA256,
        "official_prepare_batch_sha256": OFFICIAL_PREPARE_BATCH_SHA256,
        "official_transforms_sha256": OFFICIAL_TRANSFORMS_SHA256,
        "candidate_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "body_transform_to_tensor_only",
        "opencv_crop_unchanged": True,
        "prepare_batch_unchanged": True,
        "hand_transform_unchanged": True,
        "model_parameters_precision_and_heads_unchanged": True,
        "package_globals_or_source_changed": False,
        "uint8_image_upload_bytes_fraction_of_float32": 0.25,
        "pinned_staging_slots": 1,
        "pinned_reuse_protected_by_cuda_event": True,
        "device_output_storage_reused": False,
    }
    converter = _DeviceToTensor(items[2].transform, device, receipt)
    try:
        wrapper = type(items[2])(converter)
        replacement = type(transform)([items[0], items[1], wrapper])
        estimator.transform = replacement
        return GPUPreprocessHandle(estimator, transform, replacement, converter, receipt)
    except BaseException:
        estimator.transform = transform
        converter.close()
        raise
