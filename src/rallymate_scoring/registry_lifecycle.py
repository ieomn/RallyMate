from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from rallymate_scoring.feasibility import validate_feasibility_registry
from rallymate_scoring.indicator_requirements import (
    canonical_sha256,
    validate_indicator_requirements_snapshot,
)


REGISTRY_LIFECYCLE_SCHEMA_VERSION = "1.0.0"
REGISTRY_LIFECYCLE_ARTIFACT_SCOPE = "registry_lifecycle_authority"
DEFAULT_REGISTRY_LIFECYCLE_PATH = (
    Path(__file__).resolve().parents[2] / "registry-lifecycle.json"
)

RegistryRole = Literal[
    "runtime_feasibility",
    "current_scoring_requirements",
]

_ROLE_NAMES = frozenset(
    {
        "runtime_feasibility",
        "current_scoring_requirements",
    }
)
_NON_RUNTIME_NAMES = frozenset(
    {
        "historical_feasibility",
        "measurement_plans",
    }
)
_HEX_LOWER = frozenset("0123456789abcdef")


class RegistryLifecycleError(ValueError):
    """Raised when the fixed registry lifecycle authority is not trustworthy."""


@dataclass(frozen=True)
class ResolvedRegistryArtifact:
    """A content-addressed artifact parsed from the exact verified file bytes.

    Consumers must use ``payload`` (or ``raw_bytes`` if they need the original
    representation) instead of reopening ``path`` after verification.
    """

    authority_version: str
    manifest_path: Path
    manifest_sha256: str
    authority_slot: str
    role: RegistryRole | None
    kind: str
    lifecycle: str
    relative_path: str
    path: Path
    file_sha256: str
    embedded_version: str
    source_registry_version: str | None
    payload: dict[str, Any]
    raw_bytes: bytes = field(repr=False)


_SLOT_SPECS: dict[tuple[str, str], tuple[str, str, bool]] = {
    ("roles", "runtime_feasibility"): (
        "metric_feasibility_registry",
        "current",
        True,
    ),
    ("roles", "current_scoring_requirements"): (
        "indicator_scoring_requirements",
        "derived_current",
        True,
    ),
    ("non_runtime_artifacts", "historical_feasibility"): (
        "metric_feasibility_registry",
        "historical",
        False,
    ),
    ("non_runtime_artifacts", "measurement_plans"): (
        "metric_measurement_plans",
        "planning_only",
        True,
    ),
}


def _exact_keys(value: dict[str, Any], expected: set[str], field_name: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing:
        raise RegistryLifecycleError(
            f"{field_name} missing fields: {sorted(missing)}"
        )
    if extra:
        raise RegistryLifecycleError(
            f"{field_name} has unsupported fields: {sorted(extra)}"
        )


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryLifecycleError(f"{field_name} must be a non-empty string")
    return value


def _sha256(value: Any, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX_LOWER for character in value)
    ):
        raise RegistryLifecycleError(
            f"{field_name} must be a lowercase 64-character SHA-256"
        )
    return value


def _relative_path(value: Any, field_name: str) -> str:
    text = _non_empty_string(value, field_name)
    if "\\" in text:
        raise RegistryLifecycleError(
            f"{field_name} must use a portable forward-slash relative path"
        )
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    raw_parts = text.split("/")
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or any(part in {"", ".", ".."} for part in raw_parts)
        or posix.as_posix() != text
    ):
        raise RegistryLifecycleError(
            f"{field_name} must stay under the lifecycle manifest root"
        )
    return text


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryLifecycleError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise RegistryLifecycleError(f"non-finite JSON constant: {value}")


def _parse_finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RegistryLifecycleError(f"non-finite JSON number: {value}")
    return result


def _validate_canonical_utf8(value: Any, field_name: str) -> None:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RegistryLifecycleError(
                f"{field_name} contains a string that cannot be encoded as UTF-8"
            ) from exc
        return
    if isinstance(value, list):
        for item in value:
            _validate_canonical_utf8(item, field_name)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_canonical_utf8(key, field_name)
            _validate_canonical_utf8(item, field_name)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise RegistryLifecycleError(f"{field_name} contains a non-finite number")


