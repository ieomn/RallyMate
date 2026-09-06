from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from rallymate_vision.pose.metadata import SCHEMA_DATA_PATH, keypoint_schema


REPORT_VERSION = "pose-finetune-readiness-v1.2.0"
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
M95_SHADOW_REGISTRY_PATH = (
    _WORKSPACE_ROOT / "models" / "rtmpose" / "m95-shadow-candidates.json"
).resolve()
M95_SHADOW_REGISTRY_VERSION = "rtmpose-shadow-candidates-2026-09-04.1"
M95_SHADOW_REGISTRY_SHA256 = (
    "11B656E536964E8D84415719D82CE5BB5451C4071CDEA295211962323AB2E81B"
)
GOVERNANCE_FIELDS = (
    "video_id",
    "subject_id",
    "session_id",
    "camera_id",
    "consent",
    "license",
    "split",
)
_HALPE26_SCHEMA = keypoint_schema("halpe26")
HALPE26_JOINTS = tuple(
    str(point["name"]) for point in _HALPE26_SCHEMA["keypoints"]
)
if len(HALPE26_JOINTS) != 26:
    raise RuntimeError("the canonical Halpe26 registry must contain exactly 26 joints")

_ANNOTATION_FIELDS = (
    "annotation_id",
    "task_id",
    "annotator_id",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "annotated_at",
)
_ADJUDICATION_FIELDS = (
    "adjudication_id",
    "task_id",
    "source_annotation_ids",
    "reviewer_id",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "adjudicated_at",
    "status",
)
_TRUTH_SOURCE_FILES = {
    "manifest": "manifest.json",
    "tasks": "tasks.jsonl",
    "annotations": "annotations.csv",
    "adjudications": "adjudications.csv",
}
_AFFIRMATIVE_CONSENT = {"1", "approved", "granted", "true", "yes"}
_SPLITS = {"train", "val", "test"}


