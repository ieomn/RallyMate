from __future__ import annotations

import ast
import hashlib
import importlib.util
import itertools
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import MethodType
from typing import Optional
import unittest
from unittest.mock import patch

from rallymate_vision import sam3d_roma_optimization as optimization


ROOT = Path(__file__).resolve().parents[1]
MODEL_SOURCE = ROOT / "runtime/sam3d/repo/sam_3d_body/models"
HEAD_SOURCE = MODEL_SOURCE / "heads/mhr_head.py"
HAS_CPU_RUNTIME = (importlib.util.find_spec("torch") is not None
                   and importlib.util.find_spec("roma") is not None)


class RomaOptimizationImportTests(unittest.TestCase):
    def test_import_has_no_optional_model_dependencies(self):
        script = """
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'torch', 'roma', 'sam_3d_body'}:
        raise AssertionError('optional model dependency imported: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import rallymate_vision.sam3d_roma_optimization
assert not {'torch', 'roma', 'sam_3d_body'}.intersection(sys.modules)
"""
        subprocess.run([sys.executable, "-c", script], check=True, capture_output=True,
                       text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})

    def test_complete_roma_and_scipy_licenses_are_retained_byte_for_byte(self):
        for name, digest in optimization.ROMA_LICENSE_SHA256.items():
            with self.subTest(license=name):
                path = ROOT / "third_party/roma" / name
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        notice = (ROOT / "third_party/roma/NOTICE").read_text("utf-8")
        self.assertIn("SciPy", notice)
        self.assertIn("THIS SOFTWARE IS PROVIDED", notice)


@unittest.skipUnless(HAS_CPU_RUNTIME, "requires CPU Torch and pinned RoMa installation")
class _RomaCpuTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        import roma
        cls.torch, cls.roma = torch, roma
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(1)

    @classmethod
    def tearDownClass(cls):
        optimization._function_runtime.cache_clear()
        cls.torch.set_num_threads(cls.previous_threads)

    def setUp(self):
        self.torch.manual_seed(771)

    def assertBitwiseEqual(self, left, right):
        if isinstance(left, tuple):
            self.assertIsInstance(right, tuple)
            self.assertEqual(len(left), len(right))
            for a, b in zip(left, right):
                self.assertBitwiseEqual(a, b)
            return
        torch = self.torch
        self.assertEqual((left.shape, left.dtype, left.device), (right.shape, right.dtype, right.device))
        self.assertTrue(torch.equal(left.detach().contiguous().view(torch.uint8),
                                    right.detach().contiguous().view(torch.uint8)))

    def rotations(self, dtype):
        torch, roma = self.torch, self.roma
        random = torch.randn(1024, 4, dtype=dtype)
        random /= random.norm(dim=-1, keepdim=True)
        basis = torch.eye(4, dtype=dtype)
        angles = torch.tensor([0., 1e-30, 1e-12, 1e-8, 1e-7,
                               math.pi-1e-7, math.pi, math.pi+1e-7], dtype=dtype)
        vectors = torch.zeros(len(angles), 3, dtype=dtype)
        vectors[:, 0] = angles
        return torch.cat((random, -random, basis, -basis, roma.rotvec_to_unitquat(vectors),
                          torch.tensor([[.5, .5, .5, .5], [-.5, -.5, -.5, .5]], dtype=dtype)))


