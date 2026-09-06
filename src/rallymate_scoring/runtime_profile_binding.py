from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any

from rallymate_events import PHASE_CANDIDATE_VERSION
from rallymate_scoring.calibration_fitting import canonical_sha256
from rallymate_scoring.calibration_promotion import TrustedProductionCalibration
from rallymate_scoring.feasibility import validate_feasibility_registry


RUNTIME_SCORING_PROFILE_SCHEMA_VERSION = "1.0.0"
RUNTIME_SCORING_PROFILE_VERSION = "runtime-scoring-profile-v1"
RUNTIME_VIEW_EVIDENCE_SCHEMA_VERSION = "1.0.0"
TRUSTED_RUNTIME_BINDING_REGISTRY_SCHEMA_VERSION = "1.0.0"
TRUSTED_RUNTIME_BINDING_REGISTRY_SCOPE = (
    "trusted_calibration_runtime_profile_bindings"
)
CANONICALIZATION = "rallymate-canonical-json-v1"
_HEX = frozenset("0123456789abcdefABCDEF")
_RUNTIME_BOUND_TOKEN = object()


class RuntimeProfileBindingError(ValueError):
    """Raised when a production calibration/runtime profile fails closed."""


class RuntimeProfileBoundProductionCalibration(Mapping[str, Any]):
    """Ledger-, registry- and runtime-profile-authorized calibration.

    The wrapper is runtime-only and intentionally cannot be JSON serialized.
    Reloading the underlying calibration requires rechecking all three trusted
    allow-lists/evidence inputs for the current video.
    """

    __slots__ = ("_calibration", "_authorization")

    def __init__(
        self,
        calibration: TrustedProductionCalibration,
        authorization: dict[str, Any],
        *,
        _token: object,
    ) -> None:
        if _token is not _RUNTIME_BOUND_TOKEN:
            raise RuntimeProfileBindingError(
                "RuntimeProfileBoundProductionCalibration must be created by "
                "trusted runtime-profile verification"
            )
        self._calibration = calibration
        self._authorization = deepcopy(authorization)

    def __getitem__(self, key: str) -> Any:
        return self._calibration[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._calibration)

    def __len__(self) -> int:
        return len(self._calibration)

    @property
    def authorization(self) -> dict[str, Any]:
        return deepcopy(self._authorization)


def is_runtime_profile_bound_calibration(value: Any) -> bool:
    return isinstance(value, RuntimeProfileBoundProductionCalibration)


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeProfileBindingError(f"{field} must be an object")
    return value


def _exact_keys(
    value: dict[str, Any], required: set[str], field: str, *, optional: set[str] | None = None
) -> None:
    optional = optional or set()
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing:
        raise RuntimeProfileBindingError(f"{field} missing fields: {sorted(missing)}")
    if extra:
        raise RuntimeProfileBindingError(f"{field} has unsupported fields: {sorted(extra)}")


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeProfileBindingError(f"{field} must be a non-empty string")
    return value


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise RuntimeProfileBindingError(f"{field} must be a 64-character SHA-256")
    return value.lower()


