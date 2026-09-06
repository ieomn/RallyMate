#!/usr/bin/env python3
"""Validate and serve a portable three-video event/phase technical handoff.

The file is Python-stdlib-only and is copied byte-for-byte into each bundle.
Validation freezes an in-memory snapshot before the loopback listener starts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import unquote_to_bytes, urlsplit


MANIFEST_NAME = "event-handoff-manifest.json"
ENTRYPOINT = "review.html"
LAUNCHER_NAME = "serve_scoring_truth_event_handoff.py"
SCHEMA_VERSION = "1.0.0"
BUNDLE_VERSION = "scoring-truth-event-handoff-v1.0.0"
BUNDLE_STATUS = "technical_handoff_verified_external_protocol_required"
BUNDLE_ID_PREFIX = "m89-scoring-truth-event-"
WORKBENCH_JS = "scoring-truth-event-workbench.js"
WORKBENCH_CSS = "scoring-truth-event-workbench.css"
EXPECTED_JS_SHA256 = "9A4EF6A6D8CD645F17EF0ADB0834EBEDCF883EEEFD3BD5F5090F0D56C6F0A7B4"
EXPECTED_CSS_SHA256 = "50099B99D1A0DF2581C4FC5A949F520C82DAA432987C65F137401E551AD005E7"
EXPECTED_SOURCE_CONTRACT_SHA256 = (
    "19422D7256E54A4E0BF56A84590A2F6CFDB29C12DC1E2650EDE7013B054D082E"
)

EVENT_CODES = ["FS01", "FS02", "FS09"]
PHASE_KEYS_BY_EVENT = {
    "FS01": [
        "preload_ms",
        "takeoff_proxy_ms",
        "landing_proxy_ms",
        "redistribution_ms",
        "initiation_ms",
    ],
    "FS02": [
        "direction_conversion_ms",
        "support_extension_proxy_ms",
        "lead_foot_motion_onset_proxy_ms",
        "first_step_slowdown_proxy_ms",
    ],
    "FS09": [
        "peak_speed_ms",
        "deceleration_peak_ms",
        "restabilization_onset_ms",
        "stable_control_onset_ms",
    ],
}
VIDEO_SPECS = [
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
]
EXPECTED_SCOPE = {
    "event_codes": EVENT_CODES,
    "phase_keys_by_event": PHASE_KEYS_BY_EVENT,
    "tasks": VIDEO_SPECS,
}
EXPECTED_SAFETY = {
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
EXPECTED_ARTIFACT_PATHS = [
    "OPERATOR_README.md",
    *[item["media_path"] for item in VIDEO_SPECS],
    ENTRYPOINT,
    WORKBENCH_CSS,
    WORKBENCH_JS,
    LAUNCHER_NAME,
]
MANIFEST_KEYS = {
    "schema_version",
    "bundle_version",
    "bundle_id",
    "generated_at",
    "status",
    "source_authority",
    "scope",
    "entrypoint",
    "server_launcher",
    "artifacts",
    "content_root_sha256",
    "safety",
}
SOURCE_AUTHORITY_KEYS = {
    "kind",
    "source_contract_sha256",
    "manifest_created_at",
    "pack_version",
    "source_status",
}
ARTIFACT_KEYS = {"path", "bytes", "sha256"}
SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
BUNDLE_ID_RE = re.compile(r"^m89-scoring-truth-event-[0-9A-F]{64}$")
RFC3339_UTC_RE = re.compile(
    r"^(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T"
    r"([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(?:\.\d+)?Z$"
)
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
BOOTSTRAP_OPEN = (
    '<script id="scoring-truth-event-bootstrap" type="application/json">'
)


class BundleValidationError(ValueError):
    """The directory is not the exact portable bundle declared by its manifest."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _emit_json({"ok": False, "error": message}, stream=sys.stderr)
        raise SystemExit(2)


