from __future__ import annotations

from contextlib import contextmanager
import os
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rallymate_vision.sam3d_batch import BodyBatchInference, OUTPUT_FIELDS, _merge_prepared_batches
from rallymate_vision.sam3d_batch_preprocess import BatchCropPreprocessor, _BatchUint8ToTensor
from tests import test_sam3d_gpu_preprocess as fixtures


class ImportTests(unittest.TestCase):
    def test_import_is_lazy_without_numpy_torch_or_sam(self):
        code = """
import builtins
original = builtins.__import__
def guard(name, *args, **kwargs):
    if name.split('.')[0] in {'torch', 'numpy', 'torchvision', 'sam_3d_body'}:
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guard
import rallymate_vision.sam3d_batch_preprocess
"""
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True,
                       env={**os.environ, "PYTHONPATH": str(fixtures.ROOT / "src")})


@unittest.skipUnless(fixtures.HAS_FIXTURE, "requires pinned official crop and CPU Torch")
class BatchCropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.CPUParityTests.setUpClass()
        cls.torch, cls.np, cls.ToTensor = (fixtures.CPUParityTests.torch,
                                         fixtures.CPUParityTests.np, fixtures.CPUParityTests.ToTensor)
        cls.prepare_batch = staticmethod(fixtures.CPUParityTests.prepare_batch)

    @classmethod
    def tearDownClass(cls):
        fixtures.CPUParityTests.tearDownClass()

    def estimator(self):
        return fixtures.CPUParityTests().new_estimator()

    def converter(self, *, maximum=8):
        converter = _BatchUint8ToTensor(self.ToTensor(), "cpu", {}, max_subjects=maximum)
        self.addCleanup(converter.close)
        return converter

    def cpu_preprocessor(self, estimator):
        def factory(original, device, receipt):
            self.assertEqual(str(device), "cuda")
            return _BatchUint8ToTensor(original, "cpu", receipt)
        with patch("rallymate_vision.sam3d_batch_preprocess._BatchUint8ToTensor", side_effect=factory):
            processor = BatchCropPreprocessor(estimator)
        self.addCleanup(processor.close)
        return processor

    def assertExact(self, first, second):
        self.assertEqual((first.shape, first.dtype, first.stride()),
                         (second.shape, second.dtype, second.stride()))
        self.assertTrue(self.torch.equal(first, second))

    def test_all_uint8_values_normalize_exactly_in_one_group(self):
        np, torch = self.np, self.torch
        image = np.tile(np.arange(256, dtype=np.uint8).reshape(16, 16, 1), (1, 1, 3))
        inputs = [image, image[:, ::-1].copy()]
        converter = self.converter()
        actual = converter(inputs)
        expected = torch.stack([self.ToTensor()(item) for item in inputs])
        self.assertExact(expected, actual)
        self.assertEqual(converter.receipt["image_uploads"], 1)
        self.assertEqual(converter.receipt["uploaded_image_bytes"], 2 * image.nbytes)
        self.assertEqual(converter.receipt["subjects"], 2)

    def test_lookup_fallback_uses_identical_official_float32_values(self):
        pixels = self.np.random.default_rng(7).integers(0, 256, (16, 17, 3), dtype=self.np.uint8)
        converter = self.converter()
        converter._direct_division_exact = False
        self.assertExact(self.torch.stack([self.ToTensor()(pixels)]), converter([pixels]))

    def test_pool_reused_across_full_and_tail_batches_without_input_alias(self):
        np = self.np
        converter = self.converter()
        crops = [np.full((11, 13, 3), index * 20, np.uint8) for index in range(8)]
        first = converter(crops)
        snapshot = first.clone()
        host, device = converter._host.data_ptr(), converter._device_bytes.data_ptr()
        for count in (3, 8, 1):
            converter([255 - crop for crop in crops[:count]])
            self.assertEqual((host, device), (converter._host.data_ptr(), converter._device_bytes.data_ptr()))
            self.assertExact(first, snapshot)
        self.assertEqual(converter.receipt["staging_capacity_bytes"], 8 * crops[0].nbytes)

    def test_rejects_changed_shape_dtype_default_and_excess_count(self):
        np, torch = self.np, self.torch
        converter = self.converter(maximum=2)
        pixels = np.ones((10, 12, 3), np.uint8)
        converter([pixels])
        for inputs in ([], [pixels] * 3, [pixels.astype(float)], [pixels[:, :-1]], [pixels[:, :, 0]]):
            with self.subTest(count=len(inputs)), self.assertRaises(ValueError):
                converter(inputs)
        old = torch.get_default_dtype()
        try:
            torch.set_default_dtype(torch.float64)
            with self.assertRaisesRegex(ValueError, "dtype changed"):
                converter([pixels])
        finally:
            torch.set_default_dtype(old)
        self.assertEqual(converter.receipt["batch_calls"], 1)

    def test_copy_and_compute_dependencies_protect_both_staging_pools(self):
        # Real CPU tensors plus a recording CUDA API verify the event protocol;
        # no CUDA context, stream, event or GPU allocation is created by this test.
        converter, torch, log = self.converter(), self.torch, []

        class Event:
            def __init__(self):
                self.name = f"event{sum(item[0] == 'new_event' for item in log)}"
                log.append(("new_event", self.name))
            def record(self, stream):
                log.append(("record", self.name, stream.name))
            def synchronize(self):
                log.append(("host_wait", self.name))

        class Stream:
            def __init__(self, name): self.name = name
            def wait_event(self, event): log.append(("stream_wait", self.name, event.name))

        compute, copy = Stream("compute"), Stream("copy")

        @contextmanager
        def use_stream(stream):
            log.append(("enter", stream.name))
            yield
            log.append(("leave", stream.name))

        class TorchProxy:
            cuda = SimpleNamespace(Event=Event, current_stream=lambda device: compute, stream=use_stream)
            def __getattr__(self, name): return getattr(torch, name)
            def empty(self, *args, **kwargs):
                kwargs.pop("pin_memory", None)
                return torch.empty(*args, **kwargs)
            def from_numpy(self, value):
                log.append(("host_refill",))
                return torch.from_numpy(value)

        converter.torch, converter._copy_stream = TorchProxy(), copy
        pixels = self.np.ones((10, 12, 3), self.np.uint8)
        converter([pixels, pixels])
        first_copy, first_read = converter._copy_done, converter._read_done
        # Initial allocation may reuse storage with pending work on compute;
        # copy must wait on that stream's recorded history before its first H2D.
        allocation = next(item[1] for item in log if item[0] == "new_event")
        self.assertIn(("record", allocation, "compute"), log)
        self.assertLess(log.index(("record", allocation, "compute")),
                        log.index(("stream_wait", "copy", allocation)))
        self.assertLess(log.index(("stream_wait", "copy", allocation)),
                        log.index(("record", first_copy.name, "copy")))
        boundary = len(log)
        converter([pixels])
        second = log[boundary:]
        self.assertEqual(second[0], ("host_wait", first_copy.name))
        self.assertLess(second.index(("stream_wait", "copy", first_read.name)),
                        second.index(("record", converter._copy_done.name, "copy")))
        self.assertLess(second.index(("stream_wait", "compute", converter._copy_done.name)),
                        second.index(("record", converter._read_done.name, "compute")))
        last_copy, last_read = converter._copy_done, converter._read_done
        converter.close()
        self.assertEqual(log[-2:], [("host_wait", last_copy.name), ("host_wait", last_read.name)])
        self.assertIsNone(converter._host)
        self.assertIsNone(converter._device_bytes)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            converter([pixels])

    def test_official_crop_and_every_metadata_field_match_with_mixed_sources(self):
        np, torch = self.np, self.torch
        estimator = self.estimator()
        original, hand = estimator.transform, estimator.transform_hand
        processor = self.cpu_preprocessor(estimator)
        rng = np.random.default_rng(711)
        sources = [rng.integers(0, 256, (544, 960, 3), dtype=np.uint8),
                   rng.integers(0, 256, (600, 800, 3), dtype=np.uint8)[:, ::-1]]
        boxes = [[14.25, 22., 411.75, 500.5], [31.25, 5.75, 678.5, 580.25]]
        requests = [(source, np.asarray(box, np.float32)) for source, box in zip(sources, boxes)]
        expected = _merge_prepared_batches([
            self.prepare_batch(np.ascontiguousarray(source[:, :, ::-1]), original, box.reshape(1, 4))
            for source, box in requests], torch)
        first_rgb, actual = processor.prepare(requests, self.prepare_batch, _merge_prepared_batches)
        self.assertEqual(set(expected), set(actual))
        for name, value in expected.items():
            with self.subTest(field=name):
                if isinstance(value, torch.Tensor):
                    self.assertExact(value, actual[name])
                elif name == "img_ori":
                    for left, right in zip(value, actual[name]):
                        np.testing.assert_array_equal(left.data, right.data)
                else:
                    self.assertEqual(value, actual[name])
        np.testing.assert_array_equal(first_rgb, sources[0][:, :, ::-1])
        self.assertEqual(actual["img"].shape, (2, 1, 3, 512, 512))
        self.assertIs(estimator.transform, original)
        self.assertIs(estimator.transform_hand, hand)
        self.assertEqual(processor.crops, [])

    def test_existing_singleton_gpu_converter_is_not_replaced_or_called(self):
        fixture, estimator = fixtures.CPUParityTests(), self.estimator()
        with fixture.cpu_binding(estimator) as handle:
            original = estimator.transform
            processor = self.cpu_preprocessor(estimator)
            pixels = self.np.zeros((40, 50, 3), self.np.uint8)
            processor.prepare([(pixels, [0, 0, 50, 40])], self.prepare_batch, _merge_prepared_batches)
            self.assertIs(estimator.transform, original)
            self.assertEqual(handle.receipt["optimized_calls"], 0)
            processor.close()
        self.assertIsNot(estimator.transform, original)

    def test_changed_transform_and_bad_placeholder_fail_before_model_tensor(self):
        estimator = self.estimator()
        processor = self.cpu_preprocessor(estimator)
        request = [(self.np.zeros((40, 50, 3), self.np.uint8), [0, 0, 50, 40])]
        original = estimator.transform
        estimator.transform = object()
        with self.assertRaisesRegex(ValueError, "transform changed"):
            processor.prepare(request, self.prepare_batch, _merge_prepared_batches)
        estimator.transform = original

        def broken_merge(prepared, torch):
            result = _merge_prepared_batches(prepared, torch)
            result["img"] = torch.zeros((1, 1, 3, 2, 2))
            return result

        with self.assertRaisesRegex(ValueError, "placeholders"):
            processor.prepare(request, self.prepare_batch, broken_merge)
        self.assertEqual(processor.crops, [])
        self.assertEqual(processor.receipt["batch_calls"], 0)

    def test_opt_in_body_executor_replaces_placeholder_before_one_model_forward(self):
        torch, np, estimator = self.torch, self.np, self.estimator()
        processor = self.cpu_preprocessor(estimator)
        calls = []

        def recursive(value, target):
            if isinstance(value, dict): return {key: recursive(item, target) for key, item in value.items()}
            if isinstance(value, list): return [recursive(item, target) for item in value]
            if isinstance(value, torch.Tensor): return value.numpy() if target == "numpy" else value.cpu()
            return value

        def initialize(batch):
            self.assertEqual(batch["img"].shape, (2, 1, 3, 512, 512))

        def forward(image, batch, **kwargs):
            calls.append(kwargs["inference_type"])
            marker = batch["img"][:, 0].mean(dim=(1, 2, 3))[:, None].repeat(1, 3)
            return {"mhr": {key: marker for key in OUTPUT_FIELDS.values()}}

        estimator.model._initialize_batch, estimator.model.run_inference = initialize, forward
        estimator.fov_estimator, estimator.thresh_wrist_angle = None, 1.4
        with patch("rallymate_vision.sam3d_batch._verified_runtime",
                   return_value=(torch, self.prepare_batch, recursive)), \
                patch("rallymate_vision.sam3d_batch_preprocess.BatchCropPreprocessor", return_value=processor):
            runner = BodyBatchInference(estimator, batch_preprocess=True)
        requests = [(np.full((40, 50, 3), value, np.uint8), [0, 0, 50, 40]) for value in (50, 200)]
        result = runner.infer(requests, clear_cuda_cache=False)
        self.assertEqual(calls, ["body"])
        self.assertEqual(len(result), 2)
        self.assertLess(result[0]["pred_vertices"][0], result[1]["pred_vertices"][0])
        self.assertEqual(runner.receipt["batch_preprocess"]["image_uploads"], 1)
        runner.close()
        self.assertTrue(processor.converter.closed)


if __name__ == "__main__":
    unittest.main()
