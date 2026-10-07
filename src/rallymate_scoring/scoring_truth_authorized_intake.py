from __future__ import annotations

"""Read-only calibration intake verifier for operator-controlled integrations.

An externally pinned acceptance ledger is the trust boundary, just as deployment
ACLs are for the promotion ledger. Neither intake, a local annotation release,
nor the persisted calibration binding supplies its own authority. This verifier
does not authenticate people, create approvals, or authorize production scoring.
"""

from collections import defaultdict
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any, Mapping
import unicodedata

from rallymate_annotation.scoring_truth_event_phase_intake import (
    EVENT_ANNOTATIONS_PATH,
    FULL_VIDEO_REVIEW_PATH,
    INTAKE_MANIFEST_NAME,
    PLAN_PATH,
    _snapshot_file,
    _strict_json_object,
    _walk_plain_tree,
    validate_scoring_truth_event_phase_intake,
)
from rallymate_annotation.scoring_truth_intake import validate_scoring_truth_intake
from rallymate_scoring.scoring_truth_calibration_authorization import (
    CALIBRATION_AUTHORIZATION_BINDING_VERSION,
    CANONICALIZATION,
    VERIFIED_STATUS,
    ScoringTruthCalibrationAuthorizationError,
    VerifiedScoringTruthCalibrationAuthorization,
    _issue_verified_scoring_truth_calibration_authorization,
    scoring_truth_calibration_authorization_binding_sha256,
    validate_scoring_truth_calibration_authorization_binding,
)


LEDGER_VERSION = "scoring-truth-calibration-acceptance-ledger-v1.0.0"
ACCEPTANCE_SCOPE = "calibration_input_only"
_ATTESTATIONS = {
    "independent_collection_reviewed",
    "raw_source_provenance_reviewed",
    "semantic_adjudication_reviewed",
    "raw_coach_labels_reviewed",
    "projection_defaults_not_observed_truth",
}
_INPUT_PATHS = {
    "intake_manifest": "intake-manifest.json",
    "truth_manifest": "manifest.json",
    "truth_validation_report": "compiled/validation-report.json",
    "manual_events": "compiled/manual-events.jsonl",
    "manual_semantics": "compiled/manual-semantics.jsonl",
    "coach_labels": "compiled/coach-labels.jsonl",
}


def _error(message: str) -> None:
    raise ScoringTruthCalibrationAuthorizationError(message)


