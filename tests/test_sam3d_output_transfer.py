"""CPU contracts and deterministic queue fakes; no CUDA or performance claims."""
from __future__ import annotations
from collections import OrderedDict
from contextlib import nullcontext
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rallymate_vision.sam3d_output_transfer import (
    _allocate_host_like, _BatchedCPUTransfer, _map_official_tree,
    apply_output_transfer, OFFICIAL_ESTIMATOR_SHA256, OFFICIAL_RECURSIVE_TO_SHA256,
)

ROOT = Path(__file__).resolve().parents[1]


def receipt():
    return {key: 0 for key in ('optimized_calls', 'completion_waits', 'copied_tensor_leaves',
        'copied_tensor_bytes', 'last_copied_tensor_leaves', 'last_copied_tensor_bytes',
        'cpu_only_calls', 'original_fallback_calls', 'transfer_error_calls')}


class ImportTests(unittest.TestCase):
    def test_module_import_is_lazy(self):
        code = '''
import builtins
original = builtins.__import__
def guard(name, *args, **kwargs):
    if name.split('.')[0] in {'torch', 'numpy', 'sam_3d_body'}:
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guard
import rallymate_vision.sam3d_output_transfer
'''
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True,
                       env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})


class FakeDevice:
    def __init__(self, value):
        self.type = str(value).split(':')[0]
        self.index = int(str(value).split(':')[1]) if ':' in str(value) else None

    def __eq__(self, other):
        return isinstance(other, FakeDevice) and self.type == other.type and self.index == other.index

    def __str__(self):
        return self.type + (f':{self.index}' if self.index is not None else '')


class FakeTensor:
    layout = 'strided'
    is_quantized = False

    def __init__(self, backend, values, device='cuda:0'):
        self.backend, self.values, self.device = backend, values, FakeDevice(device)

    def is_conj(self):
        return False

    def is_neg(self):
        return False

    def numel(self):
        return len(self.values)

    def element_size(self):
        return 4

    def copy_(self, source, *, non_blocking):
        assert non_blocking is True
        self.backend.events.append('copy')
        if len(self.backend.pending) == self.backend.fail_copy_index:
            raise RuntimeError('injected copy failure')
        self.backend.pending.append((self, source))


class FakeTorch:
    Tensor = FakeTensor
    strided = 'strided'
    preserve_format = 'preserve_format'

    def __init__(self):
        self.pending, self.events = [], []
        self.fail_copy_index = None
        self.fail_wait = False
        self.grad = False
        self.cuda = SimpleNamespace(device=lambda _: nullcontext(),
            current_stream=lambda _: self, Event=self.event)

    @staticmethod
    def device(value):
        return value if isinstance(value, FakeDevice) else FakeDevice(value)

    def is_grad_enabled(self):
        return self.grad

    def empty_like(self, source, *, device, pin_memory, memory_format):
        assert device == 'cpu' and pin_memory and memory_format == self.preserve_format
        return FakeTensor(self, [None] * source.numel(), device='cpu')

    def synchronize(self):
        self.events.append('stream_wait')
        if self.fail_wait:
            raise RuntimeError('injected unresolved wait')
        self.finish()

    def finish(self):
        for destination, source in self.pending:
            destination.values = list(source.values)
        self.pending.clear()

    def event(self, *, blocking):
        assert blocking is True
        backend = self

        class Event:
            def record(self, stream):
                assert stream is backend
                backend.events.append('event_record')

            def synchronize(self):
                backend.events.append('event_wait')
                if backend.fail_wait:
                    raise RuntimeError('injected event failure')
                backend.finish()

        return Event()


