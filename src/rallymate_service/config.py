from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from rallymate_scoring.registry_lifecycle import (
    ResolvedRegistryArtifact,
    resolve_runtime_feasibility_registry,
)
from rallymate_scoring.technique_registry import (
    TechniqueRegistryError,
    load_technique_registry,
)
from rallymate_vision.pose.presets import (
    default_pose_deployment_preset_id,
    resolve_pose_deployment_preset,
)


class ServiceConfigError(ValueError):
    """Raised when deployment settings are incomplete or unsafe."""


def _split_http_url(value: str, setting_name: str) -> SplitResult:
    """Parse an operator-supplied HTTP(S) URL with safe failure semantics."""

    if not isinstance(value, str) or not value:
        raise ServiceConfigError(f"{setting_name} must be a non-empty URL")
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ServiceConfigError(f"{setting_name} must not contain whitespace or control characters")
    # Backslashes are not valid in an origin and can be interpreted differently
    # by URL parsers/proxies.  Reject them rather than normalizing implicitly.
    if "\\" in value:
        raise ServiceConfigError(f"{setting_name} must be a valid HTTP(S) URL")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing .port validates malformed/non-numeric ports and bad IPv6
        # brackets.  The value itself is not needed after this check.
        _ = parsed.port
    except ValueError as exc:
        raise ServiceConfigError(f"{setting_name} must be a valid HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.netloc.endswith(":")
    ):
        raise ServiceConfigError(f"{setting_name} must be a valid HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ServiceConfigError(f"{setting_name} must not contain URL credentials")
    # A query/fragment is never part of an API origin and would make generated
    # links ambiguous.  Check the raw separators so a trailing '?'/'#' is also
    # rejected even though urlsplit returns an empty component for it.
    if parsed.query or parsed.fragment or "?" in value or "#" in value:
        raise ServiceConfigError(f"{setting_name} must not contain a query or fragment")
    return parsed


def _validate_web_origins(
    public_base_url: str | None,
    cors_origins: tuple[str, ...],
    environment: str,
) -> None:
    if public_base_url is not None:
        parsed_public = _split_http_url(
            public_base_url, "RALLYMATE_PUBLIC_BASE_URL"
        )
        if parsed_public.path not in {"", "/"}:
            raise ServiceConfigError(
                "RALLYMATE_PUBLIC_BASE_URL must contain only scheme and host"
            )

    if len(set(cors_origins)) != len(cors_origins):
        raise ServiceConfigError("RALLYMATE_CORS_ORIGINS contains duplicate origins")
    for origin in cors_origins:
        if origin == "*":
            if environment.strip().lower() == "production" or public_base_url is not None:
                raise ServiceConfigError(
                    "RALLYMATE_CORS_ORIGINS must list explicit origins for a public deployment"
                )
            continue
        parsed = _split_http_url(origin, "RALLYMATE_CORS_ORIGINS")
        # A CORS origin is scheme + authority only.  A slash path does not match
        # the browser Origin header and therefore silently disables the intended
        # policy; reject it at startup instead of accepting a dead configuration.
        if parsed.path not in {"", "/"}:
            raise ServiceConfigError(
                "RALLYMATE_CORS_ORIGINS entries must not contain a path"
            )
        if parsed.path == "/":
            raise ServiceConfigError(
                "RALLYMATE_CORS_ORIGINS entries must omit the trailing slash"
            )


@dataclass(frozen=True)
class ServiceSettings:
    data_root: Path
    database_path: Path
    detect_model: Path
    pose_model: Path
    pose_backend: str = "yolo"
    pose_runtime: str = "pytorch"
    pose_profile: str = "realtime"
    pose_config: Path | None = None
    pose_preset: str | None = None
    pose_native_keypoint_format: str | None = None
    scoring_registry_lifecycle_manifest: Path | None = None
    scoring_feasibility_registry: Path | None = None
    scoring_calibration_assets: tuple[Path, ...] = ()
    scoring_trusted_promotion_ledger: Path | None = None
    scoring_trusted_runtime_profile_bindings: Path | None = None
    scoring_runtime_view_evidence_dir: Path | None = None
    technique_registry: Path | None = None
    api_key: str | None = None
    cors_origins: tuple[str, ...] = ()
    public_base_url: str | None = None
    environment: str = "development"
    model_license_ack: str = "development"
    device: str = "auto"
    cpu_threads: int = 4
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    max_video_duration_seconds: int = 30 * 60
    lease_seconds: int = 60 * 60
    max_attempts: int = 2

    @property
    def uploads_dir(self) -> Path:
        return self.data_root / "uploads"

    @property
    def requests_dir(self) -> Path:
        return self.data_root / "requests"

    @property
    def runs_dir(self) -> Path:
        return self.data_root / "runs"

    @property
    def resolved_scoring_feasibility_registry(self) -> Path:
        return self.resolved_scoring_registry_authority.path

    @property
    def resolved_scoring_registry_lifecycle_manifest(self) -> Path:
        if self.scoring_registry_lifecycle_manifest is not None:
            return self.scoring_registry_lifecycle_manifest.resolve()
        return (
            Path(__file__).resolve().parents[2]
            / "registry-lifecycle.json"
        ).resolve()

    @property
    def resolved_scoring_registry_authority(self) -> ResolvedRegistryArtifact:
        authority = resolve_runtime_feasibility_registry(
            self.resolved_scoring_registry_lifecycle_manifest
        )
        if (
            self.scoring_feasibility_registry is not None
            and self.scoring_feasibility_registry.resolve() != authority.path
        ):
            raise ServiceConfigError(
                "RALLYMATE_SCORING_FEASIBILITY_REGISTRY is not authorized by "
                "registry lifecycle role runtime_feasibility"
            )
        return authority

    @property
    def resolved_technique_registry(self) -> Path | None:
        """Return the optional operator-pinned qualitative registry path."""

        return self.technique_registry.resolve() if self.technique_registry else None

    def ensure_directories(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def validate_license(self, *, check_registry_authority: bool = True) -> None:
        """Validate startup policy and operator-controlled runtime paths.

        ``create_app`` performs the non-authority checks before accepting an
        injected settings object, while readiness still evaluates the scoring
        lifecycle authority.  Keeping that distinction preserves the existing
        diagnostic behavior for an intentionally unapproved scoring override:
        the API can expose a useful ``not_ready`` response instead of failing
        during app construction.
        """
        accepted = {
            "development",
            "enterprise",
            "agpl-compliant",
            "alternative-backend",
        }
        if not isinstance(self.environment, str):
            raise ServiceConfigError(
                "RALLYMATE_ENVIRONMENT must be development, staging, production or test"
            )
        environment = self.environment.strip().lower()
        if environment not in {
            "development",
            "staging",
            "production",
            "test",
        }:
            raise ServiceConfigError(
                "RALLYMATE_ENVIRONMENT must be development, staging, production or test"
            )
        if self.model_license_ack not in accepted:
            raise ServiceConfigError(
                "RALLYMATE_MODEL_LICENSE_ACK must be development, enterprise, "
                "agpl-compliant or alternative-backend"
            )
        if (
            environment == "production"
            and self.model_license_ack == "development"
        ):
            raise ServiceConfigError(
                "production cannot start with a development-only model license"
            )
        if self.pose_backend not in {"yolo", "rtmpose"}:
            raise ServiceConfigError("RALLYMATE_POSE_BACKEND must be yolo or rtmpose")
        if self.pose_runtime not in {"pytorch", "onnxruntime", "tensorrt"}:
            raise ServiceConfigError(
                "RALLYMATE_POSE_RUNTIME must be pytorch, onnxruntime or tensorrt"
            )
        if self.pose_profile not in {"realtime", "analysis"}:
            raise ServiceConfigError(
                "RALLYMATE_POSE_PROFILE must be realtime or analysis"
            )
        if self.pose_native_keypoint_format not in {
            None,
            "coco17",
            "halpe26",
            "coco_wholebody133",
        }:
            raise ServiceConfigError(
                "RALLYMATE_POSE_NATIVE_KEYPOINT_FORMAT must be coco17, "
                "halpe26 or coco_wholebody133"
            )
        if (
            self.pose_backend == "yolo"
            and self.pose_native_keypoint_format not in {None, "coco17"}
        ):
            raise ServiceConfigError("YOLO pose backend requires coco17 topology")
        if (
            isinstance(self.cpu_threads, bool)
            or not isinstance(self.cpu_threads, int)
            or self.cpu_threads < 1
        ):
            raise ServiceConfigError(
                "RALLYMATE_CPU_THREADS must be an integer >= 1"
            )
        if len(set(self.scoring_calibration_assets)) != len(
            self.scoring_calibration_assets
        ):
            raise ServiceConfigError(
                "RALLYMATE_SCORING_CALIBRATION_ASSETS contains duplicates"
            )
        if (
            self.scoring_calibration_assets
            and self.scoring_trusted_promotion_ledger is None
        ):
            raise ServiceConfigError(
                "production calibration assets require "
                "RALLYMATE_SCORING_TRUSTED_PROMOTION_LEDGER"
            )
        if (
            self.scoring_calibration_assets
            and self.scoring_trusted_runtime_profile_bindings is None
        ):
            raise ServiceConfigError(
                "production calibration assets require "
                "RALLYMATE_SCORING_TRUSTED_RUNTIME_PROFILE_BINDINGS"
            )
        if (
            self.scoring_calibration_assets
            and self.scoring_runtime_view_evidence_dir is None
        ):
            raise ServiceConfigError(
                "production calibration assets require "
                "RALLYMATE_SCORING_RUNTIME_VIEW_EVIDENCE_DIR"
            )
        protected_paths = [
            (
                "RALLYMATE_SCORING_REGISTRY_LIFECYCLE_MANIFEST",
                self.scoring_registry_lifecycle_manifest,
            ),
            (
                "RALLYMATE_SCORING_FEASIBILITY_REGISTRY",
                self.scoring_feasibility_registry,
            ),
            ("RALLYMATE_SCORING_TRUSTED_PROMOTION_LEDGER", self.scoring_trusted_promotion_ledger),
            (
                "RALLYMATE_SCORING_TRUSTED_RUNTIME_PROFILE_BINDINGS",
                self.scoring_trusted_runtime_profile_bindings,
            ),
            (
                "RALLYMATE_SCORING_RUNTIME_VIEW_EVIDENCE_DIR",
                self.scoring_runtime_view_evidence_dir,
            ),
        ]
        protected_paths.extend(
            (
                f"RALLYMATE_SCORING_CALIBRATION_ASSETS[{index}]",
                path,
            )
            for index, path in enumerate(self.scoring_calibration_assets)
        )
        for setting_name, configured_path in protected_paths:
            if configured_path is None:
                continue
            protected_path = configured_path.resolve()
            for writable_root in (
                self.uploads_dir,
                self.requests_dir,
                self.runs_dir,
            ):
                try:
                    protected_path.relative_to(writable_root.resolve())
                except ValueError:
                    continue
                raise ServiceConfigError(
                    f"{setting_name} cannot be inside "
                    "job-writable uploads, requests or runs directories"
                )
        if check_registry_authority:
            try:
                registry_authority = self.resolved_scoring_registry_authority
            except (FileNotFoundError, ValueError) as exc:
                raise ServiceConfigError(
                    f"scoring registry lifecycle validation failed: {exc}"
                ) from exc
            for writable_root in (
                self.uploads_dir,
                self.requests_dir,
                self.runs_dir,
            ):
                try:
                    registry_authority.path.relative_to(writable_root.resolve())
                except ValueError:
                    continue
                raise ServiceConfigError(
                    "registry lifecycle runtime_feasibility artifact cannot be inside "
                    "job-writable uploads, requests or runs directories"
                )
        if self.technique_registry is not None:
            try:
                load_technique_registry(self.technique_registry)
            except (TechniqueRegistryError, OSError) as exc:
                raise ServiceConfigError(
                    f"technique registry validation failed: {exc}"
                ) from exc
            technique_path = self.technique_registry.resolve()
            for writable_root in (self.uploads_dir, self.requests_dir, self.runs_dir):
                try:
                    technique_path.relative_to(writable_root.resolve())
                except ValueError:
                    continue
                raise ServiceConfigError(
                    "RALLYMATE_TECHNIQUE_REGISTRY cannot be inside job-writable "
                    "uploads, requests or runs directories"
                )
        _validate_web_origins(
            self.public_base_url,
            self.cors_origins,
            environment,
        )


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ServiceConfigError(
                f"{name} must be an integer >= {minimum}; got {raw!r}"
            ) from exc
    if value < minimum:
        raise ServiceConfigError(f"{name} must be >= {minimum}")
    return value


def load_settings() -> ServiceSettings:
    workspace = Path(__file__).resolve().parents[2]
    data_root = Path(
        os.getenv("RALLYMATE_DATA_ROOT", str(workspace / "service_data"))
    ).resolve()
    database = Path(
        os.getenv("RALLYMATE_DATABASE_PATH", str(data_root / "jobs.sqlite3"))
    ).resolve()
    pose_preset_name = os.getenv("RALLYMATE_POSE_PRESET") or (
        default_pose_deployment_preset_id(workspace)
    )
    pose_preset = resolve_pose_deployment_preset(pose_preset_name, workspace)
    settings = ServiceSettings(
        data_root=data_root,
        database_path=database,
        detect_model=Path(
            os.getenv(
                "RALLYMATE_DETECT_MODEL", str(workspace / "models" / "yolo26n.pt")
            )
        ).resolve(),
        pose_model=(
            pose_preset.model_path
            if pose_preset
            else Path(
                os.getenv(
                    "RALLYMATE_POSE_MODEL",
                    str(workspace / "models" / "yolo26n-pose.pt"),
                )
            ).resolve()
        ),
        pose_backend=(
            pose_preset.pose_backend
            if pose_preset
            else os.getenv("RALLYMATE_POSE_BACKEND", "yolo").lower()
        ),
        pose_runtime=(
            pose_preset.pose_runtime
            if pose_preset
            else os.getenv("RALLYMATE_POSE_RUNTIME", "pytorch").lower()
        ),
        pose_profile=(
            pose_preset.pose_profile
            if pose_preset
            else os.getenv("RALLYMATE_POSE_PROFILE", "realtime").lower()
        ),
        pose_config=(
            pose_preset.config_path
            if pose_preset
            else Path(os.environ["RALLYMATE_POSE_CONFIG"]).resolve()
            if os.getenv("RALLYMATE_POSE_CONFIG")
            else None
        ),
        pose_preset=pose_preset.preset_id if pose_preset else None,
        pose_native_keypoint_format=(
            pose_preset.native_keypoint_format
            if pose_preset
            else os.getenv("RALLYMATE_POSE_NATIVE_KEYPOINT_FORMAT") or None
        ),
        scoring_registry_lifecycle_manifest=Path(
            os.getenv(
                "RALLYMATE_SCORING_REGISTRY_LIFECYCLE_MANIFEST",
                str(workspace / "registry-lifecycle.json"),
            )
        ).resolve(),
        scoring_feasibility_registry=(
            Path(os.environ["RALLYMATE_SCORING_FEASIBILITY_REGISTRY"]).resolve()
            if os.getenv("RALLYMATE_SCORING_FEASIBILITY_REGISTRY")
            else None
        ),
        scoring_calibration_assets=tuple(
            Path(value).resolve()
            for value in os.getenv(
                "RALLYMATE_SCORING_CALIBRATION_ASSETS", ""
            ).split(os.pathsep)
            if value.strip()
        ),
        scoring_trusted_promotion_ledger=Path(
            os.getenv(
                "RALLYMATE_SCORING_TRUSTED_PROMOTION_LEDGER",
                str(
                    workspace
                    / "calibration"
                    / "trusted-calibration-promotion-ledger.json"
                ),
            )
        ).resolve(),
        scoring_trusted_runtime_profile_bindings=Path(
            os.getenv(
                "RALLYMATE_SCORING_TRUSTED_RUNTIME_PROFILE_BINDINGS",
                str(
                    workspace
                    / "calibration"
                    / "trusted-runtime-profile-bindings.json"
                ),
            )
        ).resolve(),
        scoring_runtime_view_evidence_dir=Path(
            os.getenv(
                "RALLYMATE_SCORING_RUNTIME_VIEW_EVIDENCE_DIR",
                str(workspace / "calibration" / "runtime-view-evidence"),
            )
        ).resolve(),
        technique_registry=(
            Path(os.environ["RALLYMATE_TECHNIQUE_REGISTRY"]).resolve()
            if os.getenv("RALLYMATE_TECHNIQUE_REGISTRY")
            else None
        ),
        # Environment files often leave accidental surrounding whitespace.  A
        # single canonical value keeps readiness and Bearer comparison aligned.
        api_key=(os.getenv("RALLYMATE_API_KEY") or "").strip() or None,
        cors_origins=tuple(
            origin.strip()
            for origin in os.getenv("RALLYMATE_CORS_ORIGINS", "").split(",")
            if origin.strip()
        ),
        public_base_url=os.getenv("RALLYMATE_PUBLIC_BASE_URL") or None,
        environment=os.getenv("RALLYMATE_ENVIRONMENT", "development").strip().lower(),
        model_license_ack=os.getenv(
            "RALLYMATE_MODEL_LICENSE_ACK", "development"
        ).lower(),
        device=os.getenv("RALLYMATE_DEVICE", "auto"),
        cpu_threads=_int_env("RALLYMATE_CPU_THREADS", 4),
        max_upload_bytes=_int_env(
            "RALLYMATE_MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024
        ),
        max_video_duration_seconds=_int_env(
            "RALLYMATE_MAX_VIDEO_DURATION_SECONDS", 30 * 60
        ),
        lease_seconds=_int_env("RALLYMATE_JOB_LEASE_SECONDS", 60 * 60),
        max_attempts=_int_env("RALLYMATE_MAX_ATTEMPTS", 2),
    )
    settings.validate_license()
    settings.ensure_directories()
    return settings
