"""Opt-in single-node GPU worker routing; one process tree per physical UUID.

The queue is shared locally; each free worker claims the oldest eligible job.
No network-filesystem SQLite, cross-host leases, or multi-GPU speedup is implied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from threading import Lock

from rallymate_service.mesh_render_worker import RenderLock
from rallymate_vision.owned_subprocess import ProcessTreeCleanupError

UUID_PATTERN = re.compile(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
_LOCAL_SLOTS, _LOCAL_SLOT_LOCK = set(), Lock()
MANAGED_INFERENCE_PROFILE = {
    'RALLYMATE_SAM3D_BATCH_FRAMES': '1',
    'RALLYMATE_SAM3D_BATCH_GRAPH_SIZES': '',
    'RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES': '',
    'RALLYMATE_SAM3D_BATCH_PREPROCESS': '0',
    'RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB': '512',
    'RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB': '512',
    'RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES': '2',
    'RALLYMATE_DETECT_BATCH_SIZE': '1',
    'RALLYMATE_SAM3D_CPU_THREADS': '1',
    'RALLYMATE_SAM3D_INTEROP_THREADS': '1',
    'RALLYMATE_SAM3D_PROFILE_STAGES': '0',
    'RALLYMATE_SAM3D_REUSE_CUDA_CACHE': '1',
}
WORKER_ID_PATTERN = re.compile(r'fleet-[0-9a-f]{32}')


class GPUQuarantinedError(RuntimeError):
    """Physical GPU ownership or prior process-tree cleanup is unresolved."""


def node_gpu_state_root() -> Path:
    """One node-wide state root, independent of job/data directories.

    Tests inject state_directory explicitly or patch this helper; production
    launchers do not offer a per-worker environment override that could bypass
    another worker's quarantine.
    """
    if os.name == 'nt':
        return Path(os.environ.get('ProgramData', r'C:\ProgramData')) / 'RallyMate' / 'gpu-state'
    return Path('/var/lib/rallymate/gpu-state')


def _gpu_state_path(identifier: str, state_directory: Path | None = None) -> Path:
    if not isinstance(identifier, str) or not UUID_PATTERN.fullmatch(identifier):
        raise ValueError('GPU state requires a complete physical GPU UUID')
    directory = Path(state_directory) if state_directory is not None else node_gpu_state_root()
    if not directory.is_absolute():
        raise ValueError('GPU ownership state must use an absolute node-level directory')
    return directory.resolve() / (identifier.lower() + '.json')


def _read_gpu_state(path: Path, identifier: str) -> dict | None:
    try:
        state_stat = path.lstat()
        if path.is_symlink() or state_stat.st_size > 4096:
            raise ValueError('invalid state file')
        value = json.loads(path.read_text(encoding='utf8'))
        if (not isinstance(value, dict) or value.get('version') != 1
                or value.get('gpu_uuid') != identifier.lower()
                or not isinstance(value.get('worker_id'), str) or not value['worker_id']
                or not isinstance(value.get('owner_token'), str) or len(value['owner_token']) != 32
                or value.get('status') not in ('owned', 'quarantined')):
            raise ValueError('invalid ownership state')
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        raise GPUQuarantinedError('GPU ownership state is unreadable; explicit repair is required') from exc


def _write_gpu_state(path: Path, value: dict, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def clear_gpu_quarantine(identifier: str, worker_id: str, *, confirmed_tree_exit=False,
                         state_directory: Path | None = None) -> bool:
    """Remove only this terminated owner's state under the physical GPU mutex."""
    if confirmed_tree_exit is not True:
        raise ValueError('Clearing GPU ownership requires confirmed process-tree exit')
    path = _gpu_state_path(identifier, state_directory)
    if not path.exists() and not path.is_symlink():
        return False
    slot = GPUWorkerSlot(path.parent, identifier, worker_id=worker_id, state_directory=path.parent)
    with _LOCAL_SLOT_LOCK:
        if slot.identifier in _LOCAL_SLOTS or not slot._acquire_os():
            return False  # A current owner must never be cleared by an old supervisor.
        try:
            current = _read_gpu_state(path, slot.identifier)
            if current is None or current['worker_id'] != worker_id:
                return False
            path.unlink()
            return True
        finally:
            slot._close_os()


