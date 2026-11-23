from __future__ import annotations

"""Immutable intake for the main RallyMate scoring-truth CSV exports.

An intake session is deliberately a private evidence container.  It preserves
the five submitted CSV files byte-for-byte, compiles a working copy with the
existing truth-pack compiler, and records enough identity information to
replay that compilation later.  Validation intentionally requires the original
source pack and all five submitted export paths to remain available and
byte-exact; this private authority-retention bundle is not portable,
self-contained evidence.  It is not an operator authorization, a public
handoff, a calibration approval, or a production promotion.
"""

import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from rallymate_annotation.truth_pack import (
    COACH_CSV_FIELDS,
    EVENT_CSV_FIELDS,
    KEYPOINT_CSV_FIELDS,
    REVIEW_CSV_FIELDS,
    SEMANTIC_CSV_FIELDS,
    compile_truth_pack,
)


INTAKE_VERSION = "scoring-truth-intake-v1.0.0"
INTAKE_STATUS = "private_candidate_containing_intake_not_operator_authorized"
INTAKE_MANIFEST_NAME = "intake-manifest.json"
RAW_EXPORTS_DIRECTORY = "raw-exports"
COMPILED_DIRECTORY = "compiled"

EXPORT_FIELDS: dict[str, tuple[str, ...]] = {
    "event-annotations.csv": tuple(EVENT_CSV_FIELDS),
    "keypoint-annotations.csv": tuple(KEYPOINT_CSV_FIELDS),
    "semantic-annotations.csv": tuple(SEMANTIC_CSV_FIELDS),
    "coach-labels.csv": tuple(COACH_CSV_FIELDS),
    "full-video-review-completion.csv": tuple(REVIEW_CSV_FIELDS),
}
EXPORT_FILENAMES = tuple(EXPORT_FIELDS)

_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_RETENTION_REQUIREMENT = (
    "original_source_pack_and_all_five_export_paths_must_remain_available_"
    "and_byte_exact_for_private_authority_replay"
)
_SAFETY = {
    "private_session": True,
    "candidate_information_present": True,
    "operator_authorized": False,
    "external_protocol_receipt_verified": False,
    "technical_handoff_accepted_as_authorization": False,
    "source_authorization_accepted": False,
    "human_truth_ready_claimed": False,
    "calibration_eligible": False,
    "promotion_eligible": False,
    "production_use_authorized": False,
    "source_pack_modified": False,
    "raw_exports_preserved_byte_exact": True,
    "candidate_values_promoted_to_truth": False,
    "grades_generated_by_intake": False,
    "thresholds_generated": False,
    "automatic_F3_or_F4_promotion": False,
}


