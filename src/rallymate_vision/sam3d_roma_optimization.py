"""Reversible fixed-shape RoMa conversion for the pinned SAM body head.

The caller must exclusively own the model until the handle is restored. This
module does not enable the optimization or import Torch/RoMa at import time.

Conversion arithmetic is adapted from RoMa, Copyright (c) 2020 NAVER Corp.
The full BSD-3-Clause license and SciPy notices are retained in
third_party/roma/LICENSE and third_party/roma/NOTICE.
"""
from __future__ import annotations

import ast
import copy
from functools import lru_cache
import hashlib
import inspect
import marshal
from pathlib import Path
from types import FunctionType, MethodType, SimpleNamespace


HEAD_SOURCE_SHA256 = "1e4b5482f78b337a2c739c945f29a02f579b3aca99523f69f5436c3f1e394bf8"
ROMA_SOURCE_SHA256 = {
    "mappings.py": "3d96bfd6bac2ea54bd4bb7134050a17889daf13cfb542b51f21daaed01bf0a55",
    "euler.py": "20acb540cc6189acc2a117f4b736666acb512cfa82206afd012b070ecaf7dd6b",
}
ROMA_LICENSE_SHA256 = {
    "LICENSE": "c0a0cf991efd3a2103c4db668a04f8d80957ae792d5be6346f899e17011ca68d",
    "NOTICE": "c0cc77ed3573488c814aea6454093c70f4604daacbb1fe8fafcc4d6acea562f7",
}
EULER_REPLACEMENTS = {
    "angles[2][case1or2] = 0":
        "angles[2] = torch.where(case1or2, torch.zeros_like(angles[2]), angles[2])",
    "angles[0][case1] = 2 * half_sum[case1]":
        "angles[0] = torch.where(case1, 2 * half_sum, angles[0])",
    "angles[0][case2] = 2 * (-1 if extrinsic else 1) * half_diff[case2]":
        "angles[0] = torch.where(case2, 2 * (-1 if extrinsic else 1) * half_diff, angles[0])",
    "foo[foo < -pi] += 2 * pi":
        "foo = torch.where(foo < -pi, foo + 2 * pi, foo)",
    "foo[foo > pi] -= 2 * pi":
        "foo = torch.where(foo > pi, foo - 2 * pi, foo)",
}


def _verify_file(path, expected, description):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(description + " identity differs")


def _compile_euler(source, original_globals):
    function = copy.deepcopy(next(node for node in ast.parse(source.read_text("utf-8")).body
                                  if isinstance(node, ast.FunctionDef) and node.name == "unitquat_to_euler"))
    replaced = []

    class FixedShape(ast.NodeTransformer):
        def visit(self, node):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                statement = ast.unparse(node)
                if statement in EULER_REPLACEMENTS:
                    replaced.append(statement)
                    return ast.copy_location(ast.parse(EULER_REPLACEMENTS[statement]).body[0], node)
            return super().visit(node)

    function = FixedShape().visit(function)
    if sorted(replaced) != sorted(EULER_REPLACEMENTS):
        raise ValueError("Euler rewrite must match exactly five pinned assignments")
    function.name = "unitquat_to_euler_fixed"
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = dict(original_globals)
    exec(compile(module, str(source), "exec"), namespace)
    return namespace[function.name]


def _verified_runtime():
    # Deliberately deferred: service/RTMPose imports must not require RoMa.
    import torch
    import roma
    import roma.euler
    import roma.internal

    directory = Path(inspect.getsourcefile(roma.rotmat_to_unitquat)).parent
    for name, digest in ROMA_SOURCE_SHA256.items():
        _verify_file(directory / name, digest, "RoMa " + name + " source")
    euler = _compile_euler(directory / "euler.py", roma.euler.unitquat_to_euler.__globals__)
    return SimpleNamespace(torch=torch, roma=roma, euler=euler)


@lru_cache(maxsize=1)
def _function_runtime():
    return _verified_runtime()


def _matrix_to_quaternion(R, torch, roma):
    matrix, batch_shape = roma.internal.flatten_batch_dims(R, end_dim=-3)
    num_rotations, D1, D2 = matrix.shape
    assert (D1, D2) == (3, 3), "Input should be a Bx3x3 tensor."
    decision_matrix = torch.empty((num_rotations, 4), dtype=matrix.dtype, device=matrix.device)
    decision_matrix[:, :3] = matrix.diagonal(dim1=1, dim2=2)
    decision_matrix[:, -1] = decision_matrix[:, :3].sum(axis=1)
    choices = decision_matrix.argmax(axis=1)

    # Preserve each original selected-branch formula. Trace-selected rows use
    # safe unused diagonal arithmetic, discarded before final normalization.
    i = choices.clamp(max=2)
    j = (i + 1) % 3
    k = (j + 1) % 3
    offsets = torch.stack((i*3+i, j*3+i, i*3+j, k*3+i, i*3+k, k*3+j, j*3+k), dim=1)
    entries = matrix.reshape(num_rotations, 9).gather(1, offsets)
    values = torch.stack((
        1 - decision_matrix[:, -1] + 2 * entries[:, 0],
        entries[:, 1] + entries[:, 2],
        entries[:, 3] + entries[:, 4],
        entries[:, 5] - entries[:, 6],
    ), dim=1)
    destinations = torch.stack((i, j, k, torch.full_like(i, 3)), dim=1)
    diagonal_quat = torch.empty_like(values).scatter(1, destinations, values)
    trace_quat = torch.stack((
        matrix[:, 2, 1] - matrix[:, 1, 2],
        matrix[:, 0, 2] - matrix[:, 2, 0],
        matrix[:, 1, 0] - matrix[:, 0, 1],
        1 + decision_matrix[:, -1],
    ), dim=1)
    quat = torch.where((choices == 3)[:, None], trace_quat, diagonal_quat)
    quat = quat / torch.norm(quat, dim=1)[:, None]
    return roma.internal.unflatten_batch_dims(quat, batch_shape)


