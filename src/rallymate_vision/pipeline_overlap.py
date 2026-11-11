"""Bounded chronological frame preparation ahead of one SAM request owner.

This schedules existing operations; it does not change model inputs or batches.
The producer owns the source iterator and its mutable tracking/camera state.
The consumer owns pose state, publication and cancellation callbacks.
"""
from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
import threading
import time

from rallymate_vision.owned_subprocess import ProcessTreeCleanupError


class _PreparationStopped(BaseException):
    pass


def overlap_enabled(environment) -> bool:
    value = environment.get("RALLYMATE_SAM3D_PIPELINE_OVERLAP", "0")
    if value not in {"0", "1"}:
        raise ValueError("RALLYMATE_SAM3D_PIPELINE_OVERLAP must be 0 or 1")
    return value == "1"


def require_isolated_sam(perception):
    # The direct engine shares torch's process-wide synchronization/cache state
    # with YOLO. Only the existing isolated worker is qualified for this overlap.
    from rallymate_vision.sam3d_runner import Sam3DProcess
    engine = getattr(getattr(perception, "pose_backend", None), "engine", None)
    if not isinstance(engine, Sam3DProcess):
        raise ValueError("Pipeline overlap requires the isolated SAM subprocess backend")


class DetectionStage:
    """Expose only detector methods and private snapshots of detector diagnostics."""

    def __init__(self, perception, cpu_seconds):
        self._perception, self._cpu_seconds = perception, cpu_seconds
        self.last_detection_timing = {}
        self.last_detection_timings = []
        self._owner = None

    def _call(self, name, value):
        owner = threading.get_ident()
        if self._owner is None:
            self._owner = owner
        if self._owner != owner:
            raise RuntimeError("Detection stage changed its chronological owner")
        started = time.thread_time()
        try:
            return getattr(self._perception, name)(value)
        finally:
            self._cpu_seconds["detection_seconds"] += time.thread_time() - started
            self.last_detection_timing = deepcopy(getattr(self._perception, "last_detection_timing", {}))
            self.last_detection_timings = deepcopy(getattr(self._perception, "last_detection_timings", []))

    def detect(self, frame):
        return self._call("detect", frame)

    def detect_batch(self, frames):
        return self._call("detect_batch", frames)


