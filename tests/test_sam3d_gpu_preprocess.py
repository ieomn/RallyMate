from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Callable, Dict, List, Optional, Tuple, Union
import unittest
from unittest.mock import patch

from rallymate_vision.sam3d_gpu_preprocess import (
    OFFICIAL_ESTIMATOR_SHA256, OFFICIAL_PREPARE_BATCH_SHA256, OFFICIAL_TRANSFORMS_SHA256,
    _DeviceToTensor, apply_gpu_preprocess,
)

ROOT = Path(__file__).resolve().parents[1]
SAM = ROOT / 'runtime/sam3d/repo/sam_3d_body'
HAS_FIXTURE = (SAM.is_dir() and importlib.util.find_spec('torch') is not None
               and importlib.util.find_spec('torchvision') is not None)


class ImportTests(unittest.TestCase):
    def test_optional_model_dependencies_are_lazy(self):
        code = """
import builtins, sys
original = builtins.__import__
def guard(name, *args, **kwargs):
    if name.split('.')[0] in {'torch', 'torchvision', 'numpy', 'sam_3d_body'}:
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guard
import rallymate_vision.sam3d_gpu_preprocess
"""
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True,
                       env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})


@unittest.skipUnless(HAS_FIXTURE, 'requires local official source and CPU torch/torchvision')
class CPUParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cv2
        import numpy as np
        import torch
        from torch.utils.data import default_collate
        from torchvision.transforms import ToTensor

        cls.torch, cls.np, cls.ToTensor = torch, np, ToTensor
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        cls.modules = []

        def load_file(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            cls.modules.append(name)
            spec.loader.exec_module(module)
            return module

        bbox = load_file('_gpu_preprocess_bbox_fixture', SAM / 'data/transforms/bbox_utils.py')
        prepare = load_file('_gpu_preprocess_prepare_fixture', SAM / 'data/utils/prepare_batch.py')
        cls.prepare_batch = staticmethod(prepare.prepare_batch)
        common_path = SAM / 'data/transforms/common.py'
        common = ModuleType('_gpu_preprocess_common_fixture')
        common.__file__ = str(common_path)
        sys.modules[common.__name__] = common
        cls.modules.append(common.__name__)
        common.__dict__.update(dict(cv2=cv2, np=np, nn=torch.nn, Callable=Callable,
            Dict=Dict, List=List, Optional=Optional, Tuple=Tuple, Union=Union,
            to_2tuple=lambda value: value if isinstance(value, (list, tuple)) else (value, value)))
        for name in ('bbox_cs2xyxy', 'bbox_xywh2cs', 'bbox_xyxy2cs', 'fix_aspect_ratio',
                     'get_udp_warp_matrix', 'get_warp_matrix'):
            common.__dict__[name] = getattr(bbox, name)
        needed = {'Compose', 'VisionTransformWrapper', 'GetBBoxCenterScale', 'TopdownAffine'}
        nodes = [node for node in ast.parse(common_path.read_text('utf-8')).body
                 if isinstance(node, ast.ClassDef) and node.name in needed]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(common_path), 'exec'), common.__dict__)
        cls.common = common

        estimator_path = SAM / 'sam_3d_body_estimator.py'
        estimator = ModuleType('_gpu_preprocess_estimator_fixture')
        estimator.__file__ = str(estimator_path)
        sys.modules[estimator.__name__] = estimator
        cls.modules.append(estimator.__name__)
        estimator.__dict__.update(dict(torch=torch, np=np, Optional=Optional, Union=Union,
                                      prepare_batch=prepare.prepare_batch))
        node = next(node for node in ast.parse(estimator_path.read_text('utf-8')).body
                    if isinstance(node, ast.ClassDef) and node.name == 'SAM3DBodyEstimator')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(estimator_path), 'exec'), estimator.__dict__)
        cls.Estimator = estimator.SAM3DBodyEstimator

    @classmethod
    def tearDownClass(cls):
        cls.torch.set_num_threads(cls.previous_threads)
        for name in cls.modules:
            sys.modules.pop(name, None)

    def assertTensorExact(self, expected, actual):
        torch = self.torch
        self.assertEqual((expected.shape, expected.dtype, expected.stride()),
                         (actual.shape, actual.dtype, actual.stride()))
        self.assertTrue(torch.equal(expected.contiguous().view(torch.uint8),
                                    actual.contiguous().view(torch.uint8)))

    def new_estimator(self):
        common = self.common
        value = self.Estimator.__new__(self.Estimator)
        value.device = 'cuda'
        value.model = SimpleNamespace(training=False)
        value.transform = common.Compose([
            common.GetBBoxCenterScale(), common.TopdownAffine(input_size=512, use_udp=False),
            common.VisionTransformWrapper(self.ToTensor()),
        ])
        value.transform_hand = object()
        return value

    def cpu_binding(self, estimator):
        # Exercise public installation/identity/restore without touching CUDA.
        def factory(original, device, receipt):
            self.assertEqual(device.type, 'cuda')
            return _DeviceToTensor(original, 'cpu', receipt)
        with patch('rallymate_vision.sam3d_gpu_preprocess._DeviceToTensor', side_effect=factory):
            return apply_gpu_preprocess(estimator)

    def test_pinned_source_identities(self):
        paths = {
            'sam_3d_body_estimator.py': OFFICIAL_ESTIMATOR_SHA256,
            'data/utils/prepare_batch.py': OFFICIAL_PREPARE_BATCH_SHA256,
            'data/transforms/common.py': OFFICIAL_TRANSFORMS_SHA256,
        }
        for path, expected in paths.items():
            self.assertEqual(hashlib.sha256((SAM / path).read_bytes()).hexdigest(), expected)

    def test_every_uint8_value_and_chw_layout_are_exact(self):
        np = self.np
        pixels = np.tile(np.arange(256, dtype=np.uint8).reshape(16, 16, 1), (1, 1, 3))
        receipt = {}
        converter = _DeviceToTensor(self.ToTensor(), 'cpu', receipt)
        self.assertTensorExact(self.ToTensor()(pixels), converter(pixels))
        self.assertEqual(receipt['optimized_calls'], 1)
        self.assertEqual(receipt['uploaded_image_bytes'], pixels.nbytes)
        self.assertTrue(receipt['uint8_normalization_all_values_verified'])
        converter.close()

    def test_lookup_fallback_remains_exact(self):
        pixels = self.np.random.default_rng(71).integers(0, 256, (513, 511, 3), dtype=self.np.uint8)
        converter = _DeviceToTensor(self.ToTensor(), 'cpu', {})
        converter._direct_division_exact = False
        self.assertTensorExact(self.ToTensor()(pixels), converter(pixels))

    def test_unusual_layout_and_float_input_use_original(self):
        np = self.np
        original = self.ToTensor()
        receipt = {}
        converter = _DeviceToTensor(original, 'cpu', receipt)
        pixels = np.random.default_rng(92).integers(0, 256, (10, 15, 3), dtype=np.uint8)
        for value in (pixels.transpose(1, 0, 2), pixels.astype(np.float32), pixels[:, :, 0]):
            self.assertTensorExact(original(value), converter(value))
        self.assertEqual(receipt['original_fallback_calls'], 3)
        self.assertEqual(receipt['optimized_calls'], 0)

    def test_changed_default_dtype_preserves_original(self):
        torch, np = self.torch, self.np
        converter = _DeviceToTensor(self.ToTensor(), 'cpu', {})
        pixels = np.full((3, 4, 3), 141, dtype=np.uint8)
        old = torch.get_default_dtype()
        try:
            torch.set_default_dtype(torch.float64)
            self.assertTensorExact(self.ToTensor()(pixels), converter(pixels))
        finally:
            torch.set_default_dtype(old)

    def test_real_official_crop_and_batch_values_remain_exact(self):
        np = self.np
        estimator = self.new_estimator()
        original, hand = estimator.transform, estimator.transform_hand
        image = np.random.default_rng(311).integers(0, 256, (544, 960, 3), dtype=np.uint8)
        boxes = np.array([[115.25, 84.5, 267.75, 531.5], [-14.25, -30.0, 391.5, 422.0]], dtype=np.float32)
        expected = self.prepare_batch(image, original, boxes)
        with self.cpu_binding(estimator) as handle:
            self.assertIs(estimator.transform.transforms[0], original.transforms[0])
            self.assertIs(estimator.transform.transforms[1], original.transforms[1])
            self.assertIs(estimator.transform_hand, hand)
            actual = self.prepare_batch(image, estimator.transform, boxes)
            self.assertEqual(set(expected), set(actual))
            for key, value in expected.items():
                if isinstance(value, self.torch.Tensor):
                    self.assertTensorExact(value, actual[key])
                elif key == 'img_ori':
                    self.assertIs(actual[key][0].data, image)
                else:
                    self.assertEqual(value, actual[key])
            self.assertEqual(handle.receipt['optimized_calls'], 2)
        self.assertIs(estimator.transform, original)
        handle.restore()

    def test_changed_source_or_nested_install_is_rejected_without_mutation(self):
        estimator = self.new_estimator()
        original = estimator.transform
        with patch('rallymate_vision.sam3d_gpu_preprocess.OFFICIAL_TRANSFORMS_SHA256', 'invalid'):
            with self.assertRaisesRegex(ValueError, 'source identity differs'):
                self.cpu_binding(estimator)
        self.assertIs(estimator.transform, original)
        with self.cpu_binding(estimator):
            active = estimator.transform
            with self.assertRaisesRegex(ValueError, 'composition differs'):
                self.cpu_binding(estimator)
            self.assertIs(estimator.transform, active)

    def test_training_non_body_and_cpu_estimator_are_rejected(self):
        estimator = self.new_estimator()
        with self.assertRaisesRegex(ValueError, 'only for body'):
            apply_gpu_preprocess(estimator, inference_type='full')
        estimator.model.training = True
        with self.assertRaisesRegex(ValueError, 'eval mode'):
            apply_gpu_preprocess(estimator)
        estimator.model.training = False
        estimator.device = 'cpu'
        with self.assertRaisesRegex(ValueError, 'CUDA estimator'):
            apply_gpu_preprocess(estimator)


if __name__ == '__main__':
    unittest.main()
