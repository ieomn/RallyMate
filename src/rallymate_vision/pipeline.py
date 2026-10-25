from __future__ import annotations

import copy
import json
import os
import platform
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import cv2

from rallymate_scoring.scoring_rules import validate_scoring_rule
from rallymate_scoring.granularity import analyze_scoring_readiness
from rallymate_scoring.registry_lifecycle import (
    ResolvedRegistryArtifact,
    resolve_runtime_feasibility_registry,
)
from rallymate_scoring.calculation_readiness import (
    build_indicator_calculation_readiness,
)
from rallymate_scoring.measurement_portfolio import (
    build_indicator_measurement_portfolio,
)
from rallymate_scoring.cycle_measurement import build_scoring_cycle_measurement
from rallymate_scoring.loop import run_minimum_scoring_loop
from rallymate_scoring.scoring_context import load_scoring_reference_context
from rallymate_scoring.report import write_analysis_report
from rallymate_scoring.scoring_loop_report import write_scoring_loop_report
from rallymate_scoring.stroke_candidates import recognize_strokes_from_artifacts
from rallymate_tracking import build_primary_player_artifacts
from rallymate_vision.contracts import FRAME_SCHEMA_VERSION, SCHEMA_VERSION, PipelineRequest
from rallymate_vision.inference import Yolo26Perception
from rallymate_vision.pose.metadata import keypoint_schema, sha256_file
from rallymate_vision.pose.diagnostics import bind_pose_association_diagnostics
from rallymate_vision.quality import (
    aggregate_quality,
    analyze_frame,
    probe_video,
)
from rallymate_vision.render import annotate_frame
from rallymate_vision.tracking import SimpleMultiClassTracker
from rallymate_vision.timebase import SourceVideoClock
from rallymate_vision.camera_motion import CameraMotionGuard
from rallymate_vision.manual_court import FixedManualCourtObservation
from rallymate_vision.source_replay import export_source_replay
from rallymate_vision.progress import postprocess_progress
from rallymate_vision.utils import relative_or_absolute, resolve_device, safe_float
from rallymate_vision.validation import (
    validate_frame_observation,
    validate_run_artifacts,
)


DEFAULT_SCORING_FEASIBILITY_REGISTRY = "metric-feasibility-pose-wave-v2.json"
DEFAULT_REGISTRY_LIFECYCLE_MANIFEST = "registry-lifecycle.json"
DEFAULT_TRUSTED_PROMOTION_LEDGER = (
    "calibration/trusted-calibration-promotion-ledger.json"
)


def _find_ffmpeg_executable() -> str | None:
    """Resolve ffmpeg from PATH or an installed imageio-ffmpeg bundle.

    The optional wheel ships its executable; this lookup never downloads it.
    """

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, OSError, RuntimeError, ValueError):
        return None
    return bundled if bundled and Path(bundled).is_file() else None


def _transcode_annotated_video_for_browser(
    video_path: Path,
    *,
    timeout_seconds: int = 180,
) -> dict[str, str | int | None]:
    """Best-effort H.264 transcode with a safe mp4v fallback.

    OpenCV's portable MP4 writer emits ``mp4v``.  That is accepted by many
    desktop browsers but is not reliably playable in Safari/iOS.  Production
    images include ffmpeg, so use a bounded, argument-list subprocess to
    replace the file atomically with H.264/yuv420p when ``ffmpeg`` is present.
    The original file remains intact for local environments without an H.264
    encoder or when transcoding fails.
    """

    fallback: dict[str, str | int | None] = {
        "status": "fallback",
        "codec": "mp4v",
        "pixel_format": None,
        "message": "浏览器兼容转码不可用，保留 OpenCV MP4 产物。",
    }
    ffmpeg = _find_ffmpeg_executable()
    if not ffmpeg:
        return fallback
    source = Path(video_path)
    if not source.is_file() or source.stat().st_size <= 0:
        return {**fallback, "message": "标注视频不存在或为空，未执行转码。"}
    temporary = source.with_name(f".{source.stem}.h264.tmp{source.suffix}")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
        )
    except (OSError, subprocess.TimeoutExpired):
        temporary.unlink(missing_ok=True)
        return fallback
    if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
        temporary.unlink(missing_ok=True)
        return fallback
    try:
        os.replace(temporary, source)
    except OSError:
        temporary.unlink(missing_ok=True)
        return fallback
    return {
        "status": "transcoded",
        "codec": "h264",
        "pixel_format": "yuv420p",
        "message": "已生成适合主流浏览器与 Safari 的 H.264 视频。",
    }


def _load_runtime_authorization_json(path: Path, label: str) -> dict:
    resolved = Path(path).resolve()
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"{label} is missing: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def load_trusted_runtime_binding_registry(path: Path) -> dict:
    # Legacy release annotations are retained if explicitly supplied; their
    # approval status has no role in evaluating a numeric scoring rule.
    return _load_runtime_authorization_json(
        path, "trusted runtime-profile binding registry"
    )


def load_runtime_view_evidence(path: Path) -> dict:
    payload = _load_runtime_authorization_json(path, "runtime view evidence")
    # Optional evidence may supply a content identity, but cannot fabricate or
    # mismatch the video identity merely because its review status says passed.
    video_id, sha = payload.get("video_id"), payload.get("video_sha256")
    if not isinstance(video_id, str) or not video_id.strip():
        raise ValueError("runtime view evidence video_id must be non-empty")
    if (not isinstance(sha, str) or len(sha) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in sha)):
        raise ValueError("runtime view evidence video_sha256 must be a SHA256 digest")
    return payload


def resolve_scoring_feasibility_registry(
    request: PipelineRequest,
    project_root: Path | None = None,
    registry_lifecycle_manifest_path: Path | None = None,
) -> Path:
    """Resolve the sole lifecycle-authorized registry used by this pipeline run."""

    return _resolve_scoring_feasibility_registry_authority(
        request,
        project_root=project_root,
        registry_lifecycle_manifest_path=registry_lifecycle_manifest_path,
    ).path


def _resolve_scoring_feasibility_registry_authority(
    request: PipelineRequest,
    *,
    project_root: Path | None = None,
    registry_lifecycle_manifest_path: Path | None = None,
) -> ResolvedRegistryArtifact:
    root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    manifest_path = (
        Path(registry_lifecycle_manifest_path).resolve()
        if registry_lifecycle_manifest_path is not None
        else (root / DEFAULT_REGISTRY_LIFECYCLE_MANIFEST).resolve()
    )
    return resolve_runtime_feasibility_registry(
        manifest_path, explicit_path=request.scoring.feasibility_registry)


def _verify_scoring_registry_authority_unchanged(
    authority: ResolvedRegistryArtifact,
) -> None:
    if (authority.manifest_path is not None
            and sha256_file(authority.manifest_path).lower() != authority.manifest_sha256):
        raise RuntimeError(
            "registry lifecycle manifest changed after production preflight"
        )
    if sha256_file(authority.path).lower() != authority.file_sha256:
        raise RuntimeError(
            "scoring feasibility registry changed after production preflight"
        )


