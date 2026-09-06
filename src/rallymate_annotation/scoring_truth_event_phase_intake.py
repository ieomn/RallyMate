from __future__ import annotations

"""PRIVATE, annotation-only intake for the M93 event/phase workflow.

The intake consumes two independently produced annotation submissions and one
independent adjudication submission.  It preserves the three submitted files
byte-for-byte, projects only reviewer-C's finalized event/phase decisions, and
creates an immutable directory by staging and atomic rename.  It deliberately
does not issue calibration authority, grades, thresholds, promotion authority,
or a runtime/profile change.
"""

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from rallymate_annotation.scoring_truth_authorization import (
    ScoringTruthAuthorizationError,
    validate_scoring_truth_authorization_binding,
    verify_scoring_truth_event_authorization,
)
from rallymate_annotation.truth_pack import EVENT_CSV_FIELDS, REVIEW_CSV_FIELDS


SCHEMA_VERSION = "1.0.0"
INTAKE_VERSION = "scoring-truth-event-phase-intake-v1.0.0"
INTAKE_STATUS = "private_event_phase_intake_finalized_annotation_only"
CLASSIFICATION = "PRIVATE"
REPORT_VERSION = "scoring-truth-event-phase-validation-v1.0.0"
REPORT_STATUS = "event_phase_intake_validated_annotation_only"
CANONICALIZATION = "rallymate-canonical-json-v1"

INTAKE_MANIFEST_NAME = "intake-manifest.json"
PLAN_PATH = "authority/plan.json"
RELEASE_PATH = "authority/operator-release-record.json"
M89_DIRECTORY = "authority/m89"
EXECUTION_A_MANIFEST_PATH = "sources/execution-A/execution-manifest.json"
EXECUTION_B_MANIFEST_PATH = "sources/execution-B/execution-manifest.json"
ADJUDICATION_MANIFEST_PATH = "sources/adjudication/adjudication-manifest.json"
RAW_A_PATH = "raw-submissions/A.json"
RAW_B_PATH = "raw-submissions/B.json"
RAW_C_PATH = "raw-submissions/C.json"
MANUAL_EVENTS_PATH = "compiled/manual-events.jsonl"
EVENT_ANNOTATIONS_PATH = "compiled/event-annotations.csv"
FULL_VIDEO_REVIEW_PATH = "compiled/full-video-review-completion.csv"
VALIDATION_REPORT_PATH = "compiled/validation-report.json"

PERSON_TRACK_ID_PROJECTION = 1
VIEW_GROUP_PROJECTION = "source-view-unclassified"
LEGACY_ANNOTATOR_ID = "adjudicated-independent-event-phase"

PHASE_KEYS_BY_EVENT: dict[str, tuple[str, ...]] = {
    "FS01": (
        "preload_ms",
        "takeoff_proxy_ms",
        "landing_proxy_ms",
        "redistribution_ms",
        "initiation_ms",
    ),
    "FS02": (
        "direction_conversion_ms",
        "support_extension_proxy_ms",
        "lead_foot_motion_onset_proxy_ms",
        "first_step_slowdown_proxy_ms",
    ),
    "FS09": (
        "peak_speed_ms",
        "deceleration_peak_ms",
        "restabilization_onset_ms",
        "stable_control_onset_ms",
    ),
}

DIRECTORIES = {
    "authority": "authority",
    "sources": "sources",
    "raw_submissions": "raw-submissions",
    "compiled": "compiled",
}

SAFETY = {
    "private_intake": True,
    "event_phase_annotation_only": True,
    "raw_submissions_preserved_byte_exact": True,
    "person_track_id_is_observed_truth": False,
    "view_group_is_observed_truth": False,
    "calibration_authorized": False,
    "promotion_authorized": False,
    "production_scoring_authorized": False,
    "grades_generated": False,
    "thresholds_generated": False,
    "runtime_profile_changed": False,
}

REPORT_SAFETY = {
    "event_phase_annotation_only": True,
    "projection_defaults_are_not_observed_truth": True,
    "calibration_authorized": False,
    "promotion_authorized": False,
    "production_scoring_authorized": False,
    "grades_generated": False,
    "thresholds_generated": False,
    "runtime_profile_changed": False,
}

_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_UTC_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?Z$"
)


