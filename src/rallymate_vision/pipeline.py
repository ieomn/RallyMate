from __future__ import annotations

import json
import platform
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import cv2

from rallymate_scoring.calibration import (
    validate_ordinal_model,
    validate_threshold_calibration,
)
from rallymate_scoring.calibration_promotion import (
    CalibrationPromotionError,
    authorize_production_asset_with_trusted_ledger,
    bind_trusted_production_calibration_to_registry,
)
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
from rallymate_scoring.runtime_profile_binding import (
    RuntimeProfileBindingError,
    validate_runtime_view_evidence,
    validate_trusted_runtime_binding_registry,
)
from rallymate_scoring.scoring_context import load_scoring_reference_context
from rallymate_scoring.report import write_analysis_report
from rallymate_scoring.scoring_loop_report import write_scoring_loop_report
from rallymate_tracking import build_primary_player_artifacts
from rallymate_vision.contracts import FRAME_SCHEMA_VERSION, SCHEMA_VERSION, PipelineRequest
from rallymate_vision.court import CourtDetector
from rallymate_vision.inference import Yolo26Perception
from rallymate_vision.pose.metadata import keypoint_schema, sha256_file
from rallymate_vision.quality import (
    aggregate_quality,
    analyze_frame,
    probe_video,
)
from rallymate_vision.render import annotate_frame
from rallymate_vision.tracking import SimpleMultiClassTracker
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
    payload = _load_runtime_authorization_json(
        path, "trusted runtime-profile binding registry"
    )
    try:
        validate_trusted_runtime_binding_registry(payload)
    except RuntimeProfileBindingError as exc:
        raise ValueError(
            f"trusted runtime-profile binding registry validation failed: {exc}"
        ) from exc
    return payload


def load_runtime_view_evidence(path: Path) -> dict:
    payload = _load_runtime_authorization_json(path, "runtime view evidence")
    try:
        validate_runtime_view_evidence(payload)
    except RuntimeProfileBindingError as exc:
        raise ValueError(f"runtime view evidence validation failed: {exc}") from exc
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
    authority = resolve_runtime_feasibility_registry(manifest_path)
    requested_path = request.scoring.feasibility_registry
    if requested_path is not None and requested_path.resolve() != authority.path:
        raise ValueError(
            "requested scoring feasibility registry is not authorized by "
            "registry lifecycle role runtime_feasibility"
        )
    return authority


def _verify_scoring_registry_authority_unchanged(
    authority: ResolvedRegistryArtifact,
) -> None:
    if sha256_file(authority.manifest_path).lower() != authority.manifest_sha256:
        raise RuntimeError(
            "registry lifecycle manifest changed after production preflight"
        )
    if sha256_file(authority.path).lower() != authority.file_sha256:
        raise RuntimeError(
            "scoring feasibility registry changed after production preflight"
        )


