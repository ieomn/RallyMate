from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from rallymate_scoring.calibration_fitting import canonical_sha256


TRUSTED_SCORING_RUN_BUNDLE_LEDGER_SCHEMA_VERSION = "1.0.0"
TRUSTED_SCORING_RUN_BUNDLE_LEDGER_SCOPE = "trusted_scoring_run_bundles"
SCORING_RUN_BUNDLE_BINDING_VERSION = "scoring-run-bundle-v1"
CANONICALIZATION = "rallymate-canonical-json-v1"
REQUIRED_SCORING_ARTIFACTS = frozenset(
    {
        "events_jsonl",
        "features_jsonl",
        "indicator_features_jsonl",
        "scores_jsonl",
        "event_feature_errors_json",
    }
)
_HEX = frozenset("0123456789abcdefABCDEF")


class ScoringRunBundleBindingError(ValueError):
    """Raised when an offline production scoring bundle is not trusted."""


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScoringRunBundleBindingError(f"{field} must be an object")
    return value


def _exact_keys(value: dict[str, Any], required: set[str], field: str) -> None:
    missing = required - set(value)
    extra = set(value) - required
    if missing:
        raise ScoringRunBundleBindingError(
            f"{field} missing fields: {sorted(missing)}"
        )
    if extra:
        raise ScoringRunBundleBindingError(
            f"{field} has unsupported fields: {sorted(extra)}"
        )


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoringRunBundleBindingError(f"{field} must be a non-empty string")
    return value


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise ScoringRunBundleBindingError(
            f"{field} must be a 64-character SHA-256"
        )
    return value.lower()


