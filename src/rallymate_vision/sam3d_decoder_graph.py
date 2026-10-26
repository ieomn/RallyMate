"""Opt-in capture of the pinned complete body decoder and all six callbacks.

The prompt encoder (including its original coordinate assertion) runs once per
request outside capture. Everything else uses the original forward_decoder,
the six original transformer layers, the existing graph-safe MHR implementation,
all six camera/mesh projections and all five 2D/3D token updates. This module
does not enable itself or modify official files. The caller exclusively owns
the model and restores this handle before restoring its prerequisite handles.
"""
from __future__ import annotations

from contextlib import contextmanager
import ast
import hashlib
import inspect
from pathlib import Path
import threading
import time
import textwrap
from types import MethodType

SOURCE_HASHES = {
    "model": "d123798e9519fe1ea9cfbfcc07982d33ca9f4d9dfe796d10f234cc314f3e3de9",
    "base": "ebc9174c5fc7ca9286cbe9d04d103ce5868bb40e414b172295f4f2e5b5d61b68",
    "decoder": "2d1c15a06fbb8b9a30e8b841da6fa4a49e9bf1260d4f7013e35725de081e280c",
    "decoder_layer": "7d972a5ec163fc69c72e2f2849426d08cee44fc4eda8615791b4dd180582dd76",
    "camera": "57d8e60f1073eab798dc4910a33a5df186ab600fb5496b186c1e5b3bf4c2daf0",
    "geometry": "8ce32a7540ec5aae17a65fd09f2e02b7fab5b26de65b0b7f58e4250057beb6cb",
}
SCOPE_NAMES = (
    "init_pose", "init_camera", "init_to_token_mhr", "prev_to_token_mhr",
    "prompt_encoder", "prompt_to_token", "ray_cond_emb", "hand_box_embedding",
    "keypoint_embedding", "keypoint3d_embedding", "decoder", "head_pose", "head_camera",
    "keypoint_posemb_linear", "keypoint_feat_linear", "keypoint3d_posemb_linear",
)
MODEL_METHODS = (
    "forward_decoder", "camera_project", "_full_to_crop", "_flatten_person",
    "keypoint_token_update_fn", "keypoint3d_token_update_fn",
)
BATCH_KEYS = ("img", "bbox_center", "bbox_scale", "ori_img_size", "cam_int",
              "affine_trans", "img_size", "ray_cond")
ARGUMENT_NAMES = ("image_embeddings", "init_estimate", "keypoints", "prev_estimate",
                  "condition_info", "batch")


def _identity(method):
    return id(getattr(method, "__func__", method))


def _compile_device_indices(method, owner, torch, device):
    """Preserve advanced-index semantics; only hoist source-pinned tiny lists.

    Python tensor indexing constructs an unpinned CPU LongTensor for each list,
    which cannot be copied during capture. Identical device LongTensor indices
    retain the original gather/scatter, rank, ordering and copy semantics.
    """
    fn = getattr(method, '__func__', method)
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    namespace, constants, receipt = dict(fn.__globals__), [], []

    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -evaluate(node.operand)
        if isinstance(node, ast.Name) and node.id == 'num_pose_token':
            # The pinned forward asserts num_pose_token == 1 immediately above.
            return 1
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name) and node.value.value.id == 'self'
                and node.value.attr == 'pelvis_idx'):
            return owner.pelvis_idx[evaluate(node.slice)]
        raise ValueError('Unknown source-pinned list index: ' + ast.unparse(node))

    class DeviceIndices(ast.NodeTransformer):
        def visit_Subscript(self, node):
            node = self.generic_visit(node)
            elements = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            for i, item in enumerate(elements):
                if isinstance(item, ast.List):
                    values = [evaluate(value) for value in item.elts]
                    name = '_decoder_graph_index_' + str(len(constants))
                    tensor = torch.tensor(values, device=device, dtype=torch.long)
                    namespace[name] = tensor
                    constants.append(tensor)
                    receipt.append({'expression': ast.unparse(item), 'values': values})
                    elements[i] = ast.copy_location(ast.Name(id=name, ctx=ast.Load()), item)
            node.slice = (ast.Tuple(elts=elements, ctx=ast.Load())
                          if isinstance(node.slice, ast.Tuple) else elements[0])
            return node

    tree = ast.fix_missing_locations(DeviceIndices().visit(tree))
    # The camera method calls a same-named global geometry function. Defining
    # the copied method under that global name would accidentally shadow it.
    compiled_name = '_decoder_graph_' + fn.__name__
    tree.body[0].name = compiled_name
    exec(compile(tree, inspect.getsourcefile(fn), 'exec'), namespace)
    return MethodType(namespace[compiled_name], owner), constants, receipt