class PoseStage:
    """Keep pose calls and diagnostics on the consumer, separate from detector state."""

    def __init__(self, perception):
        self._perception = perception
        self._owner = threading.get_ident()
        self.last_pose_association_diagnostics = None
        self.seconds = Counter()
        self.calls = 0

    def _call(self, name, *args, **kwargs):
        if threading.get_ident() != self._owner:
            raise RuntimeError("SAM requests must retain one consumer owner")
        wall, cpu = time.perf_counter(), time.thread_time()
        try:
            return getattr(self._perception, name)(*args, **kwargs)
        finally:
            self.seconds["sam_request_wall_seconds"] += time.perf_counter() - wall
            self.seconds["sam_request_host_thread_cpu_seconds"] += time.thread_time() - cpu
            self.calls += 1
            self.last_pose_association_diagnostics = deepcopy(
                getattr(self._perception, "last_pose_association_diagnostics", None))

    def estimate_poses(self, *args, **kwargs):
        return self._call("estimate_poses", *args, **kwargs)

    def estimate_pose_batch(self, requests):
        return self._call("estimate_pose_batch", requests)

    def pose_frame_records(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("SAM records must retain one consumer owner")
        return self._perception.pose_frame_records()


class PreparedFrameStream:
    """One producer, one consumer, bounded ready frames and source-image bytes.

    Pixels and metadata are copied before advancing the iterator, so decoder
    buffer reuse or later tracker mutations cannot change queued rows. The
    image copy is exact, without preprocessing, resizing or dtype conversion.
    Apart from the ready queue, the producer may hold an input and its owned
    copy (each up to the byte bound); upstream buffers and the consumer batch retain
    their existing independent bounds. No futures or video-length lists grow.
    """

    def __init__(self, source_factory, *, enabled, max_frames=2,
                 max_bytes=64 * 1024 * 1024, check_cancelled=lambda: None,
                 join_timeout_seconds=30):
        if type(enabled) is not bool or type(max_frames) is not int or not 1 <= max_frames <= 8:
            raise ValueError("Prepared-frame scheduling requires a boolean and 1..8 ready frames")
        if type(max_bytes) is not int or max_bytes < 1 or join_timeout_seconds <= 0:
            raise ValueError("Prepared-frame scheduling requires positive byte/join bounds")
        self.enabled, self.max_frames, self.max_bytes = enabled, max_frames, max_bytes
        self._factory, self._check_cancelled = source_factory, check_cancelled
        self._join_timeout = join_timeout_seconds
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._pending, self._bytes = deque(), 0
        self._done = self._closed = False
        self._error = None
        self._source = self._thread = None
        self._owner = threading.get_ident()
        self._stats = Counter()
        self._last_delivered_at = None
        # Start lazily: caller registers cleanup before a producer can own data.

    @property
    def alive(self):
        return self._thread is not None and self._thread.is_alive()

    def check_stopped(self):
        if self._stop.is_set():
            raise _PreparationStopped()

    def _next_source(self):
        self.check_stopped()
        wall, cpu = time.perf_counter(), time.thread_time()
        try:
            item = next(self._source)
            size = item["image"].nbytes
            if type(size) is not int or not 0 < size <= self.max_bytes:
                raise ValueError("Prepared source frame exceeds its pixel-byte bound")
            copy_started = time.perf_counter()
            item = {**deepcopy({key: value for key, value in item.items() if key != "image"}),
                    "image": item["image"].copy()}
            with self._condition:
                self._stats["ownership_copy_seconds"] += time.perf_counter() - copy_started
                self._stats["ownership_copy_image_bytes"] += size
                self._stats["prepared_frames"] += 1
                self._stats["peak_producer_frame_bytes"] = max(self._stats["peak_producer_frame_bytes"], size)
            return item, size
        finally:
            with self._condition:
                self._stats["prepare_wall_seconds"] += time.perf_counter() - wall
                self._stats["prepare_thread_cpu_seconds"] += time.thread_time() - cpu

    def _produce(self):
        try:
            self._source = iter(self._factory(self.check_stopped))
            while True:
                item, size = self._next_source()
                ready_at = time.perf_counter()
                with self._condition:
                    while not self._stop.is_set() and (len(self._pending) >= self.max_frames
                                                       or self._bytes + size > self.max_bytes):
                        started = time.perf_counter()
                        self._condition.wait(.05)
                        self._stats["producer_backpressure_wait_seconds"] += time.perf_counter() - started
                    self.check_stopped()
                    enqueued_at = time.perf_counter()
                    self._stats["ready_to_enqueue_seconds"] += enqueued_at - ready_at
                    self._pending.append((item, size, enqueued_at))
                    self._bytes += size
                    self._stats["enqueued_frames"] += 1
                    self._stats["peak_ready_frames"] = max(self._stats["peak_ready_frames"], len(self._pending))
                    self._stats["peak_ready_source_bytes"] = max(self._stats["peak_ready_source_bytes"], self._bytes)
                    self._condition.notify_all()
                item = None
        except (StopIteration, _PreparationStopped):
            pass
        except BaseException as exc:
            with self._condition:
                self._error = exc
        finally:
            try:
                if self._source is not None and callable(getattr(self._source, "close", None)):
                    self._source.close()
            except BaseException as exc:
                with self._condition:
                    self._error = self._error or exc
            finally:
                with self._condition:
                    self._done = True
                    self._condition.notify_all()

    def __iter__(self):
        return self

    def __next__(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Prepared frames must retain one consumer owner")
        if self._closed:
            raise StopIteration
        try:
            self._check_cancelled()
            now = time.perf_counter()
            if self._last_delivered_at is not None:
                self._stats["consumer_between_frames_seconds"] += now - self._last_delivered_at
                self._last_delivered_at = None
            if not self.enabled:
                if self._source is None:
                    self._source = iter(self._factory(self._check_cancelled))
                item, _ = self._next_source()
            else:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._produce, name="sam-frame-prepare", daemon=True)
                    self._thread.start()
                while True:
                    self._check_cancelled()
                    with self._condition:
                        if self._error is not None:
                            raise self._error
                        if self._pending:
                            item, size, enqueued_at = self._pending.popleft()
                            self._bytes -= size
                            residence = time.perf_counter() - enqueued_at
                            self._stats["queue_residence_seconds"] += residence
                            self._stats["max_queue_residence_seconds"] = max(
                                self._stats["max_queue_residence_seconds"], residence)
                            self._condition.notify_all()
                            break
                        if self._done:
                            raise StopIteration
                        started = time.perf_counter()
                        self._condition.wait(.05)
                        self._stats["consumer_empty_wait_seconds"] += time.perf_counter() - started
            self._check_cancelled()
            self._stats["consumed_frames"] += 1
            self._last_delivered_at = time.perf_counter()
            return item
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._closed:
            return
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(self._join_timeout)
            if self._thread.is_alive():
                # A detector still owns its frame/decoder. Do not reuse the
                # worker or destroy these resources until the process exits.
                raise ProcessTreeCleanupError("Frame preparation did not stop; its resources remain owned")
        elif self._source is not None and callable(getattr(self._source, "close", None)):
            self._source.close()
        if self._last_delivered_at is not None:
            self._stats["consumer_between_frames_seconds"] += time.perf_counter() - self._last_delivered_at
            self._last_delivered_at = None
        self._stats["discarded_ready_frames"] += len(self._pending)
        self._pending.clear()
        self._bytes = 0
        self._closed = True

    @property
    def receipt(self):
        if self.alive:
            raise RuntimeError("Join preparation before reading final pipeline statistics")
        return {"policy": "bounded_ordered_prepare_overlap_v1", "enabled": self.enabled,
                "ready_capacity_frames": self.max_frames, "ready_capacity_bytes": self.max_bytes,
                "additional_producer_frames_bound": 2, "additional_producer_bytes_bound": 2 * self.max_bytes,
                **dict(self._stats), "joined": self._closed,
                "timing_semantics": "wall and thread CPU; producer backpressure is consumer pressure; "
                "consumer empty wait is preparation starvation, not model time; residence and consumer-between "
                "include overlapping work and must not be summed with model or pipeline wall"}
