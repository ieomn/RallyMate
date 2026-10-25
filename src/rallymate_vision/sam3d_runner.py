"""Real SAM 3D execution, optionally hosted in an isolated persistent process.

The wire format contains source pixels and bbox requests, never rendered meshes
or substitute-model predictions. The model does not supply joint confidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

from rallymate_vision.pose.metadata import MHR70_NAMES, sha256_file
from rallymate_vision.sam3d_cpu import configure_sam_cpu_pools, prepare_sam_worker_environment
from rallymate_vision.sam3d_ipc import (
    SharedFrameReader, SharedFrameWriter, configured_transport, transport_metadata,
)
from rallymate_vision.owned_subprocess import OwnedProcess
from rallymate_vision.sam3d_cuda_wait import configure_sam_cuda_wait, verify_sam_cuda_wait


SAM_SOURCE_COMMIT = "b5c765a0d89d789985e186d396315e7590887b94"
CHECKPOINT_HASHES = {
    "b5a2f9d305dd02626b967aa2e86021fba07065df66ce7a7e00ffb9664f150abf": "dinov3",
    "3b1cb897f4bbd977bf81cbb0b30780a9582681ac642ee112865790ceb4d66056": "vith",
}
MHR_HASH = "352e271a6c42729c68554ceaea0c955e866970160c31e35506d782dc0f7377bc"


def configure_runtime_optimizations(model, *, inference_type, prompt_enabled, rotation_enabled,
                                    graph_enabled=False, forward_graph_scope="off",
                                    gpu_preprocess_enabled=False, output_transfer_enabled=False,
                                    decoder_graph_enabled=False, estimator=None):
    """Bind validated instance optimizations; other inference modes stay official.

    Loading dependencies is deliberately lazy. A partially applied setup is
    restored if any source identity or model contract check fails.
    """
    if forward_graph_scope not in {"off", "backbone", "backbone_decoder_layers"}:
        raise ValueError("Unsupported SAM forward graph scope")
    if decoder_graph_enabled and inference_type == "body" and not (graph_enabled and rotation_enabled):
        raise ValueError("Complete decoder graph requires the validated body-head graph and rotation binding")
    handles, metadata = [], {}
    settings = [
        ("prompt_vectorization", prompt_enabled, "sam3d_prompt_optimization", "apply_prompt_vectorized"),
        ("rotation_vectorization", rotation_enabled, "sam3d_roma_optimization", "apply_roma_fixedshape"),
    ]
    if graph_enabled:
        settings.append(("body_head_cuda_graph", graph_enabled and rotation_enabled,
                         "sam3d_cuda_graph", "apply_head_cuda_graph"))
    try:
        import importlib
        for name, requested, module_name, function_name in settings:
            metadata[name] = {"enabled": False, "requested": bool(requested),
                              "scope": "body_inference_only"}
            if not requested or inference_type != "body":
                continue
            module = importlib.import_module("rallymate_vision." + module_name)
            handle = getattr(module, function_name)(model)
            handles.append(handle)
            metadata[name].update(handle.receipt, enabled=True)
        if forward_graph_scope != "off" and inference_type == "body":
            module = importlib.import_module("rallymate_vision.sam3d_forward_graph")
            handle = module.apply_forward_cuda_graph(model, scope=forward_graph_scope)
            handles.append(handle)
            metadata["forward_cuda_graph"] = {**handle.receipt, "enabled": True}
        if gpu_preprocess_enabled and inference_type == "body":
            if estimator is None:
                raise ValueError("GPU preprocessing requires the current SAM estimator")
            module = importlib.import_module("rallymate_vision.sam3d_gpu_preprocess")
            handle = module.apply_gpu_preprocess(estimator, inference_type=inference_type)
            handles.append(handle)
            metadata["gpu_preprocess"] = {**handle.receipt, "enabled": True}
        if output_transfer_enabled and inference_type == "body":
            if estimator is None:
                raise ValueError("Batched output transfer requires the current SAM estimator")
            module = importlib.import_module("rallymate_vision.sam3d_output_transfer")
            handle = module.apply_output_transfer(estimator, inference_type=inference_type)
            handles.append(handle)
            metadata["output_transfer"] = {**handle.receipt, "enabled": True}
        if decoder_graph_enabled and inference_type == "body":
            head_handle = next(handle for handle in handles
                               if handle.receipt.get("candidate") == "single_person_body_head_cuda_graph_v1")
            forward_handle = next((handle for handle in handles
                                   if handle.receipt.get("candidate") == "pinned_backbone_decoder_layers_cuda_graph_v1"), None)
            module = importlib.import_module("rallymate_vision.sam3d_decoder_graph")
            handle = module.apply_decoder_cuda_graph(model, head_handle=head_handle,
                                                    forward_handle=forward_handle)
            handles.append(handle)
            # Earlier handles remain installed for the original eager fallback.
            # This receipt explicitly identifies which graphs it supersedes.
            metadata["decoder_cuda_graph"] = {**handle.receipt, "enabled": True}
        return handles, metadata
    except BaseException:
        for handle in reversed(handles):
            try:
                handle.restore()
            except BaseException:
                # Keep the original setup error after attempting every restore.
                pass
        raise


class _AttributeOverrides:
    """Delegate module access without mutating the original torch module."""

    def __init__(self, target, **overrides):
        self._target, self._overrides = target, overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._target, name)


class EstimatorStageTiming:
    """Scoped instrumentation of the pinned estimator in its dedicated worker.

    Wall times retain real blocking/CPU work. CUDA event spans include idle time
    between enqueue operations and are not a GPU-utilization measurement. No
    additional per-stage synchronize is introduced. Patches are always restored.
    """

    def __init__(self, estimator, torch, *, enabled: bool, reuse_cuda_cache: bool):
        self.estimator, self.torch = estimator, torch
        self.enabled, self.reuse_cuda_cache = enabled, reuse_cuda_cache
        self.wall_ms, self.calls, self.events = Counter(), Counter(), []
        self.restores = []

    def _patch(self, owner, name, replacement):
        original = getattr(owner, name)
        # Restoring an inherited bound method as an instance attribute changes
        # normal attribute lookup. Keep its prior instance-presence as well.
        had_own = name in vars(owner)
        setattr(owner, name, replacement)
        self.restores.append((owner, name, original, had_own))

    def _wrapper(self, name, function, *, cuda=False):
        def measured(*args, **kwargs):
            if not self.enabled:
                return function(*args, **kwargs)
            before = self.torch.cuda.Event(enable_timing=True) if cuda else None
            after = self.torch.cuda.Event(enable_timing=True) if cuda else None
            if before is not None:
                before.record()
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.wall_ms[name] += (time.perf_counter() - started) * 1000
                self.calls[name] += 1
                if after is not None:
                    after.record()
                    self.events.append((name, before, after))
        return measured

    def __enter__(self):
        if not self.enabled and not self.reuse_cuda_cache:
            return self
        if os.environ.get("RALLYMATE_SAM3D_ISOLATED_WORKER") != "1":
            raise RuntimeError("SAM stage profiling/cache reuse requires the isolated SAM worker")
        module = sys.modules[type(self.estimator).__module__]
        try:
            original_empty = self.torch.cuda.empty_cache
            empty = self._wrapper("allocator_cache_release",
                                  (lambda: None) if self.reuse_cuda_cache else original_empty)
            # Only the official estimator's module reference is shadowed.
            # Real torch.cuda.empty_cache and close() remain untouched, even
            # inside this dedicated single-model process.
            cuda_view = _AttributeOverrides(self.torch.cuda, empty_cache=empty)
            self._patch(module, "torch", _AttributeOverrides(self.torch, cuda=cuda_view))
            if self.enabled:
                self._patch(module, "prepare_batch", self._wrapper("crop_preprocess", module.prepare_batch))
                recursive = module.recursive_to

                def transfer(data, destination, *args, **kwargs):
                    name = {"cuda": "host_to_device", "cpu": "device_to_host", "numpy": "tensor_to_numpy"}.get(str(destination), "other_transfer")
                    return self._wrapper(name, recursive, cuda=str(destination) in {"cuda", "cpu"})(data, destination, *args, **kwargs)

                self._patch(module, "recursive_to", transfer)
                for name, label in (("_initialize_batch", "initialize_batch"), ("run_inference", "model_forward")):
                    self._patch(self.estimator.model, name, self._wrapper(label, getattr(self.estimator.model, name), cuda=True))
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_exc):
        for owner, name, original, had_own in reversed(self.restores):
            if had_own:
                setattr(owner, name, original)
            else:
                delattr(owner, name)
        self.restores.clear()

    def report(self):
        # Caller already synchronizes after the request, before reading events.
        device_ms = Counter()
        for name, before, after in self.events:
            device_ms[name] += before.elapsed_time(after)
        return {"wall_ms": dict(self.wall_ms), "cuda_stream_span_ms": dict(device_ms),
                "calls": dict(self.calls), "extra_stage_synchronizations": 0,
                "cuda_cache_policy": "reuse_between_frames" if self.reuse_cuda_cache else "official_clear_each_call"}


class SourcePTSDecoder:
    """OpenCV-like read interface that also retains unrounded decoder PTS."""

    def __init__(self, path: Path):
        try:
            import av
        except ImportError as exc:
            raise RuntimeError("SAM source-bound decoding requires the PyAV 'av' package") from exc
        self.container = av.open(str(path))
        self.stream = self.container.streams.video[0]
        self.frames = iter(self.container.decode(self.stream))
        self.binding: dict = {}
        self.timing_seconds = Counter()

    def read(self):
        started = time.perf_counter()
        try:
            frame = next(self.frames)
        except StopIteration:
            return False, None
        finally:
            self.timing_seconds["decode_seconds"] += time.perf_counter() - started
        tb = frame.time_base
        self.binding = {
            "pts": frame.pts,
            "time_base_num": tb.numerator if tb is not None else None,
            "time_base_den": tb.denominator if tb is not None else None,
            "timestamp_ms": round(frame.pts * tb * 1000) if frame.pts is not None and tb is not None else None,
        }
        started = time.perf_counter()
        pixels = frame.to_ndarray(format="bgr24")
        self.timing_seconds["decode_color_conversion_seconds"] += time.perf_counter() - started
        return True, pixels

    def get(self, _property):
        timestamp = self.binding.get("timestamp_ms")
        return timestamp if timestamp is not None else float("nan")

    def release(self):
        self.container.close()


class PrefetchSourcePTSDecoder(SourcePTSDecoder):
    """Bounded CPU prefetch retaining the exact pixels, PTS and source order.

    Only the producer touches PyAV; the consumer owns the currently exposed
    binding. Queue depth does not change stride or request count. This remains
    opt-in until same-input end-to-end measurements establish a benefit.
    """

    def __init__(self, path: Path, *, depth: int):
        if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 8:
            raise ValueError("SAM source prefetch depth must be an integer from 1 to 8")
        self._decoder = SourcePTSDecoder(path)
        self.prefetch_depth = depth
        self._queue = queue.Queue(maxsize=depth)
        self._stop = threading.Event()
        self._eof = False
        self.binding = {}
        self.timing_seconds = self._decoder.timing_seconds
        self.produced_frames = self.consumed_frames = 0
        self._thread = threading.Thread(target=self._produce, name="sam-source-prefetch", daemon=True)
        self._thread.start()

    def _put(self, item):
        while not self._stop.is_set():
            try:
                self._queue.put(item, timeout=.05)
                return
            except queue.Full:
                continue

    def _produce(self):
        try:
            while not self._stop.is_set():
                ok, frame = self._decoder.read()
                if not ok:
                    self._put((False, None, {}, None))
                    return
                self.produced_frames += 1
                self._put((True, frame, dict(self._decoder.binding), None))
        except Exception as exc:
            self._put((False, None, {}, exc))
        finally:
            self._decoder.release()

    def read(self):
        if self._eof or self._stop.is_set():
            return False, None
        while not self._stop.is_set():
            try:
                ok, frame, binding, error = self._queue.get(timeout=.05)
                break
            except queue.Empty:
                if not self._thread.is_alive():
                    raise RuntimeError("SAM source prefetch ended without an EOF record")
        else:
            return False, None
        if error is not None:
            self._eof = True
            raise error
        if not ok:
            self._eof = True
            return False, None
        self.binding = binding
        self.consumed_frames += 1
        return True, frame

    def release(self):
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("SAM source decoder did not stop within 5 seconds")
        # Release queued frames promptly after early cancellation/max_frames.
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


class Sam3DEngine:
    def __init__(self, checkpoint: Path, *, runtime_dir: Path, device: str = "cuda",
                 inference_type: str = "body"):
        self.torch = None
        self.estimator = None
        self.optimization_handles = []
        if inference_type not in {"body", "full"}:
            raise ValueError("SAM inference_type must be body or full")
        checkpoint, runtime_dir = Path(checkpoint).resolve(), Path(runtime_dir).resolve()
        self.runtime_dir = runtime_dir
        self.mesh_export_enabled = os.environ.get("RALLYMATE_SAM3D_EXPORT_MESH", "1") == "1"
        mhr_path = checkpoint.parent / "assets" / "mhr_model.pt"
        config_path = checkpoint.parent / "model_config.yaml"
        for path in (checkpoint, mhr_path, config_path):
            if not path.is_file():
                raise FileNotFoundError(f"SAM model asset is missing: {path}")
        checkpoint_hash = sha256_file(checkpoint).lower()
        if checkpoint_hash not in CHECKPOINT_HASHES or sha256_file(mhr_path).lower() != MHR_HASH:
            raise ValueError("SAM checkpoint/MHR bytes do not match the pinned official weights")
        sys.path.insert(0, str(runtime_dir / "repo"))
        os.environ.setdefault("MOMENTUM_ENABLED", "0")
        os.environ.setdefault("SAM3D_DINOV3_REPO", str(runtime_dir / "dinov3"))
        try:
            # Official model initialization can print progress; stdout is IPC only.
            with contextlib.redirect_stdout(sys.stderr):
                import torch
                self.torch = torch
                requested = f"cuda:{device}" if str(device).isdigit() else str(device)
                if requested == "cuda":
                    requested = "cuda:0"
                # Configure host waiting before any model package can activate
                # this worker's primary CUDA context. The default is a no-op.
                cuda_wait_policy = configure_sam_cuda_wait(requested)
                from sam_3d_body import SAM3DBodyEstimator, load_sam_3d_body
                from sam_3d_body.metadata.mhr70 import pose_info

                official_names = tuple(pose_info["original_keypoint_info"][index] for index in range(70))
                if official_names != MHR70_NAMES:
                    raise RuntimeError("Installed SAM native joint order differs from pinned MHR70 mapping")
                if requested == "cpu" or not torch.cuda.is_available():
                    raise RuntimeError("Pinned official SAM estimator requires a CUDA device")
                torch.cuda.set_device(torch.device(requested))
                started = time.perf_counter()
                model, config = load_sam_3d_body(checkpoint_path=str(checkpoint),
                                               mhr_path=str(mhr_path), device=requested)
                self.estimator = SAM3DBodyEstimator(model, config)
                torch.cuda.synchronize()
                load_seconds = time.perf_counter() - started
                self.optimization_handles, optimization_metadata = configure_runtime_optimizations(
                    model, inference_type=inference_type,
                    prompt_enabled=os.environ.get("RALLYMATE_SAM3D_VECTORIZE_PROMPTS", "1") == "1",
                    rotation_enabled=os.environ.get("RALLYMATE_SAM3D_VECTORIZE_ROTATIONS", "1") == "1",
                    graph_enabled=os.environ.get("RALLYMATE_SAM3D_CUDA_GRAPH", "1") == "1",
                    forward_graph_scope=os.environ.get("RALLYMATE_SAM3D_FORWARD_GRAPH", "off"),
                    gpu_preprocess_enabled=os.environ.get("RALLYMATE_SAM3D_GPU_PREPROCESS", "0") == "1",
                    output_transfer_enabled=os.environ.get("RALLYMATE_SAM3D_BATCH_OUTPUT_TRANSFER", "0") == "1",
                    decoder_graph_enabled=os.environ.get("RALLYMATE_SAM3D_COMPLETE_DECODER_GRAPH", "0") == "1",
                    estimator=self.estimator,
                )
                torch.cuda.synchronize()
                verify_sam_cuda_wait(cuda_wait_policy)
            self.torch, self.inference_type = torch, inference_type
            self.profile_stages = os.environ.get("RALLYMATE_SAM3D_PROFILE_STAGES", "0") == "1"
            self.reuse_cuda_cache = os.environ.get("RALLYMATE_SAM3D_REUSE_CUDA_CACHE", "0") == "1"
            self.inference_calls = 0
            self.metadata = {
                "backend": "sam3d", "runtime": "pytorch", "model_path": str(checkpoint),
                "model_sha256": checkpoint_hash.upper(), "config_path": str(config_path),
                "config_sha256": sha256_file(config_path), "mhr_sha256": MHR_HASH,
                "model_variant": CHECKPOINT_HASHES[checkpoint_hash], "source_commit": SAM_SOURCE_COMMIT,
                "load_seconds": load_seconds,
                "load_timing_scope": "model_load_and_estimator_with_final_cuda_sync_excludes_import_hash_and_device_initialization",
                "inference_timing_scope": "per_subject_wall_includes_bgr_conversion_estimator_completion_sync_and_output_validation_excludes_prior_sync_model_load_IPC_and_timing_report",
                "cold_call_semantics": "first_model_call_is_first_subject_after_model_load_no_separate_warmup",
                "input_size": list(config.MODEL.IMAGE_SIZE),
                "inference_type": inference_type, "native_keypoint_format": "mhr70",
                "native_keypoint_count": 70, "confidence_semantics": "not_provided_by_sam3d",
                "camera_intrinsics": "official_default_fov_uncalibrated",
                "mesh_export_enabled": self.mesh_export_enabled,
                "units_3d": "model_units", "world_metric_calibration": False,
                "profile_stages": self.profile_stages,
                "cuda_graph_requested": os.environ.get("RALLYMATE_SAM3D_CUDA_GRAPH", "1") == "1",
                "cuda_cache_policy": "reuse_between_frames" if self.reuse_cuda_cache else "official_clear_each_call",
                "cpu_threads": {"torch_intraop": torch.get_num_threads(),
                                "torch_interop": torch.get_num_interop_threads(),
                                "logical_cpu_count": os.cpu_count()},
                **optimization_metadata,
            }
            if cuda_wait_policy is not None:
                self.metadata["cuda_wait_policy"] = cuda_wait_policy
        except BaseException:
            try:
                self.close()
            except BaseException:
                # Cleanup is best effort; do not replace the initialization error.
                pass
            raise


    def infer(self, frame_bgr: np.ndarray, boxes: list[list[float]]) -> list[dict]:
        if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3 or frame_bgr.dtype != np.uint8:
            raise ValueError("SAM requires exact uint8 source BGR pixels")
        results = []
        # One requested identity per call keeps bbox-to-output binding explicit
        # and records failures individually instead of dropping an entire batch.
        for box in boxes:
            stage_ms = {}
            wait_started = time.perf_counter()
            self.torch.cuda.synchronize()
            stage_ms["prior_cuda_wait"] = (time.perf_counter() - wait_started) * 1000
            self.torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            result = {"status": "failed", "reason": None,
                      "first_model_call": self.inference_calls == 0}
            self.inference_calls += 1
            profiler = EstimatorStageTiming(self.estimator, self.torch,
                                           enabled=self.profile_stages, reuse_cuda_cache=self.reuse_cuda_cache)
            try:
                preprocess_started = time.perf_counter()
                rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
                stage_ms["source_bgr_to_rgb"] = (time.perf_counter() - preprocess_started) * 1000
                model_started = time.perf_counter()
                with contextlib.redirect_stdout(sys.stderr), profiler:
                    outputs = self.estimator.process_one_image(
                        rgb,
                        bboxes=np.asarray([box], dtype=np.float32),
                        inference_type=self.inference_type,
                    )
                stage_ms["estimator_call"] = (time.perf_counter() - model_started) * 1000
                wait_started = time.perf_counter()
                self.torch.cuda.synchronize()
                stage_ms["completion_cuda_wait"] = (time.perf_counter() - wait_started) * 1000
                validation_started = time.perf_counter()
                if len(outputs) != 1:
                    raise ValueError("SAM returned a different number of subjects than requested")
                output = outputs[0]
                xy = np.asarray(output["pred_keypoints_2d"])
                xyz = np.asarray(output["pred_keypoints_3d"])
                if xy.shape != (70, 2) or xyz.shape != (70, 3):
                    raise ValueError(f"SAM returned unsupported point dimensions {xy.shape}/{xyz.shape}")
                if not np.isfinite(xy).all() or not np.isfinite(xyz).all():
                    raise ValueError("SAM returned non-finite native geometry")
                result.update(status="ok", keypoints_2d=xy.tolist(), keypoints_3d=xyz.tolist(),
                              pred_cam_t=np.asarray(output["pred_cam_t"]).tolist(),
                              focal_length=np.asarray(output["focal_length"]).tolist())
                if self.mesh_export_enabled:
                    from rallymate_vision.sam3d_mesh import capture_mesh_output
                    result["mesh_artifact"] = capture_mesh_output(
                        output, self.estimator.faces, self.runtime_dir)
                stage_ms["output_validation_and_serialization"] = (time.perf_counter() - validation_started) * 1000
            except Exception as exc:
                # Type and a stable code suffice for public reports; raw errors
                # remain on the local worker stderr and never contain credentials.
                result.update(status="failed", reason="sam_inference_error", error_type=type(exc).__name__)
                print(f"SAM frame failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            finally:
                wait_started = time.perf_counter()
                self.torch.cuda.synchronize()
                stage_ms["final_cuda_wait"] = (time.perf_counter() - wait_started) * 1000
                result.update(inference_ms=(time.perf_counter() - started) * 1000,
                              peak_memory_bytes=self.torch.cuda.max_memory_allocated(),
                              peak_reserved_memory_bytes=self.torch.cuda.max_memory_reserved(),
                              current_allocated_memory_bytes=self.torch.cuda.memory_allocated(),
                              current_reserved_memory_bytes=self.torch.cuda.memory_reserved(),
                              memory_scope="current_process_cuda_allocated_absolute_peak_includes_other_loaded_models",
                              inference_type=self.inference_type, stage_timing_ms=stage_ms)
                if self.profile_stages or self.reuse_cuda_cache:
                    result["estimator_stage_timing"] = profiler.report()
                for handle in self.optimization_handles:
                    if handle.receipt.get("candidate") == "single_person_body_head_cuda_graph_v1":
                        result["cuda_graph"] = {
                            key: handle.receipt.get(key) for key in
                            ("capture_status", "replays", "fallback_calls", "capture_seconds",
                             "capture_error_type")
                        }
                    elif handle.receipt.get("candidate") == "pinned_backbone_decoder_layers_cuda_graph_v1":
                        result["forward_cuda_graph"] = {
                            "scope": handle.receipt["scope"],
                            "modules": [dict(item) for item in handle.receipt["modules"]],
                        }
                    elif "optimized_calls" in handle.receipt and "uploaded_image_bytes" in handle.receipt:
                        result["gpu_preprocess"] = {
                            key: handle.receipt.get(key) for key in
                            ("optimized_calls", "original_fallback_calls", "uploaded_image_bytes")
                        }
                    elif handle.receipt.get("candidate") == "pinned_all_tensor_d2h_single_wait_v1":
                        result["output_transfer"] = {
                            key: handle.receipt.get(key) for key in
                            ("optimized_calls", "completion_waits", "copied_tensor_leaves", "copied_tensor_bytes",
                             "last_copied_tensor_leaves", "last_copied_tensor_bytes", "original_fallback_calls",
                             "transfer_error_calls")
                        }
                    elif handle.receipt.get("candidate") == "complete_body_decoder_cuda_graph_v1":
                        result["decoder_cuda_graph"] = {
                            key: handle.receipt.get(key) for key in
                            ("capture_status", "replays", "fallback_calls", "capture_seconds",
                             "captured_body_heads", "body_head_executions", "captured_operations_per_replay",
                             "decoder_layer_executions", "keypoint_2d_update_executions",
                             "keypoint_3d_update_executions", "prompt_encoder_calls",
                             "superseded_during_successful_replay", "capture_error_type")
                        }
            results.append(result)
        return results

    def close(self):
        handles = getattr(self, "optimization_handles", [])
        self.optimization_handles = []
        failure = None
        for handle in reversed(handles):
            try:
                handle.restore()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        self.estimator = None
        torch = getattr(self, "torch", None)
        if torch is not None:
            try:
                torch.cuda.empty_cache()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure


class Sam3DProcess:
    """A bounded local IPC worker; all artifacts stay under the SAM runtime."""

    def __init__(self, python: Path, checkpoint: Path, *, runtime_dir: Path,
                 device: str, inference_type: str):
        # Native pools initialize during imports, before the model constructor.
        # Set the isolated worker's budget before Python/NumPy/Torch start.
        env = prepare_sam_worker_environment(os.environ)
        self.transport = configured_transport(env)
        self.transport_metadata = transport_metadata(self.transport)
        self.shared_writer = SharedFrameWriter() if self.transport == "shared_memory" else None
        self.request_id = 0
        # Timeout handling closes the process from inside infer(). External
        # cancellation terminates it first, then waits for this lock to release.
        self.lock = threading.RLock()
        self.termination_lock = threading.Lock()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.ipc_dir = Path(os.environ.get("RALLYMATE_SAM3D_IPC_DIR", self.runtime_dir / "cache" / "ipc"))
        self.ipc_dir.mkdir(parents=True, exist_ok=True)
        log_dir = self.runtime_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log = (log_dir / f"sam-worker-{os.getpid()}-{time.time_ns()}.log").open("a", encoding="utf-8")
        env["RALLYMATE_SAM3D_ISOLATED_WORKER"] = "1"
        src = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (src, env.get("PYTHONPATH"))))
        command = [str(python), "-u", "-m", "rallymate_vision.sam3d_runner", "--serve",
                   "--checkpoint", str(checkpoint), "--runtime-dir", str(runtime_dir),
                   "--device", device, "--inference-type", inference_type]
        try:
            # A Windows venv launcher can have a second Python process. Own
            # the entire tree before it starts so timeout/cancellation cannot
            # leave that child reading a shared buffer after the parent exits.
            self.owner = OwnedProcess(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=self.log, text=True, encoding="utf-8", env=env)
            self.process = self.owner.process
        except BaseException:
            self.log.close()
            raise
        self.messages = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            ready = self._receive(float(os.environ.get("RALLYMATE_SAM3D_LOAD_TIMEOUT", "600")))
            if ready.get("status") != "ready":
                raise RuntimeError(f"SAM subprocess failed to initialize ({ready.get('error_type', 'unknown')})")
            self.metadata = ready["metadata"]
            if self.transport == "shared_memory" and self.metadata.get("ipc_transport") != self.transport_metadata:
                raise RuntimeError("SAM worker did not confirm the shared-memory transport contract")
            self.metadata["ipc_transport"] = dict(self.transport_metadata)
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("SAM IPC response must be an object")
                    self.messages.put(message)
                except (ValueError, TypeError):
                    self.messages.put({"status": "protocol_error"})
        except (OSError, ValueError):
            # close() may close stdout during cancellation.
            pass
        finally:
            self.messages.put({"status": "worker_exited"})

    def _receive(self, timeout: float):
        try:
            result = self.messages.get(timeout=timeout)
        except queue.Empty as exc:
            self.close()
            raise RuntimeError("SAM subprocess timeout; worker stopped") from exc
        if not isinstance(result, dict):
            self.close()
            raise RuntimeError("SAM subprocess protocol_error: response must be an object")
        if result.get("status") in {"protocol_error", "worker_exited"}:
            self.close()
            raise RuntimeError(f"SAM subprocess {result['status']}")
        return result

    def infer(self, frame_bgr: np.ndarray, boxes: list[list[float]]) -> list[dict]:
        started = time.perf_counter()
        with self.lock:
            timing = {"lock_wait_ms": (time.perf_counter() - started) * 1000}
            if self.process.poll() is not None:
                raise RuntimeError("SAM subprocess is no longer running")
            path = None
            try:
                stage_started = time.perf_counter()
                request = {"boxes": boxes, "transport": self.transport}
                if self.shared_writer is not None:
                    request["shared_image"] = self.shared_writer.write(frame_bgr)
                    timing["copy_source_array_ms"] = (time.perf_counter() - stage_started) * 1000
                else:
                    with tempfile.NamedTemporaryFile(dir=self.ipc_dir, suffix=".npy", delete=False) as handle:
                        path = Path(handle.name)
                        np.save(handle, frame_bgr, allow_pickle=False)
                    request["image"] = str(path)
                    timing["save_source_array_ms"] = (time.perf_counter() - stage_started) * 1000
                self.request_id += 1
                request["request_id"] = self.request_id
                stage_started = time.perf_counter()
                try:
                    self.process.stdin.write(json.dumps(request) + "\n")
                    self.process.stdin.flush()
                except (OSError, ValueError):
                    self.close()
                    raise
                timing["send_request_ms"] = (time.perf_counter() - stage_started) * 1000
                stage_started = time.perf_counter()
                result = self._receive(float(os.environ.get("RALLYMATE_SAM3D_FRAME_TIMEOUT", "180")))
                timing["worker_round_trip_wait_ms"] = (time.perf_counter() - stage_started) * 1000
                if type(result.get("request_id")) is not int or result["request_id"] != self.request_id:
                    # A late or mismatched acknowledgement must never permit
                    # another write into a buffer that the child may still use.
                    self.close()
                    raise RuntimeError("SAM worker response does not match the in-flight source frame")
                if result.get("status") != "ok":
                    self.close()
                    raise RuntimeError(f"SAM worker request failed ({result.get('error_type', 'unknown')})")
                outputs = result.get("outputs")
                transport_timing = result.get("transport_timing", {})
                if (not isinstance(outputs, list) or any(not isinstance(item, dict) for item in outputs)
                        or not isinstance(transport_timing, dict)):
                    # Even a matching id must not acknowledge malformed data.
                    # Stop the entire reader tree before releasing the source
                    # mapping so a later frame cannot reuse uncertain storage.
                    self.close()
                    raise RuntimeError("SAM subprocess protocol_error: malformed outputs or timing")
                timing.update(transport_timing)
                for output in outputs:
                    output["ipc_request_timing_ms"] = dict(timing)
                    output["ipc_timing_scope"] = "shared_per_request_do_not_sum_across_subjects"
                    output["ipc_transport"] = dict(self.transport_metadata)
                return outputs
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)

    def close(self):
        # Do not wait for infer's request lock before terminating: the worker
        # may be hung. Release the termination lock before waiting on infer so
        # a simultaneous timeout inside infer can also finish close().
        with self.termination_lock:
            self.owner.close()
        with self.lock:
            if self.shared_writer is not None:
                self.shared_writer.close()
            for stream in (self.process.stdin, self.process.stdout, self.log):
                if stream is not None:
                    stream.close()


def _serve(args):
    try:
        transport = configured_transport(os.environ)
        import torch
        import cv2
        cpu_configuration = configure_sam_cpu_pools(torch, cv2, os.environ)
        engine = Sam3DEngine(args.checkpoint, runtime_dir=args.runtime_dir, device=args.device,
                             inference_type=args.inference_type)
        # Keep actual post-import values, not just the intended environment.
        engine.metadata["cpu_threads"].update(
            cpu_configuration,
            torch_intraop=torch.get_num_threads(),
            torch_interop=torch.get_num_interop_threads(),
            opencv=cv2.getNumThreads(),
            omp_wait_policy=os.environ.get("OMP_WAIT_POLICY"),
            kmp_blocktime=os.environ.get("KMP_BLOCKTIME"),
            wait_mode=os.environ.get("RALLYMATE_SAM3D_WAIT_MODE", "inherit"),
        )
        engine.metadata["ipc_transport"] = transport_metadata(transport)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), flush=True)
        print(f"SAM load failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "ready", "metadata": engine.metadata}), flush=True)
    shared_reader = SharedFrameReader() if transport == "shared_memory" else None
    last_request_id = 0
    try:
        for line in sys.stdin:
            frame = None
            request_id = None
            try:
                request = json.loads(line)
                request_id = request.get("request_id")
                if type(request_id) is not int or request_id != last_request_id + 1:
                    raise ValueError("SAM IPC request is out of order")
                last_request_id = request_id
                if request.get("transport") != transport:
                    raise ValueError("SAM IPC request uses an unexpected transport")
                load_started = time.perf_counter()
                frame = (shared_reader.read(request["shared_image"]) if shared_reader is not None
                         else np.load(request["image"], allow_pickle=False))
                load_ms = (time.perf_counter() - load_started) * 1000
                outputs = engine.infer(frame, request["boxes"])
                result = {"status": "ok", "request_id": request_id, "outputs": outputs,
                          "transport_timing": {"worker_load_source_array_ms": load_ms}}
            except Exception as exc:
                result = {"status": "failed", "request_id": request_id, "error_type": type(exc).__name__}
            finally:
                # Release the exported view before the next request can switch
                # mapping sizes and before acknowledging safe parent reuse.
                frame = None
            print(json.dumps(result, allow_nan=False), flush=True)
    finally:
        try:
            engine.close()
        finally:
            if shared_reader is not None:
                shared_reader.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--inference-type", choices=("body", "full"), default="body")
    return _serve(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
