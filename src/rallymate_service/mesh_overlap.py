"""Render sealed geometry during the same owned job's CPU postprocessing.

The caller joins this work before claiming another inference. Media publication
continues to require the existing successfully-scored-job API contract.
"""
from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from threading import Event, Thread

from rallymate_service.mesh_render_worker import (
    MeshRenderCancelled, MeshRenderDeferred, RenderLock, _read,
)
from rallymate_vision.mesh_render_task import CANCEL_NAME, TASK_NAME, atomic_json
from rallymate_vision.owned_subprocess import ProcessTreeCleanupError

LOGGER = logging.getLogger("rallymate.mesh_overlap")


class PostprocessMesh:
    """One renderer thread owning one complete child tree; never detached."""

    def __init__(self, coordinator, job_id: str, descriptor: dict, *,
                 worker_id: str, attempts: int, stop_event: Event | None = None):
        if os.environ.get("RALLYMATE_MANAGED_GPU_UUID"):
            raise ValueError("Managed GPU fleets require qualified renderer routing")
        self.coordinator, self.job_id = coordinator, job_id
        self.descriptor = copy.deepcopy(descriptor)
        self.owner = (worker_id, attempts)
        self.cancel = Event()
        self.stop_event = stop_event
        self.error = None
        self.path = coordinator.root / job_id / TASK_NAME
        # Reject an unrelated owner, changed source or unsealed ledger before
        # dispatch. Validation repeats under the render lock in the thread.
        coordinator._validate(self.path, prepared_descriptor=self.descriptor, owner=self.owner)
        self.thread = Thread(target=self._run, name="postprocess-mesh", daemon=False)
        self.thread.start()

    def _run(self):
        coordinator, output = self.coordinator, self.path.parent
        lock = RenderLock(coordinator.root / ".sam3d-mesh-render.lock")
        task_sha = self.descriptor["task_sha256"]
        try:
            if not lock.acquire():
                return  # The ordinary durable scheduler retains this request.
            task, render, task_sha = coordinator._validate(
                self.path, prepared_descriptor=self.descriptor, owner=self.owner)

            def check_cancelled():
                if self.cancel.is_set() or self.stop_event is not None and self.stop_event.is_set():
                    raise MeshRenderDeferred("postprocessing_render_stopped")
                state = coordinator.database.execution_state(self.job_id)
                if (not state or state["status"] not in {"running", "succeeded"}
                        or (state["worker_id"], state["attempts"]) != self.owner
                        or state["cancel_requested"]):
                    raise MeshRenderCancelled("source_job_owner_or_status_changed")
                cancellation = output / CANCEL_NAME
                if cancellation.is_file():
                    value = _read(cancellation)
                    if value.get("source_job_id") == self.job_id and value.get("task_sha256") == task_sha:
                        raise MeshRenderCancelled("preview_cancelled_by_request")
                # Queued jobs wait for this owned job's two branches to join.
                # Another actually-running job must never overlap its renderer.
                running = coordinator.database.count_by_status().get("running", 0)
                if running > int(state["status"] == "running"):
                    raise MeshRenderDeferred("preempted_for_inference")

            check_cancelled()
            coordinator._state(output, task_sha, "rendering", "overlap_cpu_postprocessing")
            if coordinator.renderer is None:
                from rallymate_vision.sam3d_mesh import render_mesh_subprocess
                renderer = render_mesh_subprocess
            else:
                renderer = coordinator.renderer
            arguments = {**render, **{key: Path(render[key]) for key in ("source", "output_dir", "runtime_dir")}}
            manifest = renderer(**arguments, check_cancelled=check_cancelled)
            check_cancelled()
            # Bind the complete output to the immutable task again after work.
            coordinator._validate(self.path, prepared_descriptor=self.descriptor, owner=self.owner)
            if (not isinstance(manifest, dict) or manifest.get("status") != "ready"
                    or manifest.get("source_video_sha256") != render["source_sha256"]
                    or manifest.get("ledger_sha256") != task["ledger_sha256"]):
                raise ValueError("rendered manifest does not match source task")
            from rallymate_vision.sam3d_mesh import MANIFEST_NAME
            atomic_json(output / MANIFEST_NAME, {**manifest, "source_job_id": self.job_id, "task_sha256": task_sha})
            coordinator._state(output, task_sha, "ready", "official_mesh_ready")
        except MeshRenderDeferred as exc:
            coordinator._state(output, task_sha, "pending", str(exc), retryable=True)
        except MeshRenderCancelled as exc:
            coordinator._state(output, task_sha, "cancelled", str(exc))
        except ProcessTreeCleanupError as exc:
            self.error = exc
            coordinator._state(output, task_sha, "failed", "renderer_tree_cleanup_failed")
        except BaseException as exc:
            # Optional rendering never discards valid scoring. Cleanup failures
            # above are different: they stop the worker from reusing the GPU.
            coordinator._state(output, task_sha, "failed", "official_mesh_render_failed", error_type=type(exc).__name__)
            LOGGER.exception("overlapped mesh rendering failed for %s", self.job_id)
        finally:
            lock.close()

    def finish(self, *, cancel=False):
        if cancel:
            self.cancel.set()
        self.thread.join()
        if self.error is not None:
            raise self.error
