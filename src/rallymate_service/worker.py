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
    """Raised before model loading when a CPU thread pool cannot be bounded."""


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
    """Bound Torch and OpenCV CPU pools before either inference model loads."""

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
                requested=cpu_threads,
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
                requested=cpu_threads,
                getter=cv2.getNumThreads,
                setter=cv2.setNumThreads,
            ),
        }
    configured["requested"] = cpu_threads
    LOGGER.info(
        "CPU thread pools configured before model loading: requested=%d, "
        "torch_intraop=%d, torch_interop=%d, opencv=%d",
        configured["requested"],
        configured["torch_intraop"],
        configured["torch_interop"],
        configured["opencv"],
    )
    return configured


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
        worker_id, settings.lease_seconds, settings.max_attempts
    )
    if job is None:
        return False
    LOGGER.info("processing job %s", job["id"])
    try:
        request = load_request(job["request_path"])
        progress_callback = lambda progress: database.update_progress(
            job["id"], progress, settings.lease_seconds
        )
        parameters = inspect.signature(runner).parameters
        runner_kwargs = {}
        if "progress_callback" in parameters:
            runner_kwargs["progress_callback"] = progress_callback
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
        summary = runner(request, **runner_kwargs)
        database.mark_succeeded(job["id"], summary)
        LOGGER.info("completed job %s", job["id"])
    except Exception as exc:
        LOGGER.exception("job %s failed", job["id"])
        database.mark_failed(job["id"], f"{type(exc).__name__}: {exc}")
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
        if on_state is not None:
            on_state("ready", None)
        while stop_event is None or not stop_event.is_set():
            handled = process_one(settings, database, worker_id, runner=runner)
            if once:
                return
            if not handled:
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