class ScoringTruthEventPhaseIntakeError(ValueError):
    """Raised when an M93 intake cannot be finalized or replayed exactly."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ScoringTruthEventPhaseIntakeError(
            "value is not canonical UTF-8 JSON"
        ) from exc


def _canonical_sha(value: Any) -> str:
    return _sha256(_canonical_json_bytes(value))


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ScoringTruthEventPhaseIntakeError("JSON number must be finite")
    return parsed


def _reject_constant(value: str) -> None:
    raise ScoringTruthEventPhaseIntakeError(f"invalid JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScoringTruthEventPhaseIntakeError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json_object(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_float=_parse_float,
            parse_constant=_reject_constant,
        )
        _canonical_json_bytes(value)
    except ScoringTruthEventPhaseIntakeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ScoringTruthEventPhaseIntakeError(
            f"{name} must be one strict UTF-8 JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise ScoringTruthEventPhaseIntakeError(f"{name} must be a JSON object")
    return value


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        metadata = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    is_junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(metadata.st_mode)
        or bool(attributes & reparse_flag)
        or bool(is_junction is not None and is_junction())
    )


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _snapshot_file(path: str | Path, *, name: str) -> bytes:
    requested = Path(path).expanduser().absolute()
    descriptor: int | None = None
    try:
        before_path = requested.lstat()
        before_resolved = requested.resolve(strict=True)
        if _is_link_like(requested) or not stat.S_ISREG(before_path.st_mode):
            raise ScoringTruthEventPhaseIntakeError(
                f"{name} must be a regular non-link file"
            )
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(requested, flags)
        before_open = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_open = os.fstat(descriptor)
        after_path = requested.lstat()
        after_resolved = requested.resolve(strict=True)
    except ScoringTruthEventPhaseIntakeError:
        raise
    except OSError as exc:
        raise ScoringTruthEventPhaseIntakeError(
            f"could not snapshot {name}: {requested}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    raw = b"".join(chunks)
    if (
        len({_path_identity(before_path), _path_identity(before_open), _path_identity(after_open), _path_identity(after_path)}) != 1
        or len(raw) != after_open.st_size
        or before_resolved != after_resolved
    ):
        raise ScoringTruthEventPhaseIntakeError(f"{name} changed while read")
    return raw


def _require_plain_directory(path: str | Path, *, name: str) -> Path:
    requested = Path(path).expanduser().absolute()
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise ScoringTruthEventPhaseIntakeError(f"missing {name}: {requested}") from exc
    if not resolved.is_dir() or _is_link_like(requested):
        raise ScoringTruthEventPhaseIntakeError(
            f"{name} must be a plain non-link directory"
        )
    return resolved


def _snapshot_tree(path: str | Path, *, name: str) -> dict[str, bytes]:
    root = _require_plain_directory(path, name=name)
    result: dict[str, bytes] = {}
    for current, names, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        names.sort()
        filenames.sort()
        for directory_name in names:
            child = current_path / directory_name
            if _is_link_like(child):
                raise ScoringTruthEventPhaseIntakeError(
                    f"{name} contains a linked directory: {child}"
                )
        for filename in filenames:
            child = current_path / filename
            relative = child.relative_to(root).as_posix()
            result[relative] = _snapshot_file(child, name=f"{name}/{relative}")
    if not result:
        raise ScoringTruthEventPhaseIntakeError(f"{name} is empty")
    return result


def _canonical_relative_path(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ScoringTruthEventPhaseIntakeError(
            f"{name} must be a canonical POSIX relative path"
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ScoringTruthEventPhaseIntakeError(
            f"{name} must be a canonical POSIX relative path"
        )
    return value


def _session_path(root: Path, relative: str, *, name: str) -> Path:
    canonical = _canonical_relative_path(relative, name=name)
    target = root.joinpath(*PurePosixPath(canonical).parts).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ScoringTruthEventPhaseIntakeError(f"{name} escapes the intake") from exc
    return target


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    return _path_contains(left, right) or _path_contains(right, left)


def _require_exact_keys(value: Any, expected: set[str], *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise ScoringTruthEventPhaseIntakeError(
            f"{name} fields drifted (missing={sorted(expected - actual)}, extra={sorted(actual - expected)})"
        )
    return value


def _require_sha(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ScoringTruthEventPhaseIntakeError(f"{name} must be uppercase SHA-256")
    return value


def _require_identifier(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ScoringTruthEventPhaseIntakeError(f"{name} must be a safe identifier")
    return value


def _require_timestamp(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise ScoringTruthEventPhaseIntakeError(f"{name} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ScoringTruthEventPhaseIntakeError(f"{name} is not a real instant") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ScoringTruthEventPhaseIntakeError(f"{name} must identify UTC")
    return value


def _timestamp_value(value: Any, *, name: str) -> datetime:
    timestamp = _require_timestamp(value, name=name)
    return datetime.fromisoformat(timestamp[:-1] + "+00:00")


def _latest_canonical_timestamp(values: Sequence[tuple[str, Any]]) -> str:
    latest = max(_timestamp_value(value, name=name) for name, value in values)
    return latest.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _validate_intake_chronology(
    *,
    generated_at: Any,
    source_manifests: Mapping[str, Mapping[str, Any]],
    submissions: Mapping[str, Mapping[str, Any]],
) -> None:
    generated = _timestamp_value(generated_at, name="generated_at")
    sources = [
        (f"source manifest {slot}.generated_at", source_manifests[slot].get("generated_at"))
        for slot in ("A", "B", "C")
    ]
    exports = [
        (f"submission {slot}.exported_at", submissions[slot].get("exported_at"))
        for slot in ("A", "B", "C")
    ]
    latest_source = max(
        _timestamp_value(value, name=name) for name, value in [*sources, *exports]
    )
    if generated < latest_source:
        raise ScoringTruthEventPhaseIntakeError(
            "intake generated_at predates a source bundle or submission export"
        )


def _require_nonnegative_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ScoringTruthEventPhaseIntakeError(
            f"{name} must be a non-negative integer"
        )
    return value


def _identity_key(value: Any, *, name: str) -> str:
    identifier = _require_identifier(value, name=name)
    return identifier.strip().casefold()


def _write_json(path: Path, value: Any) -> bytes:
    raw = (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _jsonl_bytes(records: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical_json_bytes(record) + b"\n" for record in records)


def _csv_bytes(fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(fields),
        extrasaction="raise",
        lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _content_root(artifacts: Sequence[Mapping[str, Any]]) -> str:
    projection = [
        {
            "path": item["path"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
            "role": item["role"],
        }
        for item in sorted(artifacts, key=lambda row: str(row["path"]))
    ]
    return _canonical_sha(projection)


def _execution_api() -> tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any], Callable[..., Any]]:
    try:
        from rallymate_annotation.scoring_truth_event_execution import (
            validate_scoring_truth_event_adjudication_bundle,
            validate_scoring_truth_event_adjudication_submission,
            validate_scoring_truth_event_annotation_submission,
            validate_scoring_truth_event_execution_bundle,
        )
    except (ImportError, AttributeError) as exc:
        raise ScoringTruthEventPhaseIntakeError(
            "event execution validators are unavailable"
        ) from exc
    return (
        validate_scoring_truth_event_execution_bundle,
        validate_scoring_truth_event_annotation_submission,
        validate_scoring_truth_event_adjudication_bundle,
        validate_scoring_truth_event_adjudication_submission,
    )


def _validated_inputs(
    *,
    execution_a_bundle_dir: str | Path,
    execution_a_submission_path: str | Path,
    execution_b_bundle_dir: str | Path,
    execution_b_submission_path: str | Path,
    adjudication_bundle_dir: str | Path,
    adjudication_submission_path: str | Path,
) -> dict[str, Mapping[str, Any]]:
    validate_bundle, validate_submission, validate_c_bundle, validate_c_submission = (
        _execution_api()
    )
    try:
        bundle_a = validate_bundle(execution_a_bundle_dir, expected_role_slot="A")
        submission_a = validate_submission(
            execution_a_submission_path, execution_a_bundle_dir
        )
        bundle_b = validate_bundle(execution_b_bundle_dir, expected_role_slot="B")
        submission_b = validate_submission(
            execution_b_submission_path, execution_b_bundle_dir
        )
        bundle_c = validate_c_bundle(adjudication_bundle_dir)
        submission_c = validate_c_submission(
            adjudication_submission_path, adjudication_bundle_dir
        )
    except ScoringTruthEventPhaseIntakeError:
        raise
    except (ValueError, OSError, TypeError, KeyError) as exc:
        raise ScoringTruthEventPhaseIntakeError(
            f"event execution input validation failed: {exc}"
        ) from exc
    snapshots = {
        "bundle_a": bundle_a,
        "submission_a": submission_a,
        "bundle_b": bundle_b,
        "submission_b": submission_b,
        "bundle_c": bundle_c,
        "submission_c": submission_c,
    }
    for name, snapshot in snapshots.items():
        if not isinstance(snapshot, Mapping):
            raise ScoringTruthEventPhaseIntakeError(
                f"execution validator returned an invalid {name} snapshot"
            )
    return snapshots


def _snapshot_bytes(snapshot: Mapping[str, Any], key: str, *, name: str) -> bytes:
    value = snapshot.get(key)
    if not isinstance(value, bytes):
        raise ScoringTruthEventPhaseIntakeError(
            f"{name} snapshot is missing byte-exact {key}"
        )
    return value


def _snapshot_object(snapshot: Mapping[str, Any], key: str, *, name: str) -> Mapping[str, Any]:
    value = snapshot.get(key)
    if not isinstance(value, Mapping):
        raise ScoringTruthEventPhaseIntakeError(
            f"{name} snapshot is missing object {key}"
        )
    return value


def _first_sha(value: Mapping[str, Any], keys: Sequence[str], *, name: str) -> str:
    for key in keys:
        candidate = value.get(key)
        if candidate is not None:
            return _require_sha(candidate, name=f"{name}.{key}")
    raise ScoringTruthEventPhaseIntakeError(f"{name} is missing {list(keys)}")


def _manifest_binding(snapshot: Mapping[str, Any], manifest: Mapping[str, Any], *, name: str) -> str:
    return _first_sha(
        snapshot,
        ("manifest_binding_sha256",),
        name=name,
    ) if snapshot.get("manifest_binding_sha256") is not None else _first_sha(
        manifest,
        ("manifest_binding_sha256",),
        name=f"{name}.manifest",
    )


def _bundle_record(
    snapshot: Mapping[str, Any], *, role_slot: str | None, path: str, adjudication: bool
) -> dict[str, Any]:
    manifest = _snapshot_object(snapshot, "manifest", name=path)
    raw = _snapshot_bytes(snapshot, "manifest_raw", name=path)
    raw_sha = _sha256(raw)
    declared_raw_sha = snapshot.get("manifest_sha256")
    if declared_raw_sha is not None and _require_sha(
        declared_raw_sha, name=f"{path}.manifest_sha256"
    ) != raw_sha:
        raise ScoringTruthEventPhaseIntakeError(
            f"{path} manifest snapshot raw SHA mismatch"
        )
    record: dict[str, Any] = {}
    if role_slot is not None:
        manifest_role = snapshot.get("role_slot")
        if manifest_role is None:
            manifest_role = manifest.get("role_slot")
        if manifest_role is None and isinstance(manifest.get("role"), Mapping):
            manifest_role = manifest["role"].get("slot")
        if manifest_role != role_slot:
            raise ScoringTruthEventPhaseIntakeError(
                f"{path} role_slot does not match {role_slot}"
            )
        record["role_slot"] = role_slot
    record.update(
        {
            "path": path,
            "raw_sha256": raw_sha,
            "manifest_binding_sha256": _manifest_binding(
                snapshot, manifest, name=path
            ),
            "bundle_id": _require_identifier(
                snapshot.get("bundle_id", manifest.get("bundle_id")),
                name=f"{path}.bundle_id",
            ),
        }
    )
    if not adjudication:
        record["execution_id"] = _require_identifier(
            snapshot.get("execution_id", manifest.get("execution_id")),
            name=f"{path}.execution_id",
        )
    record["content_root_sha256"] = _first_sha(
        snapshot,
        ("content_root_sha256",),
        name=path,
    ) if snapshot.get("content_root_sha256") is not None else _first_sha(
        manifest,
        ("content_root_sha256",),
        name=f"{path}.manifest",
    )
    return record


def _submission_snapshot(
    snapshot: Mapping[str, Any], *, role_slot: str, path: str
) -> tuple[dict[str, Any], bytes, Mapping[str, Any]]:
    submission = _snapshot_object(snapshot, "submission", name=path)
    raw = _snapshot_bytes(snapshot, "submission_raw", name=path)
    parsed = _strict_json_object(raw, name=path)
    if _canonical_json_bytes(parsed) != _canonical_json_bytes(submission):
        raise ScoringTruthEventPhaseIntakeError(
            f"{path} parsed submission differs from validator snapshot"
        )
    raw_sha = _sha256(raw)
    declared_raw = snapshot.get("submission_raw_sha256")
    if declared_raw is not None and _require_sha(
        declared_raw, name=f"{path}.submission_raw_sha256"
    ) != raw_sha:
        raise ScoringTruthEventPhaseIntakeError(
            f"{path} submission raw SHA mismatch"
        )
    if role_slot in {"A", "B"}:
        if submission.get("role_slot") != role_slot:
            raise ScoringTruthEventPhaseIntakeError(
                f"{path} submission role_slot mismatch"
            )
        participant = submission.get("annotator_id")
        revision_key = "submission_revision_sha256"
    else:
        if submission.get("reviewer_slot") != "C":
            raise ScoringTruthEventPhaseIntakeError(
                f"{path} submission reviewer_slot mismatch"
            )
        participant = submission.get("reviewer_id")
        revision_key = "adjudication_submission_revision_sha256"
    revision = _first_sha(
        snapshot, (revision_key,), name=path
    ) if snapshot.get(revision_key) is not None else _first_sha(
        submission, (revision_key,), name=f"{path}.submission"
    )
    if submission.get(revision_key) != revision:
        raise ScoringTruthEventPhaseIntakeError(
            f"{path} revision snapshot differs from submission"
        )
    record = {
        "role_slot": role_slot,
        "path": path,
        "raw_sha256": raw_sha,
        "participant_id": _require_identifier(participant, name=f"{path}.participant"),
        "revision_sha256": revision,
        "exported_at": _require_timestamp(
            submission.get("exported_at"), name=f"{path}.exported_at"
        ),
    }
    return record, raw, submission


def _submission_binding_sha(submission: Mapping[str, Any], *, name: str) -> str:
    return _first_sha(
        submission,
        ("authorization_binding_sha256",),
        name=name,
    )


def _submission_manifest_binding(submission: Mapping[str, Any], *, name: str) -> str:
    bundle_key = "adjudication_bundle" if submission.get("reviewer_slot") == "C" else "execution_bundle"
    bundle = submission.get(bundle_key)
    if not isinstance(bundle, Mapping):
        raise ScoringTruthEventPhaseIntakeError(
            f"{name}.{bundle_key} must be a bundle reference"
        )
    _require_exact_keys(
        bundle,
        {"bundle_id", "manifest_binding_sha256"},
        name=f"{name}.{bundle_key}",
    )
    _require_identifier(bundle["bundle_id"], name=f"{name}.{bundle_key}.bundle_id")
    return _require_sha(
        bundle["manifest_binding_sha256"],
        name=f"{name}.{bundle_key}.manifest_binding_sha256",
    )


def _plan_scope(plan: Mapping[str, Any]) -> tuple[list[str], dict[str, str]]:
    try:
        tasks = plan["scope"]["tasks"]
    except (KeyError, TypeError) as exc:
        raise ScoringTruthEventPhaseIntakeError(
            "authorized plan scope is incomplete"
        ) from exc
    if not isinstance(tasks, list) or len(tasks) != 3:
        raise ScoringTruthEventPhaseIntakeError(
            "authorized plan must contain exactly three tasks"
        )
    video_ids: list[str] = []
    task_by_video: dict[str, str] = {}
    for item in tasks:
        if not isinstance(item, Mapping):
            raise ScoringTruthEventPhaseIntakeError("plan task must be an object")
        video_id = _require_identifier(item.get("video_id"), name="plan video_id")
        task_id = _require_identifier(item.get("task_id"), name="plan task_id")
        if video_id in task_by_video:
            raise ScoringTruthEventPhaseIntakeError("plan video IDs must be unique")
        video_ids.append(video_id)
        task_by_video[video_id] = task_id
    return video_ids, task_by_video


def _confidence_milli(value: Any, *, name: str) -> int:
    milli = _require_nonnegative_int(value, name=name)
    if milli > 1000:
        raise ScoringTruthEventPhaseIntakeError(f"{name} must be in 0..1000")
    return milli


def _validate_phase_observations(
    event: Mapping[str, Any], *, name: str
) -> tuple[dict[str, Any], list[str]]:
    event_code = event.get("event_code")
    if event_code not in PHASE_KEYS_BY_EVENT:
        raise ScoringTruthEventPhaseIntakeError(f"{name}.event_code is invalid")
    start_ms = _require_nonnegative_int(event.get("start_ms"), name=f"{name}.start_ms")
    end_ms = _require_nonnegative_int(event.get("end_ms"), name=f"{name}.end_ms")
    if end_ms <= start_ms:
        raise ScoringTruthEventPhaseIntakeError(f"{name} boundaries are not ordered")
    phases = event.get("phase_observations")
    expected = set(PHASE_KEYS_BY_EVENT[event_code])
    _require_exact_keys(phases, expected, name=f"{name}.phase_observations")
    key_phases: dict[str, Any] = {}
    unobservable: list[str] = []
    observed_fs09: list[int] = []
    for phase_key in PHASE_KEYS_BY_EVENT[event_code]:
        phase = _require_exact_keys(
            phases[phase_key],
            {"status", "timestamp_ms", "reason"},
            name=f"{name}.{phase_key}",
        )
        status = phase["status"]
        reason = phase["reason"]
        if not isinstance(reason, str):
            raise ScoringTruthEventPhaseIntakeError(
                f"{name}.{phase_key}.reason must be a string"
            )
        if status == "observed":
            timestamp = _require_nonnegative_int(
                phase["timestamp_ms"], name=f"{name}.{phase_key}.timestamp_ms"
            )
            if timestamp < start_ms or timestamp > end_ms or reason != "":
                raise ScoringTruthEventPhaseIntakeError(
                    f"{name}.{phase_key} observed value is invalid"
                )
            key_phases[phase_key] = timestamp
            if event_code == "FS09":
                observed_fs09.append(timestamp)
        elif status == "unobservable":
            if phase["timestamp_ms"] is not None or not reason.strip():
                raise ScoringTruthEventPhaseIntakeError(
                    f"{name}.{phase_key} unobservable value is invalid"
                )
            key_phases[phase_key] = None
            unobservable.append(phase_key)
        else:
            raise ScoringTruthEventPhaseIntakeError(
                f"{name}.{phase_key}.status is invalid"
            )
    if event_code == "FS09" and any(
        value < observed_fs09[index - 1]
        for index, value in enumerate(observed_fs09)
        if index
    ):
        raise ScoringTruthEventPhaseIntakeError(
            f"{name} observed FS09 phases are not monotonic"
        )
    return key_phases, unobservable


def _source_indexes(
    submission_a: Mapping[str, Any], submission_b: Mapping[str, Any], video_ids: list[str]
) -> tuple[dict[tuple[str, str], tuple[str, Mapping[str, Any]]], dict[str, dict[str, str]], int]:
    index: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}
    video_revisions: dict[str, dict[str, str]] = {"A": {}, "B": {}}
    count = 0
    for slot, submission in (("A", submission_a), ("B", submission_b)):
        videos = submission.get("videos")
        if not isinstance(videos, list) or [item.get("video_id") for item in videos if isinstance(item, Mapping)] != video_ids:
            raise ScoringTruthEventPhaseIntakeError(
                f"submission {slot} must cover the three authorized videos in order"
            )
        for video in videos:
            _require_exact_keys(
                video,
                {"task_id", "video_id", "full_video_review", "events", "video_revision_sha256"},
                name=f"submission {slot} video",
            )
            video_id = video["video_id"]
            video_revisions[slot][video_id] = _require_sha(
                video["video_revision_sha256"],
                name=f"submission {slot} {video_id} video revision",
            )
            review = _require_exact_keys(
                video["full_video_review"],
                {"completed", "reviewed_at", "notes", "review_revision_sha256"},
                name=f"submission {slot} {video_id} review",
            )
            if review["completed"] is not True or not isinstance(review["notes"], str):
                raise ScoringTruthEventPhaseIntakeError(
                    f"submission {slot} {video_id} full-video review is incomplete"
                )
            _require_timestamp(
                review["reviewed_at"], name=f"submission {slot} {video_id} reviewed_at"
            )
            _require_sha(
                review["review_revision_sha256"],
                name=f"submission {slot} {video_id} review revision",
            )
            events = video["events"]
            if not isinstance(events, list):
                raise ScoringTruthEventPhaseIntakeError(
                    f"submission {slot} {video_id} events must be an array"
                )
            for event in events:
                if not isinstance(event, Mapping):
                    raise ScoringTruthEventPhaseIntakeError("source event must be an object")
                _require_exact_keys(
                    event,
                    {
                        "annotation_id",
                        "event_id",
                        "event_code",
                        "start_ms",
                        "end_ms",
                        "phase_observations",
                        "confidence_milli",
                        "boundary_uncertainty_ms",
                        "notes",
                        "annotated_at",
                        "annotation_revision_sha256",
                    },
                    name=f"submission {slot} source event",
                )
                annotation_id = _require_identifier(
                    event.get("annotation_id"), name=f"submission {slot} annotation_id"
                )
                if event.get("event_id") != annotation_id:
                    raise ScoringTruthEventPhaseIntakeError(
                        f"submission {slot} event_id must equal annotation_id"
                    )
                revision = _require_sha(
                    event.get("annotation_revision_sha256"),
                    name=f"submission {slot} annotation revision",
                )
                _validate_phase_observations(event, name=f"submission {slot} {annotation_id}")
                _confidence_milli(
                    event.get("confidence_milli"),
                    name=f"submission {slot} {annotation_id}.confidence_milli",
                )
                key = (slot, annotation_id)
                if key in index:
                    raise ScoringTruthEventPhaseIntakeError(
                        f"duplicate source annotation: {slot}/{annotation_id}"
                    )
                index[key] = (video_id, event)
                count += 1
    return index, video_revisions, count


def _assert_source_submission_summary(
    submission_c: Mapping[str, Any],
    submission_a: Mapping[str, Any],
    submission_b: Mapping[str, Any],
    video_revisions: Mapping[str, Mapping[str, str]],
    source_raw_sha256: Mapping[str, str],
) -> None:
    summary = _require_exact_keys(
        submission_c.get("source_submissions"), {"A", "B"}, name="C source_submissions"
    )
    for slot, submission in (("A", submission_a), ("B", submission_b)):
        expected = {
            "submission_id": submission["submission_id"],
            "raw_sha256": source_raw_sha256[slot],
            "annotator_id": submission["annotator_id"],
            "execution_bundle": submission["execution_bundle"],
            "submission_revision_sha256": submission["submission_revision_sha256"],
            "video_revision_sha256_by_video": dict(video_revisions[slot]),
        }
        if _canonical_json_bytes(summary[slot]) != _canonical_json_bytes(expected):
            raise ScoringTruthEventPhaseIntakeError(
                f"C source_submissions.{slot} does not bind the exact source revision"
            )


def _compile_outputs(
    *,
    plan: Mapping[str, Any],
    binding_sha256: str,
    submission_a: Mapping[str, Any],
    submission_b: Mapping[str, Any],
    submission_c: Mapping[str, Any],
    source_raw_sha256: Mapping[str, str],
    revision_lineage: Mapping[str, Any],
    intake_id: str,
    generated_at: str,
) -> dict[str, Any]:
    video_ids, task_by_video = _plan_scope(plan)
    index, video_revisions, source_event_count = _source_indexes(
        submission_a, submission_b, video_ids
    )
    _assert_source_submission_summary(
        submission_c,
        submission_a,
        submission_b,
        video_revisions,
        source_raw_sha256,
    )

    video_adjudications = submission_c.get("video_adjudications")
    if not isinstance(video_adjudications, list) or len(video_adjudications) != 3:
        raise ScoringTruthEventPhaseIntakeError(
            "C must explicitly complete exactly three full-video adjudication reviews"
        )
    for index_value, review in enumerate(video_adjudications):
        review = _require_exact_keys(
            review,
            {
                "task_id",
                "video_id",
                "completed",
                "notes",
                "adjudicated_at",
                "review_revision_sha256",
            },
            name=f"C video_adjudications[{index_value}]",
        )
        video_id = video_ids[index_value]
        if (
            review["video_id"] != video_id
            or review["task_id"] != task_by_video[video_id]
            or review["completed"] is not True
            or not isinstance(review["notes"], str)
        ):
            raise ScoringTruthEventPhaseIntakeError(
                f"C full-video adjudication review drifted: {video_id}"
            )
        _require_timestamp(
            review["adjudicated_at"],
            name=f"C {video_id} adjudicated_at",
        )
        _require_sha(
            review["review_revision_sha256"],
            name=f"C {video_id} review revision",
        )

    decisions = submission_c.get("decisions")
    if not isinstance(decisions, list):
        raise ScoringTruthEventPhaseIntakeError("C decisions must be an array")
    seen_adjudications: set[str] = set()
    seen_final_events: set[str] = set()
    coverage: dict[tuple[str, str], list[str]] = {}
    manual_records: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, Mapping):
            raise ScoringTruthEventPhaseIntakeError("C decision must be an object")
        _require_exact_keys(
            decision,
            {
                "adjudication_id",
                "video_id",
                "decision_status",
                "source_video_revisions",
                "source_annotation_revisions",
                "event",
                "decision_reason",
                "adjudicated_at",
                "adjudication_revision_sha256",
            },
            name="C decision",
        )
        adjudication_id = _require_identifier(
            decision.get("adjudication_id"), name="C adjudication_id"
        )
        if adjudication_id in seen_adjudications:
            raise ScoringTruthEventPhaseIntakeError(
                f"duplicate adjudication_id: {adjudication_id}"
            )
        seen_adjudications.add(adjudication_id)
        video_id = decision.get("video_id")
        if video_id not in task_by_video:
            raise ScoringTruthEventPhaseIntakeError(
                f"C decision uses an unauthorized video_id: {video_id}"
            )
        status = decision.get("decision_status")
        if status not in {"accepted_event", "rejected_sources", "c_added_event"}:
            raise ScoringTruthEventPhaseIntakeError(
                f"C decision_status is invalid: {status}"
            )
        reason = decision.get("decision_reason")
        if not isinstance(reason, str):
            raise ScoringTruthEventPhaseIntakeError(
                f"{adjudication_id}.decision_reason must be a string"
            )
        expected_video_revisions = {
            "A": video_revisions["A"][video_id],
            "B": video_revisions["B"][video_id],
        }
        if _canonical_json_bytes(decision.get("source_video_revisions")) != _canonical_json_bytes(expected_video_revisions):
            raise ScoringTruthEventPhaseIntakeError(
                f"{adjudication_id} source video revisions drifted"
            )
        refs = decision.get("source_annotation_revisions")
        if not isinstance(refs, list):
            raise ScoringTruthEventPhaseIntakeError(
                f"{adjudication_id} source refs must be an array"
            )
        normalized_refs: list[dict[str, Any]] = []
        local_refs: set[tuple[str, str]] = set()
        for reference in refs:
            reference = _require_exact_keys(
                reference,
                {"role_slot", "annotation_id", "annotation_revision_sha256", "relation"},
                name=f"{adjudication_id} source ref",
            )
            slot = reference["role_slot"]
            annotation_id = _require_identifier(
                reference["annotation_id"], name=f"{adjudication_id} source annotation_id"
            )
            key = (slot, annotation_id)
            if slot not in {"A", "B"} or key not in index:
                raise ScoringTruthEventPhaseIntakeError(
                    f"{adjudication_id} references an unknown source annotation"
                )
            if key in local_refs:
                raise ScoringTruthEventPhaseIntakeError(
                    f"{adjudication_id} repeats a source annotation"
                )
            local_refs.add(key)
            source_video_id, source_event = index[key]
            if (
                source_video_id != video_id
                or reference["annotation_revision_sha256"]
                != source_event["annotation_revision_sha256"]
            ):
                raise ScoringTruthEventPhaseIntakeError(
                    f"{adjudication_id} source revision does not match A/B"
                )
            relation = reference["relation"]
            if relation not in {"supports", "merge_source", "split_source", "rejected_source"}:
                raise ScoringTruthEventPhaseIntakeError(
                    f"{adjudication_id} source relation is invalid"
                )
            coverage.setdefault(key, []).append(relation)
            normalized_refs.append(dict(reference))

        event = decision.get("event")
        if status == "rejected_sources":
            if event is not None or not refs or not reason.strip() or any(
                ref["relation"] != "rejected_source" for ref in refs
            ):
                raise ScoringTruthEventPhaseIntakeError(
                    f"{adjudication_id} rejected_sources payload is invalid"
                )
            continue
        if not isinstance(event, Mapping):
            raise ScoringTruthEventPhaseIntakeError(
                f"{adjudication_id} requires a final event"
            )
        _require_exact_keys(
            event,
            {
                "event_id",
                "event_code",
                "start_ms",
                "end_ms",
                "phase_observations",
                "confidence_milli",
                "boundary_uncertainty_ms",
                "notes",
            },
            name=f"{adjudication_id} final event",
        )
        if status == "accepted_event" and not refs:
            raise ScoringTruthEventPhaseIntakeError(
                f"{adjudication_id} accepted_event requires source refs"
            )
        if status == "c_added_event" and (refs or not reason.strip()):
            raise ScoringTruthEventPhaseIntakeError(
                f"{adjudication_id} c_added_event requires no refs and a reason"
            )
        if any(ref["relation"] == "rejected_source" for ref in refs):
            raise ScoringTruthEventPhaseIntakeError(
                f"{adjudication_id} accepted decision contains a rejected source"
            )
        event_id = _require_identifier(event.get("event_id"), name="final event_id")
        if event_id in seen_final_events:
            raise ScoringTruthEventPhaseIntakeError(
                f"duplicate final event_id: {event_id}"
            )
        seen_final_events.add(event_id)
        key_phases, unobservable = _validate_phase_observations(
            event, name=f"final event {event_id}"
        )
        confidence_milli = _confidence_milli(
            event.get("confidence_milli"), name=f"{event_id}.confidence_milli"
        )
        boundary_uncertainty = _require_nonnegative_int(
            event.get("boundary_uncertainty_ms"),
            name=f"{event_id}.boundary_uncertainty_ms",
        )
        notes = event.get("notes")
        if not isinstance(notes, str):
            raise ScoringTruthEventPhaseIntakeError(
                f"{event_id}.notes must be a string"
            )
        adjudication_revision = _require_sha(
            decision.get("adjudication_revision_sha256"),
            name=f"{adjudication_id}.adjudication_revision_sha256",
        )
        flags = [
            "manual_event_phase_adjudicated",
            "source_view_unclassified",
            *[f"manual_phase_unobservable:{phase}" for phase in unobservable],
        ]
        record = {
            "schema_version": "1.0.0",
            "video_id": video_id,
            "event_id": event_id,
            "person_track_id": PERSON_TRACK_ID_PROJECTION,
            "event_code": event["event_code"],
            "start_ms": event["start_ms"],
            "end_ms": event["end_ms"],
            "key_phases_ms": key_phases,
            "confidence": confidence_milli / 1000,
            "confidence_milli": confidence_milli,
            "boundary_uncertainty_ms": boundary_uncertainty,
            "quality_flags": flags,
            "phase_observations": dict(event["phase_observations"]),
            "source_annotation_revisions": normalized_refs,
            "revision_lineage": dict(revision_lineage),
            "provenance": {
                "annotation_contract": INTAKE_VERSION,
                "annotation_only": True,
                "authorization_binding_sha256": binding_sha256,
                "adjudication_id": adjudication_id,
                "adjudication_revision_sha256": adjudication_revision,
                "decision_status": status,
                "decision_reason": reason,
                "notes": notes,
                "source_task_id": task_by_video[video_id],
                "projection_defaults": {
                    "person_track_id": PERSON_TRACK_ID_PROJECTION,
                    "view_group": VIEW_GROUP_PROJECTION,
                },
            },
            "annotation_source": "manual",
            "annotator_id": LEGACY_ANNOTATOR_ID,
            "view_group": VIEW_GROUP_PROJECTION,
            "reviewer_id": submission_c["reviewer_id"],
            "adjudication_status": "accepted",
        }
        manual_records.append(record)
        csv_row: dict[str, Any] = {field: "" for field in EVENT_CSV_FIELDS}
        csv_row.update(
            {
                "video_id": video_id,
                "event_id": event_id,
                "event_code": event["event_code"],
                "person_track_id": str(PERSON_TRACK_ID_PROJECTION),
                "start_ms": str(event["start_ms"]),
                "end_ms": str(event["end_ms"]),
                "annotation_confidence": f"{confidence_milli / 1000:.3f}",
                "boundary_uncertainty_ms": str(boundary_uncertainty),
                "view_group": VIEW_GROUP_PROJECTION,
                "annotator_id": LEGACY_ANNOTATOR_ID,
                "reviewer_id": submission_c["reviewer_id"],
                "adjudication_status": "accepted",
                "quality_flags": ";".join(flags),
            }
        )
        for phase_key, value in key_phases.items():
            csv_row[phase_key] = "" if value is None else str(value)
        csv_rows.append(csv_row)

    missing = sorted(set(index) - set(coverage))
    invalid_duplicates = sorted(
        key
        for key, relations in coverage.items()
        if len(relations) > 1 and any(value != "split_source" for value in relations)
    )
    if missing or invalid_duplicates:
        raise ScoringTruthEventPhaseIntakeError(
            "C adjudication does not provide complete, non-ambiguous A/B source coverage"
        )

    manual_records.sort(
        key=lambda row: (video_ids.index(row["video_id"]), row["start_ms"], row["event_id"])
    )
    row_by_event = {row["event_id"]: row for row in csv_rows}
    csv_rows = [row_by_event[row["event_id"]] for row in manual_records]

    review_rows: list[dict[str, Any]] = []
    for submission in (submission_a, submission_b):
        for video in submission["videos"]:
            review = video["full_video_review"]
            review_rows.append(
                {
                    "video_id": video["video_id"],
                    "annotator_id": submission["annotator_id"],
                    "full_video_review_completed": "true",
                    "reviewed_at": review["reviewed_at"],
                    "notes": review["notes"],
                }
            )
    if len(review_rows) != 6:
        raise ScoringTruthEventPhaseIntakeError(
            "full-video review projection must contain exactly six rows"
        )

    source_revisions = {
        "A": revision_lineage["annotator_a_export_root_sha256"],
        "B": revision_lineage["annotator_b_export_root_sha256"],
        "C": revision_lineage["reviewer_c_adjudication_root_sha256"],
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "report_id": "scoring-truth-event-phase-validation-"
        + revision_lineage["revision_id"][:24],
        "generated_at": generated_at,
        "status": REPORT_STATUS,
        "intake_id": intake_id,
        "source_revisions": source_revisions,
        "counts": {
            "videos": 3,
            "annotator_submissions": 2,
            "adjudication_submissions": 1,
            "source_events": source_event_count,
            "final_events": len(manual_records),
            "full_video_reviews": len(review_rows),
            "video_adjudications": len(video_adjudications),
        },
        "checks": {
            "authorization_reverified": True,
            "source_bundles_verified": True,
            "raw_bytes_preserved": True,
            "roles_distinct": True,
            "revision_lineage_complete": True,
            "source_coverage_complete": True,
            "compiled_outputs_replayed": True,
        },
        "projection_defaults": {
            "person_track_id": PERSON_TRACK_ID_PROJECTION,
            "view_group": VIEW_GROUP_PROJECTION,
        },
        "safety": dict(REPORT_SAFETY),
    }
    return {
        "manual_events": _jsonl_bytes(manual_records),
        "event_annotations": _csv_bytes(EVENT_CSV_FIELDS, csv_rows),
        "full_video_review_completion": _csv_bytes(REVIEW_CSV_FIELDS, review_rows),
        "validation_report_object": report,
        "validation_report": (
            json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        ).encode("utf-8"),
        "counts": report["counts"],
    }


def _roles_and_lineage(
    *,
    binding_sha256: str,
    submission_a: Mapping[str, Any],
    submission_b: Mapping[str, Any],
    submission_c: Mapping[str, Any],
) -> dict[str, Any]:
    a_id = _require_identifier(submission_a.get("annotator_id"), name="annotator A")
    b_id = _require_identifier(submission_b.get("annotator_id"), name="annotator B")
    c_id = _require_identifier(submission_c.get("reviewer_id"), name="reviewer C")
    if len({_identity_key(a_id, name="annotator A"), _identity_key(b_id, name="annotator B"), _identity_key(c_id, name="reviewer C")}) != 3:
        raise ScoringTruthEventPhaseIntakeError(
            "annotator A, annotator B, and reviewer C must be distinct"
        )
    a_root = _require_sha(
        submission_a.get("submission_revision_sha256"), name="annotator A root"
    )
    b_root = _require_sha(
        submission_b.get("submission_revision_sha256"), name="annotator B root"
    )
    c_root = _require_sha(
        submission_c.get("adjudication_submission_revision_sha256"),
        name="reviewer C root",
    )
    revision_id = _canonical_sha(
        {
            "authorization_binding_sha256": binding_sha256,
            "annotator_a_export_root_sha256": a_root,
            "annotator_b_export_root_sha256": b_root,
            "reviewer_c_adjudication_root_sha256": c_root,
        }
    )
    return {
        "revision_id": revision_id,
        "annotator_a_id": a_id,
        "annotator_b_id": b_id,
        "reviewer_c_id": c_id,
        "annotator_a_export_root_sha256": a_root,
        "annotator_b_export_root_sha256": b_root,
        "reviewer_c_adjudication_root_sha256": c_root,
        "roles_distinct": True,
        "revision_finalized": True,
    }


def _assert_lineage_bindings(
    binding: Mapping[str, Any],
    bundle_records: Mapping[str, Any],
    submissions: Mapping[str, Mapping[str, Any]],
) -> None:
    binding_sha = _require_sha(binding.get("binding_sha256"), name="event authorization")
    for slot in ("A", "B", "C"):
        submission = submissions[slot]
        if _submission_binding_sha(submission, name=f"submission {slot}") != binding_sha:
            raise ScoringTruthEventPhaseIntakeError(
                f"submission {slot} does not bind the exact event authorization"
            )
    for slot, record in (("A", bundle_records["execution"][0]), ("B", bundle_records["execution"][1])):
        if submissions[slot].get("execution_id") != record["execution_id"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"submission {slot} does not bind its execution_id"
            )
        if _submission_manifest_binding(
            submissions[slot], name=f"submission {slot}"
        ) != record["manifest_binding_sha256"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"submission {slot} does not bind its exact execution manifest projection"
            )
        expected_ref = {
            "bundle_id": record["bundle_id"],
            "manifest_binding_sha256": record["manifest_binding_sha256"],
        }
        if _canonical_json_bytes(submissions[slot].get("execution_bundle")) != _canonical_json_bytes(expected_ref):
            raise ScoringTruthEventPhaseIntakeError(
                f"submission {slot} execution bundle reference drifted"
            )
    if _submission_manifest_binding(
        submissions["C"], name="submission C"
    ) != bundle_records["adjudication"]["manifest_binding_sha256"]:
        raise ScoringTruthEventPhaseIntakeError(
            "submission C does not bind its exact adjudication manifest projection"
        )
    expected_c_ref = {
        "bundle_id": bundle_records["adjudication"]["bundle_id"],
        "manifest_binding_sha256": bundle_records["adjudication"]["manifest_binding_sha256"],
    }
    if _canonical_json_bytes(submissions["C"].get("adjudication_bundle")) != _canonical_json_bytes(expected_c_ref):
        raise ScoringTruthEventPhaseIntakeError(
            "submission C adjudication bundle reference drifted"
        )
    execution_ids = {submissions[slot].get("execution_id") for slot in ("A", "B", "C")}
    if len(execution_ids) != 1:
        raise ScoringTruthEventPhaseIntakeError(
            "A, B, and C submissions do not share one execution_id"
        )


def _artifact(path: str, raw: bytes, role: str) -> dict[str, Any]:
    return {"path": path, "bytes": len(raw), "sha256": _sha256(raw), "role": role}


def _copy_bytes(root: Path, path: str, raw: bytes) -> None:
    destination = _session_path(root, path, name="intake artifact")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)


def ingest_scoring_truth_event_phase(
    *,
    plan_path: str | Path,
    release_record_path: str | Path,
    handoff_dir: str | Path,
    execution_a_bundle_dir: str | Path,
    execution_a_submission_path: str | Path,
    execution_b_bundle_dir: str | Path,
    execution_b_submission_path: str | Path,
    adjudication_bundle_dir: str | Path,
    adjudication_submission_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Create one immutable M93 PRIVATE event/phase intake."""

    final = Path(output_dir).expanduser().absolute()
    if final.exists():
        raise FileExistsError(
            f"intake output already exists; choose a new immutable path: {final}"
        )
    source_roots = (
        Path(handoff_dir).expanduser().absolute(),
        Path(execution_a_bundle_dir).expanduser().absolute(),
        Path(execution_b_bundle_dir).expanduser().absolute(),
        Path(adjudication_bundle_dir).expanduser().absolute(),
    )
    if any(_paths_overlap(final, source) for source in source_roots):
        raise ScoringTruthEventPhaseIntakeError(
            "intake output must not overlap an authority or execution source tree"
        )
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = final.with_name(f".{final.name}.building-{uuid.uuid4().hex}")
    if staging.exists():
        raise ScoringTruthEventPhaseIntakeError("unexpected staging collision")

    try:
        try:
            binding = verify_scoring_truth_event_authorization(
                plan_path=plan_path,
                release_record_path=release_record_path,
                handoff_dir=handoff_dir,
            )
            validate_scoring_truth_authorization_binding(binding)
        except ScoringTruthAuthorizationError as exc:
            raise ScoringTruthEventPhaseIntakeError(
                f"event authorization is invalid: {exc}"
            ) from exc
        plan_raw = _snapshot_file(plan_path, name="annotation plan")
        release_raw = _snapshot_file(release_record_path, name="operator release")
        plan = _strict_json_object(plan_raw, name="annotation plan")
        handoff_tree = _snapshot_tree(handoff_dir, name="M89 handoff")

        snapshots = _validated_inputs(
            execution_a_bundle_dir=execution_a_bundle_dir,
            execution_a_submission_path=execution_a_submission_path,
            execution_b_bundle_dir=execution_b_bundle_dir,
            execution_b_submission_path=execution_b_submission_path,
            adjudication_bundle_dir=adjudication_bundle_dir,
            adjudication_submission_path=adjudication_submission_path,
        )
        bundle_records = {
            "execution": [
                _bundle_record(
                    snapshots["bundle_a"],
                    role_slot="A",
                    path=EXECUTION_A_MANIFEST_PATH,
                    adjudication=False,
                ),
                _bundle_record(
                    snapshots["bundle_b"],
                    role_slot="B",
                    path=EXECUTION_B_MANIFEST_PATH,
                    adjudication=False,
                ),
            ],
            "adjudication": _bundle_record(
                snapshots["bundle_c"],
                role_slot=None,
                path=ADJUDICATION_MANIFEST_PATH,
                adjudication=True,
            ),
        }
        raw_a, a_bytes, submission_a = _submission_snapshot(
            snapshots["submission_a"], role_slot="A", path=RAW_A_PATH
        )
        raw_b, b_bytes, submission_b = _submission_snapshot(
            snapshots["submission_b"], role_slot="B", path=RAW_B_PATH
        )
        raw_c, c_bytes, submission_c = _submission_snapshot(
            snapshots["submission_c"], role_slot="C", path=RAW_C_PATH
        )
        submissions = {"A": submission_a, "B": submission_b, "C": submission_c}
        _assert_lineage_bindings(binding, bundle_records, submissions)
        revision_lineage = _roles_and_lineage(
            binding_sha256=binding["binding_sha256"],
            submission_a=submission_a,
            submission_b=submission_b,
            submission_c=submission_c,
        )
        intake_id = "scoring-truth-event-phase-intake-" + revision_lineage[
            "revision_id"
        ][:24]
        source_manifests = {
            slot: _snapshot_object(
                snapshots[f"bundle_{slot.lower()}"],
                "manifest",
                name=f"source bundle {slot}",
            )
            for slot in ("A", "B", "C")
        }
        generated_at = _latest_canonical_timestamp(
            [
                ("intake clock", _utc_now()),
                *[
                    (f"source manifest {slot}.generated_at", source_manifests[slot].get("generated_at"))
                    for slot in ("A", "B", "C")
                ],
                *[
                    (f"submission {slot}.exported_at", submissions[slot].get("exported_at"))
                    for slot in ("A", "B", "C")
                ],
            ]
        )
        outputs = _compile_outputs(
            plan=plan,
            binding_sha256=binding["binding_sha256"],
            submission_a=submission_a,
            submission_b=submission_b,
            submission_c=submission_c,
            source_raw_sha256={"A": raw_a["raw_sha256"], "B": raw_b["raw_sha256"]},
            revision_lineage=revision_lineage,
            intake_id=intake_id,
            generated_at=generated_at,
        )

        staging.mkdir()
        artifacts: list[dict[str, Any]] = []
        authority_files = {PLAN_PATH: plan_raw, RELEASE_PATH: release_raw}
        authority_files.update(
            {f"{M89_DIRECTORY}/{path}": raw for path, raw in handoff_tree.items()}
        )
        for path, raw in sorted(authority_files.items()):
            _copy_bytes(staging, path, raw)
            artifacts.append(_artifact(path, raw, "authority_snapshot"))

        bundle_bytes = {
            EXECUTION_A_MANIFEST_PATH: _snapshot_bytes(
                snapshots["bundle_a"], "manifest_raw", name="execution A"
            ),
            EXECUTION_B_MANIFEST_PATH: _snapshot_bytes(
                snapshots["bundle_b"], "manifest_raw", name="execution B"
            ),
            ADJUDICATION_MANIFEST_PATH: _snapshot_bytes(
                snapshots["bundle_c"], "manifest_raw", name="adjudication"
            ),
        }
        for path, raw in bundle_bytes.items():
            _copy_bytes(staging, path, raw)
            artifacts.append(_artifact(path, raw, "source_bundle_manifest"))
        for path, raw in ((RAW_A_PATH, a_bytes), (RAW_B_PATH, b_bytes), (RAW_C_PATH, c_bytes)):
            _copy_bytes(staging, path, raw)
            artifacts.append(_artifact(path, raw, "raw_submission"))
        compiled = {
            MANUAL_EVENTS_PATH: (
                outputs["manual_events"],
                "compiled_manual_events",
            ),
            EVENT_ANNOTATIONS_PATH: (
                outputs["event_annotations"],
                "compiled_event_annotations",
            ),
            FULL_VIDEO_REVIEW_PATH: (
                outputs["full_video_review_completion"],
                "compiled_full_video_review_completion",
            ),
            VALIDATION_REPORT_PATH: (
                outputs["validation_report"],
                "validation_report",
            ),
        }
        for path, (raw, role) in compiled.items():
            _copy_bytes(staging, path, raw)
            artifacts.append(_artifact(path, raw, role))
        artifacts.sort(key=lambda item: item["path"])

        compilation = {
            "manual_events": {
                "path": MANUAL_EVENTS_PATH,
                "rows": outputs["counts"]["final_events"],
                "sha256": _sha256(outputs["manual_events"]),
            },
            "event_annotations": {
                "path": EVENT_ANNOTATIONS_PATH,
                "rows": outputs["counts"]["final_events"],
                "sha256": _sha256(outputs["event_annotations"]),
            },
            "full_video_review_completion": {
                "path": FULL_VIDEO_REVIEW_PATH,
                "rows": 6,
                "sha256": _sha256(outputs["full_video_review_completion"]),
            },
            "validation_report": {
                "path": VALIDATION_REPORT_PATH,
                "sha256": _sha256(outputs["validation_report"]),
            },
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "intake_version": INTAKE_VERSION,
            "intake_id": intake_id,
            "generated_at": generated_at,
            "status": INTAKE_STATUS,
            "classification": CLASSIFICATION,
            "event_authorization": dict(binding),
            "source_bundles": bundle_records,
            "revision_lineage": revision_lineage,
            "raw_submissions": [raw_a, raw_b, raw_c],
            "compilation": compilation,
            "directories": dict(DIRECTORIES),
            "artifacts": artifacts,
            "content_root_sha256": _content_root(artifacts),
            "safety": dict(SAFETY),
        }
        _write_json(staging / INTAKE_MANIFEST_NAME, manifest)

        # Re-run all external checks against their original locations immediately
        # before commit, then replay the self-contained staging intake.
        if _snapshot_file(plan_path, name="annotation plan") != plan_raw or _snapshot_file(
            release_record_path, name="operator release"
        ) != release_raw:
            raise ScoringTruthEventPhaseIntakeError(
                "event authorization inputs changed before commit"
            )
        second_binding = verify_scoring_truth_event_authorization(
            plan_path=plan_path,
            release_record_path=release_record_path,
            handoff_dir=handoff_dir,
        )
        if _canonical_json_bytes(second_binding) != _canonical_json_bytes(binding):
            raise ScoringTruthEventPhaseIntakeError(
                "event authorization changed before commit"
            )
        second = _validated_inputs(
            execution_a_bundle_dir=execution_a_bundle_dir,
            execution_a_submission_path=execution_a_submission_path,
            execution_b_bundle_dir=execution_b_bundle_dir,
            execution_b_submission_path=execution_b_submission_path,
            adjudication_bundle_dir=adjudication_bundle_dir,
            adjudication_submission_path=adjudication_submission_path,
        )
        for key, raw_key in (
            ("bundle_a", "manifest_raw"),
            ("bundle_b", "manifest_raw"),
            ("bundle_c", "manifest_raw"),
            ("submission_a", "submission_raw"),
            ("submission_b", "submission_raw"),
            ("submission_c", "submission_raw"),
        ):
            if _snapshot_bytes(second[key], raw_key, name=key) != _snapshot_bytes(
                snapshots[key], raw_key, name=key
            ):
                raise ScoringTruthEventPhaseIntakeError(
                    f"{key} changed before atomic commit"
                )
        validate_scoring_truth_event_phase_intake(staging)
        staging.replace(final)
        return manifest
    except BaseException:
        if staging.exists() and staging.is_dir() and not _is_link_like(staging):
            shutil.rmtree(staging)
        raise