def _verify_recovery_artifacts(output_dir: Path, hashes: dict, *, finalized: bool = False) -> None:
    mutable = {"summary.json", "scoring-readiness.json", "analysis-report.html"}
    for name, expected in hashes.items():
        relative = Path(name)
        path = (output_dir / relative).resolve()
        if relative.is_absolute() or output_dir.resolve() not in path.parents:
            raise ValueError("recovery artifact must remain inside the recovery output directory")
        if (not isinstance(expected, str) or len(expected) != 64
                or any(character not in "0123456789abcdef" for character in expected.lower())):
            raise ValueError(f"invalid recovery artifact SHA-256: {name}")
        if finalized and relative.as_posix() in mutable:
            continue
        if not path.is_file() or sha256_file(path).lower() != expected.lower():
            raise ValueError(f"recovery artifact SHA-256 mismatch: {name}")


def finalize_pipeline_artifacts(
    request: PipelineRequest,
    summary: dict,
    *,
    registry_lifecycle_manifest_path: Path | None = None,
    registry_authority: ResolvedRegistryArtifact | None = None,
    report_postprocessing: Callable[[str], None] | None = None,
    cancellation_callback: Callable[[], None] | None = None,
    started_at: float | None = None,
    timings: dict[str, float] | None = None,
    recovery_metadata: dict | None = None,
) -> dict:
    """Finish already-written inference/scoring artifacts without loading a model.

    Normal inference supplies its live clock and stage timings. CPU recovery
    supplies the original provisional summary and a separately copied output
    directory, plus source_failed_job_id, source_output_dir and
    source_artifact_sha256 in recovery_metadata. Only the new summary/report
    identity changes; immutable frame/event/feature provenance remains intact.
    The caller owns the job lease, directory clone and database transition.
    """
    def check_cancelled() -> None:
        if cancellation_callback is not None:
            cancellation_callback()

    check_cancelled()
    cpu_started_at = time.perf_counter()
    recovering = recovery_metadata is not None
    if recovering:
        if started_at is not None or timings is not None:
            raise ValueError("CPU recovery must not replace the source inference clock")
        summary = copy.deepcopy(summary)
        recovery = copy.deepcopy(recovery_metadata)
        source_id = recovery.get("source_failed_job_id")
        if (not isinstance(source_id, str) or not source_id
                or source_id != summary.get("job_id") or source_id == request.job_id):
            raise ValueError("CPU recovery requires distinct recovery and original failed job IDs")
        source_output = recovery.get("source_output_dir")
        if not isinstance(source_output, str) or not source_output:
            raise ValueError("CPU recovery requires source_output_dir")
        source_dir = Path(source_output).resolve()
        output_dir = request.output_dir.resolve()
        if output_dir == source_dir or source_dir in output_dir.parents:
            raise ValueError("CPU recovery must not write inside the original failed output directory")
        hashes = recovery.get("source_artifact_sha256")
        if not isinstance(hashes, dict) or not {
            "frames.jsonl", "summary.json", "scoring-loop-summary.json"
        }.issubset(hashes):
            raise ValueError("CPU recovery requires hashes of frames, summary and scoring-loop summary")
        _verify_recovery_artifacts(output_dir, hashes)
        # A copied directory must not contain linked writable finalization files.
        for name in ("summary.json", "scoring-readiness.json", "analysis-report.html"):
            destination = output_dir / name
            original = source_dir / name
            if destination.is_symlink() or (destination.exists() and original.exists()
                                           and destination.samefile(original)):
                raise ValueError(f"CPU recovery output aliases the original artifact: {name}")
        saved_summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
        if summary != saved_summary:
            raise ValueError("CPU recovery summary differs from the verified source snapshot")
        summary["job_id"] = request.job_id
        summary["reused_inference"] = True
        summary["source_failed_job_id"] = source_id
        for key, value in summary.get("artifacts", {}).items():
            if isinstance(value, str) and Path(value).is_absolute():
                try:
                    relative = Path(value).resolve().relative_to(source_dir)
                except ValueError:
                    continue
                summary["artifacts"][key] = relative.as_posix()
        recovery["mode"] = "cpu_finalization"
        recovery["status"] = "running"
        summary["recovery"] = recovery
        stage_timings: dict[str, float] = Counter()
    else:
        if started_at is None or timings is None:
            raise ValueError("normal finalization requires the original pipeline clock and timings")
        if summary.get("job_id") != request.job_id:
            raise ValueError("pipeline summary job_id does not match its request")
        stage_timings = timings

    authority = registry_authority or _resolve_scoring_feasibility_registry_authority(
        request, registry_lifecycle_manifest_path=registry_lifecycle_manifest_path,
    )
    _verify_scoring_registry_authority_unchanged(authority)
    if recovering:
        loop = json.loads((request.output_dir / "scoring-loop-summary.json").read_text(encoding="utf-8"))
        expected_registry = {
            "authority_version": authority.authority_version,
            "role": authority.role,
            "kind": authority.kind,
            "lifecycle": authority.lifecycle,
            "embedded_version": authority.embedded_version,
            "manifest_sha256": authority.manifest_sha256,
            "artifact_sha256": authority.file_sha256,
        }
        for source_loop in (loop, summary.get("minimum_scoring_loop", {})):
            provenance = source_loop.get("provenance", {})
            lifecycle = provenance.get("registry_lifecycle", {})
            if (provenance.get("pipeline_job_id") != source_id
                    or provenance.get("feasibility_registry_sha256") != authority.file_sha256
                    or any(lifecycle.get(key) != value for key, value in expected_registry.items())):
                raise ValueError("CPU recovery registry/source identity differs from production provenance")

    frames_path = request.output_dir / "frames.jsonl"
    summary_path = request.output_dir / "summary.json"
    processed = summary["processing"]["processed_frames"]

    def update_timing() -> None:
        if recovering:
            summary["recovery"].update(
                cpu_finalization_elapsed_seconds=safe_float(time.perf_counter() - cpu_started_at, 3),
                cpu_stage_seconds={key: safe_float(value, 3) for key, value in stage_timings.items()},
            )
        else:
            elapsed = time.perf_counter() - started_at
            summary["processing"].update(
                elapsed_seconds=safe_float(elapsed, 3),
                effective_processed_fps=safe_float(processed / max(elapsed, 1e-6), 3),
                stage_seconds={key: safe_float(value, 3) for key, value in stage_timings.items()},
            )

    check_cancelled()
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    stage_started = time.perf_counter()
    summary["validation"] = validate_run_artifacts(request.output_dir)
    stage_timings["artifact_validation_seconds"] = stage_timings.get("artifact_validation_seconds", 0) + time.perf_counter() - stage_started
    check_cancelled()
    if report_postprocessing is not None:
        report_postprocessing("scoring_readiness")
    stage_started = time.perf_counter()
    scoring_readiness_path = request.output_dir / "scoring-readiness.json"
    scoring_readiness = analyze_scoring_readiness(summary, frames_path)
    scoring_readiness_path.write_text(
        json.dumps(scoring_readiness, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    summary["scoring_readiness"] = scoring_readiness["summary"]
    summary["pose_assessment"] = scoring_readiness["pose_assessment"]
    summary["artifacts"]["scoring_readiness_json"] = relative_or_absolute(scoring_readiness_path, request.output_dir)
    stage_timings["report_validation_seconds"] = stage_timings.get("report_validation_seconds", 0) + time.perf_counter() - stage_started
    update_timing()
    check_cancelled()
    stage_started = time.perf_counter()
    analysis_report_path = request.output_dir / "analysis-report.html"
    write_analysis_report(summary, scoring_readiness, analysis_report_path)
    summary["artifacts"]["analysis_report_html"] = relative_or_absolute(analysis_report_path, request.output_dir)
    _verify_scoring_registry_authority_unchanged(authority)
    stage_timings["report_export_seconds"] = stage_timings.get("report_export_seconds", 0) + time.perf_counter() - stage_started
    update_timing()
    check_cancelled()
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if recovering:
        # Validate the completed summary, not just the provisional one. The
        # existing run validator does not inspect the two newly written reports.
        stage_started = time.perf_counter()
        summary["validation"] = validate_run_artifacts(request.output_dir)
        if (json.loads(scoring_readiness_path.read_text(encoding="utf-8")) != scoring_readiness
                or not analysis_report_path.is_file() or analysis_report_path.stat().st_size == 0):
            raise ValueError("CPU finalization report export is incomplete")
        _verify_recovery_artifacts(request.output_dir, hashes, finalized=True)
        _verify_scoring_registry_authority_unchanged(authority)
        stage_timings["final_artifact_validation_seconds"] = time.perf_counter() - stage_started
        check_cancelled()
        summary["recovery"]["status"] = "completed"
        update_timing()
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def load_pipeline_calibrations(
    paths: list[Path] | tuple[Path, ...],
    *,
    trusted_ledger_path: Path | None = None,
    feasibility_registry: dict | None = None,
) -> tuple[dict[str, object], list[dict]]:
    """Load explicit scoring mathematics and retain its exact source identity.

    Release ledgers and feasibility maturity are optional audit metadata. They
    cannot turn a missing threshold into a score or veto otherwise valid math.
    Legacy keyword arguments remain accepted for callers with saved settings.
    """

    calibrations: dict[str, object] = {}
    provenance: list[dict] = []
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"scoring calibration asset is missing: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid scoring calibration JSON {path}: {exc}") from exc
        validate_scoring_rule(payload)
        backend = payload["backend"]
        indicator_id = str(payload["indicator_id"])
        if indicator_id in calibrations:
            raise ValueError(f"duplicate scoring calibration for {indicator_id}")
        calibrations[indicator_id] = payload
        independent_test = payload.get("independent_test")
        provenance.append(
            {
                "indicator_id": indicator_id,
                "backend": str(backend),
                "version": payload.get("threshold_version")
                or payload.get("model_version"),
                "path": str(path),
                "sha256": sha256_file(path),
                "artifact_scope": payload.get("artifact_scope"),
                "ground_truth_dataset_version": payload.get("ground_truth_dataset_version"),
                "independent_test_status": independent_test.get("status") if isinstance(independent_test, dict) else None,
                "validation_scope": "explicit_rule_mathematics_only_not_accuracy_or_release_approval",
            }
        )
    return calibrations, provenance


def run_pipeline(
    request: PipelineRequest,
    perception: Yolo26Perception | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    trusted_promotion_ledger_path: Path | None = None,
    trusted_runtime_binding_registry_path: Path | None = None,
    runtime_view_evidence_path: Path | None = None,
    registry_lifecycle_manifest_path: Path | None = None,
    cancellation_callback: Callable[[], None] | None = None,
) -> dict:
    """Run with deterministic cleanup; cancellation callback exceptions propagate."""
    with ExitStack() as cleanup:
        return _run_pipeline(
            request, perception, progress_callback, trusted_promotion_ledger_path,
            trusted_runtime_binding_registry_path, runtime_view_evidence_path,
            registry_lifecycle_manifest_path, cancellation_callback, cleanup,
        )


def _run_pipeline(
    request: PipelineRequest,
    perception: Yolo26Perception | None,
    progress_callback: Callable[[dict], None] | None,
    trusted_promotion_ledger_path: Path | None,
    trusted_runtime_binding_registry_path: Path | None,
    runtime_view_evidence_path: Path | None,
    registry_lifecycle_manifest_path: Path | None,
    cancellation_callback: Callable[[], None] | None,
    cleanup: ExitStack,
) -> dict:
    def check_cancelled() -> None:
        if cancellation_callback is not None:
            cancellation_callback()

    def emit_progress(payload: dict) -> None:
        check_cancelled()
        if progress_callback is None:
            return
        try:
            progress_callback(payload)
        except Exception:
            # Progress reporting is operational metadata and must not abort a
            # valid GPU inference run.
            return

    started_at = time.perf_counter()
    check_cancelled()
    if not request.video_path.exists():
        raise FileNotFoundError(f"input video is missing: {request.video_path}")
    input_video_sha256 = sha256_file(request.video_path)
    registry_authority = _resolve_scoring_feasibility_registry_authority(
        request,
        registry_lifecycle_manifest_path=registry_lifecycle_manifest_path,
    )
    feasibility_registry_path = registry_authority.path
    # The exact current registry is a production authorization input, not a
    # feature-record assertion. Load and validate it before creating a run
    # directory, loading GPU models, or writing inference artifacts.
    feasibility_registry = registry_authority.payload
    # Fail before expensive GPU work if explicit scoring mathematics are
    # malformed or duplicated. Validation also
    # precedes output-directory creation so a rejected request leaves no run shell.
    calibrations, calibration_provenance = load_pipeline_calibrations(
        request.scoring.calibration_assets,
        trusted_ledger_path=trusted_promotion_ledger_path,
        feasibility_registry=feasibility_registry,
    )
    runtime_binding_registry: dict | None = None
    runtime_view_evidence: dict | None = None
    scoring_video_id = request.job_id
    runtime_authorization_files: dict[str, dict[str, str]] = {}
    scoring_reference_context_path = request.scoring.reference_context
    scoring_reference_context_sha256: str | None = None
    scoring_reference_video_id: str | None = None
    if scoring_reference_context_path is not None:
        scoring_reference_context_path = scoring_reference_context_path.resolve()
        if not scoring_reference_context_path.is_file():
            raise FileNotFoundError(
                "scoring reference context is missing: "
                f"{scoring_reference_context_path}"
            )
        scoring_reference_context_sha256 = sha256_file(
            scoring_reference_context_path
        )
        try:
            reference_payload = json.loads(
                scoring_reference_context_path.read_text(encoding="utf-8")
            )
            scoring_reference_video_id = reference_payload["video"]["video_id"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("scoring reference context video binding is invalid") from exc
        load_scoring_reference_context(
            scoring_reference_context_path,
            expected_video_id=scoring_reference_video_id,
            expected_video_sha256=input_video_sha256,
        )
    if trusted_runtime_binding_registry_path is not None:
        resolved_binding_path = Path(
            trusted_runtime_binding_registry_path
        ).resolve()
        runtime_binding_registry = load_trusted_runtime_binding_registry(
            resolved_binding_path
        )
        runtime_authorization_files["trusted_runtime_binding_registry"] = {
            "path": str(resolved_binding_path), "sha256": sha256_file(resolved_binding_path),
        }
    if runtime_view_evidence_path is not None:
        resolved_view_path = Path(runtime_view_evidence_path).resolve()
        runtime_view_evidence = load_runtime_view_evidence(resolved_view_path)
        if runtime_view_evidence["video_sha256"].lower() != input_video_sha256.lower():
            raise ValueError(
                "runtime view evidence video_sha256 does not match the input video"
            )
        # The accepted evidence owns the stable content/video identity.  The
        # request job_id remains execution provenance and may legitimately
        # change when the same reviewed video is reprocessed.
        scoring_video_id = runtime_view_evidence["video_id"]
        runtime_authorization_files["runtime_view_evidence"] = {
                "path": str(resolved_view_path),
                "sha256": sha256_file(resolved_view_path),
        }
    if scoring_reference_video_id is not None:
        if runtime_view_evidence is not None and scoring_video_id != scoring_reference_video_id:
            raise ValueError(
                "scoring reference context video_id does not match trusted runtime "
                "view evidence"
            )
        scoring_video_id = scoring_reference_video_id
    request.output_dir.mkdir(parents=True, exist_ok=True)

    metadata = probe_video(request.video_path)
    device = resolve_device(request.models.device)
    if perception is None:
        emit_progress(
            {
                "phase": "loading_models",
                "percent": 1,
                "processed_frames": 0,
                "total_frames": metadata.frame_count,
                "message": "正在加载检测与姿态模型",
            }
        )
        perception = Yolo26Perception(
            detect_model=request.models.detect,
            pose_model=request.models.pose,
            device=device,
            detect_imgsz=request.models.detect_imgsz,
            pose_imgsz=request.models.pose_imgsz,
            detect_confidence=request.models.detect_confidence,
            pose_confidence=request.models.pose_confidence,
            pose_backend_name=request.models.pose_backend,
            pose_runtime=request.models.pose_runtime,
            pose_profile=request.models.pose_profile,
            pose_config=request.models.pose_config,
            pose_native_keypoint_format=request.models.pose_native_keypoint_format,
        )
        if callable(getattr(perception, "close", None)):
            cleanup.callback(perception.close)
    else:
        loaded_pose = perception.pose_metadata()
        expected = (
            request.models.detect.resolve(),
            request.models.pose.resolve(),
            request.models.pose_backend,
            request.models.pose_runtime,
            request.models.pose_profile,
            (
                request.models.pose_native_keypoint_format
                or loaded_pose["native_keypoint_format"]
            ),
        )
        actual = (
            perception.detect_model_path.resolve(),
            perception.pose_model_path.resolve(),
            loaded_pose["backend"],
            loaded_pose["runtime"],
            loaded_pose["profile"],
            loaded_pose["native_keypoint_format"],
        )
        if actual != expected:
            raise ValueError(
                "preloaded model paths do not match the request: "
                f"expected={expected}, actual={actual}"
            )
    tracker = SimpleMultiClassTracker()
    frames_path = request.output_dir / "frames.jsonl"
    summary_path = request.output_dir / "summary.json"
    video_path = request.output_dir / "annotated.mp4"
    preview_path = request.output_dir / "preview.jpg"

    sam_enabled = perception.pose_metadata()["backend"] == "sam3d"
    sam_records: list[dict] = []
    sam_frames_path = request.output_dir / "sam3d_frames.jsonl"
    sam_evaluation_path = request.output_dir / "sam3d_evaluation.json"
    sam_file = None
    mesh_ledger = None
    mesh_archiver = None
    sam3d_mesh = None
    if sam_enabled:
        from rallymate_vision.sam3d_runner import PrefetchSourcePTSDecoder, SourcePTSDecoder
        from rallymate_vision.sam3d_mesh import LEDGER_NAME, archive_mesh_records
        sam_runtime_dir = Path(os.environ.get(
            "RALLYMATE_SAM3D_RUNTIME", Path(__file__).resolve().parents[2] / "runtime" / "sam3d",
        ))
        mesh_ledger = cleanup.enter_context(
            (request.output_dir / LEDGER_NAME).open("w", encoding="utf-8", newline="\n")
        )
        prefetch_depth = int(os.environ.get("RALLYMATE_SAM3D_PREFETCH_FRAMES", "0"))
        if not 0 <= prefetch_depth <= 8:
            raise ValueError("RALLYMATE_SAM3D_PREFETCH_FRAMES must be between 0 and 8")
        capture = (PrefetchSourcePTSDecoder(request.video_path, depth=prefetch_depth)
                   if prefetch_depth else SourcePTSDecoder(request.video_path))
        cleanup.callback(capture.release)
        backend = getattr(perception, "pose_backend", None)
        if callable(getattr(backend, "reset_sequence", None)):
            backend.reset_sequence()
        sam_file = cleanup.enter_context(sam_frames_path.open("w", encoding="utf-8", newline="\n"))
        archive_mode = os.environ.get("RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE", "0")
        if archive_mode not in {"0", "1"}:
            raise ValueError("RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE must be 0 or 1")
        if archive_mode == "1":
            from rallymate_vision.sam3d_mesh_archive import BoundedMeshArchiver
            mesh_archiver = BoundedMeshArchiver(request.output_dir, sam_runtime_dir,
                                               max_pending_frames=2)
            # Registered after both output streams: join pending disk work
            # before either stream can close, including failure/cancellation.
            cleanup.callback(mesh_archiver.close)
    else:
        capture = cv2.VideoCapture(str(request.video_path))
        cleanup.callback(capture.release)
    start_frame = int(request.processing.start_ms / 1000.0 * metadata.fps)
    end_frame = (
        int(request.processing.end_ms / 1000.0 * metadata.fps)
        if request.processing.end_ms is not None
        else metadata.frame_count
    )
    end_frame = min(end_frame, metadata.frame_count)
    planned_frames = max(
        1,
        (max(end_frame - start_frame, 0) + request.processing.frame_stride - 1)
        // request.processing.frame_stride,
    )
    if request.processing.max_frames is not None:
        planned_frames = min(planned_frames, request.processing.max_frames)
    progress_interval = max(1, min(50, planned_frames // 100 or 1))
    emit_progress(
        {
            "phase": "inference",
            "percent": 3,
            "processed_frames": 0,
            "total_frames": planned_frames,
            "message": "模型已就绪，开始逐帧推理",
        }
    )
    # Decode from the beginning so clip boundaries use presentation time even
    # for variable-frame-rate files. Frame-index/FPS seeking is only an estimate.
    clock = SourceVideoClock(metadata.fps)
    camera_guard = CameraMotionGuard()
    manual_court = FixedManualCourtObservation(request.court)
    first_processed_source_frame = None
    last_processed_timestamp_ms = None
    last_processed_source_frame = None
    camera_status_counts: Counter[str] = Counter()
    camera_compensated_frames = 0

    output_fps = metadata.fps / request.processing.frame_stride
    writer = None
    if request.processing.write_annotated_video:
        writer = cv2.VideoWriter(
            str(video_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            max(output_fps, 1.0),
            (metadata.width, metadata.height),
        )
        cleanup.callback(writer.release)
        if not writer.isOpened():
            capture.release()
            raise RuntimeError("could not initialize annotated MP4 writer")

    processed = 0
    source_frame_index = 0
    class_counts: Counter[str] = Counter()
    frames_with_class: Counter[str] = Counter()
    pose_frame_count = 0
    person_frame_count = 0
    quality_samples: list[dict] = []
    timings = Counter()
    court_status_counts: Counter[str] = Counter()

    def publish_sam_records(records, mesh_records):
        # Only this control thread publishes verified native rows, in source
        # order. The archive worker owns copies and never writes these streams.
        for mesh_record in mesh_records:
            mesh_ledger.write(json.dumps(mesh_record, ensure_ascii=False, allow_nan=False) + "\n")
        sam_records.extend(records)
        for sam_record in records:
            sam_file.write(json.dumps(sam_record, ensure_ascii=False, allow_nan=False) + "\n")
        sam_file.flush()

    def publish_archived_batches(batches):
        for batch in batches:
            publish_sam_records(batch.records, batch.mesh_records)

    with frames_path.open("w", encoding="utf-8", newline="\n") as frames_file:
        while source_frame_index < metadata.frame_count:
            check_cancelled()
            stage_started = time.perf_counter()
            ok, frame = capture.read()
            timings["decode_seconds"] += time.perf_counter() - stage_started
            check_cancelled()
            if not ok:
                break
            timestamp_ms, timestamp_source = clock.resolve(
                source_frame_index, float(capture.get(cv2.CAP_PROP_POS_MSEC))
            )
            if request.processing.end_ms is not None and timestamp_ms >= request.processing.end_ms:
                break
            if timestamp_ms < request.processing.start_ms:
                source_frame_index += 1
                continue
            if first_processed_source_frame is None:
                first_processed_source_frame = source_frame_index
            if (source_frame_index - first_processed_source_frame) % request.processing.frame_stride != 0:
                source_frame_index += 1
                continue
            last_processed_timestamp_ms = timestamp_ms
            last_processed_source_frame = source_frame_index

            stage_started = time.perf_counter()
            quality = analyze_frame(frame)
            quality_samples.append(quality)
            timings["frame_quality_seconds"] += time.perf_counter() - stage_started

            stage_started = time.perf_counter()
            detections = perception.detect(frame)
            timings["detection_seconds"] += time.perf_counter() - stage_started
            detection_timing = getattr(perception, "last_detection_timing", None)
            if isinstance(detection_timing, dict):
                for key, value in detection_timing.items():
                    timings[f"detection_component_{key.removesuffix('_ms')}_seconds"] += value / 1000
            stage_started = time.perf_counter()
            detections = tracker.update(
                detections, metadata.width, metadata.height, timestamp_ms=timestamp_ms
            )
            timings["tracking_seconds"] += time.perf_counter() - stage_started

            players = [
                detection
                for detection in detections
                if detection["class_name"] == "player"
            ]
            if players:
                person_frame_count += 1
            stage_started = time.perf_counter()
            camera_motion = camera_guard.update(
                frame, [player["bbox_px"] for player in players], timestamp_ms
            )
            timings["camera_motion_seconds"] += time.perf_counter() - stage_started
            if timestamp_source != "decoder_pts":
                camera_motion.update(status="unavailable", fixed_camera_supported=False,
                                     compensation_valid=False, matrix_to_reference=None,
                                     reason="source_timestamp_unverified")
                camera_guard.reset_reference()
            camera_status_counts[camera_motion["status"]] += 1
            camera_compensated_frames += int(camera_motion["status"] == "moving"
                                             and camera_motion.get("compensation_valid") is True)
            stage_started = time.perf_counter()
            check_cancelled()
            if sam_enabled:
                source_timestamp_ms = (round(capture.binding["timestamp_ms"])
                                       if capture.binding.get("timestamp_ms") is not None else None)
                source_binding = {
                    **capture.binding, "video_sha256": input_video_sha256,
                    "timestamp_ms": source_timestamp_ms,
                    "frame_index": source_frame_index, "processed_index": processed,
                    "camera_reference_epoch": camera_motion["reference_epoch"],
                    "relative_timestamp_ms": timestamp_ms,
                    "timestamp_source": timestamp_source,
                }
                poses = perception.estimate_poses(
                    frame, players, request.processing.max_players, source_binding=source_binding,
                )
                frame_sam_records = perception.pose_frame_records()
                mesh_started = time.perf_counter()
                if mesh_archiver is not None:
                    publish_archived_batches(mesh_archiver.submit(frame_sam_records))
                    timings["mesh_archive_submit_collect_seconds"] += time.perf_counter() - mesh_started
                else:
                    mesh_records = archive_mesh_records(frame_sam_records, request.output_dir, sam_runtime_dir)
                    timings["mesh_capture_archive_seconds"] += time.perf_counter() - mesh_started
                    publish_sam_records(frame_sam_records, mesh_records)
            else:
                poses = perception.estimate_poses(
                    frame, players, request.processing.max_players
                )
            check_cancelled()
            timings["pose_seconds"] += time.perf_counter() - stage_started
            if poses:
                pose_frame_count += 1

            # Only an explicit manual outer-corner polygon can produce a
            # display projection. Camera changes permanently invalidate it;
            # default/automatic localization and court scoring stay disabled.
            stage_started = time.perf_counter()
            court = manual_court.observe(frame, camera_motion)
            court_status_counts[court["status"]] += 1
            timings["court_projection_seconds"] += time.perf_counter() - stage_started

            seen_this_frame: set[str] = set()
            for detection in detections:
                class_counts[detection["class_name"]] += 1
                seen_this_frame.add(detection["class_name"])
            for class_name in seen_this_frame:
                frames_with_class[class_name] += 1

            record = {
                "schema_version": FRAME_SCHEMA_VERSION,
                "job_id": request.job_id,
                "frame": {
                    "index": source_frame_index,
                    "processed_index": processed,
                    "timestamp_ms": timestamp_ms,
                    "timestamp_source": timestamp_source,
                    "width": metadata.width,
                    "height": metadata.height,
                    "coordinate_origin": "top_left",
                },
                "quality": quality,
                "camera_motion": camera_motion,
                "detections": detections,
                "poses": poses,
                "court": court,
                "handoff": {
                    "pose_player_link": "poses[].person_track_id -> detections[].track_id",
                    "coordinate_space": "original_frame_pixels_and_normalized_0_1",
                    "timebase": "source_video_timestamp_ms",
                    "downstream_taxonomy": {
                        "pose": "J joint system",
                        "ball": "BALL + S02",
                        "racket": "RK + S03",
                        "court": "S05",
                    },
                },
            }
            if sam_enabled:
                record["frame"].update({**capture.binding, "timestamp_ms": timestamp_ms,
                                        "source_timestamp_ms": source_timestamp_ms,
                                        "video_sha256": input_video_sha256})
                record["sam3d_requests"] = [
                    {key: row.get(key) for key in ("person_track_id", "selection_epoch", "status", "reason",
                                                  "inference_ms", "peak_memory_bytes")}
                    for row in frame_sam_records
                ]
            stage_started = time.perf_counter()
            pose_diagnostics = bind_pose_association_diagnostics(
                getattr(perception, "last_pose_association_diagnostics", None),
                record["frame"],
            )
            if pose_diagnostics is not None:
                record["pose_association"] = pose_diagnostics
            validate_frame_observation(record)
            frames_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            timings["frame_validation_serialization_seconds"] += time.perf_counter() - stage_started

            if writer is not None:
                stage_started = time.perf_counter()
                annotated = annotate_frame(
                    frame,
                    detections,
                    poses,
                    court,
                    source_frame_index,
                    timestamp_ms,
                    quality,
                )
                writer.write(annotated)
                if processed == 0:
                    cv2.imwrite(str(preview_path), annotated)
                timings["replay_frame_write_seconds"] += time.perf_counter() - stage_started

            processed += 1
            source_frame_index += 1
            if processed == 1 or processed % progress_interval == 0:
                stage_started = time.perf_counter()
                # Let live trajectory readers see complete rows before the UI
                # receives progress for those frames.
                frames_file.flush()
                elapsed_now = max(time.perf_counter() - started_at, 1e-6)
                emit_progress(
                    {
                        "phase": "inference",
                        "percent": safe_float(
                            min(85.0, 3.0 + processed / planned_frames * 82.0),
                            1,
                        ),
                        "processed_frames": processed,
                        "total_frames": planned_frames,
                        "effective_fps": safe_float(processed / elapsed_now, 2),
                        "message": f"正在推理第 {processed}/{planned_frames} 帧",
                    }
                )
                timings["progress_callback_seconds"] += time.perf_counter() - stage_started
            if (
                request.processing.max_frames is not None
                and processed >= request.processing.max_frames
            ):
                break

    capture.release()
    decoder_timing = getattr(capture, "timing_seconds", None)
    if isinstance(decoder_timing, dict):
        for key, value in decoder_timing.items():
            timings[f"decode_component_{key}"] += value
    sam_evaluation = None
    if sam_enabled:
        check_cancelled()
        if mesh_archiver is not None:
            stage_started = time.perf_counter()
            publish_archived_batches(mesh_archiver.finish())
            mesh_ledger.flush()
            timings["mesh_archive_drain_seconds"] += time.perf_counter() - stage_started
            check_cancelled()
        from rallymate_vision.sam3d_evaluation import evaluate_sam3d_records
        sam_file.flush()
        sam_evaluation = evaluate_sam3d_records(
            sam_records, max_frame_gap=request.processing.frame_stride,
            max_gap_ms=max(160, 1500 * request.processing.frame_stride / metadata.fps),
        )
        sam_evaluation["model"] = perception.pose_metadata()
        sam_evaluation["runtime"].update(
            warmup_or_model_load_included=any(row.get("first_model_call") for row in sam_records),
            model_load_included=False,
            first_call_may_include_cuda_warmup=any(row.get("first_model_call") for row in sam_records),
            memory_scope="current_process_cuda_allocated_absolute_peak_includes_other_loaded_models",
        )
        sam_evaluation_path.write_text(json.dumps(sam_evaluation, ensure_ascii=False, indent=2, allow_nan=False),
                                       encoding="utf-8")
    if processed == 0:
        if writer is not None:
            writer.release()
        raise RuntimeError("no frames were processed; check start/end/stride settings")

    def report_postprocessing(phase: str, **counts) -> None:
        check_cancelled()
        emit_progress(postprocess_progress(
            phase, processed_frames=processed, total_frames=planned_frames, **counts,
        ))

    def report_scoring_progress(payload: dict) -> None:
        report_postprocessing(
            payload["phase"], completed=payload.get("completed"), total=payload.get("total"),
        )

    # Publish the last decoded frame and a new stage before lengthy CPU work.
    # Previously every postprocessing step remained labelled 95% inference.
    report_postprocessing("preparing_replay")
    stage_started = time.perf_counter()
    annotated_video_compatibility: dict[str, str | int | None] = {
        "status": "disabled",
        "codec": None,
        "pixel_format": None,
        "message": "未请求生成标注视频。",
    }
    if writer is not None:
        writer.release()
        annotated_video_compatibility = export_source_replay(
            request.video_path, video_path, ffmpeg=_find_ffmpeg_executable(),
            first_frame=first_processed_source_frame or 0,
            last_frame=last_processed_source_frame or 0,
            stride=request.processing.frame_stride,
        )
        if not annotated_video_compatibility["timing_preserved"]:
            annotated_video_compatibility = {
                **_transcode_annotated_video_for_browser(video_path),
                "timing_preserved": False,
                "timing_warning": "constant_fps_fallback_replay_may_not_match_source_timing",
            }
    timings["replay_export_seconds"] += time.perf_counter() - stage_started
    if sam_enabled and any(row.get("mesh_artifact") for row in sam_records):
        from rallymate_vision.mesh_render_task import create_mesh_render_task
        mesh_ledger.flush()
        check_cancelled()
        stage_started = time.perf_counter()
        try:
            sam3d_mesh = create_mesh_render_task(
                job_id=request.job_id, source=request.video_path,
                output_dir=request.output_dir, runtime_dir=sam_runtime_dir,
                first_frame=first_processed_source_frame or 0,
                last_frame=last_processed_source_frame or 0,
                stride=request.processing.frame_stride,
                source_sha256=input_video_sha256, model=perception.pose_metadata(),
            )
        except Exception as exc:
            check_cancelled()
            sam3d_mesh = {"status": "failed", "reason": "official_mesh_request_failed",
                          "error_type": type(exc).__name__, "geometry_retained": True}
        timings["official_mesh_request_seconds"] += time.perf_counter() - stage_started

    primary_timeline_path = request.output_dir / "primary-player.jsonl"
    primary_summary_path = request.output_dir / "primary-player-summary.json"
    report_postprocessing("tracking_player")
    stage_started = time.perf_counter()
    primary_player = build_primary_player_artifacts(
        frames_path,
        primary_timeline_path,
        primary_summary_path,
    )
    timings["primary_player_seconds"] += time.perf_counter() - stage_started

    report_postprocessing("recognizing_strokes")
    stage_started = time.perf_counter()
    # Keep reviewable swing/serve candidates separate from the FS scoring loop
    # and from confirmed ball-racket contacts. This also supports API backfills.
    action_recognition = recognize_strokes_from_artifacts(frames_path, primary_timeline_path)
    timings["action_recognition_seconds"] += time.perf_counter() - stage_started

    report_postprocessing("detecting_events")
    stage_started = time.perf_counter()
    # Fail closed if an operator replaces the registry while inference is in
    # progress. The preflight snapshot remains the authorization source.
    _verify_scoring_registry_authority_unchanged(registry_authority)
    for label, evidence in runtime_authorization_files.items():
        if sha256_file(Path(evidence["path"])) != evidence["sha256"]:
            raise RuntimeError(f"{label} changed after preflight")
    if calibrations:
        if sha256_file(request.video_path).lower() != input_video_sha256.lower():
            raise RuntimeError("input video changed after production preflight")
    if scoring_reference_context_path is not None:
        assert scoring_reference_context_sha256 is not None
        if sha256_file(scoring_reference_context_path) != (
            scoring_reference_context_sha256
        ):
            raise RuntimeError(
                "scoring reference context changed after inference preflight"
            )
    scoring_loop = run_minimum_scoring_loop(
        frames_path=frames_path,
        primary_timeline_path=primary_timeline_path,
        output_dir=request.output_dir,
        feasibility_registry_path=feasibility_registry_path,
        feasibility_registry=feasibility_registry,
        source_id=request.job_id,
        video_id=scoring_video_id,
        pose_model=perception.pose_metadata(),
        source_provenance={
            "video_path": str(request.video_path),
            "video_sha256": input_video_sha256,
            "pipeline_job_id": request.job_id,
            "frames_path": str(frames_path),
            "frames_sha256": sha256_file(frames_path),
            "feasibility_registry_path": str(feasibility_registry_path),
            "feasibility_registry_sha256": registry_authority.file_sha256,
            "registry_lifecycle": {
                "authority_version": registry_authority.authority_version,
                "role": registry_authority.role,
                "kind": registry_authority.kind,
                "lifecycle": registry_authority.lifecycle,
                "embedded_version": registry_authority.embedded_version,
                "manifest_path": str(registry_authority.manifest_path) if registry_authority.manifest_path is not None else None,
                "manifest_sha256": registry_authority.manifest_sha256,
                "artifact_path": str(registry_authority.path),
                "artifact_sha256": registry_authority.file_sha256,
            },
            "calibration_assets": calibration_provenance,
            "runtime_authorization_files": runtime_authorization_files or None,
        },
        calibrations=calibrations,
        runtime_binding_registry=runtime_binding_registry,
        runtime_view_evidence=runtime_view_evidence,
        scoring_reference_context_path=scoring_reference_context_path,
        progress_callback=report_scoring_progress,
    )
    report_postprocessing("finalizing")
    _verify_scoring_registry_authority_unchanged(registry_authority)
    derived_scoring_sources = {
        "feasibility_registry": {
            "path": str(feasibility_registry_path),
            "sha256": registry_authority.file_sha256.upper(),
        },
        "events": {
            "path": str(request.output_dir / "events.jsonl"),
            "sha256": sha256_file(request.output_dir / "events.jsonl").upper(),
        },
        "indicator_features": {
            "path": str(request.output_dir / "indicator-features.jsonl"),
            "sha256": sha256_file(
                request.output_dir / "indicator-features.jsonl"
            ).upper(),
        },
        "scores": {
            "path": str(request.output_dir / "scores.jsonl"),
            "sha256": sha256_file(request.output_dir / "scores.jsonl").upper(),
        },
    }
    calculation_readiness_path = request.output_dir / "calculation-readiness.json"
    calculation_readiness = build_indicator_calculation_readiness(
        registry=scoring_loop["feasibility"],
        events=scoring_loop["events"],
        indicator_records=scoring_loop["indicator_records"],
        scores=scoring_loop["scores"],
        video_id=scoring_video_id,
        sources=derived_scoring_sources,
    )
    calculation_readiness_path.write_text(
        json.dumps(calculation_readiness, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    measurement_portfolio_path = (
        request.output_dir / "indicator-measurement-portfolio.json"
    )
    measurement_portfolio = build_indicator_measurement_portfolio(
        registry=scoring_loop["feasibility"],
        events=scoring_loop["events"],
        indicator_records=scoring_loop["indicator_records"],
        scores=scoring_loop["scores"],
        video_id=scoring_video_id,
        sources=derived_scoring_sources,
    )
    measurement_portfolio_path.write_text(
        json.dumps(measurement_portfolio, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    scoring_cycle_path = request.output_dir / "scoring-cycle-measurement.json"
    scoring_cycle = build_scoring_cycle_measurement(
        registry=scoring_loop["feasibility"],
        events=scoring_loop["events"],
        indicator_records=scoring_loop["indicator_records"],
        scores=scoring_loop["scores"],
        video_id=scoring_video_id,
        sources=derived_scoring_sources,
    )
    scoring_cycle_path.write_text(
        json.dumps(scoring_cycle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    scoring_loop_report_path = request.output_dir / "scoring-loop-report.html"
    write_scoring_loop_report(scoring_loop, scoring_loop_report_path)
    timings["minimum_scoring_loop_seconds"] += time.perf_counter() - stage_started

    elapsed = time.perf_counter() - started_at
    quality_summary = aggregate_quality(quality_samples, metadata)
    coverage = {
        "player_frame_fraction": safe_float(person_frame_count / processed, 4),
        "pose_frame_fraction": safe_float(pose_frame_count / processed, 4),
        "ball_frame_fraction": safe_float(frames_with_class["ball"] / processed, 4),
        "racket_frame_fraction": safe_float(
            frames_with_class["racket"] / processed, 4
        ),
        "court_detected_fraction": safe_float(
            sum(
                count
                for status, count in court_status_counts.items()
                if status in {"detected", "calibrated"}
            )
            / processed,
            4,
        ),
        "court_calibrated_fraction": safe_float(
            court_status_counts["calibrated"] / processed,
            4,
        ),
    }
    capability_checks = {
        "player_tracking": coverage["player_frame_fraction"] >= 0.75,
        "body_pose": coverage["pose_frame_fraction"] >= 0.75,
        "ball_observation": coverage["ball_frame_fraction"] >= 0.5,
        "racket_observation": coverage["racket_frame_fraction"] >= 0.5,
        "court_region": False,  # Manual calibration is for display only.
        "court_calibration": False,
    }
    hit_event_ready = all(
        capability_checks[name]
        for name in (
            "player_tracking",
            "body_pose",
            "ball_observation",
            "racket_observation",
        )
    )
    court_region_ready = (
        hit_event_ready and capability_checks["court_region"]
    )
    metric_court_ready = (
        hit_event_ready and capability_checks["court_calibration"]
    )

    try:
        import torch
        import ultralytics

        runtime = {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "device_requested": request.models.device,
            "device_used": device,
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device_name": (
                torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
            ),
            "cpu_thread_limit": int(torch.get_num_threads()),
            "torch_interop_threads": int(torch.get_num_interop_threads()),
            "opencv_threads": int(cv2.getNumThreads()),
        }
    except Exception:
        runtime = {
            "python": platform.python_version(),
            "device_requested": request.models.device,
            "device_used": device,
            "opencv_threads": int(cv2.getNumThreads()),
        }

    pose_metadata = perception.pose_metadata()
    native_pose_schema = keypoint_schema(pose_metadata["native_keypoint_format"])
    covered_joint_ids = [
        point["downstream_joint_id"] for point in native_pose_schema["keypoints"]
    ]
    topology_gaps = [
        "palms/fingers",
        "racket head/throat/handle/sweet-spot keypoints",
        "ball z-coordinate/spin",
        "automatic metric court homography without manual calibration",
    ]
    if pose_metadata["native_keypoint_format"] == "coco17":
        topology_gaps.insert(0, "heels/midfoot/forefoot/toes")
        topology_gaps.insert(0, "head top/chin/neck/chest/spine/pelvis center")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "job_id": request.job_id,
        "status": "completed",
        "input": {
            "video_path": str(request.video_path),
            "upstream_metadata": request.upstream_metadata,
            "video": {
                "width": metadata.width,
                "height": metadata.height,
                "fps": safe_float(metadata.fps, 4),
                "frame_count": metadata.frame_count,
                "duration_ms": metadata.duration_ms,
            },
        },
        "models": {
            "detect": str(request.models.detect),
            "pose": str(request.models.pose),
            "target_classes": ["player", "ball", "racket"],
            "detection_policy": (perception.detection_metadata()
                                 if callable(getattr(perception, "detection_metadata", None)) else None),
            "pose_format": pose_metadata["native_keypoint_format"],
            "pose_backend": pose_metadata,
            "pose_deployment_preset": request.models.pose_preset,
            "pose_joint_mapping": [
                point["downstream_joint_id"]
                for point in native_pose_schema["keypoints"]
            ],
            "court_detector": "manual_fixed_camera_display_only" if manual_court.enabled else "disabled",
            "court_refresh_policy": "per_frame_camera_guard_no_reuse_after_break" if manual_court.enabled else "disabled",
        },
        "processing": {
            "processed_frames": processed,
            "source_start_frame": first_processed_source_frame,
            "source_end_frame_exclusive": source_frame_index,
            "last_processed_timestamp_ms": last_processed_timestamp_ms,
            "source_timing": clock.summary(),
            "camera_motion": {"status_counts": dict(camera_status_counts),
                              "compensated_frames": camera_compensated_frames,
                              "semantics": "background_image_plane_similarity_not_metric_or_3d"},
            "frame_stride": request.processing.frame_stride,
            "elapsed_seconds": safe_float(elapsed, 3),
            "effective_processed_fps": safe_float(processed / elapsed, 3),
            "stage_seconds": {
                key: safe_float(value, 3) for key, value in timings.items()
            },
            "stage_timing_semantics": "wall_clock; stages are partial and residual orchestration is not separately assigned; component breakdowns must not be added to parent stages; with prefetch decoder producer work overlaps consumer stages; SAM CUDA event spans and IPC waits are retained per native record; first model call may include CUDA warmup while eager model loading precedes this pipeline",
            "source_prefetch": ({"queue_capacity_frames": prefetch_depth,
                                 "decoded_frames": getattr(capture, "produced_frames", None),
                                 "consumed_frames": getattr(capture, "consumed_frames", None),
                                 "semantics": "sequential_source_pixels_and_PTS_no_frame_skipping"}
                                if sam_enabled else None),
            **({"mesh_archive": mesh_archiver.receipt} if mesh_archiver is not None else {}),
        },
        "quality": quality_summary,
        "counts": {
            "detections": dict(class_counts),
            "frames_with_class": dict(frames_with_class),
            "frames_with_pose": pose_frame_count,
            "court_status_frames": dict(court_status_counts),
        },
        "coverage": coverage,
        "primary_player": primary_player["diagnostics"],
        "action_recognition": action_recognition,
        "minimum_scoring_loop": scoring_loop["summary"],
        "calculation_readiness": calculation_readiness["summary"],
        "measurement_portfolio": measurement_portfolio["summary"],
        "scoring_cycle_measurement": scoring_cycle["summary"],
        "scoring_reference_context": scoring_loop["summary"][
            "scoring_reference_context"
        ],
        "scoring_state": scoring_loop["summary"]["result_state"],
        "sam3d_measurement": ({"status": sam_evaluation["status"], "counts": sam_evaluation["counts"],
                                "accuracy_validated": False, "confidence_available": False,
                                "camera_intrinsics": "official_default_fov_uncalibrated"}
                               if sam_evaluation is not None else None),
        "next_stage": {
            "ready": hit_event_ready,
            "ready_for_hit_event_detection": hit_event_ready,
            "hit_event_detector_enabled": False,
            "action_candidate_detector_enabled": True,
            "ready_for_court_region_events": court_region_ready,
            "ready_for_metric_court_metrics": metric_court_ready,
            "ready_for_final_scoring": (
                scoring_loop["summary"]["result_state"]["scoring_status"]
                == "scored"
            ),
            "final_scoring_status": scoring_loop["summary"]["result_state"][
                "scoring_status"
            ],
            "all_stage1_capabilities_ready": metric_court_ready,
            "capability_checks": capability_checks,
            "required_inputs": ["frames.jsonl", "summary.json"],
            "contract": "frame observations keyed by timestamp_ms and track_id",
            "limitations": [
                "COCO sports-ball and tennis-racket detections are generic baselines",
                "manual court calibration supports fixed-camera display only; court-dependent scoring remains unavailable",
                "racket face and sweet-spot keypoints require a custom model",
            ],
            "requirements_gap": {
                "native_keypoint_format": pose_metadata["native_keypoint_format"],
                "covered_joint_ids": covered_joint_ids,
                "not_covered_by_pose_topology": topology_gaps,
            },
        },
        "runtime": runtime,
        "artifacts": {
            "frames_jsonl": relative_or_absolute(frames_path, request.output_dir),
            "sam3d_frames_jsonl": "sam3d_frames.jsonl" if sam_enabled else None,
            "sam3d_evaluation_json": "sam3d_evaluation.json" if sam_enabled else None,
            "sam3d_mesh": sam3d_mesh,
            "summary_json": relative_or_absolute(summary_path, request.output_dir),
            "annotated_video": (
                relative_or_absolute(video_path, request.output_dir)
                if writer is not None
                else None
            ),
            "annotated_video_compatibility": annotated_video_compatibility,
            "preview_image": (
                relative_or_absolute(preview_path, request.output_dir)
                if preview_path.exists()
                else None
            ),
            "primary_player_jsonl": relative_or_absolute(
                primary_timeline_path, request.output_dir
            ),
            "primary_player_summary_json": relative_or_absolute(
                primary_summary_path, request.output_dir
            ),
            "events_jsonl": "events.jsonl",
            "features_jsonl": "features.jsonl",
            "indicator_features_jsonl": "indicator-features.jsonl",
            "scores_jsonl": "scores.jsonl",
            "event_feature_errors_json": "event-feature-errors.json",
            "scoring_loop_summary_json": "scoring-loop-summary.json",
            "calculation_readiness_json": relative_or_absolute(
                calculation_readiness_path, request.output_dir
            ),
            "indicator_measurement_portfolio_json": relative_or_absolute(
                measurement_portfolio_path, request.output_dir
            ),
            "scoring_cycle_measurement_json": relative_or_absolute(
                scoring_cycle_path, request.output_dir
            ),
            "scoring_loop_report_html": relative_or_absolute(
                scoring_loop_report_path, request.output_dir
            ),
        },
    }
    return finalize_pipeline_artifacts(
        request,
        summary,
        registry_authority=registry_authority,
        report_postprocessing=report_postprocessing,
        cancellation_callback=check_cancelled,
        started_at=started_at,
        timings=timings,
    )
