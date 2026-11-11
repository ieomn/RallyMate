"""Bounded CPU-only scoring work with ordered results and owned child lifetimes."""
from __future__ import annotations

from concurrent.futures import CancelledError
from collections.abc import Callable, Iterable, Iterator
import multiprocessing as mp
from multiprocessing.connection import wait
import logging
import os
from pathlib import Path
import pickle
import tempfile
import time

MAX_TASK_BYTES = 64 * 1024 * 1024
MAX_RESULT_BYTES = 64 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class _TransferLimit(Exception):
    pass


def scoring_cpu_workers() -> int:
    """An explicit setting enables CPU workers; existing callers stay serial."""
    requested = int(os.environ.get("RALLYMATE_SCORING_CPU_WORKERS", "1"))
    return min(6, max(1, requested), max(1, (os.cpu_count() or 1) - 2))


def _worker(connection, stopped, max_result_bytes) -> None:
    # These tasks are JSON and small-array CPU calculations, never model calls.
    # Limit BLAS too when the optional controller is available in the runtime.
    controller = None
    try:
        from threadpoolctl import threadpool_limits
        controller = threadpool_limits(limits=1)
    except ImportError:
        pass

    def check() -> None:
        if stopped.is_set():
            raise CancelledError("CPU scoring was cancelled")

    try:
        while not stopped.is_set():
            if not connection.poll(0.2):
                continue
            task = connection.recv()
            if task is None:
                return
            result_path = Path(task).with_suffix(".result")
            try:
                check()
                with open(task, "rb") as handle:
                    function, args, kwargs = pickle.load(_CheckedFile(handle, check))
                result = function(*args, **kwargs, cancellation_callback=check)
                with result_path.open("wb") as handle:
                    pickle.dump((True, result), _CheckedFile(handle, check, max_result_bytes),
                                protocol=pickle.HIGHEST_PROTOCOL)
                connection.send_bytes(b"ready")
            except BaseException as error:
                try:
                    with result_path.open("wb") as handle:
                        pickle.dump((False, error), _CheckedFile(handle, lambda: None, 65536),
                                    protocol=pickle.HIGHEST_PROTOCOL)
                except BaseException:
                    with result_path.open("wb") as handle:
                        pickle.dump((False, RuntimeError(
                            f"CPU scoring child failed: {type(error).__name__}")), handle)
                connection.send_bytes(b"ready")
                # An oversized result is recomputed through the original serial
                # path. Other errors terminate this worker and propagate.
                if not isinstance(error, _TransferLimit):
                    return
    except (EOFError, BrokenPipeError):
        return
    finally:
        connection.close()
        if controller is not None:
            controller.restore_original_limits()


class _CheckedFile:
    """Keep cancellation responsive while streaming caller-owned task data."""

    def __init__(self, handle, callback, max_bytes=None):
        self.handle = handle
        self.callback = callback
        self.max_bytes = max_bytes
        self.written = 0

    def write(self, data):
        view = memoryview(data)
        for start in range(0, len(view), 1024 * 1024):
            self.callback()
            block = view[start:start + 1024 * 1024]
            if self.max_bytes is not None and self.written + len(block) > self.max_bytes:
                raise _TransferLimit("CPU scoring transfer exceeds its byte budget")
            self.handle.write(block)
            self.written += len(block)
        return len(view)

    def read(self, size=-1):
        self.callback()
        return self.handle.read(size)

    def readinto(self, target):
        self.callback()
        return self.handle.readinto(target)

    def readline(self):
        self.callback()
        return self.handle.readline()


