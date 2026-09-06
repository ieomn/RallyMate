from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

SCHEMA_VERSION = "1.0.0"
PILOT_VERSION = "m96-halpe26-development-pilot-v1.0.0"
PILOT_STATUS = "awaiting_independent_A_and_B_annotations"
ANNOTATION_SUBMISSION_VERSION = "m96-halpe26-annotation-csv-v1.0.0"
ADJUDICATION_BUNDLE_VERSION = "m96-halpe26-adjudication-bundle-v1.0.0"
ADJUDICATION_SUBMISSION_VERSION = "m96-halpe26-adjudication-csv-v1.0.0"
PILOT_MANIFEST_NAME = "pilot-manifest.json"
ADJUDICATION_MANIFEST_NAME = "adjudication-manifest.json"
ROLE_MANIFEST_NAME = "role-manifest.json"
ENTRYPOINT = "review.html"
WORKBENCH_JS = "m96-pose-pilot-workbench.js"
WORKBENCH_CSS = "m96-pose-pilot-workbench.css"
FRAMES_PER_VIDEO = 8
VIDEO_COUNT = 3
JOINT_COUNT = 26
FRAME_COUNT = FRAMES_PER_VIDEO * VIDEO_COUNT
TASK_COUNT = FRAME_COUNT * JOINT_COUNT
TARGET_INSTRUCTION = "标注画面中视觉面积最大的球员；如无法区分面积，则选择画面中心横坐标更小的球员。"

ANNOTATION_FIELDS = (
    "schema_version",
    "submission_version",
    "bundle_id",
    "role_slot",
    "task_contract_sha256",
    "annotation_id",
    "task_id",
    "frame_id",
    "video_id",
    "source_frame_index",
    "joint_index",
    "joint_name",
    "annotator_id",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "annotated_at",
)

ADJUDICATION_FIELDS = (
    "schema_version",
    "submission_version",
    "adjudication_bundle_id",
    "task_contract_sha256",
    "adjudication_id",
    "task_id",
    "source_annotation_a_id",
    "source_annotation_b_id",
    "reviewer_id",
    "visible",
    "x_normalized",
    "y_normalized",
    "visibility_reason",
    "adjudicated_at",
)

SAFETY = {
    "evaluation_only": True,
    "development_subset_only": True,
    "sealed_holdout_accessed": False,
    "model_coordinates_in_tasks": False,
    "model_values_visible_to_annotators": False,
    "dataset_export_allowed": False,
    "training_allowed": False,
    "promotion_allowed": False,
    "production_default_changed": False,
    "accuracy_claim_generated": False,
    "grade_or_threshold_generated": False,
}

_SHA_RE = re.compile(r"^[0-9A-F]{64}$")
_ROLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")
_UTC_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T"
    r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$"
)
_COORD_RE = re.compile(r"^(?:0|0\.\d{1,12})$")