class QueueContractTests(unittest.TestCase):
    def make(self):
        torch = FakeTorch()
        calls = []

        def original(value, target):
            calls.append((value, target))
            return ('original', value, target)

        counts = receipt()
        return torch, calls, counts, _BatchedCPUTransfer(original, torch, 'cuda:0', counts)

    def test_complete_tree_order_all_copies_then_one_wait_and_fresh_storage(self):
        torch, original, counts, transfer = self.make()
        source = FakeTensor(torch, [1, 2, 3])
        opaque_tuple = (source,)
        cpu = FakeTensor(torch, [9], 'cpu')
        tree = OrderedDict([('all_layers', [source] * 22), ('cpu', cpu), ('opaque', opaque_tuple), ('none', None)])
        result = transfer(tree, 'cpu')
        self.assertEqual(type(result), dict)
        self.assertEqual(list(result), list(tree))
        self.assertIs(result['cpu'], cpu)
        self.assertIs(result['opaque'], opaque_tuple)
        self.assertEqual(torch.events, ['copy'] * 22 + ['event_record', 'event_wait'])
        self.assertEqual([item.values for item in result['all_layers']], [[1, 2, 3]] * 22)
        self.assertEqual(len({id(item) for item in result['all_layers']}), 22)
        self.assertEqual(counts['completion_waits'], 1)
        self.assertEqual(counts['copied_tensor_leaves'], 22)
        self.assertEqual(counts['copied_tensor_bytes'], 22 * 3 * 4)
        source.values[0] = 999
        second = transfer(tree, 'cpu')
        self.assertEqual(result['all_layers'][0].values, [1, 2, 3])
        self.assertEqual(second['all_layers'][0].values, [999, 2, 3])
        self.assertEqual(original, [])

    def test_unsupported_device_falls_back_before_any_queue(self):
        torch, original, counts, transfer = self.make()
        tree = [FakeTensor(torch, [1]), FakeTensor(torch, [2], 'cuda:1')]
        result = transfer(tree, 'cpu')
        self.assertEqual(result[0], 'original')
        self.assertEqual(original, [(tree, 'cpu')])
        self.assertEqual(torch.events, [])
        self.assertEqual(counts['original_fallback_calls'], 1)

    def test_cuda_and_numpy_targets_stay_official(self):
        torch, original, counts, transfer = self.make()
        tree = [FakeTensor(torch, [1])]
        transfer(tree, 'cuda')
        transfer(tree, 'numpy')
        self.assertEqual([target for _, target in original], ['cuda', 'numpy'])
        self.assertEqual(torch.events, [])
        self.assertEqual(counts['original_fallback_calls'], 0)

    def test_partial_copy_failure_drains_before_release_and_does_not_return(self):
        torch, _, counts, transfer = self.make()
        torch.fail_copy_index = 1
        with self.assertRaisesRegex(RuntimeError, 'copy failure'):
            transfer([FakeTensor(torch, [1]), FakeTensor(torch, [2])], 'cpu')
        self.assertEqual(torch.events, ['copy', 'copy', 'stream_wait'])
        self.assertIsNone(transfer._pending)
        self.assertEqual(counts['optimized_calls'], 0)
        self.assertEqual(counts['original_fallback_calls'], 0)
        with self.assertRaisesRegex(RuntimeError, 'unresolved transfer failure'):
            transfer([], 'cpu')

    def test_unresolved_wait_retains_sources_and_destinations_until_close(self):
        torch, _, _, transfer = self.make()
        torch.fail_wait = True
        source = FakeTensor(torch, [1])
        with self.assertRaises(RuntimeError):
            transfer([source], 'cpu')
        self.assertIs(transfer._pending[1][0][1], source)
        torch.fail_wait = False
        transfer.close()
        self.assertIsNone(transfer._pending)
        self.assertTrue(transfer._closed)

    def test_grad_enabled_falls_back_without_queue(self):
        torch, _, counts, transfer = self.make()
        torch.grad = True
        transfer(FakeTensor(torch, [1]), 'cpu')
        self.assertEqual(counts['last_fallback_reason'], 'grad_enabled')
        self.assertEqual(torch.events, [])


@unittest.skipUnless(importlib.util.find_spec('torch') is not None, 'CPU torch required')
class CPUValueLayoutTests(unittest.TestCase):
    def test_preserve_format_matches_copy_for_shapes_strides_dtypes_and_values(self):
        import torch
        base = torch.arange(120, dtype=torch.float32).reshape(4, 5, 6)
        values = [base, base.permute(2, 0, 1), base[:, ::2, :], base[:, :, ::2],
                  base[:1].expand(7, -1, -1), torch.tensor(5.), torch.empty((0, 3)),
                  torch.rand(2, 3, 4, 5).to(memory_format=torch.channels_last)]
        values.extend(torch.arange(12).reshape(3, 4).t().to(dtype) for dtype in
            (torch.float16, torch.bfloat16, torch.float64, torch.int64, torch.int32, torch.bool, torch.complex64))
        with torch.no_grad():
            for value in values:
                reference = value.to('cpu', copy=True)
                actual = _allocate_host_like(torch, value, pin_memory=False)
                actual.copy_(value)
                self.assertEqual(actual.dtype, reference.dtype)
                self.assertEqual(actual.shape, reference.shape)
                self.assertEqual(actual.stride(), reference.stride())
                self.assertEqual(actual.storage_offset(), reference.storage_offset())
                self.assertTrue(torch.equal(actual, reference))

    def test_cpu_leaves_and_opaque_tuples_keep_official_identity(self):
        import torch

        def recursive(value, target):
            return _map_official_tree(value, torch.Tensor, lambda tensor: tensor.to(target))

        source = torch.arange(3)
        opaque = (source,)
        tree = {'a': [source, source], 'b': opaque}
        counts = receipt()
        transfer = _BatchedCPUTransfer(recursive, torch, 'cuda:0', counts)
        with torch.no_grad():
            result = transfer(tree, 'cpu')
        self.assertIs(result['a'][0], source)
        self.assertIs(result['a'][1], source)
        self.assertIs(result['b'], opaque)
        self.assertEqual(counts['cpu_only_calls'], 1)

    def test_instance_scoping_original_globals_and_restoration(self):
        import torch
        original_recursive = lambda value, target: value
        globals_map = {'recursive_to': original_recursive}
        exec('def process(self, value):\n return recursive_to(value, "cpu")', globals_map)
        method = torch.no_grad()(globals_map['process'])
        estimator_type = type('Estimator', (), {'process_one_image': method})
        estimator = estimator_type()
        estimator.model = SimpleNamespace(training=False)
        estimator.device = 'cuda:0'
        with patch('rallymate_vision.sam3d_output_transfer._source_sha256',
                   side_effect=[OFFICIAL_ESTIMATOR_SHA256, OFFICIAL_RECURSIVE_TO_SHA256]), \
             patch.dict(os.environ, {'RALLYMATE_SAM3D_PROFILE_STAGES': '0', 'RALLYMATE_SAM3D_REUSE_CUDA_CACHE': '0'}):
            handle = apply_output_transfer(estimator)
        self.assertIs(globals_map['recursive_to'], original_recursive)
        self.assertIn('process_one_image', vars(estimator))
        self.assertEqual(estimator.process_one_image({'value': 7}), {'value': 7})
        handle.restore()
        handle.restore()
        self.assertNotIn('process_one_image', vars(estimator))
        self.assertIs(estimator_type.process_one_image, method)
        self.assertIs(globals_map['recursive_to'], original_recursive)


if __name__ == '__main__':
    unittest.main()
