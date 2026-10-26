from __future__ import annotations

import logging
import inspect
import socket
import time
import uuid
from collections.abc import Callable
from threading import Event, Lock

from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_vision.contracts import load_request
from rallymate_vision.inference import Yolo26Perception
from rallymate_vision.pipeline import run_pipeline
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.utils import resolve_device


LOGGER = logging.getLogger("rallymate.worker")
_CPU_THREAD_CONFIGURATION_LOCK = Lock()


class CpuThreadConfigurationError(RuntimeError):
    """A CPU thread pool could not be bounded or its effective limit observed."""


class JobCancelled(RuntimeError):
    """Cooperative stop requested; pipeline must propagate after cleanup."""


class JobLeaseLost(RuntimeError):
    """This execution attempt no longer owns the durable job."""


class CpuFinalizationSafetyError(RuntimeError):
    """A persisted recovery request must never reach an inference runner."""


def _set_thread_pool(
    *,
    name: str,
    requested: int,
    getter: Callable[[], int],
    setter: Callable[[int], None],
) -> int:
    try:
        current = int(getter())
    except Exception as exc:
        raise CpuThreadConfigurationError(
            f"failed to read {name} CPU thread count before model loading: {exc}"
        ) from exc
    if current != requested:
        try:
            setter(requested)
        except Exception as exc:
            raise CpuThreadConfigurationError(
                f"failed to set {name} CPU threads to {requested} before model "
                f"loading (current={current}): {exc}"
            ) from exc
    try:
        actual = int(getter())
    except Exception as exc:
        raise CpuThreadConfigurationError(
            f"failed to verify {name} CPU thread count after requesting "
            f"{requested}: {exc}"
        ) from exc
    if actual != requested:
        raise CpuThreadConfigurationError(
            f"{name} CPU thread limit was not applied: requested={requested}, "
            f"actual={actual}"
        )
    return actual


def configure_cpu_thread_pools(cpu_threads: int) -> dict[str, int]:
    """Bound the parent process before either inference model loads.

    Only intra-op work follows the configured CPU budget. The sequential GPU
    runner does not need another full inter-op or OpenCV worker pool.
    """

    if isinstance(cpu_threads, bool) or not isinstance(cpu_threads, int):
        raise CpuThreadConfigurationError(
            "RALLYMATE_CPU_THREADS must resolve to an integer >= 1 before model "
            f"loading; got {cpu_threads!r}"
        )
    if cpu_threads < 1:
        raise CpuThreadConfigurationError(
            "RALLYMATE_CPU_THREADS must be >= 1 before model loading; "
            f"got {cpu_threads}"
        )
    try:
        import torch
    except Exception as exc:
        raise CpuThreadConfigurationError(
            "failed to import torch while configuring CPU thread limits before "
            f"model loading: {exc}"
        ) from exc
    try:
        import cv2
    except Exception as exc:
        raise CpuThreadConfigurationError(
            "failed to import cv2 while configuring CPU thread limits before "
            f"model loading: {exc}"
        ) from exc

    with _CPU_THREAD_CONFIGURATION_LOCK:
        configured = {
            "torch_interop": _set_thread_pool(
                name="Torch inter-op",
                requested=1,
                getter=torch.get_num_interop_threads,
                setter=torch.set_num_interop_threads,
            ),
            "torch_intraop": _set_thread_pool(
                name="Torch intra-op",
                requested=cpu_threads,
                getter=torch.get_num_threads,
                setter=torch.set_num_threads,
            ),
            "opencv": _set_thread_pool(
                name="OpenCV",
                requested=1,
                getter=cv2.getNumThreads,
                setter=cv2.setNumThreads,
            ),
        }
    configured["requested"] = cpu_threads
    LOGGER.info(
        "CPU thread pools configured before model loading: requested_intraop=%d, "
        "torch_intraop=%d, torch_interop=%d, opencv=%d",
        configured["requested"],
        configured["torch_intraop"],
        configured["torch_interop"],
        configured["opencv"],
    )
    return configured


def observe_cpu_thread_pools() -> dict[str, int]:
    """Read effective pools without resetting process-global runtime settings."""

    try:
        import torch
        import cv2

        observed = {
            "torch_intraop": int(torch.get_num_threads()),
            "torch_interop": int(torch.get_num_interop_threads()),
            "opencv": int(cv2.getNumThreads()),
        }
        if any(value < 1 for value in observed.values()):
            raise ValueError(f"invalid effective CPU thread counts: {observed}")
    except Exception as exc:
        raise CpuThreadConfigurationError(
            f"failed to observe CPU thread counts after model loading: {exc}"
        ) from exc
    return observed


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


