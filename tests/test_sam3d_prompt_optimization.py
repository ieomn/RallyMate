from __future__ import annotations

import ast
from contextlib import nullcontext
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import MethodType
from typing import Any, Optional, Tuple
import unittest
from unittest.mock import patch

from rallymate_vision.sam3d_prompt_optimization import (
    OFFICIAL_SOURCE_SHA256,
    _vectorized,
    apply_prompt_vectorized,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime/sam3d/repo/sam_3d_body/models/decoders/prompt_encoder.py"
HAS_CPU_FIXTURE = (SOURCE.is_file() and importlib.util.find_spec("torch") is not None
                   and importlib.util.find_spec("numpy") is not None)


class PromptOptimizationImportTests(unittest.TestCase):
    def test_service_import_does_not_import_torch_or_sam(self):
        script = """
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'torch', 'sam_3d_body'}:
        raise AssertionError('optional model dependency imported: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import rallymate_vision.sam3d_prompt_optimization
assert 'torch' not in sys.modules
assert 'sam_3d_body' not in sys.modules
"""
        subprocess.run([sys.executable, "-c", script], check=True, capture_output=True,
                       text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})


@unittest.skipUnless(HAS_CPU_FIXTURE, "requires CPU torch and the pinned official SAM source")
class PromptOptimizationCpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        import torch
        from torch import nn

        cls.torch = torch
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        # Execute the unchanged official classes, with their real source filename
        # and LayerNorm2d dependency. Do not import or initialize the SAM package.
        namespace = dict(torch=torch, nn=nn, np=np, Any=Any, Optional=Optional, Tuple=Tuple,
                         __name__="isolated_official_prompt_optimization_test")
        transformer = SOURCE.parents[1] / "modules/transformer.py"
        layer = next(node for node in ast.parse(transformer.read_text("utf-8")).body
                     if isinstance(node, ast.ClassDef) and node.name == "LayerNorm2d")
        exec(compile(ast.Module(body=[layer], type_ignores=[]), str(transformer), "exec"), namespace)
        classes = [node for node in ast.parse(SOURCE.read_text("utf-8")).body
                   if isinstance(node, ast.ClassDef)]
        exec(compile(ast.Module(body=classes, type_ignores=[]), str(SOURCE), "exec"), namespace)
        cls.Encoder = namespace["PromptEncoder"]

    @classmethod
    def tearDownClass(cls):
        cls.torch.set_num_threads(cls.previous_threads)

    def setUp(self):
        self.torch.manual_seed(771)

    def assertBitwiseEqual(self, left, right):
        torch = self.torch
        self.assertEqual((left.shape, left.dtype, left.device), (right.shape, right.dtype, right.device))
        self.assertTrue(torch.equal(left.detach().contiguous().view(torch.uint8),
                                    right.detach().contiguous().view(torch.uint8)))

    def compare(self, model, points, labels, *, autocast=False, public=False):
        torch = self.torch

        def invoke():
            return (model(torch.cat((points, labels.unsqueeze(-1)), dim=-1)) if public
                    else model._embed_keypoints(points, labels))

        context = lambda: torch.autocast("cpu", dtype=torch.bfloat16) if autocast else nullcontext()
        with torch.no_grad(), context():
            expected = invoke()
        with apply_prompt_vectorized(model), torch.no_grad(), context():
            actual = invoke()
        for left, right in zip(expected, actual):
            self.assertBitwiseEqual(left, right)
        self.assertNotIn("_embed_keypoints", model.__dict__)

    def test_pinned_official_source_is_the_reviewed_version(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), OFFICIAL_SOURCE_SHA256)

    def test_supported_70_joint_call_reaches_optimized_math(self):
        torch = self.torch
        model = self.Encoder(32, 70).eval()
        points, labels = torch.zeros(1, 1, 2), torch.full((1, 1), -2.)

        def unexpected_fallback(_points, _labels):
            self.fail("the supported 70-joint call did not use the optimization")

        with torch.no_grad():
            expected = model._embed_keypoints(points, labels)
            actual = _vectorized(model, points, labels, unexpected_fallback)
        for left, right in zip(expected, actual):
            self.assertBitwiseEqual(left, right)

    def test_body_dummy_prompt_and_coordinate_dtypes_match_at_real_embedding_width(self):
        torch = self.torch
        model = self.Encoder(1024, 70).eval()
        for dtype in (torch.float16, torch.bfloat16, torch.float32, torch.float64):
            for autocast in (False, True):
                with self.subTest(dtype=dtype, autocast=autocast):
                    self.compare(model, torch.zeros(2, 1, 2, dtype=dtype),
                                 torch.full((2, 1), -2., dtype=dtype), autocast=autocast)
        # Public body prompts are float32. Some CPU Torch versions reject a
        # float16 torch.cat inside bfloat16 autocast before either encoder runs.
        for autocast in (False, True):
            self.compare(model, torch.zeros(2, 1, 2), torch.full((2, 1), -2.),
                         autocast=autocast, public=True)

    def test_every_legal_label_special_padding_and_unknown_labels_match(self):
        torch = self.torch
        model = self.Encoder(32, 70).eval()
        cases = {
            "all_legal": torch.arange(-2, 70, dtype=torch.float32).reshape(2, 36),
            "unknown": torch.tensor([[-100., -3., -2.5, -1.5, -.5, .5, 69.5,
                                       70., 999., float("nan"), float("inf"), -float("inf")]]),
            "mixed_padding": torch.tensor([[-2., 0., -1., 69., -2.], [3., -2., -2., 68., -1.]]),
            "int64": torch.tensor([[-3, -2, -1, 0, 69, 70]], dtype=torch.int64),
            "int32": torch.tensor([[-3, -2, -1, 0, 69, 70]], dtype=torch.int32),
            "int8": torch.tensor([[-3, -2, -1, 0, 69, 70]], dtype=torch.int8),
            "uint8": torch.tensor([[0, 69, 70, 253, 254, 255]], dtype=torch.uint8),
            "bool": torch.tensor([[False, True]]),
        }
        for name, labels in cases.items():
            points = torch.rand((*labels.shape, 2))
            points.reshape(-1)[0], points.reshape(-1)[-1] = 0., 1.
            for autocast in (False, True):
                with self.subTest(case=name, autocast=autocast):
                    self.compare(model, points, labels, autocast=autocast)

    def test_noncontiguous_inputs_keep_shape_order_and_values(self):
        torch = self.torch
        labels = torch.arange(-2, 70, dtype=torch.float32).reshape(3, 24).T
        points = torch.rand(3, 24, 2).transpose(0, 1)
        self.assertFalse(points.is_contiguous())
        self.assertFalse(labels.is_contiguous())
        self.compare(self.Encoder(32, 70).eval(), points, labels)

    def test_non70_int8_scalar_alias_counterexample_uses_original(self):
        torch = self.torch
        self.compare(self.Encoder(8, 129).eval(), torch.full((1, 5, 2), .5),
                     torch.tensor([[-128, -2, -1, 0, 127]], dtype=torch.int8))

    def test_non70_uint8_repeated_and_special_alias_counterexample_uses_original(self):
        torch = self.torch
        self.compare(self.Encoder(8, 300).eval(), torch.full((1, 5, 2), .5),
                     torch.tensor([[0, 69, 253, 254, 255]], dtype=torch.uint8))

    def test_partial_mask_shape_and_heterogeneous_weights_use_original(self):
        torch = self.torch
        model = self.Encoder(32, 70).eval()
        points = torch.rand(2, 3, 2)
        self.compare(model, points, torch.tensor([0, -2]))
        model.point_embeddings[0].to(dtype=torch.float64)
        self.compare(model, points, torch.tensor([[0, -2, 1], [4, -1, 69]]))

    def test_original_coordinate_and_empty_input_errors_are_preserved(self):
        torch = self.torch
        model = self.Encoder(32, 70).eval()
        cases = [
            (torch.empty(1, 0, 2), torch.empty(1, 0)),
            (torch.empty(0, 3, 2), torch.empty(0, 3)),
            (torch.tensor([[[-.01, .5]]]), torch.zeros(1, 1)),
            (torch.tensor([[[1.01, .5]]]), torch.zeros(1, 1)),
            (torch.tensor([[[float("nan"), .5]]]), torch.zeros(1, 1)),
        ]
        for index, (points, labels) in enumerate(cases):
            with self.subTest(case=index):
                failures = []
                for optimized in (False, True):
                    with apply_prompt_vectorized(model) if optimized else nullcontext():
                        try:
                            model._embed_keypoints(points, labels)
                        except Exception as error:
                            failures.append((type(error), str(error)))
                        else:
                            self.fail("official input rejection was lost")
                self.assertEqual(failures[0], failures[1])

    def test_no_prompt_forward_is_unchanged(self):
        model = self.Encoder(32, 70).eval()
        expected = model(None)
        with apply_prompt_vectorized(model):
            actual = model(None)
        for left, right in zip(expected, actual):
            self.assertBitwiseEqual(left, right)

    def test_live_weight_updates_are_used_without_parameter_or_buffer_replacement(self):
        torch = self.torch
        model = self.Encoder(32, 70).eval()
        parameters, buffers = dict(model.named_parameters()), dict(model.named_buffers())
        points, labels = torch.rand(1, 2, 2), torch.tensor([[4., -2.]])
        with torch.no_grad(), apply_prompt_vectorized(model):
            first = model._embed_keypoints(points, labels)
            model.point_embeddings[4].weight.add_(.25)
            model.invalid_point_embed.weight.add_(.5)
            second = model._embed_keypoints(points, labels)
        self.assertFalse(torch.equal(first[0], second[0]))
        expected = model._embed_keypoints(points, labels)
        for left, right in zip(expected, second):
            self.assertBitwiseEqual(left, right)
        self.assertTrue(all(dict(model.named_parameters())[key] is value for key, value in parameters.items()))
        self.assertTrue(all(dict(model.named_buffers())[key] is value for key, value in buffers.items()))

    def test_coordinate_and_weight_gradients_are_preserved_on_cpu(self):
        torch = self.torch
        model = self.Encoder(32, 70).eval()
        models = [copy.deepcopy(model), copy.deepcopy(model)]
        points = torch.rand(2, 5, 2)
        inputs = [points.clone().requires_grad_(True), points.clone().requires_grad_(True)]
        labels = torch.tensor([[-2., -1., 4., 4., 70.], [0., 69., 4., .5, -2.]])
        cotangent = torch.randint(-8, 9, (2, 5, 32)).float() / 8
        for optimized, (encoder, points) in enumerate(zip(models, inputs)):
            with apply_prompt_vectorized(encoder) if optimized else nullcontext():
                output, _ = encoder._embed_keypoints(points, labels)
                (output * cotangent).sum().backward()
        self.assertBitwiseEqual(inputs[0].grad, inputs[1].grad)
        for (name, left), (_, right) in zip(models[0].named_parameters(), models[1].named_parameters()):
            with self.subTest(parameter=name):
                self.assertEqual(left.grad is None, right.grad is None)
                if left.grad is not None:
                    self.assertBitwiseEqual(left.grad, right.grad)

    def test_source_hash_mismatch_does_not_install_patch(self):
        model = self.Encoder(32, 70).eval()
        with tempfile.TemporaryDirectory() as temporary:
            altered = Path(temporary) / "prompt_encoder.py"
            altered.write_bytes(SOURCE.read_bytes() + b"\n# different source\n")
            with patch("rallymate_vision.sam3d_prompt_optimization.inspect.getsourcefile", return_value=str(altered)):
                with self.assertRaisesRegex(ValueError, "source identity differs"):
                    apply_prompt_vectorized(model)
        self.assertNotIn("_embed_keypoints", model.__dict__)

    def test_training_or_missing_encoder_is_rejected_without_partial_patch(self):
        torch = self.torch
        first, second = self.Encoder(32, 70).eval(), self.Encoder(32, 70).train()
        with self.assertRaisesRegex(ValueError, "eval mode"):
            apply_prompt_vectorized(torch.nn.ModuleList([first, second]))
        self.assertNotIn("_embed_keypoints", first.__dict__)
        self.assertNotIn("_embed_keypoints", second.__dict__)
        with self.assertRaisesRegex(ValueError, "No official"):
            apply_prompt_vectorized(torch.nn.Identity())

    def test_nested_patch_is_rejected_and_exception_restores_exact_instance_dispatch(self):
        model = self.Encoder(32, 70).eval()
        original = model._embed_keypoints
        class_method = type(model)._embed_keypoints
        # A preexisting equivalent instance binding must be restored as the
        # identical bound-method object, not merely deleted.
        model._embed_keypoints = original
        with self.assertRaisesRegex(RuntimeError, "inference failure"):
            with apply_prompt_vectorized(model) as handle:
                self.assertIs(type(model)._embed_keypoints, class_method)
                with self.assertRaises(ValueError):
                    apply_prompt_vectorized(model)
                raise RuntimeError("inference failure")
        self.assertIs(model._embed_keypoints, original)
        handle.restore()
        self.assertIs(model._embed_keypoints, original)
        self.assertTrue(handle._restored)
        self.assertEqual(handle.receipt["optimized_num_body_joints"], 70)
        self.assertTrue(handle.receipt["other_joint_counts_use_original"])

    def test_different_instance_override_is_rejected(self):
        model = self.Encoder(32, 70).eval()
        original = model._embed_keypoints

        def different(_self, points, labels):
            return original(points, labels)

        override = MethodType(different, model)
        model._embed_keypoints = override
        with self.assertRaises(ValueError):
            apply_prompt_vectorized(model)
        self.assertIs(model._embed_keypoints, override)


if __name__ == "__main__":
    unittest.main()