def _constant_signature(tensors):
    return tuple((id(value), value.data_ptr(), value._version, tuple(value.shape), value.dtype, value.device)
                 for value in tensors)


def _freeze(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return type(value), value
    if isinstance(value, dict):
        return type(value), tuple((key, _freeze(item)) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return type(value), tuple(_freeze(item) for item in value)
    raise TypeError("Unsupported decoder Python branch setting")


class _Scope:
    """Traverse every captured module, without rescanning the separate backbone."""
    def __init__(self, model):
        self.model = model

    def _named(self, method):
        for root in SCOPE_NAMES:
            if hasattr(self.model, root):
                for name, value in getattr(getattr(self.model, root), method)():
                    yield root + ("." + name if name else ""), value

    def named_parameters(self):
        return self._named("named_parameters")

    def named_buffers(self):
        return self._named("named_buffers")

    def named_modules(self):
        return self._named("named_modules")


def _decoder_state(model):
    methods = tuple((name, _identity(getattr(model, name))) for name in MODEL_METHODS)
    helpers = tuple((name, _identity(getattr(owner, name))) for owner, name in (
        (model.prompt_encoder, "_embed_keypoints"),
        (model.prompt_encoder, "get_dense_pe"),
        (model.prompt_encoder.pe_layer, "_pe_encoding"),
        (model.head_pose, "replace_hands_in_pose"),
        (model.head_camera, "perspective_projection"),
    ))
    settings = (model.training, _freeze(model.cfg), _freeze(model.keypoint_embedding_idxs),
                _freeze(model.keypoint3d_embedding_idxs), _freeze(model.pelvis_idx), model.camera_type)
    scope = _Scope(model)
    tensors = tuple((name, id(value), value.data_ptr(), value._version,
                     tuple(value.shape), value.dtype, value.device)
                    for name, value in (*scope.named_parameters(), *scope.named_buffers()))
    branches = []
    for name, child in scope.named_modules():
        values = []
        for key, value in vars(child).items():
            if key.startswith('_'):
                continue
            if value is None or isinstance(value, (bool, int, float, str)):
                values.append((key, type(value), value))
            elif isinstance(value, tuple) and all(isinstance(x, (bool, int, float, str)) for x in value):
                values.append((key, tuple, value))
            elif type(value).__module__ == 'torch' and type(value).__name__ in {'dtype', 'device'}:
                values.append((key, type(value), str(value)))
        hooks = tuple((kind, tuple((key, id(value)) for key, value in getattr(child, kind, {}).items()))
                      for kind in ('_forward_hooks', '_forward_pre_hooks'))
        # Some MHR RecursiveScriptModule containers own tensors but expose no
        # forward. Their tensor/identity/branch/hook guards still apply in full.
        forward = getattr(child, 'forward', None)
        branches.append((name, id(child), _identity(forward), tuple(sorted(values)), hooks))
    return (tensors, tuple(branches)), methods, helpers, settings


def _tree_key(value, torch):
    if isinstance(value, torch.Tensor):
        return ("tensor", tuple(value.shape), tuple(value.stride()), value.dtype, value.device)
    if isinstance(value, tuple):
        return ("tuple", tuple(_tree_key(item, torch) for item in value))
    if isinstance(value, dict):
        return ("dict", tuple((key, _tree_key(item, torch)) for key, item in value.items()))
    raise TypeError("Unsupported decoder graph input")


def _clone_inputs(value, torch):
    if isinstance(value, torch.Tensor):
        return value.detach().clone()
    if isinstance(value, tuple):
        return tuple(_clone_inputs(item, torch) for item in value)
    return {key: _clone_inputs(item, torch) for key, item in value.items()}


def _copy_inputs(target, source, torch):
    if isinstance(target, torch.Tensor):
        target.copy_(source)
    elif isinstance(target, tuple):
        for a, b in zip(target, source):
            _copy_inputs(a, b, torch)
    else:
        for key in target:
            _copy_inputs(target[key], source[key], torch)


def _clone_outputs(value, torch):
    if isinstance(value, torch.Tensor):
        return value.clone()
    if isinstance(value, tuple):
        return tuple(_clone_outputs(item, torch) for item in value)
    if isinstance(value, list):
        return [_clone_outputs(item, torch) for item in value]
    if isinstance(value, dict):
        return {key: _clone_outputs(item, torch) for key, item in value.items()}
    if value is None:
        return None
    # The prerequisite head provides one immutable, source-verified topology.
    # Match its ownership contract; never introduce per-frame CPU topology copies.
    import numpy as np
    if isinstance(value, np.ndarray) and not value.flags.writeable:
        return value
    raise TypeError("Unsupported mutable/non-tensor decoder output")


@contextmanager
def _temporary_bindings(records):
    previous = []
    try:
        for owner, name, value in records:
            previous.append((owner, name, name in vars(owner), getattr(owner, name)))
            setattr(owner, name, value)
        yield
    finally:
        for owner, name, existed, old in reversed(previous):
            if existed:
                setattr(owner, name, old)
            elif name in vars(owner):
                delattr(owner, name)


class DecoderGraphHandle:
    def __init__(self, model, torch, head_handle, layer_handles=(), source_hashes=None,
                 capture_forward=None, capture_bindings=(), constants=(), index_receipt=None):
        self.model, self.torch, self.head_handle = model, torch, head_handle
        self.layer_handles = tuple(layer_handles)
        self.device = model.head_pose.faces.device
        self.original = model.forward_decoder
        self.capture_forward = capture_forward or self.original
        self.capture_bindings = tuple(capture_bindings)
        self.constants = tuple(constants)
        self.constant_state = _constant_signature(self.constants)
        self.signature = inspect.signature(self.original)
        self.had_instance = "forward_decoder" in vars(model)
        self.graph = self.inputs = self.outputs = self.key = None
        self.disabled = self.restored = False
        self.lock = threading.Lock()
        self.receipt = {
            "candidate": "complete_body_decoder_cuda_graph_v1",
            "candidate_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_sha256": source_hashes or {}, "maximum_graphs": 1,
            "capture_status": "pending_first_call", "replays": 0, "fallback_calls": 0,
            "captured_body_heads": None, "body_head_executions": 0,
            "captured_operations_per_replay": None, "decoder_layer_executions": 0,
            "keypoint_2d_update_executions": 0, "keypoint_3d_update_executions": 0,
            "replay_execution_counts_derive_from_verified_capture": True,
            "superseded_during_successful_replay": {
                "body_head_graph": True, "decoder_layer_graphs": len(layer_handles),
                "backbone_graph": False, "existing_handles_retained_for_eager_fallback": True},
            "prompt_encoder_calls": 0, "capture_seconds": None,
            "prompt_assertion_and_encoder_outside_graph": True,
            "all_six_decoder_heads_retained": True, "all_five_token_updates_retained": True,
            "camera_and_full_mesh_projections_retained": True, "output_tensors_cloned": True,
            "precision_resolution_and_weights_unchanged": True, "original_files_unchanged": True,
            "original_decoder_and_callbacks_arithmetic_unchanged": True,
            "static_advanced_indices": index_receipt or {},
            "advanced_index_rank_order_and_copy_semantics_preserved": True,
        }
        try:
            model.forward_decoder = self
            self.state = _decoder_state(model)
        except BaseException:
            self._restore_method()
            raise

    def _restore_method(self):
        if self.had_instance:
            self.model.forward_decoder = self.original
        elif "forward_decoder" in vars(self.model):
            delattr(self.model, "forward_decoder")

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
        if tuple(binding.arguments) != ARGUMENT_NAMES:
            return None
        values = binding.arguments
        if values["init_estimate"] is not None or values["prev_estimate"] is not None:
            return None
        batch = values["batch"]
        if not isinstance(batch, dict) or any(key not in batch for key in BATCH_KEYS):
            return None
        inputs = (values["image_embeddings"], values["keypoints"], values["condition_info"],
                  {key: batch[key] for key in BATCH_KEYS})
        tensors = (*inputs[:3], *inputs[3].values())
        if any(not isinstance(x, self.torch.Tensor) or not x.is_cuda or x.device != self.device
               or x.layout != self.torch.strided or x.ndim < 2 or x.shape[0] != 1
               for x in tensors):
            return None
        if (inputs[0].ndim != 4 or inputs[1].ndim != 3 or inputs[1].shape[-1] != 3
                or batch["img"].ndim != 5 or batch["img"].shape[:2] != (1, 1)
                or type(self.model.body_batch_idx) is not list or len(self.model.body_batch_idx) != 1
                or type(self.model.body_batch_idx[0]) is not int or self.model.body_batch_idx[0] != 0
                or type(self.model.hand_batch_idx) is not list or self.model.hand_batch_idx != []
                or self.model._batch_size != 1 or self.model._max_num_person != 1):
            return None
        return inputs

    def _capture(self, values):
        torch = self.torch
        started = time.perf_counter()
        self.inputs = _clone_inputs(values, torch)
        image, points, condition, batch, prompt = self.inputs
        head_calls = [0]
        layer_calls, updates_2d, updates_3d = [0], [0], [0]

        def counted_head(*args, **kwargs):
            head_calls[0] += 1
            return self.head_handle.safe_forward(*args, **kwargs)

        def supplied_prompt(*args, **kwargs):
            return prompt

        def counted(function, counter):
            def call(*args, **kwargs):
                counter[0] += 1
                return function(*args, **kwargs)
            return call

        records = [(self.model.head_pose, "forward", counted_head),
                   (self.model.prompt_encoder, "forward", supplied_prompt)]
        for layer in self.model.decoder.layers:
            original = next((handle.original for handle in self.layer_handles if handle.module is layer), layer.forward)
            records.append((layer, 'forward', counted(original, layer_calls)))
        for owner, name, value in self.capture_bindings:
            if name == 'keypoint_token_update_fn':
                value = counted(value, updates_2d)
            elif name == 'keypoint3d_token_update_fn':
                value = counted(value, updates_3d)
            records.append((owner, name, value))

        def run():
            head_calls[0] = 0
            layer_calls[0] = updates_2d[0] = updates_3d[0] = 0
            output = self.capture_forward(image, init_estimate=None, keypoints=points,
                                   prev_estimate=None, condition_info=condition, batch=batch)
            if head_calls[0] != 6 or not isinstance(output, tuple) or len(output[1]) != 6:
                raise ValueError("Complete decoder capture must execute and retain all six heads")
            if (layer_calls[0], updates_2d[0], updates_3d[0]) != (6, 5, 5):
                raise ValueError('Complete decoder capture must execute six layers and both five token updates')
            return output

        stream = torch.cuda.Stream(device=self.device)
        stream.wait_stream(torch.cuda.current_stream(self.device))
        with _temporary_bindings(records):
            with torch.cuda.stream(stream):
                for _ in range(3):
                    run()
            torch.cuda.current_stream(self.device).wait_stream(stream)
            torch.cuda.synchronize(self.device)
            graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(graph, stream=stream):
                outputs = run()
        _clone_outputs(outputs, torch)
        self.graph, self.outputs = graph, outputs
        self.receipt.update(capture_status="captured", captured_body_heads=head_calls[0],
                            captured_operations_per_replay={'body_heads': head_calls[0],
                                'decoder_layers': layer_calls[0], 'keypoint_2d_updates': updates_2d[0],
                                'keypoint_3d_updates': updates_3d[0]},
                            capture_seconds=time.perf_counter() - started)

    def __call__(self, *args, **kwargs):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Concurrent complete SAM decoder graph use is unsupported")
        try:
            if self.restored:
                raise RuntimeError("Complete SAM decoder graph has been restored")
            if (_decoder_state(self.model) != self.state
                    or _constant_signature(self.constants) != self.constant_state):
                self.receipt["capture_status"] = "invalidated_model_state"
                raise RuntimeError("Complete SAM decoder graph model state changed; reload worker")
            torch = self.torch
            if (self.model.training or torch.is_grad_enabled() or torch.is_autocast_enabled()
                    or self.head_handle.restored):
                return self._fallback(args, kwargs, "training_grad_autocast_or_prerequisite")
            inputs = self._arguments(args, kwargs)
            if self.disabled or inputs is None:
                return self._fallback(args, kwargs, "disabled_or_unsupported_contract")
            # Execute the exact current prompt encoder and its original range
            # assertion outside capture. Dynamic prompts are never frozen.
            prompt = self.model.prompt_encoder(keypoints=inputs[1])
            self.receipt["prompt_encoder_calls"] += 1
            values = (*inputs, prompt)
            key = _tree_key(values, torch)
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
                                        capture_error_type=type(error).__name__,
                                        capture_error=str(error)[:800])
                    torch.cuda.synchronize(self.device)
                    return self._fallback(args, kwargs, "capture_error")
            _copy_inputs(self.inputs, values, torch)
            self.graph.replay()
            self.receipt["replays"] += 1
            self.receipt["body_head_executions"] += 6
            self.receipt["decoder_layer_executions"] += 6
            self.receipt["keypoint_2d_update_executions"] += 5
            self.receipt["keypoint_3d_update_executions"] += 5
            return _clone_outputs(self.outputs, torch)
        finally:
            self.lock.release()

    def restore(self):
        if self.restored:
            return
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Cannot restore a running complete SAM decoder graph")
        try:
            if self.graph is not None:
                self.torch.cuda.synchronize(self.device)
            self.graph = self.inputs = self.outputs = None
            self._restore_method()
            self.restored = True
            self.receipt["restored"] = True
        finally:
            self.lock.release()


def apply_decoder_cuda_graph(model, *, head_handle, forward_handle=None):
    """Install over existing reversible head/layer handles; restore this first."""
    import torch
    from .sam3d_cuda_graph import HeadGraphHandle
    from .sam3d_forward_graph import ModuleForwardGraph
    from sam_3d_body.models.modules.geometry_utils import perspective_projection

    if type(model).__name__ != "SAM3DBody" or type(model).__module__ != "sam_3d_body.models.meta_arch.sam3d_body":
        raise ValueError("Complete decoder graph requires the pinned SAM3DBody")
    if (not isinstance(head_handle, HeadGraphHandle) or head_handle.restored
            or model.head_pose is not head_handle.head or model.head_pose.forward is not head_handle
            or model.head_pose.mhr is not head_handle.candidate
            or model.head_pose.replace_hands_in_pose != head_handle.safe_replace):
        raise ValueError("Complete decoder graph requires the active validated body-head handle")
    functions = {"model": type(model).forward_decoder, "base": type(model)._flatten_person,
                 "decoder": type(model.decoder).forward,
                 "decoder_layer": type(model.decoder.layers[0]).forward,
                 "camera": type(model.head_camera).perspective_projection,
                 "geometry": perspective_projection}
    sources = {}
    for name, fn in functions.items():
        sources[name] = hashlib.sha256(Path(inspect.getsourcefile(fn)).read_bytes()).hexdigest()
        if sources[name] != SOURCE_HASHES[name]:
            raise ValueError("Complete decoder source identity differs: " + name)
    for name in MODEL_METHODS:
        if getattr(getattr(model, name), "__func__", None) is not getattr(type(model), name):
            raise ValueError("Complete decoder requires unchanged model methods: " + name)
    if (getattr(model.head_camera.perspective_projection, '__func__', None)
            is not type(model.head_camera).perspective_projection):
        raise ValueError('Complete decoder requires the unchanged camera projection')
    if (model.decoder.forward.__func__ is not type(model.decoder).forward
            or len(model.decoder.layers) != 6 or not model.decoder.do_interm_preds
            or not model.decoder.keypoint_token_update):
        raise ValueError("Complete decoder graph requires six heads and token callbacks")
    if model.head_pose.faces.device.type != "cuda":
        raise ValueError("Complete decoder graph requires CUDA")
    for name in ('keypoint_embedding_idxs', 'keypoint3d_embedding_idxs', 'pelvis_idx'):
        values = getattr(model, name)
        if type(values) is not list or not values or any(type(value) is not int for value in values):
            raise ValueError('Complete decoder requires Python integer index lists: ' + name)
    for _, child in _Scope(model).named_modules():
        if child.training or child._forward_hooks or child._forward_pre_hooks:
            raise ValueError("Complete decoder graph requires evaluation modules without hooks")
    layer_handles = []
    available = () if forward_handle is None else forward_handle.handles
    for layer in model.decoder.layers:
        if getattr(layer.forward, "__func__", None) is type(layer).forward:
            continue
        match = next((handle for handle in available if handle.module is layer), None)
        if not isinstance(match, ModuleForwardGraph) or match.restored or layer.forward is not match:
            raise ValueError("Unsupported nested decoder layer binding")
        layer_handles.append(match)
    bindings, constants, index_receipt = [], [], {}
    capture_forward = None
    for owner, name in [(model, name) for name in ('forward_decoder', 'camera_project', '_full_to_crop',
                                                  'keypoint_token_update_fn', 'keypoint3d_token_update_fn')] + [
                          (model.head_camera, 'perspective_projection')]:
        fn, indices, changes = _compile_device_indices(getattr(owner, name), owner, torch, model.head_pose.faces.device)
        constants.extend(indices)
        index_receipt[name] = changes
        if name == 'forward_decoder':
            capture_forward = fn
        else:
            bindings.append((owner, name, fn))
    for name, values in (('body_batch_idx', [0]), ('keypoint_embedding_idxs', model.keypoint_embedding_idxs),
                         ('keypoint3d_embedding_idxs', model.keypoint3d_embedding_idxs)):
        tensor = torch.tensor(values, dtype=torch.long, device=model.head_pose.faces.device)
        constants.append(tensor)
        bindings.append((model, name, tensor))
        index_receipt[name] = list(values)
    return DecoderGraphHandle(model, torch, head_handle, layer_handles, sources,
                              capture_forward, bindings, constants, index_receipt)