def _parse_json_bytes(raw_bytes: bytes, field_name: str) -> dict[str, Any]:
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegistryLifecycleError(f"{field_name} must be UTF-8 JSON") from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
            parse_float=_parse_finite_float,
        )
    except json.JSONDecodeError as exc:
        raise RegistryLifecycleError(f"invalid {field_name} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistryLifecycleError(f"{field_name} must be a JSON object")
    _validate_canonical_utf8(payload, field_name)
    return payload


def _validate_binding(
    binding: Any,
    *,
    section: str,
    name: str,
) -> dict[str, Any]:
    field_name = f"registry_lifecycle.{section}.{name}"
    if not isinstance(binding, dict):
        raise RegistryLifecycleError(f"{field_name} must be an object")
    expected_kind, expected_lifecycle, has_source = _SLOT_SPECS[(section, name)]
    expected_fields = {
        "kind",
        "lifecycle",
        "relative_path",
        "file_sha256",
        "embedded_version",
    }
    if has_source:
        expected_fields.add("source_registry_version")
    _exact_keys(binding, expected_fields, field_name)
    if binding["kind"] != expected_kind:
        raise RegistryLifecycleError(
            f"{field_name}.kind must be {expected_kind}"
        )
    if binding["lifecycle"] != expected_lifecycle:
        raise RegistryLifecycleError(
            f"{field_name}.lifecycle must be {expected_lifecycle}"
        )
    _relative_path(binding["relative_path"], f"{field_name}.relative_path")
    _sha256(binding["file_sha256"], f"{field_name}.file_sha256")
    _non_empty_string(
        binding["embedded_version"], f"{field_name}.embedded_version"
    )
    if has_source:
        _non_empty_string(
            binding["source_registry_version"],
            f"{field_name}.source_registry_version",
        )
    return binding


def _validate_lifecycle_manifest(payload: dict[str, Any]) -> None:
    _exact_keys(
        payload,
        {
            "schema_version",
            "artifact_scope",
            "authority_version",
            "roles",
            "non_runtime_artifacts",
        },
        "registry_lifecycle",
    )
    if payload["schema_version"] != REGISTRY_LIFECYCLE_SCHEMA_VERSION:
        raise RegistryLifecycleError("unsupported registry lifecycle schema_version")
    if payload["artifact_scope"] != REGISTRY_LIFECYCLE_ARTIFACT_SCOPE:
        raise RegistryLifecycleError("registry lifecycle artifact_scope is invalid")
    _non_empty_string(
        payload["authority_version"], "registry_lifecycle.authority_version"
    )

    roles = payload["roles"]
    if not isinstance(roles, dict):
        raise RegistryLifecycleError("registry_lifecycle.roles must be an object")
    _exact_keys(roles, set(_ROLE_NAMES), "registry_lifecycle.roles")
    non_runtime = payload["non_runtime_artifacts"]
    if not isinstance(non_runtime, dict):
        raise RegistryLifecycleError(
            "registry_lifecycle.non_runtime_artifacts must be an object"
        )
    _exact_keys(
        non_runtime,
        set(_NON_RUNTIME_NAMES),
        "registry_lifecycle.non_runtime_artifacts",
    )

    bindings: list[dict[str, Any]] = []
    for section, names in (
        ("roles", _ROLE_NAMES),
        ("non_runtime_artifacts", _NON_RUNTIME_NAMES),
    ):
        container = payload[section]
        for name in sorted(names):
            bindings.append(
                _validate_binding(container[name], section=section, name=name)
            )

    paths = [binding["relative_path"] for binding in bindings]
    if len(paths) != len(set(paths)):
        raise RegistryLifecycleError(
            "registry lifecycle fixed artifact paths must be unique"
        )

    runtime = roles["runtime_feasibility"]
    requirements = roles["current_scoring_requirements"]
    historical = non_runtime["historical_feasibility"]
    if runtime["source_registry_version"] != historical["embedded_version"]:
        raise RegistryLifecycleError(
            "runtime feasibility source must be the registered historical feasibility version"
        )
    if requirements["source_registry_version"] != runtime["embedded_version"]:
        raise RegistryLifecycleError(
            "current scoring requirements must derive from runtime feasibility"
        )


def _read_manifest(
    manifest_path: str | Path,
) -> tuple[Path, str, dict[str, Any]]:
    try:
        resolved = Path(manifest_path).resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"registry lifecycle manifest is missing: {Path(manifest_path)}"
        ) from exc
    if not resolved.is_file():
        raise RegistryLifecycleError(
            f"registry lifecycle manifest is not a file: {resolved}"
        )
    raw_bytes = resolved.read_bytes()
    manifest_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    payload = _parse_json_bytes(raw_bytes, "registry lifecycle manifest")
    _validate_lifecycle_manifest(payload)
    return resolved, manifest_sha256, payload


