"""Versioned qualitative technique registry derived from the supplied DOCX rules.

The registry deliberately stores visual definitions and evidence requirements, not
coach-calibrated thresholds.  Keeping this contract separate from the existing
GS/FS scoring registry lets us add serve, return and net actions without
silently changing the 298-card compatibility surface.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping


TECHNIQUE_REGISTRY_RESOURCE = "data/technique_metrics.json"
TECHNIQUE_REGISTRY_SCHEMA_VERSION = "1.0.0"


class TechniqueRegistryError(ValueError):
    """Raised when the technique registry cannot be trusted at runtime."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TechniqueRegistryError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise TechniqueRegistryError(f"non-finite JSON constant: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not (parsed == parsed and abs(parsed) != float("inf")):
        raise TechniqueRegistryError(f"non-finite JSON number: {value}")
    return parsed


def _decode_and_validate(raw: bytes, source: str) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
            parse_float=_parse_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TechniqueRegistryError(f"invalid technique registry JSON: {source}") from exc
    if not isinstance(payload, dict):
        raise TechniqueRegistryError("technique registry must be a JSON object")
    return _validate(payload), hashlib.sha256(raw).hexdigest()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TechniqueRegistryError(f"{label} must be a non-empty string")
    return value.strip()


def _require_string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise TechniqueRegistryError(f"{label} must be a non-empty string list")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_require_text(item, f"{label}[{index}]"))
    if len(set(result)) != len(result):
        raise TechniqueRegistryError(f"{label} contains duplicates")
    return result


def _validate(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != TECHNIQUE_REGISTRY_SCHEMA_VERSION:
        raise TechniqueRegistryError(
            "unsupported technique registry schema_version: "
            f"{payload.get('schema_version')!r}"
        )
    _require_text(payload.get("registry_id"), "registry_id")
    _require_text(payload.get("registry_version"), "registry_version")
    sources = payload.get("source_documents")
    if not isinstance(sources, list) or not sources:
        raise TechniqueRegistryError("source_documents must be a non-empty list")
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise TechniqueRegistryError(f"source_documents[{index}] must be an object")
        _require_text(source.get("file_name"), f"source_documents[{index}].file_name")
        _require_text(source.get("scope"), f"source_documents[{index}].scope")
        _require_text(
            source.get("phase_contract"),
            f"source_documents[{index}].phase_contract",
        )
        source_sha256 = source.get("source_sha256")
        if (
            not isinstance(source_sha256, str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256.strip())
        ):
            raise TechniqueRegistryError(
                f"source_documents[{index}].source_sha256 must be a 64-character hex digest"
            )

    semantics = payload.get("semantics")
    if not isinstance(semantics, Mapping):
        raise TechniqueRegistryError("semantics must be an object")
    if semantics.get("formal_coach_score") is not False:
        raise TechniqueRegistryError(
            "technique registry must keep formal_coach_score=false until calibration"
        )

    contracts = payload.get("default_phase_contracts")
    if not isinstance(contracts, Mapping):
        raise TechniqueRegistryError("default_phase_contracts must be an object")
    for family, phases in contracts.items():
        _require_text(family, "default_phase_contracts key")
        _require_string_list(phases, f"default_phase_contracts[{family}]")

    techniques = payload.get("techniques")
    if not isinstance(techniques, list) or not techniques:
        raise TechniqueRegistryError("techniques must be a non-empty list")
    ids: set[str] = set()
    families: set[str] = set()
    for index, item in enumerate(techniques):
        if not isinstance(item, Mapping):
            raise TechniqueRegistryError(f"techniques[{index}] must be an object")
        technique_id = _require_text(item.get("id"), f"techniques[{index}].id")
        if technique_id in ids:
            raise TechniqueRegistryError(f"duplicate technique id: {technique_id}")
        ids.add(technique_id)
        family = _require_text(item.get("family"), f"techniques[{index}].family")
        families.add(family)
        _require_text(item.get("name_zh"), f"techniques[{index}].name_zh")
        _require_string_list(item.get("phases"), f"techniques[{index}].phases")
        _require_string_list(
            item.get("core_visual_features"),
            f"techniques[{index}].core_visual_features",
        )
        _require_string_list(
            item.get("required_evidence"),
            f"techniques[{index}].required_evidence",
        )
        _require_string_list(
            item.get("enhanced_evidence"),
            f"techniques[{index}].enhanced_evidence",
            allow_empty=True,
        )
        reference_constraints = item.get("reference_constraints", [])
        if not isinstance(reference_constraints, list):
            raise TechniqueRegistryError(
                f"techniques[{index}].reference_constraints must be a list"
            )
        for constraint_index, constraint in enumerate(reference_constraints):
            if not isinstance(constraint, Mapping):
                raise TechniqueRegistryError(
                    "techniques[{}].reference_constraints[{}] must be an object".format(
                        index, constraint_index
                    )
                )
            for field in ("id", "kind", "unit", "source", "semantics"):
                _require_text(
                    constraint.get(field),
                    f"techniques[{index}].reference_constraints[{constraint_index}].{field}",
                )
            if "value" in constraint:
                value = constraint["value"]
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not float(value) == float(value)
                    or abs(float(value)) == float("inf")
                ):
                    raise TechniqueRegistryError(
                        "techniques[{}].reference_constraints[{}].value must be "
                        "finite or null".format(index, constraint_index)
                    )
        _require_string_list(
            item.get("proxy_limits"),
            f"techniques[{index}].proxy_limits",
        )
    if not {"baseline", "serve", "return", "net_attack", "footwork"}.issubset(families):
        raise TechniqueRegistryError("technique registry is missing a required family")
    # Return a detached, JSON-safe copy so callers cannot mutate the cache.
    return json.loads(json.dumps(payload, ensure_ascii=False))


