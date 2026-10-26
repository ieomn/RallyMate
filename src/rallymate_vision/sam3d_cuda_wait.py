"""Opt-in blocking CUDA host waits before this worker creates its primary context.

The default performs no CUDA driver calls. The opt-in policy is restricted to
the isolated Windows SAM worker and its verified sole CUDA device. It changes
only scheduling bits, never resets, retains or releases a CUDA context.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
from threading import Lock


WAIT_MODE_ENV = 'RALLYMATE_SAM3D_CUDA_WAIT_MODE'
CU_CTX_SCHED_MASK = 0x7
CU_CTX_SCHED_BLOCKING_SYNC = 0x4
_POLICY_LOCK = Lock()


class Sam3DCudaWaitError(RuntimeError):
    """CUDA scheduling could not be applied safely before context activation."""


class _CudaDriver:
    def __init__(self):
        # LOAD_LIBRARY_SEARCH_SYSTEM32 avoids loading a workspace DLL of the
        # same name. Driver entry points use WINAPI on Windows.
        self.dll = ctypes.WinDLL('nvcuda.dll', winmode=0x00000800)
        signatures = {
            'cuInit': [ctypes.c_uint],
            'cuDeviceGetCount': [ctypes.POINTER(ctypes.c_int)],
            'cuDeviceGet': [ctypes.POINTER(ctypes.c_int), ctypes.c_int],
            'cuDevicePrimaryCtxGetState': [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_int)],
            'cuDriverGetVersion': [ctypes.POINTER(ctypes.c_int)],
        }
        self.set_flags_entry = ('cuDevicePrimaryCtxSetFlags_v2' if hasattr(self.dll, 'cuDevicePrimaryCtxSetFlags_v2')
                                else 'cuDevicePrimaryCtxSetFlags')
        signatures[self.set_flags_entry] = [ctypes.c_int, ctypes.c_uint]
        for name, args in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, ctypes.c_int
        self._check(self.dll.cuInit(0), 'cuInit')

    @staticmethod
    def _check(result, operation):
        if result != 0:
            raise Sam3DCudaWaitError(f'{operation} failed with CUDA driver result {result}')

    def count(self):
        value = ctypes.c_int()
        self._check(self.dll.cuDeviceGetCount(ctypes.byref(value)), 'cuDeviceGetCount')
        return value.value

    def device(self, ordinal):
        value = ctypes.c_int()
        self._check(self.dll.cuDeviceGet(ctypes.byref(value), ordinal), 'cuDeviceGet')
        return value.value

    def state(self, device):
        flags, active = ctypes.c_uint(), ctypes.c_int()
        self._check(self.dll.cuDevicePrimaryCtxGetState(device, ctypes.byref(flags), ctypes.byref(active)),
                    'cuDevicePrimaryCtxGetState')
        return flags.value, bool(active.value)

    def set_flags(self, device, flags):
        self._check(getattr(self.dll, self.set_flags_entry)(device, flags), self.set_flags_entry)

    def version(self):
        value = ctypes.c_int()
        self._check(self.dll.cuDriverGetVersion(ctypes.byref(value)), 'cuDriverGetVersion')
        return value.value


def _load_driver():
    try:
        return _CudaDriver()
    except Sam3DCudaWaitError:
        raise
    except (OSError, AttributeError) as error:
        raise Sam3DCudaWaitError('NVIDIA CUDA driver scheduling API is unavailable') from error


def configure_sam_cuda_wait(device, *, env=None):
    """Return a policy receipt or None; call before torch.cuda.set_device.

    ``runtime_default`` is an exact no-op, including no DLL loading. The opt-in
    ``blocking_sync`` policy fails closed for an active primary context, an
    ambiguous device mapping, unsupported host or a failed flags readback.
    """
    env = os.environ if env is None else env
    mode = env.get(WAIT_MODE_ENV, 'runtime_default')
    if mode == 'runtime_default':
        return None
    if mode != 'blocking_sync':
        raise Sam3DCudaWaitError(f'{WAIT_MODE_ENV} must be runtime_default or blocking_sync')
    if os.name != 'nt':
        raise Sam3DCudaWaitError('Blocking CUDA waits are currently validated only on Windows')
    if env.get('RALLYMATE_SAM3D_ISOLATED_WORKER') != '1':
        raise Sam3DCudaWaitError('Blocking CUDA waits require the isolated SAM worker')
    if str(device) not in {'0', 'cuda', 'cuda:0'} or env.get('CUDA_VISIBLE_DEVICES') not in (None, '0'):
        raise Sam3DCudaWaitError('Blocking CUDA waits require an unambiguous sole CUDA device 0')
    with _POLICY_LOCK:
        driver = _load_driver()
        if driver.count() != 1:
            raise Sam3DCudaWaitError('Blocking CUDA waits require exactly one visible CUDA device')
        native_device = driver.device(0)
        before, active = driver.state(native_device)
        if active:
            raise Sam3DCudaWaitError('SAM CUDA primary context is already active; refusing to modify or reset it')
        requested = (before & ~CU_CTX_SCHED_MASK) | CU_CTX_SCHED_BLOCKING_SYNC
        driver.set_flags(native_device, requested)
        after, active_after = driver.state(native_device)
        if after != requested or active_after:
            raise Sam3DCudaWaitError('CUDA wait flags readback differs or primary context became active')
        return {
            'mode': mode, 'scope': 'isolated_sam_primary_context_host_waits',
            'candidate_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'driver_version': driver.version(), 'driver_api': driver.set_flags_entry,
            'device_ordinal': 0, 'native_device': native_device,
            'flags_before': before, 'flags_requested': requested, 'flags_after': after,
            'scheduling_mask': CU_CTX_SCHED_MASK, 'scheduling_flags': after & CU_CTX_SCHED_MASK,
            'other_flags_preserved': before & ~CU_CTX_SCHED_MASK == after & ~CU_CTX_SCHED_MASK,
            'context_active_before': active, 'context_active_after_set': active_after,
            'context_reset_or_release': False, 'model_precision_or_graph_modified': False,
            'post_activation_verified': False,
        }


def verify_sam_cuda_wait(receipt):
    """Read back the same flags after Torch activates the context; never mutate."""
    if receipt is None:
        return None
    driver = _load_driver()
    flags, active = driver.state(receipt['native_device'])
    if not active or flags != receipt['flags_requested']:
        raise Sam3DCudaWaitError('CUDA wait policy was not retained after context activation')
    receipt.update(post_activation_verified=True, flags_after_activation=flags,
                   context_active_after_activation=active)
    return receipt
