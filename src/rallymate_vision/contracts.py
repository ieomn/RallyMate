from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rallymate_vision.pose.presets import resolve_pose_deployment_preset


REQUEST_SCHEMA_VERSION = "1.0.0"
FRAME_SCHEMA_VERSION = "1.1.0"
SCHEMA_VERSION = REQUEST_SCHEMA_VERSION


class ContractError(ValueError):
    """Raised when the upstream request does not satisfy the demo contract."""


@dataclass
class ModelConfig:
    detect: Path
    pose: Path
    detect_imgsz: int = 960
    pose_imgsz: int = 640
    detect_confidence: float = 0.15
    pose_confidence: float = 0.25
    device: str = "auto"
    pose_backend: str = "yolo"
    pose_runtime: str = "pytorch"
    pose_profile: str = "realtime"
    pose_config: Path | None = None
    pose_preset: str | None = None
    pose_native_keypoint_format: str | None = None


@dataclass
class ProcessingConfig:
    start_ms: int = 0
    end_ms: int | None = None
    frame_stride: int = 1
    max_frames: int | None = None
    max_players: int = 2
    court_every_n_frames: int = 25
    write_annotated_video: bool = True


@dataclass
class CourtConfig:
    mode: str = "auto"
    manual_polygon_normalized: list[list[float]] | None = None
    manual_polygon_role: str = "visible_region"
    refresh_policy: str = "until_usable"


@dataclass
class ScoringConfig:
    feasibility_registry: Path | None = None
    calibration_assets: list[Path] = field(default_factory=list)
    reference_context: Path | None = None