def load_pipeline_calibrations(
    paths: list[Path] | tuple[Path, ...],
    *,
    trusted_ledger_path: Path | None = None,
    feasibility_registry: dict | None = None,
) -> tuple[dict[str, object], list[dict]]:
    """Load only production assets authorized by an operator-controlled ledger.

    A ``passed`` field inside an asset is never a trust root. The canonical asset
    hash and its complete promotion lineage must match one active entry in a
    versioned ledger whose path is selected by the deployment operator, outside
    ordinary job request JSON.
    """

    calibrations: dict[str, object] = {}
    provenance: list[dict] = []
    trusted_ledger: dict | None = None
    resolved_ledger_path: Path | None = None
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"scoring calibration asset is missing: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid scoring calibration JSON {path}: {exc}") from exc
        backend = payload.get("backend")
        if backend == "threshold_rule":
            validate_threshold_calibration(payload)
        elif backend == "ordinal_regression":
            validate_ordinal_model(payload)
        else:
            raise ValueError(f"unsupported scoring calibration backend in {path}")
        if payload.get("artifact_scope") != "production":
            raise ValueError(
                "pipeline requests accept production-scoped calibration assets only"
            )
        if trusted_ledger is None:
            if trusted_ledger_path is None:
                raise ValueError(
                    "production calibration requires an operator-configured trusted "
                    "promotion ledger"
                )
            resolved_ledger_path = Path(trusted_ledger_path).resolve()
            if not resolved_ledger_path.exists() or not resolved_ledger_path.is_file():
                raise FileNotFoundError(
                    "trusted calibration promotion ledger is missing: "
                    f"{resolved_ledger_path}"
                )
            try:
                trusted_ledger = json.loads(
                    resolved_ledger_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid trusted promotion ledger JSON {resolved_ledger_path}: {exc}"
                ) from exc
        try:
            trusted_calibration = authorize_production_asset_with_trusted_ledger(
                payload, trusted_ledger
            )
            if feasibility_registry is None:
                raise CalibrationPromotionError(
                    "current runtime feasibility registry is required for production "
                    "calibration"
                )
            trusted_calibration = bind_trusted_production_calibration_to_registry(
                trusted_calibration,
                feasibility_registry,
            )
            promotion_audit = trusted_calibration.authorization
        except CalibrationPromotionError as exc:
            raise ValueError(
                f"production calibration promotion verification failed for {path}: {exc}"
            ) from exc
        indicator_id = str(payload["indicator_id"])
        if indicator_id in calibrations:
            raise ValueError(f"duplicate scoring calibration for {indicator_id}")
        calibrations[indicator_id] = trusted_calibration
        provenance.append(
            {
                "indicator_id": indicator_id,
                "backend": str(backend),
                "version": payload.get("threshold_version")
                or payload.get("model_version"),
                "path": str(path),
                "sha256": sha256_file(path),
                "ground_truth_dataset_version": payload[
                    "ground_truth_dataset_version"
                ],
                "independent_test_status": payload["independent_test"]["status"],
                "trusted_promotion_ledger_path": str(resolved_ledger_path),
                "trusted_promotion_ledger_version": promotion_audit["ledger_version"],
                "trusted_promotion_ledger_entry_id": promotion_audit["entry_id"],
                "promotion_id": promotion_audit["promotion_id"],
                "promotion_version": promotion_audit["promotion_version"],
                "promotion_lineage_sha256": promotion_audit["lineage_sha256"],
                "promotion_report_sha256": promotion_audit[
                    "promotion_report_sha256"
                ],
                "promoted_registry_version": promotion_audit[
                    "promoted_registry_version"
                ],
                "promoted_registry_content_sha256": promotion_audit[
                    "promoted_registry_content_sha256"
                ],
                "runtime_registry_version": promotion_audit[
                    "runtime_registry_version"
                ],
                "runtime_registry_content_sha256": promotion_audit[
                    "runtime_registry_content_sha256"
                ],
                "runtime_registry_indicator_id": promotion_audit[
                    "runtime_registry_indicator_id"
                ],
                "runtime_registry_indicator_level": promotion_audit[
                    "runtime_registry_indicator_level"
                ],
                "maturity_evidence_bundle_id": promotion_audit[
                    "maturity_evidence_bundle_id"
                ],
                "maturity_evidence_content_sha256": promotion_audit[
                    "maturity_evidence_content_sha256"
                ],
                "maturity_evidence_final_transition_sha256": promotion_audit[
                    "maturity_evidence_final_transition_sha256"
                ],
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
) -> dict:
    def emit_progress(payload: dict) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(payload)
        except Exception:
            # Progress reporting is operational metadata and must not abort a
            # valid GPU inference run.
            return

    started_at = time.perf_counter()
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
    # Fail before expensive GPU work if a requested production calibration is
    # malformed, duplicated, test-only or otherwise unsafe. Validation also
    # precedes output-directory creation so a rejected request leaves no run shell.
    operator_ledger_path = trusted_promotion_ledger_path
    if operator_ledger_path is None and request.scoring.calibration_assets:
        operator_ledger_path = (
            Path(__file__).resolve().parents[2] / DEFAULT_TRUSTED_PROMOTION_LEDGER
        ).resolve()
    calibrations, calibration_provenance = load_pipeline_calibrations(
        request.scoring.calibration_assets,
        trusted_ledger_path=operator_ledger_path,
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
    if calibrations:
        if trusted_runtime_binding_registry_path is None:
            raise ValueError(
                "production calibration requires an operator-configured trusted "
                "runtime-profile binding registry"
            )
        if runtime_view_evidence_path is None:
            raise ValueError(
                "production calibration requires operator-supplied accepted "
                "per-video runtime view evidence"
            )
        resolved_binding_path = Path(
            trusted_runtime_binding_registry_path
        ).resolve()
        resolved_view_path = Path(runtime_view_evidence_path).resolve()
        runtime_binding_registry = load_trusted_runtime_binding_registry(
            resolved_binding_path
        )
        runtime_view_evidence = load_runtime_view_evidence(resolved_view_path)
        if runtime_view_evidence["video_sha256"].lower() != input_video_sha256.lower():
            raise ValueError(
                "runtime view evidence video_sha256 does not match the input video"
            )
        # The accepted evidence owns the stable content/video identity.  The
        # request job_id remains execution provenance and may legitimately
        # change when the same reviewed video is reprocessed.
        scoring_video_id = runtime_view_evidence["video_id"]
        runtime_authorization_files = {
            "trusted_runtime_binding_registry": {
                "path": str(resolved_binding_path),
                "sha256": sha256_file(resolved_binding_path),
            },
            "runtime_view_evidence": {
                "path": str(resolved_view_path),
                "sha256": sha256_file(resolved_view_path),
            },
        }
    if scoring_reference_video_id is not None:
        if calibrations and scoring_video_id != scoring_reference_video_id:
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
    court_detector = CourtDetector(
        mode=request.court.mode,
        manual_polygon_normalized=request.court.manual_polygon_normalized,
        manual_polygon_role=request.court.manual_polygon_role,
    )

    frames_path = request.output_dir / "frames.jsonl"
    summary_path = request.output_dir / "summary.json"
    video_path = request.output_dir / "annotated.mp4"
    preview_path = request.output_dir / "preview.jpg"

    capture = cv2.VideoCapture(str(request.video_path))
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
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    output_fps = metadata.fps / request.processing.frame_stride
    writer = None
    if request.processing.write_annotated_video:
        writer = cv2.VideoWriter(
            str(video_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            max(output_fps, 1.0),
            (metadata.width, metadata.height),
        )
        if not writer.isOpened():
            capture.release()
            raise RuntimeError("could not initialize annotated MP4 writer")

    processed = 0
    source_frame_index = start_frame
    class_counts: Counter[str] = Counter()
    frames_with_class: Counter[str] = Counter()
    pose_frame_count = 0
    person_frame_count = 0
    quality_samples: list[dict] = []
    timings = Counter()
    latest_court: dict | None = None
    court_status_counts: Counter[str] = Counter()

    with frames_path.open("w", encoding="utf-8", newline="\n") as frames_file:
        while source_frame_index < end_frame:
            ok, frame = capture.read()
            if not ok:
                break
            if (source_frame_index - start_frame) % request.processing.frame_stride != 0:
                source_frame_index += 1
                continue
            timestamp_ms = int(source_frame_index / metadata.fps * 1000)

            quality = analyze_frame(frame)
            quality_samples.append(quality)

            stage_started = time.perf_counter()
            detections = perception.detect(frame)
            timings["detection_seconds"] += time.perf_counter() - stage_started
            detections = tracker.update(
                detections, metadata.width, metadata.height
            )

            players = [
                detection
                for detection in detections
                if detection["class_name"] == "player"
            ]
            if players:
                person_frame_count += 1
            stage_started = time.perf_counter()
            poses = perception.estimate_poses(
                frame, players, request.processing.max_players
            )
            timings["pose_seconds"] += time.perf_counter() - stage_started
            if poses:
                pose_frame_count += 1

            court_interval_due = (
                processed % request.processing.court_every_n_frames == 0
            )
            should_refresh_court = latest_court is None
            if latest_court is not None and court_interval_due:
                if request.court.refresh_policy == "interval":
                    should_refresh_court = True
                elif request.court.refresh_policy == "until_usable":
                    should_refresh_court = not bool(
                        latest_court.get("region_usable", False)
                    )
            if should_refresh_court:
                stage_started = time.perf_counter()
                latest_court = court_detector.detect(
                    frame,
                    player_boxes=[
                        player["bbox_px"] for player in players
                    ],
                )
                timings["court_seconds"] += time.perf_counter() - stage_started
                latest_court["observed_at_frame"] = source_frame_index
            court = dict(latest_court)
            court["age_frames"] = source_frame_index - int(
                court.get("observed_at_frame", source_frame_index)
            )
            court_status_counts[court["status"]] += 1

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
                    "width": metadata.width,
                    "height": metadata.height,
                    "coordinate_origin": "top_left",
                },
                "quality": quality,
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
            validate_frame_observation(record)
            frames_file.write(json.dumps(record, ensure_ascii=False) + "\n")

            if writer is not None:
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

            processed += 1
            source_frame_index += 1
            if processed == 1 or processed % progress_interval == 0:
                elapsed_now = max(time.perf_counter() - started_at, 1e-6)
                emit_progress(
                    {
                        "phase": "inference",
                        "percent": safe_float(
                            min(96.0, 3.0 + processed / planned_frames * 93.0),
                            1,
                        ),
                        "processed_frames": processed,
                        "total_frames": planned_frames,
                        "effective_fps": safe_float(processed / elapsed_now, 2),
                        "message": f"正在推理第 {processed}/{planned_frames} 帧",
                    }
                )
            if (
                request.processing.max_frames is not None
                and processed >= request.processing.max_frames
            ):
                break

    capture.release()
    if writer is not None:
        writer.release()
    if processed == 0:
        raise RuntimeError("no frames were processed; check start/end/stride settings")

    primary_timeline_path = request.output_dir / "primary-player.jsonl"
    primary_summary_path = request.output_dir / "primary-player-summary.json"
    stage_started = time.perf_counter()
    primary_player = build_primary_player_artifacts(
        frames_path,
        primary_timeline_path,
        primary_summary_path,
    )
    timings["primary_player_seconds"] += time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    # Fail closed if an operator replaces the registry while inference is in
    # progress. The preflight snapshot remains the authorization source.
    _verify_scoring_registry_authority_unchanged(registry_authority)
    if calibrations:
        assert trusted_runtime_binding_registry_path is not None
        assert runtime_view_evidence_path is not None
        if sha256_file(Path(trusted_runtime_binding_registry_path).resolve()) != (
            runtime_authorization_files["trusted_runtime_binding_registry"]["sha256"]
        ):
            raise RuntimeError(
                "trusted runtime-profile binding registry changed after preflight"
            )
        if sha256_file(Path(runtime_view_evidence_path).resolve()) != (
            runtime_authorization_files["runtime_view_evidence"]["sha256"]
        ):
            raise RuntimeError("runtime view evidence changed after preflight")
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
                "manifest_path": str(registry_authority.manifest_path),
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
    )
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

    emit_progress(
        {
            "phase": "finalizing",
            "percent": 98,
            "processed_frames": processed,
            "total_frames": planned_frames,
            "message": "正在汇总指标并校验输出产物",
        }
    )

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
        "court_region": coverage["court_detected_fraction"] >= 0.5,
        "court_calibration": coverage["court_calibrated_fraction"] >= 0.5,
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
            "pose_format": pose_metadata["native_keypoint_format"],
            "pose_backend": pose_metadata,
            "pose_deployment_preset": request.models.pose_preset,
            "pose_joint_mapping": [
                point["downstream_joint_id"]
                for point in native_pose_schema["keypoints"]
            ],
            "court_detector": "manual_polygon_or_validated_hough_hint",
            "court_refresh_policy": request.court.refresh_policy,
        },
        "processing": {
            "processed_frames": processed,
            "source_start_frame": start_frame,
            "source_end_frame_exclusive": source_frame_index,
            "frame_stride": request.processing.frame_stride,
            "elapsed_seconds": safe_float(elapsed, 3),
            "effective_processed_fps": safe_float(processed / elapsed, 3),
            "stage_seconds": {
                key: safe_float(value, 3) for key, value in timings.items()
            },
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
        "minimum_scoring_loop": scoring_loop["summary"],
        "calculation_readiness": calculation_readiness["summary"],
        "measurement_portfolio": measurement_portfolio["summary"],
        "scoring_cycle_measurement": scoring_cycle["summary"],
        "scoring_reference_context": scoring_loop["summary"][
            "scoring_reference_context"
        ],
        "scoring_state": scoring_loop["summary"]["result_state"],
        "next_stage": {
            "ready": hit_event_ready,
            "ready_for_hit_event_detection": hit_event_ready,
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
                "automatic court output is a validated region hint, not metric calibration",
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
            "summary_json": relative_or_absolute(summary_path, request.output_dir),
            "annotated_video": (
                relative_or_absolute(video_path, request.output_dir)
                if writer is not None
                else None
            ),
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
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary["validation"] = validate_run_artifacts(request.output_dir)
    emit_progress(
        {
            "phase": "scoring_readiness",
            "percent": 99,
            "processed_frames": processed,
            "total_frames": planned_frames,
            "message": "正在逐项审计 GS/FS 评分颗粒度",
        }
    )
    scoring_readiness_path = request.output_dir / "scoring-readiness.json"
    scoring_readiness = analyze_scoring_readiness(summary, frames_path)
    scoring_readiness_path.write_text(
        json.dumps(scoring_readiness, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary["scoring_readiness"] = scoring_readiness["summary"]
    summary["pose_assessment"] = scoring_readiness["pose_assessment"]
    summary["artifacts"]["scoring_readiness_json"] = relative_or_absolute(
        scoring_readiness_path, request.output_dir
    )
    analysis_report_path = request.output_dir / "analysis-report.html"
    write_analysis_report(summary, scoring_readiness, analysis_report_path)
    summary["artifacts"]["analysis_report_html"] = relative_or_absolute(
        analysis_report_path, request.output_dir
    )
    _verify_scoring_registry_authority_unchanged(registry_authority)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