class ScoringCpuPool:
    """Keep at most one in-flight result per worker and join every owned process.

    A deliberately small pipe window prevents an early slow task from retaining
    an entire video's later results. The parent performs the caller's resource,
    cancellation and lease checks while children are computing.
    """

    def __init__(self, workers: int = 1, *, cancellation_callback=None,
                 max_task_bytes=MAX_TASK_BYTES, max_result_bytes=MAX_RESULT_BYTES,
                 max_child_memory_bytes=None):
        self.workers = max(1, min(6, int(workers)))
        self.callback = cancellation_callback
        self.context = mp.get_context("spawn")
        self.stopped = self.context.Event()
        self.children = []
        self.closed = False
        self.worker_pids = []
        self.spool = None
        self.task_serial = 0
        self.peak_task_bytes = 0
        self.task_transfer_seconds = 0.0
        if not 0 < max_task_bytes <= MAX_TASK_BYTES or not 0 < max_result_bytes <= MAX_RESULT_BYTES:
            raise ValueError("CPU scoring transfer budgets must be positive and at most 64 MiB")
        self.max_task_bytes = max_task_bytes
        self.max_result_bytes = max_result_bytes
        self.max_child_memory_bytes = (int(os.environ.get("RALLYMATE_SCORING_CHILD_MEMORY_MB", "8192")) * 1024**2
                                       if max_child_memory_bytes is None else max_child_memory_bytes)
        if self.max_child_memory_bytes <= 0:
            raise ValueError("CPU scoring child memory budget must be positive")
        self.child_memory_peak_bytes = 0
        self.memory_checked_at = 0.0
        self.serial_fallback_tasks = 0
        self.process_observers = []

    def __enter__(self):
        return self

    def _check(self):
        if self.callback is not None:
            self.callback()
        if self.closed:
            raise RuntimeError("CPU scoring pool is closed")
        now = time.monotonic()
        if self.process_observers and now - self.memory_checked_at >= 1.0:
            import psutil
            from rallymate_vision.postprocess_resources import PostprocessResourceLimit
            self.memory_checked_at = now
            total = 0
            for process in self.process_observers:
                try:
                    if not process.is_running():
                        continue
                    info = process.memory_info()
                    total += getattr(info, "private", info.rss)
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied as error:
                    raise PostprocessResourceLimit("Cannot inspect CPU scoring child memory") from error
            self.child_memory_peak_bytes = max(self.child_memory_peak_bytes, total)
            if total > self.max_child_memory_bytes:
                raise PostprocessResourceLimit("CPU scoring children exceeded their aggregate memory budget")

    def _add_worker(self):
        parent, child = self.context.Pipe(duplex=True)
        process = self.context.Process(target=_worker, args=(child, self.stopped, self.max_result_bytes),
                                       name="RallyMateScoringCPU", daemon=True)
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        self.children.append((process, parent))
        self.worker_pids.append(process.pid)
        import psutil
        self.process_observers.append(psutil.Process(process.pid))
        return parent

    def _stage_task(self, function, args, kwargs):
        # Do not block the supervisor in Pipe.send(big_python_object): a child
        # stuck during import would otherwise also block cancellation/leases.
        if self.spool is None:
            self.spool = tempfile.TemporaryDirectory(prefix="rallymate-scoring-cpu-")
        path = Path(self.spool.name) / f"{self.task_serial}.pickle"
        self.task_serial += 1
        started = time.perf_counter()
        try:
            with path.open("wb") as handle:
                pickle.dump((function, args, kwargs), _CheckedFile(handle, self._check, self.max_task_bytes),
                            protocol=pickle.HIGHEST_PROTOCOL)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self.peak_task_bytes = max(self.peak_task_bytes, path.stat().st_size)
        self.task_transfer_seconds += time.perf_counter() - started
        return path

    def ordered_map(self, function: Callable, tasks: Iterable[tuple[tuple, dict]]) -> Iterator:
        if self.workers == 1:
            for args, kwargs in tasks:
                self._check()
                yield function(*args, **kwargs, cancellation_callback=self.callback)
            return
        iterator = iter(tasks)
        active, completed = {}, {}
        submitted = delivered = 0
        exhausted = False
        complete = False
        try:
            while active or completed or not exhausted:
                self._check()
                while not exhausted and len(active) + len(completed) < self.workers:
                    try:
                        args, kwargs = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    try:
                        path = self._stage_task(function, args, kwargs)
                    except _TransferLimit:
                        self.serial_fallback_tasks += 1
                        completed[submitted] = function(*args, **kwargs, cancellation_callback=self._check)
                        submitted += 1
                        continue
                    available = [pipe for _, pipe in self.children if pipe not in active]
                    pipe = available[0] if available else self._add_worker()
                    pipe.send(str(path))
                    active[pipe] = (submitted, path)
                    submitted += 1
                if active:
                    ready = wait(list(active), timeout=0.05)
                    self._check()
                    for pipe in ready:
                        try:
                            marker = pipe.recv_bytes(maxlength=16)
                            if marker != b"ready":
                                raise RuntimeError("CPU scoring child returned an invalid response")
                        except (EOFError, BrokenPipeError) as error:
                            raise RuntimeError("CPU scoring child exited without a result") from error
                        index, path = active.pop(pipe)
                        result_path = path.with_suffix(".result")
                        if result_path.stat().st_size > max(self.max_result_bytes, 65536):
                            raise RuntimeError("CPU scoring result exceeded its byte budget")
                        with result_path.open("rb") as handle:
                            success, value = pickle.load(_CheckedFile(handle, self._check))
                        result_path.unlink(missing_ok=True)
                        if not success and isinstance(value, _TransferLimit):
                            self.serial_fallback_tasks += 1
                            with path.open("rb") as handle:
                                fallback_function, args, kwargs = pickle.load(_CheckedFile(handle, self._check))
                            value = fallback_function(*args, **kwargs, cancellation_callback=self._check)
                            success = True
                        path.unlink(missing_ok=True)
                        if not success:
                            raise value
                        completed[index] = value
                    if not ready:
                        for process, pipe in self.children:
                            if pipe in active and not process.is_alive():
                                raise RuntimeError("CPU scoring child exited without a result")
                while delivered in completed:
                    value = completed.pop(delivered)
                    delivered += 1
                    yield value
            complete = True
        finally:
            if not complete:
                self.close(cancel=True)

    def close(self, *, cancel=False):
        if self.closed:
            return
        self.closed = True
        if cancel:
            self.stopped.set()
        else:
            for _, pipe in self.children:
                try:
                    pipe.send(None)
                except (EOFError, BrokenPipeError, OSError):
                    pass
        deadline = time.monotonic() + 3.0
        for process, _ in self.children:
            process.join(max(0.0, deadline - time.monotonic()))
        for process, _ in self.children:
            if process.is_alive():
                process.terminate()
        deadline = time.monotonic() + 3.0
        for process, _ in self.children:
            process.join(max(0.0, deadline - time.monotonic()))
        alive = [process.pid for process, _ in self.children if process.is_alive()]
        for process, pipe in self.children:
            pipe.close()
            if not process.is_alive():
                process.close()
        if alive:
            from rallymate_vision.owned_subprocess import ProcessTreeCleanupError
            raise ProcessTreeCleanupError(f"CPU scoring child exit not confirmed: {alive}")
        if self.spool is not None:
            spool = Path(self.spool.name).resolve()
            if spool.parent != Path(tempfile.gettempdir()).resolve() or not spool.name.startswith("rallymate-scoring-cpu-"):
                raise RuntimeError("CPU scoring staging directory escaped its temporary root")
            try:
                self.spool.cleanup()
            except OSError:
                # The children have exited. A locked temporary file must not
                # turn the caller's cancellation into a different failure.
                LOGGER.warning("Could not remove CPU scoring temporary files: %s", spool)

    def __exit__(self, exc_type, exc, traceback):
        self.close(cancel=exc_type is not None)
