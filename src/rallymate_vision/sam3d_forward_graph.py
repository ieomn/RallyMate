"""Instance-scoped CUDA graphs for the pinned SAM DINO backbone/decoder layers.

This executes the original forwards without arithmetic rewrites. Decoder prompt
updates and all six body heads remain outside these graphs, in original order.
One fixed input signature is retained per module; other signatures run eagerly.
"""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import threading
import time


SOURCE_HASHES = {
    "backbone": "1745546cf57e036706dfef378be589e24cf2a568da0e4781544e2a31e000628a",
    "dinov3_encoder": "1395c5a6297861101db35af44a04613a0c60a502c947fd3b54092852ddd682b5",
    "decoder": "2d1c15a06fbb8b9a30e8b841da6fa4a49e9bf1260d4f7013e35725de081e280c",
    "decoder_layer": "7d972a5ec163fc69c72e2f2849426d08cee44fc4eda8615791b4dd180582dd76",
}


def _verify_source(module, expected):
    forward = type(module).forward
    path = Path(inspect.getsourcefile(forward))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError("SAM forward graph source identity differs: " + path.name)
    if getattr(module.forward, "__func__", None) is not forward:
        raise ValueError("SAM forward graph requires the unchanged instance forward")
    return digest


def _state_signature(module):
    """Guard loaded tensor identity/version and captured Python branch settings."""
    tensors = tuple((name, id(value), value.data_ptr(), value._version,
                     tuple(value.shape), value.dtype, value.device)
                    for name, value in (*module.named_parameters(), *module.named_buffers()))
    branches = []
    for name, child in module.named_modules():
        values = []
        for key, value in vars(child).items():
            if key.startswith("_"):
                continue
            if value is None or isinstance(value, (bool, int, float, str)):
                values.append((key, type(value), value))
            elif isinstance(value, tuple) and all(isinstance(x, (bool, int, float, str)) for x in value):
                values.append((key, tuple, value))
            elif type(value).__module__ == "torch" and type(value).__name__ in {"dtype", "device"}:
                values.append((key, type(value), str(value)))
        hooks = tuple((kind, tuple((key, id(value)) for key, value in getattr(child, kind, {}).items()))
                      for kind in ("_forward_hooks", "_forward_pre_hooks"))
        forward = getattr(child.forward, "__func__", child.forward)
        branches.append((name, id(child), id(forward), tuple(sorted(values)), hooks))
    return tensors, tuple(branches)


def _clone_tree(value, torch):
    if isinstance(value, torch.Tensor):
        return value.clone()
    if isinstance(value, tuple):
        return tuple(_clone_tree(item, torch) for item in value)
    if isinstance(value, list):
        return [_clone_tree(item, torch) for item in value]
    if isinstance(value, dict):
        return {key: _clone_tree(item, torch) for key, item in value.items()}
    if value is None:
        return None
    raise TypeError("Pinned forward graph returned an unsupported non-tensor output")


class ModuleForwardGraph:
    """Bounded graph replay with fresh inputs and independently owned outputs."""

    def __init__(self, module, torch, *, name, argument_names, device):
        self.module, self.torch, self.device = module, torch, device
        self.name, self.argument_names = name, tuple(argument_names)
        self.had_forward = "forward" in vars(module)
        self.original = module.forward
        self.signature = inspect.signature(self.original)
        self.graph = self.inputs = self.outputs = self.key = None
        self.disabled = self.restored = False
        self.lock = threading.Lock()
        self.receipt = {"module": name, "capture_status": "pending_first_call", "replays": 0,
                        "fallback_calls": 0, "capture_seconds": None, "maximum_graphs": 1}
        try:
            module.forward = self
            self.state = _state_signature(module)
        except BaseException:
            self._restore_forward()
            raise

    def _restore_forward(self):
        if self.had_forward:
            self.module.forward = self.original
        elif "forward" in vars(self.module):
            delattr(self.module, "forward")

    def _fallback(self, args, kwargs, reason):
        self.receipt["fallback_calls"] += 1
        self.receipt["last_fallback_reason"] = reason
        return self.original(*args, **kwargs)

    def _arguments(self, args, kwargs):
        try:
            binding = self.signature.bind(*args, **kwargs)
        except TypeError:
            return None
        binding.apply_defaults()
        if tuple(binding.arguments) != self.argument_names:
            return None
        values = tuple(binding.arguments.values())
        tensor_count = 0
        for value in values:
            if value is None:
                continue
            if not isinstance(value, self.torch.Tensor):
                return None
            if (not value.is_cuda or value.device != self.device or value.ndim < 2
                    or value.shape[0] != 1 or value.layout != self.torch.strided):
                return None
            tensor_count += 1
        return values if tensor_count else None

    def _capture(self, values):
        torch = self.torch
        started = time.perf_counter()
        self.inputs = tuple(value.detach().clone() if value is not None else None for value in values)
        stream = torch.cuda.Stream(device=self.device)
        stream.wait_stream(torch.cuda.current_stream(self.device))
        with torch.cuda.stream(stream):
            for _ in range(3):
                self.original(*self.inputs)
        torch.cuda.current_stream(self.device).wait_stream(stream)
        torch.cuda.synchronize(self.device)
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph, stream=stream):
            outputs = self.original(*self.inputs)
        # Check the complete returned structure before retaining captured output.
        # The temporary clones also validate output ownership outside capture.
        _clone_tree(outputs, torch)
        self.graph, self.outputs = graph, outputs
        self.receipt.update(capture_status="captured", capture_seconds=time.perf_counter() - started)

    def __call__(self, *args, **kwargs):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Concurrent SAM module graph use is unsupported")
        try:
            if self.restored:
                raise RuntimeError("SAM module graph has been restored")
            if _state_signature(self.module) != self.state:
                self.receipt["capture_status"] = "invalidated_model_state"
                raise RuntimeError("SAM forward graph model state changed; reload the inference worker")
            torch = self.torch
            if self.module.training or torch.is_grad_enabled() or torch.is_autocast_enabled():
                return self._fallback(args, kwargs, "training_grad_or_autocast")
            values = self._arguments(args, kwargs)
            if self.disabled or values is None:
                return self._fallback(args, kwargs, "disabled_or_unsupported_contract")
            key = tuple(None if value is None else
                        (tuple(value.shape), tuple(value.stride()), value.dtype, value.device)
                        for value in values)
            if self.key is not None and key != self.key:
                return self._fallback(args, kwargs, "different_input_signature")
            if self.graph is None:
                self.key = key
                try:
                    self._capture(values)
                except Exception as error:
                    self.disabled = True
                    self.graph = self.inputs = self.outputs = None
                    self.receipt.update(capture_status="fallback_after_capture_error",
                                        capture_error_type=type(error).__name__)
                    # A poisoned CUDA context must surface, not masquerade as fallback.
                    torch.cuda.synchronize(self.device)
                    return self._fallback(args, kwargs, "capture_error")
            for target, source in zip(self.inputs, values):
                if target is not None:
                    target.copy_(source)
            self.graph.replay()
            self.receipt["replays"] += 1
            return _clone_tree(self.outputs, torch)
        finally:
            self.lock.release()

    def restore(self):
        if self.restored:
            return
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Cannot restore a running SAM module graph")
        try:
            if self.graph is not None:
                self.torch.cuda.synchronize(self.device)
            self.graph = self.inputs = self.outputs = None
            self._restore_forward()
            self.restored = True
            self.receipt["restored"] = True
        finally:
            self.lock.release()