class PersistentVisionRunner:
    """Load both networks once at worker startup and reuse them across jobs."""

    def __init__(self, settings: ServiceSettings):
        settings.validate_license()
        self.cpu_thread_configuration = configure_cpu_thread_pools(
            settings.cpu_threads
        )
        device = resolve_device(settings.device)
        self.perception = Yolo26Perception(
            detect_model=settings.detect_model,
            pose_model=settings.pose_model,
            device=device,
            detect_imgsz=960,
            pose_imgsz=640,
            detect_confidence=0.15,
            pose_confidence=0.25,
            pose_backend_name=settings.pose_backend,
            pose_runtime=settings.pose_runtime,
            pose_profile=settings.pose_profile,
            pose_config=settings.pose_config,
            pose_native_keypoint_format=settings.pose_native_keypoint_format,
        )
        # Library imports may change global pools (Ultralytics disables OpenCV
        # parallelism). Preserve both observations; do not reconfigure inter-op
        # after parallel work has started or claim the pre-import values persist.
        self.cpu_thread_configuration_after_model_load = observe_cpu_thread_pools()
        LOGGER.info(
            "CPU thread pools observed after model loading: "
            "torch_intraop=%d, torch_interop=%d, opencv=%d",
            self.cpu_thread_configuration_after_model_load["torch_intraop"],
            self.cpu_thread_configuration_after_model_load["torch_interop"],
            self.cpu_thread_configuration_after_model_load["opencv"],
        )
        if any(
            self.cpu_thread_configuration.get(name) != value
            for name, value in self.cpu_thread_configuration_after_model_load.items()
            if name in self.cpu_thread_configuration
        ):
            LOGGER.warning(
                "Model imports changed CPU thread pools: configured=%s, observed=%s",
                self.cpu_thread_configuration,
                self.cpu_thread_configuration_after_model_load,
            )
        self.trusted_promotion_ledger_path = (
            settings.scoring_trusted_promotion_ledger
        )
        self.trusted_runtime_binding_registry_path = (
            settings.scoring_trusted_runtime_profile_bindings
        )
        self.runtime_view_evidence_dir = settings.scoring_runtime_view_evidence_dir
        self.registry_lifecycle_manifest_path = (
            settings.resolved_scoring_registry_lifecycle_manifest
        )

    def __call__(
        self,
        request,
        progress_callback=None,
        trusted_promotion_ledger_path=None,
        trusted_runtime_binding_registry_path=None,
        runtime_view_evidence_path=None,
        registry_lifecycle_manifest_path=None,
        cancellation_callback=None,
    ):
        if runtime_view_evidence_path is None and request.scoring.calibration_assets:
            if self.runtime_view_evidence_dir is not None:
                runtime_view_evidence_path = self.runtime_view_evidence_dir / (
                    f"{sha256_file(request.video_path).lower()}.json"
                )
        return run_pipeline(
            request,
            perception=self.perception,
            progress_callback=progress_callback,
            cancellation_callback=cancellation_callback,
            trusted_promotion_ledger_path=(
                trusted_promotion_ledger_path
                or self.trusted_promotion_ledger_path
            ),
            trusted_runtime_binding_registry_path=(
                trusted_runtime_binding_registry_path
                or self.trusted_runtime_binding_registry_path
            ),
            runtime_view_evidence_path=runtime_view_evidence_path,
            registry_lifecycle_manifest_path=(
                registry_lifecycle_manifest_path
                or self.registry_lifecycle_manifest_path
            ),
        )


