from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from rallymate_evaluation.pose_diagnostic_review import (
    DIAGNOSTIC_FLAGS,
    PoseDiagnosticReviewError,
    validate_pose_diagnostic_review_queue,
    validate_pose_diagnostic_review_queue_sources,
)


TRUTH_PACK_VERSION = "pose-diagnostic-truth-pack-v1.0.0"
EVALUATION_VERSION = "pose-diagnostic-evaluation-v1.0.0"
MATCHING_PROTOCOL = "exact-source-frame-and-diagnostic-scope-v1"
DIAGNOSTIC_TYPES = tuple(sorted(DIAGNOSTIC_FLAGS))


class PoseDiagnosticTruthError(ValueError):
    """Raised when diagnostic truth or its source binding is unsafe."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PoseDiagnosticTruthError(
                f"{path.name} line {line_number} is invalid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise PoseDiagnosticTruthError(
                f"{path.name} line {line_number} must be an object"
            )
        records.append(record)
    return records


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def coverage_csv_header() -> list[str]:
    return [
        "coverage_id",
        "video_id",
        "diagnostic_type",
        "start_source_frame_index",
        "end_source_frame_index",
        "coverage_scope",
        "review_status",
        "annotator_ids",
        "adjudicator_id",
        "adjudicated_at",
        "null_reason",
        "notes",
    ]


def positives_csv_header() -> list[str]:
    return [
        "truth_id",
        "video_id",
        "diagnostic_type",
        "source_frame_index",
        "joint",
        "left_joint",
        "right_joint",
        "from_source_track_id",
        "to_source_track_id",
        "review_status",
        "annotator_ids",
        "adjudicator_id",
        "adjudicated_at",
        "notes",
    ]


def _timeline_from_queue(queue: Mapping[str, Any]) -> tuple[Path, list[dict[str, Any]]]:
    source = queue.get("source")
    artifacts = source.get("artifacts") if isinstance(source, Mapping) else None
    binding = (
        artifacts.get("primary_player_jsonl")
        if isinstance(artifacts, Mapping)
        else None
    )
    if not isinstance(binding, Mapping) or not isinstance(binding.get("path"), str):
        raise PoseDiagnosticTruthError("queue primary-player binding is missing")
    path = Path(binding["path"])
    records = _load_jsonl(path)
    records.sort(key=lambda item: int(item.get("processed_index", -1)))
    source_indexes: set[int] = set()
    previous_processed = -1
    for record in records:
        source_index = record.get("source_frame_index")
        processed_index = record.get("processed_index")
        timestamp_ms = record.get("timestamp_ms")
        if not all(
            isinstance(value, int)
            for value in (source_index, processed_index, timestamp_ms)
        ):
            raise PoseDiagnosticTruthError("primary timeline frame fields must be integers")
        if source_index in source_indexes or processed_index <= previous_processed:
            raise PoseDiagnosticTruthError("primary timeline order/index invariant failed")
        source_indexes.add(source_index)
        previous_processed = processed_index
    if not records:
        raise PoseDiagnosticTruthError("primary timeline cannot be empty")
    return path, records


def _blank_csv(headers: list[str], rows: Iterable[Mapping[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def build_pose_diagnostic_truth_pack_manifest(
    *,
    queue: Mapping[str, Any],
    queue_path: Path,
    coverage_path: Path,
    positives_path: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a blank, source-bound truth pack without inventing annotations."""

    validate_pose_diagnostic_review_queue(queue)
    validate_pose_diagnostic_review_queue_sources(queue)
    queue_path = queue_path.resolve()
    if not queue_path.is_file():
        raise PoseDiagnosticTruthError("queue file is missing")
    serialized_queue = json.loads(queue_path.read_text(encoding="utf-8"))
    if serialized_queue != queue:
        raise PoseDiagnosticTruthError("queue mapping differs from the bound queue file")
    timeline_path, timeline = _timeline_from_queue(queue)
    source_indexes = [item["source_frame_index"] for item in timeline]
    timestamps = [item["timestamp_ms"] for item in timeline]
    manifest = {
        "schema_version": "1.0.0",
        "pack_version": TRUTH_PACK_VERSION,
        "generated_at": generated_at or _utc_now(),
        "status": "annotation_required",
        "video_id": queue["video_id"],
        "source_queue": {
            "path": str(queue_path),
            "sha256": _sha256(queue_path),
            "queue_version": queue["queue_version"],
            "artifact_binding_sha256": queue["source"]["artifact_binding_sha256"],
            "candidate_task_count": queue["counts"]["tasks"],
        },
        "primary_timeline": {
            "path": str(timeline_path.resolve()),
            "sha256": _sha256(timeline_path),
            "selection_algorithm_version": queue["source"]["pose_model"].get(
                "primary_player"
            ),
        },
        "review_media": dict(queue["review_media"]),
        "frame_domain": {
            "frame_count": len(source_indexes),
            "first_source_frame_index": source_indexes[0],
            "last_source_frame_index": source_indexes[-1],
            "start_timestamp_ms": timestamps[0],
            "end_timestamp_ms": timestamps[-1],
            "source_frame_indexes_sha256": _canonical_hash(source_indexes),
        },
        "diagnostic_types": list(DIAGNOSTIC_TYPES),
        "matching_protocol": {
            "version": MATCHING_PROTOCOL,
            "frame_tolerance": 0,
            "keypoint_jump_scope": "exact_joint",
            "left_right_swap_scope": "exact_ordered_joint_pair",
            "source_track_switch_scope": "exact_frame",
            "primary_identity_ambiguity_scope": "exact_frame",
        },
        "annotation_files": {
            "coverage": {
                "path": str(coverage_path.resolve()),
                "template_sha256": _sha256(coverage_path),
            },
            "positives": {
                "path": str(positives_path.resolve()),
                "template_sha256": _sha256(positives_path),
            },
        },
        "truth_contract": {
            "coverage_semantics": (
                "accepted all_model_relevant_scopes coverage makes absence of a sparse "
                "positive an explicit negative only inside that interval"
            ),
            "minimum_independent_annotators": 2,
            "independent_adjudicator_required": True,
            "candidate_only_review_is_recall_truth": False,
        },
        "safety": {
            "manual_truth_present": False,
            "diagnostic_metrics_computed": False,
            "quality_gate_modified": False,
            "formal_scoring_accuracy_claim": False,
            "grades_generated": False,
            "thresholds_generated": False,
        },
    }
    validate_pose_diagnostic_truth_pack_manifest(manifest)
    return manifest