class M96PosePilotError(ValueError):
    """Raised when an M96 package, submission, or immutable binding drifts."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _require_utc(value: str, *, name: str) -> str:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise M96PosePilotError(f"{name} must be an RFC3339 UTC timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise M96PosePilotError(f"{name} is not a real timestamp") from exc
    return value


def _require_sha(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise M96PosePilotError(f"{name} must be uppercase SHA-256")
    return value


def _require_role_id(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _ROLE_ID_RE.fullmatch(value) is None:
        raise M96PosePilotError(f"{name} is not a valid pseudonymous role ID")
    return value


def _require_record_id(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _RECORD_ID_RE.fullmatch(value) is None:
        raise M96PosePilotError(f"{name} is not a valid record ID")
    return value


def _identity_key(value: Any, *, name: str) -> str:
    return _require_role_id(value, name=name).casefold()


def _safe_relative(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "%" in value:
        raise M96PosePilotError(f"{name} must be a canonical relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise M96PosePilotError(f"{name} must be a canonical relative POSIX path")
    if path.as_posix() != value:
        raise M96PosePilotError(f"{name} must be a canonical relative POSIX path")
    return value


def _snapshot(path: Path, *, name: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise M96PosePilotError(f"{name} must be a regular file")
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(raw) != after.st_size
    ):
        raise M96PosePilotError(f"{name} changed while it was read")
    return raw


def _parse_json(raw: bytes, *, name: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise M96PosePilotError(f"{name} repeats JSON key {key}")
            result[key] = value
        return result

    def reject(value: str) -> None:
        raise M96PosePilotError(f"{name} contains invalid JSON constant {value}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=reject,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise M96PosePilotError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise M96PosePilotError(f"{name} must be a JSON object")
    return value


def _bound_workspace_file(
    workspace: Path,
    relative: Any,
    expected_sha256: Any,
    *,
    name: str,
) -> dict[str, Any]:
    portable = _safe_relative(relative, name=f"{name} path")
    path = (workspace / Path(*portable.split("/"))).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise M96PosePilotError(f"{name} escapes the workspace") from exc
    raw = _snapshot(path, name=name)
    expected = _require_sha(expected_sha256, name=f"{name} SHA")
    if _sha256(raw) != expected:
        raise M96PosePilotError(f"{name} SHA drifted")
    return {
        "path": str(path),
        "relative_path": portable,
        "sha256": expected,
        "bytes": len(raw),
    }


def _load_development_sources(workspace: Path) -> dict[str, Any]:
    relative = "models/rtmpose/m95-shadow-candidates.json"
    registry_raw = _snapshot(
        workspace / Path(*relative.split("/")), name="M95 source registry"
    )
    registry = _parse_json(registry_raw, name="M95 source registry")
    if (
        registry.get("schema_version") != "1.0.0"
        or registry.get("registry_version")
        != "rtmpose-shadow-candidates-2026-09-04.1"
    ):
        raise M96PosePilotError("unsupported M95 development source registry")
    protocol = registry.get("development_protocol")
    if (
        not isinstance(protocol, dict)
        or protocol.get("phase") != "development_smoke_only"
        or protocol.get("ground_truth_used") is not False
        or protocol.get("accuracy_claim") is not False
    ):
        raise M96PosePilotError("M95 development protocol safety drifted")
    holdout = protocol.get("sealed_holdout")
    if (
        not isinstance(holdout, dict)
        or holdout.get("use_during_development") is not False
        or not isinstance(holdout.get("video_id"), str)
        or not holdout["video_id"]
    ):
        raise M96PosePilotError("sealed holdout exclusion is not binding")
    holdout_id = holdout["video_id"]
    holdout_path = _safe_relative(
        holdout.get("video_relative_path"), name="sealed holdout path"
    )
    _require_sha(holdout.get("video_sha256"), name="sealed holdout SHA")
    declared = protocol.get("videos")
    if not isinstance(declared, list) or len(declared) != VIDEO_COUNT:
        raise M96PosePilotError("M96 requires exactly three declared development videos")
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in declared:
        if not isinstance(item, dict):
            raise M96PosePilotError("development video declaration is invalid")
        video_id = item.get("video_id")
        if (
            not isinstance(video_id, str)
            or not video_id
            or video_id in seen
            or video_id == holdout_id
            or item.get("video_relative_path") == holdout_path
        ):
            raise M96PosePilotError("development video IDs/paths include a duplicate or holdout")
        seen.add(video_id)
        frame_count = item.get("frame_count")
        if not isinstance(frame_count, int) or isinstance(frame_count, bool) or frame_count < 1:
            raise M96PosePilotError(f"declared frame count is invalid: {video_id}")
        videos.append(
            {
                "video_id": video_id,
                "declared_frame_count": frame_count,
                "video": _bound_workspace_file(
                    workspace,
                    item.get("video_relative_path"),
                    item.get("video_sha256"),
                    name=f"development video {video_id}",
                ),
                "frozen_frames": _bound_workspace_file(
                    workspace,
                    item.get("frozen_frames_relative_path"),
                    item.get("frozen_frames_sha256"),
                    name=f"frozen frames {video_id}",
                ),
            }
        )
    return {
        "path": str((workspace / Path(*relative.split("/"))).resolve()),
        "sha256": _sha256(registry_raw),
        "registry_version": registry["registry_version"],
        "protocol": protocol,
        "videos": videos,
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _asset(name: str) -> bytes:
    return _snapshot(
        _repo_root() / "src" / "rallymate_annotation" / "assets" / name,
        name=f"approved asset {name}",
    )


def _artifact_records(raw_by_path: Mapping[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": path, "bytes": len(raw), "sha256": _sha256(raw)}
        for path, raw in sorted(raw_by_path.items())
    ]


def _content_root(artifacts: list[dict[str, Any]]) -> str:
    return _sha256(_canonical_bytes({"artifacts": artifacts}))


def _write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except FileExistsError as exc:
        raise M96PosePilotError(f"staging file already exists: {path.name}") from exc


def _atomic_publish(
    output_dir: Path,
    raw_by_path: Mapping[str, bytes],
    *,
    validate_staging: Callable[[Path], Any] | None = None,
) -> None:
    output = output_dir.resolve()
    if output.exists() or output.is_symlink():
        raise M96PosePilotError(f"output already exists; immutable bundles are never overwritten: {output}")
    parent = output.parent
    if not parent.is_dir():
        raise M96PosePilotError(f"output parent does not exist: {parent}")
    staging = parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    if staging.exists() or staging.is_symlink():
        raise M96PosePilotError("unique staging path unexpectedly exists")
    try:
        staging.mkdir()
        for relative, raw in sorted(raw_by_path.items()):
            _write_new(staging / Path(*relative.split("/")), raw)
        if validate_staging is not None:
            validate_staging(staging)
        for attempt in range(4):
            try:
                staging.rename(output)
                break
            except PermissionError:
                if output.exists() or attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if staging.exists() and staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)


def _read_joint_schema(workspace: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    relative = "src/rallymate_vision/pose/data/keypoint_schemas.json"
    raw = _snapshot(workspace / Path(*relative.split("/")), name="keypoint schema")
    value = _parse_json(raw, name="keypoint schema")
    try:
        halpe = value["formats"]["halpe26"]
        joints = halpe["keypoints"]
    except (KeyError, TypeError) as exc:
        raise M96PosePilotError("Halpe26 schema is missing") from exc
    if (
        not isinstance(joints, list)
        or len(joints) != JOINT_COUNT
        or halpe.get("count") != JOINT_COUNT
    ):
        raise M96PosePilotError("Halpe26 schema must contain exactly 26 joints")
    normalized: list[dict[str, Any]] = []
    for index, joint in enumerate(joints):
        if (
            not isinstance(joint, dict)
            or joint.get("index") != index
            or not isinstance(joint.get("name"), str)
            or not joint["name"]
        ):
            raise M96PosePilotError("Halpe26 joint ordering drifted")
        normalized.append(
            {
                "index": index,
                "name": joint["name"],
                "downstream_joint_id": joint.get("downstream_joint_id"),
            }
        )
    return normalized, {
        "relative_path": relative,
        "raw_sha256": _sha256(raw),
        "format": "halpe26",
        "joint_count": JOINT_COUNT,
    }


def _selected_frames(video: Mapping[str, Any]) -> list[dict[str, Any]]:
    frames_path = Path(str(video["frozen_frames"]["path"]))
    eligible: list[dict[str, Any]] = []
    seen_processed: set[int] = set()
    line_count = 0
    with frames_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            line_count += 1
            try:
                record = json.loads(line)
                frame = record["frame"]
                processed_index = int(frame["processed_index"])
                source_frame_index = int(frame.get("source_frame_index", frame["index"]))
                timestamp_ms = int(frame["timestamp_ms"])
                width = int(frame["width"])
                height = int(frame["height"])
                players = sum(
                    item.get("class_name") == "player"
                    for item in record.get("detections", [])
                    if isinstance(item, dict)
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise M96PosePilotError(
                    f"frozen frame line is invalid for {video['video_id']}:{line_number}"
                ) from exc
            if processed_index in seen_processed:
                raise M96PosePilotError(f"frozen frames repeat processed index for {video['video_id']}")
            seen_processed.add(processed_index)
            if min(source_frame_index, timestamp_ms) < 0 or min(width, height) < 1:
                raise M96PosePilotError(f"invalid frame metadata for {video['video_id']}")
            if players:
                eligible.append(
                    {
                        "video_id": video["video_id"],
                        "source_frame_index": source_frame_index,
                        "processed_index": processed_index,
                        "timestamp_ms": timestamp_ms,
                        "frame_width": width,
                        "frame_height": height,
                    }
                )
    if line_count != int(video["declared_frame_count"]):
        raise M96PosePilotError(f"frozen frame count drifted for {video['video_id']}")
    if len(eligible) < FRAMES_PER_VIDEO:
        raise M96PosePilotError(f"not enough eligible frames for {video['video_id']}")
    indexes = [
        round(index * (len(eligible) - 1) / (FRAMES_PER_VIDEO - 1))
        for index in range(FRAMES_PER_VIDEO)
    ]
    if len(set(indexes)) != FRAMES_PER_VIDEO:
        raise M96PosePilotError("evenly spaced selection produced duplicate frames")
    selected: list[dict[str, Any]] = []
    for ordinal, index in enumerate(indexes, start=1):
        row = eligible[index]
        selected.append(
            {
                "frame_id": f"m96:{row['video_id']}:f{row['source_frame_index']:08d}",
                "video_id": row["video_id"],
                "sample_ordinal": ordinal,
                "source_frame_index": row["source_frame_index"],
                "processed_index": row["processed_index"],
                "timestamp_ms": row["timestamp_ms"],
                "frame_width": row["frame_width"],
                "frame_height": row["frame_height"],
                "target_instruction": TARGET_INSTRUCTION,
            }
        )
    return selected


def _contract(workspace: Path) -> dict[str, Any]:
    verified = _load_development_sources(workspace)
    videos = verified["videos"]
    if len(videos) != VIDEO_COUNT:
        raise M96PosePilotError("M96 pilot requires exactly three development videos")
    holdout = verified["protocol"].get("sealed_holdout")
    if not isinstance(holdout, dict) or holdout.get("use_during_development") is not False:
        raise M96PosePilotError("sealed holdout exclusion is not binding")
    holdout_id = str(holdout.get("video_id", ""))
    if not holdout_id or any(video["video_id"] == holdout_id for video in videos):
        raise M96PosePilotError("sealed holdout is present in development videos")

    joints, schema_binding = _read_joint_schema(workspace)
    selected: list[dict[str, Any]] = []
    video_bindings: list[dict[str, Any]] = []
    for video in videos:
        frames = _selected_frames(video)
        selected.extend(frames)
        video_bindings.append(
            {
                "video_id": video["video_id"],
                "video_relative_path": video["video"]["relative_path"],
                "video_raw_sha256": video["video"]["sha256"],
                "video_bytes": video["video"]["bytes"],
                "frozen_frames_relative_path": video["frozen_frames"]["relative_path"],
                "frozen_frames_raw_sha256": video["frozen_frames"]["sha256"],
                "declared_frame_count": video["declared_frame_count"],
            }
        )
    if len(selected) != FRAME_COUNT or len({row["frame_id"] for row in selected}) != FRAME_COUNT:
        raise M96PosePilotError("M96 selected-frame cardinality drifted")

    tasks: list[dict[str, Any]] = []
    for frame in selected:
        for joint in joints:
            tasks.append(
                {
                    "task_id": f"{frame['frame_id']}:j{joint['index']:02d}",
                    "frame_id": frame["frame_id"],
                    "video_id": frame["video_id"],
                    "sample_ordinal": frame["sample_ordinal"],
                    "source_frame_index": frame["source_frame_index"],
                    "processed_index": frame["processed_index"],
                    "timestamp_ms": frame["timestamp_ms"],
                    "frame_width": frame["frame_width"],
                    "frame_height": frame["frame_height"],
                    "joint_index": joint["index"],
                    "joint_name": joint["name"],
                    "downstream_joint_id": joint["downstream_joint_id"],
                    "target_instruction": TARGET_INSTRUCTION,
                }
            )
    if len(tasks) != TASK_COUNT or len({row["task_id"] for row in tasks}) != TASK_COUNT:
        raise M96PosePilotError("M96 joint-task cardinality drifted")

    contract_payload = {
        "pilot_version": PILOT_VERSION,
        "evaluation_only": True,
        "selection_algorithm": "eight_evenly_spaced_ordinals_among_frozen_frames_with_at_least_one_player_detection",
        "target_rule": TARGET_INSTRUCTION,
        "coordinate_space": "full_source_frame_normalized_top_left_half_open_[0,1)",
        "video_bindings": video_bindings,
        "selected_frames": selected,
        "joint_schema": joints,
        "tasks": tasks,
    }
    return {
        "registry_binding": {
            "relative_path": Path(verified["path"]).resolve().relative_to(workspace).as_posix(),
            "raw_sha256": verified["sha256"],
            "registry_version": verified["registry_version"],
        },
        "keypoint_schema_binding": schema_binding,
        "video_bindings": video_bindings,
        "selected_frames": selected,
        "joints": joints,
        "tasks": tasks,
        "task_contract_sha256": _sha256(_canonical_bytes(contract_payload)),
    }


def _tasks_jsonl(tasks: list[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_bytes(task) + b"\n" for task in tasks)


def _governance_template(video_bindings: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    fields = (
        "video_id",
        "consent_status",
        "consent_record_reference",
        "subject_id",
        "session_id",
        "split_assignment",
        "usage_scope",
        "retention_policy",
    )
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(fields)
    for video in video_bindings:
        writer.writerow(
            (
                video["video_id"],
                "REPLACE_WITH_CONSENT_STATUS",
                "REPLACE_WITH_CONSENT_RECORD_REFERENCE",
                "REPLACE_WITH_SUBJECT_ID",
                "REPLACE_WITH_SESSION_ID",
                "REPLACE_WITH_SPLIT_ASSIGNMENT",
                "REPLACE_WITH_USAGE_SCOPE",
                "REPLACE_WITH_RETENTION_POLICY",
            )
        )
    return output.getvalue().encode("utf-8")


def _field_contract() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "pilot_version": PILOT_VERSION,
        "annotation_csv_fields": list(ANNOTATION_FIELDS),
        "adjudication_csv_fields": list(ADJUDICATION_FIELDS),
        "coordinate_contract": {
            "origin": "top_left",
            "space": "full_source_frame_normalized",
            "interval": "half_open_[0,1)",
            "visible_true": "x_normalized_and_y_normalized_required_visibility_reason_blank",
            "visible_false": "coordinates_blank_visibility_reason_required",
        },
        "identity_contract": {
            "A_B_distinct_case_insensitive": True,
            "C_distinct_from_A_B_case_insensitive": True,
            "real_names_emails_and_contact_details_forbidden": True,
        },
        "completion_contract": {
            "A_exact_task_rows": TASK_COUNT,
            "B_exact_task_rows": TASK_COUNT,
            "C_exact_disagreement_rows": "declared_by_verified_adjudication_bundle",
            "partial_CSV_is_draft_only": True,
        },
        "revision_contract": {
            "raw_sha256": "SHA256_of_exact_received_CSV_bytes",
            "submission_revision_sha256": "SHA256_of_canonical_validated_field_rows_in_task_order",
            "source_revisions_copied_without_rewriting": True,
        },
        "safety": SAFETY,
    }


def _protocol(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PILOT_VERSION,
        "purpose": "evaluation_only_development_subset_for_future_paired_model_measurement",
        "scope": {
            "video_count": VIDEO_COUNT,
            "frames_per_video": FRAMES_PER_VIDEO,
            "frame_count": FRAME_COUNT,
            "keypoint_format": "halpe26",
            "keypoint_count": JOINT_COUNT,
            "joint_task_count": TASK_COUNT,
        },
        "sampling": {
            "algorithm": "eight_evenly_spaced_ordinals_among_frozen_frames_with_at_least_one_player_detection",
            "rounding": "python_round_nearest_ties_to_even",
            "coverage": "first_through_last_eligible_timeline_positions",
            "deterministic": True,
        },
        "target_definition": {
            "instruction": TARGET_INSTRUCTION,
            "numeric_detection_box_disclosed": False,
            "pose_or_keypoint_estimate_disclosed": False,
        },
        "coordinate_definition": {
            "origin": "top_left",
            "space": "full_source_frame_normalized",
            "interval": "half_open_[0,1)",
        },
        "roles": {
            "A": "independent_annotator",
            "B": "independent_annotator",
            "C": "disagreement_only_adjudicator_after_complete_A_and_B_intake",
        },
        "source_bindings": {
            "registry": contract["registry_binding"],
            "keypoint_schema": contract["keypoint_schema_binding"],
            "videos": contract["video_bindings"],
        },
        "selected_frames": contract["selected_frames"],
        "joint_schema": contract["joints"],
        "task_contract_sha256": contract["task_contract_sha256"],
        "safety": SAFETY,
    }


def _html(bootstrap: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(
        bootstrap, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).replace("</", "<\\/")
    role = bootstrap["role_slot"]
    title = "独立标注" if role in {"A", "B"} else "分歧裁决"
    identity_label = "annotator ID" if role in {"A", "B"} else "reviewer ID"
    source_note = (
        "此角色包不含另一位标注者的结果，也不含任何模型点位。"
        if role in {"A", "B"}
        else "此包仅含 A/B 完整提交后确认存在分歧的人工点位，不含任何模型点位。"
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self'; media-src 'self'; img-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>M96 Halpe26 {title} · {role}</title>
  <link rel="stylesheet" href="{WORKBENCH_CSS}">
</head>
<body>
<main>
  <h1>M96 Halpe26 {title} · 角色 <span id="role"></span></h1>
  <p class="muted">evaluation_only 开发子集。{source_note}</p>
  <p class="warning">请使用不含姓名、邮箱或联系方式的稳定化名 ID。只有覆盖本角色全部任务的 CSV 才能通过 intake；中途可导出并重新导入恢复。</p>
  <div class="grid">
    <section class="panel">
      <div class="viewer"><video id="video" preload="metadata" playsinline></video><canvas id="canvas" width="960" height="540"></canvas></div>
      <p id="task-label"></p><p id="frame-meta" class="meta"></p><div id="sources" hidden></div>
    </section>
    <section class="panel">
      <label for="identity">{identity_label}</label><input id="identity" autocomplete="off" maxlength="64">
      <label for="task">任务</label><select id="task"></select>
      <div class="buttons"><button id="previous" type="button">上一点</button><button id="next" type="button">下一点</button></div>
      <label for="reason">不可见原因</label><textarea id="reason" placeholder="仅在该关节点不可可靠辨认时填写"></textarea>
      <div class="buttons"><button id="mark-invisible" type="button">标为不可见</button><button id="clear" type="button">清除当前点</button></div>
      <label for="import">导入本角色 CSV 恢复草稿</label><input id="import" type="file" accept=".csv,text/csv">
      <div class="buttons"><button id="export" class="primary" type="button">导出 CSV</button><button id="reset" class="danger" type="button">清空浏览器草稿</button></div>
      <p id="progress" class="meta"></p><p id="status" class="status">点击画面记录当前关节点。</p>
    </section>
  </div>
  <script id="m96-pose-pilot-bootstrap" type="application/json">{encoded}</script>
  <script src="{WORKBENCH_JS}" defer></script>
</main>
</body>
</html>
"""
    return html.encode("utf-8")


