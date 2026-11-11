"""Bounded mesh preparation and codec queues; the caller retains GL ownership."""
from __future__ import annotations

from collections import Counter, deque
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass
import threading
import time

from rallymate_vision.owned_subprocess import ProcessTreeCleanupError


MAX_PENDING_FRAMES = 2
MAX_PENDING_ESTIMATED_BYTES = 128 * 1024 * 1024


def encode_overlap_enabled(environment) -> bool:
    value = environment.get("RALLYMATE_SAM3D_MESH_ENCODE_OVERLAP", "1")
    if value not in {"0", "1"}:
        raise ValueError("RALLYMATE_SAM3D_MESH_ENCODE_OVERLAP must be 0 or 1")
    return value == "1"


class MeshEncodingQueue:
    """Overlap immutable rendered pixels with one chronological codec owner.

    The two codecs have already opened before submission. Only this worker
    calls them until finish/close confirms its exit; flushing/closing then
    returns to the caller. No GL object or prepared scene enters the queue.
    The pending bound includes the actively encoded pair, not only the deque.
    A pair exceeding the byte reservation runs alone, retaining original size.
    """
    def __init__(self, encode_pair, *, enabled, check_cancelled=lambda: None,
                 max_pending_frames=2, max_pending_bytes=MAX_PENDING_ESTIMATED_BYTES,
                 join_timeout_seconds=30.):
        if (type(enabled) is not bool or type(max_pending_frames) is not int
                or not 1 <= max_pending_frames <= 8 or type(max_pending_bytes) is not int
                or max_pending_bytes < 1 or join_timeout_seconds <= 0):
            raise ValueError("Mesh encoding requires bounded frames/bytes and a positive join timeout")
        self.encode_pair, self.enabled, self.check_cancelled = encode_pair, enabled, check_cancelled
        self.max_pending_frames, self.max_pending_bytes = max_pending_frames, max_pending_bytes
        self.join_timeout = join_timeout_seconds
        self._owner = threading.get_ident()
        self._condition = threading.Condition()
        self._queue = deque()
        self._thread = self._error = self._join_deadline = None
        self._stopping = self._draining = self._closed = False
        self._pending_frames = self._pending_bytes = 0
        self._stats = Counter()

    def _require_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Mesh encoding submission and cleanup must retain one owner")

    def _worker(self):
        try:
            while True:
                with self._condition:
                    while not self._queue and not self._stopping and not self._draining:
                        self._condition.wait()
                    if self._stopping or not self._queue and self._draining:
                        return
                    images, pts, size = self._queue.popleft()
                started = time.perf_counter()
                self.encode_pair(images, pts)
                elapsed = time.perf_counter() - started
                images = None  # Release owned arrays before releasing their reservation.
                with self._condition:
                    self._pending_frames -= 1
                    self._pending_bytes -= size
                    self._stats["encoded_frames"] += 1
                    self._stats["encode_wall_seconds"] += elapsed
                    self._condition.notify_all()
        except BaseException as error:
            with self._condition:
                self._error = error
                self._condition.notify_all()

    def submit(self, images, pts):
        self._require_owner()
        self.check_cancelled()
        if self._closed or self._draining or self._stopping:
            raise RuntimeError("Mesh encoder no longer accepts frames")
        # The renderer allocates fresh output arrays; freeze them before handing
        # ownership to the codec worker. It never sees reusable decoder buffers.
        images = tuple(images)
        size = sum(int(image.nbytes) for image in images)
        for image in images:
            image.setflags(write=False)
        if not self.enabled:
            started = time.perf_counter()
            self.encode_pair(images, pts)
            self._stats["encode_wall_seconds"] += time.perf_counter() - started
            self._stats["submitted_frames"] += 1
            self._stats["encoded_frames"] += 1
            return
        with self._condition:
            while True:
                if self._error is not None:
                    raise self._error
                self.check_cancelled()
                if (self._pending_frames < self.max_pending_frames
                        and (not self._pending_frames or self._pending_bytes + size <= self.max_pending_bytes)):
                    break
                started = time.perf_counter()
                self._condition.wait(.05)
                self._stats["foreground_backpressure_seconds"] += time.perf_counter() - started
            self._queue.append((images, pts, size))
            self._pending_frames += 1
            self._pending_bytes += size
            self._stats["submitted_frames"] += 1
            self._stats["oversize_pairs_run_alone"] += int(size > self.max_pending_bytes)
            self._stats["peak_pending_frames"] = max(self._stats["peak_pending_frames"], self._pending_frames)
            self._stats["peak_pending_pixel_bytes"] = max(self._stats["peak_pending_pixel_bytes"], self._pending_bytes)
            if self._thread is None:
                self._thread = threading.Thread(target=self._worker, name="mesh-video-encode", daemon=True)
                self._thread.start()
            self._condition.notify_all()

    def finish(self):
        """Drain every accepted frame before allowing encoder flush/publication."""
        self._require_owner()
        with self._condition:
            self._draining = True
            self._condition.notify_all()
        if self._join_deadline is None:
            self._join_deadline = time.monotonic() + self.join_timeout
        while self._thread is not None and self._thread.is_alive():
            self.check_cancelled()
            remaining = self._join_deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessTreeCleanupError("Mesh encoder did not drain; codec/resources must remain owned")
            self._thread.join(min(.05, remaining))
        self._closed = True
        if self._error is not None:
            raise self._error
        if self._stats["submitted_frames"] != self._stats["encoded_frames"]:
            raise RuntimeError("Mesh encoder did not drain every submitted frame")

    def close(self):
        """Cancel queued work on failure and join before codecs may be closed."""
        self._require_owner()
        if self._closed:
            return
        with self._condition:
            self._stopping = True
            self._stats["discarded_queued_frames"] += len(self._queue)
            self._queue.clear()
            self._condition.notify_all()
        if self._join_deadline is None:
            self._join_deadline = time.monotonic() + self.join_timeout
        if self._thread is not None:
            self._thread.join(max(0., self._join_deadline - time.monotonic()))
            if self._thread.is_alive():
                raise ProcessTreeCleanupError("Mesh encoder did not join; codec/resources must remain owned")
        self._closed = True

    @property
    def receipt(self):
        self._require_owner()
        if not self._closed or self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Join mesh encoding before collecting its receipt")
        return {"policy": "bounded_chronological_encode_overlap_v1", "enabled": self.enabled,
                "max_pending_frames_including_active": self.max_pending_frames,
                "max_pending_pixel_bytes": self.max_pending_bytes,
                **{key: self._stats[key] for key in ("submitted_frames", "encoded_frames",
                    "peak_pending_frames", "peak_pending_pixel_bytes", "oversize_pairs_run_alone",
                    "foreground_backpressure_seconds", "encode_wall_seconds", "discarded_queued_frames")},
                "joined": True, "frame_order_and_encoder_options_unchanged": True,
                "memory_scope": "rendered pixel pairs including active encode; one oversize pair runs alone; excludes codecs, GL and current render/preparation"}


