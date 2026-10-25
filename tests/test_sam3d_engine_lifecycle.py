"""Constructor/cleanup failure boundaries without Torch imports or GPU work."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rallymate_vision import sam3d_runner as runner


class SamEngineLifecycleTests(unittest.TestCase):
    def initialize(self, *, sync_error=None, metadata_error=None, restore_error=None,
                   wait_policy=None, wait_error=None):
        restored = []

        def restore_rotation():
            restored.append("rotation")
            if restore_error is not None:
                raise restore_error

        handles = [SimpleNamespace(restore=lambda: restored.append("prompt")),
                   SimpleNamespace(restore=restore_rotation)]
        cuda = SimpleNamespace(is_available=lambda: True, set_device=Mock(),
            synchronize=Mock(side_effect=[None, sync_error] if sync_error is not None else None),
            empty_cache=Mock())
        torch = ModuleType("torch")
        torch.cuda, torch.device = cuda, lambda value: value
        torch.get_num_threads, torch.get_num_interop_threads = lambda: 4, lambda: 1
        model = object()
        config = SimpleNamespace(MODEL=SimpleNamespace(IMAGE_SIZE=(256, 256)))
        package = ModuleType("sam_3d_body")
        package.__path__ = []
        package.load_sam_3d_body = lambda **kwargs: (model, config)
        package.SAM3DBodyEstimator = lambda model, config: SimpleNamespace(model=model)
        metadata = ModuleType("sam_3d_body.metadata")
        metadata.__path__ = []
        native = ModuleType("sam_3d_body.metadata.mhr70")
        native.pose_info = {"original_keypoint_info": dict(enumerate(runner.MHR70_NAMES))}
        engine = runner.Sam3DEngine.__new__(runner.Sam3DEngine)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        (folder / "assets").mkdir()
        checkpoint = folder / "checkpoint.pt"
        for path in (checkpoint, folder / "assets/mhr_model.pt", folder / "model_config.yaml"):
            path.touch()

        def digest(path):
            if Path(path) == checkpoint:
                return next(iter(runner.CHECKPOINT_HASHES))
            if Path(path).name == "model_config.yaml":
                if metadata_error is not None:
                    raise metadata_error
                return "1" * 64
            return runner.MHR_HASH

        failure = None
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"torch": torch, "sam_3d_body": package,
                "sam_3d_body.metadata": metadata, "sam_3d_body.metadata.mhr70": native}))
            stack.enter_context(patch.object(sys, "path", list(sys.path)))
            stack.enter_context(patch.dict(os.environ, os.environ.copy()))
            stack.enter_context(patch.object(runner, "sha256_file", side_effect=digest))
            wait_config = stack.enter_context(patch.object(runner, "configure_sam_cuda_wait",
                return_value=wait_policy, side_effect=wait_error))
            wait_verify = stack.enter_context(patch.object(runner, "verify_sam_cuda_wait"))
            configured = stack.enter_context(patch.object(runner, "configure_runtime_optimizations",
                return_value=(handles, {"prompt_vectorization": {"enabled": True},
                                       "rotation_vectorization": {"enabled": True}})))
            try:
                engine.__init__(checkpoint, runtime_dir=folder)
            except BaseException as error:
                failure = error
        self.wait_config, self.wait_verify = wait_config, wait_verify
        return engine, torch, restored, failure, configured

    def test_cuda_wait_policy_is_verified_before_publication(self):
        policy = {"mode": "blocking_sync", "post_activation_verified": True}
        engine, _, _, error, _ = self.initialize(wait_policy=policy)
        self.assertIsNone(error)
        self.wait_config.assert_called_once_with("cuda:0")
        self.wait_verify.assert_called_once_with(policy)
        self.assertIs(engine.metadata["cuda_wait_policy"], policy)
        engine.close()

    def test_cuda_wait_setup_failure_never_loads_model_or_installs_graphs(self):
        failure = RuntimeError("active CUDA context")
        _, _, restored, error, configured = self.initialize(wait_error=failure)
        self.assertIs(error, failure)
        configured.assert_not_called()
        self.wait_verify.assert_not_called()
        self.assertEqual(restored, [])

    def test_post_binding_sync_failure_restores_handles_and_releases_estimator(self):
        original = RuntimeError("post-bind synchronization failed")
        engine, torch, restored, error, _ = self.initialize(sync_error=original)
        self.assertIs(error, original)
        self.assertEqual(restored, ["rotation", "prompt"])
        self.assertEqual(engine.optimization_handles, [])
        self.assertIsNone(engine.estimator)
        self.assertIs(engine.torch, torch)
        torch.cuda.empty_cache.assert_called_once_with()

    def test_metadata_failure_restores_handles_and_releases_estimator(self):
        original = RuntimeError("config metadata hash failed")
        engine, torch, restored, error, _ = self.initialize(metadata_error=original)
        self.assertIs(error, original)
        self.assertEqual(restored, ["rotation", "prompt"])
        self.assertEqual(engine.optimization_handles, [])
        self.assertIsNone(engine.estimator)
        torch.cuda.empty_cache.assert_called_once_with()

    def test_initialization_error_survives_cleanup_error_and_all_handles_are_attempted(self):
        original = RuntimeError("original initialization failure")
        cleanup = ValueError("rotation restore failure")
        engine, torch, restored, error, _ = self.initialize(metadata_error=original, restore_error=cleanup)
        self.assertIs(error, original)
        self.assertEqual(restored, ["rotation", "prompt"])
        self.assertEqual(engine.optimization_handles, [])
        self.assertIsNone(engine.estimator)
        torch.cuda.empty_cache.assert_called_once_with()

    def test_success_keeps_bindings_until_close_and_preserves_metadata(self):
        engine, torch, restored, error, configured = self.initialize()
        self.assertIsNone(error)
        self.assertEqual(restored, [])
        self.assertEqual(engine.metadata["input_size"], [256, 256])
        self.assertEqual(engine.metadata["inference_type"], "body")
        self.assertTrue(engine.metadata["prompt_vectorization"]["enabled"])
        self.assertTrue(engine.metadata["rotation_vectorization"]["enabled"])
        self.assertEqual(configured.call_args.kwargs["inference_type"], "body")
        torch.cuda.empty_cache.assert_not_called()
        engine.close()
        engine.close()
        self.assertEqual(restored, ["rotation", "prompt"])
        self.assertIsNone(engine.estimator)

    def test_close_attempts_remaining_handles_and_cache_cleanup_before_reraising(self):
        restored = []
        first_error = ValueError("rotation restore failure")

        def failing_restore():
            restored.append("rotation")
            raise first_error

        engine = runner.Sam3DEngine.__new__(runner.Sam3DEngine)
        engine.optimization_handles = [SimpleNamespace(restore=lambda: restored.append("prompt")),
                                       SimpleNamespace(restore=failing_restore)]
        engine.estimator = object()
        engine.torch = SimpleNamespace(cuda=SimpleNamespace(empty_cache=Mock(side_effect=RuntimeError("cache failure"))))
        with self.assertRaises(ValueError) as caught:
            engine.close()
        self.assertIs(caught.exception, first_error)
        self.assertEqual(restored, ["rotation", "prompt"])
        self.assertEqual(engine.optimization_handles, [])
        self.assertIsNone(engine.estimator)
        engine.torch.cuda.empty_cache.assert_called_once_with()

    def test_close_is_safe_before_torch_or_model_initialization(self):
        engine = runner.Sam3DEngine.__new__(runner.Sam3DEngine)
        engine.close()
        engine.close()
        self.assertIsNone(engine.estimator)
        self.assertEqual(engine.optimization_handles, [])

    def test_setup_error_survives_rollback_error(self):
        original = ValueError("rotation source identity differs")
        restore = Mock(side_effect=RuntimeError("prompt restore failure"))
        first = SimpleNamespace(apply_prompt_vectorized=lambda _: SimpleNamespace(receipt={}, restore=restore))
        second = SimpleNamespace(apply_roma_fixedshape=Mock(side_effect=original))
        with patch("importlib.import_module", side_effect=[first, second]):
            with self.assertRaises(ValueError) as caught:
                runner.configure_runtime_optimizations(object(), inference_type="body",
                                                        prompt_enabled=True, rotation_enabled=True)
        self.assertIs(caught.exception, original)
        restore.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