def load_registry_lifecycle(
    manifest_path: str | Path = DEFAULT_REGISTRY_LIFECYCLE_PATH,
) -> dict[str, Any]:
    """Load and strictly validate the closed lifecycle manifest structure.

    This function validates the authority document only. Use
    :func:`validate_registry_lifecycle_artifacts` in CI to verify all four
    referenced files, or a role resolver in a production consumer.
    """

    _, _, payload = _read_manifest(manifest_path)
    return payload


def _resolve_under_manifest_root(root: Path, relative_path: str) -> Path:
    normalized = _relative_path(relative_path, "registry artifact relative_path")
    parts = PurePosixPath(normalized).parts
    unresolved = root.joinpath(*parts)
    try:
        resolved = unresolved.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"registry lifecycle artifact is missing: {unresolved}"
        ) from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RegistryLifecycleError(
            "registry lifecycle artifact escapes the manifest root"
        ) from exc
    if not resolved.is_file():
        raise RegistryLifecycleError(
            f"registry lifecycle artifact is not a file: {resolved}"
        )
    return resolved


def _embedded_identity(
    payload: dict[str, Any], kind: str
) -> tuple[Any, Any | None]:
    if kind == "metric_feasibility_registry":
        source = payload.get("scope")
        return (
            payload.get("registry_version"),
            source.get("extends_registry") if isinstance(source, dict) else None,
        )
    if kind == "indicator_scoring_requirements":
        source = payload.get("source")
        return (
            payload.get("requirements_version"),
            source.get("registry_version") if isinstance(source, dict) else None,
        )
    if kind == "metric_measurement_plans":
        return (
            payload.get("registry_version"),
            payload.get("source_feasibility_registry_version"),
        )
    raise RegistryLifecycleError(f"unsupported registry lifecycle kind: {kind}")


def _validate_artifact_payload(payload: dict[str, Any], kind: str) -> None:
    if kind == "metric_feasibility_registry":
        try:
            validate_feasibility_registry(payload)
        except ValueError as exc:
            raise RegistryLifecycleError(
                f"invalid metric feasibility registry: {exc}"
            ) from exc
        return
    if kind == "indicator_scoring_requirements":
        try:
            validate_indicator_requirements_snapshot(payload)
        except ValueError as exc:
            raise RegistryLifecycleError(
                f"invalid indicator scoring requirements: {exc}"
            ) from exc
        return
    if kind == "metric_measurement_plans":
        if payload.get("schema_version") != "1.0.0":
            raise RegistryLifecycleError(
                "unsupported metric measurement plans schema_version"
            )
        if not isinstance(payload.get("plans"), list) or not payload["plans"]:
            raise RegistryLifecycleError(
                "metric measurement plans must contain a non-empty plans list"
            )
        return
    raise RegistryLifecycleError(f"unsupported registry lifecycle kind: {kind}")