class ScoringTruthIntakeError(ValueError):
    """Raised when an intake cannot be proven immutable and replayable."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ScoringTruthIntakeError(f"value is not canonical JSON: {exc}") from exc


def _content_root(artifacts: Sequence[Mapping[str, Any]]) -> str:
    rows = [
        {
            "path": item["path"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
            "role": item["role"],
        }
        for item in sorted(artifacts, key=lambda item: str(item["path"]))
    ]
    return _sha256(_canonical_json_bytes(rows))


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _strict_json(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ScoringTruthIntakeError(f"{name} must be strict UTF-8: {exc}") from exc

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ScoringTruthIntakeError(
                    f"{name} contains duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ScoringTruthIntakeError(
            f"{name} contains non-finite JSON number: {value}"
        )

    try:
        value = json.loads(
            text,
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except ScoringTruthIntakeError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ScoringTruthIntakeError(f"{name} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ScoringTruthIntakeError(f"{name} must contain one JSON object")
    return value


def _is_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ScoringTruthIntakeError(f"cannot inspect path {path}: {exc}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _require_plain_directory(path: Path, *, name: str) -> Path:
    resolved = path.resolve()
    if not path.exists() or not path.is_dir():
        raise ScoringTruthIntakeError(f"{name} is not a directory: {path}")
    if _is_reparse(path):
        raise ScoringTruthIntakeError(f"{name} must not be a symlink/reparse point")
    return resolved


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    return _path_contains(left, right) or _path_contains(right, left)


def _canonical_relative_path(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ScoringTruthIntakeError(f"{name} must be a canonical POSIX path")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or pure.as_posix() != value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ScoringTruthIntakeError(f"{name} must be a canonical relative path")
    return value


def _session_path(root: Path, relative: str, *, name: str) -> Path:
    relative = _canonical_relative_path(relative, name=name)
    target = (root / Path(*PurePosixPath(relative).parts)).resolve()
    if not _path_contains(root, target):
        raise ScoringTruthIntakeError(f"{name} escapes the intake session")
    return target


def _require_exact_keys(value: Any, expected: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ScoringTruthIntakeError(f"{name} fields drifted")
    return value


def _require_sha256(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ScoringTruthIntakeError(f"{name} must be uppercase SHA-256")
    return value


def _require_nonnegative_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ScoringTruthIntakeError(f"{name} must be a non-negative integer")
    return value


def _read_csv_snapshot(
    path: Path,
    *,
    expected_header: tuple[str, ...],
    name: str,
) -> dict[str, Any]:
    if not path.is_file() or _is_reparse(path):
        raise ScoringTruthIntakeError(f"{name} is not a plain file: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ScoringTruthIntakeError(f"cannot read {name}: {exc}") from exc
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ScoringTruthIntakeError(f"{name} must be strict UTF-8 CSV: {exc}") from exc
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ScoringTruthIntakeError(f"{name} is malformed CSV: {exc}") from exc
    if not rows:
        raise ScoringTruthIntakeError(f"{name} is missing its header")
    header = rows[0]
    if len(set(header)) != len(header):
        raise ScoringTruthIntakeError(f"{name} contains a duplicate header field")
    if tuple(header) != expected_header:
        raise ScoringTruthIntakeError(f"{name} header does not exactly match its contract")
    for row_number, row in enumerate(rows[1:], start=2):
        if row == header:
            raise ScoringTruthIntakeError(
                f"{name} row {row_number} repeats the CSV header"
            )
        if len(row) != len(header):
            raise ScoringTruthIntakeError(
                f"{name} row {row_number} has {len(row)} columns; expected {len(header)}"
            )
    return {
        "path": path.resolve(),
        "raw": raw,
        "sha256": _sha256(raw),
        "bytes": len(raw),
        "rows": len(rows) - 1,
    }


def _coerce_exports(
    export_paths: Mapping[str, str | Path] | Sequence[str | Path],
) -> dict[str, Path]:
    if isinstance(export_paths, Mapping):
        supplied = {str(name): Path(value) for name, value in export_paths.items()}
    elif isinstance(export_paths, Sequence) and not isinstance(
        export_paths, (str, bytes, bytearray)
    ):
        supplied = {}
        for value in export_paths:
            path = Path(value)
            if path.name in supplied:
                raise ScoringTruthIntakeError(
                    f"duplicate scoring-truth export filename: {path.name}"
                )
            supplied[path.name] = path
    else:
        raise ScoringTruthIntakeError(
            "export_paths must be a filename-to-path mapping or a sequence"
        )
    expected = set(EXPORT_FILENAMES)
    if set(supplied) != expected:
        missing = sorted(expected - set(supplied))
        extra = sorted(set(supplied) - expected)
        raise ScoringTruthIntakeError(
            f"exactly five scoring-truth exports are required; missing={missing}, extra={extra}"
        )
    resolved = {name: supplied[name].resolve() for name in EXPORT_FILENAMES}
    if len(set(resolved.values())) != len(EXPORT_FILENAMES):
        raise ScoringTruthIntakeError("the five scoring-truth exports must be distinct files")
    return resolved


def _walk_tree(root: Path) -> tuple[list[str], list[str]]:
    directories: list[str] = []
    files: list[str] = []
    for current, names, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        names.sort()
        filenames.sort()
        for directory_name in list(names):
            path = current_path / directory_name
            if _is_reparse(path):
                raise ScoringTruthIntakeError(
                    f"tree contains a symlink/reparse directory: {path}"
                )
            directories.append(path.relative_to(root).as_posix())
        for filename in filenames:
            path = current_path / filename
            if _is_reparse(path) or not path.is_file():
                raise ScoringTruthIntakeError(
                    f"tree contains a non-plain file: {path}"
                )
            files.append(path.relative_to(root).as_posix())
    return sorted(directories), sorted(files)


def _snapshot_source_pack(root: Path) -> dict[str, Any]:
    root = _require_plain_directory(root, name="source pack")
    directories: list[str] = []
    files: list[dict[str, Any]] = []
    for current, names, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        names.sort()
        filenames.sort()
        for directory_name in list(names):
            path = current_path / directory_name
            if _is_reparse(path):
                raise ScoringTruthIntakeError(
                    f"source pack contains a symlink/reparse directory: {path}"
                )
        if current_path == root:
            if RAW_EXPORTS_DIRECTORY in names:
                raise ScoringTruthIntakeError(
                    "source pack already contains reserved raw-exports directory"
                )
            names[:] = [name for name in names if name != COMPILED_DIRECTORY]
        for directory_name in names:
            directories.append(
                (current_path / directory_name).relative_to(root).as_posix()
            )
        for filename in filenames:
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            if _is_reparse(path) or not path.is_file():
                raise ScoringTruthIntakeError(
                    f"source pack contains a non-plain file: {path}"
                )
            if current_path == root and filename in EXPORT_FILENAMES:
                continue
            if current_path == root and filename == INTAKE_MANIFEST_NAME:
                raise ScoringTruthIntakeError(
                    "source pack is already an intake session, not a static source pack"
                )
            raw = path.read_bytes()
            files.append(
                {
                    "relative_path": relative,
                    "path": path.resolve(),
                    "raw": raw,
                    "bytes": len(raw),
                    "sha256": _sha256(raw),
                }
            )
    if "manifest.json" not in {item["relative_path"] for item in files}:
        raise ScoringTruthIntakeError("source pack is missing manifest.json")
    return {
        "root": root,
        "directories": sorted(directories),
        "files": sorted(files, key=lambda item: item["relative_path"]),
    }


def _source_snapshot_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if left["root"] != right["root"] or left["directories"] != right["directories"]:
        return False
    left_files = [
        (item["relative_path"], item["path"], item["raw"])
        for item in left["files"]
    ]
    right_files = [
        (item["relative_path"], item["path"], item["raw"])
        for item in right["files"]
    ]
    return left_files == right_files


def _copy_source_snapshot(snapshot: Mapping[str, Any], target: Path) -> None:
    for relative in snapshot["directories"]:
        _session_path(target, relative, name="source-pack directory").mkdir(
            parents=True, exist_ok=True
        )
    for item in snapshot["files"]:
        destination = _session_path(
            target, item["relative_path"], name="source-pack static file"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(item["raw"])


def _normalize_compilation_report(
    report: Mapping[str, Any], pack_root: Path
) -> dict[str, Any]:
    normalized = json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))
    outputs = normalized.get("outputs")
    if not isinstance(outputs, dict):
        raise ScoringTruthIntakeError("truth compiler report.outputs is missing")

    def normalize_output(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: normalize_output(item) for key, item in value.items()}
        if not isinstance(value, str):
            raise ScoringTruthIntakeError(
                "truth compiler output paths must be strings or nested objects"
            )
        try:
            relative = Path(value).resolve().relative_to(pack_root.resolve()).as_posix()
        except ValueError as exc:
            raise ScoringTruthIntakeError(
                f"truth compiler output escaped staging pack: {value}"
            ) from exc
        return _canonical_relative_path(relative, name="truth compiler output")

    normalized["outputs"] = normalize_output(outputs)
    return normalized


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def _artifact_role(path: str, static_paths: set[str]) -> str:
    if path in static_paths:
        return "source_static"
    if path in EXPORT_FILENAMES:
        return "active_export"
    if path.startswith(f"{RAW_EXPORTS_DIRECTORY}/"):
        return "raw_export"
    if path.startswith(f"{COMPILED_DIRECTORY}/"):
        return "compiled"
    raise ScoringTruthIntakeError(f"session contains an unclassified artifact: {path}")


def _snapshot_session_artifacts(
    root: Path, *, static_paths: set[str]
) -> tuple[list[str], list[dict[str, Any]]]:
    directories, files = _walk_tree(root)
    if INTAKE_MANIFEST_NAME in files:
        files.remove(INTAKE_MANIFEST_NAME)
    artifacts = []
    for relative in files:
        path = _session_path(root, relative, name="session artifact")
        raw = path.read_bytes()
        artifacts.append(
            {
                "path": relative,
                "bytes": len(raw),
                "sha256": _sha256(raw),
                "role": _artifact_role(relative, static_paths),
            }
        )
    return directories, artifacts


def _source_file_record(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": str(item["path"]),
        "session_path": item["relative_path"],
        "bytes": item["bytes"],
        "sha256": item["sha256"],
    }


def _assert_inputs_unchanged(
    source_snapshot: Mapping[str, Any],
    export_snapshots: Mapping[str, Mapping[str, Any]],
) -> None:
    current_source = _snapshot_source_pack(Path(source_snapshot["root"]))
    if not _source_snapshot_equal(source_snapshot, current_source):
        raise ScoringTruthIntakeError("source pack changed before atomic commit")
    for name, snapshot in export_snapshots.items():
        path = Path(snapshot["path"])
        if not path.is_file() or _is_reparse(path) or path.read_bytes() != snapshot["raw"]:
            raise ScoringTruthIntakeError(
                f"{name} changed before atomic snapshot commit"
            )


def ingest_scoring_truth_exports(
    source_pack_dir: str | Path,
    export_paths: Mapping[str, str | Path] | Sequence[str | Path],
    output_dir: str | Path,
) -> dict[str, Any]:
    """Atomically create one immutable private scoring-truth intake session."""

    source_pack = Path(source_pack_dir).resolve()
    final = Path(output_dir).resolve()
    if _paths_overlap(source_pack, final):
        raise ScoringTruthIntakeError(
            "source pack and intake output must not be ancestors or descendants"
        )
    if final.exists():
        raise FileExistsError(
            f"intake output already exists; choose a new immutable path: {final}"
        )
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = final.with_name(f".{final.name}.building-{uuid.uuid4().hex}")
    if staging.exists():
        raise ScoringTruthIntakeError(f"unexpected staging collision: {staging}")

    exports = _coerce_exports(export_paths)
    source_snapshot = _snapshot_source_pack(source_pack)
    manifest_item = next(
        item
        for item in source_snapshot["files"]
        if item["relative_path"] == "manifest.json"
    )
    source_manifest = _strict_json(
        manifest_item["raw"], name="source-pack manifest.json"
    )
    if not isinstance(source_manifest.get("pack_version"), str):
        raise ScoringTruthIntakeError("source-pack manifest.pack_version is missing")

    export_snapshots = {
        name: _read_csv_snapshot(
            path,
            expected_header=EXPORT_FIELDS[name],
            name=name,
        )
        for name, path in exports.items()
    }

    committed = False
    try:
        staging.mkdir()
        _copy_source_snapshot(source_snapshot, staging)
        raw_root = staging / RAW_EXPORTS_DIRECTORY
        raw_root.mkdir()
        for name in EXPORT_FILENAMES:
            raw = export_snapshots[name]["raw"]
            (staging / name).write_bytes(raw)
            (raw_root / name).write_bytes(raw)

        try:
            report = compile_truth_pack(staging)
        except Exception as exc:
            raise ScoringTruthIntakeError(
                f"truth-pack compiler could not validate the intake: {exc}"
            ) from exc
        errors = report.get("errors")
        if not isinstance(errors, list) or errors:
            details = "; ".join(str(item) for item in errors or [])
            raise ScoringTruthIntakeError(
                f"truth-pack compiler rejected the exports: {details or 'invalid report'}"
            )
        normalized_report = _normalize_compilation_report(report, staging)
        _write_json(
            staging / COMPILED_DIRECTORY / "validation-report.json",
            normalized_report,
        )

        static_paths = {
            item["relative_path"] for item in source_snapshot["files"]
        }
        directories, artifacts = _snapshot_session_artifacts(
            staging, static_paths=static_paths
        )
        artifact_by_path = {item["path"]: item for item in artifacts}
        report_artifact = artifact_by_path.get("compiled/validation-report.json")
        if report_artifact is None:
            raise ScoringTruthIntakeError("compiler did not emit validation-report.json")
        root_sha = _content_root(artifacts)
        generated_at = _utc_now()
        intake_manifest = {
            "schema_version": "1.0.0",
            "intake_version": INTAKE_VERSION,
            "intake_id": f"scoring-truth-intake-{root_sha[:24]}",
            "generated_at": generated_at,
            "status": INTAKE_STATUS,
            "classification": "PRIVATE",
            "source_pack": {
                "path": str(source_pack),
                "pack_version": source_manifest["pack_version"],
                "source_status": source_manifest.get("status"),
                "retention_requirement": _RETENTION_REQUIREMENT,
                "manifest": _source_file_record(manifest_item),
                "static_directories": source_snapshot["directories"],
                "static_files": [
                    _source_file_record(item) for item in source_snapshot["files"]
                ],
            },
            "exports": {
                name: {
                    "source_path": str(export_snapshots[name]["path"]),
                    "submitted_name": Path(export_snapshots[name]["path"]).name,
                    "pack_path": name,
                    "raw_path": f"{RAW_EXPORTS_DIRECTORY}/{name}",
                    "bytes": export_snapshots[name]["bytes"],
                    "sha256": export_snapshots[name]["sha256"],
                    "rows": export_snapshots[name]["rows"],
                }
                for name in EXPORT_FILENAMES
            },
            "compilation": {
                "report_path": "compiled/validation-report.json",
                "report_sha256": report_artifact["sha256"],
                "status": normalized_report["status"],
                "readiness_interpretation": (
                    "diagnostic_only_not_authorization_or_calibration_eligibility"
                ),
                "errors_count": 0,
                "readiness_sha256": _sha256(
                    _canonical_json_bytes(normalized_report["readiness"])
                ),
                "readiness": normalized_report["readiness"],
            },
            "directories": directories,
            "artifacts": artifacts,
            "content_root_sha256": root_sha,
            "safety": dict(_SAFETY),
        }
        _write_json(staging / INTAKE_MANIFEST_NAME, intake_manifest)

        # First prove the staged tree.  Then re-read every external input as the
        # last pre-commit action so a changed source is never silently mixed in.
        validate_scoring_truth_intake(staging)
        _assert_inputs_unchanged(source_snapshot, export_snapshots)
        if final.exists():
            raise FileExistsError(
                f"intake output appeared before commit and will not be replaced: {final}"
            )
        staging.rename(final)
        committed = True
        return validate_scoring_truth_intake(final)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if committed and final.exists():
            shutil.rmtree(final)
        raise


def _validate_source_file_record(
    value: Any,
    *,
    source_root: Path,
    name: str,
) -> tuple[str, bytes]:
    record = _require_exact_keys(
        value,
        {"path", "session_path", "bytes", "sha256"},
        name=name,
    )
    relative = _canonical_relative_path(record["session_path"], name=f"{name}.session_path")
    expected_source = (source_root / Path(*PurePosixPath(relative).parts)).resolve()
    if str(expected_source) != record["path"]:
        raise ScoringTruthIntakeError(f"{name}.path is not bound to source_pack.path")
    if not expected_source.is_file() or _is_reparse(expected_source):
        raise ScoringTruthIntakeError(f"{name} source file is missing or not plain")
    raw = expected_source.read_bytes()
    if (
        len(raw) != _require_nonnegative_int(record["bytes"], name=f"{name}.bytes")
        or _sha256(raw) != _require_sha256(record["sha256"], name=f"{name}.sha256")
    ):
        raise ScoringTruthIntakeError(f"{name} source bytes changed")
    return relative, raw


def _replay_compilation(
    session: Path,
    *,
    static_directories: Sequence[str],
    static_paths: Sequence[str],
) -> tuple[dict[str, Any], dict[str, bytes], list[str]]:
    with tempfile.TemporaryDirectory(prefix="rallymate-scoring-truth-replay-") as directory:
        replay = Path(directory) / "pack"
        replay.mkdir()
        for relative in static_directories:
            _session_path(replay, relative, name="replay static directory").mkdir(
                parents=True, exist_ok=True
            )
        for relative in static_paths:
            source = _session_path(session, relative, name="session static file")
            target = _session_path(replay, relative, name="replay static file")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        for filename in EXPORT_FILENAMES:
            (replay / filename).write_bytes((session / filename).read_bytes())
        try:
            report = compile_truth_pack(replay)
        except Exception as exc:
            raise ScoringTruthIntakeError(
                f"replayed truth-pack compilation failed: {exc}"
            ) from exc
        if not isinstance(report.get("errors"), list) or report["errors"]:
            raise ScoringTruthIntakeError(
                "replayed truth-pack compilation reports annotation errors"
            )
        normalized = _normalize_compilation_report(report, replay)
        _write_json(replay / COMPILED_DIRECTORY / "validation-report.json", normalized)
        directories, files = _walk_tree(replay / COMPILED_DIRECTORY)
        file_bytes = {
            relative: (replay / COMPILED_DIRECTORY / Path(*PurePosixPath(relative).parts)).read_bytes()
            for relative in files
        }
        return normalized, file_bytes, directories


def validate_scoring_truth_intake(session_dir: str | Path) -> dict[str, Any]:
    """Validate exact topology, external bindings, and deterministic replay."""

    session = _require_plain_directory(Path(session_dir), name="intake session")
    manifest_path = session / INTAKE_MANIFEST_NAME
    if not manifest_path.is_file() or _is_reparse(manifest_path):
        raise ScoringTruthIntakeError("intake session is missing intake-manifest.json")
    manifest = _strict_json(manifest_path.read_bytes(), name=INTAKE_MANIFEST_NAME)
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "intake_version",
            "intake_id",
            "generated_at",
            "status",
            "classification",
            "source_pack",
            "exports",
            "compilation",
            "directories",
            "artifacts",
            "content_root_sha256",
            "safety",
        },
        name="intake manifest",
    )
    if (
        manifest["schema_version"] != "1.0.0"
        or manifest["intake_version"] != INTAKE_VERSION
        or manifest["status"] != INTAKE_STATUS
        or manifest["classification"] != "PRIVATE"
    ):
        raise ScoringTruthIntakeError("unsupported or unsafe scoring-truth intake contract")
    if not isinstance(manifest["generated_at"], str) or _UTC_RE.fullmatch(
        manifest["generated_at"]
    ) is None:
        raise ScoringTruthIntakeError("generated_at must be a strict UTC Z timestamp")
    try:
        generated_at = datetime.strptime(
            manifest["generated_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ScoringTruthIntakeError(
            "generated_at must be a semantically valid UTC timestamp"
        ) from exc
    if (
        generated_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
        != manifest["generated_at"]
    ):
        raise ScoringTruthIntakeError("generated_at is not canonical UTC")
    if manifest["safety"] != _SAFETY:
        raise ScoringTruthIntakeError("intake safety declarations drifted")

    source = _require_exact_keys(
        manifest["source_pack"],
        {
            "path",
            "pack_version",
            "source_status",
            "retention_requirement",
            "manifest",
            "static_directories",
            "static_files",
        },
        name="source_pack",
    )
    if not isinstance(source["path"], str) or not Path(source["path"]).is_absolute():
        raise ScoringTruthIntakeError("source_pack.path must be absolute")
    if source["retention_requirement"] != _RETENTION_REQUIREMENT:
        raise ScoringTruthIntakeError("source-pack authority-retention requirement drifted")
    source_root = _require_plain_directory(Path(source["path"]), name="bound source pack")
    if not isinstance(source["pack_version"], str) or not source["pack_version"]:
        raise ScoringTruthIntakeError("source_pack.pack_version must be non-empty")
    if source["source_status"] is not None and not isinstance(
        source["source_status"], str
    ):
        raise ScoringTruthIntakeError("source_pack.source_status must be string or null")
    if not isinstance(source["static_directories"], list) or any(
        not isinstance(item, str) for item in source["static_directories"]
    ):
        raise ScoringTruthIntakeError("source_pack.static_directories is invalid")
    static_directories = [
        _canonical_relative_path(item, name="source static directory")
        for item in source["static_directories"]
    ]
    if static_directories != sorted(set(static_directories)):
        raise ScoringTruthIntakeError("source static directories must be unique and sorted")
    if not isinstance(source["static_files"], list) or not source["static_files"]:
        raise ScoringTruthIntakeError("source_pack.static_files is empty")
    static_raw: dict[str, bytes] = {}
    for index, record in enumerate(source["static_files"], start=1):
        relative, raw = _validate_source_file_record(
            record,
            source_root=source_root,
            name=f"source static file {index}",
        )
        if relative in static_raw:
            raise ScoringTruthIntakeError("duplicate source static session_path")
        static_raw[relative] = raw
    if list(static_raw) != sorted(static_raw):
        raise ScoringTruthIntakeError("source static files must be sorted")
    current_source = _snapshot_source_pack(source_root)
    current_paths = [item["relative_path"] for item in current_source["files"]]
    if (
        current_source["directories"] != static_directories
        or current_paths != list(static_raw)
    ):
        raise ScoringTruthIntakeError("bound source-pack topology changed")
    manifest_record = source["manifest"]
    if manifest_record not in source["static_files"]:
        raise ScoringTruthIntakeError("source_pack.manifest is not a static-file binding")
    if manifest_record.get("session_path") != "manifest.json":
        raise ScoringTruthIntakeError("source_pack.manifest must bind manifest.json")
    source_manifest = _strict_json(static_raw["manifest.json"], name="bound source manifest")
    if (
        source_manifest.get("pack_version") != source["pack_version"]
        or source_manifest.get("status") != source["source_status"]
    ):
        raise ScoringTruthIntakeError("source-pack manifest metadata drifted")

    exports = _require_exact_keys(
        manifest["exports"], set(EXPORT_FILENAMES), name="exports"
    )
    export_raw: dict[str, bytes] = {}
    for filename in EXPORT_FILENAMES:
        record = _require_exact_keys(
            exports[filename],
            {
                "source_path",
                "submitted_name",
                "pack_path",
                "raw_path",
                "bytes",
                "sha256",
                "rows",
            },
            name=f"exports.{filename}",
        )
        if record["pack_path"] != filename or record["raw_path"] != f"raw-exports/{filename}":
            raise ScoringTruthIntakeError(f"{filename} session paths drifted")
        if not isinstance(record["submitted_name"], str) or not record["submitted_name"]:
            raise ScoringTruthIntakeError(f"{filename}.submitted_name is missing")
        source_path = Path(str(record["source_path"]))
        if not source_path.is_absolute() or not source_path.is_file() or _is_reparse(source_path):
            raise ScoringTruthIntakeError(f"{filename} bound source export is unavailable")
        if record["submitted_name"] != source_path.name:
            raise ScoringTruthIntakeError(f"{filename}.submitted_name drifted")
        raw = source_path.read_bytes()
        if (
            len(raw) != _require_nonnegative_int(record["bytes"], name=f"{filename}.bytes")
            or _sha256(raw) != _require_sha256(record["sha256"], name=f"{filename}.sha256")
        ):
            raise ScoringTruthIntakeError(f"{filename} bound source export changed")
        parsed = _read_csv_snapshot(
            source_path,
            expected_header=EXPORT_FIELDS[filename],
            name=f"bound {filename}",
        )
        if parsed["rows"] != _require_nonnegative_int(record["rows"], name=f"{filename}.rows"):
            raise ScoringTruthIntakeError(f"{filename} row count drifted")
        export_raw[filename] = raw

    if not isinstance(manifest["artifacts"], list) or not manifest["artifacts"]:
        raise ScoringTruthIntakeError("intake artifacts are empty")
    artifacts: list[dict[str, Any]] = []
    artifact_raw: dict[str, bytes] = {}
    for index, value in enumerate(manifest["artifacts"], start=1):
        artifact = _require_exact_keys(
            value,
            {"path", "bytes", "sha256", "role"},
            name=f"artifact {index}",
        )
        relative = _canonical_relative_path(artifact["path"], name=f"artifact {index}.path")
        if relative == INTAKE_MANIFEST_NAME or relative in artifact_raw:
            raise ScoringTruthIntakeError("artifact paths must be unique and exclude the manifest")
        expected_role = _artifact_role(relative, set(static_raw))
        if artifact["role"] != expected_role:
            raise ScoringTruthIntakeError(f"artifact role mismatch: {relative}")
        path = _session_path(session, relative, name=f"artifact {index}")
        if not path.is_file() or _is_reparse(path):
            raise ScoringTruthIntakeError(f"session artifact is missing or not plain: {relative}")
        raw = path.read_bytes()
        if (
            len(raw) != _require_nonnegative_int(artifact["bytes"], name=f"artifact {index}.bytes")
            or _sha256(raw) != _require_sha256(artifact["sha256"], name=f"artifact {index}.sha256")
        ):
            raise ScoringTruthIntakeError(f"session artifact bytes changed: {relative}")
        artifact_raw[relative] = raw
        artifacts.append(dict(artifact))
    artifact_paths = [item["path"] for item in artifacts]
    if artifact_paths != sorted(artifact_paths):
        raise ScoringTruthIntakeError("session artifacts must be sorted by path")

    if not isinstance(manifest["directories"], list) or any(
        not isinstance(item, str) for item in manifest["directories"]
    ):
        raise ScoringTruthIntakeError("session directories are invalid")
    declared_directories = [
        _canonical_relative_path(item, name="session directory")
        for item in manifest["directories"]
    ]
    if declared_directories != sorted(set(declared_directories)):
        raise ScoringTruthIntakeError("session directories must be unique and sorted")
    actual_directories, actual_files = _walk_tree(session)
    expected_files = sorted([INTAKE_MANIFEST_NAME, *artifact_raw])
    if actual_directories != declared_directories or actual_files != expected_files:
        raise ScoringTruthIntakeError("intake session topology drifted")

    root_sha = _require_sha256(
        manifest["content_root_sha256"], name="content_root_sha256"
    )
    if _content_root(artifacts) != root_sha:
        raise ScoringTruthIntakeError("intake content root does not match artifacts")
    if manifest["intake_id"] != f"scoring-truth-intake-{root_sha[:24]}":
        raise ScoringTruthIntakeError("intake_id does not match content root")

    for relative, raw in static_raw.items():
        if artifact_raw.get(relative) != raw:
            raise ScoringTruthIntakeError(
                f"source static file was not copied byte-exact: {relative}"
            )
    for filename, raw in export_raw.items():
        if (
            artifact_raw.get(filename) != raw
            or artifact_raw.get(f"raw-exports/{filename}") != raw
        ):
            raise ScoringTruthIntakeError(
                f"root/raw export copies are not byte-exact: {filename}"
            )

    compilation = _require_exact_keys(
        manifest["compilation"],
        {
            "report_path",
            "report_sha256",
            "status",
            "readiness_interpretation",
            "errors_count",
            "readiness_sha256",
            "readiness",
        },
        name="compilation",
    )
    if compilation["report_path"] != "compiled/validation-report.json":
        raise ScoringTruthIntakeError("compilation.report_path drifted")
    if (
        compilation["readiness_interpretation"]
        != "diagnostic_only_not_authorization_or_calibration_eligibility"
    ):
        raise ScoringTruthIntakeError("compiler readiness interpretation drifted")
    report_raw = artifact_raw.get(compilation["report_path"])
    if report_raw is None or _sha256(report_raw) != _require_sha256(
        compilation["report_sha256"], name="compilation.report_sha256"
    ):
        raise ScoringTruthIntakeError("compilation report binding mismatch")
    stored_report = _strict_json(report_raw, name="compiled validation report")
    if (
        stored_report.get("status") != compilation["status"]
        or stored_report.get("errors") != []
        or compilation["errors_count"] != 0
        or stored_report.get("readiness") != compilation["readiness"]
        or _sha256(_canonical_json_bytes(compilation["readiness"]))
        != _require_sha256(
            compilation["readiness_sha256"], name="compilation.readiness_sha256"
        )
    ):
        raise ScoringTruthIntakeError("compiler status/readiness binding drifted")
    if compilation["status"] not in {
        "annotation_required",
        "ready_for_evaluation_and_calibration_review",
    }:
        raise ScoringTruthIntakeError("intake compiler status is not admissible")

    replay_report, replay_files, replay_directories = _replay_compilation(
        session,
        static_directories=static_directories,
        static_paths=list(static_raw),
    )
    session_compiled = {
        path.removeprefix("compiled/"): raw
        for path, raw in artifact_raw.items()
        if path.startswith("compiled/")
    }
    session_compiled_directories = sorted(
        path.removeprefix("compiled/")
        for path in declared_directories
        if path.startswith("compiled/")
    )
    if (
        replay_report != stored_report
        or replay_files != session_compiled
        or replay_directories != session_compiled_directories
    ):
        raise ScoringTruthIntakeError(
            "compiled artifacts do not match deterministic truth-pack replay"
        )
    return manifest


__all__ = [
    "EXPORT_FIELDS",
    "EXPORT_FILENAMES",
    "INTAKE_STATUS",
    "INTAKE_VERSION",
    "ScoringTruthIntakeError",
    "ingest_scoring_truth_exports",
    "validate_scoring_truth_intake",
]
