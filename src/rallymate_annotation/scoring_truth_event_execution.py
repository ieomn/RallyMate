from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType, ModuleType
from typing import Any, Mapping

from rallymate_annotation.scoring_truth_authorization import (
    BINDING_STATUS as AUTHORIZATION_STATUS,
    CANONICALIZATION,
    EVENT_CODES,
    PHASE_KEYS_BY_EVENT,
    ScoringTruthAuthorizationError,
    TASKS as AUTHORIZED_TASKS,
    scoring_truth_authorization_binding_sha256,
    validate_scoring_truth_authorization_binding,
    verify_scoring_truth_event_authorization,
)
from rallymate_annotation.scoring_truth_event_handoff import (
    MANIFEST_NAME as M89_MANIFEST_NAME,
    ScoringTruthEventHandoffError,
    validate_scoring_truth_event_handoff,
)


SCHEMA_VERSION = "1.0.0"
EXECUTION_BUNDLE_VERSION = "scoring-truth-event-execution-bundle-v1.0.0"
EXECUTION_BUNDLE_STATUS = "annotation_execution_ready_operator_release_verified"
ANNOTATION_SUBMISSION_VERSION = "scoring-truth-event-annotation-submission-v1.0.0"
ANNOTATION_SUBMISSION_STATUS = "full_video_event_phase_annotation_submitted"
ADJUDICATION_BUNDLE_VERSION = "scoring-truth-event-adjudication-bundle-v1.0.0"
ADJUDICATION_BUNDLE_STATUS = "adjudication_ready_both_submissions_verified"
ADJUDICATION_SUBMISSION_VERSION = (
    "scoring-truth-event-adjudication-submission-v1.0.0"
)
ADJUDICATION_SUBMISSION_STATUS = "event_phase_adjudication_finalized"

EXECUTION_MANIFEST_NAME = "execution-manifest.json"
ADJUDICATION_MANIFEST_NAME = "adjudication-manifest.json"
SERVER_LAUNCHER = "serve_scoring_truth_event_execution.py"
ENTRYPOINT = "review.html"
CORE_JS = "scoring-truth-event-collection-core.js"
WORKBENCH_JS = "scoring-truth-event-collection-workbench.js"
WORKBENCH_CSS = "scoring-truth-event-collection-workbench.css"
WORKBENCH_HTML_SOURCE = "scoring-truth-event-collection-workbench.html"
APPROVED_ASSET_SHA256 = {
    CORE_JS: "B18F34C5BC3481F1D4B8BA16439077369011ACE318757BC240762A8B332B979D",
    WORKBENCH_JS: "3783DE18D7F3CEE4E8A5CE8D6A6AACBF63A33F267893D5EA59A5B71670110AAD",
    WORKBENCH_CSS: "C918BAA178E15DCC2E68E73242E806B8842C99BC27BBD85673922995CF30C7AF",
    WORKBENCH_HTML_SOURCE: "6E005BAD0199FD1D5DDBDD014580131046CA5B81F8E2F55D23D957383D4AC1A0",
}
APPROVED_SERVER_SHA256 = "E41198B6584FEA82D8619409C376EB41DAEACE1B83A8BB189E20C7783FE45378"
BOOTSTRAP_OPEN = (
    '<script id="scoring-truth-event-collection-bootstrap" '
    'type="application/json">'
)
BOOTSTRAP_CLOSE = "</script>"

AUTHORITY_PLAN_PATH = "authority/annotation-plan.json"
AUTHORITY_RELEASE_PATH = "authority/operator-release.json"
AUTHORITY_HANDOFF_ROOT = "authority/m89-handoff"
SOURCE_EXECUTION_A_PATH = "sources/execution-A-manifest.json"
SOURCE_EXECUTION_B_PATH = "sources/execution-B-manifest.json"
SOURCE_ANNOTATION_A_PATH = "submissions/annotator-A.json"
SOURCE_ANNOTATION_B_PATH = "submissions/annotator-B.json"

ROLE_KIND = {
    "A": "independent_event_phase_annotator",
    "B": "independent_event_phase_annotator",
    "C": "independent_event_phase_reviewer",
}
FPS_TOLERANCE = 0.05

REVISION_CONTRACT = {
    "canonicalization": CANONICALIZATION,
    "annotation_revision_excludes": ["annotation_revision_sha256"],
    "submission_revision_excludes": [
        "submission_revision_sha256",
        "exported_at",
    ],
    "adjudication_revision_excludes": ["adjudication_revision_sha256"],
    "adjudication_submission_revision_excludes": [
        "adjudication_submission_revision_sha256",
        "exported_at",
    ],
    "raw_file_sha256_separate": True,
    "hash_is_identity_signature_or_trusted_timestamp": False,
}

EXECUTION_SAFETY = {
    "annotation_execution_authorized": True,
    "operator_release_record_verified": True,
    "role_locked": True,
    "mutation_enabled": True,
    "import_enabled": False,
    "export_enabled": True,
    "local_browser_drafts_only": True,
    "remote_submission_enabled": False,
    "peer_submission_included": False,
    "adjudication_input_included": False,
    "private_source_included": False,
    "model_output_included": False,
    "candidate_boundaries_included": False,
    "machine_event_boundaries_embedded": False,
    "phase_values_embedded": False,
    "machine_keypoints_embedded": False,
    "full_video_only": True,
    "grades_or_thresholds_supported": False,
    "calibration_authorized": False,
    "promotion_authorized": False,
    "production_enabled": False,
    "maturity_promoted": False,
    "directory_listing_enabled": False,
    "exact_server_allowlist": True,
    "hash_is_identity_signature_or_trusted_timestamp": False,
}

ADJUDICATION_SAFETY = {
    **EXECUTION_SAFETY,
    "import_enabled": True,
    "peer_submission_included": True,
    "adjudication_input_included": True,
    "both_source_submissions_verified": True,
}

_EXECUTION_MANIFEST_KEYS = {
    "schema_version",
    "bundle_version",
    "bundle_id",
    "execution_id",
    "generated_at",
    "status",
    "role",
    "event_authorization",
    "source_files",
    "scope",
    "revision_contract",
    "entrypoint",
    "server_launcher",
    "artifacts",
    "content_root_sha256",
    "manifest_binding_sha256",
    "safety",
}
_ADJUDICATION_MANIFEST_KEYS = {
    "schema_version",
    "bundle_version",
    "bundle_id",
    "execution_id",
    "generated_at",
    "status",
    "role",
    "event_authorization",
    "execution_sources",
    "annotation_submissions",
    "scope",
    "revision_contract",
    "entrypoint",
    "server_launcher",
    "artifacts",
    "content_root_sha256",
    "manifest_binding_sha256",
    "safety",
}
_SOURCE_FILE_KEYS = {"path", "raw_sha256"}
_TECHNICAL_SOURCE_KEYS = {
    "path",
    "raw_sha256",
    "bundle_id",
    "content_root_sha256",
}
_ARTIFACT_KEYS = {"path", "bytes", "sha256"}
_TASK_KEYS = {
    "task_id",
    "video_id",
    "video_sha256",
    "media_path",
    "frame_rate",
    "frame_count",
    "duration_ms",
    "full_video_review_required",
}
_EXECUTION_SOURCE_KEYS = {
    "role_slot",
    "path",
    "raw_sha256",
    "bundle_id",
    "execution_id",
    "manifest_binding_sha256",
    "content_root_sha256",
}
_ANNOTATION_SOURCE_KEYS = {
    "role_slot",
    "path",
    "raw_sha256",
    "submission_id",
    "submission_revision_sha256",
    "annotator_id",
    "submitted_at",
    "exported_at",
}

_SHA_RE = re.compile(r"^[0-9A-F]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_UTC_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$"
)
_SUBMISSION_UTC_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z$"
)
JS_SAFE_INTEGER_MAX = 9_007_199_254_740_991


class ScoringTruthEventExecutionError(ValueError):
    """The local M93 execution/adjudication evidence is incomplete or drifted."""


def _validate_unicode(value: Any, *, name: str) -> None:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ScoringTruthEventExecutionError(
                f"{name} contains an isolated surrogate"
            ) from exc
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_unicode(item, name=f"{name}[{index}]")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_unicode(key, name=f"{name} key")
            _validate_unicode(item, name=f"{name}.{key}")
    elif (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > JS_SAFE_INTEGER_MAX
    ):
        raise ScoringTruthEventExecutionError(
            f"{name} exceeds the JavaScript safe integer range"
        )


def _canonical_json_bytes(value: Any) -> bytes:
    _validate_unicode(value, name="value")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
        raise ScoringTruthEventExecutionError("value is not canonical JSON") from exc


def _pretty_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
        raise ScoringTruthEventExecutionError("value is not writable JSON") from exc