def _annotation_role(
    *,
    slot: str,
    contract: Mapping[str, Any],
    protocol_sha256: str,
    generated_at: str,
    js_raw: bytes,
    css_raw: bytes,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    bundle_id = "m96-annotation-" + slot + "-" + _sha256(
        _canonical_bytes(
            {
                "pilot_version": PILOT_VERSION,
                "role_slot": slot,
                "task_contract_sha256": contract["task_contract_sha256"],
                "protocol_sha256": protocol_sha256,
            }
        )
    )
    video_urls = {
        item["video_id"]: "/" + item["video_relative_path"]
        for item in contract["video_bindings"]
    }
    bootstrap = {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "role_slot": slot,
        "role_kind": "independent_annotator",
        "task_contract_sha256": contract["task_contract_sha256"],
        "submission_schema_version": SCHEMA_VERSION,
        "submission_version": ANNOTATION_SUBMISSION_VERSION,
        "fields": list(ANNOTATION_FIELDS),
        "videos": video_urls,
        "tasks": contract["tasks"],
        "excluded_reviewer_ids": [],
        "safety": SAFETY,
    }
    html_raw = _html(bootstrap)
    artifacts_raw = {
        ENTRYPOINT: html_raw,
        WORKBENCH_JS: js_raw,
        WORKBENCH_CSS: css_raw,
    }
    artifacts = _artifact_records(artifacts_raw)
    revision_payload = {
        "bundle_version": PILOT_VERSION,
        "bundle_id": bundle_id,
        "role_slot": slot,
        "task_contract_sha256": contract["task_contract_sha256"],
        "protocol_sha256": protocol_sha256,
        "video_sources": [
            {
                "video_id": item["video_id"],
                "relative_path": item["video_relative_path"],
                "raw_sha256": item["video_raw_sha256"],
                "bytes": item["video_bytes"],
            }
            for item in contract["video_bindings"]
        ],
        "artifacts": artifacts,
        "safety": SAFETY,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_version": PILOT_VERSION,
        "bundle_kind": "independent_annotation",
        "status": "ready_for_independent_annotation",
        "bundle_id": bundle_id,
        "role_slot": slot,
        "role_kind": "independent_annotator",
        "generated_at": generated_at,
        "entrypoint": ENTRYPOINT,
        "task_contract_sha256": contract["task_contract_sha256"],
        "protocol_sha256": protocol_sha256,
        "task_count": TASK_COUNT,
        "submission_version": ANNOTATION_SUBMISSION_VERSION,
        "submission_fields": list(ANNOTATION_FIELDS),
        "video_sources": revision_payload["video_sources"],
        "artifacts": artifacts,
        "content_root_sha256": _content_root(artifacts),
        "revision_sha256": _sha256(_canonical_bytes(revision_payload)),
        "safety": SAFETY,
    }
    return manifest, {**artifacts_raw, ROLE_MANIFEST_NAME: _pretty_bytes(manifest)}


def build_m96_pose_pilot(
    workspace: str | Path,
    output_dir: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise M96PosePilotError("workspace does not exist")
    timestamp = _require_utc(generated_at or _now_utc(), name="generated_at")
    contract = _contract(root)
    tasks_raw = _tasks_jsonl(contract["tasks"])
    protocol = _protocol(contract)
    protocol["tasks_raw_sha256"] = _sha256(tasks_raw)
    protocol_raw = _pretty_bytes(protocol)
    fields_raw = _pretty_bytes(_field_contract())
    governance_raw = _governance_template(contract["video_bindings"])
    js_raw = _asset(WORKBENCH_JS)
    css_raw = _asset(WORKBENCH_CSS)

    raw_by_path: dict[str, bytes] = {
        "protocol.json": protocol_raw,
        "tasks.jsonl": tasks_raw,
        "field-contract.json": fields_raw,
        "governance.template.csv": governance_raw,
    }
    role_summaries = []
    for slot in ("A", "B"):
        role_manifest, role_raw = _annotation_role(
            slot=slot,
            contract=contract,
            protocol_sha256=_sha256(protocol_raw),
            generated_at=timestamp,
            js_raw=js_raw,
            css_raw=css_raw,
        )
        prefix = f"annotator-{slot}"
        raw_by_path.update({f"{prefix}/{path}": raw for path, raw in role_raw.items()})
        role_summaries.append(
            {
                "role_slot": slot,
                "bundle_id": role_manifest["bundle_id"],
                "manifest_path": f"{prefix}/{ROLE_MANIFEST_NAME}",
                "manifest_raw_sha256": _sha256(role_raw[ROLE_MANIFEST_NAME]),
                "revision_sha256": role_manifest["revision_sha256"],
            }
        )

    validation = {
        "schema_version": SCHEMA_VERSION,
        "report_version": "m96-pilot-build-validation-v1.0.0",
        "generated_at": timestamp,
        "status": PILOT_STATUS,
        "checks": {
            "three_development_videos": True,
            "eight_deterministic_frames_per_video": True,
            "timeline_endpoints_covered": True,
            "complete_halpe26_ordering": True,
            "exact_624_joint_tasks": True,
            "sealed_holdout_excluded": True,
            "model_coordinates_absent_from_tasks": True,
            "A_and_B_role_packages_distinct": True,
            "A_and_B_results_not_present": True,
            "governance_values_are_placeholders": True,
        },
        "counts": {
            "videos": VIDEO_COUNT,
            "frames": FRAME_COUNT,
            "joints_per_frame": JOINT_COUNT,
            "joint_tasks": TASK_COUNT,
            "human_annotation_rows": 0,
            "adjudication_rows": 0,
            "accuracy_metrics": 0,
        },
        "task_contract_sha256": contract["task_contract_sha256"],
        "next_required_action": "complete_and_return_separate_A_and_B_CSV_exports",
        "safety": SAFETY,
    }
    raw_by_path["validation-report.json"] = _pretty_bytes(validation)
    artifacts = _artifact_records(raw_by_path)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "pilot_version": PILOT_VERSION,
        "status": PILOT_STATUS,
        "generated_at": timestamp,
        "purpose": "evaluation_only_development_subset",
        "task_contract_sha256": contract["task_contract_sha256"],
        "scope": {
            "video_count": VIDEO_COUNT,
            "frames_per_video": FRAMES_PER_VIDEO,
            "frame_count": FRAME_COUNT,
            "keypoint_format": "halpe26",
            "keypoint_count": JOINT_COUNT,
            "joint_task_count": TASK_COUNT,
        },
        "roles": role_summaries,
        "artifacts": artifacts,
        "content_root_sha256": _content_root(artifacts),
        "safety": SAFETY,
    }
    raw_by_path[PILOT_MANIFEST_NAME] = _pretty_bytes(manifest)
    _atomic_publish(
        Path(output_dir),
        raw_by_path,
        validate_staging=lambda staging: validate_m96_pose_pilot(root, staging),
    )
    validated = validate_m96_pose_pilot(root, output_dir)
    return validated["manifest"]


def _validate_artifact_tree(
    directory: Path,
    artifacts: Any,
    *,
    manifest_names: set[str],
) -> dict[str, bytes]:
    if not isinstance(artifacts, list):
        raise M96PosePilotError("artifact list is missing")
    snapshots: dict[str, bytes] = {}
    seen: set[str] = set()
    for index, record in enumerate(artifacts, start=1):
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise M96PosePilotError(f"artifact record {index} keys are not exact")
        relative = _safe_relative(record["path"], name=f"artifact {index} path")
        if relative in seen:
            raise M96PosePilotError(f"duplicate artifact path: {relative}")
        seen.add(relative)
        raw = _snapshot(directory / Path(*relative.split("/")), name=f"artifact {relative}")
        if record["bytes"] != len(raw) or _require_sha(record["sha256"], name="artifact SHA") != _sha256(raw):
            raise M96PosePilotError(f"artifact binding drifted: {relative}")
        snapshots[relative] = raw
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    }
    expected = seen | manifest_names
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise M96PosePilotError(f"immutable file tree drifted; missing={missing}, extra={extra}")
    if any(path.is_symlink() for path in directory.rglob("*")):
        raise M96PosePilotError("immutable file tree must not contain symlinks")
    return snapshots


