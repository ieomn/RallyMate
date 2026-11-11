from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rallymate_vision.sam3d_batch import (
    BodyBatchInference, OUTPUT_FIELDS, SOURCE_HASHES, _merge_prepared_batches, _source_hash,
)


ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / "runtime/sam3d/repo/sam_3d_body/data/utils/prepare_batch.py"
HAS_FIXTURE = PREPARE.is_file() and importlib.util.find_spec("torch") is not None


class ImportTests(unittest.TestCase):
    def test_model_dependencies_are_lazy(self):
        source = """
import builtins
original = builtins.__import__
def guard(name, *args, **kwargs):
    if name.split('.')[0] in {'torch', 'numpy', 'sam_3d_body'}:
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guard
import rallymate_vision.sam3d_batch
"""
        subprocess.run([sys.executable, "-c", source], check=True, capture_output=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src")})


@unittest.skipUnless(HAS_FIXTURE, "requires pinned local prepare_batch and CPU torch")
class BodyBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        import torch

        cls.np, cls.torch = np, torch
        spec = importlib.util.spec_from_file_location("_sam_batch_prepare_fixture", PREPARE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.prepare = staticmethod(module.prepare_batch)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("_sam_batch_prepare_fixture", None)

    def setUp(self):
        np, torch = self.np, self.torch
        test = self

        def recursive(value, target):
            if isinstance(value, dict):
                return {key: recursive(item, target) for key, item in value.items()}
            if isinstance(value, list):
                return [recursive(item, target) for item in value]
            if isinstance(value, torch.Tensor):
                return value.numpy() if target == "numpy" else value.to(target)
            return value

        def transform(data):
            data = dict(data)
            h, w = data["img"].shape[:2]
            data["ori_img_size"] = np.asarray([w, h])
            data["img_size"] = np.asarray([2, 2])
            data["img"] = torch.from_numpy(data["img"][:2, :2].copy()).permute(2, 0, 1).float() / 255
            data["bbox_center"] = (data["bbox"][:2] + data["bbox"][2:]) / 2
            data["bbox_scale"] = data["bbox"][2:] - data["bbox"][:2]
            data["affine_trans"] = np.array([[1., 0., 0.], [0., 1., 0.]], np.float32)
            data["mask"] = np.zeros((2, 2), np.uint8)
            return data

        class Model:
            training = False
            calls = 0
            output_count_offset = 0

            def _initialize_batch(self, batch):
                self.initialized_shape = tuple(batch["img"].shape[:2])

            def run_inference(self, image, batch, **kwargs):
                test.assertFalse(torch.is_grad_enabled())
                test.assertEqual(kwargs["inference_type"], "body")
                test.assertEqual(tuple(batch["img"].shape[:2]), self.initialized_shape)
                self.calls += 1
                self.batch = batch
                # Output depends on each sample's original color, bbox and
                # camera, so broadcasting sample zero breaks parity tests.
                marker = batch["img"][:, 0, 0, 0, 0] + batch["cam_int"][:, 0, 2] + batch["bbox"][:, 0, 0]
                count = len(marker) + self.output_count_offset
                self.native = {field: marker[:count, None].repeat(1, 3) for field in OUTPUT_FIELDS.values()}
                return {"mhr": self.native}

        self.model = Model()
        self.estimator = SimpleNamespace(model=self.model, device="cpu", fov_estimator=None,
                                         transform=transform, transform_hand=object(), thresh_wrist_angle=1.4)
        self.runtime = patch("rallymate_vision.sam3d_batch._verified_runtime",
                             return_value=(torch, self.prepare, recursive))
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.runner = BodyBatchInference(self.estimator)
        self.images = [np.full((10, 20, 3), (10, 20, 30), np.uint8),
                       np.full((14, 12, 3), (50, 60, 70), np.uint8)]
        self.requests = [(self.images[0], [1., 2., 19., 9.]), (self.images[1], [3., 1., 11., 13.])]

    def test_true_one_forward_preserves_per_image_camera_pixels_and_boxes(self):
        outputs = self.runner.infer(self.requests, clear_cuda_cache=False)
        self.assertEqual(self.model.calls, 1)
        self.assertEqual(self.model.initialized_shape, (2, 1))
        self.assertEqual(self.runner.receipt["subjects"], 2)
        batch = self.model.batch
        self.np.testing.assert_array_equal(batch["ori_img_size"].numpy(), [[[20, 10]], [[12, 14]]])
        for index, (frame, box) in enumerate(self.requests):
            camera = batch["cam_int"][index].numpy()
            h, w = frame.shape[:2]
            self.assertAlmostEqual(float(camera[0, 0]), (h * h + w * w) ** .5, places=5)
            self.np.testing.assert_array_equal(camera[:2, 2], [w / 2, h / 2])
            self.np.testing.assert_array_equal(batch["img_ori"][index].data, frame[:, :, ::-1])
            self.np.testing.assert_array_equal(outputs[index]["bbox"], box)
        self.assertEqual(set(outputs[0]), set(OUTPUT_FIELDS) | {"bbox", "mask"})
        self.assertIsNone(outputs[0]["mask"])

    def test_default_retains_allocator_cache(self):
        with patch.object(self.torch.cuda, "empty_cache") as release:
            self.runner.infer(self.requests)
        release.assert_not_called()

    def test_single_and_batch_output_binding_are_identical_with_mixed_image_shapes(self):
        single = [self.runner.infer([request], clear_cuda_cache=False)[0] for request in self.requests]
        together = self.runner.infer(self.requests, clear_cuda_cache=False)
        for expected, actual in zip(single, together):
            for field in expected:
                self.np.testing.assert_array_equal(actual[field], expected[field])

    def test_returned_slots_do_not_alias_batch_or_later_inference_storage(self):
        outputs = self.runner.infer(self.requests, clear_cuda_cache=False)
        snapshot = outputs[0]["pred_vertices"].copy()
        self.model.native["pred_vertices"].fill_(-1)
        self.runner.infer(list(reversed(self.requests)), clear_cuda_cache=False)
        self.np.testing.assert_array_equal(outputs[0]["pred_vertices"], snapshot)
        self.assertIsNone(outputs[0]["pred_vertices"].base)
        self.assertIsNone(outputs[0]["bbox"].base)

    def test_count_mismatch_raises_before_any_slot_is_returned(self):
        self.model.output_count_offset = -1
        with self.assertRaisesRegex(ValueError, "subject count"):
            self.runner.infer(self.requests, clear_cuda_cache=False)
        self.assertFalse(self.runner.lock.locked())

    def test_invalid_requests_do_not_call_model_or_drop_a_subject(self):
        cases = [[], self.requests * 5, [(self.images[0].astype(float), [1, 2, 3, 4])],
                 [(self.images[0], [1, 2, 30, 4])], [(self.images[0], [1, 2, self.np.nan, 4])]]
        for requests in cases:
            with self.subTest(requests=len(requests)), self.assertRaises(ValueError):
                self.runner.infer(requests, clear_cuda_cache=False)
        self.assertEqual(self.model.calls, 0)

    def test_training_or_fov_change_and_concurrent_use_are_rejected(self):
        self.model.training = True
        with self.assertRaisesRegex(ValueError, "contract"):
            self.runner.infer(self.requests)
        self.model.training = False
        self.estimator.fov_estimator = object()
        with self.assertRaisesRegex(ValueError, "contract"):
            self.runner.infer(self.requests)
        self.estimator.fov_estimator = None
        self.runner.lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, "Concurrent"):
                self.runner.infer(self.requests)
        finally:
            self.runner.lock.release()

    def test_official_prepare_fixture_identity_and_invalid_collation(self):
        self.assertEqual(_source_hash(self.prepare), SOURCE_HASHES["prepare_batch"])
        batches = [self.prepare(frame[:, :, ::-1].copy(), self.estimator.transform,
                                self.np.asarray([box], dtype=self.np.float32)) for frame, box in self.requests]
        batches[1]["cam_int"] = batches[0]["cam_int"].repeat(2, 1, 1)
        with self.assertRaisesRegex(ValueError, "own camera"):
            _merge_prepared_batches(batches, self.torch)


if __name__ == "__main__":
    unittest.main()