def _resolve_binding_artifact(
    *,
    manifest_path: Path,
    manifest_sha256: str,
    manifest: dict[str, Any],
    section: str,
    name: str,
) -> ResolvedRegistryArtifact:
    binding = manifest[section][name]
    path = _resolve_under_manifest_root(
        manifest_path.parent, binding["relative_path"]
    )

    # This is the sole target-file read. Hashing and parsing both use these
    # exact bytes, and callers receive the parsed payload without reopening.
    raw_bytes = path.read_bytes()
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha256 != binding["file_sha256"]:
        raise RegistryLifecycleError(
            f"{section}.{name} raw file SHA-256 mismatch: "
            f"expected {binding['file_sha256']}, got {actual_sha256}"
        )
    payload = _parse_json_bytes(raw_bytes, f"{section}.{name} artifact")
    _validate_artifact_payload(payload, binding["kind"])

    embedded_version, source_registry_version = _embedded_identity(
        payload, binding["kind"]
    )
    if embedded_version != binding["embedded_version"]:
        raise RegistryLifecycleError(
            f"{section}.{name} embedded version mismatch: "
            f"expected {binding['embedded_version']}, got {embedded_version}"
        )
    expected_source = binding.get("source_registry_version")
    if expected_source is not None and source_registry_version != expected_source:
        raise RegistryLifecycleError(
            f"{section}.{name} source registry version mismatch: "
            f"expected {expected_source}, got {source_registry_version}"
        )

    role: RegistryRole | None = None
    if section == "roles":
        role = name  # type: ignore[assignment]
    return ResolvedRegistryArtifact(
        authority_version=manifest["authority_version"],
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        authority_slot=f"{section}.{name}",
        role=role,
        kind=binding["kind"],
        lifecycle=binding["lifecycle"],
        relative_path=binding["relative_path"],
        path=path,
        file_sha256=actual_sha256,
        embedded_version=embedded_version,
        source_registry_version=source_registry_version,
        payload=payload,
        raw_bytes=raw_bytes,
    )


def resolve_registry_role(
    manifest_path: str | Path,
    role: RegistryRole,
) -> ResolvedRegistryArtifact:
    """Resolve one fixed production role to exact verified bytes and JSON.

    Historical and planning-only artifacts are deliberately not addressable
    through this API. There is no fallback, catalog scan, or ``latest`` rule.
    """

    if role not in _ROLE_NAMES:
        raise RegistryLifecycleError(f"unsupported registry lifecycle role: {role}")
    resolved_manifest_path, manifest_sha256, manifest = _read_manifest(manifest_path)
    return _resolve_binding_artifact(
        manifest_path=resolved_manifest_path,
        manifest_sha256=manifest_sha256,
        manifest=manifest,
        section="roles",
        name=role,
    )


def resolve_runtime_feasibility_registry(
    manifest_path: str | Path = DEFAULT_REGISTRY_LIFECYCLE_PATH,
) -> ResolvedRegistryArtifact:
    return resolve_registry_role(manifest_path, "runtime_feasibility")


def resolve_current_scoring_requirements(
    manifest_path: str | Path = DEFAULT_REGISTRY_LIFECYCLE_PATH,
) -> ResolvedRegistryArtifact:
    return resolve_registry_role(manifest_path, "current_scoring_requirements")


def validate_registry_lifecycle_artifacts(
    manifest_path: str | Path = DEFAULT_REGISTRY_LIFECYCLE_PATH,
) -> dict[str, ResolvedRegistryArtifact]:
    """CI audit of every fixed role and explicitly retained root artifact."""

    resolved_manifest_path, manifest_sha256, manifest = _read_manifest(manifest_path)
    resolved: dict[str, ResolvedRegistryArtifact] = {}
    for section, names in (
        ("roles", sorted(_ROLE_NAMES)),
        ("non_runtime_artifacts", sorted(_NON_RUNTIME_NAMES)),
    ):
        for name in names:
            key = f"{section}.{name}"
            resolved[key] = _resolve_binding_artifact(
                manifest_path=resolved_manifest_path,
                manifest_sha256=manifest_sha256,
                manifest=manifest,
                section=section,
                name=name,
            )

    feasibility = resolved["roles.runtime_feasibility"]
    requirements = resolved["roles.current_scoring_requirements"]
    requirements_source = requirements.payload.get("source")
    if (
        not isinstance(requirements_source, dict)
        or requirements_source.get("registry_content_sha256")
        != canonical_sha256(feasibility.payload)
    ):
        raise RegistryLifecycleError(
            "current scoring requirements content binding does not match runtime feasibility"
        )
    return resolved


__all__ = [
    "DEFAULT_REGISTRY_LIFECYCLE_PATH",
    "REGISTRY_LIFECYCLE_ARTIFACT_SCOPE",
    "REGISTRY_LIFECYCLE_SCHEMA_VERSION",
    "RegistryLifecycleError",
    "RegistryRole",
    "ResolvedRegistryArtifact",
    "load_registry_lifecycle",
    "resolve_current_scoring_requirements",
    "resolve_registry_role",
    "resolve_runtime_feasibility_registry",
    "validate_registry_lifecycle_artifacts",
]