def prepare_workers(environment) -> int:
    value = environment.get("RALLYMATE_SAM3D_MESH_PREPARE_WORKERS", "0")
    if value not in {"0", "2"}:
        raise ValueError("RALLYMATE_SAM3D_MESH_PREPARE_WORKERS must be 0 or 2")
    return int(value)


@dataclass
class PreparedMeshPair:
    # Every field is private to one frame, except read-only imported modules.
    # In particular this object must never own an OffscreenRenderer/context.
    image: object
    white: object
    scene: object = None
    mesh_node: object = None
    camera_node: object = None
    camera_pose: object = None
    flip: object = None
    merged: object = None
    focal: float = 0.


def estimated_prepare_bytes(frame) -> int:
    """Conservative geometry/scene reservation, not a process RSS hard limit.

    Include owned pixels/arrays and temporary/retained float64 triangle, cross,
    angle, normal, merge, sparse-index and pyrender copies. Decoder/encoder/GL
    memory and the caller's current rendered frame have independent ownership.
    """
    image, outputs = frame["image"], frame["outputs"]
    total = 2 * int(image.nbytes)
    for vertices, faces, camera, _ in outputs:
        total += int(vertices.nbytes + faces.nbytes + camera.nbytes)
        total += 256 * len(vertices) + 512 * len(faces)
    return total


def retained_input_bytes(frame) -> int:
    return int(frame["image"].nbytes) + sum(
        int(vertices.nbytes + faces.nbytes + camera.nbytes)
        for vertices, faces, camera, _ in frame["outputs"])