def validate_pose_diagnostic_truth_pack_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != "1.0.0":
        raise PoseDiagnosticTruthError("unsupported truth pack schema_version")
    if manifest.get("pack_version") != TRUTH_PACK_VERSION:
        raise PoseDiagnosticTruthError("unsupported truth pack version")
    if manifest.get("status") != "annotation_required":
        raise PoseDiagnosticTruthError("blank truth pack status must be annotation_required")
    if manifest.get("diagnostic_types") != list(DIAGNOSTIC_TYPES):
        raise PoseDiagnosticTruthError("truth pack diagnostic type set mismatch")
    matching = manifest.get("matching_protocol")
    if not isinstance(matching, Mapping) or matching.get("version") != MATCHING_PROTOCOL:
        raise PoseDiagnosticTruthError("truth pack matching protocol mismatch")
    if matching.get("frame_tolerance") != 0:
        raise PoseDiagnosticTruthError("v1 diagnostic matching must use exact frames")
    domain = manifest.get("frame_domain")
    if not isinstance(domain, Mapping) or not isinstance(domain.get("frame_count"), int):
        raise PoseDiagnosticTruthError("truth pack frame domain missing")
    if domain["frame_count"] <= 0:
        raise PoseDiagnosticTruthError("truth pack frame domain cannot be empty")
    safety = manifest.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(field) is not False
        for field in (
            "manual_truth_present",
            "diagnostic_metrics_computed",
            "quality_gate_modified",
            "formal_scoring_accuracy_claim",
            "grades_generated",
            "thresholds_generated",
        )
    ):
        raise PoseDiagnosticTruthError("blank truth pack contains unsafe claims")


