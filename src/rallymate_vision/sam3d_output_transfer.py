"""Opt-in, instance-local batching of the official estimator's complete D2H tree.

Every dict/list leaf still reaches the CPU. The official NumPy conversion and
output selection remain unchanged. No model/CUDA imports occur at import time.
"""
from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
import types


OFFICIAL_ESTIMATOR_SHA256 = "9690d55273db4a8e56660407a2366cc398b09b98a7f9d4fcf7772d13497c04d8"
OFFICIAL_RECURSIVE_TO_SHA256 = "f991fb070b1f5d653e5fec8d048f20ccaa37e2cda1a43deea8b83dd3c0499b77"
MAX_PINNED_BYTES_PER_CALL = 256 * 1024 * 1024
MAX_CUDA_LEAVES_PER_CALL = 4096


def _source_sha256(target):
    return hashlib.sha256(Path(inspect.getsourcefile(target)).read_bytes()).hexdigest()


def _map_official_tree(value, tensor_type, convert):
    """Match pinned recursive_to: dict/list recurse, tuples/other leaves do not."""
    if isinstance(value, dict):
        return {key: _map_official_tree(item, tensor_type, convert) for key, item in value.items()}
    if isinstance(value, tensor_type):
        return convert(value)
    if isinstance(value, list):
        return [_map_official_tree(item, tensor_type, convert) for item in value]
    return value


def _allocate_host_like(torch, source, *, pin_memory=True):
    # Tensor.to's default memory format is preserve_format. empty_like has the
    # same dense-layout preservation/otherwise-contiguous allocation contract.
    return torch.empty_like(source, device="cpu", pin_memory=pin_memory,
                            memory_format=torch.preserve_format)


class _BatchedCPUTransfer:
    def __init__(self, original, torch, device, receipt):
        self.original, self.torch, self.device = original, torch, torch.device(device)
        self.receipt = receipt
        self._pending = None
        self._closed = False
        self._failed = False

    def _fallback(self, value, target, reason):
        self.receipt["original_fallback_calls"] += 1
        self.receipt["last_fallback_reason"] = reason
        return self.original(value, target)

    def __call__(self, value, target):
        if self._closed or self._failed:
            raise RuntimeError("SAM output transfer is closed or has an unresolved transfer failure")
        # Preserve the original CUDA and NumPy paths exactly, including their
        # target comparison semantics and treatment of opaque tuple leaves.
        if target != "cpu":
            return self.original(value, target)
        torch = self.torch
        if torch.is_grad_enabled():
            return self._fallback(value, target, "grad_enabled")
        sources = []
        unsupported = []

        def inspect_leaf(tensor):
            if type(tensor) is not torch.Tensor:
                unsupported.append("tensor_subclass")
            elif tensor.device.type == "cpu":
                pass  # The official .to('cpu') returns the same CPU tensor.
            elif (tensor.device != self.device or tensor.layout != torch.strided
                  or tensor.is_quantized or tensor.is_conj() or tensor.is_neg()):
                unsupported.append("unsupported_device_layout_or_view_bits")
            else:
                sources.append(tensor)
            return tensor

        _map_official_tree(value, torch.Tensor, inspect_leaf)
        count = len(sources)
        size = sum(source.numel() * source.element_size() for source in sources)
        if unsupported:
            return self._fallback(value, target, unsupported[0])
        if count > MAX_CUDA_LEAVES_PER_CALL or size > MAX_PINNED_BYTES_PER_CALL:
            return self._fallback(value, target, "bounded_host_allocation_exceeded")
        if not sources:
            self.receipt["cpu_only_calls"] += 1
            return self.original(value, target)
        jobs = []

        def allocate_leaf(source):
            if source.device.type == "cpu":
                return source
            destination = _allocate_host_like(torch, source)
            # Allocate each occurrence independently, matching repeated .to
            # calls. Returned CPU/NumPy storage is never reused by this handle.
            jobs.append((destination, source))
            return destination

        try:
            result = _map_official_tree(value, torch.Tensor, allocate_leaf)
        except (RuntimeError, MemoryError):
            # Allocation occurs before any transfer is queued, so fallback
            # cannot release or expose an in-flight destination.
            return self._fallback(value, target, "host_allocation_failed")
        with torch.cuda.device(self.device):
            stream = torch.cuda.current_stream(self.device)
            event = torch.cuda.Event(blocking=True)
            self._pending = (stream, jobs, result)
            try:
                for destination, source in jobs:
                    destination.copy_(source, non_blocking=True)
                event.record(stream)
                # No CPU/NumPy access is possible until all copies on this
                # producer/current stream have completed. Blocking avoids a
                # new host polling wait even when CUDA uses blocking sync.
                event.synchronize()
            except BaseException:
                self.receipt["transfer_error_calls"] += 1
                self._failed = True
                try:
                    stream.synchronize()
                except BaseException:
                    # Keep both source and destination references alive if
                    # completion could not be established; close retries.
                    pass
                else:
                    self._pending = None
                raise
            self._pending = None
        self.receipt["optimized_calls"] += 1
        self.receipt["completion_waits"] += 1
        self.receipt["copied_tensor_leaves"] += count
        self.receipt["copied_tensor_bytes"] += size
        self.receipt["last_copied_tensor_leaves"] = count
        self.receipt["last_copied_tensor_bytes"] = size
        return result

    def close(self):
        if self._pending is not None:
            self._pending[0].synchronize()
            self._pending = None
        self._closed = True


