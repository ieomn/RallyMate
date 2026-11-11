from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rallymate_vision import detector_forward_graph as graph
from rallymate_vision.inference import Yolo26Perception
from rallymate_vision.owned_subprocess import ProcessTreeCleanupError


class Tensor:
    def __init__(self, shape=(1, 3, 4, 4), values=None):
        self.shape, self.dtype, self.device, self.is_cuda = shape, 'fp32', 'cuda:0', True
        self._version = 0
        self.layout, self.offset, self.changed_stride = 'strided', 0, None
        self.values = list(values or [1, 2])

    def stride(self):
        return self.changed_stride or (self.shape[1] * self.shape[2] * self.shape[3], self.shape[2] * self.shape[3], self.shape[3], 1)

    def storage_offset(self):
        return self.offset

    def data_ptr(self):
        return 100

    def clone(self):
        return Tensor(self.shape, self.values)


def fake_torch():
    registry = SimpleNamespace(**{name: {} for name in (
        '_global_forward_pre_hooks', '_global_forward_hooks',
        '_global_forward_hooks_always_called', '_global_forward_hooks_with_kwargs')})
    return SimpleNamespace(Tensor=Tensor, float32='fp32',
        nn=SimpleNamespace(modules=SimpleNamespace(module=registry)),
        cuda=SimpleNamespace(synchronize=Mock()),
        backends=SimpleNamespace(cudnn=SimpleNamespace(benchmark=False, deterministic=False, allow_tf32=True),
                                 cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=False))),
        get_float32_matmul_precision=lambda: 'highest', are_deterministic_algorithms_enabled=lambda: False,
        is_deterministic_algorithms_warn_only_enabled=lambda: False,
        is_grad_enabled=lambda: False, is_autocast_enabled=lambda: False)


def bare_handle(*, captured=True):
    handle = object.__new__(graph.DetectorForwardGraph)
    handle.torch = fake_torch()
    handle.device = 'cuda:0'
    handle.lock = threading.Lock()
    handle.closed = handle.failed = handle.disabled = handle.cleanup_failed = False
    handle.graph = Mock() if captured else None
    handle.inputs = object() if captured else None
    handle.outputs = object() if captured else None
    handle.external_tensors = [object()]
    handle.model = SimpleNamespace(training=False)
    handle.model.forward = handle
    handle.original = Mock(return_value='original-output')
    handle.had_own_forward = True
    handle.original_own_forward = handle.original
    handle.state = 'fixed-state'
    handle.precision = graph._precision(handle.torch)
    handle.global_hooks = graph._global_hooks(handle.torch)
    handle.kwargs = {'augment': False, 'visualize': False, 'embed': None}
    x = Tensor()
    handle.input_signature = (tuple(x.shape), tuple(x.stride()), x.dtype, x.device) if captured else None
    handle.receipt = {'status': 'captured', 'eager_fallback_calls': 0, 'replay_calls': 0,
                      'last_fallback_reason': None, 'retained_delta_bytes': 123}
    return handle


