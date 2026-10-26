"""Complete decoder ownership and dynamic contracts without CUDA execution."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rallymate_vision import sam3d_decoder_graph as module


class Tensor:
    def __init__(self, values, shape=(1, 2), device="cuda:0", dtype="float32"):
        self.values = list(values)
        self.shape, self.ndim = shape, len(shape)
        self.device, self.dtype, self.layout, self.is_cuda = device, dtype, "strided", True
        self._version = 0

    def data_ptr(self):
        return id(self)

    def stride(self):
        return tuple(range(self.ndim, 0, -1))

    def detach(self):
        return self

    def clone(self):
        return Tensor(self.values, self.shape, self.device, self.dtype)

    def copy_(self, source):
        self.values[:] = source.values


class Model:
    training = False
    body_batch_idx, hand_batch_idx = [0], []
    _batch_size, _max_num_person = 1, 1

    def __init__(self):
        self.head_pose = SimpleNamespace(faces=Tensor([0]))
        self.prompt_encoder = Mock(side_effect=lambda keypoints: (keypoints.clone(), Tensor([1])))

    def forward_decoder(self, image_embeddings, init_estimate=None, keypoints=None,
                        prev_estimate=None, condition_info=None, batch=None):
        return {"eager": image_embeddings.clone(), "init": init_estimate, "batch": batch}


class DecoderContracts(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = [1]
        self.stack.enter_context(patch.object(module, "_decoder_state", side_effect=lambda _: tuple(self.state)))
        self.model = Model()
        self.torch = SimpleNamespace(Tensor=Tensor, strided="strided", is_grad_enabled=lambda: False,
                                     is_autocast_enabled=lambda: False,
                                     cuda=SimpleNamespace(synchronize=Mock()))
        self.head = SimpleNamespace(restored=False)
        self.handle = module.DecoderGraphHandle(self.model, self.torch, self.head)
        self.image = Tensor([1], (1, 2, 3, 4))
        self.points = Tensor([2], (1, 1, 3))
        self.condition = Tensor([3])
        self.batch = {name: Tensor([index]) for index, name in enumerate(module.BATCH_KEYS)}
        self.batch['img'] = Tensor([4], (1, 1, 3, 2, 2))

    def call(self, **overrides):
        kwargs = dict(keypoints=self.points, condition_info=self.condition, batch=self.batch)
        kwargs.update(overrides)
        return self.handle(self.image, **kwargs)

    def install_capture(self):
        def capture(values):
            self.handle.inputs = module._clone_inputs(values, self.torch)
            output = Tensor([0])
            def replay():
                a, b, c, batch, prompt = self.handle.inputs
                output.values[:] = [a.values[0] + b.values[0] + c.values[0]
                                    + batch['bbox_center'].values[0] + prompt[0].values[0]]
            self.handle.graph = SimpleNamespace(replay=replay)
            self.handle.outputs = (output, [{"vertices": output, "optional": None} for _ in range(6)])
        self.handle._capture = Mock(side_effect=capture)

    def test_all_dynamic_input_values_and_all_six_independent_outputs(self):
        self.install_capture()
        first = self.call()
        self.image.values[:] = [10]
        self.points.values[:] = [20]
        self.condition.values[:] = [30]
        self.batch['bbox_center'].values[:] = [40]
        second = self.call()
        self.assertEqual(first[0].values, [9])
        self.assertEqual(second[0].values, [120])
        self.assertEqual(len(second[1]), 6)
        second[1][0]['vertices'].values[:] = [99]
        self.assertEqual(second[1][1]['vertices'].values, [120])
        self.assertEqual(first[1][0]['vertices'].values, [9])
        self.assertEqual(self.handle.receipt['body_head_executions'], 12)
        self.assertEqual(self.model.prompt_encoder.call_count, 2)
        self.handle._capture.assert_called_once()

    def test_missing_batch_key_or_nontensor_argument_is_eager(self):
        self.install_capture()
        self.assertEqual(self.call(init_estimate=True)['init'], True)
        del self.batch['cam_int']
        self.assertIn('eager', self.call())
        self.handle._capture.assert_not_called()
        self.model.prompt_encoder.assert_not_called()

    def test_changed_batch_value_shape_or_dtype_does_not_make_second_graph(self):
        self.install_capture()
        self.call()
        self.batch['affine_trans'].dtype = 'float64'
        self.assertIn('eager', self.call())
        self.assertEqual(self.handle.receipt['last_fallback_reason'], 'different_input_signature')
        self.handle._capture.assert_called_once()

    def test_different_device_and_batch_layout_are_eager(self):
        self.install_capture()
        self.points.device = 'cuda:1'
        self.assertIn('eager', self.call())
        self.points.device = 'cuda:0'
        self.batch['img'].shape = (1, 2, 3, 2, 2)
        self.assertIn('eager', self.call())
        self.handle._capture.assert_not_called()

    def test_boolean_body_index_does_not_alias_integer_zero_contract(self):
        self.install_capture()
        self.model.body_batch_idx = [False]
        self.assertIn('eager', self.call())
        self.handle._capture.assert_not_called()

    def test_constant_index_mutation_is_detected_before_replay(self):
        self.install_capture()
        index = Tensor([0])
        self.handle.constants = (index,)
        self.handle.constant_state = module._constant_signature(self.handle.constants)
        self.call()
        index._version += 1
        with self.assertRaisesRegex(RuntimeError, 'state changed'):
            self.call()
        self.assertEqual(self.handle.receipt['replays'], 1)

    def test_prompt_assertion_runs_on_every_replay_and_rejects_bad_value(self):
        self.install_capture()
        self.call()
        self.model.prompt_encoder.side_effect = AssertionError('coordinate range')
        with self.assertRaisesRegex(AssertionError, 'range'):
            self.call()
        self.assertEqual(self.handle.receipt['replays'], 1)

    def test_capture_error_falls_back_and_never_retries(self):
        self.handle._capture = Mock(side_effect=RuntimeError('unsupported capture'))
        self.assertIn('eager', self.call())
        self.assertIn('eager', self.call())
        self.handle._capture.assert_called_once()
        self.assertEqual(self.handle.receipt['capture_status'], 'fallback_after_capture_error')

    def test_poisoned_context_propagates(self):
        self.handle._capture = Mock(side_effect=RuntimeError('capture'))
        self.torch.cuda.synchronize.side_effect = RuntimeError('device unusable')
        with self.assertRaisesRegex(RuntimeError, 'unusable'):
            self.call()

    def test_state_mutation_never_replays_old_graph(self):
        self.install_capture()
        self.call()
        self.state.append(2)
        with self.assertRaisesRegex(RuntimeError, 'state changed'):
            self.call()
        self.assertEqual(self.handle.receipt['replays'], 1)

    def test_autocast_remains_eager(self):
        self.install_capture()
        self.torch.is_autocast_enabled = lambda: True
        self.assertIn('eager', self.call())
        self.handle._capture.assert_not_called()

    def test_restore_owns_only_its_method_and_releases_graph(self):
        self.install_capture()
        self.call()
        self.handle.restore()
        self.handle.restore()
        self.assertNotIn('forward_decoder', vars(self.model))
        self.assertFalse(self.head.restored)
        self.assertIsNone(self.handle.graph)
        self.assertIsNone(self.handle.inputs)
        self.torch.cuda.synchronize.assert_called_once_with('cuda:0')
        with self.assertRaisesRegex(RuntimeError, 'restored'):
            self.call()

    def test_failed_install_restores_original_method(self):
        self.handle.restore()
        with patch.object(module, '_decoder_state', side_effect=ValueError('unsupported state')):
            with self.assertRaisesRegex(ValueError, 'unsupported state'):
                module.DecoderGraphHandle(self.model, self.torch, self.head)
        self.assertNotIn('forward_decoder', vars(self.model))

    def test_concurrent_use_or_restore_is_rejected(self):
        self.handle.lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, 'Concurrent'):
                self.call()
            with self.assertRaisesRegex(RuntimeError, 'running'):
                self.handle.restore()
        finally:
            self.handle.lock.release()


class BindingContracts(unittest.TestCase):
    def test_binding_failure_restores_prior_bindings(self):
        class Owner:
            def forward(self): return 'original'
        owner = Owner()
        original = owner.forward
        with self.assertRaisesRegex(RuntimeError, 'capture failed'):
            with module._temporary_bindings([(owner, 'forward', lambda: 'candidate')]):
                self.assertEqual(owner.forward(), 'candidate')
                raise RuntimeError('capture failed')
        self.assertNotIn('forward', vars(owner))
        self.assertEqual(owner.forward, original)

    def test_config_and_index_list_mutation_is_not_frozen(self):
        cfg = {'MODEL': {'flags': [True, 3], 'value': None}}
        before = module._freeze(cfg)
        cfg['MODEL']['flags'][1] = 4
        self.assertNotEqual(before, module._freeze(cfg))

    def test_mutable_numpy_output_is_rejected_and_immutable_topology_is_shared(self):
        import numpy as np
        faces = np.arange(9).reshape(3, 3)
        torch = SimpleNamespace(Tensor=Tensor)
        with self.assertRaisesRegex(TypeError, 'mutable/non-tensor'):
            module._clone_outputs(faces, torch)
        faces.setflags(write=False)
        self.assertIs(faces, module._clone_outputs(faces, torch))


def projection(value):
    return value + 7


class IndexFixture:
    pelvis_idx = [2, 0]

    def apply(self, value):
        num_pose_token = 1
        assert num_pose_token == 1
        copied = value.clone()
        copied[:, [0, 2]] *= -1
        return (value[:, [0, 2, -1]], value[:, [num_pose_token]],
                value[:, [self.pelvis_idx[0]]], copied)

    def projection(self, value):
        return projection(value[:, [0, 2]])


class DeviceIndexContracts(unittest.TestCase):
    def test_advanced_index_values_shapes_strides_and_updates_are_unchanged(self):
        import torch
        owner = IndexFixture()
        fn, constants, changes = module._compile_device_indices(owner.apply, owner, torch, 'cpu')
        for dtype in (torch.float32, torch.float64):
            value = torch.arange(32, dtype=dtype).reshape(4, 8)[:, ::2]
            original, candidate = owner.apply(value), fn(value)
            self.assertEqual(len(original), len(candidate))
            for before, after in zip(original, candidate):
                self.assertTrue(torch.equal(before, after))
                self.assertEqual((before.shape, before.stride(), before.dtype),
                                 (after.shape, after.stride(), after.dtype))
                self.assertNotEqual(after.data_ptr(), value.data_ptr())
        self.assertEqual(len(constants), 4)
        self.assertEqual([item['values'] for item in changes], [[0, 2], [0, 2, -1], [1], [2]])

    def test_copied_method_does_not_shadow_same_named_geometry_global(self):
        import torch
        owner = IndexFixture()
        fn, _, _ = module._compile_device_indices(owner.projection, owner, torch, 'cpu')
        value = torch.arange(12).reshape(3, 4)
        self.assertTrue(torch.equal(owner.projection(value), fn(value)))


if __name__ == '__main__':
    unittest.main()