class PoseFineTuneReadinessError(ValueError):
    """Raised when the audit invocation itself is unusable."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def load_m95_sealed_holdout_guard() -> dict[str, Any]:
    """Load the immutable M95 sealed-holdout deny binding without opening it."""

    registry_path = M95_SHADOW_REGISTRY_PATH
    if not registry_path.is_file():
        raise PoseFineTuneReadinessError(
            f"M95 shadow registry is missing: {registry_path}"
        )
    observed_registry_sha = _sha256_file(registry_path)
    if observed_registry_sha != M95_SHADOW_REGISTRY_SHA256:
        raise PoseFineTuneReadinessError("M95 shadow registry SHA-256 drift")
    registry = _read_json(registry_path)
    if registry.get("registry_version") != M95_SHADOW_REGISTRY_VERSION:
        raise PoseFineTuneReadinessError("M95 shadow registry version drift")
    protocol = registry.get("development_protocol")
    holdout = protocol.get("sealed_holdout") if isinstance(protocol, dict) else None
    if not isinstance(holdout, dict) or holdout.get("use_during_development") is not False:
        raise PoseFineTuneReadinessError("M95 sealed-holdout policy is invalid")
    video_id = str(holdout.get("video_id", "")).strip()
    relative_path_raw = holdout.get("video_relative_path")
    video_sha256 = str(holdout.get("video_sha256", "")).strip().upper()
    if not video_id:
        raise PoseFineTuneReadinessError("M95 sealed holdout video_id is missing")
    if not isinstance(relative_path_raw, str) or not relative_path_raw.strip():
        raise PoseFineTuneReadinessError("M95 sealed holdout path is missing")
    relative_path = Path(relative_path_raw)
    if relative_path.is_absolute():
        raise PoseFineTuneReadinessError("M95 sealed holdout path must be workspace-relative")
    video_path = (_WORKSPACE_ROOT / relative_path).resolve()
    if not video_path.is_relative_to(_WORKSPACE_ROOT):
        raise PoseFineTuneReadinessError("M95 sealed holdout path escapes the workspace")
    if len(video_sha256) != 64 or any(
        character not in "0123456789ABCDEF" for character in video_sha256
    ):
        raise PoseFineTuneReadinessError("M95 sealed holdout SHA-256 is invalid")
    return {
        "registry_version": M95_SHADOW_REGISTRY_VERSION,
        "registry_path": str(registry_path),
        "registry_sha256": observed_registry_sha,
        "video_id": video_id,
        "video_path": str(video_path),
        "video_sha256": video_sha256,
        "use_during_development": False,
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PoseFineTuneReadinessError(f"{path.name} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PoseFineTuneReadinessError(
                    f"{path.name} line {line_number} is invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise PoseFineTuneReadinessError(
                    f"{path.name} line {line_number} must be a JSON object"
                )
            records.append(record)
    return records


def _read_strict_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(fields):
            raise PoseFineTuneReadinessError(
                f"{path.name} headers must exactly match {list(fields)}"
            )
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in reader
        ]


def _bound_path(raw_path: Any, base_dir: Path) -> Path:
    path = Path(str(raw_path or ""))
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _check_file_binding(
    *,
    expected_path: Path,
    binding: Any,
    base_dir: Path,
    label: str,
) -> tuple[bool, str | None, str | None]:
    if not isinstance(binding, dict):
        return False, None, f"missing binding: {label}"
    try:
        bound_path = _bound_path(binding.get("path"), base_dir)
    except (OSError, RuntimeError, ValueError):
        return False, None, f"invalid bound path: {label}"
    if bound_path != expected_path.resolve():
        return False, None, f"bound path mismatch: {label}"
    if not expected_path.is_file():
        return False, None, f"bound file is missing: {label}"
    observed = _sha256_file(expected_path)
    if observed != str(binding.get("sha256", "")).upper():
        return False, observed, f"bound SHA-256 mismatch: {label}"
    return True, observed, None


def _check_declared_binding(
    *, binding: Any, base_dir: Path, label: str
) -> tuple[bool, str | None, str | None, str | None]:
    if not isinstance(binding, dict):
        return False, None, None, f"invalid binding: {label}"
    try:
        path = _bound_path(binding.get("path"), base_dir)
    except (OSError, RuntimeError, ValueError):
        return False, None, None, f"invalid bound path: {label}"
    if not path.is_file():
        return False, str(path), None, f"bound file is missing: {label}"
    observed = _sha256_file(path)
    if observed != str(binding.get("sha256", "")).upper():
        return False, str(path), observed, f"bound SHA-256 mismatch: {label}"
    return True, str(path), observed, None


def _parse_source_ids(raw: str) -> list[str]:
    return sorted({item.strip() for item in raw.split(";") if item.strip()})


def _parse_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    normalized = str(raw).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _normalized_point(value: dict[str, Any]) -> tuple[Any, Any, Any, str]:
    visible = _parse_bool(value.get("visible"))
    x = value.get("x_normalized")
    y = value.get("y_normalized")
    try:
        x = None if x in (None, "") else float(x)
        y = None if y in (None, "") else float(y)
    except (TypeError, ValueError):
        x = y = "invalid"
    reason = str(value.get("visibility_reason") or "").strip()
    return visible, x, y, reason


def _point_is_valid(point: tuple[Any, Any, Any, str]) -> bool:
    visible, x, y, reason = point
    if visible is True:
        return (
            isinstance(x, float)
            and isinstance(y, float)
            and 0.0 <= x < 1.0
            and 0.0 <= y < 1.0
            and not reason
        )
    return visible is False and x is None and y is None and bool(reason)


def _extract_source_videos(
    manifest: dict[str, Any], pack_dir: Path
) -> list[dict[str, Any]]:
    sources = manifest.get("sources", {})
    videos: list[dict[str, Any]] = []
    direct = sources.get("video") if isinstance(sources, dict) else None
    if isinstance(direct, dict):
        videos.append(
            {
                "video_id": str(manifest.get("video_id", "")).strip(),
                "binding": direct,
                "binding_base": pack_dir,
            }
        )
    per_video = sources.get("per_video", []) if isinstance(sources, dict) else []
    if isinstance(per_video, list):
        for item in per_video:
            if isinstance(item, dict) and isinstance(item.get("video"), dict):
                videos.append(
                    {
                        "video_id": str(item.get("video_id", "")).strip(),
                        "binding": item["video"],
                        "binding_base": pack_dir,
                    }
                )
    return videos


def _audit_source_videos(
    manifest: dict[str, Any],
    pack_dir: Path,
    task_video_ids: set[str],
    sealed_holdout: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[tuple[str, str]]]:
    results: list[dict[str, Any]] = []
    issues: list[str] = []
    fingerprint_entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in _extract_source_videos(manifest, pack_dir):
        video_id = item["video_id"]
        binding = item["binding"]
        if not video_id:
            issues.append("source video binding has an empty video_id")
            continue
        if video_id in seen:
            issues.append(f"duplicate source video binding: {video_id}")
            continue
        seen.add(video_id)
        try:
            path = _bound_path(binding.get("path"), item["binding_base"])
        except (OSError, RuntimeError, ValueError):
            issues.append(f"invalid source video path: {video_id}")
            continue
        expected_sha = str(binding.get("sha256", "")).upper()
        sealed_reason: str | None = None
        if video_id == sealed_holdout["video_id"]:
            sealed_reason = "video_id"
        elif str(path).casefold() == str(sealed_holdout["video_path"]).casefold():
            sealed_reason = "resolved path"
        elif expected_sha and expected_sha == sealed_holdout["video_sha256"]:
            sealed_reason = "declared SHA-256"
        if sealed_reason is not None:
            issues.append(
                f"sealed holdout source is forbidden during development: "
                f"{video_id} ({sealed_reason})"
            )
            results.append(
                {
                    "video_id": video_id,
                    "path": str(path),
                    "expected_sha256": expected_sha or None,
                    "observed_sha256": None,
                    "sha256_valid": False,
                    "sealed_holdout_rejected": True,
                }
            )
            continue
        observed_sha = _sha256_file(path) if path.is_file() else None
        valid = bool(observed_sha and expected_sha and observed_sha == expected_sha)
        if observed_sha == sealed_holdout["video_sha256"]:
            valid = False
            issues.append(
                f"sealed holdout source is forbidden during development: "
                f"{video_id} (observed SHA-256)"
            )
        if not path.is_file():
            issues.append(f"source video is missing: {video_id}")
        elif not expected_sha or observed_sha != expected_sha:
            issues.append(f"source video SHA-256 mismatch: {video_id}")
        if observed_sha:
            fingerprint_entries.append((str(path), observed_sha))
        results.append(
            {
                "video_id": video_id,
                "path": str(path),
                "expected_sha256": expected_sha or None,
                "observed_sha256": observed_sha,
                "sha256_valid": valid,
                "sealed_holdout_rejected": observed_sha
                == sealed_holdout["video_sha256"],
            }
        )
    for video_id in sorted(task_video_ids - seen):
        issues.append(f"task video has no SHA-256-bound source video: {video_id}")
    for video_id in sorted(seen - task_video_ids):
        issues.append(f"source video has no truth task: {video_id}")
    return sorted(results, key=lambda item: item["video_id"]), issues, fingerprint_entries


def _audit_pack(
    pack_dir: Path, sealed_holdout: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[tuple[str, str]]]:
    pack_dir = pack_dir.resolve()
    issues: list[str] = []
    fingerprint_entries: list[tuple[str, str]] = []
    required_paths = {
        key: pack_dir / relative for key, relative in _TRUTH_SOURCE_FILES.items()
    }
    validation_path = pack_dir / "compiled" / "validation-report.json"
    manual_path = pack_dir / "compiled" / "manual-keypoints.jsonl"
    for label, path in {
        **required_paths,
        "validation": validation_path,
        "manual_keypoints": manual_path,
    }.items():
        if not path.is_file():
            issues.append(f"missing required file: {label}")
    if issues:
        return (
            {
                "path": str(pack_dir),
                "pack_version": None,
                "validation_status": None,
                "video_ids": [],
                "source_videos": [],
                "counts": {
                    "tasks": 0,
                    "accepted_frames": 0,
                    "accepted_joint_values": 0,
                    "visible_coordinate_joint_values": 0,
                },
                "readiness": {
                    "compiled_bindings_valid": False,
                    "source_video_sha256_valid": False,
                    "two_annotator_independent_reviewer_lineage": False,
                    "full_task_coverage": False,
                },
                "issues": sorted(issues),
            },
            [],
            fingerprint_entries,
        )

    try:
        manifest = _read_json(required_paths["manifest"])
        validation = _read_json(validation_path)
        tasks = _read_jsonl(required_paths["tasks"])
        manual_records = _read_jsonl(manual_path)
        annotations = _read_strict_csv(required_paths["annotations"], _ANNOTATION_FIELDS)
        adjudications = _read_strict_csv(
            required_paths["adjudications"], _ADJUDICATION_FIELDS
        )
    except (OSError, json.JSONDecodeError, PoseFineTuneReadinessError) as exc:
        issues.append(str(exc))
        return (
            {
                "path": str(pack_dir),
                "pack_version": None,
                "validation_status": None,
                "video_ids": [],
                "source_videos": [],
                "counts": {
                    "tasks": 0,
                    "accepted_frames": 0,
                    "accepted_joint_values": 0,
                    "visible_coordinate_joint_values": 0,
                },
                "readiness": {
                    "compiled_bindings_valid": False,
                    "source_video_sha256_valid": False,
                    "two_annotator_independent_reviewer_lineage": False,
                    "full_task_coverage": False,
                },
                "issues": sorted(issues),
            },
            [],
            fingerprint_entries,
        )

    for path in [*required_paths.values(), validation_path, manual_path]:
        observed = _sha256_file(path)
        fingerprint_entries.append((str(path), observed))

    bindings_valid = True
    validation_sources = validation.get("sources", {})
    for name, expected_path in required_paths.items():
        valid, _, issue = _check_file_binding(
            expected_path=expected_path,
            binding=validation_sources.get(name)
            if isinstance(validation_sources, dict)
            else None,
            base_dir=pack_dir,
            label=f"validation.sources.{name}",
        )
        bindings_valid &= valid
        if issue:
            issues.append(issue)
    validation_artifacts = validation.get("artifacts", {})
    valid, _, issue = _check_file_binding(
        expected_path=manual_path,
        binding=validation_artifacts.get("manual_keypoints")
        if isinstance(validation_artifacts, dict)
        else None,
        base_dir=pack_dir,
        label="validation.artifacts.manual_keypoints",
    )
    bindings_valid &= valid
    if issue:
        issues.append(issue)
    for namespace, bindings, known_names in (
        ("sources", validation_sources, set(required_paths)),
        ("artifacts", validation_artifacts, {"manual_keypoints"}),
    ):
        if not isinstance(bindings, dict):
            bindings_valid = False
            issues.append(f"validation.{namespace} must be an object")
            continue
        for name, binding in sorted(bindings.items()):
            if name in known_names:
                continue
            valid, path, observed, issue = _check_declared_binding(
                binding=binding,
                base_dir=pack_dir,
                label=f"validation.{namespace}.{name}",
            )
            bindings_valid &= valid
            if path and observed:
                fingerprint_entries.append((path, observed))
            if issue:
                issues.append(issue)

    task_contract = str(manifest.get("task_contract_sha256", "")).upper()
    if not task_contract or _canonical_sha256(tasks) != task_contract:
        bindings_valid = False
        issues.append("manifest task_contract_sha256 mismatch")

    pack_version = str(manifest.get("pack_version", "")).strip() or None
    if validation.get("pack_version") != pack_version:
        bindings_valid = False
        issues.append("validation pack_version mismatch")
    validation_status = validation.get("status")
    if validation_status != "ready_for_keypoint_error_evaluation":
        issues.append(
            "compiled validation status must be "
            "ready_for_keypoint_error_evaluation"
        )

    task_by_key: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    task_by_id: dict[str, dict[str, Any]] = {}
    for task in tasks:
        try:
            task_id = str(task["task_id"]).strip()
            video_id = str(task["video_id"]).strip()
            frame_index = int(task["source_frame_index"])
            joint_name = str(task["joint_name"]).strip()
        except (KeyError, TypeError, ValueError):
            issues.append("truth task has invalid identity fields")
            continue
        if not task_id or task_id in task_by_id:
            issues.append(f"duplicate or empty truth task_id: {task_id}")
            continue
        task_by_id[task_id] = task
        task_by_key[(video_id, frame_index, joint_name)].append(task)
    task_video_ids = {
        str(task.get("video_id", "")).strip() for task in tasks if task.get("video_id")
    }
    source_videos, video_issues, video_fingerprints = _audit_source_videos(
        manifest, pack_dir, task_video_ids, sealed_holdout
    )
    issues.extend(video_issues)
    fingerprint_entries.extend(video_fingerprints)

    lineage_issue_start = len(issues)
    annotation_by_id: dict[str, dict[str, str]] = {}
    annotation_opinions: set[tuple[str, str]] = set()
    for row_number, row in enumerate(annotations, start=2):
        annotation_id = row["annotation_id"]
        if not annotation_id or annotation_id in annotation_by_id:
            issues.append(f"duplicate or empty annotation_id: {annotation_id}")
            continue
        if not row["task_id"] or row["task_id"] not in task_by_id:
            issues.append(f"annotations.csv row {row_number}: unknown task_id")
        if not row["annotator_id"]:
            issues.append(f"annotations.csv row {row_number}: annotator_id is required")
        opinion = (row["task_id"], row["annotator_id"])
        if opinion in annotation_opinions:
            issues.append(
                f"annotations.csv row {row_number}: annotator submitted twice for one task"
            )
        annotation_opinions.add(opinion)
        if not row["annotated_at"]:
            issues.append(f"annotations.csv row {row_number}: annotated_at is required")
        if not _point_is_valid(_normalized_point(row)):
            issues.append(f"annotations.csv row {row_number}: point is invalid")
        annotation_by_id[annotation_id] = row
    accepted_by_task: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(adjudications, start=2):
        if row["status"].lower() != "accepted":
            continue
        task_id = row["task_id"]
        if not task_id or task_id in accepted_by_task:
            issues.append(f"duplicate or empty accepted adjudication task_id: {task_id}")
            continue
        if task_id not in task_by_id:
            issues.append(f"adjudications.csv row {row_number}: unknown task_id")
        if not row["adjudication_id"] or not row["adjudicated_at"]:
            issues.append(
                f"adjudications.csv row {row_number}: adjudication id/time is required"
            )
        if not _point_is_valid(_normalized_point(row)):
            issues.append(f"adjudications.csv row {row_number}: point is invalid")
        accepted_by_task[task_id] = row

    accepted_values: list[dict[str, Any]] = []
    seen_compiled: set[tuple[str, int, str]] = set()
    for record_index, record in enumerate(manual_records, start=1):
        video_id = str(record.get("video_id", "")).strip()
        reviewer_id = str(record.get("reviewer_id", "")).strip()
        try:
            frame_index = int(record.get("source_frame_index"))
        except (TypeError, ValueError):
            issues.append(f"compiled record {record_index} has invalid source_frame_index")
            continue
        joints = record.get("joints")
        provenance = record.get("provenance")
        source_ids_by_joint = (
            provenance.get("source_annotation_ids_by_joint")
            if isinstance(provenance, dict)
            else None
        )
        record_issues: list[str] = []
        if record.get("adjudication_status") != "accepted":
            record_issues.append("adjudication_status is not accepted")
        if not reviewer_id:
            record_issues.append("reviewer_id is empty")
        if record.get("primary_player_id") != 1:
            record_issues.append("primary_player_id is not 1")
        if not isinstance(provenance, dict):
            record_issues.append("provenance is missing")
        else:
            if provenance.get("pack_version") != pack_version:
                record_issues.append("provenance pack_version mismatch")
            if str(provenance.get("task_contract_sha256", "")).upper() != task_contract:
                record_issues.append("provenance task_contract_sha256 mismatch")
        if not isinstance(joints, dict) or not joints:
            record_issues.append("joints are missing")
        if not isinstance(source_ids_by_joint, dict):
            record_issues.append("source_annotation_ids_by_joint is missing")
        if record_issues:
            issues.extend(
                f"compiled record {record_index}: {issue}" for issue in record_issues
            )
            continue

        for joint_name in sorted(joints):
            key = (video_id, frame_index, joint_name)
            joint_issues: list[str] = []
            if joint_name not in HALPE26_JOINTS:
                joint_issues.append("joint is not in Halpe26")
            if key in seen_compiled:
                joint_issues.append("duplicate compiled supervision")
            seen_compiled.add(key)
            matching_tasks = task_by_key.get(key, [])
            if len(matching_tasks) != 1:
                joint_issues.append("does not resolve to exactly one immutable task")
                task = None
            else:
                task = matching_tasks[0]
            source_ids = source_ids_by_joint.get(joint_name)
            if not isinstance(source_ids, list):
                joint_issues.append("provenance source annotation IDs are missing")
                source_ids = []
            source_ids = sorted({str(value).strip() for value in source_ids if str(value).strip()})
            if len(source_ids) < 2:
                joint_issues.append("fewer than two source annotations")
            source_rows = [annotation_by_id.get(source_id) for source_id in source_ids]
            if any(row is None for row in source_rows):
                joint_issues.append("provenance references an unknown source annotation")
            valid_source_rows = [row for row in source_rows if row is not None]
            annotator_ids = {row["annotator_id"] for row in valid_source_rows if row["annotator_id"]}
            if len(annotator_ids) < 2:
                joint_issues.append("source annotations lack two independent annotators")
            if task is not None and any(
                row["task_id"] != str(task["task_id"]) for row in valid_source_rows
            ):
                joint_issues.append("source annotations do not belong to the resolved task")
            adjudication = (
                accepted_by_task.get(str(task["task_id"])) if task is not None else None
            )
            if adjudication is None:
                joint_issues.append("accepted adjudication is missing")
            else:
                if _parse_source_ids(adjudication["source_annotation_ids"]) != source_ids:
                    joint_issues.append("compiled provenance disagrees with adjudication sources")
                if adjudication["reviewer_id"] != reviewer_id:
                    joint_issues.append("compiled reviewer disagrees with adjudication reviewer")
                if not reviewer_id or reviewer_id in annotator_ids:
                    joint_issues.append("reviewer is not independent from both annotators")
                if _normalized_point(joints[joint_name]) != _normalized_point(adjudication):
                    joint_issues.append("compiled joint value disagrees with adjudication")
            point = _normalized_point(joints[joint_name])
            if not _point_is_valid(point):
                joint_issues.append("compiled joint value is invalid")
            if joint_issues:
                issues.extend(
                    f"compiled {video_id}/{frame_index}/{joint_name}: {issue}"
                    for issue in joint_issues
                )
                continue
            accepted_values.append(
                {
                    "video_id": video_id,
                    "source_frame_index": frame_index,
                    "joint_name": joint_name,
                    "visible_with_coordinates": point[0] is True,
                }
            )

    validation_counts = validation.get("counts", {})
    actual_joint_count = sum(
        len(record.get("joints", {}))
        for record in manual_records
        if isinstance(record.get("joints"), dict)
    )
    for field, actual in (
        ("compiled_keypoint_records", len(manual_records)),
        ("compiled_joint_values", actual_joint_count),
        ("accepted_adjudications", len(accepted_by_task)),
    ):
        try:
            declared = int(validation_counts.get(field))
        except (TypeError, ValueError):
            declared = -1
        if declared != actual:
            issues.append(f"validation count mismatch: {field}")

    validation_errors = validation.get("errors")
    if isinstance(validation_errors, list) and validation_errors:
        issues.append("compiled validation report contains errors")
    full_coverage_declared = (
        validation.get("readiness", {}).get("full_task_coverage") is True
        if isinstance(validation.get("readiness"), dict)
        else False
    )
    all_tasks_accepted = (
        bool(task_by_id)
        and len(accepted_values) == len(task_by_id)
        and len(accepted_by_task) == len(task_by_id)
    )
    full_task_coverage = full_coverage_declared and all_tasks_accepted
    if not full_coverage_declared:
        issues.append("compiled validation does not declare full task coverage")
    if task_by_id and not all_tasks_accepted:
        issues.append("accepted compiled truth does not cover every immutable task")
    elif not task_by_id:
        issues.append("truth pack has no immutable tasks")

    source_videos_valid = bool(source_videos) and all(
        item["sha256_valid"] for item in source_videos
    )
    lineage_valid = (
        bool(accepted_values)
        and all_tasks_accepted
        and not issues[lineage_issue_start:]
    )
    accepted_frames = {
        (item["video_id"], item["source_frame_index"]) for item in accepted_values
    }
    visible_count = sum(item["visible_with_coordinates"] for item in accepted_values)
    pack_result = {
        "path": str(pack_dir),
        "pack_version": pack_version,
        "validation_status": validation_status,
        "video_ids": sorted(task_video_ids),
        "source_videos": source_videos,
        "counts": {
            "tasks": len(task_by_id),
            "accepted_frames": len(accepted_frames),
            "accepted_joint_values": len(accepted_values),
            "visible_coordinate_joint_values": visible_count,
        },
        "readiness": {
            "compiled_bindings_valid": bindings_valid,
            "source_video_sha256_valid": source_videos_valid,
            "two_annotator_independent_reviewer_lineage": lineage_valid,
            "full_task_coverage": full_task_coverage,
        },
        "issues": sorted(set(issues)),
    }
    return pack_result, accepted_values, fingerprint_entries


def _audit_governance(
    governance_csv: Path | None, expected_video_ids: set[str]
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    issues: list[str] = []
    fingerprint_entries: list[tuple[str, str]] = []
    if governance_csv is None:
        return (
            {
                "provided": False,
                "path": None,
                "sha256": None,
                "row_count": 0,
                "video_ids": [],
                "complete": False,
                "subject_session_leakage_free": False,
                "source_path_sha_cross_split_leakage_free": False,
                "split_by_video": {},
                "issues": ["governance CSV is required"],
            },
            fingerprint_entries,
        )
    governance_csv = governance_csv.resolve()
    if not governance_csv.is_file():
        return (
            {
                "provided": True,
                "path": str(governance_csv),
                "sha256": None,
                "row_count": 0,
                "video_ids": [],
                "complete": False,
                "subject_session_leakage_free": False,
                "source_path_sha_cross_split_leakage_free": False,
                "split_by_video": {},
                "issues": ["governance CSV is missing"],
            },
            fingerprint_entries,
        )
    observed_sha = _sha256_file(governance_csv)
    fingerprint_entries.append((str(governance_csv), observed_sha))
    try:
        rows = _read_strict_csv(governance_csv, GOVERNANCE_FIELDS)
    except (OSError, PoseFineTuneReadinessError) as exc:
        return (
            {
                "provided": True,
                "path": str(governance_csv),
                "sha256": observed_sha,
                "row_count": 0,
                "video_ids": [],
                "complete": False,
                "subject_session_leakage_free": False,
                "source_path_sha_cross_split_leakage_free": False,
                "split_by_video": {},
                "issues": [str(exc)],
            },
            fingerprint_entries,
        )
    by_video: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        video_id = row["video_id"]
        if not video_id:
            issues.append(f"row {row_number}: video_id is required")
            continue
        if video_id in by_video:
            issues.append(f"row {row_number}: duplicate video_id {video_id}")
            continue
        by_video[video_id] = row
        for field in ("subject_id", "session_id", "camera_id", "license"):
            if not row[field]:
                issues.append(f"row {row_number}: {field} is required")
        if row["consent"].lower() not in _AFFIRMATIVE_CONSENT:
            issues.append(f"row {row_number}: consent must be affirmative")
        if row["split"] not in _SPLITS:
            issues.append(f"row {row_number}: split must be train, val or test")
        elif row["split"] == "test":
            issues.append(
                f"row {row_number}: development dataset export only accepts train/val"
            )

    for video_id in sorted(expected_video_ids - set(by_video)):
        issues.append(f"missing governance row: {video_id}")
    for video_id in sorted(set(by_video) - expected_video_ids):
        issues.append(f"governance row is outside truth-pack videos: {video_id}")

    splits_by_subject: dict[str, set[str]] = defaultdict(set)
    splits_by_session: dict[str, set[str]] = defaultdict(set)
    for row in by_video.values():
        split = row["split"]
        if row["subject_id"] and split in _SPLITS:
            splits_by_subject[row["subject_id"]].add(split)
        if row["session_id"] and split in _SPLITS:
            splits_by_session[row["session_id"]].add(split)
    leakage_issues = [
        f"subject_id spans splits: {identifier}"
        for identifier, splits in sorted(splits_by_subject.items())
        if len(splits) > 1
    ] + [
        f"session_id spans splits: {identifier}"
        for identifier, splits in sorted(splits_by_session.items())
        if len(splits) > 1
    ]
    issues.extend(leakage_issues)
    leakage_free = not leakage_issues
    complete = (
        bool(expected_video_ids)
        and set(by_video) == expected_video_ids
        and not issues
        and leakage_free
    )
    return (
        {
            "provided": True,
            "path": str(governance_csv),
            "sha256": observed_sha,
            "row_count": len(rows),
            "video_ids": sorted(by_video),
            "complete": complete,
            "subject_session_leakage_free": leakage_free,
            "source_path_sha_cross_split_leakage_free": True,
            "split_by_video": {
                video_id: row["split"]
                for video_id, row in sorted(by_video.items())
            },
            "issues": sorted(set(issues)),
        },
        fingerprint_entries,
    )


def _source_alias_cross_split_issues(
    packs: list[dict[str, Any]], split_by_video: dict[str, str]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Reject one source video being relabelled into more than one split."""

    bindings: list[dict[str, str]] = []
    for pack in packs:
        for source in pack["source_videos"]:
            video_id = str(source.get("video_id", ""))
            path = str(source.get("path", ""))
            sha256 = str(source.get("observed_sha256") or "").upper()
            split = split_by_video.get(video_id, "")
            if video_id and path and sha256 and split in _SPLITS:
                bindings.append(
                    {
                        "video_id": video_id,
                        "path": path,
                        "path_key": path.casefold(),
                        "sha256": sha256,
                        "split": split,
                    }
                )

    issues: list[str] = []
    groups: list[dict[str, Any]] = []
    for dimension, key_name in (("resolved_path", "path_key"), ("sha256", "sha256")):
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for binding in bindings:
            grouped[binding[key_name]].append(binding)
        for key, members in sorted(grouped.items()):
            video_ids = sorted({item["video_id"] for item in members})
            splits = sorted({item["split"] for item in members})
            if len(video_ids) < 2:
                continue
            display_value = members[0]["path"] if dimension == "resolved_path" else key
            if len(splits) > 1:
                issues.append(
                    f"source video {dimension} spans splits: {display_value} "
                    f"({','.join(video_ids)}; {','.join(splits)})"
                )
                groups.append(
                    {
                        "dimension": dimension,
                        "value": display_value,
                        "video_ids": video_ids,
                        "splits": splits,
                    }
                )
            else:
                issues.append(
                    f"source video {dimension} is reused across video ids: "
                    f"{display_value} ({','.join(video_ids)}; {','.join(splits)})"
                )
    return sorted(set(issues)), groups