def _walk_plain_tree(root: Path) -> tuple[set[str], set[str]]:
    directories: set[str] = set()
    files: set[str] = set()
    for current, names, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        names.sort()
        filenames.sort()
        for name in names:
            path = current_path / name
            if _is_link_like(path):
                raise ScoringTruthEventPhaseIntakeError(
                    f"intake contains a linked directory: {path}"
                )
            directories.add(path.relative_to(root).as_posix())
        for name in filenames:
            path = current_path / name
            if _is_link_like(path) or not path.is_file():
                raise ScoringTruthEventPhaseIntakeError(
                    f"intake contains a non-plain file: {path}"
                )
            files.add(path.relative_to(root).as_posix())
    return directories, files


def _expected_directories(files: set[str]) -> set[str]:
    result: set[str] = set()
    for value in files:
        parent = PurePosixPath(value).parent
        while parent.as_posix() != ".":
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _expected_artifact_role(path: str) -> str:
    if path in {PLAN_PATH, RELEASE_PATH} or path.startswith(f"{M89_DIRECTORY}/"):
        return "authority_snapshot"
    if path in {
        EXECUTION_A_MANIFEST_PATH,
        EXECUTION_B_MANIFEST_PATH,
        ADJUDICATION_MANIFEST_PATH,
    }:
        return "source_bundle_manifest"
    if path in {RAW_A_PATH, RAW_B_PATH, RAW_C_PATH}:
        return "raw_submission"
    if path == MANUAL_EVENTS_PATH:
        return "compiled_manual_events"
    if path == EVENT_ANNOTATIONS_PATH:
        return "compiled_event_annotations"
    if path == FULL_VIDEO_REVIEW_PATH:
        return "compiled_full_video_review_completion"
    if path == VALIDATION_REPORT_PATH:
        return "validation_report"
    raise ScoringTruthEventPhaseIntakeError(
        f"artifact is outside the exact intake topology: {path}"
    )


