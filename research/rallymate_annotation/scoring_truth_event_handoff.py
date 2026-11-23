from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType, ModuleType
from typing import Any, Mapping


SCHEMA_VERSION = "1.0.0"
BUNDLE_VERSION = "scoring-truth-event-handoff-v1.0.0"
BUNDLE_STATUS = "technical_handoff_verified_external_protocol_required"
MANIFEST_NAME = "event-handoff-manifest.json"
SERVER_LAUNCHER = "serve_scoring_truth_event_handoff.py"
WORKBENCH_JS = "scoring-truth-event-workbench.js"
WORKBENCH_CSS = "scoring-truth-event-workbench.css"
SOURCE_PACK_VERSION = "scoring-truth-pack-v0.3.0"
SOURCE_PACK_STATUS = "annotation_required_no_truth_or_thresholds_generated"
SOURCE_CONTRACT_SHA256 = (
    "19422D7256E54A4E0BF56A84590A2F6CFDB29C12DC1E2650EDE7013B054D082E"
)
SERVER_SHA256 = "5E86B3709F428C792F8D4CC9F1828DD2B078E09A023AF29EE6395075A608E2DB"
WORKBENCH_JS_SHA256 = (
    "9A4EF6A6D8CD645F17EF0ADB0834EBEDCF883EEEFD3BD5F5090F0D56C6F0A7B4"
)
WORKBENCH_CSS_SHA256 = (
    "50099B99D1A0DF2581C4FC5A949F520C82DAA432987C65F137401E551AD005E7"
)