def _exact(value: Any, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _error(f"{name} fields are not exact")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _error(f"{name} must be a non-empty string")
    return value


def _identity(value: Any) -> str:
    return unicodedata.normalize("NFKC", _text(value, "participant").strip()).casefold()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789ABCDEFabcdef" for c in value):
        _error(f"{name} must be SHA-256")
    return value.upper()


def _time(value: Any, name: str) -> datetime:
    try:
        text = _text(value, name)
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("timezone missing")
        return result
    except ValueError as exc:
        raise ScoringTruthCalibrationAuthorizationError(f"{name} must include an ISO-8601 timezone") from exc


def validate_scoring_truth_acceptance_ledger(payload: Mapping[str, Any]) -> None:
    """Validate the closed ledger contract; structural validity is not authority."""
    _exact(payload, {"ledger_version", "ledger_id", "authority_id", "registered_at", "entries"}, "acceptance ledger")
    if payload["ledger_version"] != LEDGER_VERSION:
        _error("unsupported acceptance ledger version")
    _text(payload["ledger_id"], "ledger_id")
    _text(payload["authority_id"], "authority_id")
    registered = _time(payload["registered_at"], "registered_at")
    if not isinstance(payload["entries"], list) or not payload["entries"]:
        _error("acceptance ledger entries must be non-empty")
    ids: set[str] = set()
    active_intakes: set[str] = set()
    for entry in payload["entries"]:
        _exact(entry, {"authorization_id", "status", "scope", "current_revision_id", "event_intake_manifest_sha256", "binding", "approvals", "attestations", "promotion_authorized", "production_scoring_authorized"}, "acceptance entry")
        authorization_id = _text(entry["authorization_id"], "authorization_id")
        if authorization_id in ids:
            _error("duplicate authorization_id in acceptance ledger")
        ids.add(authorization_id)
        if entry["status"] not in {"active", "revoked"} or entry["scope"] != ACCEPTANCE_SCOPE:
            _error("acceptance entry status or scope is invalid")
        if entry["promotion_authorized"] is not False or entry["production_scoring_authorized"] is not False:
            _error("calibration intake acceptance cannot authorize promotion or production scoring")
        validate_scoring_truth_calibration_authorization_binding(entry["binding"])
        binding = entry["binding"]
        if binding["authorization_id"] != authorization_id or entry["current_revision_id"] != binding["revision_lineage"]["revision_id"]:
            _error("acceptance entry does not bind the current revision")
        _digest(entry["event_intake_manifest_sha256"], "event_intake_manifest_sha256")
        if entry["status"] == "active":
            intake_id = binding["intake"]["intake_id"]
            if intake_id in active_intakes:
                _error("multiple active acceptances for one truth intake")
            active_intakes.add(intake_id)
        _exact(entry["attestations"], _ATTESTATIONS, "acceptance attestations")
        if any(value is not True for value in entry["attestations"].values()):
            _error("all external acceptance reviews must be explicitly attested")
        approvals = entry["approvals"]
        if not isinstance(approvals, list) or len(approvals) != 2:
            _error("acceptance requires two independent approval records")
        reviewers: set[str] = set()
        roles: set[str] = set()
        records: set[str] = set()
        for approval in approvals:
            _exact(approval, {"reviewer_id", "role", "approved_at", "external_record_sha256"}, "acceptance approval")
            reviewers.add(_identity(approval["reviewer_id"]))
            roles.add(_text(approval["role"], "approval role"))
            records.add(_digest(approval["external_record_sha256"], "external_record_sha256"))
            if _time(approval["approved_at"], "approved_at") > registered:
                _error("approval postdates acceptance ledger registration")
        if len(reviewers) != 2 or len(records) != 2 or roles != {"truth_data_reviewer", "calibration_release_operator"}:
            _error("acceptance reviewers, roles, and external records must be independent")


def _jsonl(raw: bytes, name: str) -> list[dict[str, Any]]:
    return [_strict_json_object(line, name=name) for line in raw.splitlines() if line.strip()]


def _read(root: Path, relative: str) -> bytes:
    return _snapshot_file(root / relative, name=relative)


def _check_labels_and_semantics(raw_inputs: Mapping[str, bytes], events: list[dict[str, Any]]) -> set[str]:
    """Supplement the replayed compiler with independent-review identity checks."""
    semantics = _jsonl(raw_inputs["manual_semantics"], "manual semantics")
    labels = _jsonl(raw_inputs["coach_labels"], "coach labels")
    if not events or not semantics or not labels:
        _error("non-empty adjudicated events, semantics, and real coach labels are required")
    event_by_key = {(row["video_id"], row["event_id"]): row for row in events}
    participants: set[str] = set()
    groups: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for row in [*semantics, *labels]:
        event = event_by_key.get((row["video_id"], row["event_id"]))
        if event is None or row["indicator_id"].split("-M", 1)[0] != event["event_code"]:
            _error("truth row does not join an adjudicated M93 event and indicator")
        participants.add(_identity(row["annotator_id"]))
    for row in semantics:
        author, reviewer = _identity(row["annotator_id"]), _identity(row["reviewer_id"])
        if author == reviewer or row.get("adjudication_status") != "accepted":
            _error("accepted semantic truth requires an independent adjudicator")
        participants.add(reviewer)
    for row in labels:
        key = (row["video_id"], row["event_id"], row["indicator_id"], row["label_type"])
        coach = _identity(row["annotator_id"])
        if coach in groups[key]:
            _error("duplicate normalized coach identity for one label item")
        groups[key].add(coach)
    if any(len(coaches) < 2 for coaches in groups.values()):
        _error("every coach label item requires at least two independent coaches")
    return participants


def verify_scoring_truth_calibration_intake(
    *,
    event_phase_intake_dir: str | Path,
    truth_intake_dir: str | Path,
    trusted_acceptance_ledger_path: str | Path,
    trusted_acceptance_ledger_sha256: str,
    authorization_id: str,
) -> VerifiedScoringTruthCalibrationAuthorization:
    """Replay both intakes and issue content-bound, process-local calibration authority.

    The ledger path AND digest must come from deployment/operator configuration,
    independent of submitted data. Do not expose these arguments to uploads or
    derive the pin from the supplied ledger. All inputs remain read-only. Calling
    downstream ``require_verified...`` rechecks both complete source trees,
    retained original CSVs/source pack, and the pinned ledger before reuse.
    """
    event_root = Path(event_phase_intake_dir).absolute()
    truth_root = Path(truth_intake_dir).absolute()
    ledger_path = Path(trusted_acceptance_ledger_path).absolute()
    pin = _digest(trusted_acceptance_ledger_sha256, "trusted acceptance ledger pin")
    for root in (event_root, truth_root):
        if ledger_path == root or root in ledger_path.parents:
            _error("trusted acceptance ledger must be outside both submitted intake trees")

    def replay() -> tuple[dict[str, Any], dict[Path, str]]:
        try:
            checked_hashes: dict[Path, str] = {}

            def checked_read(root: Path, relative: str) -> bytes:
                raw = _read(root, relative)
                path = root / relative
                digest = _sha(raw)
                if path in checked_hashes and checked_hashes[path] != digest:
                    _error("authorization input changed between semantic checks")
                checked_hashes[path] = digest
                return raw

            ledger_raw = _snapshot_file(ledger_path, name="trusted acceptance ledger")
            if _sha(ledger_raw) != pin:
                _error("trusted acceptance ledger pin mismatch or ledger revoked/changed")
            ledger = _strict_json_object(ledger_raw, name="trusted acceptance ledger")
            validate_scoring_truth_acceptance_ledger(ledger)
            matches = [entry for entry in ledger["entries"] if entry["authorization_id"] == authorization_id and entry["status"] == "active"]
            if len(matches) != 1:
                _error("a unique active acceptance for this authorization is required")
            entry = matches[0]
            event_result = validate_scoring_truth_event_phase_intake(event_root)
            event_manifest = event_result["manifest"]
            if event_result["manifest_sha256"] != entry["event_intake_manifest_sha256"].upper():
                _error("acceptance does not bind the current M93 intake manifest")
            truth_manifest = validate_scoring_truth_intake(truth_root)
            retained_source_root = Path(truth_manifest["source_pack"]["path"]).absolute()
            if ledger_path == retained_source_root or retained_source_root in ledger_path.parents:
                _error("trusted acceptance ledger must be outside the retained truth source pack")
            collected_at = max(_time(event_manifest["generated_at"], "event intake generated_at"),
                               _time(truth_manifest["generated_at"], "truth intake generated_at"))
            if any(_time(approval["approved_at"], "approved_at") < collected_at for approval in entry["approvals"]):
                _error("acceptance approval predates the finalized intake evidence")
            # M93-to-CSV is an explicit adapter: only byte-exact event and review
            # exports may feed the established CSV compiler. No nearest joins.
            for filename, event_relative in (("event-annotations.csv", EVENT_ANNOTATIONS_PATH), ("full-video-review-completion.csv", FULL_VIDEO_REVIEW_PATH)):
                if checked_read(truth_root, filename) != checked_read(event_root, event_relative):
                    _error(f"truth intake {filename} is not the exact current M93 projection")
            raw_inputs = {name: checked_read(truth_root, relative) for name, relative in _INPUT_PATHS.items()}
            if _strict_json_object(raw_inputs["intake_manifest"], name="truth intake manifest") != truth_manifest:
                _error("truth intake manifest changed after compilation replay")
            pack = _strict_json_object(raw_inputs["truth_manifest"], name="truth manifest")
            plan = _strict_json_object(checked_read(event_root, PLAN_PATH), name="M93 plan")
            expected_videos = {task["video_id"]: task["video_sha256"].upper() for task in plan["scope"]["tasks"]}
            actual_videos = {video["video_id"]: _digest(video["sha256"], "truth video SHA") for video in pack["videos"]}
            if actual_videos != expected_videos or len(pack["videos"]) != len(expected_videos):
                _error("truth manifest video identities differ from M93 source media")
            events = _jsonl(raw_inputs["manual_events"], "manual events")
            participants = _check_labels_and_semantics(raw_inputs, events)
            revision = event_manifest["revision_lineage"]
            participants.update(_identity(revision[name]) for name in ("annotator_a_id", "annotator_b_id", "reviewer_c_id"))
            if any(_identity(approval["reviewer_id"]) in participants for approval in entry["approvals"]):
                _error("acceptance reviewers must be independent of truth collection/adjudication")
            binding = {
                "binding_version": CALIBRATION_AUTHORIZATION_BINDING_VERSION,
                "status": VERIFIED_STATUS,
                "canonicalization": CANONICALIZATION,
                "authorization_id": authorization_id,
                "event_protocol_authorization": event_manifest["event_authorization"],
                "intake": {name: truth_manifest[name] for name in ("intake_id", "intake_version", "content_root_sha256")},
                "revision_lineage": revision,
                "input_files": {name: _sha(raw) for name, raw in raw_inputs.items()},
            }
            binding["binding_sha256"] = scoring_truth_calibration_authorization_binding_sha256(binding)
            if binding != entry["binding"] or revision["revision_id"] != entry["current_revision_id"]:
                _error("acceptance binding differs from current replayed truth bytes/revision")
            validate_scoring_truth_calibration_authorization_binding(binding)
            # Cover complete trees and retained raw origins. Replaying below is
            # deliberate: hashes alone cannot validate newly replaced sources.
            paths = {ledger_path}
            for root in (event_root, truth_root):
                _, files = _walk_plain_tree(root)
                paths.update(root / relative for relative in files)
            source = truth_manifest["source_pack"]
            paths.update(Path(row["path"]) for row in source["static_files"])
            paths.update(Path(row["source_path"]) for row in truth_manifest["exports"].values())
            snapshots = {path: _sha(_snapshot_file(path, name="authorization source")) for path in paths}
            if snapshots[ledger_path] != pin:
                _error("acceptance ledger changed during replay")
            for path, digest in checked_hashes.items():
                if snapshots.get(path) != digest:
                    _error("authorization input changed after semantic checks")
            for root, artifacts in ((event_root, event_result["artifacts"]), (truth_root, truth_manifest["artifacts"])):
                for artifact in artifacts:
                    if snapshots.get(root / artifact["path"]) != artifact["sha256"].upper():
                        _error("intake artifact changed after compilation replay")
            for record in source["static_files"]:
                if snapshots.get(Path(record["path"])) != record["sha256"].upper():
                    _error("retained source pack changed after compilation replay")
            for record in truth_manifest["exports"].values():
                if snapshots.get(Path(record["source_path"])) != record["sha256"].upper():
                    _error("retained original CSV changed after compilation replay")
            for name, relative in _INPUT_PATHS.items():
                if snapshots[truth_root / relative] != binding["input_files"][name]:
                    _error("calibration truth inputs changed during replay")
            if snapshots[event_root / INTAKE_MANIFEST_NAME] != event_result["manifest_sha256"]:
                _error("event intake manifest changed during replay")
            return binding, snapshots
        except ScoringTruthCalibrationAuthorizationError:
            raise
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise ScoringTruthCalibrationAuthorizationError(f"authorized intake replay failed: {exc}") from exc

    binding, snapshots = replay()

    def assert_current() -> None:
        # Replaying preserves exact topology, retained-origin and semantic
        # checks as well as catching a revoked/replaced ledger in this process.
        current_binding, current_snapshots = replay()
        if current_binding != binding or current_snapshots != snapshots:
            _error("authorized intake sources changed after verification")

    assert_current()
    return _issue_verified_scoring_truth_calibration_authorization(binding, assert_sources_current=assert_current)


__all__ = ["LEDGER_VERSION", "validate_scoring_truth_acceptance_ledger", "verify_scoring_truth_calibration_intake"]