class RomaOptimizationMathTests(_RomaCpuTestCase):
    def test_all_four_quaternion_branches_ties_and_batch_layouts_are_bitwise_equal(self):
        torch, roma = self.torch, self.roma
        observed = set()
        for dtype in (torch.float32, torch.float64):
            matrices = roma.unitquat_to_rotmat(self.rotations(dtype))
            diagonal = matrices.diagonal(dim1=-2, dim2=-1)
            choices = torch.cat((diagonal, diagonal.sum(-1, keepdim=True)), dim=-1).argmax(-1)
            observed.update(choices.tolist())
            samples = (matrices, matrices[0], matrices[:24].reshape(2, 3, 4, 3, 3), matrices[:0],
                       matrices[:12].transpose(0, 1).contiguous().transpose(0, 1))
            for sample in samples:
                with self.subTest(dtype=dtype, shape=sample.shape):
                    self.assertBitwiseEqual(roma.rotmat_to_unitquat(sample),
                                             optimization.rotmat_to_unitquat_fixed(sample))
        self.assertEqual(observed, {0, 1, 2, 3})

    def test_all_conventions_singularities_pi_epsilon_degrees_and_tuples_match(self):
        torch, roma = self.torch, self.roma
        conventions = ["".join(x) for x in itertools.product("xyz", repeat=3)
                       if x[0] != x[1] and x[1] != x[2]]
        conventions += [item.upper() for item in conventions]
        for dtype in (torch.float32, torch.float64):
            quaternions = self.rotations(dtype)
            for convention in conventions:
                symmetric = convention[0] == convention[2]
                centers = [0., math.pi] if symmetric else [-math.pi/2, math.pi/2]
                angles = [[outer, center+delta, -outer] for center, outer, delta in itertools.product(
                    centers, [-math.pi, 0., math.pi], [-2e-7, -1e-7, -.5e-7, 0., .5e-7, 1e-7, 2e-7])]
                angles = torch.tensor(angles, dtype=dtype)
                edge = torch.tensor([[math.pi, 0. if symmetric else math.pi/2, -math.pi]], dtype=dtype)
                angles = torch.cat((angles, edge,
                    torch.nextafter(edge, torch.full_like(edge, math.inf)),
                    torch.nextafter(edge, torch.full_like(edge, -math.inf))))
                boundary = roma.euler_to_unitquat(convention, angles)
                inputs = torch.cat((quaternions, boundary, -boundary))
                for epsilon, degrees, as_tuple in itertools.product((0., 1e-7, 1e-5), (False, True), (False, True)):
                    with self.subTest(dtype=dtype, convention=convention, epsilon=epsilon,
                                      degrees=degrees, as_tuple=as_tuple):
                        options = dict(epsilon=epsilon, degrees=degrees, as_tuple=as_tuple)
                        self.assertBitwiseEqual(roma.unitquat_to_euler(convention, inputs, **options),
                            optimization.unitquat_to_euler_fixed(convention, inputs, **options))
                matrices = roma.unitquat_to_rotmat(inputs)
                for sample in (matrices, matrices[0], matrices[:0]):
                    self.assertBitwiseEqual(roma.rotmat_to_euler(convention, sample),
                                             optimization.rotmat_to_euler_fixed(convention, sample))

    def test_gradients_away_from_singularities_match(self):
        torch, roma = self.torch, self.roma
        functions = ((roma.rotmat_to_unitquat, optimization.rotmat_to_unitquat_fixed, True),
            (lambda x: roma.unitquat_to_euler("ZYX", x),
             lambda x: optimization.unitquat_to_euler_fixed("ZYX", x), False))
        for before, after, matrix_input in functions:
            quaternions = torch.randn(32, 4, dtype=torch.float64)
            quaternions /= quaternions.norm(dim=-1, keepdim=True)
            base = roma.unitquat_to_rotmat(quaternions) if matrix_input else quaternions
            inputs = [base.clone().requires_grad_(True), base.clone().requires_grad_(True)]
            for values, function in zip(inputs, (before, after)):
                function(values).square().sum().backward()
            self.assertBitwiseEqual(inputs[0].grad, inputs[1].grad)

    def test_malformed_matrix_and_convention_errors_are_preserved(self):
        torch, roma = self.torch, self.roma
        pairs = ((lambda: roma.rotmat_to_unitquat(torch.eye(2)),
                  lambda: optimization.rotmat_to_unitquat_fixed(torch.eye(2))),
                 (lambda: roma.unitquat_to_euler("xxx", torch.tensor([0., 0., 0., 1.])),
                  lambda: optimization.unitquat_to_euler_fixed("xxx", torch.tensor([0., 0., 0., 1.]))))
        for original, changed in pairs:
            errors = []
            for call in (original, changed):
                try:
                    call()
                except Exception as exc:
                    errors.append((type(exc), str(exc)))
                else:
                    self.fail("expected original rejection")
            self.assertEqual(errors[0], errors[1])

    def test_cpu_operator_trace_removes_nonzero_without_gpu_activity(self):
        torch, roma = self.torch, self.roma
        counts = []
        for function in (roma.rotmat_to_euler, optimization.rotmat_to_euler_fixed):
            with torch.no_grad(), torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU]) as prof:
                function("ZYX", torch.eye(3).unsqueeze(0))
            counts.append(sum(event.count for event in prof.key_averages() if event.key == "aten::nonzero"))
        self.assertEqual(counts, [18, 0])

    def test_euler_rewrite_requires_all_five_exact_assignments(self):
        roma = self.roma
        import inspect
        source = Path(inspect.getsourcefile(roma.euler.unitquat_to_euler))
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "euler.py"
            changed.write_text(source.read_text("utf-8").replace("angles[2][case1or2] = 0",
                                                                "angles[2][case1or2] = 1"), "utf-8")
            with self.assertRaisesRegex(ValueError, "exactly five"):
                optimization._compile_euler(changed, roma.euler.unitquat_to_euler.__globals__)