class OutputTransferHandle:
    def __init__(self, estimator, replacement, converter, receipt):
        self.estimator, self.replacement, self.converter = estimator, replacement, converter
        self.receipt, self._restored = receipt, False

    def restore(self):
        if self._restored:
            return
        if self.estimator.process_one_image is not self.replacement:
            raise RuntimeError("SAM estimator method changed while output transfer was installed")
        self.converter.close()
        delattr(self.estimator, "process_one_image")
        self._restored = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.restore()


def apply_output_transfer(estimator, *, inference_type="body"):
    """Opt-in complete-tree D2H batching; caller must explicitly install it.

    Profiling/cache-reuse wrappers currently patch official module globals;
    reject those combinations rather than silently bypass their behavior.
    torch.profiler around the inference call remains supported.
    """
    import torch

    if inference_type != "body" or estimator.model.training:
        raise ValueError("Output transfer requires eval-mode body inference")
    device = torch.device(estimator.device)
    if device.type != "cuda" or device.index not in (None, 0):
        raise ValueError("Output transfer is validated only for CUDA device 0")
    device = torch.device("cuda:0")
    if (os.environ.get("RALLYMATE_SAM3D_PROFILE_STAGES", "0") != "0"
            or os.environ.get("RALLYMATE_SAM3D_REUSE_CUDA_CACHE", "0") != "0"):
        raise ValueError("Output transfer cannot bypass scoped stage/cache wrappers")
    if "process_one_image" in vars(estimator):
        raise ValueError("Estimator already has an instance method replacement")
    function = inspect.unwrap(estimator.process_one_image)
    recursive = function.__globals__.get("recursive_to")
    if (_source_sha256(type(estimator)) != OFFICIAL_ESTIMATOR_SHA256
            or not inspect.isfunction(recursive)
            or _source_sha256(recursive) != OFFICIAL_RECURSIVE_TO_SHA256):
        raise ValueError("Official estimator/recursive_to source identity differs")
    receipt = {
        "candidate": "pinned_all_tensor_d2h_single_wait_v1",
        "candidate_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "official_estimator_sha256": OFFICIAL_ESTIMATOR_SHA256,
        "official_recursive_to_sha256": OFFICIAL_RECURSIVE_TO_SHA256,
        "scope": "official_estimator_recursive_to_cpu_complete_dict_list_tree",
        "device": str(device), "all_tensor_leaves_retained": True,
        "official_numpy_conversion_unchanged": True,
        "official_output_selection_unchanged": True,
        "model_parameters_precision_and_all_heads_unchanged": True,
        "package_globals_or_source_changed": False,
        "fresh_host_storage_each_tensor_occurrence": True,
        "returned_host_storage_reused": False,
        "memory_format": "torch.preserve_format",
        "completion_policy": "one_current_stream_blocking_event_before_cpu_access",
        "maximum_pinned_bytes_per_call": MAX_PINNED_BYTES_PER_CALL,
        "maximum_cuda_tensor_leaves_per_call": MAX_CUDA_LEAVES_PER_CALL,
        "optimized_calls": 0, "completion_waits": 0, "copied_tensor_leaves": 0,
        "copied_tensor_bytes": 0, "last_copied_tensor_leaves": 0,
        "last_copied_tensor_bytes": 0, "cpu_only_calls": 0,
        "original_fallback_calls": 0, "last_fallback_reason": None,
        "transfer_error_calls": 0,
    }
    converter = _BatchedCPUTransfer(recursive, torch, device, receipt)
    globals_copy = dict(function.__globals__, recursive_to=converter)
    cloned = types.FunctionType(function.__code__, globals_copy, function.__name__,
                                function.__defaults__, function.__closure__)
    cloned.__kwdefaults__ = function.__kwdefaults__
    cloned.__annotations__ = dict(function.__annotations__)
    cloned.__qualname__ = function.__qualname__
    replacement = types.MethodType(torch.no_grad()(cloned), estimator)
    estimator.process_one_image = replacement
    return OutputTransferHandle(estimator, replacement, converter, receipt)