@dataclass
class PipelineRequest:
    job_id: str
    video_path: Path
    output_dir: Path
    models: ModelConfig
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    court: CourtConfig = field(default_factory=CourtConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    upstream_metadata: dict[str, Any] = field(default_factory=dict)
    request_path: Path | None = None


def _resolve(base: Path, value: str, field_name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string")
    path = Path(value)
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def _number(
    data: dict[str, Any],
    key: str,
    default: int | float,
    minimum: int | float,
) -> int | float:
    value = data.get(key, default)
    if not isinstance(value, (int, float)) or value < minimum:
        raise ContractError(f"{key} must be a number >= {minimum}")
    return value


def _validate_pose_preset_binding(models: ModelConfig) -> None:
    if models.pose_preset is None:
        return
    workspace = Path(__file__).resolve().parents[2]
    try:
        preset = resolve_pose_deployment_preset(
            models.pose_preset,
            workspace,
            require_files=False,
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ContractError(
            f"models.pose_preset is not registered: {models.pose_preset!r}"
        ) from exc

    expected_fields: tuple[tuple[str, Any, Any], ...] = (
        ("models.pose", models.pose.resolve(), preset.model_path.resolve()),
        ("models.pose_backend", models.pose_backend, preset.pose_backend),
        ("models.pose_runtime", models.pose_runtime, preset.pose_runtime),
        ("models.pose_profile", models.pose_profile, preset.pose_profile),
        (
            "models.pose_config",
            models.pose_config.resolve() if models.pose_config is not None else None,
            preset.config_path.resolve() if preset.config_path is not None else None,
        ),
        (
            "models.pose_native_keypoint_format",
            models.pose_native_keypoint_format,
            preset.native_keypoint_format,
        ),
    )
    for field_name, actual, expected in expected_fields:
        if actual != expected:
            raise ContractError(
                f"{field_name} does not match pose preset "
                f"{models.pose_preset!r}: expected {expected!s}, got {actual!s}"
            )


def load_request(path: str | Path) -> PipelineRequest:
    request_path = Path(path).resolve()
    if not request_path.exists():
        raise ContractError(f"request file does not exist: {request_path}")
    try:
        raw = json.loads(request_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"request is not valid JSON: {exc}") from exc

    if raw.get("schema_version") not in (None, SCHEMA_VERSION):
        raise ContractError(
            f"unsupported schema_version {raw.get('schema_version')!r}; "
            f"expected {SCHEMA_VERSION}"
        )

    base = request_path.parent
    source = raw.get("source", {})
    output = raw.get("output", {})
    model_data = raw.get("models", {})
    processing_data = raw.get("processing", {})
    court_data = raw.get("court", {})
    scoring_data = raw.get("scoring", {})
    if not isinstance(scoring_data, dict):
        raise ContractError("scoring must be an object")
    unsupported_scoring_fields = set(scoring_data) - {
        "feasibility_registry",
        "reference_context",
        "calibration_assets",
    }
    if unsupported_scoring_fields:
        raise ContractError(
            "scoring has unsupported fields: "
            f"{sorted(unsupported_scoring_fields)}"
        )

    job_id = raw.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ContractError("job_id must be a non-empty string")

    video_path = _resolve(base, source.get("video_path", ""), "source.video_path")
    output_dir = _resolve(base, output.get("directory", ""), "output.directory")
    detect_model = _resolve(base, model_data.get("detect", ""), "models.detect")
    pose_model = _resolve(base, model_data.get("pose", ""), "models.pose")

    processing = ProcessingConfig(
        start_ms=int(_number(processing_data, "start_ms", 0, 0)),
        end_ms=(
            int(processing_data["end_ms"])
            if processing_data.get("end_ms") is not None
            else None
        ),
        frame_stride=int(_number(processing_data, "frame_stride", 1, 1)),
        max_frames=(
            int(_number(processing_data, "max_frames", 1, 1))
            if processing_data.get("max_frames") is not None
            else None
        ),
        max_players=int(_number(processing_data, "max_players", 2, 1)),
        court_every_n_frames=int(
            _number(processing_data, "court_every_n_frames", 25, 1)
        ),
        write_annotated_video=bool(
            processing_data.get("write_annotated_video", True)
        ),
    )
    if processing.end_ms is not None and processing.end_ms <= processing.start_ms:
        raise ContractError("processing.end_ms must be greater than start_ms")

    models = ModelConfig(
        detect=detect_model,
        pose=pose_model,
        detect_imgsz=int(_number(model_data, "detect_imgsz", 960, 320)),
        pose_imgsz=int(_number(model_data, "pose_imgsz", 640, 320)),
        detect_confidence=float(
            _number(model_data, "detect_confidence", 0.15, 0.0)
        ),
        pose_confidence=float(
            _number(model_data, "pose_confidence", 0.25, 0.0)
        ),
        device=str(model_data.get("device", "auto")),
        pose_backend=str(model_data.get("pose_backend", "yolo")).lower(),
        pose_runtime=str(model_data.get("pose_runtime", "pytorch")).lower(),
        pose_profile=str(model_data.get("pose_profile", "realtime")).lower(),
        pose_config=(
            _resolve(base, model_data["pose_config"], "models.pose_config")
            if model_data.get("pose_config") is not None
            else None
        ),
        pose_preset=(
            str(model_data["pose_preset"])
            if model_data.get("pose_preset") is not None
            else None
        ),
        pose_native_keypoint_format=(
            str(model_data["pose_native_keypoint_format"])
            if model_data.get("pose_native_keypoint_format") is not None
            else None
        ),
    )
    if not 0.0 <= models.detect_confidence <= 1.0:
        raise ContractError("models.detect_confidence must be in the 0..1 range")
    if not 0.0 <= models.pose_confidence <= 1.0:
        raise ContractError("models.pose_confidence must be in the 0..1 range")
    if models.pose_backend not in {"yolo", "rtmpose"}:
        raise ContractError("models.pose_backend must be yolo or rtmpose")
    if models.pose_runtime not in {"pytorch", "onnxruntime", "tensorrt"}:
        raise ContractError(
            "models.pose_runtime must be pytorch, onnxruntime or tensorrt"
        )
    if models.pose_profile not in {"realtime", "analysis"}:
        raise ContractError("models.pose_profile must be realtime or analysis")
    if models.pose_native_keypoint_format not in {
        None,
        "coco17",
        "halpe26",
        "coco_wholebody133",
    }:
        raise ContractError(
            "models.pose_native_keypoint_format must be coco17, halpe26 or "
            "coco_wholebody133"
        )
    _validate_pose_preset_binding(models)

    polygon = court_data.get("manual_polygon_normalized")
    if polygon is not None:
        if (
            not isinstance(polygon, list)
            or len(polygon) != 4
            or any(
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    not isinstance(value, (int, float)) or not 0 <= value <= 1
                    for value in point
                )
                for point in polygon
            )
        ):
            raise ContractError(
                "court.manual_polygon_normalized must contain four [x, y] "
                "points in the 0..1 range"
            )

    court = CourtConfig(
        mode=str(court_data.get("mode", "auto")),
        manual_polygon_normalized=polygon,
        manual_polygon_role=str(
            court_data.get("manual_polygon_role", "visible_region")
        ),
        refresh_policy=str(
            court_data.get("refresh_policy", "until_usable")
        ),
    )
    if court.mode not in {"auto", "manual", "disabled"}:
        raise ContractError("court.mode must be auto, manual or disabled")
    if court.mode == "manual" and polygon is None:
        raise ContractError(
            "court.manual_polygon_normalized is required when court.mode=manual"
        )
    if court.manual_polygon_role not in {
        "visible_region",
        "court_outer_doubles_corners",
    }:
        raise ContractError(
            "court.manual_polygon_role must be visible_region or "
            "court_outer_doubles_corners"
        )
    if court.refresh_policy not in {"once", "until_usable", "interval"}:
        raise ContractError(
            "court.refresh_policy must be once, until_usable or interval"
        )

    calibration_asset_values = scoring_data.get("calibration_assets", [])
    if not isinstance(calibration_asset_values, list):
        raise ContractError("scoring.calibration_assets must be an array")
    scoring = ScoringConfig(
        feasibility_registry=(
            _resolve(
                base,
                scoring_data["feasibility_registry"],
                "scoring.feasibility_registry",
            )
            if scoring_data.get("feasibility_registry") is not None
            else None
        ),
        calibration_assets=(
            [
                _resolve(
                    base,
                    value,
                    f"scoring.calibration_assets[{index}]",
                )
                for index, value in enumerate(calibration_asset_values)
            ]
        ),
        reference_context=(
            _resolve(
                base,
                scoring_data["reference_context"],
                "scoring.reference_context",
            )
            if scoring_data.get("reference_context") is not None
            else None
        ),
    )
    if len(set(scoring.calibration_assets)) != len(scoring.calibration_assets):
        raise ContractError("scoring.calibration_assets must not contain duplicates")

    return PipelineRequest(
        job_id=job_id.strip(),
        video_path=video_path,
        output_dir=output_dir,
        models=models,
        processing=processing,
        court=court,
        scoring=scoring,
        upstream_metadata=raw.get("upstream_metadata", {}),
        request_path=request_path,
    )