def process_one(
    settings: ServiceSettings,
    database: JobDatabase,
    worker_id: str,
    runner: Callable = run_pipeline,
    *,
    expected_job_id: str | None = None,
) -> bool:
    settings.validate_license()
    for name, path in (
        ("detect model", settings.detect_model),
        ("pose model", settings.pose_model),
    ):
        if not path.exists():
            raise FileNotFoundError(f"{name} is missing: {path}")
    if settings.pose_config is not None and not settings.pose_config.exists():
        raise FileNotFoundError(f"pose config is missing: {settings.pose_config}")
    job = database.claim_next(
        worker_id, settings.lease_seconds, settings.max_attempts,
        expected_job_id=expected_job_id,
    )
    if job is None:
        return False
    LOGGER.info("processing job %s", job["id"])
    owner = {"worker_id": worker_id, "attempts": job["attempts"]}

    def check_cancelled():
        state = database.execution_state(job["id"])
        if (state is None or state["status"] != "running"
                or state["worker_id"] != worker_id or state["attempts"] != job["attempts"]):
            raise JobLeaseLost("job is no longer owned by this worker attempt")
        if state["cancel_requested"]:
            raise JobCancelled("job cancellation requested")

    try:
        check_cancelled()
        request = load_request(job["request_path"])
        upstream = getattr(request, "upstream_metadata", {})
        if not isinstance(upstream, dict):
            raise CpuFinalizationSafetyError("request upstream_metadata must be an object")
        cpu_only = getattr(runner, "cpu_finalization_only", False) is True
        if "finalization_recovery" in upstream or cpu_only:
            recovery = upstream.get("finalization_recovery")
            valid_marker = (
                isinstance(recovery, dict)
                and recovery.get("reused_inference") is True
                and isinstance(recovery.get("source_failed_job_id"), str)
                and bool(recovery["source_failed_job_id"].strip())
                and recovery["source_failed_job_id"] != job["id"]
                and all(isinstance(recovery.get(key), str)
                        and len(recovery[key]) == 64
                        and all(c in "0123456789abcdef" for c in recovery[key].lower())
                        for key in ("frames_sha256", "video_sha256"))
            )
            if (not valid_marker or not cpu_only or expected_job_id != job["id"]
                    or getattr(request, "job_id", None) != job["id"]):
                raise CpuFinalizationSafetyError(
                    "CPU finalization recovery cannot run inference: a valid persisted "
                    "recovery marker, an explicitly CPU-only runner, and an exact "
                    "expected_job_id are required"
                )
        progress_callback = lambda progress: database.update_progress(
            job["id"], progress, settings.lease_seconds, **owner
        )
        parameters = inspect.signature(runner).parameters
        runner_kwargs = {}
        if "progress_callback" in parameters:
            runner_kwargs["progress_callback"] = progress_callback
        if "cancellation_callback" in parameters:
            runner_kwargs["cancellation_callback"] = check_cancelled
        if "trusted_promotion_ledger_path" in parameters:
            runner_kwargs["trusted_promotion_ledger_path"] = (
                settings.scoring_trusted_promotion_ledger
            )
        if "trusted_runtime_binding_registry_path" in parameters:
            runner_kwargs["trusted_runtime_binding_registry_path"] = (
                settings.scoring_trusted_runtime_profile_bindings
            )
        if "runtime_view_evidence_path" in parameters:
            if (
                request.scoring.calibration_assets
                and settings.scoring_runtime_view_evidence_dir is not None
            ):
                runner_kwargs["runtime_view_evidence_path"] = (
                    settings.scoring_runtime_view_evidence_dir
                    / f"{sha256_file(request.video_path).lower()}.json"
                )
            else:
                runner_kwargs["runtime_view_evidence_path"] = None
        if "registry_lifecycle_manifest_path" in parameters:
            runner_kwargs["registry_lifecycle_manifest_path"] = (
                settings.resolved_scoring_registry_lifecycle_manifest
            )
        check_cancelled()
        summary = runner(request, **runner_kwargs)
        outcome = database.mark_succeeded(job["id"], summary, **owner)
        LOGGER.info("job %s finished with state %s", job["id"], outcome or "ownership_lost")
    except JobCancelled:
        database.mark_cancelled(job["id"], **owner)
        LOGGER.info("job %s cancellation acknowledged after runner exited", job["id"])
    except JobLeaseLost:
        # A stale worker must stop without changing the newer attempt's state.
        LOGGER.warning("job %s worker attempt lost ownership; stopped", job["id"])
    except Exception as exc:
        LOGGER.exception("job %s failed", job["id"])
        database.mark_failed(job["id"], f"{type(exc).__name__}: {exc}", **owner)
    return True


def run_worker(
    settings: ServiceSettings,
    database: JobDatabase,
    poll_seconds: float = 2.0,
    once: bool = False,
    stop_event: Event | None = None,
    on_state: Callable[[str, str | None], None] | None = None,
) -> None:
    settings.validate_license()
    settings.ensure_directories()
    database.initialize()
    worker_id = default_worker_id()
    if on_state is not None:
        on_state("loading_models", None)
    try:
        runner = PersistentVisionRunner(settings)
        from rallymate_service.mesh_render_worker import MeshRenderCoordinator
        from rallymate_vision.owned_subprocess import ProcessTreeCleanupError
        mesh_renderer = MeshRenderCoordinator(settings, database)
        if on_state is not None:
            on_state("ready", None)
        while stop_event is None or not stop_event.is_set():
            handled = process_one(settings, database, worker_id, runner=runner)
            if once:
                return
            if not handled:
                # Completed scoring is already durable. Optional rendering only
                # uses idle time and yields promptly to a new inference request.
                try:
                    if mesh_renderer.process_one(stop_event):
                        continue
                except ProcessTreeCleanupError:
                    # A new inference must not overlap an unconfirmed GPU tree.
                    raise
                except Exception:
                    LOGGER.exception("optional mesh scheduler failed; inference worker remains available")
                if stop_event is not None:
                    stop_event.wait(poll_seconds)
                else:
                    time.sleep(poll_seconds)
    except Exception as exc:
        if on_state is not None:
            on_state("failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if on_state is not None and stop_event is not None and stop_event.is_set():
            on_state("stopped", None)