@lru_cache(maxsize=8)
def _load_cached(source: str, raw: bytes) -> tuple[dict[str, Any], str]:
    """Decode/validate one exact byte snapshot.

    The previous cache used ``mtime_ns + size`` as its freshness key.  That is
    usually adequate for a local file, but an atomic operator update can retain
    both values (and a coarse filesystem can report the same timestamp), which
    would leave a stale qualitative registry in memory.  Keying by the bytes
    actually read also makes the payload and its SHA-256 provenance indivisible.
    ``raw`` is tiny for this registry and the cache is intentionally bounded.
    """

    return _decode_and_validate(raw, source)


def _load_source(source: Any, label: str) -> tuple[dict[str, Any], str]:
    """Read a filesystem path or an importlib Traversable resource once."""

    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise TechniqueRegistryError(f"cannot read technique registry: {label}") from exc
    return _load_cached(label, raw)


def _packaged_source() -> Any:
    """Return the package resource without coercing it to a filesystem Path.

    ``importlib.resources.files`` may return a zip-backed ``Traversable`` in a
    wheel/zipapp.  Converting that object through ``Path(str(...))`` works only
    for an unpacked source tree and breaks normal wheel installation.
    """

    return files("rallymate_scoring").joinpath(TECHNIQUE_REGISTRY_RESOURCE)


def _load_registry(path: Path | None = None) -> tuple[dict[str, Any], str]:
    if path is None:
        source = _packaged_source()
        return _load_source(source, f"rallymate_scoring/{TECHNIQUE_REGISTRY_RESOURCE}")
    resolved = Path(path).resolve()
    return _load_source(resolved, str(resolved))


def load_technique_registry(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the qualitative technique registry.

    ``path`` is optional so a deployment can pin an audited registry outside the
    writable job directory.  The packaged registry is used by default.
    """

    payload, _ = load_technique_registry_snapshot(path)
    return payload


def load_technique_registry_snapshot(
    path: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """Read, validate and hash one indivisible registry byte snapshot.

    Callers that need both the parsed payload and provenance should use this
    function rather than loading the same path twice.  In particular, queued
    job assessment must never combine payload bytes from one operator version
    with a digest read after an atomic replacement.
    """

    payload, digest = _load_registry(path)
    return json.loads(json.dumps(payload, ensure_ascii=False)), digest


def technique_registry_digest(path: Path | None = None) -> str:
    """Return the SHA-256 digest used for API provenance."""

    _, digest = load_technique_registry_snapshot(path)
    return digest


def technique_catalog(path: Path | None = None) -> dict[str, Any]:
    """Return the stable public catalog payload."""

    registry, digest = load_technique_registry_snapshot(path)
    return {
        "schema_version": registry["schema_version"],
        "registry_id": registry["registry_id"],
        "registry_version": registry["registry_version"],
        "registry_sha256": digest,
        "source_documents": registry["source_documents"],
        "semantics": registry["semantics"],
        "default_phase_contracts": registry["default_phase_contracts"],
        "techniques": registry["techniques"],
    }