def _parse_artifacts(manifest: Mapping[str, Any], root: Path) -> list[dict[str, Any]]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ScoringTruthEventPhaseIntakeError("manifest.artifacts must be non-empty")
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed_roles = {
        "authority_snapshot",
        "source_bundle_manifest",
        "raw_submission",
        "compiled_manual_events",
        "compiled_event_annotations",
        "compiled_full_video_review_completion",
        "validation_report",
    }
    for item in artifacts:
        item = _require_exact_keys(
            item, {"path", "bytes", "sha256", "role"}, name="artifact"
        )
        path = _canonical_relative_path(item["path"], name="artifact.path")
        if path == INTAKE_MANIFEST_NAME or path in seen:
            raise ScoringTruthEventPhaseIntakeError(
                f"duplicate or recursive artifact path: {path}"
            )
        seen.add(path)
        expected_bytes = _require_nonnegative_int(item["bytes"], name=f"{path}.bytes")
        expected_sha = _require_sha(item["sha256"], name=f"{path}.sha256")
        if item["role"] not in allowed_roles:
            raise ScoringTruthEventPhaseIntakeError(f"{path} artifact role is invalid")
        if item["role"] != _expected_artifact_role(path):
            raise ScoringTruthEventPhaseIntakeError(
                f"{path} artifact role does not match its exact topology"
            )
        raw = _snapshot_file(_session_path(root, path, name="artifact"), name=path)
        if len(raw) != expected_bytes or _sha256(raw) != expected_sha:
            raise ScoringTruthEventPhaseIntakeError(f"artifact bytes changed: {path}")
        parsed.append(dict(item))
    if [item["path"] for item in parsed] != sorted(seen):
        raise ScoringTruthEventPhaseIntakeError("artifacts must be sorted by path")
    return parsed