EVENT_CODES = ("FS01", "FS02", "FS09")
INDICATOR_IDS = (
    "FS01-M02",
    "FS01-M03",
    "FS01-M04",
    "FS01-M05",
    "FS02-M02",
    "FS02-M03",
    "FS02-M04",
    "FS02-M05",
    "FS09-M01",
    "FS09-M02",
    "FS09-M03",
    "FS09-M04",
    "FS09-M05",
)
VIDEO_SPECS = (
    {
        "task_id": "m89:3ae77ee3271d67de171585a5c39ddd69:full-video-event-phase",
        "video_id": "3ae77ee3271d67de171585a5c39ddd69",
        "media_path": "media/3ae77ee3271d67de171585a5c39ddd69.mp4",
        "sha256": "D5194DF6070DA253DEF5DB599E4F81847F4BEF3E146AB0A5D39F7F077F9CB5D9",
        "frame_rate": {"numerator": 30000, "denominator": 1001},
        "full_video_review_required": True,
    },
    {
        "task_id": "m89:850cb0006b406c7176eeda8d711cd065:full-video-event-phase",
        "video_id": "850cb0006b406c7176eeda8d711cd065",
        "media_path": "media/850cb0006b406c7176eeda8d711cd065.mp4",
        "sha256": "71D3F59B7A8B966EF7645CAC412CB03679376F70A2F080D270391A32697D7FA9",
        "frame_rate": {"numerator": 30, "denominator": 1},
        "full_video_review_required": True,
    },
    {
        "task_id": "m89:8d7754d0de6d315674013d5b69a0b6ba:full-video-event-phase",
        "video_id": "8d7754d0de6d315674013d5b69a0b6ba",
        "media_path": "media/8d7754d0de6d315674013d5b69a0b6ba.mp4",
        "sha256": "FEDA989F394818B27AE38E5BA12DF4690E6139DF53DB9CD87E6C6278B3C2E7E0",
        "frame_rate": {"numerator": 24, "denominator": 1},
        "full_video_review_required": True,
    },
)
PHASE_KEYS_BY_EVENT = {
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
PUBLIC_ARTIFACT_PATHS = (
    "OPERATOR_README.md",
    *(item["media_path"] for item in VIDEO_SPECS),
    "review.html",
    WORKBENCH_CSS,
    WORKBENCH_JS,
    SERVER_LAUNCHER,
)

_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_WINDOWS_ABSOLUTE_RE = re.compile(rb"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")


class ScoringTruthEventHandoffError(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ScoringTruthEventHandoffError("value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _reject_json_constant(value: str) -> None:
    raise ScoringTruthEventHandoffError(f"invalid JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScoringTruthEventHandoffError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json_object(path: Path, *, name: str) -> tuple[dict[str, Any], bytes]:
    raw = _snapshot_file(path, name=name)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except ScoringTruthEventHandoffError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ScoringTruthEventHandoffError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ScoringTruthEventHandoffError(f"{name} must be a JSON object")
    return value, raw


def _require_sha(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ScoringTruthEventHandoffError(f"{name} must be uppercase SHA-256")
    return value


def _canonical_utc(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScoringTruthEventHandoffError(f"{name} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScoringTruthEventHandoffError(f"{name} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ScoringTruthEventHandoffError(f"{name} must identify UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _snapshot_file(path: Path, *, name: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ScoringTruthEventHandoffError(f"missing file: {name}") from exc
    if _is_link_like(path) or not path.is_file():
        raise ScoringTruthEventHandoffError(f"{name} must be a regular file")
    try:
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise ScoringTruthEventHandoffError(f"could not read file: {name}") from exc
    before_id = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_id = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_id != after_id or len(raw) != after.st_size:
        raise ScoringTruthEventHandoffError(f"file changed while read: {name}")
    return raw


def _source_projection(manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        created_at = _canonical_utc(manifest["created_at"], name="source created_at")
        videos = [
            {"video_id": item["video_id"], "sha256": item["sha256"]}
            for item in manifest["videos"]
        ]
        projection = {
            "pack_version": manifest["pack_version"],
            "manifest_created_at": created_at,
            "source_status": manifest["status"],
            "event_codes": manifest["scope"]["events"],
            "indicator_ids": manifest["scope"]["indicators"],
            "videos": videos,
        }
    except (KeyError, TypeError) as exc:
        raise ScoringTruthEventHandoffError("source pack identity is incomplete") from exc
    return projection


def _collect_leaf_tokens(value: Any, *, skip_keys: set[str] | None = None) -> set[bytes]:
    skip = skip_keys or set()
    tokens: set[bytes] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in skip:
                tokens.update(_collect_leaf_tokens(item, skip_keys=skip))
    elif isinstance(value, list):
        for item in value:
            tokens.update(_collect_leaf_tokens(item, skip_keys=skip))
    elif isinstance(value, str) and len(value) >= 4:
        tokens.add(value.encode("utf-8").lower())
    elif isinstance(value, int) and not isinstance(value, bool) and len(str(abs(value))) >= 5:
        tokens.add(str(value).encode("ascii"))
    return tokens


def _sensitive_source_tokens(manifest: dict[str, Any]) -> set[bytes]:
    tokens = {
        bytes.fromhex(value)
        for value in (
            "63616e646964617465",
            "70696c6f74",
            "6d6f64656c",
            "66756c6c2d74657374",
            "70726976617465",
        )
    }
    for section in ("scoring_source_binding", "pilot_keypoint_events"):
        if section in manifest:
            tokens.update(
                _collect_leaf_tokens(
                    manifest[section],
                    skip_keys={"video_id", "event_code", "indicator_id"},
                )
            )
    for item in manifest.get("videos", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            path_text = item["path"].encode("utf-8").lower()
            tokens.add(path_text)
            source_path = item["path"]
            parent = (
                PureWindowsPath(source_path).parent
                if PureWindowsPath(source_path).drive
                else Path(source_path).parent
            )
            if str(parent) not in {"", "."}:
                tokens.add(str(parent).encode("utf-8").lower())
    return {token for token in tokens if token}


def _scan_public_bytes(
    files: Mapping[str, bytes], *, source_manifest: dict[str, Any]
) -> None:
    tokens = _sensitive_source_tokens(source_manifest)
    for path, raw in files.items():
        lowered = raw.lower()
        for token in tokens:
            if token in lowered:
                raise ScoringTruthEventHandoffError(
                    f"public byte boundary includes non-public source content: {path}"
                )
        if PurePosixPath(path).suffix.lower() != ".mp4" and _WINDOWS_ABSOLUTE_RE.search(raw):
            raise ScoringTruthEventHandoffError(
                f"public text includes an absolute source location: {path}"
            )


def _video_directory(
    manifest: dict[str, Any], explicit: str | Path | None
) -> Path:
    if explicit is not None:
        directory = Path(explicit).expanduser()
    else:
        try:
            parents = {Path(item["path"]).expanduser().parent for item in manifest["videos"]}
        except (KeyError, TypeError) as exc:
            raise ScoringTruthEventHandoffError("source video locations are invalid") from exc
        if len(parents) != 1:
            raise ScoringTruthEventHandoffError(
                "source videos do not share one directory; pass video_dir explicitly"
            )
        directory = next(iter(parents))
    if _is_link_like(directory):
        raise ScoringTruthEventHandoffError("video_dir may not be a link")
    try:
        resolved = directory.resolve(strict=True)
    except OSError as exc:
        raise ScoringTruthEventHandoffError("video_dir does not exist") from exc
    if not resolved.is_dir():
        raise ScoringTruthEventHandoffError("video_dir must be a directory")
    return resolved


def _source_snapshot(
    source_pack_dir: str | Path, *, video_dir: str | Path | None = None
) -> dict[str, Any]:
    requested = Path(source_pack_dir).expanduser()
    if _is_link_like(requested):
        raise ScoringTruthEventHandoffError("source pack may not be a link")
    try:
        source = requested.resolve(strict=True)
    except OSError as exc:
        raise ScoringTruthEventHandoffError("source pack does not exist") from exc
    if not source.is_dir():
        raise ScoringTruthEventHandoffError("source pack must be a directory")
    manifest, manifest_raw = _read_json_object(
        source / "manifest.json", name="source manifest.json"
    )
    projection = _source_projection(manifest)
    expected_projection_header = {
        "pack_version": SOURCE_PACK_VERSION,
        "source_status": SOURCE_PACK_STATUS,
        "event_codes": list(EVENT_CODES),
        "indicator_ids": list(INDICATOR_IDS),
    }
    for key, expected in expected_projection_header.items():
        if projection.get(key) != expected:
            raise ScoringTruthEventHandoffError(f"source {key} drifted")
    if len(projection["videos"]) != len(VIDEO_SPECS):
        raise ScoringTruthEventHandoffError("source must select exactly three videos")

    try:
        source_video_rows = manifest["videos"]
    except (KeyError, TypeError) as exc:
        raise ScoringTruthEventHandoffError("source videos are missing") from exc
    if not isinstance(source_video_rows, list):
        raise ScoringTruthEventHandoffError("source videos must be an array")
    selected_dir = _video_directory(manifest, video_dir)
    video_raw: dict[str, bytes] = {}
    for index, (source_row, expected) in enumerate(
        zip(source_video_rows, VIDEO_SPECS, strict=True)
    ):
        if not isinstance(source_row, dict):
            raise ScoringTruthEventHandoffError(f"source videos[{index}] is invalid")
        expected_identity = {
            "video_id": expected["video_id"],
            "sha256": expected["sha256"],
            "full_video_event_annotation_required": True,
        }
        for key, value in expected_identity.items():
            if source_row.get(key) != value:
                raise ScoringTruthEventHandoffError(
                    f"source video identity drifted: {expected['video_id']}"
                )
        source_path = source_row.get("path")
        if not isinstance(source_path, str) or Path(source_path).name != f"{expected['video_id']}.mp4":
            raise ScoringTruthEventHandoffError(
                f"source video filename drifted: {expected['video_id']}"
            )
        selected = selected_dir / f"{expected['video_id']}.mp4"
        raw = _snapshot_file(selected, name=f"source video {expected['video_id']}")
        if _sha256(raw) != expected["sha256"]:
            raise ScoringTruthEventHandoffError(
                f"source video SHA-256 mismatch: {expected['video_id']}"
            )
        video_raw[expected["media_path"]] = raw

    projection_sha = _sha256(_canonical_bytes(projection))
    if projection_sha != SOURCE_CONTRACT_SHA256:
        raise ScoringTruthEventHandoffError("safe source projection drifted")
    authority = {
        "kind": "canonical_scoring_truth_event_source_projection_hash",
        "source_contract_sha256": projection_sha,
        "manifest_created_at": projection["manifest_created_at"],
        "pack_version": projection["pack_version"],
        "source_status": projection["source_status"],
    }
    return {
        "source": source,
        "video_dir": selected_dir,
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "projection": projection,
        "source_authority": authority,
        "video_raw": MappingProxyType(video_raw),
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _asset_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name


def _server_source_path() -> Path:
    return _repo_root() / "scripts" / SERVER_LAUNCHER


def _load_server_module(path: Path, *, expected_sha: str) -> ModuleType:
    raw = _snapshot_file(path, name=SERVER_LAUNCHER)
    if _sha256(raw) != expected_sha:
        raise ScoringTruthEventHandoffError("standalone server authority drifted")
    module_name = f"_rallymate_event_handoff_server_{uuid.uuid4().hex}"
    module = ModuleType(module_name)
    module.__file__ = str(path)
    sys.modules[module_name] = module
    try:
        code = compile(raw, str(path), "exec")
        exec(code, module.__dict__)
    except Exception as exc:
        raise ScoringTruthEventHandoffError("standalone validator could not load") from exc
    finally:
        sys.modules.pop(module_name, None)
    return module


def _scope() -> dict[str, Any]:
    return {
        "event_codes": list(EVENT_CODES),
        "phase_keys_by_event": {
            key: list(value) for key, value in PHASE_KEYS_BY_EVENT.items()
        },
        "tasks": [
            {**item, "frame_rate": dict(item["frame_rate"])}
            for item in VIDEO_SPECS
        ],
    }


def _safety() -> dict[str, bool]:
    return {
        "annotation_execution_authorized": False,
        "external_protocol_receipt_verified": False,
        "mutation_enabled": False,
        "import_enabled": False,
        "export_enabled": False,
        "full_video_only": True,
        "machine_event_boundaries_embedded": False,
        "phase_values_embedded": False,
        "machine_keypoints_embedded": False,
        "grades_or_thresholds_supported": False,
        "source_paths_embedded": False,
        "nonselected_videos_embedded": False,
        "directory_listing_enabled": False,
        "exact_server_allowlist": True,
        "source_replay_optional_for_portable_validation": True,
        "hash_is_identity_signature_or_trusted_timestamp": False,
        "production_enabled": False,
        "maturity_promoted": False,
    }


def _bundle_id(authority: dict[str, Any], generated_at: str) -> str:
    digest = _sha256(
        _canonical_bytes(
            {
                "bundle_version": BUNDLE_VERSION,
                "generated_at": generated_at,
                "source_authority": authority,
                "scope": _scope(),
            }
        )
    )
    return f"m89-scoring-truth-event-{digest}"


def _content_root(artifacts: list[dict[str, Any]]) -> str:
    return _sha256(_canonical_bytes({"artifacts": artifacts}))


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode("utf-8")


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left_resolved = left.resolve(strict=False)
        right_resolved = right.resolve(strict=False)
        common = Path(os.path.commonpath((str(left_resolved), str(right_resolved))))
    except (OSError, ValueError):
        return False
    return common == left_resolved or common == right_resolved


def _write_new_file(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ScoringTruthEventHandoffError(f"could not write staging file: {path.name}") from exc


def validate_scoring_truth_event_handoff(
    bundle_dir: str | Path,
    *,
    source_pack_dir: str | Path | None = None,
    video_dir: str | Path | None = None,
) -> dict[str, Any]:
    bundle = Path(bundle_dir).expanduser()
    launcher = bundle / SERVER_LAUNCHER
    launcher_raw = _snapshot_file(launcher, name=SERVER_LAUNCHER)
    if _sha256(launcher_raw) != SERVER_SHA256:
        raise ScoringTruthEventHandoffError("standalone server authority drifted")
    server = _load_server_module(_server_source_path(), expected_sha=SERVER_SHA256)
    try:
        manifest, snapshots = server.validate_and_snapshot(bundle)
    except Exception as exc:
        if exc.__class__.__name__ == "BundleValidationError":
            raise ScoringTruthEventHandoffError(str(exc)) from exc
        raise

    source_replayed = False
    if source_pack_dir is not None:
        source = _source_snapshot(source_pack_dir, video_dir=video_dir)
        if manifest["source_authority"] != source["source_authority"]:
            raise ScoringTruthEventHandoffError("source authority replay mismatch")
        for task in VIDEO_SPECS:
            path = task["media_path"]
            if snapshots[path] != source["video_raw"][path]:
                raise ScoringTruthEventHandoffError(
                    f"source media replay mismatch: {task['video_id']}"
                )
        _scan_public_bytes(snapshots, source_manifest=source["manifest"])
        source_replayed = True
    elif video_dir is not None:
        raise ScoringTruthEventHandoffError(
            "video_dir requires source_pack_dir for source replay"
        )

    artifacts = {
        record["path"]: {
            **record,
            "raw": snapshots[record["path"]],
        }
        for record in manifest["artifacts"]
    }
    root = bundle.resolve(strict=True)
    manifest_raw = snapshots[MANIFEST_NAME]
    return {
        "bundle_dir": root,
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "manifest_sha256": _sha256(manifest_raw),
        "bundle_id": manifest["bundle_id"],
        "content_root_sha256": manifest["content_root_sha256"],
        "artifacts": artifacts,
        "source_replayed": source_replayed,
    }


def build_scoring_truth_event_handoff(
    source_pack_dir: str | Path,
    output_dir: str | Path,
    *,
    video_dir: str | Path | None = None,
) -> dict[str, Any]:
    output = Path(output_dir).expanduser()
    if output.exists() or output.is_symlink():
        raise ScoringTruthEventHandoffError("output directory must not already exist")
    source = _source_snapshot(source_pack_dir, video_dir=video_dir)
    output_resolved = output.resolve(strict=False)
    if _paths_overlap(output_resolved, source["source"]) or _paths_overlap(
        output_resolved, source["video_dir"]
    ):
        raise ScoringTruthEventHandoffError("output must not overlap source directories")

    server_raw = _snapshot_file(_server_source_path(), name=SERVER_LAUNCHER)
    if _sha256(server_raw) != SERVER_SHA256:
        raise ScoringTruthEventHandoffError("standalone server authority drifted")
    js_raw = _snapshot_file(_asset_path(WORKBENCH_JS), name=WORKBENCH_JS)
    css_raw = _snapshot_file(_asset_path(WORKBENCH_CSS), name=WORKBENCH_CSS)
    if _sha256(js_raw) != WORKBENCH_JS_SHA256:
        raise ScoringTruthEventHandoffError("workbench JS authority drifted")
    if _sha256(css_raw) != WORKBENCH_CSS_SHA256:
        raise ScoringTruthEventHandoffError("workbench CSS authority drifted")
    server = _load_server_module(_server_source_path(), expected_sha=SERVER_SHA256)

    generated_at = (
        datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    authority = source["source_authority"]
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "bundle_version": BUNDLE_VERSION,
        "bundle_id": _bundle_id(authority, generated_at),
        "generated_at": generated_at,
        "status": BUNDLE_STATUS,
        "source_authority": authority,
        "scope": _scope(),
        "entrypoint": "review.html",
        "server_launcher": SERVER_LAUNCHER,
        "artifacts": [],
        "content_root_sha256": "0" * 64,
        "safety": _safety(),
    }
    raw_by_path: dict[str, bytes] = {
        "OPERATOR_README.md": server._operator_readme(),
        "review.html": server._review_html(manifest),
        WORKBENCH_CSS: css_raw,
        WORKBENCH_JS: js_raw,
        SERVER_LAUNCHER: server_raw,
        **dict(source["video_raw"]),
    }
    if tuple(sorted(raw_by_path)) != PUBLIC_ARTIFACT_PATHS:
        raise ScoringTruthEventHandoffError("public artifact construction drifted")
    _scan_public_bytes(raw_by_path, source_manifest=source["manifest"])
    artifacts = [
        {"path": path, "bytes": len(raw_by_path[path]), "sha256": _sha256(raw_by_path[path])}
        for path in PUBLIC_ARTIFACT_PATHS
    ]
    manifest["artifacts"] = artifacts
    manifest["content_root_sha256"] = _content_root(artifacts)
    manifest_raw = _manifest_bytes(manifest)
    _scan_public_bytes(
        {**raw_by_path, MANIFEST_NAME: manifest_raw},
        source_manifest=source["manifest"],
    )

    parent = output.parent.resolve(strict=False)
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    if staging.exists() or staging.is_symlink():
        raise ScoringTruthEventHandoffError("unique staging path unexpectedly exists")
    try:
        staging.mkdir()
        for path in PUBLIC_ARTIFACT_PATHS:
            _write_new_file(staging / Path(*path.split("/")), raw_by_path[path])
        _write_new_file(staging / MANIFEST_NAME, manifest_raw)
        validate_scoring_truth_event_handoff(
            staging,
            source_pack_dir=source_pack_dir,
            video_dir=source["video_dir"],
        )
        if output.exists() or output.is_symlink():
            raise ScoringTruthEventHandoffError("output appeared during build")
        try:
            staging.rename(output)
        except OSError as exc:
            raise ScoringTruthEventHandoffError("atomic publish rename failed") from exc
    except BaseException:
        if staging.exists() and staging.is_dir() and not _is_link_like(staging):
            shutil.rmtree(staging)
        raise
    return manifest


__all__ = [
    "BUNDLE_STATUS",
    "BUNDLE_VERSION",
    "MANIFEST_NAME",
    "PUBLIC_ARTIFACT_PATHS",
    "ScoringTruthEventHandoffError",
    "build_scoring_truth_event_handoff",
    "validate_scoring_truth_event_handoff",
]