def new_managed_worker_id() -> str:
    return 'fleet-' + uuid.uuid4().hex


def require_managed_worker_id(value: str) -> str:
    if not isinstance(value, str) or not WORKER_ID_PATTERN.fullmatch(value):
        raise ValueError('Managed workers require a fresh launcher-generated owner ID')
    return value


@dataclass
class ManagedWorkerProcess:
    worker_id: str
    gpu_uuid: str
    owner: object
    state_directory: Path | None = None
    tree_exit_confirmed: bool = field(default=False, init=False)
    cleanup_failed: bool = field(default=False, init=False)

    def __post_init__(self):
        require_managed_worker_id(self.worker_id)
        if not UUID_PATTERN.fullmatch(self.gpu_uuid):
            raise ValueError('Managed process requires a complete physical GPU UUID')


def stop_managed_workers(workers: list[ManagedWorkerProcess], database, *, max_attempts: int) -> list[dict]:
    """Stop every owned tree before making its unfinished attempts claimable.

    A PID exit is insufficient, especially with Windows venv launchers. If any
    tree cannot be confirmed stopped, recover none automatically. A parent
    crash or failed cleanup still requires explicit operator repair.
    """
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError('Managed recovery requires a positive retry budget')
    errors = []
    if len({worker.worker_id for worker in workers}) != len(workers):
        errors.append(ValueError('Managed process owner IDs must never be reused'))
    for worker in workers:
        if worker.cleanup_failed:
            errors.append(ProcessTreeCleanupError('A previous worker cleanup was not confirmed'))
            continue
        if worker.tree_exit_confirmed:
            continue
        try:
            if getattr(worker.owner, 'closed', False):
                raise ProcessTreeCleanupError('Worker tree was closed outside managed recovery; confirmation is unavailable')
            worker.owner.close()
            if getattr(worker.owner, 'closed', False) is not True or worker.owner.poll() is None:
                raise ProcessTreeCleanupError('Worker owner did not confirm complete process-tree exit')
            worker.tree_exit_confirmed = True
        except Exception as exc:
            worker.cleanup_failed = True
            errors.append(exc)
    if errors:
        raise ProcessTreeCleanupError('Fleet shutdown could not confirm every owned tree; no attempts were recovered') from errors[0]
    receipts = []
    for worker in workers:
        # Read after termination: a final claim just before the stop must not
        # escape recovery. The following transaction fences this exact attempt.
        cleared = clear_gpu_quarantine(worker.gpu_uuid, worker.worker_id,
            confirmed_tree_exit=True, state_directory=worker.state_directory)
        attempts = database.running_attempts_for_worker(worker.worker_id)
        recovered = database.recover_confirmed_worker_attempts(worker.worker_id, attempts,
            max_attempts=max_attempts, confirmed_tree_exit=True)
        receipts.append({'worker_id': worker.worker_id, 'gpu_uuid': worker.gpu_uuid,
                         'tree_exit_confirmed': True, 'gpu_quarantine_cleared': cleared,
                         'recovered_attempts': recovered})
    return receipts


@dataclass(frozen=True)
class GPU:
    uuid: str
    name: str
    total_mib: int
    free_mib: int


def cuda_property_uuid(value) -> str:
    """Torch releases expose either a bare UUID or NVIDIA's GPU- prefixed form."""
    identifier = str(value)
    if not identifier.startswith('GPU-'):
        identifier = 'GPU-' + identifier
    if not UUID_PATTERN.fullmatch(identifier):
        raise ValueError('CUDA device properties did not provide a complete physical UUID')
    return 'GPU-' + identifier[4:].lower()