def _timestamp(value: Any, field: str) -> datetime:
    text = _string(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScoringRunBundleBindingError(
            f"{field} must be a valid ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScoringRunBundleBindingError(f"{field} must include a timezone")
    return parsed


def _artifact_hashes(value: Any, field: str) -> dict[str, str]:
    artifacts = _object(value, field)
    _exact_keys(artifacts, set(REQUIRED_SCORING_ARTIFACTS), field)
    return {
        name: _sha256(artifacts[name], f"{field}.{name}")
        for name in sorted(REQUIRED_SCORING_ARTIFACTS)
    }


def scoring_run_bundle_identity(
    summary: dict[str, Any],
    *,
    scoring_summary_sha256: str,
) -> dict[str, Any]:
    """Project a scoring summary into the immutable production trust identity."""

    summary = _object(summary, "scoring_summary")
    schema_version = _string(
        summary.get("schema_version"), "scoring_summary.schema_version"
    )
    if schema_version != "1.0.0":
        raise ScoringRunBundleBindingError(
            "unsupported scoring summary schema_version"
        )
    loop_version = _string(
        summary.get("loop_version"), "scoring_summary.loop_version"
    )
    video_id = _string(summary.get("video_id"), "scoring_summary.video_id")
    provenance = _object(summary.get("provenance"), "scoring_summary.provenance")
    video_sha256 = _sha256(
        provenance.get("video_sha256"),
        "scoring_summary.provenance.video_sha256",
    )
    artifact_paths = _object(summary.get("artifacts"), "scoring_summary.artifacts")
    _exact_keys(
        artifact_paths,
        set(REQUIRED_SCORING_ARTIFACTS),
        "scoring_summary.artifacts",
    )
    for name, path in artifact_paths.items():
        _string(path, f"scoring_summary.artifacts.{name}")
    artifacts = _artifact_hashes(
        summary.get("artifact_sha256"), "scoring_summary.artifact_sha256"
    )
    return {
        "binding_version": SCORING_RUN_BUNDLE_BINDING_VERSION,
        "scoring_summary": {
            "schema_version": schema_version,
            "loop_version": loop_version,
            "file_sha256": _sha256(
                scoring_summary_sha256, "scoring_summary_sha256"
            ),
        },
        "video": {
            "video_id": video_id,
            "video_sha256": video_sha256,
        },
        "artifact_sha256": artifacts,
    }


def scoring_run_bundle_root_sha256(identity: dict[str, Any]) -> str:
    normalized = _validate_bundle_identity(identity, "bundle_identity")
    return canonical_sha256(normalized)


def _validate_bundle_identity(identity: Any, field: str) -> dict[str, Any]:
    identity = _object(identity, field)
    _exact_keys(
        identity,
        {"binding_version", "scoring_summary", "video", "artifact_sha256"},
        field,
    )
    if identity["binding_version"] != SCORING_RUN_BUNDLE_BINDING_VERSION:
        raise ScoringRunBundleBindingError(
            f"{field}.binding_version is unsupported"
        )
    summary = _object(identity["scoring_summary"], f"{field}.scoring_summary")
    _exact_keys(
        summary,
        {"schema_version", "loop_version", "file_sha256"},
        f"{field}.scoring_summary",
    )
    schema_version = _string(
        summary["schema_version"], f"{field}.scoring_summary.schema_version"
    )
    if schema_version != "1.0.0":
        raise ScoringRunBundleBindingError(
            f"{field}.scoring_summary.schema_version is unsupported"
        )
    loop_version = _string(
        summary["loop_version"], f"{field}.scoring_summary.loop_version"
    )
    summary_sha256 = _sha256(
        summary["file_sha256"], f"{field}.scoring_summary.file_sha256"
    )
    video = _object(identity["video"], f"{field}.video")
    _exact_keys(video, {"video_id", "video_sha256"}, f"{field}.video")
    video_id = _string(video["video_id"], f"{field}.video.video_id")
    video_sha256 = _sha256(
        video["video_sha256"], f"{field}.video.video_sha256"
    )
    return {
        "binding_version": SCORING_RUN_BUNDLE_BINDING_VERSION,
        "scoring_summary": {
            "schema_version": schema_version,
            "loop_version": loop_version,
            "file_sha256": summary_sha256,
        },
        "video": {
            "video_id": video_id,
            "video_sha256": video_sha256,
        },
        "artifact_sha256": _artifact_hashes(
            identity["artifact_sha256"], f"{field}.artifact_sha256"
        ),
    }


def build_scoring_run_bundle_entry(
    *,
    summary: dict[str, Any],
    scoring_summary_sha256: str,
    entry_id: str,
    review_id: str,
    reviewer_id: str,
    review_source_sha256: str,
    reviewed_at: str,
    registered_at: str,
) -> dict[str, Any]:
    identity = scoring_run_bundle_identity(
        summary, scoring_summary_sha256=scoring_summary_sha256
    )
    entry = {
        "entry_id": _string(entry_id, "entry_id"),
        "status": "active",
        "bundle_identity": identity,
        "bundle_root_sha256": canonical_sha256(identity),
        "authorization_review": {
            "decision": "approved_for_production_rescoring",
            "review_id": _string(review_id, "review_id"),
            "reviewer_id": _string(reviewer_id, "reviewer_id"),
            "source_sha256": _sha256(
                review_source_sha256, "review_source_sha256"
            ),
            "reviewed_at": reviewed_at,
        },
        "registered_at": registered_at,
    }
    _validate_entry(entry, 0)
    return entry


def build_trusted_scoring_run_bundle_ledger(
    *,
    entries: list[dict[str, Any]],
    ledger_id: str,
    ledger_version: str,
    authority_id: str,
    registered_at: str,
) -> dict[str, Any]:
    ledger = {
        "schema_version": TRUSTED_SCORING_RUN_BUNDLE_LEDGER_SCHEMA_VERSION,
        "artifact_scope": TRUSTED_SCORING_RUN_BUNDLE_LEDGER_SCOPE,
        "ledger_id": _string(ledger_id, "ledger_id"),
        "ledger_version": _string(ledger_version, "ledger_version"),
        "source": {
            "kind": "operator_controlled_local_ledger",
            "authority_id": _string(authority_id, "authority_id"),
        },
        "registered_at": registered_at,
        "canonicalization": CANONICALIZATION,
        "entries": deepcopy(entries),
        "trust_boundary": (
            "local ledger path and file ACL are controlled by the deployment "
            "operator; ordinary uploads, scoring bundles and job requests cannot "
            "select or modify this ledger"
        ),
    }
    validate_trusted_scoring_run_bundle_ledger(ledger)
    return ledger


def _validate_entry(entry: Any, index: int) -> dict[str, Any]:
    field = f"trusted_run_bundle_ledger.entries[{index}]"
    entry = _object(entry, field)
    _exact_keys(
        entry,
        {
            "entry_id",
            "status",
            "bundle_identity",
            "bundle_root_sha256",
            "authorization_review",
            "registered_at",
        },
        field,
    )
    _string(entry["entry_id"], f"{field}.entry_id")
    if entry["status"] not in {"active", "revoked"}:
        raise ScoringRunBundleBindingError(f"{field}.status is invalid")
    identity = _validate_bundle_identity(
        entry["bundle_identity"], f"{field}.bundle_identity"
    )
    expected_root = canonical_sha256(identity)
    if (
        _sha256(entry["bundle_root_sha256"], f"{field}.bundle_root_sha256")
        != expected_root
    ):
        raise ScoringRunBundleBindingError(
            f"{field}.bundle_root_sha256 does not match bundle_identity"
        )
    review = _object(
        entry["authorization_review"], f"{field}.authorization_review"
    )
    _exact_keys(
        review,
        {"decision", "review_id", "reviewer_id", "source_sha256", "reviewed_at"},
        f"{field}.authorization_review",
    )
    if review["decision"] != "approved_for_production_rescoring":
        raise ScoringRunBundleBindingError(
            f"{field}.authorization_review.decision is invalid"
        )
    for name in ("review_id", "reviewer_id"):
        _string(review[name], f"{field}.authorization_review.{name}")
    _sha256(
        review["source_sha256"],
        f"{field}.authorization_review.source_sha256",
    )
    reviewed_at = _timestamp(
        review["reviewed_at"], f"{field}.authorization_review.reviewed_at"
    )
    registered_at = _timestamp(entry["registered_at"], f"{field}.registered_at")
    if reviewed_at > registered_at:
        raise ScoringRunBundleBindingError(
            f"{field} authorization review must not postdate registration"
        )
    return identity


def validate_trusted_scoring_run_bundle_ledger(ledger: dict[str, Any]) -> None:
    ledger = _object(ledger, "trusted_run_bundle_ledger")
    _exact_keys(
        ledger,
        {
            "schema_version",
            "artifact_scope",
            "ledger_id",
            "ledger_version",
            "source",
            "registered_at",
            "canonicalization",
            "entries",
            "trust_boundary",
        },
        "trusted_run_bundle_ledger",
    )
    if ledger["schema_version"] != TRUSTED_SCORING_RUN_BUNDLE_LEDGER_SCHEMA_VERSION:
        raise ScoringRunBundleBindingError(
            "unsupported trusted scoring run-bundle ledger schema_version"
        )
    if ledger["artifact_scope"] != TRUSTED_SCORING_RUN_BUNDLE_LEDGER_SCOPE:
        raise ScoringRunBundleBindingError(
            "trusted scoring run-bundle ledger artifact_scope is invalid"
        )
    for name in ("ledger_id", "ledger_version", "trust_boundary"):
        _string(ledger[name], f"trusted_run_bundle_ledger.{name}")
    if ledger["canonicalization"] != CANONICALIZATION:
        raise ScoringRunBundleBindingError(
            "trusted scoring run-bundle ledger canonicalization is invalid"
        )
    source = _object(ledger["source"], "trusted_run_bundle_ledger.source")
    _exact_keys(
        source,
        {"kind", "authority_id"},
        "trusted_run_bundle_ledger.source",
    )
    if source["kind"] != "operator_controlled_local_ledger":
        raise ScoringRunBundleBindingError(
            "trusted scoring run-bundle ledger source.kind is invalid"
        )
    _string(source["authority_id"], "trusted_run_bundle_ledger.source.authority_id")
    ledger_time = _timestamp(
        ledger["registered_at"], "trusted_run_bundle_ledger.registered_at"
    )
    entries = ledger["entries"]
    if not isinstance(entries, list) or not entries:
        raise ScoringRunBundleBindingError(
            "trusted scoring run-bundle ledger entries must be non-empty"
        )
    entry_ids: set[str] = set()
    active_roots: set[str] = set()
    active_video_ids: set[str] = set()
    for index, entry in enumerate(entries):
        identity = _validate_entry(entry, index)
        if entry["entry_id"] in entry_ids:
            raise ScoringRunBundleBindingError(
                "trusted scoring run-bundle ledger entry_id values must be unique"
            )
        entry_ids.add(entry["entry_id"])
        if _timestamp(
            entry["registered_at"],
            f"trusted_run_bundle_ledger.entries[{index}].registered_at",
        ) > ledger_time:
            raise ScoringRunBundleBindingError(
                "trusted scoring run-bundle entry must not postdate ledger registration"
            )
        if entry["status"] == "active":
            root = _sha256(
                entry["bundle_root_sha256"],
                f"trusted_run_bundle_ledger.entries[{index}].bundle_root_sha256",
            )
            if root in active_roots:
                raise ScoringRunBundleBindingError(
                    "trusted scoring run-bundle ledger has duplicate active bundle roots"
                )
            active_roots.add(root)
            video_id = identity["video"]["video_id"]
            if video_id in active_video_ids:
                raise ScoringRunBundleBindingError(
                    "trusted scoring run-bundle ledger has multiple active entries "
                    f"for video_id {video_id}"
                )
            active_video_ids.add(video_id)


def load_trusted_scoring_run_bundle_ledger(path: Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"trusted scoring run-bundle ledger is missing: {resolved}"
        )
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScoringRunBundleBindingError(
            f"invalid trusted scoring run-bundle ledger JSON {resolved}: {exc}"
        ) from exc
    validate_trusted_scoring_run_bundle_ledger(payload)
    return payload


def authorize_scoring_run_bundle(
    *,
    summary: dict[str, Any],
    scoring_summary_sha256: str,
    ledger: dict[str, Any],
) -> dict[str, Any]:
    """Authorize an exact content-addressed scoring bundle for production reuse."""

    validate_trusted_scoring_run_bundle_ledger(ledger)
    identity = scoring_run_bundle_identity(
        summary, scoring_summary_sha256=scoring_summary_sha256
    )
    root = canonical_sha256(identity)
    matches = []
    for index, entry in enumerate(ledger["entries"]):
        if entry["status"] != "active":
            continue
        registered_identity = _validate_bundle_identity(
            entry["bundle_identity"],
            f"trusted_run_bundle_ledger.entries[{index}].bundle_identity",
        )
        if (
            registered_identity == identity
            and _sha256(
                entry["bundle_root_sha256"],
                f"trusted_run_bundle_ledger.entries[{index}].bundle_root_sha256",
            )
            == root
        ):
            matches.append(entry)
    if len(matches) != 1:
        raise ScoringRunBundleBindingError(
            "scoring bundle has no unique active exact entry in the operator-controlled ledger"
        )
    entry = matches[0]
    return {
        "trusted_scoring_run_bundle_verified": True,
        "ledger_id": ledger["ledger_id"],
        "ledger_version": ledger["ledger_version"],
        "ledger_authority_id": ledger["source"]["authority_id"],
        "entry_id": entry["entry_id"],
        "bundle_root_sha256": root,
        "scoring_summary_sha256": identity["scoring_summary"]["file_sha256"],
        "video_id": identity["video"]["video_id"],
        "video_sha256": identity["video"]["video_sha256"],
        "review_id": entry["authorization_review"]["review_id"],
        "reviewer_id": entry["authorization_review"]["reviewer_id"],
    }


__all__ = [
    "REQUIRED_SCORING_ARTIFACTS",
    "SCORING_RUN_BUNDLE_BINDING_VERSION",
    "ScoringRunBundleBindingError",
    "authorize_scoring_run_bundle",
    "build_scoring_run_bundle_entry",
    "build_trusted_scoring_run_bundle_ledger",
    "load_trusted_scoring_run_bundle_ledger",
    "scoring_run_bundle_identity",
    "scoring_run_bundle_root_sha256",
    "validate_trusted_scoring_run_bundle_ledger",
]