def validate_pose_diagnostic_truth_pack_sources(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    validate_pose_diagnostic_truth_pack_manifest(manifest)
    source_queue = manifest.get("source_queue")
    if not isinstance(source_queue, Mapping):
        raise PoseDiagnosticTruthError("source queue binding missing")
    queue_path = Path(str(source_queue.get("path", "")))
    if not queue_path.is_file() or _sha256(queue_path) != source_queue.get("sha256"):
        raise PoseDiagnosticTruthError("source queue SHA mismatch")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    validate_pose_diagnostic_review_queue(queue)
    if queue.get("video_id") != manifest.get("video_id"):
        raise PoseDiagnosticTruthError("truth pack video_id differs from queue")
    if queue.get("queue_version") != source_queue.get("queue_version"):
        raise PoseDiagnosticTruthError("truth pack queue version mismatch")
    if (
        queue.get("source", {}).get("artifact_binding_sha256")
        != source_queue.get("artifact_binding_sha256")
    ):
        raise PoseDiagnosticTruthError("truth pack artifact binding mismatch")
    source_validation = validate_pose_diagnostic_review_queue_sources(queue)
    timeline_path, timeline = _timeline_from_queue(queue)
    timeline_binding = manifest.get("primary_timeline")
    if not isinstance(timeline_binding, Mapping):
        raise PoseDiagnosticTruthError("truth pack primary timeline binding missing")
    if (
        str(timeline_path.resolve()) != timeline_binding.get("path")
        or _sha256(timeline_path) != timeline_binding.get("sha256")
    ):
        raise PoseDiagnosticTruthError("truth pack primary timeline SHA/path mismatch")
    source_indexes = [item["source_frame_index"] for item in timeline]
    timestamps = [item["timestamp_ms"] for item in timeline]
    expected_domain = {
        "frame_count": len(source_indexes),
        "first_source_frame_index": source_indexes[0],
        "last_source_frame_index": source_indexes[-1],
        "start_timestamp_ms": timestamps[0],
        "end_timestamp_ms": timestamps[-1],
        "source_frame_indexes_sha256": _canonical_hash(source_indexes),
    }
    if manifest.get("frame_domain") != expected_domain:
        raise PoseDiagnosticTruthError("truth pack frame domain binding mismatch")
    return queue, timeline


def _review_metadata(row: Mapping[str, str], *, line_number: int) -> tuple[list[str], str]:
    annotators = [item.strip() for item in row["annotator_ids"].split(";") if item.strip()]
    if len(set(annotators)) < 2 or len(annotators) != len(set(annotators)):
        raise PoseDiagnosticTruthError(
            f"CSV line {line_number} requires at least two unique annotator_ids"
        )
    adjudicator = row["adjudicator_id"].strip()
    if not adjudicator or adjudicator in annotators:
        raise PoseDiagnosticTruthError(
            f"CSV line {line_number} requires an independent adjudicator_id"
        )
    timestamp = row["adjudicated_at"].strip()
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PoseDiagnosticTruthError(
            f"CSV line {line_number} adjudicated_at is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise PoseDiagnosticTruthError(
            f"CSV line {line_number} adjudicated_at must include a timezone"
        )
    return sorted(annotators), adjudicator


def _read_csv(text: str, expected_header: list[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames != expected_header:
        raise PoseDiagnosticTruthError("CSV header mismatch")
    return [dict(row) for row in reader]


def _parse_int(value: str, *, field: str, line_number: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise PoseDiagnosticTruthError(
            f"CSV line {line_number} {field} must be an integer"
        ) from exc
    if parsed < 0:
        raise PoseDiagnosticTruthError(
            f"CSV line {line_number} {field} must be non-negative"
        )
    return parsed


def _parse_coverage(
    text: str,
    *,
    video_id: str,
    source_indexes: set[int],
) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    ids: set[str] = set()
    for line_number, row in enumerate(
        _read_csv(text, coverage_csv_header()), start=2
    ):
        coverage_id = row["coverage_id"].strip()
        if not coverage_id or coverage_id in ids:
            raise PoseDiagnosticTruthError(
                f"coverage CSV line {line_number} id missing or duplicate"
            )
        ids.add(coverage_id)
        if row["video_id"] != video_id:
            raise PoseDiagnosticTruthError(
                f"coverage CSV line {line_number} video_id mismatch"
            )
        diagnostic_type = row["diagnostic_type"]
        if diagnostic_type not in DIAGNOSTIC_TYPES:
            raise PoseDiagnosticTruthError(
                f"coverage CSV line {line_number} diagnostic_type invalid"
            )
        start = _parse_int(
            row["start_source_frame_index"],
            field="start_source_frame_index",
            line_number=line_number,
        )
        end = _parse_int(
            row["end_source_frame_index"],
            field="end_source_frame_index",
            line_number=line_number,
        )
        if start > end or start not in source_indexes or end not in source_indexes:
            raise PoseDiagnosticTruthError(
                f"coverage CSV line {line_number} interval is outside the timeline"
            )
        if row["coverage_scope"] != "all_model_relevant_scopes":
            raise PoseDiagnosticTruthError(
                f"coverage CSV line {line_number} coverage_scope invalid"
            )
        status = row["review_status"]
        if status not in {"pending", "accepted", "not_observable"}:
            raise PoseDiagnosticTruthError(
                f"coverage CSV line {line_number} review_status invalid"
            )
        annotators: list[str] = []
        adjudicator: str | None = None
        if status != "pending":
            annotators, adjudicator = _review_metadata(row, line_number=line_number)
        elif any(
            row[field].strip()
            for field in ("annotator_ids", "adjudicator_id", "adjudicated_at")
        ):
            raise PoseDiagnosticTruthError(
                f"coverage CSV line {line_number} pending row has review metadata"
            )
        null_reason = row["null_reason"].strip() or None
        if status == "not_observable" and null_reason is None:
            raise PoseDiagnosticTruthError(
                f"coverage CSV line {line_number} requires null_reason"
            )
        if status == "accepted" and null_reason is not None:
            raise PoseDiagnosticTruthError(
                f"coverage CSV line {line_number} accepted row cannot have null_reason"
            )
        parsed.append(
            {
                "coverage_id": coverage_id,
                "diagnostic_type": diagnostic_type,
                "start_source_frame_index": start,
                "end_source_frame_index": end,
                "review_status": status,
                "annotator_ids": annotators,
                "adjudicator_id": adjudicator,
                "adjudicated_at": row["adjudicated_at"].strip() or None,
                "null_reason": null_reason,
                "notes": row["notes"].strip() or None,
            }
        )
    accepted_by_type: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for item in parsed:
        if item["review_status"] == "accepted":
            accepted_by_type[item["diagnostic_type"]].append(
                (item["start_source_frame_index"], item["end_source_frame_index"])
            )
    for diagnostic_type, intervals in accepted_by_type.items():
        previous_end = -1
        for start, end in sorted(intervals):
            if start <= previous_end:
                raise PoseDiagnosticTruthError(
                    f"accepted coverage intervals overlap for {diagnostic_type}"
                )
            previous_end = end
    return parsed


def _truth_identity(
    diagnostic_type: str,
    source_frame_index: int,
    *,
    joint: str | None = None,
    left_joint: str | None = None,
    right_joint: str | None = None,
) -> tuple[str, int, str, str]:
    if diagnostic_type == "keypoint_jump":
        if not joint or left_joint or right_joint:
            raise PoseDiagnosticTruthError(
                "keypoint_jump truth requires only joint"
            )
        return diagnostic_type, source_frame_index, joint, ""
    if diagnostic_type == "left_right_swap":
        if not left_joint or not right_joint or joint or left_joint == right_joint:
            raise PoseDiagnosticTruthError(
                "left_right_swap truth requires a distinct ordered joint pair"
            )
        return diagnostic_type, source_frame_index, left_joint, right_joint
    if joint or left_joint or right_joint:
        raise PoseDiagnosticTruthError(
            f"{diagnostic_type} truth does not accept joint scope fields"
        )
    return diagnostic_type, source_frame_index, "", ""


def _parse_optional_track(value: str, *, field: str, line_number: int) -> int | None:
    if not value.strip():
        return None
    return _parse_int(value, field=field, line_number=line_number)


def _parse_positives(
    text: str,
    *,
    video_id: str,
    source_indexes: set[int],
    allowed_joints: set[str],
) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    ids: set[str] = set()
    identities: set[tuple[str, int, str, str]] = set()
    for line_number, row in enumerate(
        _read_csv(text, positives_csv_header()), start=2
    ):
        truth_id = row["truth_id"].strip()
        if not truth_id or truth_id in ids:
            raise PoseDiagnosticTruthError(
                f"positives CSV line {line_number} truth_id missing or duplicate"
            )
        ids.add(truth_id)
        if row["video_id"] != video_id:
            raise PoseDiagnosticTruthError(
                f"positives CSV line {line_number} video_id mismatch"
            )
        diagnostic_type = row["diagnostic_type"]
        if diagnostic_type not in DIAGNOSTIC_TYPES:
            raise PoseDiagnosticTruthError(
                f"positives CSV line {line_number} diagnostic_type invalid"
            )
        source_index = _parse_int(
            row["source_frame_index"],
            field="source_frame_index",
            line_number=line_number,
        )
        if source_index not in source_indexes:
            raise PoseDiagnosticTruthError(
                f"positives CSV line {line_number} frame is outside the timeline"
            )
        status = row["review_status"]
        if status not in {"pending", "accepted", "rejected", "not_observable"}:
            raise PoseDiagnosticTruthError(
                f"positives CSV line {line_number} review_status invalid"
            )
        annotators: list[str] = []
        adjudicator: str | None = None
        if status != "pending":
            annotators, adjudicator = _review_metadata(row, line_number=line_number)
        identity = _truth_identity(
            diagnostic_type,
            source_index,
            joint=row["joint"].strip() or None,
            left_joint=row["left_joint"].strip() or None,
            right_joint=row["right_joint"].strip() or None,
        )
        scoped_joints = {value for value in identity[2:] if value}
        if not scoped_joints.issubset(allowed_joints):
            raise PoseDiagnosticTruthError(
                f"positives CSV line {line_number} references a joint absent from source Pose"
            )
        if status == "accepted" and identity in identities:
            raise PoseDiagnosticTruthError(
                f"positives CSV line {line_number} duplicates an accepted truth identity"
            )
        if status == "accepted":
            identities.add(identity)
        from_track = _parse_optional_track(
            row["from_source_track_id"],
            field="from_source_track_id",
            line_number=line_number,
        )
        to_track = _parse_optional_track(
            row["to_source_track_id"],
            field="to_source_track_id",
            line_number=line_number,
        )
        if diagnostic_type != "source_track_switch" and (
            from_track is not None or to_track is not None
        ):
            raise PoseDiagnosticTruthError(
                f"positives CSV line {line_number} track IDs only apply to source switches"
            )
        if diagnostic_type == "source_track_switch" and (
            (from_track is None) != (to_track is None)
        ):
            raise PoseDiagnosticTruthError(
                f"positives CSV line {line_number} requires both or neither Track ID"
            )
        parsed.append(
            {
                "truth_id": truth_id,
                "diagnostic_type": diagnostic_type,
                "source_frame_index": source_index,
                "joint": row["joint"].strip() or None,
                "left_joint": row["left_joint"].strip() or None,
                "right_joint": row["right_joint"].strip() or None,
                "from_source_track_id": from_track,
                "to_source_track_id": to_track,
                "review_status": status,
                "annotator_ids": annotators,
                "adjudicator_id": adjudicator,
                "adjudicated_at": row["adjudicated_at"].strip() or None,
                "notes": row["notes"].strip() or None,
                "identity": identity,
            }
        )
    return parsed


def _candidate_identities(queue: Mapping[str, Any]) -> tuple[set[tuple[str, int, str, str]], int]:
    identities: set[tuple[str, int, str, str]] = set()
    unlocalized = 0
    for task in queue["tasks"]:
        diagnostic_type = task["diagnostic_type"]
        source_index = task["candidate_source_frame_index"]
        if source_index is None:
            unlocalized += 1
            continue
        if diagnostic_type == "keypoint_jump":
            for joint in task["joints"]:
                identities.add((diagnostic_type, source_index, joint, ""))
        elif diagnostic_type == "left_right_swap":
            for left_joint, right_joint in task["joint_pairs"]:
                identities.add(
                    (diagnostic_type, source_index, left_joint, right_joint)
                )
        else:
            identities.add((diagnostic_type, source_index, "", ""))
    return identities, unlocalized


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 8)


def evaluate_pose_diagnostic_truth(
    *,
    manifest: Mapping[str, Any],
    coverage_csv_text: str,
    positives_csv_text: str,
    coverage_path: Path | None = None,
    positives_path: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate candidates only where exhaustive human coverage is explicit."""

    if coverage_path is not None and coverage_path.read_text(
        encoding="utf-8"
    ) != coverage_csv_text:
        raise PoseDiagnosticTruthError("coverage path content differs from evaluated text")
    if positives_path is not None and positives_path.read_text(
        encoding="utf-8"
    ) != positives_csv_text:
        raise PoseDiagnosticTruthError("positives path content differs from evaluated text")
    queue, timeline = validate_pose_diagnostic_truth_pack_sources(manifest)
    source_indexes = {item["source_frame_index"] for item in timeline}
    frames_binding = queue["source"]["artifacts"].get("frames_jsonl")
    frames_path = Path(str(frames_binding.get("path", "")))
    allowed_joints = {
        point["name"]
        for record in _load_jsonl(frames_path)
        for pose in record.get("poses", [])
        if isinstance(pose, Mapping)
        for point in pose.get("keypoints", [])
        if isinstance(point, Mapping) and isinstance(point.get("name"), str)
    }
    if not allowed_joints:
        raise PoseDiagnosticTruthError("source frames contain no named Pose joints")
    coverage = _parse_coverage(
        coverage_csv_text,
        video_id=manifest["video_id"],
        source_indexes=source_indexes,
    )
    positives = _parse_positives(
        positives_csv_text,
        video_id=manifest["video_id"],
        source_indexes=source_indexes,
        allowed_joints=allowed_joints,
    )
    accepted_coverage: dict[str, set[int]] = {
        diagnostic_type: set() for diagnostic_type in DIAGNOSTIC_TYPES
    }
    for row in coverage:
        if row["review_status"] != "accepted":
            continue
        accepted_coverage[row["diagnostic_type"]].update(
            index
            for index in source_indexes
            if row["start_source_frame_index"]
            <= index
            <= row["end_source_frame_index"]
        )
    accepted_truth: dict[str, set[tuple[str, int, str, str]]] = {
        diagnostic_type: set() for diagnostic_type in DIAGNOSTIC_TYPES
    }
    for row in positives:
        if row["review_status"] != "accepted":
            continue
        diagnostic_type = row["diagnostic_type"]
        if row["source_frame_index"] not in accepted_coverage[diagnostic_type]:
            raise PoseDiagnosticTruthError(
                f"accepted positive {row['truth_id']} is outside accepted coverage"
            )
        accepted_truth[diagnostic_type].add(row["identity"])

    predicted, unlocalized_total = _candidate_identities(queue)
    metrics: dict[str, dict[str, Any]] = {}
    total_tp = total_fp = total_fn = 0
    fully_covered_types = 0
    for diagnostic_type in DIAGNOSTIC_TYPES:
        covered_frames = accepted_coverage[diagnostic_type]
        predictions_total = {
            item for item in predicted if item[0] == diagnostic_type
        }
        predictions_covered = {
            item for item in predictions_total if item[1] in covered_frames
        }
        truth = accepted_truth[diagnostic_type]
        tp_items = predictions_covered & truth
        fp_items = predictions_covered - truth
        fn_items = truth - predictions_covered
        tp, fp, fn = len(tp_items), len(fp_items), len(fn_items)
        total_tp += tp
        total_fp += fp
        total_fn += fn
        full_coverage = covered_frames == source_indexes
        if full_coverage:
            fully_covered_types += 1
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        f1 = (
            None
            if precision is None or recall is None
            else 0.0
            if precision + recall == 0
            else round(2 * precision * recall / (precision + recall), 8)
        )
        metrics[diagnostic_type] = {
            "coverage_status": (
                "full_timeline"
                if full_coverage
                else "partial"
                if covered_frames
                else "missing"
            ),
            "reviewed_frame_count": len(covered_frames),
            "timeline_frame_count": len(source_indexes),
            "coverage_fraction": round(len(covered_frames) / len(source_indexes), 8),
            "full_timeline_recall_ready": full_coverage,
            "prediction_count_total": len(predictions_total),
            "prediction_count_in_reviewed_coverage": len(predictions_covered),
            "truth_positive_count": len(truth),
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "metric_scope": (
                "full_timeline" if full_coverage else "accepted_coverage_only"
            ),
        }

    accepted_rows = sum(
        item["review_status"] == "accepted" for item in coverage
    )
    status = (
        "evaluated_full_timeline"
        if fully_covered_types == len(DIAGNOSTIC_TYPES)
        else "partial_coverage"
        if accepted_rows
        else "annotation_required"
    )
    overall_precision = _safe_ratio(total_tp, total_tp + total_fp)
    overall_recall = _safe_ratio(total_tp, total_tp + total_fn)
    overall_f1 = (
        None
        if overall_precision is None
        or overall_recall is None
        else 0.0
        if overall_precision + overall_recall == 0
        else round(
            2
            * overall_precision
            * overall_recall
            / (overall_precision + overall_recall),
            8,
        )
    )
    report = {
        "schema_version": "1.0.0",
        "evaluation_version": EVALUATION_VERSION,
        "generated_at": generated_at or _utc_now(),
        "status": status,
        "video_id": manifest["video_id"],
        "matching_protocol": dict(manifest["matching_protocol"]),
        "source": {
            "truth_pack_version": manifest["pack_version"],
            "truth_pack_manifest_content_sha256": _canonical_hash(manifest),
            "truth_pack_manifest_canonicalization": "rallymate-canonical-json-v1",
            "truth_pack_source_queue_sha256": manifest["source_queue"]["sha256"],
            "queue_artifact_binding_sha256": manifest["source_queue"][
                "artifact_binding_sha256"
            ],
            "coverage": {
                "path": str(coverage_path.resolve()) if coverage_path else None,
                "sha256": (
                    _sha256(coverage_path)
                    if coverage_path
                    else hashlib.sha256(coverage_csv_text.encode("utf-8")).hexdigest().upper()
                ),
            },
            "positives": {
                "path": str(positives_path.resolve()) if positives_path else None,
                "sha256": (
                    _sha256(positives_path)
                    if positives_path
                    else hashlib.sha256(positives_csv_text.encode("utf-8")).hexdigest().upper()
                ),
            },
            "source_validation": {
                "status": "passed",
                "queue_and_run_artifacts_rehashed": True,
            },
        },
        "annotation_counts": {
            "coverage_rows": len(coverage),
            "accepted_coverage_rows": accepted_rows,
            "positive_rows": len(positives),
            "accepted_positive_rows": sum(
                item["review_status"] == "accepted" for item in positives
            ),
            "unlocalized_candidate_tasks": unlocalized_total,
        },
        "by_diagnostic_type": metrics,
        "micro_average_on_accepted_coverage": {
            "true_positive": total_tp,
            "false_positive": total_fp,
            "false_negative": total_fn,
            "precision": overall_precision,
            "recall": overall_recall,
            "f1": overall_f1,
            "full_timeline": fully_covered_types == len(DIAGNOSTIC_TYPES),
        },
        "safety": {
            "candidate_only_review_used_as_recall_truth": False,
            "quality_gate_modified": False,
            "formal_scoring_accuracy_claim": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
        },
    }
    validate_pose_diagnostic_evaluation(report)
    return report


def validate_pose_diagnostic_evaluation(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0":
        raise PoseDiagnosticTruthError("unsupported diagnostic evaluation schema")
    if report.get("evaluation_version") != EVALUATION_VERSION:
        raise PoseDiagnosticTruthError("unsupported diagnostic evaluation version")
    if report.get("status") not in {
        "annotation_required",
        "partial_coverage",
        "evaluated_full_timeline",
    }:
        raise PoseDiagnosticTruthError("diagnostic evaluation status invalid")
    metrics = report.get("by_diagnostic_type")
    if not isinstance(metrics, Mapping) or set(metrics) != set(DIAGNOSTIC_TYPES):
        raise PoseDiagnosticTruthError("diagnostic evaluation type coverage mismatch")
    total_tp = total_fp = total_fn = 0
    full_type_count = 0
    for diagnostic_type, values in metrics.items():
        if not isinstance(values, Mapping):
            raise PoseDiagnosticTruthError("diagnostic evaluation metric must be an object")
        for name in ("precision", "recall", "f1", "coverage_fraction"):
            value = values.get(name)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not 0 <= value <= 1
            ):
                raise PoseDiagnosticTruthError(
                    f"{diagnostic_type}.{name} must be null or within [0,1]"
                )
        full = values.get("coverage_status") == "full_timeline"
        if values.get("full_timeline_recall_ready") is not full:
            raise PoseDiagnosticTruthError("full timeline readiness invariant failed")
        reviewed = values.get("reviewed_frame_count")
        timeline_count = values.get("timeline_frame_count")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                reviewed,
                values.get("prediction_count_total"),
                values.get("prediction_count_in_reviewed_coverage"),
                values.get("truth_positive_count"),
                values.get("true_positive"),
                values.get("false_positive"),
                values.get("false_negative"),
            )
        ) or isinstance(timeline_count, bool) or not isinstance(
            timeline_count, int
        ) or timeline_count < 1:
            raise PoseDiagnosticTruthError("diagnostic evaluation counts are invalid")
        if reviewed > timeline_count:
            raise PoseDiagnosticTruthError("reviewed frames cannot exceed timeline")
        expected_coverage_status = (
            "full_timeline"
            if reviewed == timeline_count
            else "partial"
            if reviewed
            else "missing"
        )
        if values.get("coverage_status") != expected_coverage_status:
            raise PoseDiagnosticTruthError("coverage status/count invariant failed")
        if not math.isclose(
            float(values["coverage_fraction"]),
            reviewed / timeline_count,
            rel_tol=0.0,
            abs_tol=1e-8,
        ):
            raise PoseDiagnosticTruthError("coverage fraction/count invariant failed")
        expected_scope = (
            "full_timeline" if expected_coverage_status == "full_timeline" else "accepted_coverage_only"
        )
        if values.get("metric_scope") != expected_scope:
            raise PoseDiagnosticTruthError("metric scope/coverage invariant failed")
        predictions_covered = values["prediction_count_in_reviewed_coverage"]
        if predictions_covered > values["prediction_count_total"]:
            raise PoseDiagnosticTruthError("reviewed predictions exceed total predictions")
        tp = values["true_positive"]
        fp = values["false_positive"]
        fn = values["false_negative"]
        if tp + fp != predictions_covered:
            raise PoseDiagnosticTruthError("TP/FP do not reconstruct reviewed predictions")
        if tp + fn != values["truth_positive_count"]:
            raise PoseDiagnosticTruthError("TP/FN do not reconstruct truth positives")
        expected_precision = _safe_ratio(tp, tp + fp)
        expected_recall = _safe_ratio(tp, tp + fn)
        expected_f1 = (
            None
            if expected_precision is None or expected_recall is None
            else 0.0
            if expected_precision + expected_recall == 0
            else round(
                2
                * expected_precision
                * expected_recall
                / (expected_precision + expected_recall),
                8,
            )
        )
        for field, expected in (
            ("precision", expected_precision),
            ("recall", expected_recall),
            ("f1", expected_f1),
        ):
            actual = values.get(field)
            if (actual is None) != (expected is None) or (
                actual is not None
                and not math.isclose(
                    float(actual), float(expected), rel_tol=0.0, abs_tol=1e-8
                )
            ):
                raise PoseDiagnosticTruthError(
                    f"{diagnostic_type}.{field} does not match TP/FP/FN"
                )
        total_tp += tp
        total_fp += fp
        total_fn += fn
        full_type_count += int(full)
    counts = report.get("annotation_counts")
    if not isinstance(counts, Mapping):
        raise PoseDiagnosticTruthError("diagnostic annotation counts missing")
    for field in (
        "coverage_rows",
        "accepted_coverage_rows",
        "positive_rows",
        "accepted_positive_rows",
        "unlocalized_candidate_tasks",
    ):
        value = counts.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PoseDiagnosticTruthError(f"annotation_counts.{field} invalid")
    if counts["accepted_positive_rows"] != sum(
        values["truth_positive_count"] for values in metrics.values()
    ):
        raise PoseDiagnosticTruthError("accepted positive count/metrics mismatch")
    expected_status = (
        "evaluated_full_timeline"
        if full_type_count == len(DIAGNOSTIC_TYPES)
        else "partial_coverage"
        if counts["accepted_coverage_rows"]
        else "annotation_required"
    )
    if report.get("status") != expected_status:
        raise PoseDiagnosticTruthError("evaluation status/coverage invariant failed")
    micro = report.get("micro_average_on_accepted_coverage")
    if not isinstance(micro, Mapping):
        raise PoseDiagnosticTruthError("diagnostic micro average missing")
    if any(
        micro.get(field) != expected
        for field, expected in (
            ("true_positive", total_tp),
            ("false_positive", total_fp),
            ("false_negative", total_fn),
            ("full_timeline", full_type_count == len(DIAGNOSTIC_TYPES)),
        )
    ):
        raise PoseDiagnosticTruthError("micro count/full-timeline invariant failed")
    micro_precision = _safe_ratio(total_tp, total_tp + total_fp)
    micro_recall = _safe_ratio(total_tp, total_tp + total_fn)
    micro_f1 = (
        None
        if micro_precision is None or micro_recall is None
        else 0.0
        if micro_precision + micro_recall == 0
        else round(
            2 * micro_precision * micro_recall / (micro_precision + micro_recall),
            8,
        )
    )
    for field, expected in (
        ("precision", micro_precision),
        ("recall", micro_recall),
        ("f1", micro_f1),
    ):
        actual = micro.get(field)
        if (actual is None) != (expected is None) or (
            actual is not None
            and not math.isclose(
                float(actual), float(expected), rel_tol=0.0, abs_tol=1e-8
            )
        ):
            raise PoseDiagnosticTruthError(
                f"micro_average_on_accepted_coverage.{field} invariant failed"
            )
    safety = report.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(field) is not False
        for field in (
            "candidate_only_review_used_as_recall_truth",
            "quality_gate_modified",
            "formal_scoring_accuracy_claim",
            "grades_generated",
            "thresholds_generated",
            "maturity_promoted",
        )
    ):
        raise PoseDiagnosticTruthError("diagnostic evaluation contains unsafe claims")


def render_pose_diagnostic_truth_html(
    manifest: Mapping[str, Any],
    *,
    timeline: list[dict[str, Any]],
    output_path: Path,
) -> str:
    """Render a video-first sparse truth editor; browser drafts are never truth."""

    validate_pose_diagnostic_truth_pack_manifest(manifest)
    output_path = output_path.resolve()
    media_path = Path(str(manifest["review_media"]["path"])).resolve()
    media_url = Path(os.path.relpath(media_path, output_path.parent)).as_posix()
    offset_ms = manifest["review_media"]["video_time_offset_ms"]
    frames = [
        {
            "source_frame_index": item["source_frame_index"],
            "processed_index": item["processed_index"],
            "timestamp_ms": item["timestamp_ms"],
            "media_time_ms": max(0, item["timestamp_ms"] - offset_ms),
        }
        for item in timeline
    ]
    payload = json.dumps(
        {
            "video_id": manifest["video_id"],
            "diagnostic_types": manifest["diagnostic_types"],
            "frame_domain": manifest["frame_domain"],
            "frames": frames,
            "media_url": media_url,
            "binding": manifest["source_queue"]["artifact_binding_sha256"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).replace("<", "\\u003c")
    title = html.escape(f"RallyMate Pose 诊断全时间线真值 · {manifest['video_id']}")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0}}main{{max-width:1500px;margin:auto;padding:18px}}.warning{{background:#3f2a08;border:1px solid #a16207;padding:12px;border-radius:8px}}.grid{{display:grid;grid-template-columns:minmax(420px,1.2fr) minmax(420px,1fr);gap:18px}}video{{width:100%;max-height:66vh;background:#000}}fieldset{{border:1px solid #475569;margin:10px 0}}label{{display:block;margin:7px 0}}input,select,button{{font:inherit;padding:6px}}input[type=text]{{width:min(95%,520px)}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #334155;padding:6px}}code{{color:#93c5fd}}.muted{{color:#94a3b8}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main>
<h1>{title}</h1><p class="warning">必须先逐帧/逐段完成盲审，再查看候选复核队列。浏览器草稿不是真值；导出的 coverage/positives CSV 还必须经过 Python 源哈希校验和独立裁决。此页面不会修改评分门禁、等级或阈值。</p>
<div class="grid"><section><video id="video" controls preload="metadata" src="{html.escape(media_url)}"></video><p><button id="prev">◀ 上一帧</button> <button id="next">下一帧 ▶</button> <strong id="frame"></strong></p><p class="muted">使用实际 timestamp_ms 映射，不假设固定 FPS。</p>
<fieldset><legend>新增人工确认异常</legend><label>类型 <select id="type"></select></label><label>joint（jump）<input id="joint" type="text"></label><label>left_joint（swap）<input id="left" type="text"></label><label>right_joint（swap）<input id="right" type="text"></label><label>from/to Track（可选，仅 switch）<input id="from" size="8"> <input id="to" size="8"></label><label>备注 <input id="notes" type="text"></label><button id="add">加入真阳性</button></fieldset></section>
<section><fieldset><legend>独立复核与裁决元数据</legend><label>独立标注者 IDs（分号分隔，至少2名）<input id="annotators" type="text"></label><label>独立裁决者 ID <input id="adjudicator" type="text"></label><label>裁决时间 ISO-8601 <input id="at" type="text"></label></fieldset><fieldset><legend>完整覆盖确认</legend><div id="coverage"></div></fieldset><p><button id="exportCoverage">导出 coverage.csv</button> <button id="exportPositives">导出 positives.csv</button></p><table><thead><tr><th>帧</th><th>类型/范围</th><th>操作</th></tr></thead><tbody id="rows"></tbody></table></section></div>
<script id="truthData" type="application/json">{payload}</script><script>
const p=JSON.parse(document.getElementById('truthData').textContent),v=document.getElementById('video'),frameEl=document.getElementById('frame'),typeEl=document.getElementById('type'),rowsEl=document.getElementById('rows'),coverageEl=document.getElementById('coverage');const key='rallymate-pose-diagnostic-truth:'+p.binding,draft=JSON.parse(localStorage.getItem(key)||'{{"positives":[],"coverage":{{}}}}');p.diagnostic_types.forEach(t=>{{typeEl.add(new Option(t,t));const l=document.createElement('label');l.innerHTML=`<input type="checkbox" data-type="${{t}}"> 已完整盲审 ${{t}} 的全部帧和相关关节范围`;const c=l.querySelector('input');c.checked=!!draft.coverage[t];c.onchange=()=>{{draft.coverage[t]=c.checked;save();}};coverageEl.appendChild(l);}});['annotators','adjudicator','at'].forEach(id=>{{const e=document.getElementById(id);e.value=draft[id]||'';e.oninput=()=>{{draft[id]=e.value;save();}};}});if(!draft.at){{draft.at=new Date().toISOString();document.getElementById('at').value=draft.at;}}
function save(){{localStorage.setItem(key,JSON.stringify(draft));render();}}function nearest(){{const ms=v.currentTime*1000;let lo=0,hi=p.frames.length-1;while(lo<hi){{const m=Math.floor((lo+hi)/2);if(p.frames[m].media_time_ms<ms)lo=m+1;else hi=m;}}if(lo>0&&Math.abs(p.frames[lo-1].media_time_ms-ms)<Math.abs(p.frames[lo].media_time_ms-ms))lo--;return lo;}}function show(){{const f=p.frames[nearest()];frameEl.textContent=`源帧 ${{f.source_frame_index}} · processed ${{f.processed_index}} · ${{f.timestamp_ms}} ms`;}}v.ontimeupdate=show;document.getElementById('prev').onclick=()=>{{const i=Math.max(0,nearest()-1);v.currentTime=p.frames[i].media_time_ms/1000;show();}};document.getElementById('next').onclick=()=>{{const i=Math.min(p.frames.length-1,nearest()+1);v.currentTime=p.frames[i].media_time_ms/1000;show();}};
document.getElementById('add').onclick=()=>{{const f=p.frames[nearest()],t=typeEl.value,token=globalThis.crypto?.randomUUID?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2),item={{truth_id:'pdt-'+token,video_id:p.video_id,diagnostic_type:t,source_frame_index:f.source_frame_index,joint:document.getElementById('joint').value.trim(),left_joint:document.getElementById('left').value.trim(),right_joint:document.getElementById('right').value.trim(),from_source_track_id:document.getElementById('from').value.trim(),to_source_track_id:document.getElementById('to').value.trim(),notes:document.getElementById('notes').value.trim()}};draft.positives.push(item);save();}};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));function render(){{rowsEl.innerHTML='';draft.positives.forEach((x,i)=>{{const tr=document.createElement('tr');tr.innerHTML=`<td>${{x.source_frame_index}}</td><td>${{esc(x.diagnostic_type)}}<br>${{esc(x.joint||[x.left_joint,x.right_joint].filter(Boolean).join(' / ')||'frame')}}</td><td><button>删除</button></td>`;tr.querySelector('button').onclick=()=>{{draft.positives.splice(i,1);save();}};rowsEl.appendChild(tr);}});}}render();
function csv(rows){{return '\ufeff'+rows.map(r=>r.map(x=>'"'+String(x??'').replaceAll('"','""')+'"').join(',')).join('\\r\\n');}}function download(name,text){{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{{type:'text/csv'}}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}}function syncMeta(){{draft.annotators=document.getElementById('annotators').value;draft.adjudicator=document.getElementById('adjudicator').value;draft.at=document.getElementById('at').value;save();}}document.getElementById('exportCoverage').onclick=()=>{{syncMeta();const d=p.frame_domain,ch=[{json.dumps(coverage_csv_header(), ensure_ascii=False)}];p.diagnostic_types.forEach((t,i)=>ch.push([`coverage-${{i+1}}`,p.video_id,t,d.first_source_frame_index,d.last_source_frame_index,'all_model_relevant_scopes',draft.coverage[t]?'accepted':'pending',draft.coverage[t]?draft.annotators:'',draft.coverage[t]?draft.adjudicator:'',draft.coverage[t]?draft.at:'','','']));download('pose-diagnostic-coverage.csv',csv(ch));}};document.getElementById('exportPositives').onclick=()=>{{syncMeta();const ph=[{json.dumps(positives_csv_header(), ensure_ascii=False)}];draft.positives.forEach(x=>ph.push([x.truth_id,p.video_id,x.diagnostic_type,x.source_frame_index,x.joint,x.left_joint,x.right_joint,x.from_source_track_id,x.to_source_track_id,'accepted',draft.annotators,draft.adjudicator,draft.at,x.notes]));download('pose-diagnostic-positives.csv',csv(ph));}};show();
</script></main></body></html>"""


def write_blank_pose_diagnostic_truth_pack(
    *,
    queue_path: Path,
    output_dir: Path,
    generated_at: str | None = None,
    overwrite_unchanged_blank: bool = False,
) -> dict[str, Any]:
    queue_path = queue_path.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        if not overwrite_unchanged_blank:
            raise PoseDiagnosticTruthError(f"refusing to overwrite non-empty {output_dir}")
        existing_manifest_path = output_dir / "manifest.json"
        if not existing_manifest_path.is_file():
            raise PoseDiagnosticTruthError("cannot verify existing blank truth pack")
        existing_manifest = json.loads(
            existing_manifest_path.read_text(encoding="utf-8")
        )
        validate_pose_diagnostic_truth_pack_manifest(existing_manifest)
        for name in ("coverage", "positives"):
            binding = existing_manifest["annotation_files"][name]
            path = Path(binding["path"])
            if not path.is_file() or _sha256(path) != binding["template_sha256"]:
                raise PoseDiagnosticTruthError(
                    "refusing to overwrite a truth pack whose annotation template changed"
                )
    output_dir.mkdir(parents=True, exist_ok=True)
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    validate_pose_diagnostic_review_queue(queue)
    validate_pose_diagnostic_review_queue_sources(queue)
    _, timeline = _timeline_from_queue(queue)
    source_indexes = [item["source_frame_index"] for item in timeline]
    coverage_path = output_dir / "pose-diagnostic-coverage.csv"
    positives_path = output_dir / "pose-diagnostic-positives.csv"
    coverage_rows = [
        {
            "coverage_id": f"coverage-{index + 1}",
            "video_id": queue["video_id"],
            "diagnostic_type": diagnostic_type,
            "start_source_frame_index": source_indexes[0],
            "end_source_frame_index": source_indexes[-1],
            "coverage_scope": "all_model_relevant_scopes",
            "review_status": "pending",
            "annotator_ids": "",
            "adjudicator_id": "",
            "adjudicated_at": "",
            "null_reason": "",
            "notes": "",
        }
        for index, diagnostic_type in enumerate(DIAGNOSTIC_TYPES)
    ]
    coverage_path.write_text(
        _blank_csv(coverage_csv_header(), coverage_rows), encoding="utf-8"
    )
    positives_path.write_text(
        _blank_csv(positives_csv_header(), []), encoding="utf-8"
    )
    manifest = build_pose_diagnostic_truth_pack_manifest(
        queue=queue,
        queue_path=queue_path,
        coverage_path=coverage_path,
        positives_path=positives_path,
        generated_at=generated_at,
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(
        render_pose_diagnostic_truth_html(
            manifest, timeline=timeline, output_path=output_dir / "index.html"
        ),
        encoding="utf-8",
    )
    return manifest
