"""Bounded uint8 crop staging for the pinned SAM BODY multi-image path.

The official OpenCV crop and prepare_batch metadata remain authoritative. Only
the crop tensor upload/layout/float32 normalization is grouped across subjects.
Imports remain lazy so service startup does not load Torch or touch CUDA.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


MAX_SUBJECTS = 8
MAX_CROP_BYTES = 24 * 1024 * 1024


class _BatchUint8ToTensor:
    """One reusable pinned/GPU byte pool, with explicit copy/read dependencies.

    Model input floats own new storage on each call. Only compact uint8 staging
    is reused. CPU mode exercises arithmetic and storage contracts in tests;
    the public preprocessor requires CUDA.
    """

    def __init__(self, original, device, receipt, *, max_subjects=MAX_SUBJECTS):
        import torch

        if type(max_subjects) is not int or not 1 <= max_subjects <= MAX_SUBJECTS:
            raise ValueError("SAM crop staging requires 1..8 subject slots")
        self.torch, self.device, self.receipt = torch, torch.device(device), receipt
        self.max_subjects = max_subjects
        self._host = self._device_bytes = self._shape = None
        self._copy_done = self._read_done = None
        self._copy_stream = (torch.cuda.Stream(device=self.device)
                             if self.device.type == "cuda" else None)
        self.closed = False
        values = torch.arange(256, dtype=torch.uint8).reshape(16, 16, 1)
        reference = original(values.numpy()).flatten().contiguous()
        if reference.dtype != torch.float32:
            raise ValueError("SAM batch normalization requires the official float32 ToTensor default")
        self._reference = reference.to(self.device)
        candidate = values.flatten().to(self.device).float().div(255)
        self._direct_division_exact = bool(torch.equal(reference, candidate.cpu()))
        receipt.update(
            normalization_policy=("gpu_division_verified_all_256_values" if self._direct_division_exact
                                  else "gpu_lookup_of_official_cpu_float32_values"),
            uint8_normalization_all_values_verified=True,
            batch_calls=0, subjects=0, image_uploads=0, uploaded_image_bytes=0,
            staging_capacity_bytes=0, pinned_capacity_bytes=0,
            maximum_subjects=max_subjects, maximum_crop_bytes=MAX_CROP_BYTES,
            copy_stream="dedicated" if self._copy_stream is not None else "cpu_test",
            pinned_reuse_dependency="previous_H2D_completion_event_host_wait",
            device_reuse_dependency="previous_normalization_read_event_copy_stream_wait",
            compute_dependency="H2D_completion_event_compute_stream_wait",
            initial_allocation_dependency="compute_allocation_event_copy_stream_wait",
            model_input_storage_reused=False,
        )

    def __call__(self, crops):
        import numpy as np

        torch = self.torch
        if self.closed:
            raise RuntimeError("SAM crop staging is closed")
        if torch.get_default_dtype() != torch.float32:
            raise ValueError("SAM crop staging default dtype changed")
        if not isinstance(crops, (list, tuple)) or not 1 <= len(crops) <= self.max_subjects:
            raise ValueError("SAM crop staging subject count exceeds capacity")
        shape = tuple(crops[0].shape) if isinstance(crops[0], np.ndarray) else ()
        if (len(shape) != 3 or shape[2] != 3 or min(shape[:2]) < 1
                or self.max_subjects * int(np.prod(shape)) > MAX_CROP_BYTES):
            raise ValueError("SAM crop staging requires bounded nonempty HWC crops")
        if any(not isinstance(item, np.ndarray) or item.dtype != np.uint8 or tuple(item.shape) != shape
               for item in crops):
            raise ValueError("SAM crop staging requires equal-shape uint8 RGB crops")
        if self._shape is not None and shape != self._shape:
            raise ValueError("SAM crop dimensions changed; create a new staging owner")

        # A host buffer may be refilled only once the prior async H2D completed.
        # This wait is restricted to that copy, never a whole-device synchronize.
        if self._copy_done is not None:
            self._copy_done.synchronize()
            self._copy_done = None
        compute_stream = (torch.cuda.current_stream(self.device)
                          if self._copy_stream is not None else None)
        allocation_ready = None
        if self._host is None:
            allocation_shape = (self.max_subjects, *shape)
            host = torch.empty(allocation_shape, dtype=torch.uint8,
                               pin_memory=self._copy_stream is not None)
            device_bytes = torch.empty(allocation_shape, dtype=torch.uint8, device=self.device)
            # The caching allocator can reuse storage whose prior compute work
            # is still queued on this stream. A different stream must wait for
            # that history even though this newly allocated tensor is empty.
            if compute_stream is not None:
                allocation_ready = torch.cuda.Event()
                allocation_ready.record(compute_stream)
            self._host, self._device_bytes, self._shape = host, device_bytes, shape
            capacity = self.max_subjects * int(np.prod(shape))
            self.receipt.update(staging_capacity_bytes=capacity,
                                pinned_capacity_bytes=capacity if self._copy_stream is not None else 0)
        for index, pixels in enumerate(crops):
            self._host[index].copy_(torch.from_numpy(pixels))
        source, destination = self._host[:len(crops)], self._device_bytes[:len(crops)]
        if self._copy_stream is not None:
            with torch.cuda.stream(self._copy_stream):
                if allocation_ready is not None:
                    self._copy_stream.wait_event(allocation_ready)
                if self._read_done is not None:
                    self._copy_stream.wait_event(self._read_done)
                destination.copy_(source, non_blocking=True)
                self._copy_done = torch.cuda.Event()
                self._copy_done.record(self._copy_stream)
            compute_stream.wait_event(self._copy_done)
        else:
            destination.copy_(source)
        try:
            # Match ToTensor's CHW-contiguous -> float32 -> divide order. The
            # lookup fallback preserves every official CPU float32 value.
            nchw = destination.permute(0, 3, 1, 2).contiguous()
            result = (nchw.float().div(255) if self._direct_division_exact
                      else self._reference[nchw.long()])
        finally:
            if compute_stream is not None:
                self._read_done = torch.cuda.Event()
                self._read_done.record(compute_stream)
        self.receipt["batch_calls"] += 1
        self.receipt["subjects"] += len(crops)
        self.receipt["image_uploads"] += 1
        self.receipt["uploaded_image_bytes"] += len(crops) * int(np.prod(shape))
        self.receipt["last_batch_subjects"] = len(crops)
        self.receipt["last_image_uploads"] = 1
        return result

    def close(self):
        if self.closed:
            return
        # The last model input has independent float storage; only staging
        # copies and reads must finish before these compact pools are released.
        if self._copy_done is not None:
            self._copy_done.synchronize()
        if self._read_done is not None:
            self._read_done.synchronize()
        self._copy_done = self._read_done = self._copy_stream = None
        self._host = self._device_bytes = self._reference = None
        self.closed = True


class BatchCropPreprocessor:
    """Own a separate transform; never replace the estimator's active transform.

    Official prepare_batch performs all metadata construction with a zero-pixel
    CHW placeholder. The placeholder is replaced with real grouped crop tensors
    before model initialization. No placeholder can be returned as model input.
    """

    def __init__(self, estimator):
        import torch
        from torchvision.transforms import ToTensor
        from rallymate_vision.sam3d_gpu_preprocess import (
            OFFICIAL_TRANSFORMS_SHA256, _DeviceToTensor, _source_sha256,
        )

        if estimator.model.training or torch.device(estimator.device).type != "cuda":
            raise ValueError("SAM batch crop preprocessing requires a CUDA evaluation model")
        transform = estimator.transform
        items = getattr(transform, "transforms", ())
        if (type(transform).__name__ != "Compose" or _source_sha256(type(transform)) != OFFICIAL_TRANSFORMS_SHA256
                or len(items) != 3 or type(items[0]).__name__ != "GetBBoxCenterScale"
                or type(items[1]).__name__ != "TopdownAffine" or type(items[2]).__name__ != "VisionTransformWrapper"
                or any(_source_sha256(type(item)) != OFFICIAL_TRANSFORMS_SHA256 for item in items)):
            raise ValueError("SAM batch crop preprocessing requires the pinned official transform")
        original = items[2].transform
        if isinstance(original, _DeviceToTensor):
            original = original.original
        if type(original) is not ToTensor:
            raise ValueError("SAM batch crop preprocessing requires the official ToTensor")
        self.torch, self.estimator, self.original_transform = torch, estimator, transform
        self.crops = []
        self.receipt = {
            "candidate": "official_crop_batched_uint8_staging_v1",
            "candidate_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "official_transforms_sha256": OFFICIAL_TRANSFORMS_SHA256,
            "opencv_crop_unchanged": True, "prepare_batch_metadata_unchanged": True,
            "parameters_precision_resolution_and_outputs_unchanged": True,
            "estimator_transform_replaced": False,
            "scope": "batch_body_crop_tensor_upload_only; not_GPU_affine_or_video_decode",
        }
        self.converter = _BatchUint8ToTensor(original, estimator.device, self.receipt)
        self.transform = type(transform)([items[0], items[1], type(items[2])(self._collect)])

    def _collect(self, pixels):
        import numpy as np

        if (not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8
                or pixels.ndim != 3 or pixels.shape[2] != 3):
            raise ValueError("Official SAM crop did not produce uint8 RGB")
        if len(self.crops) >= MAX_SUBJECTS:
            raise ValueError("SAM batch crop count exceeded staging capacity")
        self.crops.append(np.ascontiguousarray(pixels))
        return self.torch.empty((3, 0, 0), dtype=self.torch.float32)

    def prepare(self, requests, prepare_batch, merge_batches):
        import numpy as np

        if self.converter.closed:
            raise RuntimeError("SAM batch crop preprocessing is closed")
        if self.estimator.transform is not self.original_transform:
            raise ValueError("SAM transform changed while batch staging was installed")
        if not 1 <= len(requests) <= MAX_SUBJECTS:
            raise ValueError("SAM batch crop count must be 1..8")
        self.crops = []
        prepared, first_rgb = [], None
        try:
            for frame, box in requests:
                rgb = np.ascontiguousarray(frame[:, :, ::-1])
                if first_rgb is None:
                    first_rgb = rgb
                prepared.append(prepare_batch(rgb, self.transform, np.asarray(box).reshape(1, 4), None, None))
            batch = merge_batches(prepared, self.torch)
            if (tuple(batch["img"].shape) != (len(requests), 1, 3, 0, 0)
                    or len(self.crops) != len(requests)):
                raise ValueError("Official SAM crop placeholders do not match source subjects")
            batch["img"] = self.converter(self.crops).unsqueeze(1)
            if (batch["img"].ndim != 5 or tuple(batch["img"].shape[:3]) != (len(requests), 1, 3)
                    or min(batch["img"].shape[-2:]) < 1 or batch["img"].dtype != self.torch.float32):
                raise ValueError("SAM batch crop model tensor is invalid")
            return first_rgb, batch
        finally:
            self.crops = []

    def close(self):
        self.converter.close()
        self.crops = []
