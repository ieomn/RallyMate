"""Default-off, source-pinned FP32 YOLO26 partial CUDA graph.

One detector instance retains one rect shape. The original end-to-end topk
postprocess stays eager, including its CPU index. Public output tensors have
independent storage. Unsupported setup uses the original predictor; CUDA/OOM,
changed live model state, and unconfirmed cleanup are never swallowed.

A later video's resolution or a ball-refinement second shape disables this
candidate for the rest of the detector instance lifetime. There is no per-job
reset or multiple-shape cache; this does not qualify arbitrary users' videos.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import threading
import time

from rallymate_vision.owned_subprocess import ProcessTreeCleanupError

ENABLED_ENV = 'RALLYMATE_DETECT_FORWARD_GRAPH'
BUDGET_ENV = 'RALLYMATE_DETECT_GRAPH_MAX_MIB'
SCOPE = 'detector_instance_lifetime_cumulative; initial_predict_is_eager; one_static_shape'
SOURCE_HASHES = {
    'cfg/default.yaml': '3ef9fcf96280ba18b761a6165f7d77e14630b6759d5b32b28cd9d31025734dca',
    'data/augment.py': '5c2f4eb3582616858e7e8b09a4d7c2d5b544aa72dc8999f5a95b299136dd19c8',
    'engine/model.py': '45af5b44eb627e9f15e621509c8f4aad5213c28591deba8d279681af0bd83ae1',
    'engine/predictor.py': '06a34e5a548da22a2ac46f84fbca6c026792c38454db80b50cf8ea25a2e19635',
    'engine/results.py': '5211adc78d69247cc54c4ae6c29eae158768652567ab525b618e6df9c09079b5',
    'models/yolo/detect/predict.py': 'da7c65332c3fcd52aad5a7891514e06f81062f6bce16ae292d3f6d02b1c17c5f',
    'nn/autobackend.py': '39dcad3238d4cb98382f1fb2ac366b2d06cf03ae982d24a4f4c500205449c197',
    'nn/modules/block.py': '1e135bf35f1fc3520687b73942259108ccbfb6ed983bda0e74e7bc6a76db0b6a',
    'nn/modules/conv.py': 'c802f36ea1596d8910f2b651d57877b50a4960857b97cbb5a20d44a1dbbaa774',
    'nn/modules/head.py': 'eb2e24882dc993307adf48082db3fb67127b7d0abfe14a57e3874889a7db023f',
    'nn/modules/transformer.py': '8986f3f58a5e30a42545f544f1a35f5e5861b13ee5d4507d8b2afab1c41df782',
    'nn/tasks.py': 'f888a0af2d62f8e41a7936176656341e1522c5116302f145f97717e6a3c7f578',
    'utils/__init__.py': '4fb5313367ee85c1b638b4e839afae86808a82046403f4db8d951829f09bfeeb',
    'utils/nms.py': 'f17f8bfba204ff96e95be41747504d23949f32a68fd29fe4a17b151cdf08b212',
    'utils/ops.py': 'c5e2a6d91e7f79a6990aeb2d3b7f88db1acda3cb60efcea92fe041caf4991f90',
    'utils/tal.py': '37494f68725dc9a134dbb8be5db218c1b02bfd8cbf448d6bf50058c631ce4a81',
    'utils/torch_utils.py': 'cda1bf8718c24b4882e885114a16a1434b68e8ccea67d56b8155f7c81934aef8',
}


@dataclass(frozen=True)
class DetectorGraphOptions:
    enabled: bool = False
    max_retained_bytes: int = 256 * 1024**2

    @classmethod
    def from_environment(cls, environment=None):
        env = os.environ if environment is None else environment
        flag = env.get(ENABLED_ENV, '0')
        if not isinstance(flag, str) or flag not in {'0', '1'}:
            raise ValueError(f'{ENABLED_ENV} must be 0 or 1')
        raw = env.get(BUDGET_ENV, '256')
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or not 16 <= int(raw) <= 512:
            raise ValueError(f'{BUDGET_ENV} must be an integer in [16,512] MiB')
        return cls(flag == '1', int(raw) * 1024**2)


class _Unsupported(Exception):
    pass


class DetectorGraphFatalError(ProcessTreeCleanupError):
    """The persistent worker must exit before another job can use this model."""


def _identity(method):
    return (id(method.__self__), id(method.__func__)) if hasattr(method, '__func__') else (id(method),)


def _precision(torch):
    return (torch.backends.cudnn.benchmark, torch.backends.cudnn.deterministic,
            torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32,
            torch.get_float32_matmul_precision(), torch.are_deterministic_algorithms_enabled(),
            torch.is_deterministic_algorithms_warn_only_enabled())


def _global_hooks(torch):
    module = getattr(getattr(getattr(torch, 'nn', None), 'modules', None), 'module', None)
    result = []
    for name in ('_global_forward_pre_hooks', '_global_forward_hooks',
                 '_global_forward_hooks_always_called', '_global_forward_hooks_with_kwargs'):
        hooks = getattr(module, name, None)
        if not isinstance(hooks, dict):
            raise _Unsupported('global_forward_hook_registry_unavailable:' + name)
        result.append((name, id(hooks), tuple((key, id(value)) for key, value in hooks.items())))
    return tuple(result)


def _version(value):
    try:
        return value._version
    except RuntimeError as error:
        # Anchors produced in inference_mode have no version counter. Their
        # object/storage identity stays pinned and no eager shape update is
        # allowed until this graph has been synchronized and released.
        if 'Inference tensors do not track version counter' not in str(error):
            raise
        return None


def _tensor_state(value):
    return (id(value), value.data_ptr(), _version(value), tuple(value.shape), value.dtype, value.device,
            tuple(value.stride()), value.layout, value.storage_offset())


def _state(model):
    tensors = tuple((name, _tensor_state(value)) for name, value in
                    (*model.named_parameters(), *model.named_buffers()))
    branches = []
    for name, child in model.named_modules():
        values = []
        for key, value in vars(child).items():
            if key.startswith('_'):
                continue
            if value is None or type(value) in (bool, int, float, str):
                values.append((key, type(value), value))
            elif type(value) in (tuple, list) and all(type(x) in (bool, int, float, str) for x in value):
                values.append((key, type(value), tuple(value)))
        hooks = tuple((kind, tuple((key, id(value)) for key, value in getattr(child, kind, {}).items()))
                      for kind in ('_forward_hooks', '_forward_pre_hooks'))
        branches.append((name, id(child), _identity(child.forward), id(type(child).forward),
                         tuple(sorted(values)), hooks))
    head = model.model[-1]
    methods = tuple(_identity(getattr(owner, name)) for owner, name in (
        (model, 'predict'), (model, '_predict_once'), (head, 'postprocess'),
        (head, 'get_topk_index'), (head, 'forward_head'), (head, '_inference'),
        (head, '_get_decode_boxes'), (head, 'decode_bboxes')))
    settings = (head.dynamic, head.export, head.end2end, head.max_det, head.agnostic_nms,
                head.reg_max, head.nc, tuple(head.shape) if head.shape is not None else None)
    return tensors, tuple(branches), methods, settings, _tensor_state(head.anchors), _tensor_state(head.strides)


def _clone_tree(value, torch, memo=None):
    memo = {} if memo is None else memo
    if isinstance(value, torch.Tensor):
        if id(value) not in memo:
            memo[id(value)] = value.clone()
        return memo[id(value)]
    if type(value) is tuple:
        return tuple(_clone_tree(item, torch, memo) for item in value)
    if type(value) is list:
        return [_clone_tree(item, torch, memo) for item in value]
    if type(value) is dict:
        return {key: _clone_tree(item, torch, memo) for key, item in value.items()}
    if value is None or type(value) in (bool, int, float, str):
        return value
    raise TypeError('Detector graph returned an unsupported output type')


def _source_identity():
    import ultralytics
    if ultralytics.__version__ != '8.4.21':
        raise _Unsupported('unsupported_ultralytics_version')
    base = Path(ultralytics.__file__).resolve().parent
    actual = {}
    for name, expected in SOURCE_HASHES.items():
        try:
            actual[name] = hashlib.sha256((base / name).read_bytes()).hexdigest()
        except OSError as error:
            raise _Unsupported('source_unreadable:' + name) from error
        if actual[name] != expected:
            raise _Unsupported('source_hash_mismatch:' + name)
    return {'ultralytics_version': ultralytics.__version__, 'sha256': actual,
            'check_scope': 'at_install_and_capture; live methods/state checked on every replay'}


def _require_original_method(owner, name, expected):
    actual = getattr(owner, name)
    if getattr(actual, '__self__', None) is not owner or getattr(actual, '__func__', None) is not expected:
        raise _Unsupported('runtime_method_already_replaced:' + name)


def _require_official_forward(owner, fused_types):
    allowed = [type(owner).forward]
    if type(owner) in fused_types:
        allowed.append(type(owner).forward_fuse)
    actual = owner.forward
    if getattr(actual, '__self__', None) is not owner or getattr(actual, '__func__', None) not in allowed:
        raise _Unsupported('runtime_submodule_forward_already_replaced')
    return actual.__func__


def _verify_official_methods(model, torch):
    import ultralytics
    from ultralytics.nn.tasks import BaseModel
    from ultralytics.nn.modules.head import Detect
    from ultralytics.nn.modules.conv import Conv, Conv2, DWConv, ConvTranspose, RepConv
    from ultralytics.nn.modules.block import RepVGGDW
    head = model.model[-1]
    functions = []
    for owner, cls, names in ((model, BaseModel, ('forward', 'predict', '_predict_once')),
                             (head, Detect, ('forward', 'forward_head', '_inference', '_get_decode_boxes',
                                             'decode_bboxes', 'postprocess', 'get_topk_index'))):
        for name in names:
            expected = getattr(cls, name)
            _require_original_method(owner, name, expected)
            functions.append(expected)
    fused_types = (Conv, Conv2, DWConv, ConvTranspose, RepConv, RepVGGDW)
    for child in model.modules():
        if child is not model and not type(child).__module__.startswith(('ultralytics.nn.modules.', 'torch.nn.modules.')):
            raise _Unsupported('non_official_submodule_type')
        functions.append(_require_official_forward(child, fused_types))
    ultra_root = Path(ultralytics.__file__).resolve().parent
    torch_root = Path(torch.__file__).resolve().parent / 'nn'
    for function in functions:
        code = getattr(function, '__code__', None)
        if code is None:
            raise _Unsupported('runtime_method_source_unavailable')
        filename = Path(code.co_filename).resolve()
        if filename.is_relative_to(ultra_root):
            if filename.relative_to(ultra_root).as_posix() not in SOURCE_HASHES:
                raise _Unsupported('runtime_method_outside_pinned_sources')
        elif not filename.is_relative_to(torch_root):
            raise _Unsupported('runtime_method_outside_official_packages')


def _qualify(predictor):
    import torch
    from ultralytics.nn.autobackend import AutoBackend
    from ultralytics.nn.tasks import BaseModel, DetectionModel
    from ultralytics.nn.modules.head import Detect
    if torch.__version__ != '2.11.0+cu128':
        raise _Unsupported('unsupported_torch_version')
    if any(row[2] for row in _global_hooks(torch)):
        raise _Unsupported('global_forward_hooks_not_supported')
    backend = getattr(predictor, 'model', None)
    if type(backend) is not AutoBackend or not backend.pt or backend.fp16 or backend.device.type != 'cuda':
        raise _Unsupported('requires_pytorch_fp32_cuda_backend')
    model = backend.model
    if type(model) is not DetectionModel or type(model.model[-1]) is not Detect:
        raise _Unsupported('requires_original_detection_model_and_detect_head')
    head = model.model[-1]
    if not head.end2end or head.reg_max != 1 or head.dynamic or head.export or model.training:
        raise _Unsupported('requires_yolo26_static_end2end_eval_head')
    if getattr(model.forward, '__func__', None) is not BaseModel.forward:
        raise _Unsupported('model_forward_already_replaced')
    if any(module.training or module._forward_hooks or module._forward_pre_hooks for module in model.modules()):
        raise _Unsupported('training_or_forward_hooks_not_supported')
    if any(value.dtype != torch.float32 or value.device != backend.device or value.layout != torch.strided
           for value in model.parameters()):
        raise _Unsupported('requires_fp32_weights_on_one_device')
    args = predictor.args
    if (args.batch != 1 or not args.rect or args.augment or args.compile or args.half
            or getattr(args, 'embed', None) is not None):
        raise _Unsupported('predictor_options_not_qualified')
    source = _source_identity()
    _verify_official_methods(model, torch)
    return model, torch, source


class DetectorForwardGraph:
    """Own one instance wrapper, graph pool, static tensors, and original method."""
    def __init__(self, model, torch, options, source):
        self.model, self.torch, self.options = model, torch, options
        self.original = model.forward
        self.had_own_forward = 'forward' in model.__dict__
        self.original_own_forward = model.__dict__.get('forward')
        self.head = model.model[-1]
        self.postprocess = self.head.postprocess
        self.postprocess_identity = _identity(self.postprocess)
        self.graph = self.inputs = self.outputs = None
        self.external_tensors = []
        self.input_signature = None
        self.lock = threading.Lock()
        self.disabled = self.closed = self.failed = self.cleanup_failed = False
        self.precision = _precision(torch)
        self.global_hooks = _global_hooks(torch)
        if any(row[2] for row in self.global_hooks):
            raise _Unsupported('global_forward_hooks_not_supported')
        self.kwargs = {'augment': False, 'visualize': False, 'embed': None}
        self.receipt = {'status': 'installed_pending_actual_input', 'capture_calls': 0, 'replay_calls': 0,
                        'eager_fallback_calls': 0, 'last_fallback_reason': None, 'source': source,
                        'max_graphs': 1, 'max_retained_delta_bytes': options.max_retained_bytes,
                        'memory_budget_scope': 'allocator_retained_delta_after_capture; not transient/physical_VRAM_hard_cap',
                        'boundary': 'original_forward_before_original_eager_postprocess',
                        'full_output_tree_cloned': True, 'scope': SCOPE,
                        'tensor_state_guard': 'object/storage/shape/dtype/device and _version when available; inference tensors pinned by exclusive ownership',
                        'precision_policy': list(self.precision), 'closed': False}
        model.forward = self
        try:
            self.state = _state(model)
        except BaseException:
            if self.had_own_forward:
                model.forward = self.original_own_forward
            else:
                del model.forward
            raise

    @contextmanager
    def _without_postprocess(self):
        if _identity(self.head.postprocess) != self.postprocess_identity:
            raise RuntimeError('Detector postprocess identity changed')
        had_own, previous = 'postprocess' in self.head.__dict__, self.head.__dict__.get('postprocess')
        self.head.postprocess = lambda prediction: prediction
        try:
            yield
        finally:
            if had_own:
                self.head.postprocess = previous
            else:
                del self.head.postprocess
            if _identity(self.head.postprocess) != self.postprocess_identity:
                raise RuntimeError('Detector original postprocess restoration failed')

    def _release(self):
        # Keep all references pinned if synchronization fails; the owner must
        # then terminate its process tree instead of admitting new GPU work.
        if self.graph is not None or self.inputs is not None:
            try:
                self.torch.cuda.synchronize(self.device)
            except BaseException as error:
                self.cleanup_failed = True
                raise ProcessTreeCleanupError('Detector CUDA graph release was not confirmed') from error
        self.graph = self.inputs = self.outputs = None
        self.external_tensors = []
        self.receipt['retained_delta_bytes'] = 0

    def _disable(self, reason):
        self._release()
        self.disabled = True
        self.receipt.update(status='disabled_eager', last_fallback_reason=reason)

    def _eager(self, args, kwargs, reason):
        self.receipt['eager_fallback_calls'] += 1
        self.receipt['last_fallback_reason'] = reason
        return self.original(*args, **kwargs)

    def _capture(self, x):
        torch = self.torch
        source = _source_identity()
        if source != self.receipt['source']:
            raise _Unsupported('source_changed_before_capture')
        self.device = x.device
        free_bytes, _ = torch.cuda.mem_get_info(self.device)
        if free_bytes < self.options.max_retained_bytes + 64 * 1024**2:
            self._disable('insufficient_free_memory_before_capture')
            return
        before_a, before_r = torch.cuda.memory_allocated(self.device), torch.cuda.memory_reserved(self.device)
        started = time.perf_counter()
        self.inputs = x.clone()
        stream = torch.cuda.Stream(device=self.device)
        stream.wait_stream(torch.cuda.current_stream(self.device))
        with torch.cuda.stream(stream), torch.inference_mode():
            with self._without_postprocess():
                for _ in range(3):
                    warm = self.original(self.inputs, **self.kwargs)
            if type(warm) is not tuple or len(warm) != 2 or type(warm[1]) is not dict:
                raise RuntimeError('Detector pre-postprocess output contract changed')
            for _ in range(3):
                post_warm = self.postprocess(warm[0])
            del post_warm, warm
        torch.cuda.current_stream(self.device).wait_stream(stream)
        torch.cuda.synchronize(self.device)
        if _global_hooks(torch) != self.global_hooks or _precision(torch) != self.precision:
            raise RuntimeError('Global hook/precision state changed during detector capture')
        self.receipt['warmup_seconds'] = time.perf_counter() - started
        self.external_tensors = list(self.model.parameters()) + list(self.model.buffers())
        self.external_tensors += [self.head.anchors, self.head.strides]
        self.graph = torch.cuda.CUDAGraph()
        started = time.perf_counter()
        with self._without_postprocess():
            with torch.cuda.graph(self.graph, stream=stream), torch.inference_mode():
                self.outputs = self.original(self.inputs, **self.kwargs)
        torch.cuda.synchronize(self.device)
        if _global_hooks(torch) != self.global_hooks or _precision(torch) != self.precision:
            raise RuntimeError('Global hook/precision state changed during detector capture')
        retained = max(0, torch.cuda.memory_allocated(self.device) - before_a,
                       torch.cuda.memory_reserved(self.device) - before_r)
        self.receipt.update(capture_calls=1, capture_seconds=time.perf_counter() - started,
                            measured_capture_delta_bytes=retained)
        if retained > self.options.max_retained_bytes:
            self._disable('retained_memory_budget_exceeded')
            return
        self.input_signature = (tuple(x.shape), tuple(x.stride()), x.dtype, x.device)
        self.state = _state(self.model)
        self.receipt.update(status='captured', input_shape=list(x.shape), input_dtype=str(x.dtype),
                            retained_delta_bytes=retained)

    def __call__(self, x, *args, **kwargs):
        if not self.lock.acquire(blocking=False):
            error = RuntimeError('Concurrent detector graph use is forbidden')
            self.failed = True
            self.receipt.update(status='failed', error_type=type(error).__name__, error=str(error))
            raise DetectorGraphFatalError(str(error)) from error
        try:
            if self.closed or self.failed or self.cleanup_failed:
                raise RuntimeError('Detector graph is closed or failed; process cleanup is required')
            if self.disabled:
                return self._eager((x, *args), kwargs, self.receipt['last_fallback_reason'])
            if (_state(self.model) != self.state or _precision(self.torch) != self.precision
                    or _global_hooks(self.torch) != self.global_hooks):
                raise RuntimeError('Detector live model/method/precision state changed')
            torch = self.torch
            if torch.is_grad_enabled() or torch.is_autocast_enabled() or self.model.training:
                raise RuntimeError('Detector graph requires unchanged eval inference without autocast')
            key = (tuple(x.shape), tuple(x.stride()), x.dtype, x.device) if isinstance(x, torch.Tensor) else None
            unsupported = (key is None or len(x.shape) != 4 or x.shape[0] != 1
                           or x.dtype != torch.float32 or not x.is_cuda or args or kwargs != self.kwargs)
            if unsupported or self.input_signature is not None and key != self.input_signature:
                reason = 'unsupported_input_or_options' if unsupported else 'input_shape_stride_or_device_changed'
                self._disable(reason)
                return self._eager((x, *args), kwargs, reason)
            if self.graph is None:
                try:
                    self._capture(x)
                except _Unsupported as error:
                    self._disable(str(error))
                if self.disabled:
                    return self._eager((x, *args), kwargs, self.receipt['last_fallback_reason'])
            self.inputs.copy_(x)
            self.graph.replay()
            prepost_y, complete_preds = self.outputs
            output = _clone_tree((self.postprocess(prepost_y), complete_preds), torch)
            self.receipt['replay_calls'] += 1
            return output
        except BaseException as error:
            self.failed = True
            self.receipt.update(status='failed', error_type=type(error).__name__, error=str(error)[:512])
            if isinstance(error, Exception) and not isinstance(error, ProcessTreeCleanupError):
                raise DetectorGraphFatalError(f'Detector graph cannot continue: {type(error).__name__}: {error}') from error
            raise
        finally:
            self.lock.release()

    def close(self):
        if self.closed:
            return
        if self.cleanup_failed:
            raise ProcessTreeCleanupError('Detector graph cleanup previously failed')
        if not self.lock.acquire(timeout=30):
            self.cleanup_failed = True
            raise ProcessTreeCleanupError('Detector graph is still in use during cleanup')
        try:
            self._release()
            if self.model.forward is not self:
                self.cleanup_failed = True
                raise ProcessTreeCleanupError('Detector forward changed before graph restoration')
            if self.had_own_forward:
                self.model.forward = self.original_own_forward
            else:
                del self.model.forward
            self.closed = True
            self.receipt['closed'] = True
        finally:
            self.lock.release()

    def metadata(self):
        with self.lock:
            return deepcopy(self.receipt)


class DetectorGraphController:
    """Attach only after the first untouched prediction has built AutoBackend."""
    def __init__(self, pose_backend, options):
        self.options = options
        self.handle = self.backend = None
        self.retired_metadata = None
        self.disabled = self.closed = False
        self.receipt = {'requested': options.enabled, 'status': 'awaiting_first_eager_predict',
                        'initial_eager_predict_calls': 0, 'scope': SCOPE}
        from rallymate_vision.sam3d_runner import Sam3DProcess
        if not isinstance(getattr(pose_backend, 'engine', None), Sam3DProcess):
            self.disabled = True
            self.receipt.update(status='unsupported_eager', reason='requires_actual_isolated_sam3d_process')

    def before_predict(self, predictor):
        if self.closed:
            raise DetectorGraphFatalError('Detector graph controller was closed')
        backend_replaced = getattr(predictor, 'model', None) is not self.backend
        if self.handle is not None and (backend_replaced or getattr(self.backend, 'model', None) is not self.handle.model):
            self.handle.close()
            self.retired_metadata = self.handle.metadata()
            self.handle = self.backend = None
            self.disabled = True
            self.receipt.update(status='unsupported_eager',
                                reason='predictor_backend_replaced' if backend_replaced else 'predictor_inner_model_replaced')

    def after_predict(self, predictor):
        if self.handle is not None or self.disabled:
            return
        self.receipt['initial_eager_predict_calls'] += 1
        try:
            model, torch, source = _qualify(predictor)
        except _Unsupported as error:
            self.disabled = True
            self.receipt.update(status='unsupported_eager', reason=str(error))
            return
        self.backend = predictor.model
        self.handle = DetectorForwardGraph(model, torch, self.options, source)
        self.receipt['status'] = 'installed'

    def metadata(self):
        value = deepcopy(self.receipt)
        if self.handle is not None:
            value['runtime'] = self.handle.metadata()
        elif self.retired_metadata is not None:
            value['runtime'] = deepcopy(self.retired_metadata)
        return value

    def close(self):
        if self.handle is not None:
            self.handle.close()
        self.closed = True
        self.receipt['closed'] = True