class ForwardGraphHandle:
    def __init__(self, handles, scope, source_hashes):
        self.handles = handles
        self.receipt = {
            "candidate": "pinned_backbone_decoder_layers_cuda_graph_v1", "scope": scope,
            "candidate_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_sha256": source_hashes, "modules": [handle.receipt for handle in handles],
            "maximum_graphs": len(handles), "output_tensors_cloned": True,
            "all_six_decoder_heads_retained": True, "decoder_callbacks_unchanged": True,
            "original_forward_arithmetic_unchanged": True,
            "precision_resolution_and_weights_unchanged": True, "original_files_unchanged": True,
        }

    def restore(self):
        first_error = None
        failures = []
        for handle in reversed(self.handles):
            try:
                handle.restore()
            except BaseException as error:
                if first_error is None:
                    first_error = error
                failures.append({"module": handle.receipt.get("module"),
                                 "error_type": type(error).__name__})
        self.receipt["restored"] = not failures
        self.receipt["restore_failures"] = failures
        if first_error is not None:
            raise first_error


def apply_forward_cuda_graph(model, *, scope="backbone_decoder_layers"):
    """Add graph-safe regions without nesting the existing body-head graph.

    ``backbone`` allows an isolated ablation. All installation is reversible;
    actual capture/replay status remains in each live module receipt.
    """
    import torch
    if scope not in {"backbone", "backbone_decoder_layers"}:
        raise ValueError("Unsupported SAM forward graph scope")
    backbone = model.backbone
    if type(backbone).__name__ != "Dinov3Backbone" or type(backbone).__module__ != "sam_3d_body.models.backbones.dinov3":
        raise ValueError("SAM forward graph requires the pinned DINOv3 backbone")
    sources = {"backbone": _verify_source(backbone, SOURCE_HASHES["backbone"]),
               "dinov3_encoder": _verify_source(backbone.encoder, SOURCE_HASHES["dinov3_encoder"])}
    modules = [("backbone", backbone, ("x", "extra_embed"))]
    if scope == "backbone_decoder_layers":
        decoder = model.decoder
        sources["decoder"] = _verify_source(decoder, SOURCE_HASHES["decoder"])
        if len(decoder.layers) != 6 or not decoder.do_interm_preds:
            raise ValueError("SAM forward graph requires all six intermediate decoder heads")
        for index, layer in enumerate(decoder.layers):
            sources["decoder_layer"] = _verify_source(layer, SOURCE_HASHES["decoder_layer"])
            modules.append((f"decoder.layers.{index}", layer, ("x", "context", "x_pe", "context_pe", "x_mask")))
    handles = []
    try:
        for name, module, arguments in modules:
            device = next(module.parameters()).device
            if device.type != "cuda" or any(child.training for child in module.modules()):
                raise ValueError("SAM forward graph requires CUDA evaluation modules")
            # Hooks nested inside a captured module would execute only on capture.
            if any(child._forward_hooks or child._forward_pre_hooks for child in module.modules()):
                raise ValueError("SAM forward graph does not capture modules with runtime hooks")
            handles.append(ModuleForwardGraph(module, torch, name=name, argument_names=arguments, device=device))
        return ForwardGraphHandle(handles, scope, sources)
    except BaseException:
        for handle in reversed(handles):
            handle.restore()
        raise
