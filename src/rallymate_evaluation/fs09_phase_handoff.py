from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from rallymate_evaluation.fs09_phase_truth import (
    FS09PhaseTruthError,
    _verify_pack_sources,
    validate_fs09_phase_analysis_plan,
)


BUNDLE_SCHEMA_VERSION = "1.1.0"
BUNDLE_VERSION = "fs09-phase-blind-handoff-v1.1.0"
BUNDLE_STATUS = "technical_handoff_verified_external_protocol_required"
MANIFEST_NAME = "handoff-manifest.json"
SERVER_LAUNCHER = "serve_fs09_phase_blind_handoff.py"
TASK_IDS = (
    "m77:8d7754d0de6d315674013d5b69a0b6ba:fs09-phase-001",
    "m77:8d7754d0de6d315674013d5b69a0b6ba:fs09-phase-002",
)
MEDIA_PATHS = (
    "media/task-001-browser.mp4",
    "media/task-002-browser.mp4",
)
PUBLIC_ARTIFACT_PATHS = (
    "OPERATOR_README.md",
    "analysis-plan.json",
    "fs09-phase-truth-workbench.css",
    "fs09-phase-truth-workbench.js",
    *MEDIA_PATHS,
    "review.html",
    SERVER_LAUNCHER,
)
EXPECTED_DIRECTORIES = {"media"}

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_BUNDLE_ID_RE = re.compile(r"^m88-fs09-blind-[A-F0-9]{64}$")
_RFC3339_UTC_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T"
    r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$"
)
_BOOTSTRAP_OPEN = (
    '<script id="fs09-phase-truth-bootstrap" type="application/json">'
)