def _validate_manifest_shape(manifest: Mapping[str, Any]) -> None:
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "intake_version",
            "intake_id",
            "generated_at",
            "status",
            "classification",
            "event_authorization",
            "source_bundles",
            "revision_lineage",
            "raw_submissions",
            "compilation",
            "directories",
            "artifacts",
            "content_root_sha256",
            "safety",
        },
        name="intake manifest",
    )
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["intake_version"] != INTAKE_VERSION
        or manifest["status"] != INTAKE_STATUS
        or manifest["classification"] != CLASSIFICATION
    ):
        raise ScoringTruthEventPhaseIntakeError("intake version/status is invalid")
    _require_identifier(manifest["intake_id"], name="intake_id")
    _require_timestamp(manifest["generated_at"], name="generated_at")
    if manifest["directories"] != DIRECTORIES:
        raise ScoringTruthEventPhaseIntakeError("intake directories drifted")
    if manifest["safety"] != SAFETY:
        raise ScoringTruthEventPhaseIntakeError("intake safety drifted")
    _require_sha(manifest["content_root_sha256"], name="content_root_sha256")


def _validate_source_records(
    manifest: Mapping[str, Any], root: Path
) -> tuple[dict[str, Mapping[str, Any]], dict[str, bytes]]:
    try:
        from rallymate_annotation.scoring_truth_event_execution import (
            scoring_truth_event_manifest_binding_sha256,
        )
    except (ImportError, AttributeError) as exc:
        raise ScoringTruthEventPhaseIntakeError(
            "event execution manifest projection validator is unavailable"
        ) from exc
    bundles = _require_exact_keys(
        manifest["source_bundles"], {"execution", "adjudication"}, name="source_bundles"
    )
    executions = bundles["execution"]
    if not isinstance(executions, list) or len(executions) != 2:
        raise ScoringTruthEventPhaseIntakeError(
            "source_bundles.execution must contain exact A then B"
        )
    for index, (slot, path) in enumerate(
        (("A", EXECUTION_A_MANIFEST_PATH), ("B", EXECUTION_B_MANIFEST_PATH))
    ):
        record = _require_exact_keys(
            executions[index],
            {
                "role_slot",
                "path",
                "raw_sha256",
                "manifest_binding_sha256",
                "bundle_id",
                "execution_id",
                "content_root_sha256",
            },
            name=f"execution {slot} record",
        )
        if record["role_slot"] != slot or record["path"] != path:
            raise ScoringTruthEventPhaseIntakeError(
                f"execution {slot} source record drifted"
            )
        _require_identifier(record["bundle_id"], name=f"execution {slot} bundle_id")
        _require_identifier(record["execution_id"], name=f"execution {slot} execution_id")
        for key in ("raw_sha256", "manifest_binding_sha256", "content_root_sha256"):
            _require_sha(record[key], name=f"execution {slot}.{key}")
    adjudication = _require_exact_keys(
        bundles["adjudication"],
        {
            "path",
            "raw_sha256",
            "manifest_binding_sha256",
            "bundle_id",
            "content_root_sha256",
        },
        name="adjudication source record",
    )
    if adjudication["path"] != ADJUDICATION_MANIFEST_PATH:
        raise ScoringTruthEventPhaseIntakeError("adjudication source path drifted")
    _require_identifier(adjudication["bundle_id"], name="adjudication bundle_id")
    for key in ("raw_sha256", "manifest_binding_sha256", "content_root_sha256"):
        _require_sha(adjudication[key], name=f"adjudication.{key}")

    parsed: dict[str, Mapping[str, Any]] = {}
    raw_by_slot: dict[str, bytes] = {}
    for slot, record in (("A", executions[0]), ("B", executions[1]), ("C", adjudication)):
        raw = _snapshot_file(
            _session_path(root, record["path"], name="source manifest"),
            name=f"source manifest {slot}",
        )
        if _sha256(raw) != record["raw_sha256"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} raw SHA mismatch"
            )
        source_manifest = _strict_json_object(raw, name=f"source manifest {slot}")
        try:
            replayed_binding = scoring_truth_event_manifest_binding_sha256(
                source_manifest
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} projection replay failed: {exc}"
            ) from exc
        if replayed_binding != record["manifest_binding_sha256"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} projection binding replay mismatch"
            )
        if source_manifest.get("manifest_binding_sha256") != record["manifest_binding_sha256"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} projection binding mismatch"
            )
        if source_manifest.get("bundle_id") != record["bundle_id"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} bundle_id mismatch"
            )
        if source_manifest.get("content_root_sha256") != record["content_root_sha256"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} content root mismatch"
            )
        if _canonical_json_bytes(source_manifest.get("event_authorization")) != _canonical_json_bytes(
            manifest["event_authorization"]
        ):
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} event authorization mismatch"
            )
        if slot != "C" and source_manifest.get("execution_id") != record["execution_id"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} execution_id mismatch"
            )
        role = source_manifest.get("role")
        source_slot = role.get("slot") if isinstance(role, Mapping) else source_manifest.get("role_slot")
        if source_slot != slot:
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} role mismatch"
            )
        expected_execution_id = executions[0]["execution_id"]
        if source_manifest.get("execution_id") != expected_execution_id:
            raise ScoringTruthEventPhaseIntakeError(
                f"source manifest {slot} execution lineage mismatch"
            )
        parsed[slot] = source_manifest
        raw_by_slot[slot] = raw
    return parsed, raw_by_slot


