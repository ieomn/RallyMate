"""Forward graph ownership, contracts and invalidation, without a CUDA device."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rallymate_vision import sam3d_forward_graph as module


class FakeTensor:
    def __init__(self, values, shape=(1, 2)):
        self.values = list(values)
        self.shape, self.ndim = shape, len(shape)
        self.device, self.dtype, self.layout, self.is_cuda = "cuda:0", "fp32", "strided", True

    def stride(self):
        return (self.shape[-1], 1)

    def copy_(self, other):
        self.values[:] = other.values

    def clone(self):
        return FakeTensor(self.values, self.shape)


class FakeModule:
    training = False

    def forward(self, x, extra_embed=None):
        return {"eager": x.clone(), "extra": extra_embed}


class ForwardGraphContracts(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = ["same"]
        self.stack.enter_context(patch.object(module, "_state_signature", side_effect=lambda _: tuple(self.state)))
        self.model = FakeModule()
        self.original = self.model.forward
        self.torch = SimpleNamespace(Tensor=FakeTensor, strided="strided", is_grad_enabled=lambda: False,
                                     is_autocast_enabled=lambda: False,
                                     cuda=SimpleNamespace(synchronize=Mock()))
        self.handle = module.ModuleForwardGraph(self.model, self.torch, name="test",
                                                argument_names=("x", "extra_embed"), device="cuda:0")
        self.x = FakeTensor([1, 2])

    def install_capture(self):
        def capture(values):
            self.handle.inputs = tuple(value.clone() if value is not None else None for value in values)
            out = FakeTensor([0])
            def replay():
                out.values[:] = [sum(self.handle.inputs[0].values)]
            self.handle.graph = SimpleNamespace(replay=replay)
            self.handle.outputs = (out, {"nested": [out, None]})
        self.handle._capture = Mock(side_effect=capture)

    def test_replay_uses_dynamic_values_and_recursively_clones_outputs(self):
        self.install_capture()
        first = self.handle(self.x, extra_embed=None)
        self.x.values[:] = [30, 40]
        second = self.handle(x=self.x)
        self.assertEqual(first[0].values, [3])
        self.assertEqual(first[1]["nested"][0].values, [3])
        self.assertEqual(second[0].values, [70])
        self.assertIsNot(first[0], first[1]["nested"][0])
        self.handle._capture.assert_called_once()
        self.assertEqual(self.handle.receipt['replays'], 2)

    def test_changed_shape_falls_back_without_second_graph(self):
        self.install_capture()
        self.handle(self.x)
        self.x.shape = (1, 3)
        self.assertEqual(self.handle(self.x)["eager"].values, [1, 2])
        self.handle._capture.assert_called_once()
        self.assertEqual(self.handle.receipt['last_fallback_reason'], 'different_input_signature')

    def test_nontensor_argument_is_not_silently_frozen(self):
        self.install_capture()
        self.assertEqual(self.handle(self.x, extra_embed=True)["extra"], True)
        self.handle._capture.assert_not_called()

    def test_changed_none_to_tensor_signature_falls_back(self):
        self.install_capture()
        self.handle(self.x)
        other = FakeTensor([6])
        self.assertIs(self.handle(self.x, extra_embed=other)["extra"], other)
        self.handle._capture.assert_called_once()

    def test_cpu_or_other_device_is_eager(self):
        self.install_capture()
        self.x.is_cuda = False
        self.assertIn('eager', self.handle(self.x))
        self.x.is_cuda, self.x.device = True, 'cuda:1'
        self.assertIn('eager', self.handle(self.x))
        self.handle._capture.assert_not_called()

    def test_autocast_is_eager_and_keeps_precision_policy(self):
        self.install_capture()
        self.torch.is_autocast_enabled = lambda: True
        self.assertIn('eager', self.handle(self.x))
        self.handle._capture.assert_not_called()

    def test_capture_error_falls_back_and_does_not_retry(self):
        self.handle._capture = Mock(side_effect=RuntimeError('capture unsupported'))
        self.assertIn('eager', self.handle(self.x))
        self.assertIn('eager', self.handle(self.x))
        self.handle._capture.assert_called_once()
        self.assertTrue(self.handle.disabled)
        self.assertEqual(self.handle.receipt['capture_error_type'], 'RuntimeError')

    def test_poisoned_cuda_context_is_not_hidden(self):
        self.handle._capture = Mock(side_effect=RuntimeError('capture error'))
        self.torch.cuda.synchronize.side_effect = RuntimeError('device unusable')
        with self.assertRaisesRegex(RuntimeError, 'unusable'):
            self.handle(self.x)

    def test_model_change_never_replays_stale_parameters_or_branches(self):
        self.install_capture()
        self.handle(self.x)
        self.state[:] = ['changed']
        with self.assertRaisesRegex(RuntimeError, 'state changed'):
            self.handle(self.x)
        self.assertEqual(self.handle.receipt['replays'], 1)

    def test_restore_removes_instance_override_and_releases_storage(self):
        self.install_capture()
        self.handle(self.x)
        self.handle.restore()
        self.handle.restore()
        self.assertNotIn('forward', vars(self.model))
        self.assertEqual(self.model.forward, self.original)
        self.assertIsNone(self.handle.graph)
        self.assertIsNone(self.handle.outputs)
        self.torch.cuda.synchronize.assert_called_once_with('cuda:0')
        with self.assertRaisesRegex(RuntimeError, 'restored'):
            self.handle(self.x)

    def test_existing_instance_override_is_restored(self):
        self.handle.restore()
        self.model.forward = self.original
        handle = module.ModuleForwardGraph(self.model, self.torch, name='own',
                                           argument_names=('x', 'extra_embed'), device='cuda:0')
        handle.restore()
        self.assertIs(vars(self.model)['forward'], self.original)

    def test_reentrancy_and_restore_while_running_fail(self):
        self.handle.lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, 'Concurrent'):
                self.handle(self.x)
            with self.assertRaisesRegex(RuntimeError, 'running'):
                self.handle.restore()
        finally:
            self.handle.lock.release()

    def test_failed_install_restores_original_forward(self):
        self.handle.restore()
        with patch.object(module, '_state_signature', side_effect=ValueError('cannot inspect')):
            with self.assertRaisesRegex(ValueError, 'cannot inspect'):
                module.ModuleForwardGraph(self.model, self.torch, name='failed',
                                          argument_names=('x', 'extra_embed'), device='cuda:0')
        self.assertNotIn('forward', vars(self.model))


class StateSignatureTests(unittest.TestCase):
    def test_public_branch_change_is_observed_without_tensor_changes(self):
        class Branch:
            def __init__(self):
                self.training = False
                self.repeat_pe = True
            def named_parameters(self): return []
            def named_buffers(self): return []
            def named_modules(self): return [('self', self)]
            def forward(self): pass
        value = Branch()
        before = module._state_signature(value)
        value.repeat_pe = False
        self.assertNotEqual(before, module._state_signature(value))

    def test_unknown_output_type_is_rejected(self):
        with self.assertRaisesRegex(TypeError, 'non-tensor output'):
            module._clone_tree({'output': 'unexpected'}, SimpleNamespace(Tensor=FakeTensor))


class CompositeRestoreTests(unittest.TestCase):
    def test_failed_restore_does_not_skip_remaining_modules_and_preserves_first_error(self):
        called = []
        first_error = RuntimeError('first failure in reverse order')
        def restore(name, error=None):
            def action():
                called.append(name)
                if error is not None:
                    raise error
            return action
        handles = [SimpleNamespace(receipt={'module': 'a'}, restore=restore('a')),
                   SimpleNamespace(receipt={'module': 'b'}, restore=restore('b', ValueError('second'))),
                   SimpleNamespace(receipt={'module': 'c'}, restore=restore('c', first_error))]
        composite = module.ForwardGraphHandle(handles, 'test', {})
        with self.assertRaises(RuntimeError) as caught:
            composite.restore()
        self.assertIs(caught.exception, first_error)
        self.assertEqual(called, ['c', 'b', 'a'])
        self.assertFalse(composite.receipt['restored'])
        self.assertEqual(composite.receipt['restore_failures'],
                         [{'module': 'c', 'error_type': 'RuntimeError'},
                          {'module': 'b', 'error_type': 'ValueError'}])

    def test_successful_retry_clears_previous_failure_receipt(self):
        action = Mock(side_effect=[RuntimeError('temporary failure'), None])
        composite = module.ForwardGraphHandle([SimpleNamespace(receipt={'module': 'a'}, restore=action)], 'test', {})
        with self.assertRaises(RuntimeError):
            composite.restore()
        composite.restore()
        self.assertTrue(composite.receipt['restored'])
        self.assertEqual(composite.receipt['restore_failures'], [])


if __name__ == '__main__':
    unittest.main()