def _read_tasks(raw: bytes) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line:
            raise M96PosePilotError("tasks JSONL contains a blank line")
        tasks.append(_parse_json(line, name=f"task line {line_number}"))
    if not tasks:
        raise M96PosePilotError("tasks JSONL is empty")
    return tasks


def _extract_bootstrap(raw: bytes) -> dict[str, Any]:
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise M96PosePilotError("review HTML is not UTF-8") from exc
    marker = '<script id="m96-pose-pilot-bootstrap" type="application/json">'
    if html.count(marker) != 1:
        raise M96PosePilotError("review HTML bootstrap is missing or ambiguous")
    start = html.index(marker) + len(marker)
    end = html.find("</script>", start)
    if end < 0:
        raise M96PosePilotError("review HTML bootstrap is not closed")
    return _parse_json(html[start:end].replace("<\\/", "</").encode("utf-8"), name="review bootstrap")


def _validate_role_manifest_shape(
    manifest: Mapping[str, Any],
    *,
    expected_slot: str,
    expected_kind: str,
) -> None:
    expected_keys = {
        "schema_version",
        "bundle_version",
        "bundle_kind",
        "status",
        "bundle_id",
        "role_slot",
        "role_kind",
        "generated_at",
        "entrypoint",
        "task_contract_sha256",
        "protocol_sha256",
        "task_count",
        "submission_version",
        "submission_fields",
        "video_sources",
        "artifacts",
        "content_root_sha256",
        "revision_sha256",
        "safety",
    }
    if set(manifest) != expected_keys:
        raise M96PosePilotError("role manifest keys are not exact")
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["bundle_kind"] != expected_kind
        or manifest["role_slot"] != expected_slot
        or manifest["entrypoint"] != ENTRYPOINT
        or manifest["safety"] != SAFETY
    ):
        raise M96PosePilotError("role manifest identity or safety drifted")
    _require_utc(manifest["generated_at"], name="role generated_at")
    _require_record_id(manifest["bundle_id"], name="role bundle_id")
    _require_sha(manifest["task_contract_sha256"], name="task contract SHA")
    _require_sha(manifest["protocol_sha256"], name="protocol SHA")
    _require_sha(manifest["content_root_sha256"], name="content root SHA")
    _require_sha(manifest["revision_sha256"], name="role revision SHA")
    if not isinstance(manifest["video_sources"], list) or len(manifest["video_sources"]) != VIDEO_COUNT:
        raise M96PosePilotError("role video source count drifted")
    for record in manifest["video_sources"]:
        if not isinstance(record, dict) or set(record) != {"video_id", "relative_path", "raw_sha256", "bytes"}:
            raise M96PosePilotError("role video source keys are not exact")
        _safe_relative(record["relative_path"], name="role video path")
        _require_sha(record["raw_sha256"], name="role video SHA")
        if not isinstance(record["bytes"], int) or isinstance(record["bytes"], bool) or record["bytes"] < 1:
            raise M96PosePilotError("role video byte size is invalid")