@unittest.skipUnless(HAS_CPU_RUNTIME and HEAD_SOURCE.is_file(),
                     "requires CPU Torch/RoMa and pinned official head source")
class RomaOptimizationHookTests(_RomaCpuTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        torch, roma = cls.torch, cls.roma
        import torch.nn.functional as F
        spec = importlib.util.spec_from_file_location("isolated_roma_test_mhr_utils", MODEL_SOURCE / "modules/mhr_utils.py")
        utils = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(utils)
        namespace = dict(torch=torch, F=F, roma=roma, Optional=Optional,
            compact_cont_to_model_params_body=utils.compact_cont_to_model_params_body,
            mhr_param_hand_mask=utils.mhr_param_hand_mask)
        classes = ast.parse(HEAD_SOURCE.read_text("utf-8")).body
        head = next(node for node in classes if isinstance(node, ast.ClassDef) and node.name == "MHRHead")
        forward = next(node for node in head.body if isinstance(node, ast.FunctionDef) and node.name == "forward")
        geometry = next(node for node in ast.parse((MODEL_SOURCE / "modules/geometry_utils.py").read_text("utf-8")).body
                        if isinstance(node, ast.FunctionDef) and node.name == "rot6d_to_rotmat")
        exec(compile(ast.Module(body=[geometry, forward], type_ignores=[]), str(HEAD_SOURCE), "exec"), namespace)

        class MHRHead(torch.nn.Module):
            forward = namespace["forward"]

            def __init__(self):
                super().__init__()
                self.body_cont_dim, self.num_shape_comps, self.num_scale_comps = 260, 45, 28
                self.num_hand_comps, self.num_face_comps = 54, 72
                self.proj = torch.nn.Linear(4, 519)
                self.faces = torch.nn.Parameter(torch.arange(12).reshape(4, 3), requires_grad=False)
                self.calls = 0

            def mhr_forward(self, **kwargs):
                # Deliberately synthetic backend: tests the real head method's
                # numerical arguments/output wiring, not pretrained MHR parity.
                self.calls += 1
                values = [value.reshape(value.shape[0], -1) for value in kwargs.values()
                          if isinstance(value, torch.Tensor)]
                params = torch.cat(values, dim=1)
                base, batch = params.sum(dim=1).reshape(-1, 1, 1), params.shape[0]
                return (base.expand(batch, 8, 3).clone(), base.expand(batch, 308, 3).clone(),
                        base.expand(batch, 127, 3).clone(), params,
                        torch.eye(3).expand(batch, 127, 3, 3).clone())

        cls.Head = MHRHead

    def model(self):
        model = self.torch.nn.Module()
        model.head_pose, model.head_pose_hand = self.Head(), self.Head()
        return model.eval()

    def test_head_scope_code_globals_parameters_and_outputs_are_preserved(self):
        import numpy as np
        model, torch, roma = self.model(), self.torch, self.roma
        original = model.head_pose.forward.__func__
        globals_before = dict(original.__globals__)
        roma_before = dict(vars(roma))
        hand, mhr = model.head_pose_hand.forward.__func__, model.head_pose.mhr_forward.__func__
        parameters, states = dict(model.named_parameters()), {k: v.clone() for k, v in model.state_dict().items()}
        inputs = [torch.randn(batch, 4) for batch in (1, 2) for _ in range(6)]
        with torch.no_grad():
            expected = [model.head_pose(value) for value in inputs]
            with optimization.apply_roma_fixedshape(model) as handle:
                replacement = model.head_pose.forward.__func__
                for name in ("__code__", "__defaults__", "__kwdefaults__", "__closure__", "__annotations__"):
                    self.assertIs(getattr(replacement, name), getattr(original, name))
                self.assertEqual(replacement.__globals__.keys(), globals_before.keys())
                self.assertTrue(all(replacement.__globals__[key] is value for key, value in globals_before.items() if key != "roma"))
                self.assertIs(model.head_pose_hand.forward.__func__, hand)
                self.assertIs(model.head_pose.mhr_forward.__func__, mhr)
                actual = [model.head_pose(value) for value in inputs]
        self.assertTrue(handle.restored)
        for old, new in zip(expected, actual):
            self.assertEqual(old.keys(), new.keys())
            for key, value in old.items():
                if isinstance(value, torch.Tensor):
                    self.assertBitwiseEqual(value, new[key])
                elif isinstance(value, np.ndarray):
                    self.assertTrue(np.array_equal(value, new[key]))
                else:
                    self.assertEqual(value, new[key])
        self.assertEqual(model.head_pose.calls, 24)
        self.assertTrue(all(dict(model.named_parameters())[key] is value for key, value in parameters.items()))
        for key, value in states.items():
            self.assertBitwiseEqual(value, model.state_dict()[key])
        self.assertEqual(original.__globals__.keys(), globals_before.keys())
        self.assertTrue(all(original.__globals__[key] is value for key, value in globals_before.items()))
        self.assertEqual(vars(roma).keys(), roma_before.keys())
        self.assertTrue(all(vars(roma)[key] is value for key, value in roma_before.items()))
        self.assertNotIn("forward", model.head_pose.__dict__)

    def test_exception_restores_identical_preexisting_binding_and_is_idempotent(self):
        model = self.model()
        original = model.head_pose.forward
        model.head_pose.forward = original
        with self.assertRaisesRegex(RuntimeError, "inference failure"):
            with optimization.apply_roma_fixedshape(model) as handle:
                with self.assertRaisesRegex(ValueError, "already overridden"):
                    optimization.apply_roma_fixedshape(model)
                raise RuntimeError("inference failure")
        self.assertIs(model.head_pose.forward, original)
        handle.restore()
        self.assertIs(model.head_pose.forward, original)

    def test_source_mismatch_rejects_before_mutation(self):
        model = self.model()
        failures = ((optimization.ROMA_SOURCE_SHA256, "mappings.py"),
                    (optimization.ROMA_SOURCE_SHA256, "euler.py"))
        for constants, key in failures:
            with self.subTest(source=key), patch.dict(constants, {key: "0"*64}):
                with self.assertRaisesRegex(ValueError, "identity differs"):
                    optimization.apply_roma_fixedshape(model)
                self.assertNotIn("forward", model.head_pose.__dict__)
        with patch.object(optimization, "HEAD_SOURCE_SHA256", "0"*64):
            with self.assertRaisesRegex(ValueError, "identity differs"):
                optimization.apply_roma_fixedshape(model)
        self.assertNotIn("forward", model.head_pose.__dict__)

    def test_distribution_license_packaging_is_not_a_runtime_gate(self):
        model = self.model()
        with patch("importlib.metadata.distribution", side_effect=AssertionError("packaging queried")):
            with optimization.apply_roma_fixedshape(model) as handle:
                self.assertEqual(handle.receipt["roma_license_sha256"], optimization.ROMA_LICENSE_SHA256)
        self.assertTrue(handle.restored)

    def test_training_override_or_wrong_global_are_rejected(self):
        model = self.model()
        model.head_pose.train()
        with self.assertRaisesRegex(ValueError, "eval mode"):
            optimization.apply_roma_fixedshape(model)
        model.head_pose.eval()
        model.head_pose.forward = MethodType(lambda self, value: value, model.head_pose)
        original = model.head_pose.forward
        with self.assertRaisesRegex(ValueError, "already overridden"):
            optimization.apply_roma_fixedshape(model)
        self.assertIs(model.head_pose.forward, original)
        delattr(model.head_pose, "forward")
        with patch.dict(model.head_pose.forward.__func__.__globals__, {"roma": object()}):
            with self.assertRaisesRegex(ValueError, "expected unchanged module"):
                optimization.apply_roma_fixedshape(model)
        self.assertNotIn("forward", model.head_pose.__dict__)


if __name__ == "__main__":
    unittest.main()