def discover_gpus() -> list[GPU]:
    result = subprocess.run(['nvidia-smi', '--query-gpu=uuid,name,memory.total,memory.free',
                             '--format=csv,noheader,nounits'], capture_output=True, text=True,
                            timeout=10, check=True,
                            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    return parse_inventory(result.stdout)


def parse_inventory(text: str) -> list[GPU]:
    result, identifiers = [], set()
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        if len(row) != 4:
            raise ValueError('Unrecognized NVIDIA inventory format')
        identifier, name, total, free = (item.strip() for item in row)
        if not UUID_PATTERN.fullmatch(identifier) or identifier in identifiers:
            raise ValueError('GPU inventory has a missing or duplicate physical UUID')
        gpu = GPU(identifier, name, int(total), int(free))
        if not 0 <= gpu.free_mib <= gpu.total_mib or gpu.total_mib <= 0:
            raise ValueError('GPU memory counters are unavailable or inconsistent')
        identifiers.add(identifier)
        result.append(gpu)
    if not result:
        raise ValueError('No physical NVIDIA GPUs are visible')
    return result


def fleet_plan(identifiers: list[str], inventory: list[GPU], *, detector_mib=1024,
               sam_mib=11264, reserve_mib=2048, cpu_threads=2) -> list[dict]:
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError('Choose distinct physical GPU UUIDs; concurrency is one worker per card')
    if any(type(value) is not int or value < 256 for value in (detector_mib, sam_mib, reserve_mib)):
        raise ValueError('Each allocator budget and physical headroom must be >=256 MiB')
    if type(cpu_threads) is not int or not 1 <= cpu_threads <= 64:
        raise ValueError('CPU threads must be between 1 and 64 per worker')
    devices = {gpu.uuid: gpu for gpu in inventory}
    plans = []
    for identifier in identifiers:
        if identifier not in devices:
            raise ValueError(f'Requested physical GPU is absent: {identifier}')
        gpu = devices[identifier]
        if detector_mib + sam_mib + reserve_mib > gpu.total_mib:
            raise ValueError(f'Allocator caps plus physical headroom exceed {identifier} capacity')
        plans.append({'gpu_uuid': identifier, 'gpu_name': gpu.name, 'total_mib': gpu.total_mib,
                      'free_mib_at_plan': gpu.free_mib, 'concurrency': 1,
                      'detector_allocator_mib': detector_mib, 'sam_allocator_mib': sam_mib,
                      'physical_headroom_mib': reserve_mib, 'cpu_threads': cpu_threads,
                      'device': '0', 'queue_policy': 'shared_local_fifo'})
    return plans


def worker_environment(plan: dict, base: dict[str, str]) -> dict[str, str]:
    result = dict(base)
    # Qualification is still pending. Shell variables from an isolated batch
    # experiment must not silently become a managed production configuration.
    result.update(MANAGED_INFERENCE_PROFILE)
    result.update(CUDA_VISIBLE_DEVICES=plan['gpu_uuid'], RALLYMATE_DEVICE='0',
                  RALLYMATE_MANAGED_GPU_UUID=plan['gpu_uuid'],
                  RALLYMATE_DETECTOR_CUDA_MEMORY_MIB=str(plan['detector_allocator_mib']),
                  RALLYMATE_SAM3D_CUDA_MEMORY_MIB=str(plan['sam_allocator_mib']),
                  RALLYMATE_GPU_PHYSICAL_HEADROOM_MIB=str(plan['physical_headroom_mib']),
                  RALLYMATE_CPU_THREADS=str(plan['cpu_threads']))
    # UUID visibility remaps the selected physical card to logical CUDA 0.
    # The legacy Windows blocking-sync policy deliberately rejects that mapping.
    result['RALLYMATE_SAM3D_CUDA_WAIT_MODE'] = 'runtime_default'
    for name in ('OMP_NUM_THREADS', 'OMP_THREAD_LIMIT', 'MKL_NUM_THREADS',
                 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'OPENCV_FOR_THREADS_NUM'):
        result[name] = str(plan['cpu_threads'])
    return result


class GPUWorkerSlot:
    """Node-wide GPU mutex plus persistent unresolved-cleanup quarantine.

    Windows mutexes are thread-owned. Acquire and release on the same thread,
    and retain this slot until the entire owned inference process tree exits.
    Managed owners also journal active ownership before loading any model; a
    process crash releases the mutex but not that durable ownership record.
    """
    def __init__(self, root: Path, identifier: str, *, worker_id: str | None = None,
                 state_directory: Path | None = None):
        if not UUID_PATTERN.fullmatch(identifier):
            raise ValueError('Worker slots require a complete physical GPU UUID')
        self.identifier, self.handle, self.kernel, self.lock = identifier.lower(), None, None, None
        self.worker_id = worker_id or 'slot-' + uuid.uuid4().hex
        if not isinstance(self.worker_id, str) or not self.worker_id or len(self.worker_id) > 256:
            raise ValueError('GPU slot requires a bounded owner identity')
        self.owner_token = uuid.uuid4().hex
        self.state_path = _gpu_state_path(identifier, state_directory)
        self.persistent_owner = bool(WORKER_ID_PATTERN.fullmatch(self.worker_id))
        self.quarantined = False
        self.state_owned = False
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self.kernel.CreateMutexW.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            self.kernel.ReleaseMutex.restype = wintypes.BOOL
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
        else:
            directory = Path(tempfile.gettempdir()) / 'rallymate-gpu-worker-locks'
            directory.mkdir(parents=True, exist_ok=True)
            self.lock = RenderLock(directory / (self.identifier + '.lock'))

    def acquire(self) -> bool:
        # Windows mutexes are reentrant on the owning thread; separate worker
        # instances in that thread must still be rejected as oversubscription.
        with _LOCAL_SLOT_LOCK:
            if self.identifier in _LOCAL_SLOTS:
                return False
            acquired = self._acquire_os()
            if acquired:
                try:
                    if _read_gpu_state(self.state_path, self.identifier) is not None:
                        raise GPUQuarantinedError('GPU has unresolved ownership or cleanup; explicit confirmed-tree repair is required')
                    if self.persistent_owner:
                        # Managed crash recovery requires the launcher to confirm
                        # complete tree exit before clearing this durable marker.
                        _write_gpu_state(self.state_path, self._state('owned'), exclusive=True)
                        self.state_owned = True
                    else:
                        # Legacy preview restarts intentionally force-stop their
                        # verified tree. Preserve that behavior: only explicit
                        # cleanup failure creates a persistent legacy quarantine.
                        self.state_path.parent.mkdir(parents=True, exist_ok=True)
                        probe = self.state_path.with_name(self.state_path.name + '.' + self.owner_token + '.probe')
                        try:
                            with probe.open('x', encoding='utf8') as stream:
                                stream.write('state-directory-write-check')
                                stream.flush()
                                os.fsync(stream.fileno())
                        finally:
                            probe.unlink(missing_ok=True)
                    _LOCAL_SLOTS.add(self.identifier)
                except BaseException:
                    self._close_os()
                    raise
            return acquired

    def _state(self, status: str, reason: str | None = None) -> dict:
        return {'version': 1, 'gpu_uuid': self.identifier, 'worker_id': self.worker_id,
                'owner_token': self.owner_token, 'pid': os.getpid(), 'status': status,
                'reason': reason, 'updated_at': datetime.now(timezone.utc).isoformat()}

    def quarantine(self, reason: str):
        """Persist unresolved cleanup before releasing any OS ownership handle."""
        with _LOCAL_SLOT_LOCK:
            if (self.identifier not in _LOCAL_SLOTS
                    or self.handle is None and (self.lock is None or self.lock.handle is None)):
                raise RuntimeError('Only the current physical GPU owner can quarantine it')
            self.quarantined = True
            current = _read_gpu_state(self.state_path, self.identifier)
            if current is not None and (current['worker_id'] != self.worker_id
                                        or current['owner_token'] != self.owner_token):
                raise GPUQuarantinedError('GPU ownership marker changed; refusing to overwrite another owner')
            _write_gpu_state(self.state_path, self._state('quarantined', str(reason)[:256]),
                             exclusive=current is None)
            self.state_owned = True

    def _acquire_os(self) -> bool:
        if self.handle is not None or self.lock is not None and self.lock.handle is not None:
            raise RuntimeError('GPU slot is already acquired by this owner')
        if self.kernel is None:
            return self.lock.acquire()
        import ctypes
        handle = self.kernel.CreateMutexW(None, False, 'Global\\RallyMateGPUWorker_' + self.identifier)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        outcome = self.kernel.WaitForSingleObject(handle, 0)
        if outcome in (0, 0x80):  # WAIT_OBJECT_0 / abandoned owner; mutex acquired.
            self.handle = handle
            return True
        error = ctypes.get_last_error()
        self.kernel.CloseHandle(handle)
        if outcome == 0x102:  # WAIT_TIMEOUT: a live worker owns the physical card.
            return False
        raise ctypes.WinError(error)

    def close(self):
        with _LOCAL_SLOT_LOCK:
            if self.handle is None and (self.lock is None or self.lock.handle is None):
                return
            try:
                if self.state_owned and not self.quarantined:
                    current = _read_gpu_state(self.state_path, self.identifier)
                    if current is not None:
                        if current['worker_id'] != self.worker_id or current['owner_token'] != self.owner_token:
                            raise GPUQuarantinedError('GPU ownership marker changed during cleanup')
                        self.state_path.unlink()
            finally:
                self._close_os()
                _LOCAL_SLOTS.discard(self.identifier)

    def _close_os(self):
        if self.kernel is None:
            self.lock.close()
        elif self.handle is not None:
            import ctypes
            handle, self.handle = self.handle, None
            try:
                if not self.kernel.ReleaseMutex(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self.kernel.CloseHandle(handle)

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError('A worker already owns this physical GPU')
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if isinstance(exc, ProcessTreeCleanupError):
                self.quarantine(f'{type(exc).__name__}: {exc}')
        finally:
            self.close()


def validate_worker_environment(env: dict[str, str], inventory: list[GPU]) -> dict:
    identifier = env.get('RALLYMATE_MANAGED_GPU_UUID', '')
    if env.get('CUDA_VISIBLE_DEVICES') != identifier or env.get('RALLYMATE_DEVICE') != '0':
        raise ValueError('Managed worker must expose exactly its physical UUID as logical CUDA 0')
    if any(env.get(key) != value for key, value in MANAGED_INFERENCE_PROFILE.items()):
        raise ValueError('Managed workers require the qualified default inference profile; experimental batching is not enabled')
    plan = fleet_plan([identifier], inventory,
        detector_mib=int(env['RALLYMATE_DETECTOR_CUDA_MEMORY_MIB']),
        sam_mib=int(env['RALLYMATE_SAM3D_CUDA_MEMORY_MIB']),
        reserve_mib=int(env['RALLYMATE_GPU_PHYSICAL_HEADROOM_MIB']),
        cpu_threads=int(env['RALLYMATE_CPU_THREADS']))[0]
    if plan['free_mib_at_plan'] < (plan['detector_allocator_mib'] + plan['sam_allocator_mib']
                                  + plan['physical_headroom_mib']):
        raise RuntimeError('GPU has insufficient free memory for worker budgets plus physical headroom')
    return plan
