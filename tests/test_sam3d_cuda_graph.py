"""Graph lifetime/fallback contracts without requiring a CUDA device."""
from contextlib import ExitStack
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rallymate_vision import sam3d_cuda_graph as module


class FakeTensor:
    def __init__(self, values, width):
        self.values = list(values)
        self.shape, self.ndim = (1, width), 2
        self.device, self.dtype, self.is_cuda = "cuda:0", "fp32", True

    def copy_(self, other):
        self.values[:] = other.values

    def clone(self):
        return FakeTensor(self.values, self.shape[1])


class GraphContractsTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = ["unchanged"]
        self.stack.enter_context(patch.object(module, "_state_signature",
                                              side_effect=lambda _: tuple(self.state)))
        self.original = Mock(return_value={"original": True})
        self.head = SimpleNamespace(
            mhr=object(), faces=SimpleNamespace(device="cuda:0"), training=False,
            npose=3, forward=self.original, replace_hands_in_pose=Mock())
        self.original_mhr = self.head.mhr
        self.safe, self.safe_replace = Mock(), Mock()
        self.torch = SimpleNamespace(
            Tensor=FakeTensor, float32="fp32", is_grad_enabled=lambda: False,
            is_autocast_enabled=lambda: False,
            cuda=SimpleNamespace(synchronize=Mock()))
        self.handle = module.HeadGraphHandle(
            self.head, object(), self.safe, self.safe_replace, self.torch)
        self.args = (FakeTensor([1, 2], 2), FakeTensor([3, 4, 5], 3))

    def install_fake_capture(self):
        def capture(args):
            self.handle.inputs = tuple(x.clone() for x in args)
            output = FakeTensor([0], 1)

            def replay():
                output.values[0] = sum(self.handle.inputs[0].values)

            self.handle.graph = SimpleNamespace(replay=replay)
            self.handle.outputs = {"vertices": output, "none": None}
        self.handle._capture = Mock(side_effect=capture)

    def test_replay_copies_each_input_and_does_not_alias_prior_output(self):
        self.install_fake_capture()
        first = self.handle(*self.args)
        self.args[0].values[:] = [20, 30]
        second = self.handle(*self.args)
        self.assertEqual(first["vertices"].values, [3])
        self.assertEqual(second["vertices"].values, [50])
        self.assertEqual(self.handle.receipt["replays"], 2)
        self.assertEqual(self.handle._capture.call_count, 1)

    def test_unsupported_shape_uses_original_and_restores_candidate(self):
        current_mhr = self.head.mhr
        self.args[0].shape = (2, 2)
        result = self.handle(*self.args)
        self.assertEqual(result, {"original": True})
        self.original.assert_called_once_with(*self.args)
        self.assertIs(self.head.mhr, current_mhr)
        self.assertIs(self.head.replace_hands_in_pose, self.safe_replace)

    def test_autocast_uses_original_precision_policy(self):
        self.torch.is_autocast_enabled = lambda: True
        self.assertEqual(self.handle(*self.args), {"original": True})
        self.assertIsNone(self.handle.graph)

    def test_failed_install_restores_every_original_attribute(self):
        self.handle.restore()
        with patch.object(module, "_state_signature", side_effect=ValueError("state scan failed")):
            with self.assertRaisesRegex(ValueError, "state scan failed"):
                module.HeadGraphHandle(self.head, object(), self.safe, self.safe_replace, self.torch)
        self.assertIs(self.head.forward, self.original)
        self.assertIs(self.head.mhr, self.original_mhr)

    def test_capture_failure_falls_back_once_then_never_recaptures(self):
        self.handle._capture = Mock(side_effect=RuntimeError("unsupported capture"))
        self.assertEqual(self.handle(*self.args), {"original": True})
        self.assertEqual(self.handle(*self.args), {"original": True})
        self.handle._capture.assert_called_once()
        self.assertEqual(self.handle.receipt["capture_status"], "fallback_after_capture_error")
        self.assertEqual(self.handle.receipt["fallback_calls"], 2)
        self.assertIsNone(self.handle.graph)

    def test_fatal_cuda_failure_is_not_hidden_as_successful_fallback(self):
        self.handle._capture = Mock(side_effect=RuntimeError("capture failed"))
        self.torch.cuda.synchronize.side_effect = RuntimeError("device unusable")
        with self.assertRaisesRegex(RuntimeError, "device unusable"):
            self.handle(*self.args)
        self.original.assert_not_called()

    def test_changed_model_or_training_state_never_replays_stale_graph(self):
        self.install_fake_capture()
        self.handle(*self.args)
        self.state[:] = ["modified_tensor"]
        with self.assertRaisesRegex(RuntimeError, "state changed"):
            self.handle(*self.args)
        self.assertEqual(self.handle.receipt["replays"], 1)
        self.assertEqual(self.handle.receipt["capture_status"], "invalidated_model_state")

    def test_different_signature_falls_back_without_allocating_second_graph(self):
        self.install_fake_capture()
        self.handle(*self.args)
        self.args[0].shape = (1, 4)
        self.assertEqual(self.handle(*self.args), {"original": True})
        self.assertEqual(self.handle._capture.call_count, 1)

    def test_reentrancy_and_restore_while_running_are_explicit(self):
        self.handle.lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, "Concurrent"):
                self.handle(*self.args)
            with self.assertRaisesRegex(RuntimeError, "running"):
                self.handle.restore()
        finally:
            self.handle.lock.release()

    def test_restore_is_idempotent_and_releases_graph_storage(self):
        self.install_fake_capture()
        self.handle(*self.args)
        self.handle.restore()
        self.handle.restore()
        self.assertIs(self.head.mhr, self.original_mhr)
        self.assertIs(self.head.forward, self.original)
        self.assertIsNone(self.handle.graph)
        self.assertIsNone(self.handle.inputs)
        self.assertIsNone(self.handle.outputs)
        self.torch.cuda.synchronize.assert_called_once_with("cuda:0")
        with self.assertRaisesRegex(RuntimeError, "restored"):
            self.handle(*self.args)

    def test_exception_in_original_fallback_restores_candidate(self):
        candidate = self.head.mhr
        self.original.side_effect = ValueError("original error")
        with self.assertRaisesRegex(ValueError, "original error"):
            self.handle(*self.args, do_pcblend=False)
        self.assertIs(self.head.mhr, candidate)
        self.assertIs(self.head.replace_hands_in_pose, self.safe_replace)


class SourceGuardTest(unittest.TestCase):
    def test_script_identity_checked_before_rewrite(self):
        with self.assertRaisesRegex(ValueError, "source differs"):
            module._rewrite_script(b"untrusted new source")

    def test_exact_script_rewrites_leave_other_arithmetic_untouched(self):
        source = ("\n".join(module.SCRIPT_REPLACEMENTS) + "\nKEEP_FP64_SKELETON").encode()
        with patch.object(module, "SCRIPT_UTIL_SHA256", hashlib.sha256(source).hexdigest()):
            output = module._rewrite_script(source).decode()
        for expression in module.SCRIPT_REPLACEMENTS.values():
            self.assertIn(expression, output)
        self.assertTrue(output.endswith("KEEP_FP64_SKELETON"))

    def test_repeated_expression_rejected(self):
        source = ("\n".join(module.SCRIPT_REPLACEMENTS) + "\n" +
                  next(iter(module.SCRIPT_REPLACEMENTS))).encode()
        with patch.object(module, "SCRIPT_UTIL_SHA256", hashlib.sha256(source).hexdigest()):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                module._rewrite_script(source)


if __name__ == "__main__":
    unittest.main()