def rotmat_to_unitquat_fixed(R):
    runtime = _function_runtime()
    return _matrix_to_quaternion(R, runtime.torch, runtime.roma)


def unitquat_to_euler_fixed(convention, quat, as_tuple=False, degrees=False, epsilon=1e-7):
    return _function_runtime().euler(convention, quat, as_tuple=as_tuple,
                                     degrees=degrees, epsilon=epsilon)


def _rotmat_to_euler(runtime, convention, rotmat, as_tuple=False, degrees=False, epsilon=1e-7):
    return runtime.euler(convention, _matrix_to_quaternion(rotmat, runtime.torch, runtime.roma),
                         as_tuple=as_tuple, degrees=degrees, epsilon=epsilon)


def rotmat_to_euler_fixed(convention, rotmat, as_tuple=False, degrees=False, epsilon=1e-7):
    return _rotmat_to_euler(_function_runtime(), convention, rotmat, as_tuple, degrees, epsilon)


class _RomaProxy:
    def __init__(self, runtime):
        self._runtime = runtime

    def rotmat_to_euler(self, convention, rotmat, as_tuple=False, degrees=False, epsilon=1e-7):
        return _rotmat_to_euler(self._runtime, convention, rotmat, as_tuple, degrees, epsilon)

    def __getattr__(self, name):
        return getattr(self._runtime.roma, name)


class RomaInstanceHandle:
    def __init__(self, head, existed, previous, receipt):
        self.head, self.existed, self.previous = head, existed, previous
        self.receipt = receipt
        self.restored = False

    def restore(self):
        if self.restored:
            return
        if self.existed:
            self.head.forward = self.previous
        else:
            delattr(self.head, "forward")
        self.restored = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.restore()


def apply_roma_fixedshape(model):
    """Bind only model.head_pose.forward; caller selects body mode and restores.

    Source validation completes before mutation. The exact original
    code, defaults, closure and annotations are retained with a private globals
    copy. Hand-head and mhr_forward methods and global RoMa remain unchanged.
    """
    head = model.head_pose
    if head.training:
        raise ValueError("Optimization requires eval mode")
    original = head.forward.__func__
    if original is not head.__class__.forward:
        raise ValueError("Body-head forward is already overridden")
    source = Path(inspect.getsourcefile(original))
    _verify_file(source, HEAD_SOURCE_SHA256, "Official body-head source")
    runtime = _verified_runtime()
    if original.__globals__.get("roma") is not runtime.roma:
        raise ValueError("Body-head RoMa global is not the expected unchanged module")
    namespace = dict(original.__globals__)
    namespace["roma"] = _RomaProxy(runtime)
    replacement = FunctionType(original.__code__, namespace, original.__name__,
                               original.__defaults__, original.__closure__)
    replacement.__kwdefaults__ = original.__kwdefaults__
    replacement.__annotations__ = original.__annotations__
    replacement.__dict__.update(original.__dict__)
    replacement.__doc__ = original.__doc__
    replacement.__module__ = original.__module__
    replacement.__qualname__ = original.__qualname__
    existed = "forward" in head.__dict__
    previous = head.__dict__.get("forward")
    receipt = {
        "candidate": "roma_fixedshape_in_body_head_forward_only",
        "scope": "body_mode_head_pose_forward",
        "head_source_sha256": HEAD_SOURCE_SHA256,
        "roma_source_sha256": dict(ROMA_SOURCE_SHA256),
        "roma_license_sha256": dict(ROMA_LICENSE_SHA256),
        "candidate_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "original_forward_code_sha256": hashlib.sha256(marshal.dumps(original.__code__)).hexdigest(),
        "original_code_defaults_closure_annotations_retained": True,
        "only_globals_entry_replaced_in_copy": "roma",
        "only_roma_proxy_attribute_overridden": "rotmat_to_euler",
        "body_head_mhr_forward_and_hand_head_unchanged": True,
        "original_global_roma_module_unchanged": True,
        "parameters_buffers_precision_and_head_count_unchanged": True,
        "license_files": ["third_party/roma/LICENSE", "third_party/roma/NOTICE"],
    }
    handle = RomaInstanceHandle(head, existed, previous, receipt)
    head.forward = MethodType(replacement, head)
    return handle
