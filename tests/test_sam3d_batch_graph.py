"""Bounded additional CUDA graph signatures, tested without a CUDA device."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rallymate_vision import sam3d_forward_graph as module
from tests.test_sam3d_forward_graph import FakeModule, FakeTensor


class BatchForwardGraphContracts(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = ["same"]
        self.stack.enter_context(patch.object(module, "_state_signature", side_effect=lambda _: tuple(self.state)))
        self.model = FakeModule()
        self.allocated = self.reserved = 0
        self.capture_cost = 32
        self.budget = {"limit_bytes": 512, "retained_bytes": 0}
        self.torch = SimpleNamespace(
            Tensor=FakeTensor, strided="strided", is_grad_enabled=lambda: False,
            is_autocast_enabled=lambda: False, cuda=SimpleNamespace(
                synchronize=Mock(), mem_get_info=Mock(return_value=(1024 ** 3, 2 * 1024 ** 3)),
                memory_allocated=lambda _: self.allocated, memory_reserved=lambda _: self.reserved))
        self.handle = self.make_handle()
        self.handle._build_graph = Mock(side_effect=self.build)
        self.handle.original = Mock(wraps=self.handle.original)

    def make_handle(self, sizes=(2, 4), model=None):
        return module.ModuleForwardGraph(
            model or self.model, self.torch, name="batch-test", argument_names=("x", "extra_embed"),
            device="cuda:0", additional_batch_sizes=sizes, batch_memory_budget=self.budget)

    @staticmethod
    def tensor(n, value=1, width=2):
        return FakeTensor([value] * (n * width), shape=(n, width))

    def build(self, values):
        inputs = tuple(value.clone() if value is not None else None for value in values)
        output = self.tensor(inputs[0].shape[0], 0)
        self.allocated += self.capture_cost
        self.reserved += self.capture_cost

        def replay():
            output.values[:] = [value * 10 for value in inputs[0].values]

        return SimpleNamespace(replay=Mock(side_effect=replay)), inputs, {"output": output}

    def test_scalar_and_two_batch_slots_keep_identity_storage_and_dynamic_values(self):
        identity = self.model.forward
        scalar = self.handle(self.tensor(1, 1))
        two = self.handle(self.tensor(2, 2))
        four = self.handle(self.tensor(4, 4))
        scalar_graph, scalar_inputs = self.handle.graph, self.handle.inputs
        two_again = self.handle(self.tensor(2, 8))
        scalar_again = self.handle(self.tensor(1, 7))
        self.assertIs(self.model.forward, identity)
        self.assertIs(self.handle.graph, scalar_graph)
        self.assertIs(self.handle.inputs, scalar_inputs)
        self.assertEqual(scalar["output"].values, [10] * 2)
        self.assertEqual(two["output"].values, [20] * 4)
        self.assertEqual(four["output"].values, [40] * 8)
        self.assertEqual(two_again["output"].values, [80] * 4)
        self.assertEqual(scalar_again["output"].values, [70] * 2)
        self.assertEqual(self.handle._build_graph.call_count, 3)
        self.assertEqual(self.handle.receipt["replays"], 2)
        self.assertEqual(self.handle.batch_slots[2]["receipt"]["replays"], 2)
        self.assertEqual(self.handle.batch_slots[4]["receipt"]["replays"], 1)
        self.assertEqual(self.budget["retained_bytes"], 64)
        self.assertEqual(module.ForwardGraphHandle([self.handle], "test", {}).receipt["maximum_graphs"], 3)

    def test_unconfigured_sizes_and_mixed_batch_tensors_are_eager(self):
        self.assertIn("eager", self.handle(self.tensor(3)))
        self.assertIn("eager", self.handle(self.tensor(2), self.tensor(4)))
        self.assertIn("eager", self.handle(self.tensor(2), self.tensor(1)))
        self.handle._build_graph.assert_not_called()
        self.assertEqual(set(self.handle.batch_slots), {2, 4})

    def test_pinned_decoder_broadcast_context_pe_is_copied_without_expansion(self):
        class Layer(FakeModule):
            def forward(self, x, context, x_pe=None, context_pe=None, x_mask=None):
                return {"eager": x.clone()}

        layer = Layer()
        handle = module.ModuleForwardGraph(
            layer, self.torch, name="decoder.layers.0",
            argument_names=("x", "context", "x_pe", "context_pe", "x_mask"),
            device="cuda:0", additional_batch_sizes=(2, 4), batch_memory_budget=self.budget)
        handle._build_graph = Mock(side_effect=self.build)
        arguments = (self.tensor(4), self.tensor(4), self.tensor(4), self.tensor(1), self.tensor(4))
        self.assertIn("output", handle(*arguments))
        self.assertEqual(handle.batch_slots[4]["inputs"][3].shape, (1, 2))
        arguments[3].values[:] = [13, 14]
        handle(*arguments)
        self.assertEqual(handle.batch_slots[4]["inputs"][3].values, [13, 14])
        handle._build_graph.assert_called_once()
        # No arbitrary broadcast permissions are granted to x_pe or context.
        self.assertIn("eager", handle(arguments[0], self.tensor(1), *arguments[2:]))
        self.assertIn("eager", handle(*arguments[:2], self.tensor(1), *arguments[3:]))

    def test_signature_change_does_not_recapture_existing_batch(self):
        self.handle(self.tensor(2))
        self.assertIn("eager", self.handle(self.tensor(2, width=3)))
        self.assertIn("eager", self.handle(self.tensor(2), self.tensor(2)))
        self.handle._build_graph.assert_called_once()
        self.assertEqual(self.handle.batch_slots[2]["receipt"]["last_fallback_reason"], "different_input_signature")

    def test_default_remains_singleton_and_option_validation_is_before_mutation(self):
        model = FakeModule()
        handle = self.make_handle(sizes=(), model=model)
        handle._build_graph = Mock(side_effect=self.build)
        self.assertIn("eager", handle(self.tensor(2)))
        handle._build_graph.assert_not_called()
        self.assertEqual(handle.batch_slots, {})
        for sizes in ((1,), (9,), (True,), (2, 2), (2, 4, 8), ([],)):
            untouched = FakeModule()
            with self.subTest(sizes=sizes), self.assertRaises(ValueError):
                self.make_handle(sizes=sizes, model=untouched)
            self.assertNotIn("forward", vars(untouched))

    def test_failed_batch_capture_does_not_disable_scalar_or_other_batch(self):
        self.handle._build_graph.side_effect = [RuntimeError("capture failed"), self.build((self.tensor(1), None)),
                                               self.build((self.tensor(4), None))]
        self.assertIn("eager", self.handle(self.tensor(2)))
        self.assertIn("eager", self.handle(self.tensor(2)))
        self.assertIn("output", self.handle(self.tensor(1)))
        self.assertIn("output", self.handle(self.tensor(4)))
        self.assertTrue(self.handle.batch_slots[2]["disabled"])
        self.assertFalse(self.handle.disabled)
        self.assertFalse(self.handle.batch_slots[4]["disabled"])
        self.assertEqual(self.handle._build_graph.call_count, 3)

    def test_poisoned_cuda_context_propagates_and_does_not_run_eager(self):
        self.handle._build_graph.side_effect = RuntimeError("capture failed")
        self.torch.cuda.synchronize.side_effect = RuntimeError("device unusable")
        with self.assertRaisesRegex(RuntimeError, "device unusable"):
            self.handle(self.tensor(2))
        self.handle.original.assert_not_called()

    def test_free_memory_admission_prevents_capture_and_retry(self):
        self.torch.cuda.mem_get_info.return_value = (100 * 1024 ** 2, 2 * 1024 ** 3)
        self.assertIn("eager", self.handle(self.tensor(2)))
        self.torch.cuda.mem_get_info.return_value = (1024 ** 3, 2 * 1024 ** 3)
        self.assertIn("eager", self.handle(self.tensor(2)))
        self.handle._build_graph.assert_not_called()
        self.assertEqual(self.handle.batch_slots[2]["receipt"]["capture_status"], "memory_admission_rejected")

    def test_shared_budget_caps_pools_across_modules_and_restore_releases_charge(self):
        self.capture_cost = 300
        self.handle(self.tensor(2))
        other = self.make_handle(model=FakeModule())
        other._build_graph = Mock(side_effect=self.build)
        self.assertIn("eager", other(self.tensor(4)))
        self.assertEqual(self.budget["retained_bytes"], 300)
        self.assertIsNone(other.batch_slots[4]["graph"])
        self.assertIsNone(other.batch_slots[4]["inputs"])
        self.assertEqual(other.batch_slots[4]["receipt"]["capture_status"], "retained_memory_budget_exceeded")
        self.handle.restore()
        self.assertEqual(self.budget["retained_bytes"], 0)
        self.assertIsNone(self.handle.batch_slots[2]["graph"])
        other.restore()
        self.assertEqual(self.budget["retained_bytes"], 0)
        self.assertNotIn("forward", vars(self.model))

    def test_budget_rejection_eager_failure_is_not_run_twice(self):
        self.capture_cost = 600
        self.handle.original.side_effect = ValueError("real inference failure")
        with self.assertRaisesRegex(ValueError, "real inference failure"):
            self.handle(self.tensor(2))
        self.handle.original.assert_called_once()
        self.assertEqual(self.handle.batch_slots[2]["receipt"]["capture_status"], "retained_memory_budget_exceeded")

    def test_model_state_change_invalidates_batch_graph(self):
        self.handle(self.tensor(2))
        graph = self.handle.batch_slots[2]["graph"]
        self.state[:] = ["changed"]
        with self.assertRaisesRegex(RuntimeError, "state changed"):
            self.handle(self.tensor(2, 9))
        self.assertEqual(graph.replay.call_count, 1)


if __name__ == "__main__":
    unittest.main()