def _validate_raw_submission_records(
    manifest: Mapping[str, Any], root: Path
) -> tuple[dict[str, Mapping[str, Any]], dict[str, bytes]]:
    records = manifest["raw_submissions"]
    if not isinstance(records, list) or len(records) != 3:
        raise ScoringTruthEventPhaseIntakeError(
            "raw_submissions must contain exact A, B, C"
        )
    parsed: dict[str, Mapping[str, Any]] = {}
    raw_by_slot: dict[str, bytes] = {}
    for index, (slot, path, identity_key, revision_key) in enumerate(
        (
            ("A", RAW_A_PATH, "annotator_id", "submission_revision_sha256"),
            ("B", RAW_B_PATH, "annotator_id", "submission_revision_sha256"),
            ("C", RAW_C_PATH, "reviewer_id", "adjudication_submission_revision_sha256"),
        )
    ):
        record = _require_exact_keys(
            records[index],
            {"role_slot", "path", "raw_sha256", "participant_id", "revision_sha256", "exported_at"},
            name=f"raw submission {slot}",
        )
        if record["role_slot"] != slot or record["path"] != path:
            raise ScoringTruthEventPhaseIntakeError(
                f"raw submission {slot} order/path drifted"
            )
        raw = _snapshot_file(
            _session_path(root, path, name="raw submission"), name=f"submission {slot}"
        )
        if _sha256(raw) != _require_sha(record["raw_sha256"], name=f"submission {slot} raw"):
            raise ScoringTruthEventPhaseIntakeError(
                f"raw submission {slot} bytes changed"
            )
        submission = _strict_json_object(raw, name=f"submission {slot}")
        if slot in {"A", "B"}:
            _require_exact_keys(
                submission,
                {
                    "schema_version",
                    "submission_version",
                    "status",
                    "artifact_scope",
                    "execution_id",
                    "execution_bundle",
                    "authorization_binding_sha256",
                    "submission_id",
                    "role_slot",
                    "annotator_id",
                    "videos",
                    "submitted_at",
                    "submission_revision_sha256",
                    "exported_at",
                },
                name=f"submission {slot}",
            )
            if submission.get("role_slot") != slot:
                raise ScoringTruthEventPhaseIntakeError(
                    f"submission {slot} role_slot mismatch"
                )
        else:
            _require_exact_keys(
                submission,
                {
                    "schema_version",
                    "adjudication_version",
                    "status",
                    "artifact_scope",
                    "execution_id",
                    "adjudication_bundle",
                    "authorization_binding_sha256",
                    "reviewer_slot",
                    "reviewer_id",
                    "source_submissions",
                    "video_adjudications",
                    "decisions",
                    "adjudicated_at",
                    "adjudication_submission_revision_sha256",
                    "exported_at",
                },
                name="submission C",
            )
            if submission.get("reviewer_slot") != "C":
                raise ScoringTruthEventPhaseIntakeError(
                    "submission C reviewer_slot mismatch"
                )
        if submission.get(identity_key) != record["participant_id"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"raw submission {slot} participant mismatch"
            )
        if submission.get(revision_key) != record["revision_sha256"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"raw submission {slot} revision mismatch"
            )
        if submission.get("exported_at") != record["exported_at"]:
            raise ScoringTruthEventPhaseIntakeError(
                f"raw submission {slot} exported_at mismatch"
            )
        _require_identifier(record["participant_id"], name=f"submission {slot} participant")
        _require_sha(record["revision_sha256"], name=f"submission {slot} revision")
        _require_timestamp(record["exported_at"], name=f"submission {slot} exported_at")
        parsed[slot] = submission
        raw_by_slot[slot] = raw
    return parsed, raw_by_slot


