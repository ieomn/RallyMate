"""Reversible CUDA Graph execution of the pinned, single-person SAM body head.

Every decoder head still runs, with its current pose token and initial estimate.
Only static bookkeeping is moved out of that call; arithmetic, weights, FP64
skeleton transforms, output ownership and all 18,439 vertices are preserved.
Official files on disk are never modified. Torch remains an optional lazy import.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import io
from pathlib import Path
import textwrap
import threading
import time
from types import FunctionType, MethodType
import zipfile


MHR_SHA256 = "352e271a6c42729c68554ceaea0c955e866970160c31e35506d782dc0f7377bc"
SCRIPT_UTIL_SHA256 = "715e960ec6d195cd682f2bc613a5c907c843100c071f556d5e5e543d34eccaa1"
HEAD_SHA256 = "1e4b5482f78b337a2c739c945f29a02f579b3aca99523f69f5436c3f1e394bf8"
UTIL_SHA256 = "a25c0bc7165fdb490f14f28bf4b007ad0ece3dc0c8c0bc4230eb2d743cf6c210"
SCRIPT_REPLACEMENTS = {
    "torch.sparse_coo_tensor(sparse_indices, sparse_weight, [_0, _1])":
        "torch.sparse_coo_tensor(sparse_indices, sparse_weight, [_0, _1], is_coalesced=True)",
    "torch.empty(torch.add(_3, [3, 3]), dtype=ops.prim.dtype(r))":
        "torch.empty(torch.add(_3, [3, 3]), dtype=ops.prim.dtype(r), device=ops.prim.device(r))",
}


def _verify(path, digest):
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
        raise ValueError("CUDA Graph source/asset identity differs: " + Path(path).name)


def _rewrite_script(data):
    if hashlib.sha256(data).hexdigest() != SCRIPT_UTIL_SHA256:
        raise ValueError("Pinned MHR script source differs")
    source = data.decode("utf-8")
    for old, new in SCRIPT_REPLACEMENTS.items():
        if source.count(old) != 1:
            raise ValueError("MHR rewrite must match exactly one expression")
        source = source.replace(old, new)
    return source.encode("utf-8")


def _validate_sparse_indices(module, torch):
    """Check real checkpoint values, never label arbitrary indices coalesced."""
    found = 0
    for _, child in module.named_modules():
        if not hasattr(child, "sparse_shape"):
            continue
        found += 1
        indices = child.sparse_indices.detach().cpu()
        shape = tuple(child.sparse_shape)
        values = child.sparse_weight.detach().cpu()
        if (indices.dtype != torch.int64 or indices.ndim != 2 or indices.shape[0] != 2
                or indices.shape[1] != values.numel() or values.ndim != 1
                or not bool(torch.isfinite(values).all()) or len(shape) != 2
                or min(shape) <= 0 or not bool((indices >= 0).all())
                or not bool((indices[0] < shape[0]).all())
                or not bool((indices[1] < shape[1]).all())):
            raise ValueError("MHR sparse tensor invariants differ")
        ordered = indices[0] * shape[1] + indices[1]
        if not bool((ordered[1:] > ordered[:-1]).all()):
            raise ValueError("MHR sparse indices must be strictly sorted and unique")
    if found != 1:
        raise ValueError("Expected exactly one pinned MHR sparse linear layer")


def _load_graph_safe_mhr(path, original, torch):
    _verify(path, MHR_SHA256)
    _validate_sparse_indices(original, torch)
    target, changed = io.BytesIO(), 0
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(target, "w") as output:
        for info in source.infolist():
            data = source.read(info)
            if info.filename.endswith("/pymomentum/mhr/utils.py"):
                data = _rewrite_script(data)
                changed += 1
            output.writestr(info, data)
    if changed != 1:
        raise ValueError("Expected one pinned MHR utility script member")
    target.seek(0)
    candidate = torch.jit.load(target, map_location=next(original.parameters()).device).eval()
    # Reuse the actual loaded model tensors, including legitimate prior state.
    # This is not a second set of learned weights or a changed state_dict schema.
    candidate.load_state_dict(original.state_dict(), strict=True, assign=True)
    _validate_sparse_indices(candidate, torch)
    return candidate


def _compile_static(fn, assignments, expressions):
    """Copy a source-pinned function with checked static-expression substitutions."""
    syntax = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    namespace = dict(fn.__globals__)
    matched_assignments, matched_expressions = set(), set()

    class Replace(ast.NodeTransformer):
        def visit_Assign(self, node):
            if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in assignments):
                name = node.targets[0].id
                namespace["_graph_static_" + name] = assignments[name]
                node.value = ast.Name(id="_graph_static_" + name, ctx=ast.Load())
                matched_assignments.add(name)
                return node
            return self.generic_visit(node)

        def visit_Call(self, node):
            source = ast.unparse(node)
            if source in expressions:
                matched_expressions.add(source)
                return ast.copy_location(ast.parse(expressions[source], mode="eval").body, node)
            return self.generic_visit(node)

    syntax = ast.fix_missing_locations(Replace().visit(syntax))
    if matched_assignments != set(assignments) or matched_expressions != set(expressions):
        raise ValueError("Static head rewrites do not match the pinned source")
    exec(compile(syntax, inspect.getsourcefile(fn), "exec"), namespace)
    return namespace[fn.__name__]


def _safe_head_functions(head, torch):
    from sam_3d_body.models.modules import mhr_utils
    from rallymate_vision.sam3d_roma_optimization import _RomaProxy

    _verify(inspect.getsourcefile(type(head).forward), HEAD_SHA256)
    _verify(inspect.getsourcefile(mhr_utils.compact_cont_to_model_params_body), UTIL_SHA256)
    original = head.forward.__func__
    if (original.__code__ is not type(head).forward.__code__
            or not isinstance(original.__globals__.get("roma"), _RomaProxy)):
        raise ValueError("CUDA Graph requires the validated body-head rotation binding")
    device = head.faces.device
    body_fn = mhr_utils.compact_cont_to_model_params_body
    assignments = {}
    for node in ast.walk(ast.parse(inspect.getsource(body_fn))):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) == "torch.LongTensor"):
            assignments[node.targets[0].id] = torch.tensor(
                ast.literal_eval(node.value.args[0]), device=device, dtype=torch.long)
    if len(assignments) != 3:
        raise ValueError("Expected three fixed body index lists")
    body = _compile_static(body_fn, assignments, {
        "torch.zeros(*body_pose_cont.shape[:-1], 133).to(body_pose_cont)":
            "body_pose_cont.new_zeros((*body_pose_cont.shape[:-1], 133))"})

    hand_fn = mhr_utils.compact_cont_to_model_params_hand
    # Evaluate only its source-pinned, input-independent mask prelude on CPU.
    prefix = []
    for node in ast.parse(inspect.getsource(hand_fn)).body[0].body:
        prefix.append(node)
        if (isinstance(node, ast.Assign)
                and ast.unparse(node.targets[0]) == "mask_model_params_onedofs"):
            break
    namespace = dict(hand_fn.__globals__)
    namespace["hand_cont"] = torch.zeros(1, 54)
    exec(compile(ast.fix_missing_locations(ast.Module(body=prefix, type_ignores=[])),
                 inspect.getsourcefile(hand_fn), "exec"), namespace)
    names = ("mask_cont_threedofs", "mask_cont_onedofs",
             "mask_model_params_threedofs", "mask_model_params_onedofs")
    hand = _compile_static(hand_fn, {
        name: namespace[name].nonzero().flatten().to(device) for name in names
    }, {"torch.zeros(*hand_cont.shape[:-1], 27).to(hand_cont)":
            "hand_cont.new_zeros((*hand_cont.shape[:-1], 27))"})
    previous_replace = head.replace_hands_in_pose.__func__
    replacement = FunctionType(previous_replace.__code__, {
        **previous_replace.__globals__, "compact_cont_to_model_params_hand": hand},
        previous_replace.__name__, previous_replace.__defaults__, previous_replace.__closure__)
    namespace = {
        **original.__globals__, "compact_cont_to_model_params_body": body,
        "mhr_param_hand_mask": mhr_utils.mhr_param_hand_mask.nonzero().flatten().to(device),
        "_graph_faces": head.faces.detach().cpu().numpy().copy(),
    }
    namespace["_graph_faces"].setflags(write=False)
    source = textwrap.dedent(inspect.getsource(type(head).forward))
    changes = {
        "pred_pose_euler[:, mhr_param_hand_mask] = 0":
            "pred_pose_euler.index_fill_(1, mhr_param_hand_mask, 0)",
        "self.faces.cpu().numpy()": "_graph_faces",
        "[..., [1, 2]]": "[..., 1:3]",
    }
    for before, after in changes.items():
        if source.count(before) != (3 if before == "[..., [1, 2]]" else 1):
            raise ValueError("Pinned body-head rewrite count differs")
        source = source.replace(before, after)
    exec(compile(ast.parse(source), inspect.getsourcefile(type(head).forward), "exec"), namespace)
    return MethodType(namespace["forward"], head), MethodType(replacement, head)


def _state_signature(head):
    # Tensor version guards detect normal in-place optimizer/load_state_dict edits;
    # pointer/identity guards additionally detect replacement, .to(), and dtype changes.
    return tuple((name, id(value), value.data_ptr(), value._version,
                  tuple(value.shape), value.dtype, value.device)
                 for name, value in (*head.named_parameters(), *head.named_buffers()))


class HeadGraphHandle:
    """One bounded graph, owned by one head, with independent returned tensors."""
    def __init__(self, head, candidate, safe_forward, safe_replace, torch):
        self.head, self.torch = head, torch
        self.original_mhr, self.candidate = head.mhr, candidate
        self.previous = {name: (name in vars(head), getattr(head, name))
                         for name in ("forward", "replace_hands_in_pose")}
        self.safe_forward, self.safe_replace = safe_forward, safe_replace
        self.graph = self.inputs = self.outputs = self.key = None
        self.restored, self.disabled = False, False
        self.lock = threading.Lock()
        self.receipt = {
            "candidate": "single_person_body_head_cuda_graph_v1",
            "candidate_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "mhr_sha256": MHR_SHA256, "head_source_sha256": HEAD_SHA256,
            "utility_source_sha256": UTIL_SHA256, "script_utility_sha256": SCRIPT_UTIL_SHA256,
            "capture_status": "pending_first_body_head", "replays": 0, "fallback_calls": 0,
            "capture_seconds": None, "maximum_graphs": 1,
            "all_six_decoder_heads_retained": True, "output_tensors_cloned": True,
            "precision_resolution_and_weights_unchanged": True,
            "original_files_unchanged": True, "sparse_indices_verified_sorted_unique": True,
        }
        try:
            head.mhr = candidate
            head.replace_hands_in_pose = safe_replace
            head.forward = self
            self.state = _state_signature(head)
        except BaseException:
            head.mhr = self.original_mhr
            for name, (existed, value) in self.previous.items():
                if existed:
                    setattr(head, name, value)
                elif name in vars(head):
                    delattr(head, name)
            raise

    def _fallback(self, *args, **kwargs):
        self.receipt["fallback_calls"] += 1
        current_mhr, current_replace = self.head.mhr, self.head.replace_hands_in_pose
        self.head.mhr = self.original_mhr
        self.head.replace_hands_in_pose = self.previous["replace_hands_in_pose"][1]
        try:
            return self.previous["forward"][1](*args, **kwargs)
        finally:
            self.head.mhr, self.head.replace_hands_in_pose = current_mhr, current_replace

    def _capture(self, args):
        torch = self.torch
        started = time.perf_counter()
        self.inputs = tuple(value.detach().clone() for value in args)
        stream = torch.cuda.Stream(device=args[0].device)
        stream.wait_stream(torch.cuda.current_stream(args[0].device))
        with torch.cuda.stream(stream):
            for _ in range(5):
                self.safe_forward(*self.inputs)
        torch.cuda.current_stream(args[0].device).wait_stream(stream)
        torch.cuda.synchronize(args[0].device)
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph, stream=stream):
            outputs = self.safe_forward(*self.inputs)
        self.graph, self.outputs = graph, outputs
        self.receipt.update(capture_status="captured",
                            capture_seconds=time.perf_counter() - started)

    def __call__(self, *args, **kwargs):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Concurrent use of a single SAM CUDA Graph head is unsupported")
        try:
            torch = self.torch
            if self.restored:
                raise RuntimeError("SAM CUDA Graph handle has been restored")
            if self.head.training or _state_signature(self.head) != self.state:
                # A changed trained/model state must never replay captured pointers.
                self.receipt["capture_status"] = "invalidated_model_state"
                raise RuntimeError("SAM CUDA Graph model state changed; reload the inference worker")
            supported = (not kwargs and len(args) == 2 and not torch.is_grad_enabled()
                and not torch.is_autocast_enabled()
                and all(isinstance(x, torch.Tensor) and x.ndim == 2 and x.shape[0] == 1
                        and x.is_cuda and x.dtype == torch.float32 for x in args)
                and args[0].device == self.head.faces.device and args[1].device == args[0].device
                and args[1].shape[1] == self.head.npose)
            if self.disabled or not supported:
                return self._fallback(*args, **kwargs)
            key = tuple((tuple(value.shape), value.dtype, value.device) for value in args)
            if self.key is not None and key != self.key:
                return self._fallback(*args, **kwargs)
            if self.graph is None:
                self.key = key
                try:
                    self._capture(args)
                except Exception as error:
                    self.disabled = True
                    self.graph = self.inputs = self.outputs = None
                    self.receipt.update(capture_status="fallback_after_capture_error",
                                        capture_error_type=type(error).__name__)
                    # Confirm the device is usable before calling the original path.
                    # Fatal CUDA errors propagate instead of being hidden as fallback.
                    torch.cuda.synchronize(args[0].device)
                    return self._fallback(*args, **kwargs)
            for destination, source in zip(self.inputs, args):
                destination.copy_(source)
            self.graph.replay()
            self.receipt["replays"] += 1
            return {key: value.clone() if isinstance(value, torch.Tensor) else value
                    for key, value in self.outputs.items()}
        finally:
            self.lock.release()

    def restore(self):
        if self.restored:
            return
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Cannot restore a running SAM CUDA Graph head")
        try:
            self.torch.cuda.synchronize(self.head.faces.device)
            self.graph = self.inputs = self.outputs = None
            self.head.mhr = self.original_mhr
            for name, (existed, value) in self.previous.items():
                if existed:
                    setattr(self.head, name, value)
                else:
                    delattr(self.head, name)
            self.restored = True
            self.receipt["restored"] = True
        finally:
            self.lock.release()


def apply_head_cuda_graph(model):
    """Install only after the validated RoMa instance binding, in body mode."""
    import torch
    head = model.head_pose
    if head.training or head.faces.device.type != "cuda":
        raise ValueError("SAM CUDA Graph requires a CUDA evaluation model")
    safe_forward, safe_replace = _safe_head_functions(head, torch)
    candidate = _load_graph_safe_mhr(Path(head.model_data_dir), head.mhr, torch)
    return HeadGraphHandle(head, candidate, safe_forward, safe_replace, torch)
