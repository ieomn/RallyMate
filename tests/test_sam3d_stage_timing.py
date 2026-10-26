"""Transport instrumentation tests; these do not claim GPU speed or accuracy."""
import os
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from rallymate_vision.sam3d_runner import EstimatorStageTiming


class EventFixture:
    def __init__(self, **_kwargs):
        pass

    def record(self):
        pass

    def elapsed_time(self, _after):
        return 2.5


class ModelFixture:
    def _initialize_batch(self, value):
        return value

    def run_inference(self, value):
        return {"value": value}


class StageTimingTests(unittest.TestCase):
    def setUp(self):
        self.module = ModuleType("sam_stage_fixture")
        self.module.prepare_batch = lambda *args: args
        self.module.recursive_to = lambda value, destination: (value, destination)
        self.empty_calls = []
        self.cuda = SimpleNamespace(Event=EventFixture,
                                    empty_cache=lambda: self.empty_calls.append(True))
        self.torch = SimpleNamespace(cuda=self.cuda)
        self.module.torch = self.torch
        self.estimator = type("EstimatorFixture", (), {"__module__": self.module.__name__})()
        self.estimator.model = ModelFixture()
        self.modules = patch.dict(sys.modules, {self.module.__name__: self.module})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_disabled_path_does_not_patch_or_require_isolation(self):
        original = self.cuda.empty_cache
        with EstimatorStageTiming(self.estimator, self.torch, enabled=False, reuse_cuda_cache=False):
            self.assertIs(self.cuda.empty_cache, original)

    def test_instrumentation_requires_dedicated_worker(self):
        with patch.dict(os.environ, {"RALLYMATE_SAM3D_ISOLATED_WORKER": "0"}):
            with self.assertRaises(RuntimeError):
                with EstimatorStageTiming(self.estimator, self.torch, enabled=True, reuse_cuda_cache=False):
                    pass

    def test_records_stages_without_changing_results_and_restores_on_error(self):
        original_prepare, original_transfer = self.module.prepare_batch, self.module.recursive_to
        original_empty = self.cuda.empty_cache
        timer = EstimatorStageTiming(self.estimator, self.torch, enabled=True, reuse_cuda_cache=False)
        with patch.dict(os.environ, {"RALLYMATE_SAM3D_ISOLATED_WORKER": "1"}):
            with self.assertRaisesRegex(ValueError, "fixture failure"):
                with timer:
                    self.assertIs(self.cuda.empty_cache, original_empty)
                    self.module.torch.cuda.empty_cache()
                    self.assertEqual(self.module.prepare_batch(1, 2), (1, 2))
                    self.assertEqual(self.module.recursive_to(1, "cuda"), (1, "cuda"))
                    self.assertEqual(self.estimator.model._initialize_batch(3), 3)
                    self.assertEqual(self.estimator.model.run_inference(4), {"value": 4})
                    raise ValueError("fixture failure")
        self.assertIs(self.module.prepare_batch, original_prepare)
        self.assertIs(self.module.recursive_to, original_transfer)
        self.assertIs(self.cuda.empty_cache, original_empty)
        self.assertIs(self.module.torch, self.torch)
        self.assertNotIn("run_inference", vars(self.estimator.model))
        self.assertEqual(self.empty_calls, [True])
        report = timer.report()
        self.assertEqual(report["calls"]["crop_preprocess"], 1)
        self.assertEqual(report["cuda_stream_span_ms"]["model_forward"], 2.5)
        self.assertEqual(report["extra_stage_synchronizations"], 0)

    def test_explicit_cache_reuse_skips_only_in_scope_and_restores(self):
        with patch.dict(os.environ, {"RALLYMATE_SAM3D_ISOLATED_WORKER": "1"}):
            with EstimatorStageTiming(self.estimator, self.torch, enabled=False, reuse_cuda_cache=True) as timer:
                self.module.torch.cuda.empty_cache()
                self.assertEqual(self.empty_calls, [])
                # Other model/module references to torch are never suppressed.
                self.torch.cuda.empty_cache()
                self.assertEqual(self.empty_calls, [True])
        self.cuda.empty_cache()
        self.assertEqual(self.empty_calls, [True, True])
        self.assertEqual(timer.report()["cuda_cache_policy"], "reuse_between_frames")