def _validate_revision_lineage(
    manifest: Mapping[str, Any], submissions: Mapping[str, Mapping[str, Any]]
) -> Mapping[str, Any]:
    lineage = _require_exact_keys(
        manifest["revision_lineage"],
        {
            "revision_id",
            "annotator_a_id",
            "annotator_b_id",
            "reviewer_c_id",
            "annotator_a_export_root_sha256",
            "annotator_b_export_root_sha256",
            "reviewer_c_adjudication_root_sha256",
            "roles_distinct",
            "revision_finalized",
        },
        name="revision_lineage",
    )
    expected = _roles_and_lineage(
        binding_sha256=manifest["event_authorization"]["binding_sha256"],
        submission_a=submissions["A"],
        submission_b=submissions["B"],
        submission_c=submissions["C"],
    )
    if _canonical_json_bytes(lineage) != _canonical_json_bytes(expected):
        raise ScoringTruthEventPhaseIntakeError("revision_lineage replay mismatch")
    expected_intake_id = "scoring-truth-event-phase-intake-" + expected["revision_id"][:24]
    if manifest["intake_id"] != expected_intake_id:
        raise ScoringTruthEventPhaseIntakeError("intake_id replay mismatch")
    return lineage


def _validate_compilation_records(
    manifest: Mapping[str, Any], root: Path, expected: Mapping[str, bytes], final_events: int
) -> None:
    compilation = _require_exact_keys(
        manifest["compilation"],
        {
            "manual_events",
            "event_annotations",
            "full_video_review_completion",
            "validation_report",
        },
        name="compilation",
    )
    definitions = (
        ("manual_events", MANUAL_EVENTS_PATH, final_events),
        ("event_annotations", EVENT_ANNOTATIONS_PATH, final_events),
        ("full_video_review_completion", FULL_VIDEO_REVIEW_PATH, 6),
    )
    for key, path, rows in definitions:
        record = _require_exact_keys(
            compilation[key], {"path", "rows", "sha256"}, name=f"compilation.{key}"
        )
        if record["path"] != path or record["rows"] != rows:
            raise ScoringTruthEventPhaseIntakeError(f"compilation.{key} drifted")
        raw = _snapshot_file(_session_path(root, path, name=key), name=key)
        if raw != expected[key] or _sha256(raw) != record["sha256"]:
            raise ScoringTruthEventPhaseIntakeError(f"compiled {key} replay mismatch")
    report = _require_exact_keys(
        compilation["validation_report"], {"path", "sha256"}, name="validation_report"
    )
    if report["path"] != VALIDATION_REPORT_PATH:
        raise ScoringTruthEventPhaseIntakeError("validation report path drifted")
    raw = _snapshot_file(
        _session_path(root, VALIDATION_REPORT_PATH, name="validation report"),
        name="validation report",
    )
    if raw != expected["validation_report"] or _sha256(raw) != report["sha256"]:
        raise ScoringTruthEventPhaseIntakeError(
            "compiled validation report replay mismatch"
        )