def _emit_json(value: Mapping[str, Any], *, stream: Any = sys.stdout) -> None:
    print(
        json.dumps(
            dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ),
        file=stream,
        flush=True,
    )


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
        raise BundleValidationError("value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _reject_json_constant(value: str) -> None:
    raise BundleValidationError(f"invalid JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_object(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except BundleValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BundleValidationError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BundleValidationError(f"{name} must be a JSON object")
    return value


def _require_exact_keys(value: Any, expected: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise BundleValidationError(f"{name} keys are not exact")
    return value


def _require_sha256(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise BundleValidationError(f"{name} must be uppercase SHA-256")
    return value


def _timestamp(value: Any, *, name: str) -> datetime:
    if not isinstance(value, str) or RFC3339_UTC_RE.fullmatch(value) is None:
        raise BundleValidationError(f"{name} must be canonical RFC3339 UTC ending in Z")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleValidationError(f"{name} is not a real timestamp") from exc


def _safe_path(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "%" in value:
        raise BundleValidationError(f"{name} must be a portable relative path")
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise BundleValidationError(f"{name} must be a portable relative path")
    return value


def _content_root(artifacts: list[dict[str, Any]]) -> str:
    return _sha256(_canonical_bytes({"artifacts": artifacts}))


def _bundle_id(source_authority: dict[str, Any], generated_at: str) -> str:
    digest = _sha256(
        _canonical_bytes(
            {
                "bundle_version": BUNDLE_VERSION,
                "generated_at": generated_at,
                "source_authority": source_authority,
                "scope": EXPECTED_SCOPE,
            }
        )
    )
    return f"{BUNDLE_ID_PREFIX}{digest}"


def _operator_readme() -> bytes:
    return (
        "# HARD STOP — technical handoff only\n\n"
        "Do not begin annotator A work, annotator B work, reviewer C work, or any "
        "browser write until an independent external protocol receipt has been "
        "verified. The receipt must bind the raw `event-handoff-manifest.json` "
        "SHA-256, bundle ID, and source-contract SHA-256 for this exact bundle and "
        "must predate every human label.\n\n"
        "This directory contains exactly three full-length video tasks for FS01, "
        "FS02, and FS09 event/phase technical inspection. It contains no event "
        "boundaries, phase values, person tracks, keypoints, grades, thresholds, "
        "or production release. Playback, seeking, and one-frame stepping are "
        "available; all annotation writes, imports, and exports are disabled.\n\n"
        "## Technical verification\n\n"
        "Run from this directory with Python 3:\n\n"
        "```powershell\n"
        "python serve_scoring_truth_event_handoff.py --directory . --validate-only\n"
        "python serve_scoring_truth_event_handoff.py --directory . --bind 127.0.0.1 --port 8765\n"
        "```\n\n"
        "Open `http://127.0.0.1:8765/`. Validation checks the exact file tree, "
        "byte counts, SHA-256 values, content root, safety gates, and task/media "
        "bindings before serving an immutable in-memory snapshot. A passing hash "
        "check proves content consistency only; it does not prove a person's "
        "identity, independence, signature, trusted timestamp, protocol receipt, "
        "or authority to annotate.\n\n"
        "## Future controlled handoff\n\n"
        "Do not edit or unlock this technical bundle. After a valid external "
        "receipt exists, a separate controlled process must issue a new authorized "
        "bundle bound to that receipt. Keep A and B independent, and assign C as a "
        "distinct reviewer. This technical bundle cannot collect truth, evaluate "
        "accuracy, generate A–E, set thresholds, or promote F2/F3/F4.\n"
    ).encode("utf-8")


def _bootstrap(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "bundle_id": manifest["bundle_id"],
        "status": BUNDLE_STATUS,
        "source_contract_sha256": manifest["source_authority"]["source_contract_sha256"],
        "annotation_execution_authorized": False,
        "external_protocol_receipt_verified": False,
        "mutation_enabled": False,
        "import_enabled": False,
        "export_enabled": False,
        "event_codes": EVENT_CODES,
        "phase_keys_by_event": PHASE_KEYS_BY_EVENT,
        "tasks": VIDEO_SPECS,
    }


def _review_html(manifest: dict[str, Any]) -> bytes:
    data = json.dumps(
        _bootstrap(manifest), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).replace("</", "<\\/")
    phase_fields = "".join(
        f'<label>{phase}<input type="number" disabled data-mutation></label>'
        for phase in PHASE_KEYS_BY_EVENT.values()
        for phase in phase
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>RallyMate 三视频 event/phase 技术交接</title>
  <link rel="stylesheet" href="{WORKBENCH_CSS}">
  <script id="scoring-truth-event-bootstrap" type="application/json">{data}</script>
  <script src="{WORKBENCH_JS}" defer></script>
</head>
<body>
<main class="shell">
  <header class="hero">
    <h1>RallyMate 三视频全片 event/phase 技术交接</h1>
    <p class="gate"><strong>HARD STOP：</strong>外部协议回执尚未验证，本包不授权任何人工标注。</p>
    <p id="technical-status" class="status">正在校验技术门禁…</p>
    <p class="identity mono">bundle <span>{manifest['bundle_id']}</span><br>source contract SHA-256 <span>{manifest['source_authority']['source_contract_sha256']}</span></p>
  </header>

  <section class="panel" aria-labelledby="video-heading">
    <h2 id="video-heading">三段原始全片技术检查</h2>
    <div class="toolbar">
      <label>视频<select id="video-select"></select></label>
      <button id="play-pause" type="button">播放/暂停</button>
      <button type="button" data-step-frames="-1">−1 帧</button>
      <button type="button" data-step-frames="1">+1 帧</button>
    </div>
    <video id="workbench-video" controls preload="metadata"></video>
    <input id="seek-slider" type="range" min="0" max="0" value="0" step="0.001" aria-label="视频时间">
    <div class="meta-grid small">
      <div>task <span id="active-task-id" class="mono"></span></div>
      <div>video <span id="active-video-id" class="mono"></span></div>
      <div><span id="frame-rate" class="mono"></span> · <span id="time-readout" class="mono">0.000 s / 0.000 s</span></div>
    </div>
  </section>

  <section class="panel" aria-labelledby="form-heading">
    <h2 id="form-heading">event/phase 表单结构预览</h2>
    <p>以下控件只用于确认字段布局。外部协议回执缺失时，所有写入保持禁用。</p>
    <fieldset disabled data-mutation>
      <div class="phase-grid">
        <label>event_code<select disabled data-mutation><option>FS01</option><option>FS02</option><option>FS09</option></select></label>
        <label>event_start_ms<input type="number" disabled data-mutation></label>
        <label>event_end_ms<input type="number" disabled data-mutation></label>
        {phase_fields}
      </div>
    </fieldset>
    <div class="actions">
      <button id="save-event" type="button" disabled data-mutation>保存（锁定）</button>
      <label>导入（锁定）<input id="import-csv" type="file" disabled data-mutation></label>
      <button id="export-events" type="button" disabled data-mutation>导出（锁定）</button>
      <button id="mark-review-complete" type="button" disabled data-mutation>完成全片审阅（锁定）</button>
    </div>
  </section>
</main>
</body>
</html>
"""
    return html.encode("utf-8")


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _snapshot_file(path: Path, *, name: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise BundleValidationError(f"missing file: {name}") from exc
    if _is_link_like(path) or not path.is_file():
        raise BundleValidationError(f"{name} must be a regular file")
    try:
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise BundleValidationError(f"could not read file: {name}") from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(raw) != after.st_size:
        raise BundleValidationError(f"file changed during validation: {name}")
    return raw


def _actual_files(root: Path) -> set[str]:
    if _is_link_like(root) or not root.is_dir():
        raise BundleValidationError("bundle root must be a real directory")
    files: set[str] = set()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for dirname in dirnames:
            path = current_path / dirname
            relative = path.relative_to(root).as_posix()
            if _is_link_like(path):
                raise BundleValidationError(f"bundle contains a link: {relative}")
            if relative != "media":
                raise BundleValidationError(f"bundle contains extra directory: {relative}")
        for filename in filenames:
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            if _is_link_like(path) or not path.is_file():
                raise BundleValidationError(f"bundle contains a non-file: {relative}")
            files.add(relative)
    return files


def _validate_text_boundary(snapshots: Mapping[str, bytes]) -> None:
    markers = tuple(
        bytes.fromhex(value)
        for value in (
            "63616e646964617465",
            "70696c6f74",
            "6d6f64656c",
            "66756c6c2d74657374",
            "70726976617465",
        )
    )
    text_suffixes = {".css", ".html", ".js", ".json", ".md", ".py"}
    for path, raw in snapshots.items():
        if PurePosixPath(path).suffix.lower() not in text_suffixes:
            continue
        lowered = raw.lower()
        if any(marker in lowered for marker in markers):
            raise BundleValidationError(f"non-public source marker found: {path}")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BundleValidationError(f"text artifact is not UTF-8: {path}") from exc
        if re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", text):
            raise BundleValidationError(f"absolute source location found: {path}")


def validate_and_snapshot(directory: str | Path) -> tuple[dict[str, Any], Mapping[str, bytes]]:
    requested = Path(directory).expanduser()
    if _is_link_like(requested):
        raise BundleValidationError("bundle directory may not be a link")
    try:
        root = requested.resolve(strict=True)
    except OSError as exc:
        raise BundleValidationError("bundle directory does not exist") from exc
    if not root.is_dir():
        raise BundleValidationError("--directory must name a directory")

    manifest_raw = _snapshot_file(root / MANIFEST_NAME, name=MANIFEST_NAME)
    manifest = _load_json_object(manifest_raw, name=MANIFEST_NAME)
    _require_exact_keys(manifest, MANIFEST_KEYS, name="manifest")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise BundleValidationError("schema_version drifted")
    if manifest["bundle_version"] != BUNDLE_VERSION:
        raise BundleValidationError("bundle_version drifted")
    if manifest["status"] != BUNDLE_STATUS:
        raise BundleValidationError("technical-only status drifted")
    generated_at = _timestamp(manifest["generated_at"], name="generated_at")

    authority = _require_exact_keys(
        manifest["source_authority"], SOURCE_AUTHORITY_KEYS, name="source_authority"
    )
    if authority["kind"] != "canonical_scoring_truth_event_source_projection_hash":
        raise BundleValidationError("source authority kind drifted")
    source_contract_sha = _require_sha256(
        authority["source_contract_sha256"], name="source contract SHA-256"
    )
    if source_contract_sha != EXPECTED_SOURCE_CONTRACT_SHA256:
        raise BundleValidationError("source contract authority drifted")
    source_created_at = _timestamp(
        authority["manifest_created_at"], name="source manifest created_at"
    )
    if source_created_at > generated_at:
        raise BundleValidationError("bundle predates its source authority")
    if authority["pack_version"] != "scoring-truth-pack-v0.3.0":
        raise BundleValidationError("source pack version drifted")
    if authority["source_status"] != "annotation_required_no_truth_or_thresholds_generated":
        raise BundleValidationError("source status drifted")

    if _canonical_bytes(manifest["scope"]) != _canonical_bytes(EXPECTED_SCOPE):
        raise BundleValidationError("three-video event/phase scope drifted")
    if manifest["safety"] != EXPECTED_SAFETY:
        raise BundleValidationError("technical-only safety gates drifted")
    if manifest["entrypoint"] != ENTRYPOINT:
        raise BundleValidationError("entrypoint drifted")
    if manifest["server_launcher"] != LAUNCHER_NAME:
        raise BundleValidationError("server launcher drifted")

    bundle_id = manifest["bundle_id"]
    if not isinstance(bundle_id, str) or BUNDLE_ID_RE.fullmatch(bundle_id) is None:
        raise BundleValidationError("bundle_id format drifted")
    if bundle_id != _bundle_id(authority, manifest["generated_at"]):
        raise BundleValidationError("bundle_id does not bind source, scope, and time")

    artifact_values = manifest["artifacts"]
    if not isinstance(artifact_values, list):
        raise BundleValidationError("artifacts must be an array")
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(artifact_values):
        record = _require_exact_keys(item, ARTIFACT_KEYS, name=f"artifacts[{index}]")
        path = _safe_path(record["path"], name=f"artifacts[{index}].path")
        byte_count = record["bytes"]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count <= 0:
            raise BundleValidationError(f"artifacts[{index}].bytes must be positive")
        digest = _require_sha256(record["sha256"], name=f"artifacts[{index}].sha256")
        if path in seen:
            raise BundleValidationError(f"duplicate artifact path: {path}")
        seen.add(path)
        artifacts.append({"path": path, "bytes": byte_count, "sha256": digest})
    if [item["path"] for item in artifacts] != EXPECTED_ARTIFACT_PATHS:
        raise BundleValidationError("artifact allowlist is not exact")
    if _content_root(artifacts) != _require_sha256(
        manifest["content_root_sha256"], name="content_root_sha256"
    ):
        raise BundleValidationError("content root mismatch")

    expected_files = set(EXPECTED_ARTIFACT_PATHS) | {MANIFEST_NAME}
    actual_files = _actual_files(root)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise BundleValidationError(
            f"bundle file set mismatch (missing={missing}, extra={extra})"
        )

    snapshots: dict[str, bytes] = {MANIFEST_NAME: manifest_raw}
    for record in artifacts:
        path = record["path"]
        raw = _snapshot_file(root / Path(*path.split("/")), name=path)
        if len(raw) != record["bytes"] or _sha256(raw) != record["sha256"]:
            raise BundleValidationError(f"artifact bytes/SHA mismatch: {path}")
        snapshots[path] = raw

    records = {item["path"]: item for item in artifacts}
    if records[WORKBENCH_JS]["sha256"] != EXPECTED_JS_SHA256:
        raise BundleValidationError("workbench JS authority drifted")
    if records[WORKBENCH_CSS]["sha256"] != EXPECTED_CSS_SHA256:
        raise BundleValidationError("workbench CSS authority drifted")
    for task in VIDEO_SPECS:
        if records[task["media_path"]]["sha256"] != task["sha256"]:
            raise BundleValidationError(f"video authority drifted: {task['video_id']}")
    if snapshots["OPERATOR_README.md"] != _operator_readme():
        raise BundleValidationError("operator README contract drifted")
    if snapshots[ENTRYPOINT] != _review_html(manifest):
        raise BundleValidationError("review HTML/bootstrap contract drifted")
    _validate_text_boundary(snapshots)

    return manifest, MappingProxyType(snapshots)


def _request_path(raw_target: str) -> str | None:
    parts = urlsplit(raw_target)
    if parts.scheme or parts.netloc:
        return None
    try:
        decoded = unquote_to_bytes(parts.path).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    if "%" in decoded or "\\" in decoded or "\x00" in decoded:
        return None
    if decoded == "/":
        return ENTRYPOINT
    if not decoded.startswith("/") or decoded.startswith("//"):
        return None
    try:
        return _safe_path(decoded[1:], name="request path")
    except BundleValidationError:
        return None


def _content_type(path: str) -> str:
    explicit = {
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".mp4": "video/mp4",
        ".py": "text/x-python; charset=utf-8",
    }
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in explicit:
        return explicit[suffix]
    guessed, _encoding = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _parse_range(value: str, size: int) -> tuple[int, int]:
    match = RANGE_RE.fullmatch(value.strip())
    if match is None or size <= 0:
        raise ValueError("invalid range")
    start_text, end_text = match.groups()
    if not start_text:
        if not end_text:
            raise ValueError("empty range")
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("invalid suffix range")
        return max(0, size - suffix), size - 1
    start = int(start_text)
    if start >= size:
        raise ValueError("range begins beyond resource")
    end = size - 1 if not end_text else min(size - 1, int(end_text))
    if end < start:
        raise ValueError("range end precedes start")
    return start, end


def make_request_handler(
    snapshots: Mapping[str, bytes], *, entrypoint: str = ENTRYPOINT
) -> type[BaseHTTPRequestHandler]:
    class EventHandoffRequestHandler(BaseHTTPRequestHandler):
        server_version = "RallyMateEventHandoff/1.0"
        sys_version = ""

        def _headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-RallyMate-Event-Handoff-Server", "snapshot-v1")

        def _error(
            self,
            status: HTTPStatus,
            code: str,
            *,
            head_only: bool,
            content_range: str | None = None,
        ) -> None:
            body = _canonical_bytes({"error": code})
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if content_range is not None:
                self.send_header("Content-Range", content_range)
                self.send_header("Accept-Ranges", "bytes")
            self._headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _host_allowed(self) -> bool:
            port = int(self.server.server_address[1])
            return self.headers.get_all("Host", failobj=[]) == [f"127.0.0.1:{port}"]

        def _serve(self, *, head_only: bool) -> None:
            if not self._host_allowed():
                self._error(
                    HTTPStatus.MISDIRECTED_REQUEST,
                    "host_not_allowed",
                    head_only=head_only,
                )
                return
            path = _request_path(self.path)
            if path is None or path not in snapshots:
                self._error(HTTPStatus.NOT_FOUND, "not_found", head_only=head_only)
                return
            raw = snapshots[path]
            size = len(raw)
            range_header = self.headers.get("Range")
            if range_header is None:
                status = HTTPStatus.OK
                start, end = 0, size - 1
            else:
                try:
                    start, end = _parse_range(range_header, size)
                except (TypeError, ValueError, OverflowError):
                    self._error(
                        HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                        "range_not_satisfiable",
                        head_only=head_only,
                        content_range=f"bytes */{size}",
                    )
                    return
                status = HTTPStatus.PARTIAL_CONTENT
            body = raw if status == HTTPStatus.OK else raw[start : end + 1]
            self.send_response(status)
            self.send_header("Content-Type", _content_type(path))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self._headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._serve(head_only=False)

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(head_only=True)

        def _method_not_allowed(self) -> None:
            if not self._host_allowed():
                self._error(
                    HTTPStatus.MISDIRECTED_REQUEST,
                    "host_not_allowed",
                    head_only=False,
                )
                return
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self._headers()
            self.end_headers()

        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_OPTIONS = _method_not_allowed
        do_TRACE = _method_not_allowed
        do_CONNECT = _method_not_allowed

    return EventHandoffRequestHandler


class SnapshotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        bind_and_activate: bool = True,
    ) -> None:
        if server_address[0] != "127.0.0.1":
            raise BundleValidationError("snapshot server requires exact IPv4 loopback")
        super().__init__(server_address, request_handler_class, bind_and_activate)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = JsonArgumentParser(
        description="Validate and serve the three-video event/phase technical handoff"
    )
    parser.add_argument("--directory", type=Path, default=Path("."))
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    if args.bind != "127.0.0.1":
        parser.error("--bind must be exactly 127.0.0.1")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    return args


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest, snapshots = validate_and_snapshot(args.directory)
    result = {
        "ok": True,
        "status": "valid",
        "bundle_status": manifest["status"],
        "manifest_sha256": _sha256(snapshots[MANIFEST_NAME]),
        "bundle_id": manifest["bundle_id"],
        "source_contract_sha256": manifest["source_authority"]["source_contract_sha256"],
        "content_root_sha256": manifest["content_root_sha256"],
        "file_count": len(snapshots),
    }
    if args.validate_only:
        _emit_json(result)
        return 0
    handler = make_request_handler(snapshots)
    server = SnapshotHTTPServer((args.bind, args.port), handler)
    host, port = server.server_address[:2]
    _emit_json({**result, "status": "serving", "url": f"http://{host}:{port}/"})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except (BundleValidationError, OSError) as exc:
        _emit_json({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