def _canonical_file_bytes(value: Any) -> bytes:
    return _canonical_json_bytes(value) + b"\n"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _reject_json_constant(value: str) -> None:
    raise ScoringTruthEventExecutionError(f"invalid JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScoringTruthEventExecutionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json_object(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except ScoringTruthEventExecutionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ScoringTruthEventExecutionError(
            f"{name} must be strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ScoringTruthEventExecutionError(f"{name} must be a JSON object")
    _validate_unicode(value, name=name)
    return value


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    is_junction = getattr(path, "is_junction", None)
    return bool(
        stat.S_ISLNK(metadata.st_mode)
        or attributes & reparse_flag
        or (is_junction is not None and is_junction())
    )


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _snapshot_file(path: Path, *, name: str) -> bytes:
    requested = path.expanduser().absolute()
    descriptor: int | None = None
    try:
        before_path = requested.lstat()
        before_resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise ScoringTruthEventExecutionError(f"missing file: {name}") from exc
    if _is_link_like(requested) or not stat.S_ISREG(before_path.st_mode):
        raise ScoringTruthEventExecutionError(
            f"{name} must be a regular non-link file"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
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
    except OSError as exc:
        raise ScoringTruthEventExecutionError(f"could not snapshot file: {name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    raw = b"".join(chunks)
    if (
        len(
            {
                _path_identity(before_path),
                _path_identity(before_open),
                _path_identity(after_open),
                _path_identity(after_path),
            }
        )
        != 1
        or before_resolved != after_resolved
        or len(raw) != after_open.st_size
    ):
        raise ScoringTruthEventExecutionError(f"file changed while read: {name}")
    return raw


def _snapshot_json(path: str | Path, *, name: str) -> tuple[dict[str, Any], bytes]:
    raw = _snapshot_file(Path(path), name=name)
    return _parse_json_object(raw, name=name), raw


def _require_exact_keys(
    value: Any, expected: set[str], *, name: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise ScoringTruthEventExecutionError(
            f"{name} keys are invalid "
            f"(missing={sorted(expected - actual)}, extra={sorted(actual - expected)})"
        )
    return value


def _require_exact_json(value: Any, expected: Any, *, name: str) -> None:
    if _canonical_json_bytes(value) != _canonical_json_bytes(expected):
        raise ScoringTruthEventExecutionError(f"{name} is invalid")


def _require_sha(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ScoringTruthEventExecutionError(f"{name} must be uppercase SHA-256")
    return value


def _require_identifier(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ScoringTruthEventExecutionError(f"{name} must be a safe identifier")
    return value


def _require_int(
    value: Any, *, name: str, minimum: int | None = None, maximum: int | None = None
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ScoringTruthEventExecutionError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ScoringTruthEventExecutionError(f"{name} is below its minimum")
    effective_maximum = (
        JS_SAFE_INTEGER_MAX
        if maximum is None
        else min(maximum, JS_SAFE_INTEGER_MAX)
    )
    if value > effective_maximum:
        raise ScoringTruthEventExecutionError(f"{name} exceeds its maximum")
    return value


def _parse_utc(value: Any, *, name: str, submission: bool = False) -> datetime:
    pattern = _SUBMISSION_UTC_RE if submission else _UTC_RE
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        qualifier = "canonical millisecond UTC" if submission else "canonical UTC"
        raise ScoringTruthEventExecutionError(f"{name} must be {qualifier} ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ScoringTruthEventExecutionError(f"{name} is not a real timestamp") from exc
    timespec = "milliseconds" if submission else "auto"
    if (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec=timespec)
        .replace("+00:00", "Z")
        != value
    ):
        raise ScoringTruthEventExecutionError(f"{name} is not canonical UTC")
    return parsed


def _safe_relative_path(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "%" in value:
        raise ScoringTruthEventExecutionError(
            f"{name} must be a portable relative path"
        )
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or posix.as_posix() != value
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ScoringTruthEventExecutionError(
            f"{name} must be a portable relative path"
        )
    return value


def _identity_key(value: Any, *, name: str) -> str:
    identity = _require_identifier(value.strip() if isinstance(value, str) else value, name=name)
    if identity != value:
        raise ScoringTruthEventExecutionError(f"{name} must not contain outer whitespace")
    return unicodedata.normalize("NFKC", identity).casefold()


def _now_utc() -> str:
    return _format_generated_utc(datetime.now(timezone.utc))


def _format_generated_utc(value: datetime) -> str:
    utc = value.astimezone(timezone.utc)
    timespec = "seconds" if utc.microsecond == 0 else "microseconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _asset_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name


def _approved_asset_snapshot(name: str) -> bytes:
    raw = _snapshot_file(_asset_path(name), name=name)
    if _sha256(raw) != APPROVED_ASSET_SHA256[name]:
        raise ScoringTruthEventExecutionError(f"approved workbench asset drifted: {name}")
    return raw


def _server_source_path() -> Path:
    return _repo_root() / "scripts" / SERVER_LAUNCHER


def _load_server_module(raw: bytes, *, source_path: Path) -> ModuleType:
    module_name = f"_rallymate_m93_event_execution_server_{uuid.uuid4().hex}"
    module = ModuleType(module_name)
    module.__file__ = str(source_path)
    sys.modules[module_name] = module
    try:
        exec(compile(raw, str(source_path), "exec"), module.__dict__)
    except Exception as exc:
        raise ScoringTruthEventExecutionError(
            "standalone bundle validator could not load"
        ) from exc
    finally:
        sys.modules.pop(module_name, None)
    return module


def _manifest_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key
        not in {"artifacts", "content_root_sha256", "manifest_binding_sha256"}
    }


def scoring_truth_event_manifest_binding_sha256(
    manifest: Mapping[str, Any],
) -> str:
    """Return the non-circular manifest projection checksum used by UI exports."""

    if not isinstance(manifest, Mapping):
        raise ScoringTruthEventExecutionError("manifest must be an object")
    return _sha256(_canonical_json_bytes(_manifest_projection(manifest)))


def _content_root(artifacts: list[dict[str, Any]]) -> str:
    return _sha256(_canonical_json_bytes({"artifacts": artifacts}))


def _artifact_records(raw_by_path: Mapping[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": path, "bytes": len(raw_by_path[path]), "sha256": _sha256(raw_by_path[path])}
        for path in sorted(raw_by_path)
    ]


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        a = left.resolve(strict=False)
        b = right.resolve(strict=False)
        common = Path(os.path.commonpath((str(a), str(b))))
    except (OSError, ValueError):
        return False
    return common == a or common == b


def _write_new_file(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ScoringTruthEventExecutionError(
            f"could not write staging file: {path.name}"
        ) from exc


def _write_tree(staging: Path, raw_by_path: Mapping[str, bytes]) -> None:
    for relative in sorted(raw_by_path):
        _write_new_file(staging / Path(*relative.split("/")), raw_by_path[relative])


def _publish_staging(staging: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise ScoringTruthEventExecutionError("output appeared during build")
    try:
        staging.rename(output)
    except OSError as exc:
        raise ScoringTruthEventExecutionError("atomic publish rename failed") from exc


def _probe_video(path: Path, *, expected_raw: bytes, name: str) -> dict[str, int]:
    before = _snapshot_file(path, name=name)
    if before != expected_raw:
        raise ScoringTruthEventExecutionError(f"media snapshot mismatch before probe: {name}")
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise ScoringTruthEventExecutionError(
            "OpenCV is required for the main M93 media probe"
        ) from exc
    capture = cv2.VideoCapture(str(path.resolve()))
    if not capture.isOpened():
        raise ScoringTruthEventExecutionError(f"cannot open verified media: {name}")
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    capture.release()
    after = _snapshot_file(path, name=name)
    if before != after:
        raise ScoringTruthEventExecutionError(f"media changed while probed: {name}")
    if frame_count <= 0 or not (fps > 0.0):
        raise ScoringTruthEventExecutionError(f"media probe is invalid: {name}")
    duration_ms = round(frame_count / fps * 1000)
    if duration_ms <= 0:
        raise ScoringTruthEventExecutionError(f"media duration is invalid: {name}")
    return {"frame_count": frame_count, "duration_ms": duration_ms, "fps_milli": round(fps * 1000)}


def _authorization_snapshot(
    *, plan_path: str | Path, release_record_path: str | Path, handoff_dir: str | Path
) -> dict[str, Any]:
    plan, plan_raw = _snapshot_json(plan_path, name="annotation plan")
    release, release_raw = _snapshot_json(
        release_record_path, name="operator release record"
    )
    try:
        binding = verify_scoring_truth_event_authorization(
            plan_path=plan_path,
            release_record_path=release_record_path,
            handoff_dir=handoff_dir,
        )
        handoff = validate_scoring_truth_event_handoff(handoff_dir)
    except (ScoringTruthAuthorizationError, ScoringTruthEventHandoffError) as exc:
        raise ScoringTruthEventExecutionError(
            f"event annotation authorization is invalid: {exc}"
        ) from exc
    if binding["plan"]["raw_sha256"] != _sha256(plan_raw):
        raise ScoringTruthEventExecutionError("annotation plan changed around verification")
    if binding["release_record"]["raw_sha256"] != _sha256(release_raw):
        raise ScoringTruthEventExecutionError(
            "operator release record changed around verification"
        )
    if binding["technical_handoff"]["manifest_raw_sha256"] != handoff["manifest_sha256"]:
        raise ScoringTruthEventExecutionError("M89 handoff changed around verification")
    m89_raw: dict[str, bytes] = {M89_MANIFEST_NAME: handoff["manifest_raw"]}
    m89_raw.update(
        {path: record["raw"] for path, record in handoff["artifacts"].items()}
    )
    return {
        "binding": binding,
        "plan": plan,
        "plan_raw": plan_raw,
        "release": release,
        "release_raw": release_raw,
        "handoff": handoff,
        "m89_raw": MappingProxyType(m89_raw),
    }


def _authority_raw(snapshot: Mapping[str, Any]) -> dict[str, bytes]:
    result = {
        AUTHORITY_PLAN_PATH: snapshot["plan_raw"],
        AUTHORITY_RELEASE_PATH: snapshot["release_raw"],
    }
    result.update(
        {
            f"{AUTHORITY_HANDOFF_ROOT}/{path}": raw
            for path, raw in snapshot["m89_raw"].items()
        }
    )
    return result


def _source_files(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    binding = snapshot["binding"]
    return {
        "plan": {
            "path": AUTHORITY_PLAN_PATH,
            "raw_sha256": _sha256(snapshot["plan_raw"]),
        },
        "operator_release": {
            "path": AUTHORITY_RELEASE_PATH,
            "raw_sha256": _sha256(snapshot["release_raw"]),
        },
        "technical_handoff": {
            "path": f"{AUTHORITY_HANDOFF_ROOT}/{M89_MANIFEST_NAME}",
            "raw_sha256": binding["technical_handoff"]["manifest_raw_sha256"],
            "bundle_id": binding["technical_handoff"]["bundle_id"],
            "content_root_sha256": binding["technical_handoff"]["content_root_sha256"],
        },
    }


def _execution_id(binding: Mapping[str, Any], plan_scope: Any) -> str:
    digest = _sha256(
        _canonical_json_bytes(
            {
                "bundle_version": EXECUTION_BUNDLE_VERSION,
                "authorization_binding_sha256": binding["binding_sha256"],
                "plan_scope": plan_scope,
            }
        )
    )
    return f"m93-event-execution-{digest}"


def _bundle_id(
    *, version: str, execution_id: str, generated_at: str, role_slot: str
) -> str:
    digest = _sha256(
        _canonical_json_bytes(
            {
                "bundle_version": version,
                "execution_id": execution_id,
                "generated_at": generated_at,
                "role_slot": role_slot,
            }
        )
    )
    label = "execution" if version == EXECUTION_BUNDLE_VERSION else "adjudication"
    return f"m93-event-{label}-{role_slot}-{digest}"


def _scope_from_staging(
    *, staging: Path, snapshot: Mapping[str, Any], authority_raw: Mapping[str, bytes]
) -> dict[str, Any]:
    m89_manifest = snapshot["handoff"]["manifest"]
    tasks: list[dict[str, Any]] = []
    for authorized, technical in zip(AUTHORIZED_TASKS, m89_manifest["scope"]["tasks"]):
        media_path = f"{AUTHORITY_HANDOFF_ROOT}/{technical['media_path']}"
        probe = _probe_video(
            staging / Path(*media_path.split("/")),
            expected_raw=authority_raw[media_path],
            name=media_path,
        )
        expected_fps = technical["frame_rate"]["numerator"] / technical["frame_rate"]["denominator"]
        if abs(probe["fps_milli"] / 1000 - expected_fps) > FPS_TOLERANCE:
            raise ScoringTruthEventExecutionError(
                f"media FPS does not match M89 frame rate: {technical['video_id']}"
            )
        tasks.append(
            {
                "task_id": authorized["task_id"],
                "video_id": authorized["video_id"],
                "video_sha256": authorized["video_sha256"],
                "media_path": media_path,
                "frame_rate": dict(technical["frame_rate"]),
                "frame_count": probe["frame_count"],
                "duration_ms": probe["duration_ms"],
                "full_video_review_required": True,
            }
        )
    return {
        "event_codes": list(EVENT_CODES),
        "phase_keys_by_event": {
            key: list(value) for key, value in PHASE_KEYS_BY_EVENT.items()
        },
        "tasks": tasks,
    }


def _inject_bootstrap(template_raw: bytes, bootstrap: Mapping[str, Any]) -> bytes:
    try:
        template = template_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScoringTruthEventExecutionError(
            f"{WORKBENCH_HTML_SOURCE} must be UTF-8"
        ) from exc
    marker = BOOTSTRAP_OPEN + "{}" + BOOTSTRAP_CLOSE
    if template.count(marker) != 1:
        raise ScoringTruthEventExecutionError(
            "workbench HTML must contain exactly one empty bootstrap element"
        )
    encoded = _canonical_json_bytes(dict(bootstrap)).decode("utf-8").replace("</", "<\\/")
    encoded = encoded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return template.replace(marker, BOOTSTRAP_OPEN + encoded + BOOTSTRAP_CLOSE).encode(
        "utf-8"
    )


def _extract_bootstrap(raw: bytes) -> dict[str, Any]:
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScoringTruthEventExecutionError("review.html must be UTF-8") from exc
    if html.count(BOOTSTRAP_OPEN) != 1:
        raise ScoringTruthEventExecutionError("review.html bootstrap element is invalid")
    start = html.index(BOOTSTRAP_OPEN) + len(BOOTSTRAP_OPEN)
    end = html.find(BOOTSTRAP_CLOSE, start)
    if end < 0:
        raise ScoringTruthEventExecutionError("review.html bootstrap is not closed")
    encoded = html[start:end]
    bootstrap = _parse_json_object(
        encoded.replace("<\\/", "</").encode("utf-8"),
        name="review.html bootstrap",
    )
    expected_encoded = _canonical_json_bytes(bootstrap).decode("utf-8").replace(
        "</", "<\\/"
    )
    expected_encoded = expected_encoded.replace("\u2028", "\\u2028").replace(
        "\u2029", "\\u2029"
    )
    if encoded != expected_encoded:
        raise ScoringTruthEventExecutionError(
            "review.html bootstrap JSON is not canonical"
        )
    restored_template = (html[:start] + "{}" + html[end:]).encode("utf-8")
    if (
        _sha256(restored_template)
        != APPROVED_ASSET_SHA256[WORKBENCH_HTML_SOURCE]
    ):
        raise ScoringTruthEventExecutionError(
            "approved workbench HTML template drifted"
        )
    return bootstrap


def _bundle_ref(manifest: Mapping[str, Any]) -> dict[str, str]:
    # content_root_sha256 is intentionally excluded.  review.html contains this
    # reference and is itself an artifact in that content root.
    return {
        "bundle_id": manifest["bundle_id"],
        "manifest_binding_sha256": manifest["manifest_binding_sha256"],
    }


def _bootstrap_tasks(scope: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": item["task_id"],
            "video_id": item["video_id"],
            "media_path": item["media_path"],
            "media_sha256": item["video_sha256"],
            "duration_ms": item["duration_ms"],
            "frame_rate": dict(item["frame_rate"]),
            "full_video_review_required": item["full_video_review_required"],
        }
        for item in scope["tasks"]
    ]


def _bootstrap_common(manifest: Mapping[str, Any]) -> dict[str, Any]:
    safety = manifest["safety"]
    return {
        "schema_version": SCHEMA_VERSION,
        "workbench_version": "scoring-truth-event-collection-workbench-v1.0.0",
        "bundle_version": manifest["bundle_version"],
        "execution_id": manifest["execution_id"],
        "bundle_status": manifest["status"],
        "authorization_status": manifest["event_authorization"]["status"],
        "authorization_binding_sha256": manifest["event_authorization"][
            "binding_sha256"
        ],
        "role_slot": manifest["role"]["slot"],
        "annotation_execution_authorized": safety[
            "annotation_execution_authorized"
        ],
        "operator_release_record_verified": safety[
            "operator_release_record_verified"
        ],
        "mutation_enabled": safety["mutation_enabled"],
        "import_enabled": safety["import_enabled"],
        "export_enabled": safety["export_enabled"],
        "full_video_only": safety["full_video_only"],
        "machine_event_boundaries_embedded": safety[
            "machine_event_boundaries_embedded"
        ],
        "phase_values_embedded": safety["phase_values_embedded"],
        "machine_keypoints_embedded": safety["machine_keypoints_embedded"],
        "grades_or_thresholds_supported": safety[
            "grades_or_thresholds_supported"
        ],
        "calibration_authorized": safety["calibration_authorized"],
        "promotion_authorized": safety["promotion_authorized"],
        "production_enabled": safety["production_enabled"],
        "maturity_promoted": safety["maturity_promoted"],
        "event_codes": list(manifest["scope"]["event_codes"]),
        "phase_keys_by_event": {
            key: list(value)
            for key, value in manifest["scope"]["phase_keys_by_event"].items()
        },
        "required_annotator_slots": ["A", "B"],
        "reviewer_slot": "C",
        "tasks": _bootstrap_tasks(manifest["scope"]),
    }


def _execution_bootstrap(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {**_bootstrap_common(manifest), "execution_bundle": _bundle_ref(manifest)}


def _source_execution_ref(record: Mapping[str, Any]) -> dict[str, str]:
    return {
        "bundle_id": record["bundle_id"],
        "manifest_binding_sha256": record["manifest_binding_sha256"],
    }


def _adjudication_bootstrap(manifest: Mapping[str, Any]) -> dict[str, Any]:
    execution_sources = {
        item["role_slot"]: _source_execution_ref(item)
        for item in manifest["execution_sources"]
    }
    expected_submissions = {
        item["role_slot"]: {
            "submission_id": item["submission_id"],
            "raw_sha256": item["raw_sha256"],
            "submission_revision_sha256": item["submission_revision_sha256"],
            "annotator_id": item["annotator_id"],
        }
        for item in manifest["annotation_submissions"]
    }
    return {
        **_bootstrap_common(manifest),
        "adjudication_bundle": _bundle_ref(manifest),
        "source_execution_bundles": execution_sources,
        "expected_source_submissions": expected_submissions,
    }


def _validate_role(value: Any, *, expected_slot: str, name: str = "role") -> None:
    role = _require_exact_keys(value, {"slot", "kind"}, name=name)
    if role["slot"] != expected_slot or role["kind"] != ROLE_KIND[expected_slot]:
        raise ScoringTruthEventExecutionError(f"{name} is invalid")


def _validate_source_files(
    source_files: Any,
    *,
    binding: Mapping[str, Any],
    snapshots: Mapping[str, bytes] | None,
) -> None:
    source = _require_exact_keys(
        source_files,
        {"plan", "operator_release", "technical_handoff"},
        name="source_files",
    )
    plan = _require_exact_keys(source["plan"], _SOURCE_FILE_KEYS, name="source plan")
    release = _require_exact_keys(
        source["operator_release"], _SOURCE_FILE_KEYS, name="source operator release"
    )
    technical = _require_exact_keys(
        source["technical_handoff"],
        _TECHNICAL_SOURCE_KEYS,
        name="source technical handoff",
    )
    expected = {
        "plan": {
            "path": AUTHORITY_PLAN_PATH,
            "raw_sha256": binding["plan"]["raw_sha256"],
        },
        "operator_release": {
            "path": AUTHORITY_RELEASE_PATH,
            "raw_sha256": binding["release_record"]["raw_sha256"],
        },
        "technical_handoff": {
            "path": f"{AUTHORITY_HANDOFF_ROOT}/{M89_MANIFEST_NAME}",
            "raw_sha256": binding["technical_handoff"]["manifest_raw_sha256"],
            "bundle_id": binding["technical_handoff"]["bundle_id"],
            "content_root_sha256": binding["technical_handoff"][
                "content_root_sha256"
            ],
        },
    }
    _require_exact_json(source, expected, name="source_files")
    if snapshots is not None:
        for record in (plan, release, technical):
            if _sha256(snapshots[record["path"]]) != record["raw_sha256"]:
                raise ScoringTruthEventExecutionError(
                    f"source file bytes drifted: {record['path']}"
                )


def _validate_scope_shape(scope: Any, *, name: str = "scope") -> list[Mapping[str, Any]]:
    value = _require_exact_keys(
        scope, {"event_codes", "phase_keys_by_event", "tasks"}, name=name
    )
    _require_exact_json(value["event_codes"], EVENT_CODES, name=f"{name}.event_codes")
    _require_exact_json(
        value["phase_keys_by_event"],
        PHASE_KEYS_BY_EVENT,
        name=f"{name}.phase_keys_by_event",
    )
    tasks = value["tasks"]
    if not isinstance(tasks, list) or len(tasks) != len(AUTHORIZED_TASKS):
        raise ScoringTruthEventExecutionError(f"{name}.tasks must contain three tasks")
    for index, (task, authorized) in enumerate(zip(tasks, AUTHORIZED_TASKS)):
        item = _require_exact_keys(task, _TASK_KEYS, name=f"{name}.tasks[{index}]")
        expected_base = {
            "task_id": authorized["task_id"],
            "video_id": authorized["video_id"],
            "video_sha256": authorized["video_sha256"],
            "full_video_review_required": True,
        }
        for key, expected in expected_base.items():
            if item[key] != expected:
                raise ScoringTruthEventExecutionError(
                    f"{name}.tasks[{index}].{key} is invalid"
                )
        expected_media = f"{AUTHORITY_HANDOFF_ROOT}/media/{authorized['video_id']}.mp4"
        if item["media_path"] != expected_media:
            raise ScoringTruthEventExecutionError(
                f"{name}.tasks[{index}].media_path is invalid"
            )
        frame_rate = _require_exact_keys(
            item["frame_rate"], {"numerator", "denominator"}, name="frame_rate"
        )
        _require_int(frame_rate["numerator"], name="frame_rate.numerator", minimum=1)
        _require_int(frame_rate["denominator"], name="frame_rate.denominator", minimum=1)
        _require_int(item["frame_count"], name="frame_count", minimum=1)
        _require_int(item["duration_ms"], name="duration_ms", minimum=1)
    return tasks


def _verify_scope_media(
    *,
    bundle: Path,
    scope: Mapping[str, Any],
    snapshots: Mapping[str, bytes],
) -> None:
    for task in scope["tasks"]:
        raw = snapshots[task["media_path"]]
        if _sha256(raw) != task["video_sha256"]:
            raise ScoringTruthEventExecutionError(
                f"media SHA drifted: {task['video_id']}"
            )
        probe = _probe_video(
            bundle / Path(*task["media_path"].split("/")),
            expected_raw=raw,
            name=task["media_path"],
        )
        if probe["frame_count"] != task["frame_count"]:
            raise ScoringTruthEventExecutionError(
                f"media frame count drifted: {task['video_id']}"
            )
        if probe["duration_ms"] != task["duration_ms"]:
            raise ScoringTruthEventExecutionError(
                f"media duration drifted: {task['video_id']}"
            )
        expected_fps = task["frame_rate"]["numerator"] / task["frame_rate"]["denominator"]
        if abs(probe["fps_milli"] / 1000 - expected_fps) > FPS_TOLERANCE:
            raise ScoringTruthEventExecutionError(
                f"media FPS drifted: {task['video_id']}"
            )


def _validate_scope_frame_rates_against_handoff(
    scope: Mapping[str, Any], authority: Mapping[str, Any]
) -> None:
    technical_tasks = authority["handoff"]["manifest"]["scope"]["tasks"]
    for index, (task, technical) in enumerate(zip(scope["tasks"], technical_tasks)):
        _require_exact_json(
            task["frame_rate"],
            technical["frame_rate"],
            name=f"scope.tasks[{index}].frame_rate",
        )


def _validate_artifacts(manifest: Mapping[str, Any], snapshots: Mapping[str, bytes]) -> None:
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ScoringTruthEventExecutionError("artifacts must be a non-empty array")
    paths: list[str] = []
    for index, record in enumerate(artifacts):
        item = _require_exact_keys(record, _ARTIFACT_KEYS, name=f"artifacts[{index}]")
        path = _safe_relative_path(item["path"], name=f"artifacts[{index}].path")
        _require_int(item["bytes"], name=f"artifacts[{index}].bytes", minimum=1)
        _require_sha(item["sha256"], name=f"artifacts[{index}].sha256")
        if path not in snapshots:
            raise ScoringTruthEventExecutionError(f"missing artifact snapshot: {path}")
        if len(snapshots[path]) != item["bytes"] or _sha256(snapshots[path]) != item["sha256"]:
            raise ScoringTruthEventExecutionError(f"artifact bytes drifted: {path}")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ScoringTruthEventExecutionError(
            "artifact paths must be unique and lexically sorted"
        )
    _require_sha(manifest["content_root_sha256"], name="content_root_sha256")
    if _content_root(artifacts) != manifest["content_root_sha256"]:
        raise ScoringTruthEventExecutionError("content root checksum mismatch")
    records = {item["path"]: item for item in artifacts}
    for path in (CORE_JS, WORKBENCH_JS, WORKBENCH_CSS):
        if (
            path not in records
            or records[path]["sha256"] != APPROVED_ASSET_SHA256[path]
        ):
            raise ScoringTruthEventExecutionError(
                f"approved workbench asset drifted: {path}"
            )


def _validate_manifest_binding(manifest: Mapping[str, Any]) -> None:
    binding = _require_sha(
        manifest["manifest_binding_sha256"], name="manifest_binding_sha256"
    )
    if scoring_truth_event_manifest_binding_sha256(manifest) != binding:
        raise ScoringTruthEventExecutionError("manifest binding checksum mismatch")


def _server_snapshot(bundle: Path) -> tuple[dict[str, Any], Mapping[str, bytes]]:
    bundled_raw = _snapshot_file(bundle / SERVER_LAUNCHER, name=SERVER_LAUNCHER)
    source_path = _server_source_path()
    source_raw = _snapshot_file(source_path, name=f"source {SERVER_LAUNCHER}")
    if _sha256(source_raw) != APPROVED_SERVER_SHA256 or bundled_raw != source_raw:
        raise ScoringTruthEventExecutionError("standalone server authority drifted")
    server = _load_server_module(source_raw, source_path=source_path)
    try:
        manifest, snapshots = server.validate_and_snapshot(bundle)
    except Exception as exc:
        if exc.__class__.__name__ == "BundleValidationError":
            raise ScoringTruthEventExecutionError(str(exc)) from exc
        raise
    return manifest, snapshots


def _validate_execution_manifest_shape(
    manifest: Mapping[str, Any], *, expected_role_slot: str | None = None
) -> None:
    _require_exact_keys(manifest, _EXECUTION_MANIFEST_KEYS, name="execution manifest")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ScoringTruthEventExecutionError("execution schema_version is invalid")
    if manifest["bundle_version"] != EXECUTION_BUNDLE_VERSION:
        raise ScoringTruthEventExecutionError("execution bundle_version is invalid")
    if manifest["status"] != EXECUTION_BUNDLE_STATUS:
        raise ScoringTruthEventExecutionError("execution status is invalid")
    slot = manifest["role"].get("slot") if isinstance(manifest["role"], Mapping) else None
    if slot not in {"A", "B"}:
        raise ScoringTruthEventExecutionError("execution role_slot must be A or B")
    if expected_role_slot is not None and slot != expected_role_slot:
        raise ScoringTruthEventExecutionError(
            f"execution role_slot is {slot}, expected {expected_role_slot}"
        )
    _validate_role(manifest["role"], expected_slot=slot)
    _require_identifier(manifest["bundle_id"], name="bundle_id")
    _require_identifier(manifest["execution_id"], name="execution_id")
    generated_at = _parse_utc(manifest["generated_at"], name="generated_at")
    if manifest["entrypoint"] != ENTRYPOINT or manifest["server_launcher"] != SERVER_LAUNCHER:
        raise ScoringTruthEventExecutionError("execution entrypoint/launcher drifted")
    _require_exact_json(
        manifest["revision_contract"], REVISION_CONTRACT, name="revision_contract"
    )
    _require_exact_json(manifest["safety"], EXECUTION_SAFETY, name="execution safety")
    _validate_scope_shape(manifest["scope"])
    try:
        validate_scoring_truth_authorization_binding(manifest["event_authorization"])
    except ScoringTruthAuthorizationError as exc:
        raise ScoringTruthEventExecutionError(
            f"execution authorization binding is invalid: {exc}"
        ) from exc
    released_at = _parse_utc(
        manifest["event_authorization"]["release_record"]["released_at"],
        name="operator release released_at",
    )
    if generated_at < released_at:
        raise ScoringTruthEventExecutionError(
            "execution bundle generated_at predates the operator release"
        )
    expected_execution_id = _execution_id(
        manifest["event_authorization"],
        {
            "event_codes": list(EVENT_CODES),
            "phase_keys_by_event": {
                key: list(value) for key, value in PHASE_KEYS_BY_EVENT.items()
            },
            "tasks": [dict(item) for item in AUTHORIZED_TASKS],
        },
    )
    if manifest["execution_id"] != expected_execution_id:
        raise ScoringTruthEventExecutionError("execution_id checksum mismatch")
    expected_bundle_id = _bundle_id(
        version=EXECUTION_BUNDLE_VERSION,
        execution_id=manifest["execution_id"],
        generated_at=manifest["generated_at"],
        role_slot=slot,
    )
    if manifest["bundle_id"] != expected_bundle_id:
        raise ScoringTruthEventExecutionError("execution bundle_id checksum mismatch")
    _validate_manifest_binding(manifest)


def validate_scoring_truth_event_execution_bundle(
    bundle_dir: str | Path, *, expected_role_slot: str | None = None
) -> dict[str, Any]:
    """Re-verify one portable A/B execution bundle and its copied M90 authority."""

    bundle = Path(bundle_dir).expanduser().absolute()
    manifest, snapshots = _server_snapshot(bundle)
    _validate_execution_manifest_shape(
        manifest, expected_role_slot=expected_role_slot
    )
    _validate_artifacts(manifest, snapshots)
    try:
        authority = _authorization_snapshot(
            plan_path=bundle / AUTHORITY_PLAN_PATH,
            release_record_path=bundle / AUTHORITY_RELEASE_PATH,
            handoff_dir=bundle / AUTHORITY_HANDOFF_ROOT,
        )
    except ScoringTruthEventExecutionError as exc:
        raise ScoringTruthEventExecutionError(
            f"internal event authorization replay failed: {exc}"
        ) from exc
    _require_exact_json(
        manifest["event_authorization"],
        authority["binding"],
        name="execution event_authorization",
    )
    _validate_scope_frame_rates_against_handoff(manifest["scope"], authority)
    _validate_source_files(
        manifest["source_files"], binding=authority["binding"], snapshots=snapshots
    )
    expected_authority_paths = set(_authority_raw(authority))
    actual_authority_paths = {
        item["path"]
        for item in manifest["artifacts"]
        if item["path"].startswith("authority/")
    }
    if actual_authority_paths != expected_authority_paths:
        raise ScoringTruthEventExecutionError("authority artifact topology drifted")
    expected_artifact_paths = expected_authority_paths | {
        ENTRYPOINT,
        CORE_JS,
        WORKBENCH_JS,
        WORKBENCH_CSS,
        SERVER_LAUNCHER,
    }
    if {item["path"] for item in manifest["artifacts"]} != expected_artifact_paths:
        raise ScoringTruthEventExecutionError(
            "execution artifact topology is not exact"
        )
    _verify_scope_media(bundle=bundle, scope=manifest["scope"], snapshots=snapshots)
    expected_bootstrap = _execution_bootstrap(manifest)
    _require_exact_json(
        _extract_bootstrap(snapshots[ENTRYPOINT]),
        expected_bootstrap,
        name="execution workbench bootstrap",
    )
    manifest_raw = snapshots[EXECUTION_MANIFEST_NAME]
    artifacts = {
        record["path"]: {**record, "raw": snapshots[record["path"]]}
        for record in manifest["artifacts"]
    }
    return {
        "bundle_dir": bundle.resolve(strict=True),
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "manifest_sha256": _sha256(manifest_raw),
        "manifest_binding_sha256": manifest["manifest_binding_sha256"],
        "bundle_id": manifest["bundle_id"],
        "execution_id": manifest["execution_id"],
        "role_slot": manifest["role"]["slot"],
        "content_root_sha256": manifest["content_root_sha256"],
        "artifacts": artifacts,
        "authorization": authority["binding"],
        "bootstrap": expected_bootstrap,
    }


def build_scoring_truth_event_execution_bundle(
    *,
    plan_path: str | Path,
    release_record_path: str | Path,
    handoff_dir: str | Path,
    role_slot: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Build one role-locked A or B bundle after a verified local M90 release."""

    if role_slot not in {"A", "B"}:
        raise ScoringTruthEventExecutionError("role_slot must be A or B")
    output = Path(output_dir).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ScoringTruthEventExecutionError("output directory must not already exist")
    for source_path in (Path(plan_path), Path(release_record_path), Path(handoff_dir)):
        if _paths_overlap(output, source_path):
            raise ScoringTruthEventExecutionError(
                "output must not overlap authority sources"
            )
    authority = _authorization_snapshot(
        plan_path=plan_path,
        release_record_path=release_record_path,
        handoff_dir=handoff_dir,
    )
    authority_raw = _authority_raw(authority)
    source_server_raw = _snapshot_file(_server_source_path(), name=SERVER_LAUNCHER)
    if _sha256(source_server_raw) != APPROVED_SERVER_SHA256:
        raise ScoringTruthEventExecutionError("standalone server authority drifted")
    asset_sources = {
        name: _approved_asset_snapshot(name) for name in APPROVED_ASSET_SHA256
    }
    parent = output.parent.resolve(strict=False)
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    if staging.exists() or staging.is_symlink():
        raise ScoringTruthEventExecutionError("unique staging path unexpectedly exists")
    try:
        staging.mkdir()
        _write_tree(staging, authority_raw)
        scope = _scope_from_staging(
            staging=staging, snapshot=authority, authority_raw=authority_raw
        )
        generated_at = _format_generated_utc(
            max(
                _parse_utc(_now_utc(), name="local execution build time"),
                _parse_utc(
                    authority["binding"]["release_record"]["released_at"],
                    name="operator release released_at",
                ),
            )
        )
        execution_id = _execution_id(authority["binding"], authority["plan"]["scope"])
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "bundle_version": EXECUTION_BUNDLE_VERSION,
            "bundle_id": _bundle_id(
                version=EXECUTION_BUNDLE_VERSION,
                execution_id=execution_id,
                generated_at=generated_at,
                role_slot=role_slot,
            ),
            "execution_id": execution_id,
            "generated_at": generated_at,
            "status": EXECUTION_BUNDLE_STATUS,
            "role": {"slot": role_slot, "kind": ROLE_KIND[role_slot]},
            "event_authorization": authority["binding"],
            "source_files": _source_files(authority),
            "scope": scope,
            "revision_contract": REVISION_CONTRACT,
            "entrypoint": ENTRYPOINT,
            "server_launcher": SERVER_LAUNCHER,
            "artifacts": [],
            "content_root_sha256": "0" * 64,
            "manifest_binding_sha256": "0" * 64,
            "safety": EXECUTION_SAFETY,
        }
        manifest["manifest_binding_sha256"] = (
            scoring_truth_event_manifest_binding_sha256(manifest)
        )
        review_raw = _inject_bootstrap(
            asset_sources[WORKBENCH_HTML_SOURCE], _execution_bootstrap(manifest)
        )
        raw_by_path = {
            **authority_raw,
            ENTRYPOINT: review_raw,
            CORE_JS: asset_sources[CORE_JS],
            WORKBENCH_JS: asset_sources[WORKBENCH_JS],
            WORKBENCH_CSS: asset_sources[WORKBENCH_CSS],
            SERVER_LAUNCHER: source_server_raw,
        }
        manifest["artifacts"] = _artifact_records(raw_by_path)
        manifest["content_root_sha256"] = _content_root(manifest["artifacts"])
        _write_tree(
            staging,
            {
                key: value
                for key, value in raw_by_path.items()
                if key not in authority_raw
            },
        )
        _write_new_file(
            staging / EXECUTION_MANIFEST_NAME, _pretty_json_bytes(manifest)
        )
        validate_scoring_truth_event_execution_bundle(
            staging, expected_role_slot=role_slot
        )
        replay = _authorization_snapshot(
            plan_path=plan_path,
            release_record_path=release_record_path,
            handoff_dir=handoff_dir,
        )
        _require_exact_json(
            replay["binding"], authority["binding"], name="source authorization recheck"
        )
        if dict(replay["m89_raw"]) != dict(authority["m89_raw"]):
            raise ScoringTruthEventExecutionError("M89 source changed before publish")
        if _snapshot_file(Path(plan_path), name="annotation plan recheck") != authority["plan_raw"]:
            raise ScoringTruthEventExecutionError("annotation plan changed before publish")
        if (
            _snapshot_file(Path(release_record_path), name="operator release recheck")
            != authority["release_raw"]
        ):
            raise ScoringTruthEventExecutionError(
                "operator release changed before publish"
            )
        for name, raw in asset_sources.items():
            if _snapshot_file(_asset_path(name), name=f"{name} recheck") != raw:
                raise ScoringTruthEventExecutionError(
                    f"workbench source changed before publish: {name}"
                )
        if _snapshot_file(_server_source_path(), name="server recheck") != source_server_raw:
            raise ScoringTruthEventExecutionError("server source changed before publish")
        _publish_staging(staging, output)
    except BaseException:
        if staging.exists() and staging.is_dir() and not _is_link_like(staging):
            shutil.rmtree(staging)
        raise
    return manifest


def _require_canonical_submission_file(
    path: str | Path, *, name: str
) -> tuple[dict[str, Any], bytes]:
    submission, raw = _snapshot_json(path, name=name)
    expected_raw = _canonical_file_bytes(submission)
    if raw != expected_raw:
        raise ScoringTruthEventExecutionError(
            f"{name} must be canonical UTF-8 JSON followed by one newline"
        )
    return submission, raw


def _validate_bundle_ref(value: Any, *, manifest: Mapping[str, Any], name: str) -> None:
    reference = _require_exact_keys(
        value, {"bundle_id", "manifest_binding_sha256"}, name=name
    )
    expected = _bundle_ref(manifest)
    _require_exact_json(reference, expected, name=name)


def _annotation_submission_id(
    *, manifest: Mapping[str, Any], role_slot: str, annotator_id: str
) -> str:
    digest = _sha256(
        _canonical_json_bytes(
            {
                "execution_id": manifest["execution_id"],
                "execution_bundle": _bundle_ref(manifest),
                "authorization_binding_sha256": manifest["event_authorization"][
                    "binding_sha256"
                ],
                "role_slot": role_slot,
                "annotator_identity_key": _identity_key(
                    annotator_id, name="annotator_id"
                ),
            }
        )
    )
    return f"submission-{digest}"


def _validate_phase_observations(
    value: Any,
    *,
    event_code: str,
    start_ms: int,
    end_ms: int,
    name: str,
) -> None:
    expected_keys = set(PHASE_KEYS_BY_EVENT[event_code])
    phases = _require_exact_keys(value, expected_keys, name=name)
    observed_fs09: list[int] = []
    for key in PHASE_KEYS_BY_EVENT[event_code]:
        phase = _require_exact_keys(
            phases[key], {"status", "timestamp_ms", "reason"}, name=f"{name}.{key}"
        )
        if phase["status"] not in {"observed", "unobservable"}:
            raise ScoringTruthEventExecutionError(
                f"{name}.{key}.status must be observed or unobservable"
            )
        if not isinstance(phase["reason"], str):
            raise ScoringTruthEventExecutionError(
                f"{name}.{key}.reason must be a string"
            )
        if phase["status"] == "observed":
            timestamp = _require_int(
                phase["timestamp_ms"],
                name=f"{name}.{key}.timestamp_ms",
                minimum=start_ms,
                maximum=end_ms,
            )
            if phase["reason"] != "":
                raise ScoringTruthEventExecutionError(
                    f"{name}.{key}.reason must be blank when observed"
                )
            if event_code == "FS09":
                observed_fs09.append(timestamp)
        else:
            if phase["timestamp_ms"] is not None or not phase["reason"].strip():
                raise ScoringTruthEventExecutionError(
                    f"{name}.{key} unobservable requires null timestamp and reason"
                )
    if any(
        value < observed_fs09[index - 1]
        for index, value in enumerate(observed_fs09)
        if index > 0
    ):
        raise ScoringTruthEventExecutionError(
            "FS09 observed phase timestamps must be monotonic"
        )


def _validate_event_value(
    value: Any,
    *,
    task: Mapping[str, Any],
    role_slot: str,
    final: bool,
    name: str,
) -> None:
    id_field = "event_id" if final else "annotation_id"
    expected_keys = {
        id_field,
        "event_code",
        "start_ms",
        "end_ms",
        "phase_observations",
        "confidence_milli",
        "boundary_uncertainty_ms",
        "notes",
    }
    event = _require_exact_keys(value, expected_keys, name=name)
    identifier = _require_identifier(event[id_field], name=f"{name}.{id_field}")
    if not identifier.startswith(f"m93:{role_slot}:"):
        raise ScoringTruthEventExecutionError(
            f"{name}.{id_field} must start with m93:{role_slot}:"
        )
    if event["event_code"] not in EVENT_CODES:
        raise ScoringTruthEventExecutionError(f"{name}.event_code is invalid")
    start = _require_int(event["start_ms"], name=f"{name}.start_ms", minimum=0)
    end = _require_int(
        event["end_ms"],
        name=f"{name}.end_ms",
        minimum=start + 1,
        maximum=task["duration_ms"],
    )
    _require_int(
        event["confidence_milli"],
        name=f"{name}.confidence_milli",
        minimum=0,
        maximum=1000,
    )
    _require_int(
        event["boundary_uncertainty_ms"],
        name=f"{name}.boundary_uncertainty_ms",
        minimum=0,
    )
    if not isinstance(event["notes"], str):
        raise ScoringTruthEventExecutionError(f"{name}.notes must be a string")
    _validate_phase_observations(
        event["phase_observations"],
        event_code=event["event_code"],
        start_ms=start,
        end_ms=end,
        name=f"{name}.phase_observations",
    )


def _annotation_revision_payload(
    *,
    manifest: Mapping[str, Any],
    role_slot: str,
    annotator_id: str,
    task: Mapping[str, Any],
    annotation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "execution_id": manifest["execution_id"],
        "execution_bundle": _bundle_ref(manifest),
        "authorization_binding_sha256": manifest["event_authorization"][
            "binding_sha256"
        ],
        "role_slot": role_slot,
        "annotator_id": annotator_id,
        "task_id": task["task_id"],
        "video_id": task["video_id"],
        "annotation": dict(annotation),
    }


def _review_revision_payload(
    *,
    manifest: Mapping[str, Any],
    role_slot: str,
    annotator_id: str,
    task: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "execution_id": manifest["execution_id"],
        "execution_bundle": _bundle_ref(manifest),
        "authorization_binding_sha256": manifest["event_authorization"][
            "binding_sha256"
        ],
        "role_slot": role_slot,
        "annotator_id": annotator_id,
        "task_id": task["task_id"],
        "video_id": task["video_id"],
        "full_video_review": dict(review),
    }


def _validate_annotation_submission_object(
    submission: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    raw: bytes | None,
) -> dict[str, Any]:
    top_keys = {
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
    }
    value = _require_exact_keys(submission, top_keys, name="annotation submission")
    if (
        value["schema_version"] != SCHEMA_VERSION
        or value["submission_version"] != ANNOTATION_SUBMISSION_VERSION
        or value["status"] != ANNOTATION_SUBMISSION_STATUS
        or value["artifact_scope"]
        != "independent_full_video_event_phase_annotation"
    ):
        raise ScoringTruthEventExecutionError(
            "annotation submission version/status/scope is invalid"
        )
    role_slot = value["role_slot"]
    if role_slot not in {"A", "B"} or role_slot != manifest["role"]["slot"]:
        raise ScoringTruthEventExecutionError(
            "annotation submission role does not match execution bundle"
        )
    if value["execution_id"] != manifest["execution_id"]:
        raise ScoringTruthEventExecutionError("annotation execution_id mismatch")
    _validate_bundle_ref(
        value["execution_bundle"], manifest=manifest, name="execution_bundle"
    )
    if (
        value["authorization_binding_sha256"]
        != manifest["event_authorization"]["binding_sha256"]
    ):
        raise ScoringTruthEventExecutionError(
            "annotation authorization binding mismatch"
        )
    annotator_id = _require_identifier(value["annotator_id"], name="annotator_id")
    _identity_key(annotator_id, name="annotator_id")
    expected_submission_id = _annotation_submission_id(
        manifest=manifest, role_slot=role_slot, annotator_id=annotator_id
    )
    if value["submission_id"] != expected_submission_id:
        raise ScoringTruthEventExecutionError(
            "annotation submission_id is not deterministic for its lineage"
        )
    submitted_at = _parse_utc(
        value["submitted_at"], name="submitted_at", submission=True
    )
    exported_at = _parse_utc(
        value["exported_at"], name="exported_at", submission=True
    )
    generated_at = _parse_utc(manifest["generated_at"], name="bundle generated_at")
    if not generated_at <= submitted_at <= exported_at:
        raise ScoringTruthEventExecutionError(
            "bundle, submission, and export claimed chronology is invalid"
        )
    videos = value["videos"]
    tasks = manifest["scope"]["tasks"]
    if not isinstance(videos, list) or len(videos) != len(tasks):
        raise ScoringTruthEventExecutionError(
            "annotation submission must cover all three videos"
        )
    seen_ids: set[str] = set()
    for index, (video, task) in enumerate(zip(videos, tasks)):
        item = _require_exact_keys(
            video,
            {
                "task_id",
                "video_id",
                "full_video_review",
                "events",
                "video_revision_sha256",
            },
            name=f"videos[{index}]",
        )
        if item["task_id"] != task["task_id"] or item["video_id"] != task["video_id"]:
            raise ScoringTruthEventExecutionError(
                "annotation video order/scope drifted"
            )
        review = _require_exact_keys(
            item["full_video_review"],
            {"completed", "notes", "reviewed_at", "review_revision_sha256"},
            name=f"videos[{index}].full_video_review",
        )
        if review["completed"] is not True or not isinstance(review["notes"], str):
            raise ScoringTruthEventExecutionError(
                f"full-video review is incomplete: {task['video_id']}"
            )
        reviewed_at = _parse_utc(
            review["reviewed_at"],
            name=f"{task['video_id']} reviewed_at",
            submission=True,
        )
        if not generated_at <= reviewed_at <= submitted_at:
            raise ScoringTruthEventExecutionError(
                f"full-video review chronology is invalid: {task['video_id']}"
            )
        review_base = {
            "completed": review["completed"],
            "notes": review["notes"],
            "reviewed_at": review["reviewed_at"],
        }
        expected_review_sha = _sha256(
            _canonical_json_bytes(
                _review_revision_payload(
                    manifest=manifest,
                    role_slot=role_slot,
                    annotator_id=annotator_id,
                    task=task,
                    review=review_base,
                )
            )
        )
        if review["review_revision_sha256"] != expected_review_sha:
            raise ScoringTruthEventExecutionError(
                f"full-video review revision mismatch: {task['video_id']}"
            )
        events = item["events"]
        if not isinstance(events, list):
            raise ScoringTruthEventExecutionError(
                f"events must be an array: {task['video_id']}"
            )
        prior_key: tuple[int, str] | None = None
        for event_index, event in enumerate(events):
            row = _require_exact_keys(
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
                name=f"videos[{index}].events[{event_index}]",
            )
            if row["event_id"] != row["annotation_id"]:
                raise ScoringTruthEventExecutionError(
                    "annotation event_id must equal annotation_id"
                )
            decision = {
                key: row[key]
                for key in (
                    "annotation_id",
                    "event_code",
                    "start_ms",
                    "end_ms",
                    "phase_observations",
                    "confidence_milli",
                    "boundary_uncertainty_ms",
                    "notes",
                )
            }
            _validate_event_value(
                decision,
                task=task,
                role_slot=role_slot,
                final=False,
                name=f"event {row['annotation_id']}",
            )
            annotated_at = _parse_utc(
                row["annotated_at"],
                name=f"{row['annotation_id']} annotated_at",
                submission=True,
            )
            if not generated_at <= annotated_at <= reviewed_at <= submitted_at:
                raise ScoringTruthEventExecutionError(
                    f"annotation/review chronology is invalid: {row['annotation_id']}"
                )
            if row["annotation_id"] in seen_ids:
                raise ScoringTruthEventExecutionError(
                    f"duplicate annotation_id: {row['annotation_id']}"
                )
            seen_ids.add(row["annotation_id"])
            sort_key = (row["start_ms"], row["annotation_id"])
            if prior_key is not None and sort_key < prior_key:
                raise ScoringTruthEventExecutionError(
                    f"events are not canonically sorted: {task['video_id']}"
                )
            prior_key = sort_key
            row_without_revision = {
                key: row[key]
                for key in row
                if key != "annotation_revision_sha256"
            }
            expected_annotation_sha = _sha256(
                _canonical_json_bytes(
                    _annotation_revision_payload(
                        manifest=manifest,
                        role_slot=role_slot,
                        annotator_id=annotator_id,
                        task=task,
                        annotation=row_without_revision,
                    )
                )
            )
            if row["annotation_revision_sha256"] != expected_annotation_sha:
                raise ScoringTruthEventExecutionError(
                    f"annotation revision mismatch: {row['annotation_id']}"
                )
        video_base = {
            "task_id": item["task_id"],
            "video_id": item["video_id"],
            "full_video_review": item["full_video_review"],
            "events": item["events"],
        }
        expected_video_revision = _sha256(
            _canonical_json_bytes(
                {
                    "execution_id": manifest["execution_id"],
                    "execution_bundle": _bundle_ref(manifest),
                    "authorization_binding_sha256": manifest[
                        "event_authorization"
                    ]["binding_sha256"],
                    "role_slot": role_slot,
                    "annotator_id": annotator_id,
                    **video_base,
                }
            )
        )
        if item["video_revision_sha256"] != expected_video_revision:
            raise ScoringTruthEventExecutionError(
                f"video revision mismatch: {task['video_id']}"
            )
    revision_payload = {
        key: value[key]
        for key in value
        if key not in {"submission_revision_sha256", "exported_at"}
    }
    expected_submission_revision = _sha256(
        _canonical_json_bytes(revision_payload)
    )
    if value["submission_revision_sha256"] != expected_submission_revision:
        raise ScoringTruthEventExecutionError(
            "annotation submission revision mismatch"
        )
    if raw is not None and _canonical_file_bytes(value) != raw:
        raise ScoringTruthEventExecutionError(
            "annotation submission bytes are not canonical"
        )
    return dict(value)


def validate_scoring_truth_event_annotation_submission(
    submission_path: str | Path, execution_bundle_dir: str | Path
) -> dict[str, Any]:
    """Validate a canonical A/B export against its exact role execution bundle."""

    bundle = validate_scoring_truth_event_execution_bundle(execution_bundle_dir)
    submission, raw = _require_canonical_submission_file(
        submission_path, name="annotation submission"
    )
    validated = _validate_annotation_submission_object(
        submission, manifest=bundle["manifest"], raw=raw
    )
    return {
        "submission_path": Path(submission_path).expanduser().resolve(strict=True),
        "submission": validated,
        "submission_raw": raw,
        "submission_raw_sha256": _sha256(raw),
        "submission_id": validated["submission_id"],
        "submission_revision_sha256": validated["submission_revision_sha256"],
        "role_slot": validated["role_slot"],
        "annotator_id": validated["annotator_id"],
        "execution_bundle": bundle,
    }


def _execution_source_record(
    snapshot: Mapping[str, Any], *, role_slot: str, path: str
) -> dict[str, Any]:
    manifest = snapshot["manifest"]
    return {
        "role_slot": role_slot,
        "path": path,
        "raw_sha256": snapshot["manifest_sha256"],
        "bundle_id": manifest["bundle_id"],
        "execution_id": manifest["execution_id"],
        "manifest_binding_sha256": manifest["manifest_binding_sha256"],
        "content_root_sha256": manifest["content_root_sha256"],
    }


def _annotation_source_record(
    snapshot: Mapping[str, Any], *, role_slot: str, path: str
) -> dict[str, Any]:
    submission = snapshot["submission"]
    return {
        "role_slot": role_slot,
        "path": path,
        "raw_sha256": snapshot["submission_raw_sha256"],
        "submission_id": submission["submission_id"],
        "submission_revision_sha256": submission[
            "submission_revision_sha256"
        ],
        "annotator_id": submission["annotator_id"],
        "submitted_at": submission["submitted_at"],
        "exported_at": submission["exported_at"],
    }


def _validate_adjudication_manifest_shape(manifest: Mapping[str, Any]) -> None:
    _require_exact_keys(
        manifest, _ADJUDICATION_MANIFEST_KEYS, name="adjudication manifest"
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ScoringTruthEventExecutionError(
            "adjudication schema_version is invalid"
        )
    if manifest["bundle_version"] != ADJUDICATION_BUNDLE_VERSION:
        raise ScoringTruthEventExecutionError(
            "adjudication bundle_version is invalid"
        )
    if manifest["status"] != ADJUDICATION_BUNDLE_STATUS:
        raise ScoringTruthEventExecutionError("adjudication status is invalid")
    _validate_role(manifest["role"], expected_slot="C")
    _require_identifier(manifest["bundle_id"], name="bundle_id")
    _require_identifier(manifest["execution_id"], name="execution_id")
    generated_at = _parse_utc(manifest["generated_at"], name="generated_at")
    if (
        manifest["entrypoint"] != ENTRYPOINT
        or manifest["server_launcher"] != SERVER_LAUNCHER
    ):
        raise ScoringTruthEventExecutionError(
            "adjudication entrypoint/launcher drifted"
        )
    _require_exact_json(
        manifest["revision_contract"], REVISION_CONTRACT, name="revision_contract"
    )
    _require_exact_json(
        manifest["safety"], ADJUDICATION_SAFETY, name="adjudication safety"
    )
    _validate_scope_shape(manifest["scope"])
    try:
        validate_scoring_truth_authorization_binding(manifest["event_authorization"])
    except ScoringTruthAuthorizationError as exc:
        raise ScoringTruthEventExecutionError(
            f"adjudication authorization binding is invalid: {exc}"
        ) from exc
    if generated_at < _parse_utc(
        manifest["event_authorization"]["release_record"]["released_at"],
        name="operator release released_at",
    ):
        raise ScoringTruthEventExecutionError(
            "adjudication bundle generated_at predates the operator release"
        )
    expected_execution_id = _execution_id(
        manifest["event_authorization"],
        {
            "event_codes": list(EVENT_CODES),
            "phase_keys_by_event": {
                key: list(value) for key, value in PHASE_KEYS_BY_EVENT.items()
            },
            "tasks": [dict(item) for item in AUTHORIZED_TASKS],
        },
    )
    if manifest["execution_id"] != expected_execution_id:
        raise ScoringTruthEventExecutionError("adjudication execution_id mismatch")
    expected_bundle_id = _bundle_id(
        version=ADJUDICATION_BUNDLE_VERSION,
        execution_id=manifest["execution_id"],
        generated_at=manifest["generated_at"],
        role_slot="C",
    )
    if manifest["bundle_id"] != expected_bundle_id:
        raise ScoringTruthEventExecutionError(
            "adjudication bundle_id checksum mismatch"
        )
    execution_sources = manifest["execution_sources"]
    if not isinstance(execution_sources, list) or len(execution_sources) != 2:
        raise ScoringTruthEventExecutionError(
            "adjudication requires exact A/B execution sources"
        )
    annotation_sources = manifest["annotation_submissions"]
    if not isinstance(annotation_sources, list) or len(annotation_sources) != 2:
        raise ScoringTruthEventExecutionError(
            "adjudication requires exact A/B annotation submissions"
        )
    for index, slot in enumerate(("A", "B")):
        source = _require_exact_keys(
            execution_sources[index],
            _EXECUTION_SOURCE_KEYS,
            name=f"execution_sources[{index}]",
        )
        if (
            source["role_slot"] != slot
            or source["path"]
            != (SOURCE_EXECUTION_A_PATH if slot == "A" else SOURCE_EXECUTION_B_PATH)
        ):
            raise ScoringTruthEventExecutionError(
                "execution source role/path order is invalid"
            )
        for key in (
            "raw_sha256",
            "manifest_binding_sha256",
            "content_root_sha256",
        ):
            _require_sha(source[key], name=f"execution_sources[{index}].{key}")
        _require_identifier(source["bundle_id"], name="source bundle_id")
        if source["execution_id"] != manifest["execution_id"]:
            raise ScoringTruthEventExecutionError(
                "source execution_id does not match adjudication"
            )
        annotation = _require_exact_keys(
            annotation_sources[index],
            _ANNOTATION_SOURCE_KEYS,
            name=f"annotation_submissions[{index}]",
        )
        if (
            annotation["role_slot"] != slot
            or annotation["path"]
            != (SOURCE_ANNOTATION_A_PATH if slot == "A" else SOURCE_ANNOTATION_B_PATH)
        ):
            raise ScoringTruthEventExecutionError(
                "annotation source role/path order is invalid"
            )
        for key in ("raw_sha256", "submission_revision_sha256"):
            _require_sha(annotation[key], name=f"annotation source {key}")
        _require_identifier(annotation["submission_id"], name="submission_id")
        _identity_key(annotation["annotator_id"], name="annotator_id")
        submitted = _parse_utc(
            annotation["submitted_at"], name="source submitted_at", submission=True
        )
        exported = _parse_utc(
            annotation["exported_at"], name="source exported_at", submission=True
        )
        if submitted > exported:
            raise ScoringTruthEventExecutionError(
                "source submission chronology is invalid"
            )
        if exported > generated_at:
            raise ScoringTruthEventExecutionError(
                "adjudication bundle generated_at predates a source submission export"
            )
    if _identity_key(
        annotation_sources[0]["annotator_id"], name="annotator A"
    ) == _identity_key(annotation_sources[1]["annotator_id"], name="annotator B"):
        raise ScoringTruthEventExecutionError(
            "annotator A and B identities must be distinct"
        )
    _validate_manifest_binding(manifest)


def _parse_source_manifest(raw: bytes, *, name: str) -> dict[str, Any]:
    manifest = _parse_json_object(raw, name=name)
    _validate_execution_manifest_shape(manifest)
    return manifest


def validate_scoring_truth_event_adjudication_bundle(
    bundle_dir: str | Path,
) -> dict[str, Any]:
    """Validate a C bundle created only from exact verified A/B revisions."""

    bundle = Path(bundle_dir).expanduser().absolute()
    manifest, snapshots = _server_snapshot(bundle)
    _validate_adjudication_manifest_shape(manifest)
    _validate_artifacts(manifest, snapshots)
    try:
        authority = _authorization_snapshot(
            plan_path=bundle / AUTHORITY_PLAN_PATH,
            release_record_path=bundle / AUTHORITY_RELEASE_PATH,
            handoff_dir=bundle / AUTHORITY_HANDOFF_ROOT,
        )
    except ScoringTruthEventExecutionError as exc:
        raise ScoringTruthEventExecutionError(
            f"internal adjudication authorization replay failed: {exc}"
        ) from exc
    _require_exact_json(
        manifest["event_authorization"],
        authority["binding"],
        name="adjudication event_authorization",
    )
    expected_authority_paths = set(_authority_raw(authority))
    actual_authority_paths = {
        item["path"]
        for item in manifest["artifacts"]
        if item["path"].startswith("authority/")
    }
    if actual_authority_paths != expected_authority_paths:
        raise ScoringTruthEventExecutionError(
            "adjudication authority artifact topology drifted"
        )
    expected_artifact_paths = expected_authority_paths | {
        ENTRYPOINT,
        CORE_JS,
        WORKBENCH_JS,
        WORKBENCH_CSS,
        SERVER_LAUNCHER,
        SOURCE_EXECUTION_A_PATH,
        SOURCE_EXECUTION_B_PATH,
        SOURCE_ANNOTATION_A_PATH,
        SOURCE_ANNOTATION_B_PATH,
    }
    if {item["path"] for item in manifest["artifacts"]} != expected_artifact_paths:
        raise ScoringTruthEventExecutionError(
            "adjudication artifact topology is not exact"
        )
    _validate_scope_frame_rates_against_handoff(manifest["scope"], authority)
    _verify_scope_media(bundle=bundle, scope=manifest["scope"], snapshots=snapshots)

    source_manifests: dict[str, dict[str, Any]] = {}
    source_submissions: dict[str, dict[str, Any]] = {}
    source_submission_raw: dict[str, bytes] = {}
    for index, slot in enumerate(("A", "B")):
        execution_record = manifest["execution_sources"][index]
        raw_manifest = snapshots[execution_record["path"]]
        if _sha256(raw_manifest) != execution_record["raw_sha256"]:
            raise ScoringTruthEventExecutionError(
                f"source execution manifest bytes drifted: {slot}"
            )
        source_manifest = _parse_source_manifest(
            raw_manifest, name=f"source execution {slot} manifest"
        )
        if source_manifest["role"]["slot"] != slot:
            raise ScoringTruthEventExecutionError(
                f"source execution manifest role mismatch: {slot}"
            )
        if _parse_utc(
            source_manifest["generated_at"],
            name=f"source execution {slot} generated_at",
        ) > _parse_utc(manifest["generated_at"], name="adjudication generated_at"):
            raise ScoringTruthEventExecutionError(
                f"adjudication bundle generated_at predates source execution {slot}"
            )
        expected_execution_record = {
            "role_slot": slot,
            "path": execution_record["path"],
            "raw_sha256": _sha256(raw_manifest),
            "bundle_id": source_manifest["bundle_id"],
            "execution_id": source_manifest["execution_id"],
            "manifest_binding_sha256": source_manifest[
                "manifest_binding_sha256"
            ],
            "content_root_sha256": source_manifest["content_root_sha256"],
        }
        _require_exact_json(
            execution_record,
            expected_execution_record,
            name=f"execution source {slot}",
        )
        _require_exact_json(
            source_manifest["event_authorization"],
            manifest["event_authorization"],
            name=f"source execution {slot} authorization",
        )
        _require_exact_json(
            source_manifest["scope"],
            manifest["scope"],
            name=f"source execution {slot} scope",
        )
        _validate_source_files(
            source_manifest["source_files"],
            binding=manifest["event_authorization"],
            snapshots=None,
        )
        annotation_record = manifest["annotation_submissions"][index]
        annotation_raw = snapshots[annotation_record["path"]]
        if (
            _sha256(annotation_raw) != annotation_record["raw_sha256"]
            or _canonical_file_bytes(
                _parse_json_object(
                    annotation_raw, name=f"source annotation {slot} submission"
                )
            )
            != annotation_raw
        ):
            raise ScoringTruthEventExecutionError(
                f"source annotation {slot} bytes are not exact canonical JSON"
            )
        annotation = _parse_json_object(
            annotation_raw, name=f"source annotation {slot} submission"
        )
        validated = _validate_annotation_submission_object(
            annotation, manifest=source_manifest, raw=annotation_raw
        )
        expected_annotation_record = {
            "role_slot": slot,
            "path": annotation_record["path"],
            "raw_sha256": _sha256(annotation_raw),
            "submission_id": validated["submission_id"],
            "submission_revision_sha256": validated[
                "submission_revision_sha256"
            ],
            "annotator_id": validated["annotator_id"],
            "submitted_at": validated["submitted_at"],
            "exported_at": validated["exported_at"],
        }
        _require_exact_json(
            annotation_record,
            expected_annotation_record,
            name=f"annotation source {slot}",
        )
        source_manifests[slot] = source_manifest
        source_submissions[slot] = validated
        source_submission_raw[slot] = annotation_raw
    if _identity_key(
        source_submissions["A"]["annotator_id"], name="annotator A"
    ) == _identity_key(source_submissions["B"]["annotator_id"], name="annotator B"):
        raise ScoringTruthEventExecutionError(
            "annotator A and B identities must be distinct"
        )
    expected_bootstrap = _adjudication_bootstrap(manifest)
    _require_exact_json(
        _extract_bootstrap(snapshots[ENTRYPOINT]),
        expected_bootstrap,
        name="adjudication workbench bootstrap",
    )
    manifest_raw = snapshots[ADJUDICATION_MANIFEST_NAME]
    artifacts = {
        record["path"]: {**record, "raw": snapshots[record["path"]]}
        for record in manifest["artifacts"]
    }
    return {
        "bundle_dir": bundle.resolve(strict=True),
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "manifest_sha256": _sha256(manifest_raw),
        "manifest_binding_sha256": manifest["manifest_binding_sha256"],
        "bundle_id": manifest["bundle_id"],
        "execution_id": manifest["execution_id"],
        "role_slot": "C",
        "content_root_sha256": manifest["content_root_sha256"],
        "artifacts": artifacts,
        "authorization": authority["binding"],
        "bootstrap": expected_bootstrap,
        "source_execution_manifests": source_manifests,
        "source_submissions": source_submissions,
        "source_submission_raw": source_submission_raw,
    }


def build_scoring_truth_event_adjudication_bundle(
    *,
    annotator_a_bundle_dir: str | Path,
    annotator_a_submission_path: str | Path,
    annotator_b_bundle_dir: str | Path,
    annotator_b_submission_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Build the C workbench only after exact A and B bundles/exports validate."""

    output = Path(output_dir).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ScoringTruthEventExecutionError("output directory must not already exist")
    sources = (
        Path(annotator_a_bundle_dir),
        Path(annotator_a_submission_path),
        Path(annotator_b_bundle_dir),
        Path(annotator_b_submission_path),
    )
    if any(_paths_overlap(output, source) for source in sources):
        raise ScoringTruthEventExecutionError(
            "output must not overlap execution bundles or submissions"
        )
    bundle_a = validate_scoring_truth_event_execution_bundle(
        annotator_a_bundle_dir, expected_role_slot="A"
    )
    bundle_b = validate_scoring_truth_event_execution_bundle(
        annotator_b_bundle_dir, expected_role_slot="B"
    )
    submission_a = validate_scoring_truth_event_annotation_submission(
        annotator_a_submission_path, annotator_a_bundle_dir
    )
    submission_b = validate_scoring_truth_event_annotation_submission(
        annotator_b_submission_path, annotator_b_bundle_dir
    )
    if bundle_a["execution_id"] != bundle_b["execution_id"]:
        raise ScoringTruthEventExecutionError(
            "A/B execution IDs do not describe the same execution"
        )
    _require_exact_json(
        bundle_a["authorization"],
        bundle_b["authorization"],
        name="A/B authorization binding",
    )
    _require_exact_json(
        bundle_a["manifest"]["scope"],
        bundle_b["manifest"]["scope"],
        name="A/B execution scope",
    )
    if _identity_key(submission_a["annotator_id"], name="annotator A") == _identity_key(
        submission_b["annotator_id"], name="annotator B"
    ):
        raise ScoringTruthEventExecutionError(
            "annotator A and B identities must be distinct"
        )
    authority_raw_a = {
        path: record["raw"]
        for path, record in bundle_a["artifacts"].items()
        if path.startswith("authority/")
    }
    authority_raw_b = {
        path: record["raw"]
        for path, record in bundle_b["artifacts"].items()
        if path.startswith("authority/")
    }
    if authority_raw_a != authority_raw_b:
        raise ScoringTruthEventExecutionError(
            "A/B authority snapshots are not byte-identical"
        )
    source_server_raw = _snapshot_file(_server_source_path(), name=SERVER_LAUNCHER)
    if _sha256(source_server_raw) != APPROVED_SERVER_SHA256:
        raise ScoringTruthEventExecutionError("standalone server authority drifted")
    asset_sources = {
        name: _approved_asset_snapshot(name) for name in APPROVED_ASSET_SHA256
    }
    parent = output.parent.resolve(strict=False)
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    if staging.exists() or staging.is_symlink():
        raise ScoringTruthEventExecutionError("unique staging path unexpectedly exists")
    try:
        staging.mkdir()
        _write_tree(staging, authority_raw_a)
        latest_source_export = max(
            _parse_utc(
                submission_a["submission"]["exported_at"],
                name="A exported_at",
                submission=True,
            ),
            _parse_utc(
                submission_b["submission"]["exported_at"],
                name="B exported_at",
                submission=True,
            ),
        )
        generated_value = max(
            _parse_utc(_now_utc(), name="local adjudication build time"),
            latest_source_export,
        )
        generated_at = _format_generated_utc(generated_value)
        execution_sources = [
            _execution_source_record(
                bundle_a, role_slot="A", path=SOURCE_EXECUTION_A_PATH
            ),
            _execution_source_record(
                bundle_b, role_slot="B", path=SOURCE_EXECUTION_B_PATH
            ),
        ]
        annotation_sources = [
            _annotation_source_record(
                submission_a, role_slot="A", path=SOURCE_ANNOTATION_A_PATH
            ),
            _annotation_source_record(
                submission_b, role_slot="B", path=SOURCE_ANNOTATION_B_PATH
            ),
        ]
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "bundle_version": ADJUDICATION_BUNDLE_VERSION,
            "bundle_id": _bundle_id(
                version=ADJUDICATION_BUNDLE_VERSION,
                execution_id=bundle_a["execution_id"],
                generated_at=generated_at,
                role_slot="C",
            ),
            "execution_id": bundle_a["execution_id"],
            "generated_at": generated_at,
            "status": ADJUDICATION_BUNDLE_STATUS,
            "role": {"slot": "C", "kind": ROLE_KIND["C"]},
            "event_authorization": bundle_a["authorization"],
            "execution_sources": execution_sources,
            "annotation_submissions": annotation_sources,
            "scope": bundle_a["manifest"]["scope"],
            "revision_contract": REVISION_CONTRACT,
            "entrypoint": ENTRYPOINT,
            "server_launcher": SERVER_LAUNCHER,
            "artifacts": [],
            "content_root_sha256": "0" * 64,
            "manifest_binding_sha256": "0" * 64,
            "safety": ADJUDICATION_SAFETY,
        }
        manifest["manifest_binding_sha256"] = (
            scoring_truth_event_manifest_binding_sha256(manifest)
        )
        review_raw = _inject_bootstrap(
            asset_sources[WORKBENCH_HTML_SOURCE], _adjudication_bootstrap(manifest)
        )
        raw_by_path = {
            **authority_raw_a,
            SOURCE_EXECUTION_A_PATH: bundle_a["manifest_raw"],
            SOURCE_EXECUTION_B_PATH: bundle_b["manifest_raw"],
            SOURCE_ANNOTATION_A_PATH: submission_a["submission_raw"],
            SOURCE_ANNOTATION_B_PATH: submission_b["submission_raw"],
            ENTRYPOINT: review_raw,
            CORE_JS: asset_sources[CORE_JS],
            WORKBENCH_JS: asset_sources[WORKBENCH_JS],
            WORKBENCH_CSS: asset_sources[WORKBENCH_CSS],
            SERVER_LAUNCHER: source_server_raw,
        }
        manifest["artifacts"] = _artifact_records(raw_by_path)
        manifest["content_root_sha256"] = _content_root(manifest["artifacts"])
        _write_tree(
            staging,
            {
                key: value
                for key, value in raw_by_path.items()
                if key not in authority_raw_a
            },
        )
        _write_new_file(
            staging / ADJUDICATION_MANIFEST_NAME, _pretty_json_bytes(manifest)
        )
        validate_scoring_truth_event_adjudication_bundle(staging)
        recheck_bundle_a = validate_scoring_truth_event_execution_bundle(
            annotator_a_bundle_dir, expected_role_slot="A"
        )
        recheck_bundle_b = validate_scoring_truth_event_execution_bundle(
            annotator_b_bundle_dir, expected_role_slot="B"
        )
        recheck_submission_a = validate_scoring_truth_event_annotation_submission(
            annotator_a_submission_path, annotator_a_bundle_dir
        )
        recheck_submission_b = validate_scoring_truth_event_annotation_submission(
            annotator_b_submission_path, annotator_b_bundle_dir
        )
        for original, replay, name in (
            (bundle_a["manifest_raw"], recheck_bundle_a["manifest_raw"], "A bundle"),
            (bundle_b["manifest_raw"], recheck_bundle_b["manifest_raw"], "B bundle"),
            (
                submission_a["submission_raw"],
                recheck_submission_a["submission_raw"],
                "A submission",
            ),
            (
                submission_b["submission_raw"],
                recheck_submission_b["submission_raw"],
                "B submission",
            ),
        ):
            if original != replay:
                raise ScoringTruthEventExecutionError(
                    f"{name} changed before adjudication publish"
                )
        for name, raw in asset_sources.items():
            if _snapshot_file(_asset_path(name), name=f"{name} recheck") != raw:
                raise ScoringTruthEventExecutionError(
                    f"workbench source changed before publish: {name}"
                )
        if _snapshot_file(_server_source_path(), name="server recheck") != source_server_raw:
            raise ScoringTruthEventExecutionError("server source changed before publish")
        _publish_staging(staging, output)
    except BaseException:
        if staging.exists() and staging.is_dir() and not _is_link_like(staging):
            shutil.rmtree(staging)
        raise
    return manifest


def _source_submission_summary(
    *,
    submission: Mapping[str, Any],
    raw_sha256: str,
) -> dict[str, Any]:
    return {
        "submission_id": submission["submission_id"],
        "raw_sha256": raw_sha256,
        "annotator_id": submission["annotator_id"],
        "execution_bundle": submission["execution_bundle"],
        "submission_revision_sha256": submission[
            "submission_revision_sha256"
        ],
        "video_revision_sha256_by_video": {
            video["video_id"]: video["video_revision_sha256"]
            for video in submission["videos"]
        },
    }


def _source_video_revisions(
    sources: Mapping[str, Mapping[str, Any]], video_id: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    for slot in ("A", "B"):
        video = next(
            (item for item in sources[slot]["videos"] if item["video_id"] == video_id),
            None,
        )
        if video is None:
            raise ScoringTruthEventExecutionError(
                f"source {slot} is missing video: {video_id}"
            )
        result[slot] = video["video_revision_sha256"]
    return result


def _source_event_index(
    sources: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[str, str], tuple[str, Mapping[str, Any]]]:
    result: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}
    for slot in ("A", "B"):
        for video in sources[slot]["videos"]:
            for event in video["events"]:
                result[(slot, event["annotation_id"])] = (video["video_id"], event)
    return result


def _validate_adjudication_submission_object(
    submission: Mapping[str, Any],
    *,
    bundle_snapshot: Mapping[str, Any],
    raw: bytes | None,
) -> dict[str, Any]:
    manifest = bundle_snapshot["manifest"]
    sources = bundle_snapshot["source_submissions"]
    top_keys = {
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
    }
    value = _require_exact_keys(
        submission, top_keys, name="adjudication submission"
    )
    if (
        value["schema_version"] != SCHEMA_VERSION
        or value["adjudication_version"] != ADJUDICATION_SUBMISSION_VERSION
        or value["status"] != ADJUDICATION_SUBMISSION_STATUS
        or value["artifact_scope"]
        != "independent_full_video_event_phase_adjudication"
    ):
        raise ScoringTruthEventExecutionError(
            "adjudication submission version/status/scope is invalid"
        )
    if value["execution_id"] != manifest["execution_id"]:
        raise ScoringTruthEventExecutionError(
            "adjudication submission execution_id mismatch"
        )
    _validate_bundle_ref(
        value["adjudication_bundle"],
        manifest=manifest,
        name="adjudication_bundle",
    )
    if (
        value["authorization_binding_sha256"]
        != manifest["event_authorization"]["binding_sha256"]
    ):
        raise ScoringTruthEventExecutionError(
            "adjudication authorization binding mismatch"
        )
    if value["reviewer_slot"] != "C":
        raise ScoringTruthEventExecutionError("reviewer_slot must be C")
    reviewer_id = _require_identifier(value["reviewer_id"], name="reviewer_id")
    reviewer_identity = _identity_key(reviewer_id, name="reviewer_id")
    source_identities = {
        _identity_key(sources[slot]["annotator_id"], name=f"annotator {slot}")
        for slot in ("A", "B")
    }
    if reviewer_identity in source_identities:
        raise ScoringTruthEventExecutionError(
            "reviewer C identity must differ from annotators A and B"
        )
    expected_source_summaries = {
        slot: _source_submission_summary(
            submission=sources[slot],
            raw_sha256=_sha256(bundle_snapshot["source_submission_raw"][slot]),
        )
        for slot in ("A", "B")
    }
    _require_exact_json(
        value["source_submissions"],
        expected_source_summaries,
        name="adjudication source_submissions",
    )
    generated_at = _parse_utc(manifest["generated_at"], name="bundle generated_at")
    adjudicated_at = _parse_utc(
        value["adjudicated_at"], name="adjudicated_at", submission=True
    )
    exported_at = _parse_utc(
        value["exported_at"], name="exported_at", submission=True
    )
    if not generated_at <= adjudicated_at <= exported_at:
        raise ScoringTruthEventExecutionError(
            "adjudication bundle/submission/export chronology is invalid"
        )
    video_reviews = value["video_adjudications"]
    tasks = manifest["scope"]["tasks"]
    if not isinstance(video_reviews, list) or len(video_reviews) != len(tasks):
        raise ScoringTruthEventExecutionError(
            "video_adjudications must cover all three videos"
        )
    video_review_times: dict[str, datetime] = {}
    for index, (review, task) in enumerate(zip(video_reviews, tasks)):
        item = _require_exact_keys(
            review,
            {
                "task_id",
                "video_id",
                "completed",
                "notes",
                "adjudicated_at",
                "review_revision_sha256",
            },
            name=f"video_adjudications[{index}]",
        )
        if (
            item["task_id"] != task["task_id"]
            or item["video_id"] != task["video_id"]
            or item["completed"] is not True
            or not isinstance(item["notes"], str)
        ):
            raise ScoringTruthEventExecutionError(
                f"video adjudication review is invalid: {task['video_id']}"
            )
        item_time = _parse_utc(
            item["adjudicated_at"],
            name=f"{task['video_id']} video adjudicated_at",
            submission=True,
        )
        if not generated_at <= item_time <= adjudicated_at:
            raise ScoringTruthEventExecutionError(
                f"video adjudication chronology is invalid: {task['video_id']}"
            )
        video_review_times[task["video_id"]] = item_time
        source_video_revisions = _source_video_revisions(
            sources, task["video_id"]
        )
        base = {
            "task_id": item["task_id"],
            "video_id": item["video_id"],
            "completed": True,
            "notes": item["notes"],
            "adjudicated_at": item["adjudicated_at"],
        }
        payload = {
            "execution_id": manifest["execution_id"],
            "adjudication_bundle": _bundle_ref(manifest),
            "authorization_binding_sha256": manifest["event_authorization"][
                "binding_sha256"
            ],
            "reviewer_slot": "C",
            "reviewer_id": reviewer_id,
            "source_video_revisions": source_video_revisions,
            "video_adjudication": base,
        }
        if item["review_revision_sha256"] != _sha256(
            _canonical_json_bytes(payload)
        ):
            raise ScoringTruthEventExecutionError(
                f"video adjudication revision mismatch: {task['video_id']}"
            )
    decisions = value["decisions"]
    if not isinstance(decisions, list):
        raise ScoringTruthEventExecutionError("decisions must be an array")
    source_index = _source_event_index(sources)
    coverage: dict[tuple[str, str], list[str]] = {
        key: [] for key in source_index
    }
    seen_adjudications: set[str] = set()
    seen_final_events: set[str] = set()
    prior_sort: tuple[str, int, str] | None = None
    for index, decision in enumerate(decisions):
        item = _require_exact_keys(
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
            name=f"decisions[{index}]",
        )
        adjudication_id = _require_identifier(
            item["adjudication_id"], name=f"decisions[{index}].adjudication_id"
        )
        if not adjudication_id.startswith("m93:C:"):
            raise ScoringTruthEventExecutionError(
                "adjudication_id must start with m93:C:"
            )
        if adjudication_id in seen_adjudications:
            raise ScoringTruthEventExecutionError(
                f"duplicate adjudication_id: {adjudication_id}"
            )
        seen_adjudications.add(adjudication_id)
        task = next(
            (task for task in tasks if task["video_id"] == item["video_id"]),
            None,
        )
        if task is None:
            raise ScoringTruthEventExecutionError(
                f"unknown adjudication video_id: {item['video_id']}"
            )
        expected_video_revisions = _source_video_revisions(
            sources, item["video_id"]
        )
        _require_exact_json(
            item["source_video_revisions"],
            expected_video_revisions,
            name=f"{adjudication_id}.source_video_revisions",
        )
        if item["decision_status"] not in {
            "accepted_event",
            "rejected_sources",
            "c_added_event",
        }:
            raise ScoringTruthEventExecutionError(
                f"invalid decision_status: {adjudication_id}"
            )
        if not isinstance(item["decision_reason"], str):
            raise ScoringTruthEventExecutionError(
                f"decision_reason must be a string: {adjudication_id}"
            )
        refs = item["source_annotation_revisions"]
        if not isinstance(refs, list):
            raise ScoringTruthEventExecutionError(
                f"source_annotation_revisions must be an array: {adjudication_id}"
            )
        local_refs: set[tuple[str, str]] = set()
        for ref_index, ref in enumerate(refs):
            source_ref = _require_exact_keys(
                ref,
                {
                    "role_slot",
                    "annotation_id",
                    "annotation_revision_sha256",
                    "relation",
                },
                name=f"{adjudication_id}.refs[{ref_index}]",
            )
            if source_ref["role_slot"] not in {"A", "B"}:
                raise ScoringTruthEventExecutionError(
                    f"invalid source role: {adjudication_id}"
                )
            key = (source_ref["role_slot"], source_ref["annotation_id"])
            if key in local_refs:
                raise ScoringTruthEventExecutionError(
                    f"duplicate source ref inside decision: {key}"
                )
            local_refs.add(key)
            source = source_index.get(key)
            if (
                source is None
                or source[0] != item["video_id"]
                or source[1]["annotation_revision_sha256"]
                != source_ref["annotation_revision_sha256"]
            ):
                raise ScoringTruthEventExecutionError(
                    f"source annotation revision mismatch: {key}"
                )
            if source_ref["relation"] not in {
                "supports",
                "merge_source",
                "split_source",
                "rejected_source",
            }:
                raise ScoringTruthEventExecutionError(
                    f"invalid source relation: {key}"
                )
            coverage[key].append(source_ref["relation"])
        status = item["decision_status"]
        event = item["event"]
        if status == "rejected_sources":
            if (
                event is not None
                or not refs
                or not item["decision_reason"].strip()
                or any(ref["relation"] != "rejected_source" for ref in refs)
            ):
                raise ScoringTruthEventExecutionError(
                    f"rejected_sources contract is invalid: {adjudication_id}"
                )
            sort_start = 0
        else:
            if event is None:
                raise ScoringTruthEventExecutionError(
                    f"{status} requires a final event: {adjudication_id}"
                )
            _validate_event_value(
                event,
                task=task,
                role_slot="C",
                final=True,
                name=f"{adjudication_id}.event",
            )
            final_id = event["event_id"]
            if final_id in seen_final_events:
                raise ScoringTruthEventExecutionError(
                    f"duplicate final event_id: {final_id}"
                )
            seen_final_events.add(final_id)
            sort_start = event["start_ms"]
            if status == "accepted_event" and (
                not refs or any(ref["relation"] == "rejected_source" for ref in refs)
            ):
                raise ScoringTruthEventExecutionError(
                    f"accepted_event source refs are invalid: {adjudication_id}"
                )
            if status == "c_added_event" and (
                refs or not item["decision_reason"].strip()
            ):
                raise ScoringTruthEventExecutionError(
                    f"c_added_event requires no refs and a reason: {adjudication_id}"
                )
        item_time = _parse_utc(
            item["adjudicated_at"],
            name=f"{adjudication_id}.adjudicated_at",
            submission=True,
        )
        if not (
            generated_at
            <= item_time
            <= video_review_times[item["video_id"]]
            <= adjudicated_at
        ):
            raise ScoringTruthEventExecutionError(
                f"decision/video-review chronology is invalid: {adjudication_id}"
            )
        sort_key = (item["video_id"], sort_start, adjudication_id)
        if prior_sort is not None and sort_key < prior_sort:
            raise ScoringTruthEventExecutionError(
                "adjudication decisions are not canonically sorted"
            )
        prior_sort = sort_key
        base = {
            key: item[key]
            for key in (
                "adjudication_id",
                "video_id",
                "decision_status",
                "source_video_revisions",
                "source_annotation_revisions",
                "event",
                "decision_reason",
                "adjudicated_at",
            )
        }
        payload = {
            "execution_id": manifest["execution_id"],
            "adjudication_bundle": _bundle_ref(manifest),
            "authorization_binding_sha256": manifest["event_authorization"][
                "binding_sha256"
            ],
            "reviewer_slot": "C",
            "reviewer_id": reviewer_id,
            "decision": base,
        }
        if item["adjudication_revision_sha256"] != _sha256(
            _canonical_json_bytes(payload)
        ):
            raise ScoringTruthEventExecutionError(
                f"adjudication revision mismatch: {adjudication_id}"
            )
    missing = [key for key, relations in coverage.items() if not relations]
    invalid_duplicates = [
        key
        for key, relations in coverage.items()
        if len(relations) > 1 and any(value != "split_source" for value in relations)
    ]
    if missing or invalid_duplicates:
        raise ScoringTruthEventExecutionError(
            "adjudication source coverage is incomplete or non-reciprocal"
        )
    revision_payload = {
        key: value[key]
        for key in value
        if key
        not in {"adjudication_submission_revision_sha256", "exported_at"}
    }
    expected_revision = _sha256(_canonical_json_bytes(revision_payload))
    if value["adjudication_submission_revision_sha256"] != expected_revision:
        raise ScoringTruthEventExecutionError(
            "adjudication submission revision mismatch"
        )
    if raw is not None and _canonical_file_bytes(value) != raw:
        raise ScoringTruthEventExecutionError(
            "adjudication submission bytes are not canonical"
        )
    return dict(value)


def validate_scoring_truth_event_adjudication_submission(
    submission_path: str | Path, adjudication_bundle_dir: str | Path
) -> dict[str, Any]:
    """Validate C's finalized canonical export and exact A/B revision coverage."""

    bundle = validate_scoring_truth_event_adjudication_bundle(
        adjudication_bundle_dir
    )
    submission, raw = _require_canonical_submission_file(
        submission_path, name="adjudication submission"
    )
    validated = _validate_adjudication_submission_object(
        submission, bundle_snapshot=bundle, raw=raw
    )
    return {
        "submission_path": Path(submission_path).expanduser().resolve(strict=True),
        "submission": validated,
        "submission_raw": raw,
        "submission_raw_sha256": _sha256(raw),
        "adjudication_submission_revision_sha256": validated[
            "adjudication_submission_revision_sha256"
        ],
        "role_slot": "C",
        "reviewer_id": validated["reviewer_id"],
        "adjudication_bundle": bundle,
    }


__all__ = [
    "ADJUDICATION_BUNDLE_STATUS",
    "ADJUDICATION_BUNDLE_VERSION",
    "ADJUDICATION_MANIFEST_NAME",
    "ADJUDICATION_SUBMISSION_STATUS",
    "ADJUDICATION_SUBMISSION_VERSION",
    "ANNOTATION_SUBMISSION_STATUS",
    "ANNOTATION_SUBMISSION_VERSION",
    "EXECUTION_BUNDLE_STATUS",
    "EXECUTION_BUNDLE_VERSION",
    "EXECUTION_MANIFEST_NAME",
    "ScoringTruthEventExecutionError",
    "build_scoring_truth_event_adjudication_bundle",
    "build_scoring_truth_event_execution_bundle",
    "scoring_truth_event_manifest_binding_sha256",
    "validate_scoring_truth_event_adjudication_bundle",
    "validate_scoring_truth_event_adjudication_submission",
    "validate_scoring_truth_event_annotation_submission",
    "validate_scoring_truth_event_execution_bundle",
]