class FS09PhaseBlindHandoffError(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _require_sha256(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise FS09PhaseBlindHandoffError(f"{name} must be uppercase SHA-256")
    return value


def _require_exact_keys(value: Any, expected: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise FS09PhaseBlindHandoffError(f"{name} keys are not exact")
    return value


def _reject_json_constant(value: str) -> None:
    raise FS09PhaseBlindHandoffError(f"invalid JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FS09PhaseBlindHandoffError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _timestamp(value: Any, *, name: str) -> datetime:
    if not isinstance(value, str) or _RFC3339_UTC_RE.fullmatch(value) is None:
        raise FS09PhaseBlindHandoffError(
            f"{name} must be a canonical RFC3339 UTC timestamp ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FS09PhaseBlindHandoffError(
            f"{name} must be a canonical RFC3339 UTC timestamp ending in Z"
        ) from exc
    if parsed.tzinfo is None:
        raise FS09PhaseBlindHandoffError(f"{name} must include a timezone")
    return parsed


def _safe_relative_path(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise FS09PhaseBlindHandoffError(f"{name} must be a portable relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FS09PhaseBlindHandoffError(f"{name} must be a portable relative path")
    if path.as_posix() != value:
        raise FS09PhaseBlindHandoffError(f"{name} must be canonical POSIX path text")
    return value


def _content_root(artifacts: list[dict[str, Any]]) -> str:
    return _sha256_bytes(_canonical_bytes({"artifacts": artifacts}))


def _bundle_identity(
    source_authority: dict[str, Any], task_ids: list[str], generated_at: str
) -> str:
    digest = _sha256_bytes(
        _canonical_bytes(
            {
                "bundle_version": BUNDLE_VERSION,
                "generated_at": generated_at,
                "source_authority": source_authority,
                "task_ids": task_ids,
            }
        )
    )
    return f"m88-fs09-blind-{digest}"


def _anonymous_operator_readme() -> bytes:
    text = f"""# HARD STOP — technical handoff only

Do not begin annotator A work, annotator B work, reviewer C work, or the first browser save until an independent external protocol receipt or trust anchor has been verified, binds the raw `handoff-manifest.json` SHA-256, bundle ID, and analysis-plan SHA-256 for this exact handoff, and is shown to predate every label. This bundle is technical-only: its manifest records `annotation_execution_authorized=false` and `external_protocol_receipt_verified=false`.

This directory is the public technical handoff for two FS09-M05 annotation tasks. Exact sealed candidate boundary and phase values are not embedded in this bundle; this is not a claim of global secrecy. A pre-frozen candidate-selected coarse anchor selects the target action and intentionally reveals coarse candidate-selected localization. Neither the coarse anchors nor any candidate-derived hashes—including the candidate-contract hash and sealed-candidates-artifact hash—are confidential against known-source or dictionary lookup. Those SHA-256 values are content commitments only; they are not candidate data, a person's identity, a signature, or a trusted timestamp. This bundle does not authorize annotation and does not contain the private candidate file, Pose records, compiled truth, grades, thresholds, or a production release.

## Technical verification only

Use Python 3 from this directory:

```powershell
python {SERVER_LAUNCHER} --directory . --validate-only
python {SERVER_LAUNCHER} --directory . --bind 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/` only for technical verification. The launcher validates the exact public file tree and SHA-256 values before serving an in-memory snapshot. It does not expose directory listings or files outside the allowlist. A successful hash check proves content consistency, not a person's identity, independence, signature, trusted timestamp, protocol receipt, or authorization to annotate. Its validation JSON reports the raw manifest SHA-256, bundle ID, analysis-plan SHA-256, and technical-only bundle status for comparison with the future independent receipt.

## Future role protocol — not executable by this bundle

Even if an external receipt is obtained later, do not edit, unlock, or use this technical bundle for A/B/C decisions. A future controlled tool must generate a separate, verifiable authorized contract/version bound to that receipt; M88 does not provide that receipt-to-authorized-handoff path. The steps below describe that future workflow only.

1. The study operator gives A, B, and C only separate verified copies of the authorized public bundle on isolated devices or browser profiles. They must not receive the repository, reports, runtime event files, other annotation packs, model outputs, or the private authority pack.
2. The study operator assigns three different pseudonymous IDs to annotator A, annotator B, and reviewer C. Do not use a real name, email address, account name, phone number, or organization identifier in an ID, note, reason, or filename.
3. A and B work only in independent-annotation mode and must not view each other's CSV before export.
4. C uses a fresh browser profile, switches to independent-adjudication mode, imports exactly the two complete A/B annotation CSV files, and makes an independent decision.
5. The visible player video is personal research material. Do not upload, forward, screen-record, or retain it outside the authorized study workflow. The clips contain no audio stream, but visible-person privacy still applies.

## Return

Return only the two annotation CSV files and the one adjudication CSV through the operator-approved channel. Do not return a modified copy of this directory and do not manually merge CSV files. Keep the original browser downloads unchanged. After the operator confirms receipt, use “清空浏览器草稿”, close the private browser profile, and follow the operator's retention or deletion policy.

The central operator performs intake and evaluation against a separate private canonical evidence pack. This public handoff cannot verify the external protocol, authorize annotation, compile truth, evaluate a model, generate A–E, or change runtime, F2, F3, F4, or production state.
"""
    return text.encode("utf-8")


def _server_source_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / SERVER_LAUNCHER


def _parse_bootstrap(html: str) -> tuple[int, int, dict[str, Any]]:
    start = html.find(_BOOTSTRAP_OPEN)
    if start < 0:
        raise FS09PhaseBlindHandoffError("canonical workbench bootstrap is missing")
    data_start = start + len(_BOOTSTRAP_OPEN)
    data_end = html.find("</script>", data_start)
    if data_end < 0 or html.find(_BOOTSTRAP_OPEN, data_start) >= 0:
        raise FS09PhaseBlindHandoffError("canonical workbench bootstrap is ambiguous")
    try:
        value = json.loads(html[data_start:data_end].replace("<\\/", "</"))
    except json.JSONDecodeError as exc:
        raise FS09PhaseBlindHandoffError("canonical workbench bootstrap is invalid") from exc
    if not isinstance(value, dict):
        raise FS09PhaseBlindHandoffError("canonical workbench bootstrap must be an object")
    return data_start, data_end, value


def _public_review_html(
    canonical_raw: bytes,
    *,
    bundle_id: str,
    analysis_plan_sha256: str,
) -> bytes:
    try:
        html = canonical_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FS09PhaseBlindHandoffError("canonical review HTML is not UTF-8") from exc
    data_start, data_end, bootstrap = _parse_bootstrap(html)
    if (
        bootstrap.get("handoff_bundle_id") is not None
        or bootstrap.get("analysis_plan_sha256") is not None
        or bootstrap.get("annotation_execution_authorized") is not False
        or bootstrap.get("external_protocol_receipt_verified") is not False
    ):
        raise FS09PhaseBlindHandoffError(
            "canonical review HTML handoff/plan/protocol gate drifted"
        )
    tasks = bootstrap.get("tasks")
    if (
        not isinstance(tasks, list)
        or [str(item.get("task_id", "")) for item in tasks] != list(TASK_IDS)
        or bootstrap.get("review_clips")
        != dict(zip(TASK_IDS, MEDIA_PATHS, strict=True))
    ):
        raise FS09PhaseBlindHandoffError("canonical workbench task/media binding drifted")
    forbidden_bootstrap_keys = {
        "candidates",
        "candidate_events",
        "candidate_phases",
        "pose_records",
        "pose_overlay",
    }
    if forbidden_bootstrap_keys.intersection(bootstrap):
        raise FS09PhaseBlindHandoffError("canonical workbench contains blinded data")
    bootstrap["handoff_bundle_id"] = bundle_id
    bootstrap["analysis_plan_sha256"] = analysis_plan_sha256
    encoded = json.dumps(bootstrap, ensure_ascii=False, allow_nan=False).replace(
        "</", "<\\/"
    )
    html = html[:data_start] + encoded + html[data_end:]
    identity = (
        '<p id="blind-handoff-identity"><strong>Portable blind handoff</strong> · '
        f'<code>{bundle_id}</code> · analysis plan SHA-256 '
        f'<code>{analysis_plan_sha256}</code></p>'
    )
    heading = "<h1>M77 FS09 事件与稳定控制阶段盲标</h1>"
    if html.count(heading) != 1:
        raise FS09PhaseBlindHandoffError("canonical workbench heading drifted")
    html = html.replace(heading, heading + identity, 1)
    return html.encode("utf-8")


def _recover_canonical_review_html(
    public_raw: bytes,
    *,
    bundle_id: str,
    analysis_plan_sha256: str,
) -> bytes:
    try:
        html = public_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FS09PhaseBlindHandoffError("public review HTML is not UTF-8") from exc
    data_start, data_end, bootstrap = _parse_bootstrap(html)
    if (
        bootstrap.get("handoff_bundle_id") != bundle_id
        or bootstrap.get("analysis_plan_sha256") != analysis_plan_sha256
    ):
        raise FS09PhaseBlindHandoffError("public review identity binding drifted")
    bootstrap["handoff_bundle_id"] = None
    bootstrap["analysis_plan_sha256"] = None
    encoded = json.dumps(bootstrap, ensure_ascii=False, allow_nan=False).replace(
        "</", "<\\/"
    )
    html = html[:data_start] + encoded + html[data_end:]
    identity = (
        '<p id="blind-handoff-identity"><strong>Portable blind handoff</strong> · '
        f'<code>{bundle_id}</code> · analysis plan SHA-256 '
        f'<code>{analysis_plan_sha256}</code></p>'
    )
    if html.count(identity) != 1:
        raise FS09PhaseBlindHandoffError("public review transformation is not reversible")
    html = html.replace(identity, "", 1)
    return html.encode("utf-8")


def _read_json_object(path: Path, *, name: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except FS09PhaseBlindHandoffError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseBlindHandoffError(f"{name} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise FS09PhaseBlindHandoffError(f"{name} must be a JSON object")
    return value, raw, _sha256_bytes(raw)


def _plan_bindings(plan: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    try:
        study = plan["study_binding"]
        if study["blind_handoff_version"] != BUNDLE_VERSION:
            raise KeyError("blind_handoff_version")
        handoff = study["blind_handoff_artifact_sha256"]
        workbench = study["workbench_artifact_sha256"]
        sources = study["source_artifact_sha256"]
    except (KeyError, TypeError) as exc:
        raise FS09PhaseBlindHandoffError(
            "analysis plan does not bind the M88 blind handoff"
        ) from exc
    _require_exact_keys(
        handoff,
        {"OPERATOR_README.md", SERVER_LAUNCHER},
        name="analysis plan blind handoff artifacts",
    )
    _require_exact_keys(
        workbench,
        {
            "fs09-phase-truth-workbench.js",
            "fs09-phase-truth-workbench.css",
            "review.html",
            "OPERATOR_README.md",
        },
        name="analysis plan workbench artifacts",
    )
    for name, value in {**handoff, **workbench, **sources}.items():
        _require_sha256(value, name=f"analysis plan artifact {name}")
    return handoff, workbench, sources


def _source_snapshot(
    source_pack_dir: str | Path, *, generated_at: str
) -> dict[str, Any]:
    source = Path(source_pack_dir).resolve()
    manifest, manifest_raw, manifest_sha = _read_json_object(
        source / "manifest.json", name="source canonical manifest"
    )
    try:
        _verify_pack_sources(source, manifest)
    except (FS09PhaseTruthError, OSError, ValueError) as exc:
        raise FS09PhaseBlindHandoffError(
            f"source canonical pack failed verification: {exc}"
        ) from exc
    plan, plan_raw, plan_sha = _read_json_object(
        source / "analysis-plan.json", name="source analysis plan"
    )
    try:
        validate_fs09_phase_analysis_plan(plan)
    except (FS09PhaseTruthError, ValueError) as exc:
        raise FS09PhaseBlindHandoffError("source analysis plan is invalid") from exc
    handoff_hashes, workbench_hashes, source_hashes = _plan_bindings(plan)
    task_rows: list[dict[str, Any]] = []
    try:
        for line in (source / "tasks.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("task row")
                task_rows.append(row)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise FS09PhaseBlindHandoffError("source task contract is invalid") from exc
    if [str(row.get("task_id", "")) for row in task_rows] != list(TASK_IDS):
        raise FS09PhaseBlindHandoffError("source task membership drifted")

    server_path = _server_source_path()
    named_paths = {
        "analysis-plan.json": source / "analysis-plan.json",
        "review.html": source / "review.html",
        "fs09-phase-truth-workbench.js": source
        / "fs09-phase-truth-workbench.js",
        "fs09-phase-truth-workbench.css": source
        / "fs09-phase-truth-workbench.css",
        MEDIA_PATHS[0]: source / MEDIA_PATHS[0],
        MEDIA_PATHS[1]: source / MEDIA_PATHS[1],
        SERVER_LAUNCHER: server_path,
    }
    raw_by_name: dict[str, bytes] = {}
    try:
        raw_by_name = {name: path.read_bytes() for name, path in named_paths.items()}
    except OSError as exc:
        raise FS09PhaseBlindHandoffError("handoff source artifact is missing") from exc
    raw_by_name["OPERATOR_README.md"] = _anonymous_operator_readme()

    expected_hashes = {
        "fs09-phase-truth-workbench.js": workbench_hashes[
            "fs09-phase-truth-workbench.js"
        ],
        "fs09-phase-truth-workbench.css": workbench_hashes[
            "fs09-phase-truth-workbench.css"
        ],
        "OPERATOR_README.md": handoff_hashes["OPERATOR_README.md"],
        SERVER_LAUNCHER: handoff_hashes[SERVER_LAUNCHER],
        MEDIA_PATHS[0]: source_hashes["task_001_review_clip"],
        MEDIA_PATHS[1]: source_hashes["task_002_review_clip"],
    }
    for name, expected in expected_hashes.items():
        if _sha256_bytes(raw_by_name[name]) != expected:
            raise FS09PhaseBlindHandoffError(
                f"analysis plan blind handoff SHA mismatch: {name}"
            )
    if _sha256_bytes(raw_by_name["analysis-plan.json"]) != plan_sha:
        raise FS09PhaseBlindHandoffError("analysis plan snapshot drifted")

    source_authority = {
        "kind": "private_canonical_pack_content_hash",
        "manifest_sha256": manifest_sha,
        "manifest_generated_at": manifest["generated_at"],
        "analysis_plan_sha256": plan_sha,
        "plan_version": plan["plan_version"],
        "pack_version": manifest["pack_version"],
        "task_contract_sha256": manifest["task_contract_sha256"],
        "candidate_contract_sha256": manifest["candidate_contract_sha256"],
    }
    bundle_id = _bundle_identity(source_authority, list(TASK_IDS), generated_at)
    raw_by_name["review.html"] = _public_review_html(
        raw_by_name["review.html"],
        bundle_id=bundle_id,
        analysis_plan_sha256=plan_sha,
    )
    return {
        "source": source,
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "plan": plan,
        "plan_raw": plan_raw,
        "source_authority": source_authority,
        "bundle_id": bundle_id,
        "raw_by_name": raw_by_name,
        "named_paths": named_paths,
    }


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _directory_tree(root: Path) -> tuple[set[str], set[str]]:
    if not root.is_dir() or _is_link_like(root):
        raise FS09PhaseBlindHandoffError("blind handoff root must be a real directory")
    files: set[str] = set()
    directories: set[str] = set()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for dirname in dirnames:
            path = current_path / dirname
            if _is_link_like(path):
                raise FS09PhaseBlindHandoffError(
                    "blind handoff cannot contain symlinks or junctions"
                )
            directories.add(path.relative_to(root).as_posix())
        for filename in filenames:
            path = current_path / filename
            if _is_link_like(path):
                raise FS09PhaseBlindHandoffError(
                    "blind handoff cannot contain symlinks or junctions"
                )
            files.add(path.relative_to(root).as_posix())
    return files, directories


def validate_fs09_phase_blind_handoff(
    bundle_dir: str | Path,
    *,
    source_pack_dir: str | Path | None = None,
) -> dict[str, Any]:
    requested_bundle = Path(bundle_dir).expanduser()
    if _is_link_like(requested_bundle):
        raise FS09PhaseBlindHandoffError(
            "blind handoff root cannot be a symlink or junction"
        )
    bundle = requested_bundle.resolve()
    manifest, manifest_raw, _manifest_sha = _read_json_object(
        bundle / MANIFEST_NAME, name="blind handoff manifest"
    )
    _require_exact_keys(
        manifest,
        {
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
        },
        name="blind handoff manifest",
    )
    if (
        manifest["schema_version"] != BUNDLE_SCHEMA_VERSION
        or manifest["bundle_version"] != BUNDLE_VERSION
        or manifest["status"] != BUNDLE_STATUS
        or manifest["entrypoint"] != "review.html"
        or manifest["server_launcher"] != SERVER_LAUNCHER
        or not isinstance(manifest["bundle_id"], str)
        or _BUNDLE_ID_RE.fullmatch(manifest["bundle_id"]) is None
    ):
        raise FS09PhaseBlindHandoffError("unsupported blind handoff contract")
    generated_at = _timestamp(manifest["generated_at"], name="generated_at")
    authority = _require_exact_keys(
        manifest["source_authority"],
        {
            "kind",
            "manifest_sha256",
            "manifest_generated_at",
            "analysis_plan_sha256",
            "plan_version",
            "pack_version",
            "task_contract_sha256",
            "candidate_contract_sha256",
        },
        name="blind handoff source authority",
    )
    if authority["kind"] != "private_canonical_pack_content_hash":
        raise FS09PhaseBlindHandoffError("blind handoff authority kind is invalid")
    for name in (
        "manifest_sha256",
        "analysis_plan_sha256",
        "task_contract_sha256",
        "candidate_contract_sha256",
    ):
        _require_sha256(authority[name], name=f"source authority {name}")
    manifest_time = _timestamp(
        authority["manifest_generated_at"], name="source manifest generated_at"
    )
    if not isinstance(authority["plan_version"], str) or not authority["plan_version"]:
        raise FS09PhaseBlindHandoffError("source authority plan version is invalid")
    if not isinstance(authority["pack_version"], str) or not authority["pack_version"]:
        raise FS09PhaseBlindHandoffError("source authority pack version is invalid")

    scope = _require_exact_keys(
        manifest["scope"], {"task_ids", "media_paths"}, name="blind handoff scope"
    )
    if scope != {"task_ids": list(TASK_IDS), "media_paths": list(MEDIA_PATHS)}:
        raise FS09PhaseBlindHandoffError("blind handoff scope drifted")
    if manifest["bundle_id"] != _bundle_identity(
        authority, list(TASK_IDS), manifest["generated_at"]
    ):
        raise FS09PhaseBlindHandoffError("blind handoff bundle ID drifted")

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(PUBLIC_ARTIFACT_PATHS):
        raise FS09PhaseBlindHandoffError("blind handoff artifact membership is incomplete")
    expected_paths = set(PUBLIC_ARTIFACT_PATHS)
    seen: set[str] = set()
    for index, item in enumerate(artifacts, start=1):
        record = _require_exact_keys(
            item, {"path", "bytes", "sha256"}, name=f"blind handoff artifact {index}"
        )
        path = _safe_relative_path(record["path"], name=f"artifact path {index}")
        if path in seen:
            raise FS09PhaseBlindHandoffError("blind handoff artifact paths must be unique")
        seen.add(path)
        if (
            not isinstance(record["bytes"], int)
            or isinstance(record["bytes"], bool)
            or record["bytes"] <= 0
        ):
            raise FS09PhaseBlindHandoffError(f"artifact byte count is invalid: {path}")
        _require_sha256(record["sha256"], name=f"artifact SHA: {path}")
    if seen != expected_paths or [item["path"] for item in artifacts] != sorted(expected_paths):
        raise FS09PhaseBlindHandoffError("blind handoff artifact membership/order drifted")
    content_root = _require_sha256(
        manifest["content_root_sha256"], name="blind handoff content root"
    )
    if content_root != _content_root(artifacts):
        raise FS09PhaseBlindHandoffError("blind handoff content root mismatch")

    expected_safety = {
        "relative_artifact_paths_only": True,
        "canonical_manifest_included": False,
        "tasks_file_included": False,
        "sealed_candidates_included": False,
        "compiled_truth_included": False,
        "absolute_paths_included": False,
        "candidate_boundaries_embedded_in_annotation_ui": False,
        "candidate_phases_embedded_in_annotation_ui": False,
        "coarse_target_selection_anchor_included": True,
        "target_selection_anchor_is_candidate_boundary_or_phase": False,
        "exact_candidate_boundaries_derivable_from_public_window": False,
        "exact_candidate_boundaries_or_phases_embedded_in_bundle": False,
        "isolated_public_bundle_only_required": True,
        "coarse_anchor_is_confidential": False,
        "candidate_contract_hash_is_confidential": False,
        "sealed_candidates_artifact_hash_is_confidential": False,
        "candidate_derived_hashes_are_confidential": False,
        "candidate_derived_hashes_are_content_commitments_only": True,
        "pose_overlay_embedded_in_annotation_ui": False,
        "directory_listing_enabled": False,
        "exact_server_allowlist": True,
        "source_pack_required_for_authority_replay": True,
        "hash_is_identity_or_signature": False,
        "annotation_execution_authorized": False,
        "external_protocol_receipt_verified": False,
        "production_enabled": False,
        "grade_generated": False,
        "threshold_generated": False,
        "maturity_promoted": False,
    }
    safety = _require_exact_keys(
        manifest["safety"], set(expected_safety), name="blind handoff safety"
    )
    if safety != expected_safety:
        raise FS09PhaseBlindHandoffError("unsafe blind handoff declaration")

    files, directories = _directory_tree(bundle)
    if files != expected_paths | {MANIFEST_NAME} or directories != EXPECTED_DIRECTORIES:
        raise FS09PhaseBlindHandoffError("blind handoff file tree is not exact")
    raw_by_name: dict[str, bytes] = {}
    for record in artifacts:
        path = bundle / record["path"]
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise FS09PhaseBlindHandoffError(
                f"blind handoff artifact is unreadable: {record['path']}"
            ) from exc
        if len(raw) != record["bytes"] or _sha256_bytes(raw) != record["sha256"]:
            raise FS09PhaseBlindHandoffError(
                f"blind handoff artifact mismatch: {record['path']}"
            )
        raw_by_name[record["path"]] = raw

    try:
        bundled_plan = json.loads(raw_by_name["analysis-plan.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseBlindHandoffError("bundled analysis plan is invalid") from exc
    if not isinstance(bundled_plan, dict):
        raise FS09PhaseBlindHandoffError("bundled analysis plan must be an object")
    try:
        validate_fs09_phase_analysis_plan(bundled_plan)
    except (FS09PhaseTruthError, ValueError) as exc:
        raise FS09PhaseBlindHandoffError("bundled analysis plan is invalid") from exc
    if (
        _sha256_bytes(raw_by_name["analysis-plan.json"])
        != authority["analysis_plan_sha256"]
        or bundled_plan.get("plan_version") != authority["plan_version"]
    ):
        raise FS09PhaseBlindHandoffError("bundled analysis plan authority drifted")
    study_binding = bundled_plan["study_binding"]
    if (
        authority["pack_version"] != study_binding["pack_version"]
        or authority["task_contract_sha256"]
        != study_binding["task_contract_sha256"]
        or authority["candidate_contract_sha256"]
        != study_binding["candidate_contract_sha256"]
    ):
        raise FS09PhaseBlindHandoffError(
            "bundled analysis plan source-contract authority drifted"
        )
    handoff_hashes, workbench_hashes, source_hashes = _plan_bindings(bundled_plan)
    expected_plan_hashes = {
        "fs09-phase-truth-workbench.js": workbench_hashes[
            "fs09-phase-truth-workbench.js"
        ],
        "fs09-phase-truth-workbench.css": workbench_hashes[
            "fs09-phase-truth-workbench.css"
        ],
        "OPERATOR_README.md": handoff_hashes["OPERATOR_README.md"],
        SERVER_LAUNCHER: handoff_hashes[SERVER_LAUNCHER],
        MEDIA_PATHS[0]: source_hashes["task_001_review_clip"],
        MEDIA_PATHS[1]: source_hashes["task_002_review_clip"],
    }
    for name, expected_sha in expected_plan_hashes.items():
        if _sha256_bytes(raw_by_name[name]) != expected_sha:
            raise FS09PhaseBlindHandoffError(
                f"blind handoff does not match the analysis plan: {name}"
            )
    recovered_review = _recover_canonical_review_html(
        raw_by_name["review.html"],
        bundle_id=manifest["bundle_id"],
        analysis_plan_sha256=authority["analysis_plan_sha256"],
    )
    if _sha256_bytes(recovered_review) != workbench_hashes["review.html"]:
        raise FS09PhaseBlindHandoffError(
            "public review does not derive from the plan-bound canonical workbench"
        )
    frozen_at = _timestamp(bundled_plan.get("frozen_at"), name="analysis plan frozen_at")
    if not frozen_at < manifest_time <= generated_at:
        raise FS09PhaseBlindHandoffError("blind handoff chronology is invalid")
    try:
        public_html = raw_by_name["review.html"].decode("utf-8")
        _start, _end, public_bootstrap = _parse_bootstrap(public_html)
    except UnicodeDecodeError as exc:
        raise FS09PhaseBlindHandoffError("public review HTML is not UTF-8") from exc
    if (
        public_bootstrap.get("handoff_bundle_id") != manifest["bundle_id"]
        or public_bootstrap.get("analysis_plan_sha256")
        != authority["analysis_plan_sha256"]
        or public_bootstrap.get("annotation_execution_authorized") is not False
        or public_bootstrap.get("external_protocol_receipt_verified") is not False
    ):
        raise FS09PhaseBlindHandoffError("public review identity binding drifted")
    tasks = public_bootstrap.get("tasks")
    if (
        not isinstance(tasks, list)
        or [str(item.get("task_id", "")) for item in tasks] != list(TASK_IDS)
        or public_bootstrap.get("review_clips")
        != dict(zip(TASK_IDS, MEDIA_PATHS, strict=True))
    ):
        raise FS09PhaseBlindHandoffError("public review task/media binding drifted")
    if "Python 门禁" in public_html or "scripts/compile_m77" in public_html:
        raise FS09PhaseBlindHandoffError("public review retains private operator commands")

    if source_pack_dir is not None:
        expected = _source_snapshot(
            source_pack_dir, generated_at=manifest["generated_at"]
        )
        if (
            expected["source_authority"] != authority
            or expected["bundle_id"] != manifest["bundle_id"]
            or expected["raw_by_name"] != raw_by_name
        ):
            raise FS09PhaseBlindHandoffError(
                "blind handoff does not replay its private canonical source"
            )
        if (bundle / MANIFEST_NAME).read_bytes() != manifest_raw:
            raise FS09PhaseBlindHandoffError(
                "blind handoff manifest changed during source replay"
            )
    current_files, current_directories = _directory_tree(bundle)
    if current_files != files or current_directories != directories:
        raise FS09PhaseBlindHandoffError("blind handoff tree changed during verification")
    if (bundle / MANIFEST_NAME).read_bytes() != manifest_raw:
        raise FS09PhaseBlindHandoffError(
            "blind handoff manifest changed during verification"
        )
    for name, raw in raw_by_name.items():
        if (bundle / name).read_bytes() != raw:
            raise FS09PhaseBlindHandoffError(
                f"blind handoff artifact changed during verification: {name}"
            )
    return {
        "bundle_dir": bundle,
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "manifest_sha256": _sha256_bytes(manifest_raw),
        "artifacts": {
            item["path"]: {
                "raw": raw_by_name[item["path"]],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
            }
            for item in artifacts
        },
        "generated_at": manifest["generated_at"],
        "bundle_id": manifest["bundle_id"],
        "content_root_sha256": manifest["content_root_sha256"],
    }


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def build_fs09_phase_blind_handoff(
    source_pack_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    source = Path(source_pack_dir).resolve()
    final = Path(output_dir).resolve()
    staging = final.with_name(f".{final.name}.building")
    if (
        _paths_overlap(source, final)
        or final.exists()
        or staging.exists()
        or not final.parent.is_dir()
    ):
        raise FS09PhaseBlindHandoffError(
            "handoff output exists, overlaps the source pack, or has no parent"
        )
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    snapshot = _source_snapshot(source, generated_at=generated_at)
    raw_by_name = snapshot["raw_by_name"]
    artifacts = [
        {
            "path": name,
            "bytes": len(raw_by_name[name]),
            "sha256": _sha256_bytes(raw_by_name[name]),
        }
        for name in sorted(PUBLIC_ARTIFACT_PATHS)
    ]
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_version": BUNDLE_VERSION,
        "bundle_id": snapshot["bundle_id"],
        "generated_at": generated_at,
        "status": BUNDLE_STATUS,
        "source_authority": snapshot["source_authority"],
        "scope": {"task_ids": list(TASK_IDS), "media_paths": list(MEDIA_PATHS)},
        "entrypoint": "review.html",
        "server_launcher": SERVER_LAUNCHER,
        "artifacts": artifacts,
        "content_root_sha256": _content_root(artifacts),
        "safety": {
            "relative_artifact_paths_only": True,
            "canonical_manifest_included": False,
            "tasks_file_included": False,
            "sealed_candidates_included": False,
            "compiled_truth_included": False,
            "absolute_paths_included": False,
            "candidate_boundaries_embedded_in_annotation_ui": False,
            "candidate_phases_embedded_in_annotation_ui": False,
            "coarse_target_selection_anchor_included": True,
            "target_selection_anchor_is_candidate_boundary_or_phase": False,
            "exact_candidate_boundaries_derivable_from_public_window": False,
            "exact_candidate_boundaries_or_phases_embedded_in_bundle": False,
            "isolated_public_bundle_only_required": True,
            "coarse_anchor_is_confidential": False,
            "candidate_contract_hash_is_confidential": False,
            "sealed_candidates_artifact_hash_is_confidential": False,
            "candidate_derived_hashes_are_confidential": False,
            "candidate_derived_hashes_are_content_commitments_only": True,
            "pose_overlay_embedded_in_annotation_ui": False,
            "directory_listing_enabled": False,
            "exact_server_allowlist": True,
            "source_pack_required_for_authority_replay": True,
            "hash_is_identity_or_signature": False,
            "annotation_execution_authorized": False,
            "external_protocol_receipt_verified": False,
            "production_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    staging_created = False
    committed = False
    try:
        staging.mkdir()
        staging_created = True
        for name, raw in raw_by_name.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        validate_fs09_phase_blind_handoff(staging, source_pack_dir=source)
        current = _source_snapshot(source, generated_at=generated_at)
        if (
            current["manifest_raw"] != snapshot["manifest_raw"]
            or current["plan_raw"] != snapshot["plan_raw"]
            or current["raw_by_name"] != snapshot["raw_by_name"]
        ):
            raise FS09PhaseBlindHandoffError(
                "source canonical pack changed before handoff commit"
            )
        if final.exists():
            raise FS09PhaseBlindHandoffError(
                "handoff output appeared before atomic commit"
            )
        staging.rename(final)
        staging_created = False
        committed = True
        validate_fs09_phase_blind_handoff(final, source_pack_dir=source)
        return manifest
    except Exception:
        if staging_created and staging.exists():
            shutil.rmtree(staging)
        if committed and final.exists():
            shutil.rmtree(final)
        raise


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "BUNDLE_STATUS",
    "BUNDLE_VERSION",
    "FS09PhaseBlindHandoffError",
    "build_fs09_phase_blind_handoff",
    "validate_fs09_phase_blind_handoff",
]