def validate_scoring_truth_event_phase_intake(
    session_dir: str | Path,
) -> dict[str, Any]:
    """Replay an M93 intake from its PRIVATE, self-contained snapshots."""

    root = _require_plain_directory(session_dir, name="event-phase intake")
    manifest_raw = _snapshot_file(root / INTAKE_MANIFEST_NAME, name="intake manifest")
    manifest = _strict_json_object(manifest_raw, name="intake manifest")
    _validate_manifest_shape(manifest)
    artifacts = _parse_artifacts(manifest, root)
    if _content_root(artifacts) != manifest["content_root_sha256"]:
        raise ScoringTruthEventPhaseIntakeError("intake content root mismatch")
    actual_directories, actual_files = _walk_plain_tree(root)
    expected_files = {INTAKE_MANIFEST_NAME, *[item["path"] for item in artifacts]}
    if actual_files != expected_files or actual_directories != _expected_directories(expected_files):
        raise ScoringTruthEventPhaseIntakeError("intake exact topology drifted")

    try:
        verified_binding = verify_scoring_truth_event_authorization(
            plan_path=root / PLAN_PATH,
            release_record_path=root / RELEASE_PATH,
            handoff_dir=root / M89_DIRECTORY,
        )
        validate_scoring_truth_authorization_binding(verified_binding)
    except ScoringTruthAuthorizationError as exc:
        raise ScoringTruthEventPhaseIntakeError(
            f"snapshotted event authorization replay failed: {exc}"
        ) from exc
    if _canonical_json_bytes(verified_binding) != _canonical_json_bytes(
        manifest["event_authorization"]
    ):
        raise ScoringTruthEventPhaseIntakeError(
            "snapshotted event authorization binding drifted"
        )

    source_manifests, _source_raw = _validate_source_records(manifest, root)
    submissions, _submission_raw = _validate_raw_submission_records(manifest, root)
    _validate_intake_chronology(
        generated_at=manifest["generated_at"],
        source_manifests=source_manifests,
        submissions=submissions,
    )
    _assert_lineage_bindings(
        manifest["event_authorization"], manifest["source_bundles"], submissions
    )
    lineage = _validate_revision_lineage(manifest, submissions)
    plan = _strict_json_object(
        _snapshot_file(root / PLAN_PATH, name="annotation plan"),
        name="annotation plan",
    )
    outputs = _compile_outputs(
        plan=plan,
        binding_sha256=verified_binding["binding_sha256"],
        submission_a=submissions["A"],
        submission_b=submissions["B"],
        submission_c=submissions["C"],
        source_raw_sha256={
            "A": _sha256(_submission_raw["A"]),
            "B": _sha256(_submission_raw["B"]),
        },
        revision_lineage=lineage,
        intake_id=manifest["intake_id"],
        generated_at=manifest["generated_at"],
    )
    _validate_compilation_records(
        manifest,
        root,
        {
            "manual_events": outputs["manual_events"],
            "event_annotations": outputs["event_annotations"],
            "full_video_review_completion": outputs[
                "full_video_review_completion"
            ],
            "validation_report": outputs["validation_report"],
        },
        outputs["counts"]["final_events"],
    )
    return {
        "session_dir": root,
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "manifest_sha256": _sha256(manifest_raw),
        "content_root_sha256": manifest["content_root_sha256"],
        "artifacts": artifacts,
    }


__all__ = [
    "CLASSIFICATION",
    "INTAKE_STATUS",
    "INTAKE_VERSION",
    "ScoringTruthEventPhaseIntakeError",
    "ingest_scoring_truth_event_phase",
    "validate_scoring_truth_event_phase_intake",
]
