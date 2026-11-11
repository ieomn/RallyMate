from contextlib import ExitStack
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rallymate_service import worker
from rallymate_vision import inference


class WorkerResourceCleanupTests(unittest.TestCase):
    def runner_context(self, perception, device="0"):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {}, clear=True))
        fake_torch = SimpleNamespace(device=Mock(side_effect=lambda value: ("torch.device", value)))
        stack.enter_context(patch.dict(sys.modules, {"torch": fake_torch}))
        stack.enter_context(patch.object(worker, "configure_cpu_thread_pools", return_value={"requested": 2}))
        stack.enter_context(patch.object(worker, "resolve_device", return_value=device))
        constructor = stack.enter_context(patch.object(worker, "Yolo26Perception", return_value=perception))
        observe = stack.enter_context(patch.object(worker, "observe_cpu_thread_pools", return_value={
            "torch_intraop": 2, "torch_interop": 1, "opencv": 1}))
        return Mock(cpu_threads=2, device=device), fake_torch, constructor, observe

    def test_post_model_observation_failure_closes_owned_perception(self):
        perception = Mock()
        settings, _, _, observe = self.runner_context(perception)
        observe.side_effect = RuntimeError("observation failed")
        with self.assertRaisesRegex(RuntimeError, "observation failed"):
            worker.PersistentVisionRunner(settings)
        perception.close.assert_called_once_with()

    def test_every_cuda_worker_preserves_visibility_mapping_with_explicit_torch_device(self):
        for device, expected in (("1", "cuda:1"), ("cuda", "cuda:0"), ("cuda:2", "cuda:2")):
            with self.subTest(device=device):
                perception = SimpleNamespace(close=Mock())
                settings, torch, constructor, _ = self.runner_context(perception, device)
                runner = worker.PersistentVisionRunner(settings)
                self.assertEqual(constructor.call_args.kwargs["device"], device)
                self.assertEqual(runner.perception.device, ("torch.device", expected))
                torch.device.assert_called_once_with(expected)
                perception.close.assert_not_called()

    def test_cpu_worker_keeps_cpu_device_and_does_not_create_cuda_device(self):
        perception = SimpleNamespace(device="cpu", close=Mock())
        settings, torch, _, _ = self.runner_context(perception, "cpu")
        runner = worker.PersistentVisionRunner(settings)
        self.assertEqual(runner.perception.device, "cpu")
        torch.device.assert_not_called()

    def construct_perception(self, backend, *, injected=False, names=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        detect = Path(temporary.name) / "detect.pt"
        detect.write_bytes(b"test")
        model = SimpleNamespace(names={0: "person"} if names is None else names)
        with patch.dict(sys.modules, {"ultralytics": SimpleNamespace(YOLO=Mock(return_value=model))}), \
             patch.object(inference, "build_pose_backend", return_value=backend):
            return inference.Yolo26Perception(detect, Path("pose.pt"), "cpu", 960, 640, .15, .25,
                pose_backend_instance=backend if injected else None)

    def test_new_backend_is_closed_if_metadata_fails_after_start(self):
        backend = Mock()
        backend.metadata.side_effect = RuntimeError("metadata failed")
        with self.assertRaisesRegex(RuntimeError, "metadata failed"):
            self.construct_perception(backend)
        backend.close.assert_called_once_with()

    def test_new_backend_is_closed_if_detector_metadata_fails(self):
        backend = Mock()
        backend.metadata.return_value = SimpleNamespace(model_path="pose.pt")
        with self.assertRaises(AttributeError):
            self.construct_perception(backend, names=[])
        backend.close.assert_called_once_with()

    def test_injected_backend_remains_caller_owned_on_constructor_failure(self):
        backend = Mock()
        backend.metadata.side_effect = RuntimeError("metadata failed")
        with self.assertRaisesRegex(RuntimeError, "metadata failed"):
            self.construct_perception(backend, injected=True)
        backend.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