def audit_pose_finetune_readiness(
    truth_pack_dirs: Iterable[str | Path],
    *,
    governance_csv: str | Path | None = None,
) -> dict[str, Any]:
    """Audit adjudicated Halpe26 truth for dataset-export readiness.

    The audit is read-only. It verifies the compiled artifacts against their
    recorded hashes and reconstructs annotator/reviewer lineage from the source
    CSVs instead of trusting readiness booleans in an editable report.
    """

    pack_dirs = sorted({Path(path).resolve() for path in truth_pack_dirs}, key=str)
    if not pack_dirs:
        raise PoseFineTuneReadinessError("at least one truth-pack directory is required")

    packs: list[dict[str, Any]] = []
    all_values: list[dict[str, Any]] = []
    fingerprint_entries: list[tuple[str, str]] = []
    sealed_holdout = load_m95_sealed_holdout_guard()
    fingerprint_entries.append(
        (sealed_holdout["registry_path"], sealed_holdout["registry_sha256"])
    )
    topology_sha256 = _sha256_file(SCHEMA_DATA_PATH)
    fingerprint_entries.append((str(SCHEMA_DATA_PATH.resolve()), topology_sha256))
    for pack_dir in pack_dirs:
        pack, values, fingerprints = _audit_pack(pack_dir, sealed_holdout)
        packs.append(pack)
        for value in values:
            value["_pack_path"] = str(pack_dir)
        all_values.extend(values)
        fingerprint_entries.extend(fingerprints)

    aggregate_issues: list[str] = []
    unique_values: dict[tuple[str, int, str], dict[str, Any]] = {}
    frame_pack_owners: dict[tuple[str, int], str] = {}
    for value in all_values:
        frame_key = (value["video_id"], value["source_frame_index"])
        pack_path = value["_pack_path"]
        previous_pack = frame_pack_owners.setdefault(frame_key, pack_path)
        if previous_pack != pack_path:
            aggregate_issues.append(
                "accepted video/frame supervision is split across packs: "
                f"{frame_key[0]}/{frame_key[1]}"
            )
        key = (
            value["video_id"],
            value["source_frame_index"],
            value["joint_name"],
        )
        if key in unique_values:
            aggregate_issues.append(
                f"accepted supervision appears in multiple packs: {key[0]}/{key[1]}/{key[2]}"
            )
            continue
        unique_values[key] = value

    accepted_counts = Counter(
        value["joint_name"] for value in unique_values.values()
    )
    visible_counts = Counter(
        value["joint_name"]
        for value in unique_values.values()
        if value["visible_with_coordinates"]
    )
    halpe26_supervision = [
        {
            "index": index,
            "joint_name": joint_name,
            "accepted": accepted_counts[joint_name],
            "visible_with_coordinates": visible_counts[joint_name],
            "invisible": accepted_counts[joint_name] - visible_counts[joint_name],
        }
        for index, joint_name in enumerate(HALPE26_JOINTS)
    ]
    all_halpe26_supervised = all(
        row["visible_with_coordinates"] > 0 for row in halpe26_supervision
    )
    expected_video_ids = {
        video_id for pack in packs for video_id in pack["video_ids"]
    }
    governance, governance_fingerprints = _audit_governance(
        Path(governance_csv) if governance_csv is not None else None,
        expected_video_ids,
    )
    fingerprint_entries.extend(governance_fingerprints)
    source_alias_issues, source_alias_groups = _source_alias_cross_split_issues(
        packs, governance["split_by_video"]
    )
    if source_alias_issues:
        governance["issues"] = sorted(
            set([*governance["issues"], *source_alias_issues])
        )
        governance["complete"] = False
    if source_alias_groups:
        governance["source_path_sha_cross_split_leakage_free"] = False

    frame_has_visible_coordinate: dict[tuple[str, int], bool] = defaultdict(bool)
    for value in unique_values.values():
        frame_key = (value["video_id"], value["source_frame_index"])
        frame_has_visible_coordinate[frame_key] |= value["visible_with_coordinates"]
    aggregate_issues.extend(
        f"accepted frame has no visible coordinate supervision: {video_id}/{frame_index}"
        for (video_id, frame_index), has_visible in sorted(
            frame_has_visible_coordinate.items()
        )
        if not has_visible
    )

    train_visible_counts = Counter(
        value["joint_name"]
        for value in unique_values.values()
        if value["visible_with_coordinates"]
        and governance["split_by_video"].get(value["video_id"]) == "train"
    )
    train_halpe26_supervision = [
        {
            "index": index,
            "joint_name": joint_name,
            "visible_with_coordinates": train_visible_counts[joint_name],
        }
        for index, joint_name in enumerate(HALPE26_JOINTS)
    ]
    train_halpe26_supervised = all(
        row["visible_with_coordinates"] > 0 for row in train_halpe26_supervision
    )

    compiled_bindings_valid = all(
        pack["readiness"]["compiled_bindings_valid"] for pack in packs
    )
    source_video_sha256_valid = all(
        pack["readiness"]["source_video_sha256_valid"] for pack in packs
    )
    lineage_valid = all(
        pack["readiness"]["two_annotator_independent_reviewer_lineage"]
        for pack in packs
    )
    full_task_coverage = all(
        pack["readiness"]["full_task_coverage"] for pack in packs
    )
    accepted_truth_present = bool(unique_values)
    truth_ready = (
        compiled_bindings_valid
        and source_video_sha256_valid
        and lineage_valid
        and full_task_coverage
        and accepted_truth_present
        and all_halpe26_supervised
        and not aggregate_issues
        and all(not pack["issues"] for pack in packs)
    )
    dataset_export_allowed = (
        truth_ready and governance["complete"] and train_halpe26_supervised
    )
    if not truth_ready:
        status = "annotation_required"
    elif not governance["complete"]:
        status = "governance_required"
    elif not train_halpe26_supervised:
        status = "annotation_required"
    else:
        status = "ready_for_dataset_export"

    blockers = list(aggregate_issues)
    for pack in packs:
        blockers.extend(f"{pack['path']}: {issue}" for issue in pack["issues"])
    if not accepted_truth_present:
        blockers.append("no accepted adjudicated keypoint truth")
    blockers.extend(
        f"Halpe26 joint lacks visible coordinate supervision: {row['joint_name']}"
        for row in halpe26_supervision
        if row["visible_with_coordinates"] == 0
    )
    blockers.extend(governance["issues"])
    if governance["complete"] and not train_halpe26_supervised:
        blockers.extend(
            f"train split lacks visible coordinate supervision: {row['joint_name']}"
            for row in train_halpe26_supervision
            if row["visible_with_coordinates"] == 0
        )

    accepted_frames = {
        (value["video_id"], value["source_frame_index"])
        for value in unique_values.values()
    }
    fingerprint = _canonical_sha256(sorted(set(fingerprint_entries)))
    return {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "status": status,
        "accuracy_claim": False,
        "training_started": False,
        "inputs": {
            "truth_pack_dirs": [str(path) for path in pack_dirs],
            "governance_csv": str(Path(governance_csv).resolve())
            if governance_csv is not None
            else None,
            "input_fingerprint_sha256": fingerprint,
        },
        "topology": {
            "native_keypoint_format": "halpe26",
            "keypoint_count": len(HALPE26_JOINTS),
            "registry_path": str(SCHEMA_DATA_PATH.resolve()),
            "registry_sha256": topology_sha256,
            "joint_names": list(HALPE26_JOINTS),
        },
        "sealed_holdout_guard": sealed_holdout,
        "counts": {
            "pack_count": len(packs),
            "source_video_count": len(expected_video_ids),
            "accepted_frames": len(accepted_frames),
            "accepted_joint_values": len(unique_values),
            "visible_coordinate_joint_values": sum(visible_counts.values()),
        },
        "halpe26_supervision": halpe26_supervision,
        "train_halpe26_supervision": train_halpe26_supervision,
        "source_alias_cross_split_groups": source_alias_groups,
        "readiness": {
            "compiled_bindings_valid": compiled_bindings_valid,
            "source_video_sha256_valid": source_video_sha256_valid,
            "two_annotator_independent_reviewer_lineage": lineage_valid,
            "full_task_coverage": full_task_coverage,
            "accepted_truth_present": accepted_truth_present,
            "all_halpe26_joints_supervised": all_halpe26_supervised,
            "train_split_all_halpe26_joints_supervised": train_halpe26_supervised,
            "governance_complete": governance["complete"],
            "subject_session_leakage_free": governance[
                "subject_session_leakage_free"
            ],
            "source_path_sha_cross_split_leakage_free": governance[
                "source_path_sha_cross_split_leakage_free"
            ],
            "sealed_holdout_registry_binding_valid": True,
            "dataset_export_allowed": dataset_export_allowed,
        },
        "packs": packs,
        "governance": governance,
        "blockers": sorted(set(blockers)),
    }