class PreparedMeshFrames:
    """FIFO CPU futures; source reads and result consumption stay on one thread.

    At most two unconsumed jobs are retained. A not-yet-submitted source item
    counts toward that bound, and owns only input arrays, not another scene.
    Oversize items drain earlier futures and use the original synchronous path.
    Worker threads publish only their own final counters; receipts require join.
    Daemon threads permit an isolated OwnedProcess to exit after a failed join,
    rather than an executor's implicit interpreter-exit wait hiding that error.
    """

    def __init__(self, source, prepare, *, workers, check_cancelled=lambda: None,
                 max_pending_bytes=MAX_PENDING_ESTIMATED_BYTES, join_timeout_seconds=30.):
        if workers not in (0, 2) or type(workers) is not int:
            raise ValueError("Mesh preparation supports exactly zero or two CPU workers")
        if type(max_pending_bytes) is not int or max_pending_bytes < 1 or join_timeout_seconds <= 0:
            raise ValueError("Mesh preparation requires positive byte and join bounds")
        self.source, self.prepare, self.workers = iter(source), prepare, workers
        self.check_cancelled = check_cancelled
        self.max_pending_bytes, self.join_timeout = max_pending_bytes, join_timeout_seconds
        self._owner = threading.get_ident()
        self._condition = threading.Condition()
        self._jobs, self._pending = deque(), deque()
        self._threads, self._worker_stats = [], [None] * workers
        self._deferred = None
        self._pending_bytes = 0
        self._pending_input_bytes = 0
        self._join_deadline = None
        self._exhausted = self._stopping = self._closed = False
        self._stats = Counter()

    def _require_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Mesh source, FIFO consumption and cleanup must retain one owner")

    def _worker(self, index):
        seconds, calls = 0., 0
        try:
            while True:
                with self._condition:
                    while not self._jobs and not self._stopping:
                        self._condition.wait()
                    if self._stopping:
                        return
                    future, frame = self._jobs.popleft()
                if future.set_running_or_notify_cancel():
                    started = time.perf_counter()
                    try:
                        result = self.prepare(frame["image"], frame["outputs"])
                    except BaseException as error:
                        future.set_exception(error)
                    else:
                        future.set_result(result)
                        result = None
                    finally:
                        seconds += time.perf_counter() - started
                        calls += 1
                future = frame = None
        finally:
            self._worker_stats[index] = {"calls": calls, "wall_seconds": seconds}

    def _start(self):
        if self._threads:
            return
        for index in range(self.workers):
            thread = threading.Thread(target=self._worker, args=(index,),
                                      name=f"mesh-prepare-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def _sync(self, frame, *, fallback=False):
        self.check_cancelled()
        started = time.perf_counter()
        pair = self.prepare(frame["image"], frame["outputs"])
        self._stats["synchronous_prepare_wall_seconds"] += time.perf_counter() - started
        self._stats["synchronous_large_frame_fallbacks"] += int(fallback)
        self._stats["consumed_frames"] += 1
        self.check_cancelled()
        return frame, pair

    def _fill(self):
        while len(self._pending) < MAX_PENDING_FRAMES and not self._exhausted:
            self.check_cancelled()
            if self._deferred is not None:
                frame, cost = self._deferred
                self._deferred = None
            else:
                try:
                    frame = next(self.source)
                except StopIteration:
                    self._exhausted = True
                    break
                cost = estimated_prepare_bytes(frame)
            if cost > self.max_pending_bytes or self._pending_bytes + cost > self.max_pending_bytes:
                self._deferred = (frame, cost)
                self._track_retained()
                break
            self._start()
            future = Future()
            self._pending.append((future, frame, cost))
            self._pending_bytes += cost
            self._pending_input_bytes += retained_input_bytes(frame)
            self._stats["submitted_frames"] += 1
            self._stats["peak_pending_frames"] = max(self._stats["peak_pending_frames"], len(self._pending))
            self._stats["peak_pending_estimated_bytes"] = max(
                self._stats["peak_pending_estimated_bytes"], self._pending_bytes)
            self._track_retained()
            with self._condition:
                self._jobs.append((future, frame))
                self._condition.notify()

    def _track_retained(self):
        deferred_bytes = retained_input_bytes(self._deferred[0]) if self._deferred is not None else 0
        self._stats["peak_deferred_input_bytes"] = max(self._stats["peak_deferred_input_bytes"], deferred_bytes)
        self._stats["peak_unconsumed_jobs"] = max(self._stats["peak_unconsumed_jobs"],
            len(self._pending) + int(self._deferred is not None))
        self._stats["peak_unconsumed_input_bytes"] = max(self._stats["peak_unconsumed_input_bytes"],
            self._pending_input_bytes + deferred_bytes)
        self._stats["peak_submitted_scene_estimate_plus_deferred_input_bytes"] = max(
            self._stats["peak_submitted_scene_estimate_plus_deferred_input_bytes"], self._pending_bytes + deferred_bytes)

    def __iter__(self):
        return self

    def __next__(self):
        self._require_owner()
        if self._closed:
            raise StopIteration
        try:
            self.check_cancelled()
            if not self.workers:
                return self._sync(next(self.source))
            self._fill()
            if self._pending:
                future, frame, cost = self._pending.popleft()
                started = time.perf_counter()
                try:
                    while True:
                        self.check_cancelled()
                        try:
                            pair = future.result(timeout=.05)
                            break
                        except FutureTimeout:
                            if future.done():
                                # Distinguish a real task TimeoutError from a
                                # wait timeout racing with normal completion.
                                pair = future.result()
                                break
                            continue
                finally:
                    self._stats["foreground_wait_seconds"] += time.perf_counter() - started
                    self._pending_bytes -= cost
                    self._pending_input_bytes -= retained_input_bytes(frame)
                self._stats["consumed_frames"] += 1
                self.check_cancelled()
                # The returned pair occupies the main thread's current-frame
                # slot. Keep two later jobs running during its GL/encode work.
                self._fill()
                return frame, pair
            if self._deferred is not None:
                frame, _ = self._deferred
                self._deferred = None
                return self._sync(frame, fallback=True)
            raise StopIteration
        except BaseException:
            self.close()
            raise

    def close(self):
        self._require_owner()
        if self._closed:
            return
        with self._condition:
            self._stopping = True
            for future, _, _ in self._pending:
                future.cancel()
            self._jobs.clear()
            self._condition.notify_all()
        if self._join_deadline is None:
            self._join_deadline = time.monotonic() + self.join_timeout
        for thread in self._threads:
            thread.join(max(0., self._join_deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self._threads):
            raise ProcessTreeCleanupError("Mesh CPU preparation did not join; GL/resources must remain owned")
        close = getattr(self.source, "close", None)
        try:
            if callable(close):
                close()
        finally:
            self._pending.clear()
            self._deferred = None
            self._pending_bytes = self._pending_input_bytes = 0
            self._closed = True

    @property
    def receipt(self):
        self._require_owner()
        if not self._closed or any(thread.is_alive() for thread in self._threads):
            raise RuntimeError("Join CPU preparation before collecting its final counters")
        worker_stats = [row for row in self._worker_stats if row is not None]
        return {"policy": "bounded_cpu_scene_prepare_fifo_v1", "workers": self.workers,
            "max_pending_frames": MAX_PENDING_FRAMES, "max_pending_estimated_bytes": self.max_pending_bytes,
            **{key: self._stats[key] for key in ("submitted_frames", "consumed_frames", "peak_pending_frames",
                "peak_pending_estimated_bytes", "synchronous_large_frame_fallbacks", "foreground_wait_seconds",
                "synchronous_prepare_wall_seconds", "peak_deferred_input_bytes", "peak_unconsumed_jobs",
                "peak_unconsumed_input_bytes", "peak_submitted_scene_estimate_plus_deferred_input_bytes")},
            "prepare_worker_wall_seconds_sum": sum(row["wall_seconds"] for row in worker_stats),
            "per_worker": worker_stats, "joined": True,
            "memory_scope": "128MiB default caps only submitted scene estimates, not total RSS or deferred raw inputs. At most two unconsumed jobs include deferred input; its measured raw bytes and combined peak are reported separately. A single oversized source frame is read, drains previous work, then prepares synchronously and can exceed that scene reservation. Current main-thread frame, decoder/encoder and GL allocations are excluded",
            "timing_semantics": "Worker prepare wall sums overlap each other and main-thread decode/GL/encode; foreground wait is only consumer blocking; do not sum overlapping phases as pipeline wall"}
