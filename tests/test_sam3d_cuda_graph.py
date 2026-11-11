"""Graph lifetime/fallback contracts without requiring a CUDA device."""
from contextlib import ExitStack
import hashlib
import os
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
        value = FakeTensor(self.values, self.shape[1])
        value.shape = self.shape
        return value


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

    def test_opt_in_batch_slots_preserve_scalar_graph_and_output_ownership(self):
        self.handle.restore()
        self.handle = module.HeadGraphHandle(self.head, object(), self.safe, self.safe_replace,
            self.torch, additional_batch_sizes=(2, 4))
        self.install_fake_capture()
        scalar = self.handle(*self.args)
        scalar_graph = self.handle.graph
        for size in (2, 4, 2):
            args = tuple(value.clone() for value in self.args)
            for value in args:
                value.shape = (size, value.shape[1])
            batch = self.handle(*args)
            self.assertEqual(batch["vertices"].values, [3])
            self.assertIs(self.handle.graph, scalar_graph)
        self.assertEqual(len(self.handle.batch_slots), 2)
        self.assertEqual(self.handle.receipt["batch_slots"]["2"]["replays"], 2)
        self.assertEqual(self.handle(*self.args)["vertices"].values, scalar["vertices"].values)
        self.assertEqual(self.handle._capture.call_count, 3)
        self.handle.restore()
        self.assertEqual(self.handle.batch_slots, {})

    def test_batch_slot_capture_failure_does_not_disable_scalar(self):
        self.handle.restore()
        self.handle = module.HeadGraphHandle(self.head, object(), self.safe, self.safe_replace,
            self.torch, additional_batch_sizes=(2,))
        self.install_fake_capture()
        scalar = self.handle(*self.args)
        self.handle._capture = Mock(side_effect=RuntimeError("batch capture failure"))
        args = tuple(value.clone() for value in self.args)
        for value in args:
            value.shape = (2, value.shape[1])
        self.assertEqual(self.handle(*args), {"original": True})
        self.assertFalse(self.handle.disabled)
        self.assertTrue(self.handle.batch_slots[2]["disabled"])
        self.assertEqual(self.handle(*self.args)["vertices"].values, scalar["vertices"].values)

    def test_batch_shape_configuration_is_bounded(self):
        for sizes in ((2, 4, 8), (1,), (2, 2), (True,), (9,)):
            with self.subTest(sizes=sizes), self.assertRaises(ValueError):
                module.HeadGraphHandle(self.head, object(), self.safe, self.safe_replace,
                                       self.torch, additional_batch_sizes=sizes)

    def test_configured_head_budget_releases_over_limit_graph_without_losing_output(self):
        self.torch.cuda.mem_get_info = Mock(return_value=(4 * 1024 ** 3, 16 * 1024 ** 3))
        args = tuple(value.clone() for value in self.args)
        for value in args:
            value.shape = (2, value.shape[1])
        for mib, retained in (("512", False), ("1024", True)):
            self.handle.restore()
            with self.subTest(mib=mib), patch.dict(os.environ,
                    {"RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB": mib}):
                self.handle = module.HeadGraphHandle(self.head, object(), self.safe, self.safe_replace,
                    self.torch, additional_batch_sizes=(2,))
            self.install_fake_capture()
            self.torch.cuda.memory_reserved = Mock(side_effect=(0, 700 * 1024 ** 2))
            with patch.dict(os.environ, {"RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB": "128"}):
                result = self.handle(*args)
            self.assertEqual(result["vertices"].values, [3])
            self.assertEqual(self.handle.batch_slots[2]["graph"] is not None, retained)
            self.assertEqual(self.handle.receipt["additional_retained_pool_budget_bytes"],
                             int(mib) * 1024 ** 2)
            if not retained:
                self.assertEqual(self.handle(*args), {"original": True})
                self.handle._capture.assert_called_once()
                self.assertEqual(self.handle.batch_slots[2]["receipt"]["capture_status"],
                                 "released_memory_budget")
            # Additional graph eviction must never disable the singleton path.
            self.assertEqual(self.handle(*self.args)["vertices"].values, [3])

    def test_head_configured_budget_caps_combined_shapes_and_preserves_first(self):
        self.handle.restore()
        with patch.dict(os.environ, {"RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB": "1024"}):
            self.handle = module.HeadGraphHandle(self.head, object(), self.safe, self.safe_replace,
                self.torch, additional_batch_sizes=(2, 4))
        self.install_fake_capture()
        cost = 600 * 1024 ** 2
        self.torch.cuda.mem_get_info = Mock(return_value=(4 * 1024 ** 3, 16 * 1024 ** 3))
        self.torch.cuda.memory_reserved = Mock(side_effect=(0, cost, cost, 2 * cost))
        for size in (2, 4):
            args = tuple(value.clone() for value in self.args)
            for value in args:
                value.shape = (size, value.shape[1])
            self.assertEqual(self.handle(*args)["vertices"].values, [3])
        self.assertIsNotNone(self.handle.batch_slots[2]["graph"])
        self.assertIsNone(self.handle.batch_slots[4]["graph"])
        self.assertEqual(self.handle.batch_slots[2]["receipt"]["retained_pool_delta_bytes"], cost)
        self.assertEqual(self.handle.batch_slots[4]["receipt"]["retained_pool_delta_bytes"], 0)
        self.original.side_effect = ValueError("eager failure")
        with self.assertRaisesRegex(ValueError, "eager failure"):
            self.handle(*args)
        self.original.assert_called_once()
        self.assertEqual(self.handle._capture.call_count, 2)

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
    def test_budget_bounds_and_default_are_resolved_without_device_access(self):
        for variable in ("RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB",
                         "RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB"):
            with patch.dict(os.environ, {}, clear=True):
                budget = module._graph_memory_budget(variable)
                self.assertEqual(budget["limit_bytes"], 512 * 1024 ** 2)
                self.assertEqual(budget["configuration_source"], "default")
            for value in ("128", "4096"):
                with self.subTest(variable=variable, value=value), patch.dict(os.environ, {variable: value}):
                    self.assertEqual(module._graph_memory_budget(variable)["limit_bytes"],
                                     int(value) * 1024 ** 2)

    def test_invalid_head_budget_fails_before_model_or_cuda_access(self):
        for value in ("", "127", "4097", "-512", "512.0", "True", "１２８", "9" * 100):
            with self.subTest(value=value), patch.dict(os.environ,
                    {"RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB": value}):
                with self.assertRaisesRegex(ValueError, "BUDGET_MIB"):
                    module.apply_head_cuda_graph(object())
                head = SimpleNamespace()
                with self.assertRaisesRegex(ValueError, "BUDGET_MIB"):
                    module.HeadGraphHandle(head, None, None, None, None)
                self.assertEqual(vars(head), {})

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
