"""Bounded CPU mesh serialization behind the current request's GPU work."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import numpy as np


class BoundedMeshWriter:
    """Own snapshots until atomic capture completes; never touch CUDA tensors.

    Backpressure counts queued and running groups and their copied array bytes.
    Result publication remains the caller's responsibility after Future.result.
    """

    def __init__(self, runtime_dir, *, max_groups=2, max_bytes=64 * 1024 * 1024):
        if type(max_groups) is not int or max_groups < 1 or type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("Mesh capture bounds must be positive integers")
        self.runtime_dir, self.max_groups, self.max_bytes = runtime_dir, max_groups, max_bytes
        self._condition = threading.Condition()
        self._groups = self._bytes = 0
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sam-mesh-capture")
        self.receipt = {"worker_threads": 1, "maximum_pending_groups": max_groups,
                        "maximum_pending_array_bytes": max_bytes,
                        "peak_pending_groups": 0, "peak_pending_array_bytes": 0,
                        "submitted_groups": 0, "publication": "joined_before_request_reply"}

    def submit(self, outputs, faces):
        if not isinstance(outputs, list) or not 1 <= len(outputs) <= 8:
            raise ValueError("Mesh capture requires one to eight subjects")
        selected = [{key: np.asarray(output[key]) for key in
                     ("pred_vertices", "pred_cam_t", "focal_length")} for output in outputs]
        faces = np.asarray(faces)
        arrays = [faces] + [array for output in selected for array in output.values()]
        if any(array.dtype.kind not in "fiu" for array in arrays):
            raise ValueError("Mesh capture requires numeric arrays")
        size = sum(array.nbytes for array in arrays)
        if size > self.max_bytes:
            raise ValueError("Mesh capture group exceeds its bounded array budget")
        started = time.perf_counter()
        with self._condition:
            while not self._closed and (self._groups >= self.max_groups or self._bytes + size > self.max_bytes):
                self._condition.wait()
            if self._closed:
                raise RuntimeError("Mesh capture writer is closed")
            self._groups += 1
            self._bytes += size
            try:
                # Snapshot before inference can reuse output storage. All dtype
                # and values remain exact; packaging and hashes run on worker.
                owned = [{key: np.array(value, copy=True) for key, value in output.items()}
                         for output in selected]
                # The executor's WorkItem can survive in an exception
                # traceback. Keep every array behind a clearable container so
                # releasing a budget slot also releases its WorkItem references.
                payload = [owned, np.array(faces, copy=True)]
                future = self._executor.submit(self._capture, payload, size)
            except BaseException:
                self._groups -= 1
                self._bytes -= size
                self._condition.notify_all()
                raise
            self.receipt["submitted_groups"] += 1
            self.receipt["peak_pending_groups"] = max(self.receipt["peak_pending_groups"], self._groups)
            self.receipt["peak_pending_array_bytes"] = max(self.receipt["peak_pending_array_bytes"], self._bytes)
        return future, (time.perf_counter() - started) * 1000

    def _capture(self, payload, size):
        from rallymate_vision.sam3d_mesh import capture_mesh_output, discard_unarchived_mesh_records

        outputs, faces = payload
        started, artifacts = time.perf_counter(), []
        try:
            for output in outputs:
                artifacts.append(capture_mesh_output(output, faces, self.runtime_dir))
            return artifacts, (time.perf_counter() - started) * 1000
        except BaseException as exc:
            discard_unarchived_mesh_records([{"mesh_artifact": artifact} for artifact in artifacts], self.runtime_dir)
            # A failed future must not keep the capture function's array views
            # alive through its traceback after this budget slot is released.
            raise exc.with_traceback(None) from None
        finally:
            # Release owned CPU arrays before advertising the memory capacity.
            outputs.clear()
            payload.clear()
            output = faces = None
            with self._condition:
                self._groups -= 1
                self._bytes -= size
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        # Never return while writes still access owned arrays or output paths.
        self._executor.shutdown(wait=True)