class DetectorForwardGraphTests(unittest.TestCase):
    def test_default_disabled_and_strict_configuration(self):
        self.assertFalse(graph.DetectorGraphOptions.from_environment({}).enabled)
        for bad in ('', 'true', '2', True, None, []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                graph.DetectorGraphOptions.from_environment({graph.ENABLED_ENV: bad})
        for bad in ('0', '15', '513', '256.0', '-1', '２５６'):
            with self.subTest(budget=bad), self.assertRaises(ValueError):
                graph.DetectorGraphOptions.from_environment({graph.BUDGET_ENV: bad})
        options = graph.DetectorGraphOptions.from_environment({graph.ENABLED_ENV: '1', graph.BUDGET_ENV: '32'})
        self.assertTrue(options.enabled)
        self.assertEqual(options.max_retained_bytes, 32 * 1024**2)

    def test_default_off_never_constructs_graph_or_loads_an_extra_model(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'detect.pt'
            checkpoint.write_bytes(b'weights')
            yolo = Mock(return_value=SimpleNamespace(names={0: 'person'}))
            backend = Mock()
            backend.metadata.return_value = SimpleNamespace(model_path='pose.pt')
            with patch.dict(os.environ, {}, clear=True), \
                 patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=yolo)}), \
                 patch.object(graph, 'DetectorGraphController', side_effect=AssertionError('must remain off')), \
                 patch.object(graph, '_source_identity', side_effect=AssertionError('must not hash')):
                perception = Yolo26Perception(checkpoint, Path('pose.pt'), 'cpu', 960, 640, .15, .25,
                                              pose_backend_instance=backend)
                self.assertIsNone(perception._detector_forward_graph)
                self.assertEqual(perception.detection_metadata()['forward_graph'], {'requested': False, 'status': 'disabled'})
                yolo.assert_called_once_with(str(checkpoint))
                perception.close()
                backend.close.assert_called_once_with()

    def test_invalid_flag_fails_before_model_load(self):
        yolo = Mock()
        with patch.dict(os.environ, {graph.ENABLED_ENV: 'yes'}, clear=True), \
             patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=yolo)}), self.assertRaises(ValueError):
            Yolo26Perception(Path('absent.pt'), Path('pose.pt'), 'cpu', 960, 640, .15, .25)
        yolo.assert_not_called()

    def controller(self, *, actual_process=True):
        class Process:
            pass
        backend = SimpleNamespace(engine=Process() if actual_process else object())
        with patch.dict(sys.modules, {'rallymate_vision.sam3d_runner': SimpleNamespace(Sam3DProcess=Process)}):
            return graph.DetectorGraphController(backend, graph.DetectorGraphOptions(True))

    def test_actual_external_engine_required_and_first_predict_untouched(self):
        unsupported = self.controller(actual_process=False)
        self.assertTrue(unsupported.disabled)
        self.assertEqual(unsupported.metadata()['reason'], 'requires_actual_isolated_sam3d_process')
        controller = self.controller()
        predictor = SimpleNamespace(model=SimpleNamespace(model='model'))
        handle = Mock(model='model')
        with patch.object(graph, '_qualify', return_value=('model', 'torch', 'source')) as qualify, \
             patch.object(graph, 'DetectorForwardGraph', return_value=handle) as create:
            controller.before_predict(None)
            qualify.assert_not_called()
            controller.after_predict(predictor)
            controller.before_predict(predictor)
            controller.after_predict(predictor)
            qualify.assert_called_once_with(predictor)
            create.assert_called_once()
        self.assertEqual(controller.metadata()['initial_eager_predict_calls'], 1)

    def test_unsupported_source_keeps_original_eager_path(self):
        controller = self.controller()
        with patch.object(graph, '_qualify', side_effect=graph._Unsupported('source_hash_mismatch:head')):
            controller.after_predict(SimpleNamespace(model=object()))
        self.assertTrue(controller.disabled)
        self.assertIsNone(controller.handle)
        self.assertEqual(controller.metadata()['reason'], 'source_hash_mismatch:head')

    def test_backend_replacement_releases_old_model_references_once(self):
        controller = self.controller()
        old = Mock()
        old.metadata.return_value = {'closed': True, 'replay_calls': 3}
        controller.handle, controller.backend = old, object()
        replacement = SimpleNamespace(model=object())
        controller.before_predict(replacement)
        controller.before_predict(replacement)
        controller.after_predict(replacement)
        old.close.assert_called_once_with()
        self.assertIsNone(controller.handle)
        self.assertIsNone(controller.backend)
        self.assertTrue(controller.disabled)
        self.assertEqual(controller.metadata()['runtime'], {'closed': True, 'replay_calls': 3})

    def test_inner_model_replacement_also_releases_old_references(self):
        controller = self.controller()
        old_model = object()
        old = Mock(model=old_model)
        old.metadata.return_value = {'closed': True}
        controller.handle, controller.backend = old, SimpleNamespace(model=old_model)
        predictor = SimpleNamespace(model=controller.backend)
        controller.backend.model = object()
        controller.before_predict(predictor)
        controller.before_predict(predictor)
        old.close.assert_called_once_with()
        self.assertIsNone(controller.backend)
        self.assertIsNone(controller.handle)
        self.assertEqual(controller.metadata()['reason'], 'predictor_inner_model_replaced')

    def test_new_shape_synchronizes_releases_then_uses_original_and_stays_eager(self):
        handle = bare_handle()
        old_graph = handle.graph
        def original(x, **kwargs):
            self.assertIsNone(handle.graph)
            self.assertIsNone(handle.inputs)
            handle.torch.cuda.synchronize.assert_called_once_with('cuda:0')
            return x.shape
        handle.original = Mock(side_effect=original)
        with patch.object(graph, '_state', return_value='fixed-state'):
            changed = Tensor((1, 3, 8, 4))
            self.assertEqual(handle(changed, **handle.kwargs), changed.shape)
            self.assertEqual(handle(Tensor(), **handle.kwargs), (1, 3, 4, 4))
        old_graph.replay.assert_not_called()
        self.assertEqual(handle.receipt['eager_fallback_calls'], 2)
        self.assertEqual(handle.receipt['last_fallback_reason'], 'input_shape_stride_or_device_changed')

    def test_inplace_weight_version_and_precision_changes_fail_closed(self):
        tensor = Tensor()
        first = graph._tensor_state(tensor)
        tensor._version += 1
        self.assertNotEqual(first, graph._tensor_state(tensor))
        self.assertEqual(first[1], graph._tensor_state(tensor)[1])
        handle = bare_handle()
        handle.state = first
        with patch.object(graph, '_state', return_value=graph._tensor_state(tensor)), self.assertRaisesRegex(RuntimeError, 'state changed'):
            handle(Tensor(), **handle.kwargs)
        handle.graph.replay.assert_not_called()
        handle.original.assert_not_called()
        handle = bare_handle()
        handle.torch.backends.cudnn.allow_tf32 = False
        with patch.object(graph, '_state', return_value='fixed-state'), self.assertRaisesRegex(RuntimeError, 'state changed'):
            handle(Tensor(), **handle.kwargs)
        handle.graph.replay.assert_not_called()

    def test_same_storage_shape_and_version_view_changes_are_detected(self):
        tensor = Tensor()
        first = graph._tensor_state(tensor)
        tensor.changed_stride = (48, 16, 1, 4)
        changed = graph._tensor_state(tensor)
        self.assertEqual(first[:6], changed[:6])
        self.assertNotEqual(first, changed)
        tensor.changed_stride = None
        tensor.offset = 1
        self.assertNotEqual(first, graph._tensor_state(tensor))
        tensor.offset = 0
        tensor.layout = 'other_layout'
        self.assertNotEqual(first, graph._tensor_state(tensor))

    def test_global_forward_hooks_missing_or_added_cannot_be_silently_skipped(self):
        torch = fake_torch()
        before = graph._global_hooks(torch)
        torch.nn.modules.module._global_forward_pre_hooks[1] = lambda *_: None
        self.assertNotEqual(before, graph._global_hooks(torch))
        del torch.nn.modules.module._global_forward_hooks
        with self.assertRaises(graph._Unsupported):
            graph._global_hooks(torch)
        handle = bare_handle()
        handle.torch.nn.modules.module._global_forward_hooks[7] = lambda *_: None
        with patch.object(graph, '_state', return_value='fixed-state'), self.assertRaisesRegex(RuntimeError, 'state changed'):
            handle(Tensor(), **handle.kwargs)
        handle.graph.replay.assert_not_called()

    def test_capture_oom_is_propagated_without_eager_fallback(self):
        handle = bare_handle(captured=False)
        error = MemoryError('synthetic CUDA OOM')
        handle._capture = Mock(side_effect=error)
        with patch.object(graph, '_state', return_value='fixed-state'), self.assertRaises(graph.DetectorGraphFatalError) as raised:
            handle(Tensor(), **handle.kwargs)
        self.assertIs(raised.exception.__cause__, error)
        self.assertEqual(handle.receipt['error_type'], 'MemoryError')
        handle.original.assert_not_called()
        self.assertTrue(handle.failed)
        self.assertEqual(handle.receipt['eager_fallback_calls'], 0)

    def test_interrupts_are_not_wrapped_and_concurrent_use_is_worker_fatal(self):
        for error in (KeyboardInterrupt(), SystemExit(5)):
            handle = bare_handle(captured=False)
            handle._capture = Mock(side_effect=error)
            with patch.object(graph, '_state', return_value='fixed-state'), self.assertRaises(type(error)) as raised:
                handle(Tensor(), **handle.kwargs)
            self.assertIs(raised.exception, error)
        handle = bare_handle()
        handle.lock.acquire()
        try:
            with self.assertRaises(graph.DetectorGraphFatalError):
                handle(Tensor(), **handle.kwargs)
            self.assertTrue(handle.failed)
            handle.graph.replay.assert_not_called()
        finally:
            handle.lock.release()

    def test_graph_fatal_crosses_process_one_without_reusable_job_failure(self):
        from rallymate_service import worker
        handle = bare_handle(captured=False)
        error = MemoryError('synthetic CUDA OOM')
        handle._capture = Mock(side_effect=error)
        def runner(request):
            return handle(Tensor(), **handle.kwargs)
        settings = SimpleNamespace(validate_license=Mock(), detect_model=Path(__file__),
                                   pose_model=Path(__file__), pose_config=None, lease_seconds=60, max_attempts=3)
        database = Mock()
        database.claim_next.return_value = {'id': 'job', 'attempts': 1, 'request_path': 'unused.json'}
        database.execution_state.return_value = {'status': 'running', 'worker_id': 'worker',
                                                'attempts': 1, 'cancel_requested': False}
        with patch.object(worker, 'load_request', return_value=SimpleNamespace(upstream_metadata={})), \
             patch.object(graph, '_state', return_value='fixed-state'), self.assertRaises(graph.DetectorGraphFatalError) as raised:
            worker.process_one(settings, database, 'worker', runner)
        self.assertIs(raised.exception.__cause__, error)
        database.mark_failed.assert_not_called()
        database.mark_succeeded.assert_not_called()
        database.claim_next.assert_called_once()

    def test_cleanup_failure_is_sticky_and_pins_graph_storage(self):
        handle = bare_handle()
        original_graph = handle.graph
        handle.torch.cuda.synchronize.side_effect = RuntimeError('sync failed')
        for _ in range(2):
            with self.assertRaises(ProcessTreeCleanupError):
                handle.close()
        handle.torch.cuda.synchronize.assert_called_once()
        self.assertIs(handle.graph, original_graph)
        self.assertIsNotNone(handle.inputs)
        self.assertFalse(handle.closed)

    def test_perception_closes_sam_even_when_graph_cleanup_fails(self):
        perception = object.__new__(Yolo26Perception)
        perception._detector_forward_graph = Mock()
        perception._detector_forward_graph.close.side_effect = ProcessTreeCleanupError('graph failed')
        perception.pose_backend = Mock()
        with self.assertRaises(ProcessTreeCleanupError):
            perception.close()
        perception.pose_backend.close.assert_called_once_with()

    def test_partial_method_restored_on_success_and_exception(self):
        class Head:
            def postprocess(self, value):
                return value * 2
        for own in (False, True):
            for failure in (False, True):
                head = Head()
                if own:
                    head.postprocess = lambda value: value * 3
                handle = object.__new__(graph.DetectorForwardGraph)
                handle.head = head
                handle.postprocess_identity = graph._identity(head.postprocess)
                with self.assertRaises(ValueError) if failure else nullcontext():
                    with handle._without_postprocess():
                        self.assertEqual(head.postprocess(5), 5)
                        if failure:
                            raise ValueError('capture failed')
                self.assertEqual(head.postprocess(5), 15 if own else 10)
                self.assertEqual('postprocess' in head.__dict__, own)
                self.assertEqual(graph._identity(head.postprocess), handle.postprocess_identity)

    def test_constructor_state_failure_restores_original_forward(self):
        class Model:
            def forward(self, value):
                return value
        model = Model()
        model.model = [SimpleNamespace(postprocess=lambda value: value)]
        original = graph._identity(model.forward)
        with patch.object(graph, '_state', side_effect=RuntimeError('bad state')), self.assertRaisesRegex(RuntimeError, 'bad state'):
            graph.DetectorForwardGraph(model, fake_torch(), graph.DetectorGraphOptions(True), {})
        self.assertNotIn('forward', model.__dict__)
        self.assertEqual(graph._identity(model.forward), original)

    def test_preinstalled_runtime_wrappers_rejected_but_official_fused_forward_allowed(self):
        class Layer:
            def forward(self, value):
                return value
            def forward_fuse(self, value):
                return value
            def postprocess(self, value):
                return value
        layer = Layer()
        self.assertIs(graph._require_official_forward(layer, (Layer,)), Layer.forward)
        graph._require_original_method(layer, 'postprocess', Layer.postprocess)
        layer.forward = layer.forward_fuse
        self.assertIs(graph._require_official_forward(layer, (Layer,)), Layer.forward_fuse)
        layer.forward = lambda value: value
        with self.assertRaises(graph._Unsupported):
            graph._require_official_forward(layer, (Layer,))
        layer.postprocess = lambda value: value
        with self.assertRaises(graph._Unsupported):
            graph._require_original_method(layer, 'postprocess', Layer.postprocess)

    def test_complete_output_clone_is_independent_and_retains_repeated_references(self):
        value = Tensor(values=[3, 4])
        tree = (value, {'one2one': {'features': [value]}, 'one2many': {}})
        cloned = graph._clone_tree(tree, fake_torch())
        value.values[0] = 99
        self.assertEqual(cloned[0].values, [3, 4])
        self.assertIs(cloned[0], cloned[1]['one2one']['features'][0])
        self.assertIsNot(cloned[1], tree[1])

    def test_source_hash_is_verified_without_model_import(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / '__init__.py').write_text('')
            source = base / 'head.py'
            source.write_bytes(b'audited')
            digest = hashlib.sha256(b'audited').hexdigest()
            package = SimpleNamespace(__version__='8.4.21', __file__=str(base / '__init__.py'))
            with patch.dict(sys.modules, {'ultralytics': package}), patch.object(graph, 'SOURCE_HASHES', {'head.py': digest}):
                self.assertEqual(graph._source_identity()['sha256'], {'head.py': digest})
                source.write_bytes(b'changed')
                with self.assertRaisesRegex(graph._Unsupported, 'source_hash_mismatch'):
                    graph._source_identity()


if __name__ == '__main__':
    unittest.main()