def _timestamp(value: Any, field: str) -> datetime:
    text = _string(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeProfileBindingError(
            f"{field} must be a valid ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeProfileBindingError(f"{field} must include a timezone")
    return parsed


def _unique_strings(value: Any, field: str, *, non_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeProfileBindingError(f"{field} must be an array")
    if non_empty and not value:
        raise RuntimeProfileBindingError(f"{field} must not be empty")
    if any(not isinstance(item, str) or not item for item in value):
        raise RuntimeProfileBindingError(f"{field} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise RuntimeProfileBindingError(f"{field} must contain unique values")
    return list(value)


def _calibration_version(calibration: Mapping[str, Any]) -> str:
    value = calibration.get("threshold_version") or calibration.get("model_version")
    return _string(value, "calibration.version")


def _indicator_runtime_version(
    model_versions: Mapping[str, Any],
    field: str,
    indicator_id: str,
) -> Any:
    """Resolve an observed version that may be scoped per indicator.

    Indicator definitions and feature contracts are not necessarily uniform
    across a registry.  Runtime summaries therefore carry those two fields as
    ``indicator_id -> version`` mappings, while shared event/Track contracts
    remain scalar strings.  Accepting only an exact mapping entry prevents one
    indicator's reviewed profile from authorizing another indicator.
    """

    value = model_versions.get(field)
    if isinstance(value, Mapping):
        return value.get(indicator_id)
    return value


def _find_indicator(
    feasibility_registry: dict[str, Any], indicator_id: str
) -> dict[str, Any]:
    try:
        validate_feasibility_registry(feasibility_registry)
    except ValueError as exc:
        raise RuntimeProfileBindingError(
            f"invalid feasibility registry: {exc}"
        ) from exc
    matches = [
        item
        for item in feasibility_registry["indicators"]
        if item.get("indicator_id") == indicator_id
    ]
    if len(matches) != 1:
        raise RuntimeProfileBindingError(
            "feasibility registry must contain the calibration indicator exactly once"
        )
    if matches[0].get("feasibility_level") != "F4":
        raise RuntimeProfileBindingError(
            f"runtime indicator {indicator_id} must remain F4"
        )
    return matches[0]


def _validate_pose_profile(pose: dict[str, Any], field: str) -> None:
    required = {
        "backend",
        "runtime",
        "profile",
        "model_sha256",
        "native_keypoint_format",
        "native_keypoint_count",
        "keypoint_schema_version",
    }
    _exact_keys(pose, required, field)
    for name in required - {"model_sha256", "native_keypoint_count"}:
        _string(pose[name], f"{field}.{name}")
    _sha256(pose["model_sha256"], f"{field}.model_sha256")
    count = pose["native_keypoint_count"]
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise RuntimeProfileBindingError(
            f"{field}.native_keypoint_count must be a positive integer"
        )


def _validate_pipeline_profile(pipeline: dict[str, Any], field: str) -> None:
    required = {
        "indicator_definition",
        "event_contract",
        "event_detector",
        "phase_contract",
        "primary_player",
        "quality_policy",
        "feature_contract",
    }
    _exact_keys(pipeline, required, field)
    for name in required:
        _string(pipeline[name], f"{field}.{name}")


def _validate_view_profile(view: dict[str, Any], field: str) -> None:
    required = {
        "view_profile_id",
        "view_group",
        "required_constraints",
        "verification_protocol_version",
    }
    _exact_keys(view, required, field)
    for name in required - {"required_constraints"}:
        _string(view[name], f"{field}.{name}")
    _unique_strings(view["required_constraints"], f"{field}.required_constraints")


def validate_runtime_scoring_profile(profile: dict[str, Any]) -> None:
    profile = _require_object(profile, "runtime_profile")
    required = {
        "schema_version",
        "profile_version",
        "profile_id",
        "pose",
        "pipeline",
        "view",
        "canonicalization",
    }
    _exact_keys(profile, required, "runtime_profile")
    if profile["schema_version"] != RUNTIME_SCORING_PROFILE_SCHEMA_VERSION:
        raise RuntimeProfileBindingError("unsupported runtime profile schema_version")
    if profile["profile_version"] != RUNTIME_SCORING_PROFILE_VERSION:
        raise RuntimeProfileBindingError("unsupported runtime profile_version")
    _string(profile["profile_id"], "runtime_profile.profile_id")
    if profile["canonicalization"] != CANONICALIZATION:
        raise RuntimeProfileBindingError("runtime profile canonicalization is invalid")
    _validate_pose_profile(
        _require_object(profile["pose"], "runtime_profile.pose"),
        "runtime_profile.pose",
    )
    _validate_pipeline_profile(
        _require_object(profile["pipeline"], "runtime_profile.pipeline"),
        "runtime_profile.pipeline",
    )
    _validate_view_profile(
        _require_object(profile["view"], "runtime_profile.view"),
        "runtime_profile.view",
    )


def build_runtime_scoring_profile(
    *,
    model_versions: Mapping[str, Any],
    indicator: dict[str, Any],
    profile_id: str,
    view_profile_id: str,
    view_group: str,
    verification_protocol_version: str,
) -> dict[str, Any]:
    """Build an exact static deployment profile from observed runtime facts.

    ``view_group`` is a declared capture class. A separate, accepted view
    evidence record is still mandatory for each video; this helper never
    treats a declaration as visual correctness evidence.
    """

    versions = _require_object(indicator.get("versions"), "indicator.versions")
    required_versions = {
        "indicator_definition",
        "event_contract",
        "event_detector",
        "phase_contract",
        "primary_player",
        "quality_policy",
        "feature_contract",
    }
    missing = required_versions - set(versions)
    if missing:
        raise RuntimeProfileBindingError(
            f"indicator.versions missing runtime bindings: {sorted(missing)}"
        )
    indicator_id = _string(indicator.get("indicator_id"), "indicator.indicator_id")
    observed_pipeline = {
        "indicator_definition": _indicator_runtime_version(
            model_versions, "indicator_definition", indicator_id
        ),
        "event_contract": model_versions.get("event_contract"),
        "event_detector": model_versions.get("event"),
        "phase_contract": model_versions.get("phase_contract"),
        "primary_player": model_versions.get("primary_player"),
        "quality_policy": model_versions.get("quality_policy"),
        "feature_contract": _indicator_runtime_version(
            model_versions, "feature_contract", indicator_id
        ),
    }
    for name, actual in observed_pipeline.items():
        expected = versions[name]
        if actual != expected:
            raise RuntimeProfileBindingError(
                f"runtime {name} does not match F4 registry: "
                f"runtime={actual!r}, expected={expected!r}"
            )
    pose = {
        "backend": model_versions.get("pose_backend"),
        "runtime": model_versions.get("pose_runtime"),
        "profile": model_versions.get("pose_profile"),
        "model_sha256": model_versions.get("pose_model_sha256"),
        "native_keypoint_format": model_versions.get("native_keypoint_format"),
        "native_keypoint_count": model_versions.get("native_keypoint_count"),
        "keypoint_schema_version": model_versions.get("keypoint_schema_version"),
    }
    profile = {
        "schema_version": RUNTIME_SCORING_PROFILE_SCHEMA_VERSION,
        "profile_version": RUNTIME_SCORING_PROFILE_VERSION,
        "profile_id": _string(profile_id, "profile_id"),
        "pose": pose,
        "pipeline": dict(observed_pipeline),
        "view": {
            "view_profile_id": _string(view_profile_id, "view_profile_id"),
            "view_group": _string(view_group, "view_group"),
            "required_constraints": list(indicator.get("view_constraints", [])),
            "verification_protocol_version": _string(
                verification_protocol_version,
                "verification_protocol_version",
            ),
        },
        "canonicalization": CANONICALIZATION,
    }
    validate_runtime_scoring_profile(profile)
    return profile


def validate_runtime_view_evidence(evidence: dict[str, Any]) -> None:
    evidence = _require_object(evidence, "view_evidence")
    required = {
        "schema_version",
        "artifact_scope",
        "evidence_id",
        "video_id",
        "video_sha256",
        "view_profile_id",
        "view_group",
        "verification_protocol_version",
        "satisfied_constraints",
        "status",
        "verifier_id",
        "verified_at",
        "source_sha256",
        "canonicalization",
    }
    _exact_keys(evidence, required, "view_evidence")
    if evidence["schema_version"] != RUNTIME_VIEW_EVIDENCE_SCHEMA_VERSION:
        raise RuntimeProfileBindingError(
            "unsupported runtime view evidence schema_version"
        )
    if evidence["artifact_scope"] != "runtime_view_evidence":
        raise RuntimeProfileBindingError("runtime view evidence artifact_scope is invalid")
    for name in (
        "evidence_id",
        "video_id",
        "view_profile_id",
        "view_group",
        "verification_protocol_version",
        "verifier_id",
    ):
        _string(evidence[name], f"view_evidence.{name}")
    _sha256(evidence["video_sha256"], "view_evidence.video_sha256")
    _sha256(evidence["source_sha256"], "view_evidence.source_sha256")
    _timestamp(evidence["verified_at"], "view_evidence.verified_at")
    _unique_strings(
        evidence["satisfied_constraints"],
        "view_evidence.satisfied_constraints",
    )
    if evidence["status"] != "accepted":
        raise RuntimeProfileBindingError(
            "runtime view evidence must be explicitly accepted"
        )
    if evidence["canonicalization"] != CANONICALIZATION:
        raise RuntimeProfileBindingError(
            "runtime view evidence canonicalization is invalid"
        )


def _validate_binding_entry(entry: dict[str, Any], index: int) -> None:
    field = f"binding_registry.entries[{index}]"
    required = {
        "binding_id",
        "status",
        "indicator_id",
        "backend",
        "calibration_version",
        "calibration_asset_sha256",
        "trusted_promotion",
        "feasibility_registry",
        "runtime_profile",
        "runtime_profile_sha256",
        "compatibility_review",
        "registered_at",
    }
    _exact_keys(entry, required, field)
    for name in ("binding_id", "indicator_id", "calibration_version"):
        _string(entry[name], f"{field}.{name}")
    if entry["status"] not in {"active", "revoked"}:
        raise RuntimeProfileBindingError(f"{field}.status is invalid")
    if entry["backend"] not in {"threshold_rule", "ordinal_regression"}:
        raise RuntimeProfileBindingError(f"{field}.backend is invalid")
    _sha256(entry["calibration_asset_sha256"], f"{field}.calibration_asset_sha256")
    _timestamp(entry["registered_at"], f"{field}.registered_at")

    promotion = _require_object(entry["trusted_promotion"], f"{field}.trusted_promotion")
    _exact_keys(
        promotion,
        {"ledger_id", "ledger_version", "entry_id"},
        f"{field}.trusted_promotion",
    )
    for name in promotion:
        _string(promotion[name], f"{field}.trusted_promotion.{name}")

    registry = _require_object(
        entry["feasibility_registry"], f"{field}.feasibility_registry"
    )
    _exact_keys(
        registry,
        {"registry_version", "content_sha256"},
        f"{field}.feasibility_registry",
    )
    _string(registry["registry_version"], f"{field}.feasibility_registry.registry_version")
    _sha256(registry["content_sha256"], f"{field}.feasibility_registry.content_sha256")

    profile = _require_object(entry["runtime_profile"], f"{field}.runtime_profile")
    validate_runtime_scoring_profile(profile)
    expected_profile_sha = canonical_sha256(profile)
    if _sha256(
        entry["runtime_profile_sha256"], f"{field}.runtime_profile_sha256"
    ) != expected_profile_sha:
        raise RuntimeProfileBindingError(
            f"{field}.runtime_profile_sha256 does not match runtime_profile"
        )

    review = _require_object(
        entry["compatibility_review"], f"{field}.compatibility_review"
    )
    _exact_keys(
        review,
        {"review_id", "reviewer_id", "source_sha256", "reviewed_at"},
        f"{field}.compatibility_review",
    )
    for name in ("review_id", "reviewer_id"):
        _string(review[name], f"{field}.compatibility_review.{name}")
    _sha256(review["source_sha256"], f"{field}.compatibility_review.source_sha256")
    reviewed_at = _timestamp(
        review["reviewed_at"], f"{field}.compatibility_review.reviewed_at"
    )
    registered_at = _timestamp(entry["registered_at"], f"{field}.registered_at")
    if reviewed_at > registered_at:
        raise RuntimeProfileBindingError(
            f"{field} compatibility review must not postdate registration"
        )


def validate_trusted_runtime_binding_registry(registry: dict[str, Any]) -> None:
    registry = _require_object(registry, "binding_registry")
    required = {
        "schema_version",
        "artifact_scope",
        "registry_id",
        "registry_version",
        "source",
        "registered_at",
        "canonicalization",
        "entries",
    }
    _exact_keys(registry, required, "binding_registry")
    if registry["schema_version"] != TRUSTED_RUNTIME_BINDING_REGISTRY_SCHEMA_VERSION:
        raise RuntimeProfileBindingError(
            "unsupported trusted runtime binding registry schema_version"
        )
    if registry["artifact_scope"] != TRUSTED_RUNTIME_BINDING_REGISTRY_SCOPE:
        raise RuntimeProfileBindingError(
            "trusted runtime binding registry artifact_scope is invalid"
        )
    for name in ("registry_id", "registry_version"):
        _string(registry[name], f"binding_registry.{name}")
    registered_at = _timestamp(registry["registered_at"], "binding_registry.registered_at")
    if registry["canonicalization"] != CANONICALIZATION:
        raise RuntimeProfileBindingError(
            "trusted runtime binding registry canonicalization is invalid"
        )
    source = _require_object(registry["source"], "binding_registry.source")
    _exact_keys(
        source,
        {"authority_id", "source_sha256", "registered_at"},
        "binding_registry.source",
    )
    _string(source["authority_id"], "binding_registry.source.authority_id")
    _sha256(source["source_sha256"], "binding_registry.source.source_sha256")
    source_time = _timestamp(source["registered_at"], "binding_registry.source.registered_at")
    if source_time > registered_at:
        raise RuntimeProfileBindingError(
            "binding registry source must not postdate registry registration"
        )
    entries = registry["entries"]
    if not isinstance(entries, list):
        raise RuntimeProfileBindingError("binding_registry.entries must be an array")
    for index, entry in enumerate(entries):
        _validate_binding_entry(_require_object(entry, f"binding_registry.entries[{index}]"), index)
        if _timestamp(entry["registered_at"], f"binding_registry.entries[{index}].registered_at") > registered_at:
            raise RuntimeProfileBindingError(
                "binding entry must not postdate registry registration"
            )
    binding_ids = [entry["binding_id"] for entry in entries]
    if len(binding_ids) != len(set(binding_ids)):
        raise RuntimeProfileBindingError("binding_registry binding_id values must be unique")


def _profile_matches_indicator(
    profile: dict[str, Any], indicator: dict[str, Any]
) -> None:
    versions = indicator["versions"]
    expected_pipeline = {
        name: versions[name]
        for name in (
            "indicator_definition",
            "event_contract",
            "event_detector",
            "phase_contract",
            "primary_player",
            "quality_policy",
            "feature_contract",
        )
    }
    if profile["pipeline"] != expected_pipeline:
        raise RuntimeProfileBindingError(
            "runtime profile pipeline versions do not exactly match F4 registry"
        )
    if profile["view"]["required_constraints"] != indicator["view_constraints"]:
        raise RuntimeProfileBindingError(
            "runtime profile view constraints do not exactly match F4 registry"
        )


def bind_trusted_production_calibration_to_runtime_profile(
    calibration: TrustedProductionCalibration,
    *,
    binding_registry: dict[str, Any],
    runtime_profile: dict[str, Any],
    view_evidence: dict[str, Any],
    feasibility_registry: dict[str, Any],
) -> RuntimeProfileBoundProductionCalibration:
    """Authorize one immutable calibration for one exact runtime/video view.

    This sidecar gate does not modify the calibration asset contract. It binds
    the existing asset hash and ledger authorization to an operator-reviewed
    deployment profile, then binds that profile to accepted per-video view
    evidence. Any mismatch fails before A--E can be emitted.
    """

    if not isinstance(calibration, TrustedProductionCalibration):
        raise RuntimeProfileBindingError(
            "runtime profile binding requires a ledger-authorized calibration"
        )
    authorization = calibration.authorization
    if authorization.get("runtime_registry_binding_verified") is not True:
        raise RuntimeProfileBindingError(
            "runtime profile binding requires exact feasibility registry binding"
        )
    validate_trusted_runtime_binding_registry(binding_registry)
    validate_runtime_scoring_profile(runtime_profile)
    validate_runtime_view_evidence(view_evidence)

    indicator_id = str(calibration["indicator_id"])
    indicator = _find_indicator(feasibility_registry, indicator_id)
    registry_sha = canonical_sha256(feasibility_registry)
    if (
        authorization.get("runtime_registry_version")
        != feasibility_registry["registry_version"]
        or authorization.get("runtime_registry_content_sha256") != registry_sha
    ):
        raise RuntimeProfileBindingError(
            "runtime profile feasibility registry differs from calibration authorization"
        )
    _profile_matches_indicator(runtime_profile, indicator)

    view = runtime_profile["view"]
    for field in (
        "view_profile_id",
        "view_group",
        "verification_protocol_version",
    ):
        if view_evidence[field] != view[field]:
            raise RuntimeProfileBindingError(
                f"runtime view evidence {field} does not match runtime profile"
            )
    if not set(view["required_constraints"]).issubset(
        view_evidence["satisfied_constraints"]
    ):
        raise RuntimeProfileBindingError(
            "runtime view evidence is missing one or more exact F4 view constraints"
        )

    asset_sha = canonical_sha256(dict(calibration))
    version = _calibration_version(calibration)
    profile_sha = canonical_sha256(runtime_profile)
    expected = {
        "indicator_id": indicator_id,
        "backend": calibration["backend"],
        "calibration_version": version,
        "calibration_asset_sha256": asset_sha,
        "trusted_promotion": {
            "ledger_id": authorization["ledger_id"],
            "ledger_version": authorization["ledger_version"],
            "entry_id": authorization["entry_id"],
        },
        "feasibility_registry": {
            "registry_version": feasibility_registry["registry_version"],
            "content_sha256": registry_sha,
        },
        "runtime_profile_sha256": profile_sha,
    }
    matches = []
    for entry in binding_registry["entries"]:
        if entry["status"] != "active":
            continue
        if all(entry[field] == value for field, value in expected.items()):
            matches.append(entry)
    if len(matches) != 1:
        raise RuntimeProfileBindingError(
            "production calibration has no unique active exact runtime-profile binding"
        )
    entry = matches[0]
    if entry["runtime_profile"] != runtime_profile:
        raise RuntimeProfileBindingError(
            "runtime profile payload differs despite matching content hash"
        )

    bound_authorization = {
        **authorization,
        "runtime_profile_binding_verified": True,
        "runtime_binding_registry_id": binding_registry["registry_id"],
        "runtime_binding_registry_version": binding_registry["registry_version"],
        "runtime_binding_authority_id": binding_registry["source"]["authority_id"],
        "runtime_binding_id": entry["binding_id"],
        "runtime_profile_id": runtime_profile["profile_id"],
        "runtime_profile_version": runtime_profile["profile_version"],
        "runtime_profile_content_sha256": profile_sha,
        "runtime_profile_pose": deepcopy(runtime_profile["pose"]),
        "runtime_profile_pipeline": deepcopy(runtime_profile["pipeline"]),
        "runtime_profile_view": deepcopy(runtime_profile["view"]),
        "runtime_view_evidence_id": view_evidence["evidence_id"],
        "runtime_view_evidence_sha256": canonical_sha256(view_evidence),
        "runtime_view_video_id": view_evidence["video_id"],
        "runtime_view_video_sha256": view_evidence["video_sha256"].lower(),
    }
    return RuntimeProfileBoundProductionCalibration(
        calibration,
        bound_authorization,
        _token=_RUNTIME_BOUND_TOKEN,
    )


def bind_production_calibrations_for_runtime(
    calibrations: Mapping[str, Mapping[str, Any]],
    *,
    binding_registry: dict[str, Any],
    view_evidence: dict[str, Any],
    feasibility_registry: dict[str, Any],
    model_versions: Mapping[str, Any],
) -> dict[str, RuntimeProfileBoundProductionCalibration]:
    """Bind a set of registry-authorized assets to one observed video runtime.

    Profile identifiers are operator-owned values from the trusted sidecar.
    Runtime code therefore selects a unique active entry by immutable asset,
    ledger, registry and view identity, rebuilds that entry's profile from the
    observed model/pipeline facts, and requires byte-equivalent canonical data.
    """

    validate_trusted_runtime_binding_registry(binding_registry)
    validate_runtime_view_evidence(view_evidence)
    output: dict[str, RuntimeProfileBoundProductionCalibration] = {}
    for indicator_id, calibration in calibrations.items():
        if not isinstance(calibration, TrustedProductionCalibration):
            raise RuntimeProfileBindingError(
                "batch runtime binding requires registry-authorized production "
                f"calibration for {indicator_id}"
            )
        if calibration.get("artifact_scope") != "production":
            raise RuntimeProfileBindingError(
                "runtime profile binding is only valid for production calibrations"
            )
        authorization = calibration.authorization
        asset_sha = canonical_sha256(dict(calibration))
        candidates = []
        for entry in binding_registry["entries"]:
            view = entry["runtime_profile"]["view"]
            if (
                entry["status"] == "active"
                and entry["indicator_id"] == indicator_id
                and entry["backend"] == calibration["backend"]
                and entry["calibration_version"] == _calibration_version(calibration)
                and entry["calibration_asset_sha256"] == asset_sha
                and entry["trusted_promotion"]
                == {
                    "ledger_id": authorization["ledger_id"],
                    "ledger_version": authorization["ledger_version"],
                    "entry_id": authorization["entry_id"],
                }
                and entry["feasibility_registry"]
                == {
                    "registry_version": feasibility_registry["registry_version"],
                    "content_sha256": canonical_sha256(feasibility_registry),
                }
                and view["view_profile_id"] == view_evidence["view_profile_id"]
                and view["view_group"] == view_evidence["view_group"]
                and view["verification_protocol_version"]
                == view_evidence["verification_protocol_version"]
            ):
                candidates.append(entry)
        if len(candidates) != 1:
            raise RuntimeProfileBindingError(
                f"production calibration {indicator_id} has no unique active "
                "binding for the current runtime view identity"
            )
        expected_profile = candidates[0]["runtime_profile"]
        indicator = _find_indicator(feasibility_registry, indicator_id)
        observed_profile = build_runtime_scoring_profile(
            model_versions=model_versions,
            indicator=indicator,
            profile_id=expected_profile["profile_id"],
            view_profile_id=view_evidence["view_profile_id"],
            view_group=view_evidence["view_group"],
            verification_protocol_version=view_evidence[
                "verification_protocol_version"
            ],
        )
        if observed_profile != expected_profile:
            raise RuntimeProfileBindingError(
                f"observed runtime profile for {indicator_id} differs from the "
                "operator-reviewed binding"
            )
        output[indicator_id] = bind_trusted_production_calibration_to_runtime_profile(
            calibration,
            binding_registry=binding_registry,
            runtime_profile=observed_profile,
            view_evidence=view_evidence,
            feasibility_registry=feasibility_registry,
        )
    return output


def runtime_profile_versions(
    calibration: Mapping[str, Any],
) -> dict[str, str]:
    if not isinstance(calibration, RuntimeProfileBoundProductionCalibration):
        return {}
    authorization = calibration.authorization
    return {
        "runtime_binding_registry": authorization["runtime_binding_registry_version"],
        "runtime_binding_id": authorization["runtime_binding_id"],
        "runtime_profile": authorization["runtime_profile_id"],
        "runtime_profile_sha256": authorization["runtime_profile_content_sha256"],
        "runtime_view_evidence": authorization["runtime_view_evidence_id"],
        "runtime_view_evidence_sha256": authorization[
            "runtime_view_evidence_sha256"
        ],
    }


__all__ = [
    "RuntimeProfileBindingError",
    "RuntimeProfileBoundProductionCalibration",
    "bind_trusted_production_calibration_to_runtime_profile",
    "bind_production_calibrations_for_runtime",
    "build_runtime_scoring_profile",
    "is_runtime_profile_bound_calibration",
    "runtime_profile_versions",
    "validate_runtime_scoring_profile",
    "validate_runtime_view_evidence",
    "validate_trusted_runtime_binding_registry",
]
