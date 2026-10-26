"""One idle-only renderer, with durable requests and inference preemption."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from threading import Event

from rallymate_vision.mesh_render_task import TASK_VERSION, TASK_NAME, STATE_NAME, CANCEL_NAME, atomic_json
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.owned_subprocess import ProcessTreeCleanupError

LOGGER = logging.getLogger("rallymate.mesh")


class MeshRenderDeferred(RuntimeError):
    """New inference or service shutdown: retained request must be retried."""


class MeshRenderCancelled(RuntimeError):
    """Only the optional visualization was explicitly cancelled."""


class RenderLock:
    """OS-held lock, released on close/crash; stale files never block recovery."""
    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def acquire(self) -> bool:
        handle = self.path.open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self.handle = handle
        return True

    def close(self) -> None:
        if self.handle is not None:
            handle, self.handle = self.handle, None
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _read(path: Path) -> dict:
    if path.resolve() != path.absolute():
        raise ValueError("mesh sidecar cannot be a symlink")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("mesh sidecar exceeds bound")
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate mesh sidecar key")
            result[key] = value
        return result
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite mesh sidecar")))
    if not isinstance(value, dict):
        raise ValueError("mesh sidecar must be an object")
    return value


def _sha(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


class MeshRenderCoordinator:
    def __init__(self, settings, database, *, renderer=None):
        self.settings, self.database, self.renderer = settings, database, renderer
        self.root = settings.runs_dir.resolve()

    def _busy(self) -> bool:
        statuses = self.database.count_by_status()
        return any(statuses.get(status, 0) > 0 for status in ("queued", "running"))

    def _job(self, identifier: str):
        with self.database.connect() as connection:
            row = connection.execute("SELECT id, status, cancel_requested, video_path, output_dir, summary_json "
                                     "FROM jobs WHERE id = ?", (identifier,)).fetchone()
        return dict(row) if row is not None else None

    def _validate(self, path: Path) -> tuple[dict, dict, str]:
        output = path.parent.resolve()
        if output.parent != self.root or path.parent.absolute() != output or path.resolve() != path.absolute() or output.name in {".", ".."}:
            raise ValueError("mesh task escapes owned job directory")
        task = _read(path)
        if task.get("version") != TASK_VERSION or task.get("job_id") != output.name or not _sha(task.get("ledger_sha256")):
            raise ValueError("mesh task identity mismatch")
        job = self._job(output.name)
        if not job or job["status"] != "succeeded" or job["cancel_requested"]:
            raise MeshRenderDeferred("source_job_not_successfully_completed")
        if Path(job["output_dir"]).resolve() != output:
            raise ValueError("mesh job output directory mismatch")
        render = task.get("render")
        if not isinstance(render, dict) or set(render) != {"source", "output_dir", "runtime_dir", "first_frame", "last_frame", "stride", "source_sha256", "model"}:
            raise ValueError("mesh render arguments mismatch")
        runtime = Path(os.environ.get("RALLYMATE_SAM3D_RUNTIME", Path(__file__).resolve().parents[2] / "runtime" / "sam3d")).resolve()
        if (Path(render["output_dir"]).resolve() != output or Path(render["source"]).resolve() != Path(job["video_path"]).resolve()
                or Path(render["runtime_dir"]).resolve() != runtime or not _sha(render.get("source_sha256"))
                or not isinstance(render["model"], dict)):
            raise ValueError("mesh render source/runtime mismatch")
        first, last, stride = (render[key] for key in ("first_frame", "last_frame", "stride"))
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (first, last, stride)) or not 0 <= first <= last or stride < 1:
            raise ValueError("invalid mesh frame selection")
        task_sha = sha256_file(path)
        summary = json.loads(job["summary_json"] or "{}")
        descriptor = summary.get("artifacts", {}).get("sam3d_mesh", {})
        if (not isinstance(descriptor, dict) or descriptor.get("status") != "pending"
                or descriptor.get("task") != TASK_NAME or descriptor.get("task_sha256") != task_sha
                or descriptor.get("source_job_id") != job["id"]
                or descriptor.get("source_video_sha256") != render["source_sha256"]
                or descriptor.get("ledger_sha256") != task["ledger_sha256"]):
            raise ValueError("mesh task is not bound to the completed summary")
        from rallymate_vision.sam3d_mesh import LEDGER_NAME
        ledger = output / LEDGER_NAME
        if ledger.resolve().parent != output or sha256_file(ledger) != task["ledger_sha256"]:
            raise ValueError("mesh ledger changed")
        if sha256_file(Path(render["source"])).lower() != render["source_sha256"].lower():
            raise ValueError("mesh source video changed")
        return task, render, task_sha

    def _state(self, output: Path, task_sha: str, status: str, reason: str, **extra) -> None:
        atomic_json(output / STATE_NAME, {"version": TASK_VERSION, "source_job_id": output.name,
            "task_sha256": task_sha, "status": status, "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(), **extra})

    def cancel(self, job_id: str) -> None:
        """Optional-preview cancellation; it never changes the scored job."""
        path = self.root / job_id / TASK_NAME
        _, _, task_sha = self._validate(path)
        atomic_json(path.parent / CANCEL_NAME, {"source_job_id": job_id, "task_sha256": task_sha})
        self._state(path.parent, task_sha, "cancelled", "preview_cancelled_by_request")

    def process_one(self, stop_event: Event | None = None) -> bool:
        if stop_event is not None and stop_event.is_set() or self._busy():
            return False
        lock = RenderLock(self.root / ".sam3d-mesh-render.lock")
        if not lock.acquire():
            return False
        try:
            if self._busy():
                return False
            # Recover all durable requests, including those beyond the API's
            # newest-100-jobs listing. Only direct owned child directories enter.
            paths = sorted((entry / TASK_NAME for entry in self.root.iterdir()
                            if entry.is_dir() and entry.resolve() == entry.absolute() and entry.resolve().parent == self.root and (entry / TASK_NAME).is_file()),
                           key=lambda item: item.stat().st_mtime_ns)
            for path in paths:
                if stop_event is not None and stop_event.is_set() or self._busy():
                    return False
                task_sha = sha256_file(path)
                state_path = path.parent / STATE_NAME
                if state_path.is_file():
                    try:
                        state = _read(state_path)
                    except (ValueError, OSError):
                        state = {}
                    if state.get("task_sha256") == task_sha and state.get("status") in {"ready", "failed", "cancelled"}:
                        continue
                try:
                    task, render, task_sha = self._validate(path)
                except MeshRenderDeferred:
                    continue
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    self._state(path.parent, task_sha, "failed", "invalid_mesh_request", error_type=type(exc).__name__)
                    LOGGER.warning("invalid mesh request for job %s: %s", path.parent.name, exc)
                    continue
                output = path.parent
                def check_cancelled(*, preempt=True):
                    if stop_event is not None and stop_event.is_set():
                        raise MeshRenderDeferred("service_stopping")
                    cancel_path = output / CANCEL_NAME
                    if cancel_path.is_file():
                        cancellation = _read(cancel_path)
                        if cancellation.get("source_job_id") == output.name and cancellation.get("task_sha256") == task_sha:
                            raise MeshRenderCancelled("preview_cancelled_by_request")
                    state = self.database.execution_state(output.name)
                    if not state or state["status"] != "succeeded" or state["cancel_requested"]:
                        raise MeshRenderCancelled("source_job_no_longer_succeeded")
                    if preempt and self._busy():
                        raise MeshRenderDeferred("preempted_for_inference")
                try:
                    check_cancelled()
                    self._state(output, task_sha, "rendering", "idle_gpu_render")
                    if self.renderer is None:
                        from rallymate_vision.sam3d_mesh import render_mesh_subprocess
                        renderer = render_mesh_subprocess
                    else:
                        renderer = self.renderer
                    arguments = {**render, **{key: Path(render[key]) for key in ("source", "output_dir", "runtime_dir")}}
                    manifest = renderer(**arguments, check_cancelled=check_cancelled)
                    check_cancelled(preempt=False)
                    if (not isinstance(manifest, dict) or manifest.get("status") != "ready"
                            or manifest.get("source_video_sha256") != render["source_sha256"]
                            or manifest.get("ledger_sha256") != task["ledger_sha256"]):
                        raise ValueError("rendered manifest does not match source task")
                    from rallymate_vision.sam3d_mesh import MANIFEST_NAME
                    manifest = {**manifest, "source_job_id": output.name, "task_sha256": task_sha}
                    atomic_json(output / MANIFEST_NAME, manifest)
                    self._state(output, task_sha, "ready", "official_mesh_ready")
                except MeshRenderDeferred as exc:
                    self._state(output, task_sha, "pending", str(exc), retryable=True)
                except MeshRenderCancelled as exc:
                    self._state(output, task_sha, "cancelled", str(exc))
                except ProcessTreeCleanupError:
                    self._state(output, task_sha, "failed", "renderer_tree_cleanup_failed")
                    raise
                except Exception as exc:
                    self._state(output, task_sha, "failed", "official_mesh_render_failed", error_type=type(exc).__name__)
                    LOGGER.exception("optional mesh rendering failed for job %s", output.name)
                return True
            return False
        finally:
            lock.close()