def _validate_role_directory(
    directory: Path,
    *,
    expected_slot: str,
    expected_kind: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    manifest_raw = _snapshot(directory / ROLE_MANIFEST_NAME, name="role manifest")
    manifest = _parse_json(manifest_raw, name="role manifest")
    _validate_role_manifest_shape(manifest, expected_slot=expected_slot, expected_kind=expected_kind)
    snapshots = _validate_artifact_tree(
        directory,
        manifest["artifacts"],
        manifest_names={ROLE_MANIFEST_NAME},
    )
    if manifest["content_root_sha256"] != _content_root(manifest["artifacts"]):
        raise M96PosePilotError("role content root drifted")
    bootstrap = _extract_bootstrap(snapshots[ENTRYPOINT])
    required_bootstrap = {
        "schema_version",
        "bundle_id",
        "role_slot",
        "role_kind",
        "task_contract_sha256",
        "submission_schema_version",
        "submission_version",
        "fields",
        "videos",
        "tasks",
        "excluded_reviewer_ids",
        "safety",
    }
    if set(bootstrap) != required_bootstrap:
        raise M96PosePilotError("role bootstrap keys are not exact")
    if (
        bootstrap["bundle_id"] != manifest["bundle_id"]
        or bootstrap["role_slot"] != expected_slot
        or bootstrap["task_contract_sha256"] != manifest["task_contract_sha256"]
        or bootstrap["submission_version"] != manifest["submission_version"]
        or bootstrap["fields"] != manifest["submission_fields"]
        or bootstrap["safety"] != SAFETY
    ):
        raise M96PosePilotError("role bootstrap binding drifted")
    return manifest, snapshots


def _validate_governance(raw: bytes, video_ids: list[str]) -> None:
    try:
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise M96PosePilotError("governance template is not valid UTF-8 CSV") from exc
    expected_header = [
        "video_id",
        "consent_status",
        "consent_record_reference",
        "subject_id",
        "session_id",
        "split_assignment",
        "usage_scope",
        "retention_policy",
    ]
    if not rows or rows[0] != expected_header or len(rows) != VIDEO_COUNT + 1:
        raise M96PosePilotError("governance template shape drifted")
    if [row[0] for row in rows[1:]] != video_ids:
        raise M96PosePilotError("governance video IDs drifted")
    for row in rows[1:]:
        if len(row) != len(expected_header) or any(
            not cell.startswith("REPLACE_WITH_") for cell in row[1:]
        ):
            raise M96PosePilotError("governance template contains a non-placeholder value")


def validate_m96_pose_pilot(
    workspace: str | Path,
    bundle_dir: str | Path,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    bundle = Path(bundle_dir).resolve()
    if bundle.is_symlink() or not bundle.is_dir():
        raise M96PosePilotError("pilot bundle must be a regular directory")
    manifest_raw = _snapshot(bundle / PILOT_MANIFEST_NAME, name="pilot manifest")
    manifest = _parse_json(manifest_raw, name="pilot manifest")
    expected_keys = {
        "schema_version",
        "pilot_version",
        "status",
        "generated_at",
        "purpose",
        "task_contract_sha256",
        "scope",
        "roles",
        "artifacts",
        "content_root_sha256",
        "safety",
    }
    if set(manifest) != expected_keys:
        raise M96PosePilotError("pilot manifest keys are not exact")
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["pilot_version"] != PILOT_VERSION
        or manifest["status"] != PILOT_STATUS
        or manifest["purpose"] != "evaluation_only_development_subset"
        or manifest["scope"]
        != {
            "video_count": VIDEO_COUNT,
            "frames_per_video": FRAMES_PER_VIDEO,
            "frame_count": FRAME_COUNT,
            "keypoint_format": "halpe26",
            "keypoint_count": JOINT_COUNT,
            "joint_task_count": TASK_COUNT,
        }
        or manifest["safety"] != SAFETY
    ):
        raise M96PosePilotError("pilot manifest identity, scope, or safety drifted")
    _require_utc(manifest["generated_at"], name="pilot generated_at")
    _require_sha(manifest["task_contract_sha256"], name="task contract SHA")
    if manifest["content_root_sha256"] != _content_root(manifest["artifacts"]):
        raise M96PosePilotError("pilot content root drifted")
    snapshots = _validate_artifact_tree(
        bundle,
        manifest["artifacts"],
        manifest_names={PILOT_MANIFEST_NAME},
    )
    contract = _contract(root)
    if manifest["task_contract_sha256"] != contract["task_contract_sha256"]:
        raise M96PosePilotError("task contract drifted from frozen development sources")
    tasks = _read_tasks(snapshots["tasks.jsonl"])
    if tasks != contract["tasks"] or len(tasks) != TASK_COUNT:
        raise M96PosePilotError("task rows drifted from deterministic replay")
    forbidden_task_keys = {
        "bbox_px",
        "bbox_normalized",
        "keypoints",
        "pose",
        "confidence",
        "x_normalized",
        "y_normalized",
        "x_px",
        "y_px",
    }
    if any(forbidden_task_keys.intersection(task) for task in tasks):
        raise M96PosePilotError("task rows disclose model coordinates or values")
    protocol = _parse_json(snapshots["protocol.json"], name="protocol")
    expected_protocol = _protocol(contract)
    expected_protocol["tasks_raw_sha256"] = _sha256(snapshots["tasks.jsonl"])
    if protocol != expected_protocol:
        raise M96PosePilotError("immutable pilot protocol drifted")
    if _parse_json(snapshots["field-contract.json"], name="field contract") != _field_contract():
        raise M96PosePilotError("field contract drifted")
    _validate_governance(
        snapshots["governance.template.csv"],
        [item["video_id"] for item in contract["video_bindings"]],
    )
    validation = _parse_json(snapshots["validation-report.json"], name="validation report")
    if (
        validation.get("status") != PILOT_STATUS
        or validation.get("task_contract_sha256") != contract["task_contract_sha256"]
        or validation.get("counts", {}).get("human_annotation_rows") != 0
        or validation.get("counts", {}).get("accuracy_metrics") != 0
        or validation.get("safety") != SAFETY
    ):
        raise M96PosePilotError("blank-pilot validation report drifted")
    if not isinstance(manifest["roles"], list) or [item.get("role_slot") for item in manifest["roles"]] != ["A", "B"]:
        raise M96PosePilotError("pilot role list drifted")
    roles: dict[str, dict[str, Any]] = {}
    for summary in manifest["roles"]:
        if not isinstance(summary, dict) or set(summary) != {
            "role_slot",
            "bundle_id",
            "manifest_path",
            "manifest_raw_sha256",
            "revision_sha256",
        }:
            raise M96PosePilotError("pilot role summary keys are not exact")
        slot = summary["role_slot"]
        role_manifest, role_snapshots = _validate_role_directory(
            bundle / f"annotator-{slot}",
            expected_slot=slot,
            expected_kind="independent_annotation",
        )
        role_manifest_raw = _snapshot(
            bundle / Path(*summary["manifest_path"].split("/")),
            name=f"annotator {slot} manifest",
        )
        if (
            role_manifest["bundle_id"] != summary["bundle_id"]
            or role_manifest["revision_sha256"] != summary["revision_sha256"]
            or _sha256(role_manifest_raw) != summary["manifest_raw_sha256"]
            or role_manifest["task_contract_sha256"] != contract["task_contract_sha256"]
            or role_manifest["protocol_sha256"] != _sha256(snapshots["protocol.json"])
            or role_manifest["task_count"] != TASK_COUNT
            or role_manifest["submission_fields"] != list(ANNOTATION_FIELDS)
        ):
            raise M96PosePilotError(f"annotator {slot} role binding drifted")
        bootstrap = _extract_bootstrap(role_snapshots[ENTRYPOINT])
        if bootstrap["tasks"] != tasks or bootstrap["excluded_reviewer_ids"] != []:
            raise M96PosePilotError(f"annotator {slot} task isolation drifted")
        expected_videos = {
            item["video_id"]: "/" + item["video_relative_path"]
            for item in contract["video_bindings"]
        }
        if bootstrap["videos"] != expected_videos:
            raise M96PosePilotError(f"annotator {slot} video binding drifted")
        roles[slot] = role_manifest
    if roles["A"]["bundle_id"] == roles["B"]["bundle_id"]:
        raise M96PosePilotError("A and B role bundle IDs must be distinct")
    return {
        "manifest": manifest,
        "manifest_raw_sha256": _sha256(manifest_raw),
        "snapshots": snapshots,
        "tasks": tasks,
        "roles": roles,
        "contract": contract,
    }


def _parse_csv(raw: bytes, *, fields: tuple[str, ...], name: str) -> list[dict[str, str]]:
    if b"\x00" in raw:
        raise M96PosePilotError(f"{name} contains a NUL byte")
    try:
        text = raw.decode("utf-8-sig")
        values = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise M96PosePilotError(f"{name} is not valid UTF-8 CSV") from exc
    if not values or tuple(values[0]) != fields:
        raise M96PosePilotError(f"{name} header does not match the exact role contract")
    if len(set(values[0])) != len(values[0]):
        raise M96PosePilotError(f"{name} repeats a header field")
    rows: list[dict[str, str]] = []
    for number, values_row in enumerate(values[1:], start=2):
        if not values_row or all(value == "" for value in values_row):
            raise M96PosePilotError(f"{name} contains a blank row at line {number}")
        if len(values_row) != len(fields):
            raise M96PosePilotError(f"{name} row width is invalid at line {number}")
        rows.append(dict(zip(fields, values_row, strict=True)))
    return rows


def _validate_coordinate(value: str, *, name: str) -> Decimal:
    if _COORD_RE.fullmatch(value) is None:
        raise M96PosePilotError(f"{name} must be a canonical decimal in [0,1)")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise M96PosePilotError(f"{name} is invalid") from exc
    if not number.is_finite() or number < 0 or number >= 1:
        raise M96PosePilotError(f"{name} must be in [0,1)")
    return number


def _validate_point(row: Mapping[str, str], *, prefix: str) -> None:
    visible = row["visible"]
    if visible not in {"true", "false"}:
        raise M96PosePilotError(f"{prefix} visible must be true or false")
    if visible == "true":
        _validate_coordinate(row["x_normalized"], name=f"{prefix} x_normalized")
        _validate_coordinate(row["y_normalized"], name=f"{prefix} y_normalized")
        if row["visibility_reason"]:
            raise M96PosePilotError(f"{prefix} visible row must have a blank reason")
    else:
        if row["x_normalized"] or row["y_normalized"]:
            raise M96PosePilotError(f"{prefix} invisible row must have blank coordinates")
        reason = row["visibility_reason"]
        if not reason or reason != reason.strip() or len(reason) > 256:
            raise M96PosePilotError(f"{prefix} invisible reason is invalid")


def _annotation_submission_bytes(
    raw: bytes,
    *,
    role_manifest: Mapping[str, Any],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    slot = str(role_manifest["role_slot"])
    if slot not in {"A", "B"}:
        raise M96PosePilotError("annotation submission requires role A or B")
    rows = _parse_csv(raw, fields=ANNOTATION_FIELDS, name=f"annotator {slot} submission")
    if len(rows) != len(tasks):
        raise M96PosePilotError(
            f"annotator {slot} submission must contain exactly {len(tasks)} task rows"
        )
    task_by_id = {task["task_id"]: task for task in tasks}
    by_task: dict[str, dict[str, str]] = {}
    annotator_key: str | None = None
    annotator_id: str | None = None
    annotation_ids: set[str] = set()
    for number, row in enumerate(rows, start=2):
        prefix = f"annotator {slot} CSV line {number}"
        task = task_by_id.get(row["task_id"])
        if task is None or row["task_id"] in by_task:
            raise M96PosePilotError(f"{prefix} has an unknown or duplicate task_id")
        expected = {
            "schema_version": SCHEMA_VERSION,
            "submission_version": ANNOTATION_SUBMISSION_VERSION,
            "bundle_id": role_manifest["bundle_id"],
            "role_slot": slot,
            "task_contract_sha256": role_manifest["task_contract_sha256"],
            "task_id": task["task_id"],
            "frame_id": task["frame_id"],
            "video_id": task["video_id"],
            "source_frame_index": str(task["source_frame_index"]),
            "joint_index": str(task["joint_index"]),
            "joint_name": task["joint_name"],
        }
        if any(row[field] != value for field, value in expected.items()):
            raise M96PosePilotError(f"{prefix} task or role binding drifted")
        record_id = _require_record_id(row["annotation_id"], name=f"{prefix} annotation_id")
        if record_id in annotation_ids:
            raise M96PosePilotError(f"{prefix} repeats annotation_id")
        annotation_ids.add(record_id)
        current_key = _identity_key(row["annotator_id"], name=f"{prefix} annotator_id")
        if annotator_key is None:
            annotator_key = current_key
            annotator_id = row["annotator_id"]
        elif current_key != annotator_key or row["annotator_id"] != annotator_id:
            raise M96PosePilotError(f"annotator {slot} submission mixes role IDs")
        _validate_point(row, prefix=prefix)
        _require_utc(row["annotated_at"], name=f"{prefix} annotated_at")
        by_task[row["task_id"]] = row
    if set(by_task) != set(task_by_id):
        raise M96PosePilotError(f"annotator {slot} submission coverage is incomplete")
    ordered = [by_task[task["task_id"]] for task in tasks]
    revision_payload = {
        "submission_version": ANNOTATION_SUBMISSION_VERSION,
        "bundle_id": role_manifest["bundle_id"],
        "role_slot": slot,
        "task_contract_sha256": role_manifest["task_contract_sha256"],
        "annotator_id": annotator_id,
        "rows": ordered,
    }
    return {
        "role_slot": slot,
        "annotator_id": annotator_id,
        "identity_key": annotator_key,
        "rows": ordered,
        "by_task": by_task,
        "raw_sha256": _sha256(raw),
        "submission_revision_sha256": _sha256(_canonical_bytes(revision_payload)),
    }


def validate_m96_annotation_submission(
    workspace: str | Path,
    pilot_dir: str | Path,
    role_slot: str,
    submission_path: str | Path,
) -> dict[str, Any]:
    if role_slot not in {"A", "B"}:
        raise M96PosePilotError("role_slot must be A or B")
    pilot = validate_m96_pose_pilot(workspace, pilot_dir)
    raw = _snapshot(Path(submission_path), name=f"annotator {role_slot} raw CSV")
    return _annotation_submission_bytes(
        raw,
        role_manifest=pilot["roles"][role_slot],
        tasks=pilot["tasks"],
    )


def _semantic_point(row: Mapping[str, str]) -> tuple[Any, ...]:
    if row["visible"] == "false":
        return (False, row["visibility_reason"])
    return (
        True,
        _validate_coordinate(row["x_normalized"], name="x_normalized").normalize(),
        _validate_coordinate(row["y_normalized"], name="y_normalized").normalize(),
    )


def _adjudication_role(
    *,
    bundle_id: str,
    contract_sha256: str,
    protocol_sha256: str,
    video_sources: list[dict[str, Any]],
    disagreement_tasks: list[dict[str, Any]],
    source_ids: list[str],
    generated_at: str,
    js_raw: bytes,
    css_raw: bytes,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    bootstrap = {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "role_slot": "C",
        "role_kind": "disagreement_only_adjudicator",
        "task_contract_sha256": contract_sha256,
        "submission_schema_version": SCHEMA_VERSION,
        "submission_version": ADJUDICATION_SUBMISSION_VERSION,
        "fields": list(ADJUDICATION_FIELDS),
        "videos": {item["video_id"]: "/" + item["relative_path"] for item in video_sources},
        "tasks": disagreement_tasks,
        "excluded_reviewer_ids": source_ids,
        "safety": SAFETY,
    }
    artifacts_raw = {
        ENTRYPOINT: _html(bootstrap),
        WORKBENCH_JS: js_raw,
        WORKBENCH_CSS: css_raw,
    }
    artifacts = _artifact_records(artifacts_raw)
    revision_payload = {
        "bundle_version": ADJUDICATION_BUNDLE_VERSION,
        "bundle_id": bundle_id,
        "role_slot": "C",
        "task_contract_sha256": contract_sha256,
        "protocol_sha256": protocol_sha256,
        "video_sources": video_sources,
        "disagreement_task_ids": [task["task_id"] for task in disagreement_tasks],
        "source_role_ids": source_ids,
        "artifacts": artifacts,
        "safety": SAFETY,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_version": ADJUDICATION_BUNDLE_VERSION,
        "bundle_kind": "disagreement_adjudication",
        "status": "ready_for_C_after_complete_A_and_B_intake",
        "bundle_id": bundle_id,
        "role_slot": "C",
        "role_kind": "disagreement_only_adjudicator",
        "generated_at": generated_at,
        "entrypoint": ENTRYPOINT,
        "task_contract_sha256": contract_sha256,
        "protocol_sha256": protocol_sha256,
        "task_count": len(disagreement_tasks),
        "submission_version": ADJUDICATION_SUBMISSION_VERSION,
        "submission_fields": list(ADJUDICATION_FIELDS),
        "video_sources": video_sources,
        "artifacts": artifacts,
        "content_root_sha256": _content_root(artifacts),
        "revision_sha256": _sha256(_canonical_bytes(revision_payload)),
        "safety": SAFETY,
    }
    return manifest, {**artifacts_raw, ROLE_MANIFEST_NAME: _pretty_bytes(manifest)}


def build_m96_adjudication_bundle(
    workspace: str | Path,
    pilot_dir: str | Path,
    annotator_a_csv: str | Path,
    annotator_b_csv: str | Path,
    output_dir: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    pilot_path = Path(pilot_dir).resolve()
    pilot = validate_m96_pose_pilot(root, pilot_path)
    timestamp = _require_utc(generated_at or _now_utc(), name="generated_at")
    raw_a = _snapshot(Path(annotator_a_csv), name="annotator A raw CSV")
    raw_b = _snapshot(Path(annotator_b_csv), name="annotator B raw CSV")
    submission_a = _annotation_submission_bytes(
        raw_a, role_manifest=pilot["roles"]["A"], tasks=pilot["tasks"]
    )
    submission_b = _annotation_submission_bytes(
        raw_b, role_manifest=pilot["roles"]["B"], tasks=pilot["tasks"]
    )
    if submission_a["identity_key"] == submission_b["identity_key"]:
        raise M96PosePilotError("annotator A and B role IDs must be distinct")

    disagreement_tasks: list[dict[str, Any]] = []
    for task in pilot["tasks"]:
        row_a = submission_a["by_task"][task["task_id"]]
        row_b = submission_b["by_task"][task["task_id"]]
        if _semantic_point(row_a) == _semantic_point(row_b):
            continue
        disagreement_tasks.append(
            {
                **task,
                "sources": [
                    {
                        "slot": "A",
                        "annotation_id": row_a["annotation_id"],
                        "visible": row_a["visible"],
                        "x_normalized": row_a["x_normalized"],
                        "y_normalized": row_a["y_normalized"],
                        "visibility_reason": row_a["visibility_reason"],
                    },
                    {
                        "slot": "B",
                        "annotation_id": row_b["annotation_id"],
                        "visible": row_b["visible"],
                        "x_normalized": row_b["x_normalized"],
                        "y_normalized": row_b["y_normalized"],
                        "visibility_reason": row_b["visibility_reason"],
                    },
                ],
            }
        )

    pilot_manifest_raw = _snapshot(pilot_path / PILOT_MANIFEST_NAME, name="pilot manifest source")
    role_a_manifest_raw = _snapshot(pilot_path / "annotator-A" / ROLE_MANIFEST_NAME, name="A role manifest source")
    role_b_manifest_raw = _snapshot(pilot_path / "annotator-B" / ROLE_MANIFEST_NAME, name="B role manifest source")
    tasks_raw = pilot["snapshots"]["tasks.jsonl"]
    protocol_raw = pilot["snapshots"]["protocol.json"]
    fields_raw = pilot["snapshots"]["field-contract.json"]
    bundle_id = "m96-adjudication-" + _sha256(
        _canonical_bytes(
            {
                "bundle_version": ADJUDICATION_BUNDLE_VERSION,
                "task_contract_sha256": pilot["manifest"]["task_contract_sha256"],
                "A_raw_sha256": submission_a["raw_sha256"],
                "A_revision_sha256": submission_a["submission_revision_sha256"],
                "B_raw_sha256": submission_b["raw_sha256"],
                "B_revision_sha256": submission_b["submission_revision_sha256"],
            }
        )
    )
    video_sources = pilot["roles"]["A"]["video_sources"]
    c_manifest, c_role_raw = _adjudication_role(
        bundle_id=bundle_id,
        contract_sha256=pilot["manifest"]["task_contract_sha256"],
        protocol_sha256=_sha256(protocol_raw),
        video_sources=video_sources,
        disagreement_tasks=disagreement_tasks,
        source_ids=[submission_a["annotator_id"], submission_b["annotator_id"]],
        generated_at=timestamp,
        js_raw=_asset(WORKBENCH_JS),
        css_raw=_asset(WORKBENCH_CSS),
    )
    raw_by_path: dict[str, bytes] = {
        "sources/pilot-manifest.json": pilot_manifest_raw,
        "sources/role-A-manifest.json": role_a_manifest_raw,
        "sources/role-B-manifest.json": role_b_manifest_raw,
        "sources/protocol.json": protocol_raw,
        "sources/tasks.jsonl": tasks_raw,
        "sources/field-contract.json": fields_raw,
        "submissions/annotator-A.csv": raw_a,
        "submissions/annotator-B.csv": raw_b,
    }
    raw_by_path.update({f"adjudicator-C/{path}": raw for path, raw in c_role_raw.items()})
    source_records = [
        {
            "role_slot": "A",
            "bundle_id": pilot["roles"]["A"]["bundle_id"],
            "annotator_id": submission_a["annotator_id"],
            "raw_path": "submissions/annotator-A.csv",
            "raw_sha256": submission_a["raw_sha256"],
            "submission_revision_sha256": submission_a["submission_revision_sha256"],
            "role_manifest_raw_sha256": _sha256(role_a_manifest_raw),
        },
        {
            "role_slot": "B",
            "bundle_id": pilot["roles"]["B"]["bundle_id"],
            "annotator_id": submission_b["annotator_id"],
            "raw_path": "submissions/annotator-B.csv",
            "raw_sha256": submission_b["raw_sha256"],
            "submission_revision_sha256": submission_b["submission_revision_sha256"],
            "role_manifest_raw_sha256": _sha256(role_b_manifest_raw),
        },
    ]
    validation = {
        "schema_version": SCHEMA_VERSION,
        "report_version": "m96-A-B-atomic-intake-validation-v1.0.0",
        "generated_at": timestamp,
        "status": "ready_for_C_after_complete_A_and_B_intake",
        "checks": {
            "A_complete": True,
            "B_complete": True,
            "A_B_task_contract_equal": True,
            "A_B_role_ids_distinct": True,
            "raw_bytes_preserved": True,
            "revisions_preserved": True,
            "C_contains_only_disagreements": True,
            "model_values_absent": True,
        },
        "counts": {
            "A_rows": TASK_COUNT,
            "B_rows": TASK_COUNT,
            "agreement_rows": TASK_COUNT - len(disagreement_tasks),
            "C_disagreement_tasks": len(disagreement_tasks),
            "accuracy_metrics": 0,
        },
        "sources": source_records,
        "safety": SAFETY,
    }
    raw_by_path["validation-report.json"] = _pretty_bytes(validation)
    artifacts = _artifact_records(raw_by_path)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_version": ADJUDICATION_BUNDLE_VERSION,
        "status": "ready_for_C_after_complete_A_and_B_intake",
        "generated_at": timestamp,
        "bundle_id": bundle_id,
        "task_contract_sha256": pilot["manifest"]["task_contract_sha256"],
        "pilot_source": {
            "manifest_raw_sha256": _sha256(pilot_manifest_raw),
            "protocol_raw_sha256": _sha256(protocol_raw),
            "tasks_raw_sha256": _sha256(tasks_raw),
        },
        "sources": source_records,
        "scope": {
            "source_task_count": TASK_COUNT,
            "agreement_count": TASK_COUNT - len(disagreement_tasks),
            "disagreement_count": len(disagreement_tasks),
        },
        "C_role": {
            "manifest_path": f"adjudicator-C/{ROLE_MANIFEST_NAME}",
            "manifest_raw_sha256": _sha256(c_role_raw[ROLE_MANIFEST_NAME]),
            "revision_sha256": c_manifest["revision_sha256"],
        },
        "artifacts": artifacts,
        "content_root_sha256": _content_root(artifacts),
        "safety": SAFETY,
    }
    raw_by_path[ADJUDICATION_MANIFEST_NAME] = _pretty_bytes(manifest)
    _atomic_publish(
        Path(output_dir),
        raw_by_path,
        validate_staging=lambda staging: validate_m96_adjudication_bundle(root, staging),
    )
    return validate_m96_adjudication_bundle(root, output_dir)["manifest"]


def _source_artifact_sha(manifest: Mapping[str, Any], path: str) -> str:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise M96PosePilotError("source manifest artifacts are missing")
    matches = [item for item in artifacts if isinstance(item, dict) and item.get("path") == path]
    if len(matches) != 1:
        raise M96PosePilotError(f"source manifest does not bind {path}")
    return _require_sha(matches[0].get("sha256"), name=f"source {path} SHA")


def validate_m96_adjudication_bundle(
    workspace: str | Path,
    bundle_dir: str | Path,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    bundle = Path(bundle_dir).resolve()
    if bundle.is_symlink() or not bundle.is_dir():
        raise M96PosePilotError("adjudication bundle must be a regular directory")
    manifest_raw = _snapshot(bundle / ADJUDICATION_MANIFEST_NAME, name="adjudication manifest")
    manifest = _parse_json(manifest_raw, name="adjudication manifest")
    expected_keys = {
        "schema_version",
        "bundle_version",
        "status",
        "generated_at",
        "bundle_id",
        "task_contract_sha256",
        "pilot_source",
        "sources",
        "scope",
        "C_role",
        "artifacts",
        "content_root_sha256",
        "safety",
    }
    if set(manifest) != expected_keys:
        raise M96PosePilotError("adjudication manifest keys are not exact")
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["bundle_version"] != ADJUDICATION_BUNDLE_VERSION
        or manifest["status"] != "ready_for_C_after_complete_A_and_B_intake"
        or manifest["safety"] != SAFETY
    ):
        raise M96PosePilotError("adjudication manifest identity or safety drifted")
    _require_utc(manifest["generated_at"], name="adjudication generated_at")
    _require_record_id(manifest["bundle_id"], name="adjudication bundle_id")
    _require_sha(manifest["task_contract_sha256"], name="task contract SHA")
    if manifest["content_root_sha256"] != _content_root(manifest["artifacts"]):
        raise M96PosePilotError("adjudication content root drifted")
    snapshots = _validate_artifact_tree(
        bundle,
        manifest["artifacts"],
        manifest_names={ADJUDICATION_MANIFEST_NAME},
    )
    pilot_manifest_raw = snapshots["sources/pilot-manifest.json"]
    protocol_raw = snapshots["sources/protocol.json"]
    tasks_raw = snapshots["sources/tasks.jsonl"]
    pilot_manifest = _parse_json(pilot_manifest_raw, name="source pilot manifest")
    protocol = _parse_json(protocol_raw, name="source protocol")
    tasks = _read_tasks(tasks_raw)
    if (
        manifest["pilot_source"]
        != {
            "manifest_raw_sha256": _sha256(pilot_manifest_raw),
            "protocol_raw_sha256": _sha256(protocol_raw),
            "tasks_raw_sha256": _sha256(tasks_raw),
        }
        or _source_artifact_sha(pilot_manifest, "protocol.json") != _sha256(protocol_raw)
        or _source_artifact_sha(pilot_manifest, "tasks.jsonl") != _sha256(tasks_raw)
        or protocol.get("tasks_raw_sha256") != _sha256(tasks_raw)
        or protocol.get("task_contract_sha256") != manifest["task_contract_sha256"]
        or pilot_manifest.get("task_contract_sha256") != manifest["task_contract_sha256"]
        or len(tasks) != TASK_COUNT
    ):
        raise M96PosePilotError("adjudication pilot source binding drifted")

    submissions: dict[str, dict[str, Any]] = {}
    if not isinstance(manifest["sources"], list) or [item.get("role_slot") for item in manifest["sources"]] != ["A", "B"]:
        raise M96PosePilotError("adjudication source order drifted")
    for source in manifest["sources"]:
        if not isinstance(source, dict) or set(source) != {
            "role_slot",
            "bundle_id",
            "annotator_id",
            "raw_path",
            "raw_sha256",
            "submission_revision_sha256",
            "role_manifest_raw_sha256",
        }:
            raise M96PosePilotError("adjudication source keys are not exact")
        slot = source["role_slot"]
        role_raw = snapshots[f"sources/role-{slot}-manifest.json"]
        role_manifest = _parse_json(role_raw, name=f"source role {slot} manifest")
        _validate_role_manifest_shape(
            role_manifest, expected_slot=slot, expected_kind="independent_annotation"
        )
        raw_path = _safe_relative(source["raw_path"], name=f"source {slot} raw path")
        raw = snapshots.get(raw_path)
        if raw is None:
            raise M96PosePilotError(f"source {slot} raw CSV path is not an artifact")
        submission = _annotation_submission_bytes(raw, role_manifest=role_manifest, tasks=tasks)
        if (
            source["bundle_id"] != role_manifest["bundle_id"]
            or source["annotator_id"] != submission["annotator_id"]
            or source["raw_sha256"] != submission["raw_sha256"]
            or source["submission_revision_sha256"] != submission["submission_revision_sha256"]
            or source["role_manifest_raw_sha256"] != _sha256(role_raw)
        ):
            raise M96PosePilotError(f"source {slot} raw bytes or revision drifted")
        submissions[slot] = submission
    if submissions["A"]["identity_key"] == submissions["B"]["identity_key"]:
        raise M96PosePilotError("annotator A and B role IDs must be distinct")

    expected_disagreements: list[dict[str, Any]] = []
    for task in tasks:
        row_a = submissions["A"]["by_task"][task["task_id"]]
        row_b = submissions["B"]["by_task"][task["task_id"]]
        if _semantic_point(row_a) == _semantic_point(row_b):
            continue
        expected_disagreements.append(
            {
                **task,
                "sources": [
                    {
                        "slot": "A",
                        "annotation_id": row_a["annotation_id"],
                        "visible": row_a["visible"],
                        "x_normalized": row_a["x_normalized"],
                        "y_normalized": row_a["y_normalized"],
                        "visibility_reason": row_a["visibility_reason"],
                    },
                    {
                        "slot": "B",
                        "annotation_id": row_b["annotation_id"],
                        "visible": row_b["visible"],
                        "x_normalized": row_b["x_normalized"],
                        "y_normalized": row_b["y_normalized"],
                        "visibility_reason": row_b["visibility_reason"],
                    },
                ],
            }
        )
    scope = {
        "source_task_count": TASK_COUNT,
        "agreement_count": TASK_COUNT - len(expected_disagreements),
        "disagreement_count": len(expected_disagreements),
    }
    if manifest["scope"] != scope:
        raise M96PosePilotError("adjudication disagreement scope drifted")
    c_manifest, c_snapshots = _validate_role_directory(
        bundle / "adjudicator-C",
        expected_slot="C",
        expected_kind="disagreement_adjudication",
    )
    c_manifest_raw = _snapshot(bundle / "adjudicator-C" / ROLE_MANIFEST_NAME, name="C role manifest")
    if (
        c_manifest["bundle_id"] != manifest["bundle_id"]
        or c_manifest["task_contract_sha256"] != manifest["task_contract_sha256"]
        or c_manifest["protocol_sha256"] != _sha256(protocol_raw)
        or c_manifest["task_count"] != len(expected_disagreements)
        or c_manifest["submission_version"] != ADJUDICATION_SUBMISSION_VERSION
        or c_manifest["submission_fields"] != list(ADJUDICATION_FIELDS)
        or manifest["C_role"]
        != {
            "manifest_path": f"adjudicator-C/{ROLE_MANIFEST_NAME}",
            "manifest_raw_sha256": _sha256(c_manifest_raw),
            "revision_sha256": c_manifest["revision_sha256"],
        }
    ):
        raise M96PosePilotError("C role binding drifted")
    c_bootstrap = _extract_bootstrap(c_snapshots[ENTRYPOINT])
    if (
        c_bootstrap["tasks"] != expected_disagreements
        or c_bootstrap["excluded_reviewer_ids"]
        != [submissions["A"]["annotator_id"], submissions["B"]["annotator_id"]]
    ):
        raise M96PosePilotError("C workbench exposes something other than necessary A/B disagreements")
    validation = _parse_json(snapshots["validation-report.json"], name="adjudication validation")
    if (
        validation.get("status") != manifest["status"]
        or validation.get("counts", {}).get("C_disagreement_tasks") != len(expected_disagreements)
        or validation.get("counts", {}).get("accuracy_metrics") != 0
        or validation.get("sources") != manifest["sources"]
        or validation.get("safety") != SAFETY
    ):
        raise M96PosePilotError("adjudication validation report drifted")
    for video in c_manifest["video_sources"]:
        path = root / Path(*video["relative_path"].split("/"))
        raw = _snapshot(path, name=f"C video {video['video_id']}")
        if len(raw) != video["bytes"] or _sha256(raw) != video["raw_sha256"]:
            raise M96PosePilotError(f"C video binding drifted: {video['video_id']}")
    return {
        "manifest": manifest,
        "manifest_raw_sha256": _sha256(manifest_raw),
        "snapshots": snapshots,
        "tasks": tasks,
        "disagreement_tasks": expected_disagreements,
        "submissions": submissions,
        "C_role": c_manifest,
    }


def _adjudication_submission_bytes(
    raw: bytes,
    *,
    c_manifest: Mapping[str, Any],
    disagreement_tasks: list[dict[str, Any]],
    excluded_ids: list[str],
) -> dict[str, Any]:
    rows = _parse_csv(raw, fields=ADJUDICATION_FIELDS, name="reviewer C submission")
    if len(rows) != len(disagreement_tasks):
        raise M96PosePilotError(
            f"reviewer C submission must contain exactly {len(disagreement_tasks)} disagreement rows"
        )
    task_by_id = {task["task_id"]: task for task in disagreement_tasks}
    by_task: dict[str, dict[str, str]] = {}
    reviewer_id: str | None = None
    reviewer_key: str | None = None
    record_ids: set[str] = set()
    excluded_keys = {_identity_key(value, name="source annotator ID") for value in excluded_ids}
    for number, row in enumerate(rows, start=2):
        prefix = f"reviewer C CSV line {number}"
        task = task_by_id.get(row["task_id"])
        if task is None or row["task_id"] in by_task:
            raise M96PosePilotError(f"{prefix} has an unknown or duplicate task_id")
        expected = {
            "schema_version": SCHEMA_VERSION,
            "submission_version": ADJUDICATION_SUBMISSION_VERSION,
            "adjudication_bundle_id": c_manifest["bundle_id"],
            "task_contract_sha256": c_manifest["task_contract_sha256"],
            "task_id": task["task_id"],
            "source_annotation_a_id": task["sources"][0]["annotation_id"],
            "source_annotation_b_id": task["sources"][1]["annotation_id"],
        }
        if any(row[field] != value for field, value in expected.items()):
            raise M96PosePilotError(f"{prefix} task, source, or bundle binding drifted")
        record_id = _require_record_id(row["adjudication_id"], name=f"{prefix} adjudication_id")
        if record_id in record_ids:
            raise M96PosePilotError(f"{prefix} repeats adjudication_id")
        record_ids.add(record_id)
        current_key = _identity_key(row["reviewer_id"], name=f"{prefix} reviewer_id")
        if current_key in excluded_keys:
            raise M96PosePilotError("reviewer C role ID must differ from annotators A and B")
        if reviewer_key is None:
            reviewer_key = current_key
            reviewer_id = row["reviewer_id"]
        elif current_key != reviewer_key or row["reviewer_id"] != reviewer_id:
            raise M96PosePilotError("reviewer C submission mixes role IDs")
        _validate_point(row, prefix=prefix)
        _require_utc(row["adjudicated_at"], name=f"{prefix} adjudicated_at")
        by_task[row["task_id"]] = row
    ordered = [by_task[task["task_id"]] for task in disagreement_tasks]
    revision_payload = {
        "submission_version": ADJUDICATION_SUBMISSION_VERSION,
        "bundle_id": c_manifest["bundle_id"],
        "task_contract_sha256": c_manifest["task_contract_sha256"],
        "reviewer_id": reviewer_id,
        "rows": ordered,
    }
    return {
        "reviewer_id": reviewer_id,
        "rows": ordered,
        "by_task": by_task,
        "raw_sha256": _sha256(raw),
        "submission_revision_sha256": _sha256(_canonical_bytes(revision_payload)),
        "status": "valid_complete_C_submission" if ordered else "valid_no_disagreements_no_C_decisions_required",
    }


def validate_m96_adjudication_submission(
    workspace: str | Path,
    adjudication_bundle_dir: str | Path,
    submission_path: str | Path,
) -> dict[str, Any]:
    bundle = validate_m96_adjudication_bundle(workspace, adjudication_bundle_dir)
    raw = _snapshot(Path(submission_path), name="reviewer C raw CSV")
    return _adjudication_submission_bytes(
        raw,
        c_manifest=bundle["C_role"],
        disagreement_tasks=bundle["disagreement_tasks"],
        excluded_ids=[
            bundle["submissions"]["A"]["annotator_id"],
            bundle["submissions"]["B"]["annotator_id"],
        ],
    )


def role_service_contract(
    workspace: str | Path,
    bundle_dir: str | Path,
    role_slot: str,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    bundle = Path(bundle_dir).resolve()
    if role_slot in {"A", "B"}:
        validated = validate_m96_pose_pilot(root, bundle)
        role_dir = bundle / f"annotator-{role_slot}"
        role_manifest = validated["roles"][role_slot]
    elif role_slot == "C":
        validated = validate_m96_adjudication_bundle(root, bundle)
        role_dir = bundle / "adjudicator-C"
        role_manifest = validated["C_role"]
    else:
        raise M96PosePilotError("role must be A, B, or C")
    try:
        role_relative = role_dir.relative_to(root).as_posix()
    except ValueError as exc:
        raise M96PosePilotError("served bundle must be inside the workspace") from exc
    allowed = [
        f"{role_relative}/{ENTRYPOINT}",
        f"{role_relative}/{WORKBENCH_JS}",
        f"{role_relative}/{WORKBENCH_CSS}",
        f"{role_relative}/{ROLE_MANIFEST_NAME}",
    ]
    for video in role_manifest["video_sources"]:
        relative = _safe_relative(video["relative_path"], name="served video path")
        raw = _snapshot(root / Path(*relative.split("/")), name=f"served video {video['video_id']}")
        if len(raw) != video["bytes"] or _sha256(raw) != video["raw_sha256"]:
            raise M96PosePilotError(f"served video binding drifted: {video['video_id']}")
        allowed.append(relative)
    if len(set(allowed)) != len(allowed):
        raise M96PosePilotError("service allowlist contains duplicates")
    return {
        "workspace": str(root),
        "bundle": str(bundle),
        "role_slot": role_slot,
        "bundle_id": role_manifest["bundle_id"],
        "entrypoint": "/" + allowed[0],
        "allowed_relative_paths": allowed,
    }
