from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_vision.validation import validate_run_artifacts


QUEUE_VERSION = "pose-diagnostic-review-queue-v1.0.0"
DIAGNOSTIC_FLAGS = {
    "keypoint_jump": "keypoint_jump_candidates_present",
    "left_right_swap": "left_right_swap_candidates_present",
    "source_track_switch": "source_track_switch_candidates_present",
    "primary_identity_ambiguity": "primary_identity_ambiguous",
}
OUTSIDE_FLAGS = {
    "keypoint_jump": "keypoint_jump_candidates_outside_indicator_joints",
    "left_right_swap": "left_right_swap_candidates_outside_indicator_joints",
}
DECISION_VALUES = (
    "pending",
    "confirmed_issue",
    "false_positive",
    "uncertain",
    "not_observable",
)


class PoseDiagnosticReviewError(ValueError):
    """Raised when a diagnostic review artifact is incomplete or unsafe."""


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
            raise PoseDiagnosticReviewError(
                f"{path.name} line {line_number} is invalid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise PoseDiagnosticReviewError(
                f"{path.name} line {line_number} must be an object"
            )
        records.append(record)
    return records


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


def _required_joints(score: Mapping[str, Any]) -> set[str] | None:
    feature = score.get("feature")
    items = feature.get("items") if isinstance(feature, Mapping) else None
    if not isinstance(items, list) or not items:
        return None
    joints: set[str] = set()
    for item in items:
        name = item.get("feature_name") if isinstance(item, Mapping) else None
        definition = FEATURE_DEFINITIONS.get(name) if isinstance(name, str) else None
        required = definition.get("required_joints") if definition else None
        if not isinstance(required, list) or any(
            not isinstance(joint, str) or not joint for joint in required
        ):
            return None
        joints.update(required)
    return joints or None


def _frame_record_index(
    frames: Iterable[Mapping[str, Any]],
) -> tuple[dict[int, Mapping[str, Any]], list[int]]:
    by_source: dict[int, Mapping[str, Any]] = {}
    ordered: list[tuple[int, int]] = []
    for record in frames:
        frame = record.get("frame")
        if not isinstance(frame, Mapping):
            raise PoseDiagnosticReviewError("frames.jsonl record has no frame object")
        source_index = frame.get("index")
        processed_index = frame.get("processed_index")
        timestamp_ms = frame.get("timestamp_ms")
        if not all(isinstance(value, int) for value in (source_index, processed_index, timestamp_ms)):
            raise PoseDiagnosticReviewError("frame indexes/timestamp must be integers")
        if source_index in by_source:
            raise PoseDiagnosticReviewError("duplicate source frame index")
        by_source[source_index] = record
        ordered.append((processed_index, source_index))
    ordered.sort()
    return by_source, [source for _, source in ordered]


def _timeline_index(
    timeline: Iterable[Mapping[str, Any]],
) -> tuple[dict[int, Mapping[str, Any]], list[Mapping[str, Any]]]:
    by_source: dict[int, Mapping[str, Any]] = {}
    ordered = sorted(timeline, key=lambda item: int(item.get("processed_index", -1)))
    for record in ordered:
        source_index = record.get("source_frame_index")
        if not isinstance(source_index, int):
            raise PoseDiagnosticReviewError(
                "primary-player source_frame_index must be an integer"
            )
        if source_index in by_source:
            raise PoseDiagnosticReviewError("duplicate primary source frame index")
        by_source[source_index] = record
    return by_source, ordered


def _task_key(
    diagnostic_type: str,
    source_frame_index: int | None,
    discriminator: tuple[Any, ...],
) -> tuple[Any, ...]:
    return (diagnostic_type, source_frame_index, *discriminator)


def _add_task_candidate(
    tasks: dict[tuple[Any, ...], dict[str, Any]],
    *,
    diagnostic_type: str,
    source_frame_index: int | None,
    discriminator: tuple[Any, ...],
    joints: Iterable[str] = (),
    joint_pair: tuple[str, str] | None = None,
    track_transition: Mapping[str, Any] | None = None,
    event: Mapping[str, Any],
) -> None:
    key = _task_key(diagnostic_type, source_frame_index, discriminator)
    task = tasks.setdefault(
        key,
        {
            "diagnostic_type": diagnostic_type,
            "diagnostic_flag": DIAGNOSTIC_FLAGS[diagnostic_type],
            "candidate_source_frame_index": source_frame_index,
            "joints": set(),
            "joint_pairs": set(),
            "track_transition": dict(track_transition) if track_transition else None,
            "event_refs": {},
        },
    )
    task["joints"].update(joints)
    if joint_pair is not None:
        task["joint_pairs"].add(joint_pair)
    task["event_refs"][event["event_id"]] = {
        "event_id": event["event_id"],
        "event_code": event["event_code"],
        "start_ms": event["start_ms"],
        "end_ms": event["end_ms"],
    }


def _joint_observations(
    *,
    candidate_source_frame_index: int | None,
    joints: list[str],
    frames_by_source: Mapping[int, Mapping[str, Any]],
    ordered_sources: list[int],
    timeline_by_source: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if candidate_source_frame_index is None or not joints:
        return []
    try:
        position = ordered_sources.index(candidate_source_frame_index)
    except ValueError:
        return []
    indexes = ordered_sources[max(0, position - 1) : position + 2]
    observations: list[dict[str, Any]] = []
    for source_index in indexes:
        frame_record = frames_by_source[source_index]
        frame = frame_record["frame"]
        timeline = timeline_by_source.get(source_index, {})
        selected_track = timeline.get("source_track_id")
        pose = next(
            (
                item
                for item in frame_record.get("poses", [])
                if item.get("person_track_id") == selected_track
            ),
            None,
        )
        keypoints = {
            item.get("name"): item
            for item in (pose.get("keypoints", []) if isinstance(pose, Mapping) else [])
            if isinstance(item, Mapping)
        }
        joint_values = []
        for joint in joints:
            point = keypoints.get(joint)
            joint_values.append(
                {
                    "joint": joint,
                    "x_normalized": point.get("x_normalized") if point else None,
                    "y_normalized": point.get("y_normalized") if point else None,
                    "confidence": point.get("confidence") if point else None,
                    "in_frame": point.get("in_frame", True) if point else None,
                }
            )
        observations.append(
            {
                "role": (
                    "candidate"
                    if source_index == candidate_source_frame_index
                    else "previous"
                    if source_index < candidate_source_frame_index
                    else "next"
                ),
                "source_frame_index": source_index,
                "processed_index": frame["processed_index"],
                "timestamp_ms": frame["timestamp_ms"],
                "source_track_id": selected_track,
                "joints": joint_values,
            }
        )
    return observations


def build_pose_diagnostic_review_queue(
    *,
    video_id: str,
    events: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    primary_timeline: list[dict[str, Any]],
    source: dict[str, Any],
    review_media: dict[str, Any],
    context_ms: int = 800,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build exact-frame review tasks without converting diagnostics to truth."""

    if not isinstance(video_id, str) or not video_id:
        raise PoseDiagnosticReviewError("video_id must be non-empty")
    if not isinstance(context_ms, int) or context_ms < 0:
        raise PoseDiagnosticReviewError("context_ms must be a non-negative integer")
    frames_by_source, ordered_sources = _frame_record_index(frames)
    timeline_by_source, timeline_ordered = _timeline_index(primary_timeline)
    if set(frames_by_source) != set(timeline_by_source):
        raise PoseDiagnosticReviewError("frames and primary timeline source indexes differ")
    max_timestamp_ms = max(
        (int(record["frame"]["timestamp_ms"]) for record in frames), default=0
    )
    events_by_id = {event.get("event_id"): event for event in events}
    if len(events_by_id) != len(events) or None in events_by_id:
        raise PoseDiagnosticReviewError("events require unique event_id values")
    scores_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for score in scores:
        event_id = score.get("event_id")
        if event_id not in events_by_id:
            raise PoseDiagnosticReviewError("score references an unknown event")
        scores_by_event[event_id].append(score)

    candidates: dict[tuple[Any, ...], dict[str, Any]] = {}
    for event in events:
        diagnostics = event.get("track_diagnostics")
        if not isinstance(diagnostics, Mapping):
            raise PoseDiagnosticReviewError("event track_diagnostics missing")

        jump_by_joint = diagnostics.get("keypoint_jump_candidate_frames_by_joint", {})
        if isinstance(jump_by_joint, Mapping):
            for joint, candidate_frames in jump_by_joint.items():
                if not isinstance(joint, str) or not isinstance(candidate_frames, list):
                    raise PoseDiagnosticReviewError("invalid joint-level jump provenance")
                for source_frame in candidate_frames:
                    if not isinstance(source_frame, int):
                        raise PoseDiagnosticReviewError("jump frame must be an integer")
                    _add_task_candidate(
                        candidates,
                        diagnostic_type="keypoint_jump",
                        source_frame_index=source_frame,
                        discriminator=(joint,),
                        joints=(joint,),
                        event=event,
                    )

        swap_pairs = diagnostics.get("left_right_swap_candidate_joint_pairs", [])
        if isinstance(swap_pairs, list):
            for pair in swap_pairs:
                if not isinstance(pair, Mapping):
                    raise PoseDiagnosticReviewError("invalid swap-pair provenance")
                left = pair.get("left_joint")
                right = pair.get("right_joint")
                candidate_frames = pair.get("frames")
                if not isinstance(left, str) or not isinstance(right, str) or not isinstance(candidate_frames, list):
                    raise PoseDiagnosticReviewError("invalid swap-pair fields")
                for source_frame in candidate_frames:
                    if not isinstance(source_frame, int):
                        raise PoseDiagnosticReviewError("swap frame must be an integer")
                    _add_task_candidate(
                        candidates,
                        diagnostic_type="left_right_swap",
                        source_frame_index=source_frame,
                        discriminator=(left, right),
                        joints=(left, right),
                        joint_pair=(left, right),
                        event=event,
                    )

        ambiguous_frames = diagnostics.get("primary_identity_ambiguous_frames", [])
        if isinstance(ambiguous_frames, list):
            for source_frame in ambiguous_frames:
                if not isinstance(source_frame, int):
                    raise PoseDiagnosticReviewError("ambiguity frame must be an integer")
                _add_task_candidate(
                    candidates,
                    diagnostic_type="primary_identity_ambiguity",
                    source_frame_index=source_frame,
                    discriminator=(),
                    event=event,
                )

        if diagnostics.get("source_track_switch_candidate_count", 0):
            in_event = [
                item
                for item in timeline_ordered
                if event["start_ms"] <= item.get("timestamp_ms", -1) <= event["end_ms"]
                and item.get("selection_status") == "selected"
            ]
            transitions = []
            for previous, current in zip(in_event, in_event[1:]):
                before = previous.get("source_track_id")
                after = current.get("source_track_id")
                if before != after:
                    transitions.append((current, before, after))
            if transitions:
                for current, before, after in transitions:
                    _add_task_candidate(
                        candidates,
                        diagnostic_type="source_track_switch",
                        source_frame_index=current["source_frame_index"],
                        discriminator=(before, after),
                        track_transition={
                            "from_source_track_id": before,
                            "to_source_track_id": after,
                        },
                        event=event,
                    )
            else:
                _add_task_candidate(
                    candidates,
                    diagnostic_type="source_track_switch",
                    source_frame_index=None,
                    discriminator=(event["event_id"], "unlocated"),
                    track_transition={
                        "from_source_track_id": None,
                        "to_source_track_id": None,
                    },
                    event=event,
                )

    final_tasks: list[dict[str, Any]] = []
    media_offset_ms = review_media.get("video_time_offset_ms", 0)
    if not isinstance(media_offset_ms, int):
        raise PoseDiagnosticReviewError("review media offset must be an integer")
    for key, candidate in candidates.items():
        source_frame = candidate["candidate_source_frame_index"]
        event_refs = sorted(
            candidate["event_refs"].values(),
            key=lambda item: (item["start_ms"], item["event_code"], item["event_id"]),
        )
        frame_record = frames_by_source.get(source_frame) if source_frame is not None else None
        frame = frame_record.get("frame") if isinstance(frame_record, Mapping) else None
        if source_frame is not None and not isinstance(frame, Mapping):
            raise PoseDiagnosticReviewError(
                f"diagnostic frame {source_frame} is absent from frames.jsonl"
            )
        timestamp_ms = (
            int(frame["timestamp_ms"])
            if isinstance(frame, Mapping)
            else min(item["start_ms"] for item in event_refs)
        )
        clip_start = max(0, timestamp_ms - context_ms)
        clip_end = min(max_timestamp_ms, timestamp_ms + context_ms)
        affected_instances: set[str] = set()
        affected_indicators: set[str] = set()
        advisory_indicators: set[str] = set()
        task_joints = set(candidate["joints"])
        diagnostic_flag = candidate["diagnostic_flag"]
        outside_flag = OUTSIDE_FLAGS.get(candidate["diagnostic_type"])
        for event_ref in event_refs:
            for score in scores_by_event[event_ref["event_id"]]:
                indicator_id = score.get("indicator_id")
                if not isinstance(indicator_id, str):
                    continue
                gate = score.get("quality_gate")
                if not isinstance(gate, Mapping):
                    continue
                required_joints = _required_joints(score)
                joint_relevant = (
                    not task_joints
                    or required_joints is None
                    or not task_joints.isdisjoint(required_joints)
                )
                if diagnostic_flag in gate.get("scoring_block_flags", []) and joint_relevant:
                    affected_indicators.add(indicator_id)
                    affected_instances.add(f"{event_ref['event_id']}::{indicator_id}")
                if outside_flag and outside_flag in gate.get("input_quality_flags", []):
                    advisory_indicators.add(indicator_id)
        task_identity = {
            "video_id": video_id,
            "diagnostic_type": candidate["diagnostic_type"],
            "source_frame_index": source_frame,
            "joints": sorted(task_joints),
            "joint_pairs": sorted([list(pair) for pair in candidate["joint_pairs"]]),
            "track_transition": candidate["track_transition"],
        }
        task_id = "pdr-" + _canonical_hash(task_identity)[:16].lower()
        final_tasks.append(
            {
                "task_id": task_id,
                "diagnostic_type": candidate["diagnostic_type"],
                "diagnostic_flag": diagnostic_flag,
                "review_status": "pending",
                "decision": None,
                "candidate_source_frame_index": source_frame,
                "processed_index": frame.get("processed_index") if frame else None,
                "timestamp_ms": timestamp_ms,
                "review_media_time_ms": max(0, timestamp_ms - media_offset_ms),
                "clip_start_ms": clip_start,
                "clip_end_ms": clip_end,
                "joints": sorted(task_joints),
                "joint_pairs": sorted([list(pair) for pair in candidate["joint_pairs"]]),
                "track_transition": candidate["track_transition"],
                "event_refs": event_refs,
                "affected_indicator_ids": sorted(affected_indicators),
                "affected_indicator_instances": sorted(affected_instances),
                "advisory_only_indicator_ids": sorted(
                    advisory_indicators - affected_indicators
                ),
                "joint_observations": _joint_observations(
                    candidate_source_frame_index=source_frame,
                    joints=sorted(task_joints),
                    frames_by_source=frames_by_source,
                    ordered_sources=ordered_sources,
                    timeline_by_source=timeline_by_source,
                ),
                "review_semantics": (
                    "heuristic_candidate_requires_human_review;pending_is_not_truth;"
                    "decision_does_not_modify_pose_or_scoring_gate"
                ),
            }
        )
    final_tasks.sort(
        key=lambda item: (
            item["timestamp_ms"],
            item["diagnostic_type"],
            item["task_id"],
        )
    )
    by_type = Counter(item["diagnostic_type"] for item in final_tasks)
    unique_indicator_instances = {
        instance
        for task in final_tasks
        for instance in task["affected_indicator_instances"]
    }
    queue = {
        "schema_version": "1.0.0",
        "queue_version": QUEUE_VERSION,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "review_required" if final_tasks else "no_candidates",
        "video_id": video_id,
        "source": source,
        "review_media": review_media,
        "scope": {
            "diagnostic_types": sorted(DIAGNOSTIC_FLAGS),
            "deduplication": (
                "exact source frame plus joint/pair/track transition across overlapping events"
            ),
            "context_ms": context_ms,
        },
        "counts": {
            "tasks": len(final_tasks),
            "pending": len(final_tasks),
            "by_diagnostic_type": dict(sorted(by_type.items())),
            "unique_candidate_frames": len(
                {
                    item["candidate_source_frame_index"]
                    for item in final_tasks
                    if item["candidate_source_frame_index"] is not None
                }
            ),
            "unique_affected_indicator_instances": len(unique_indicator_instances),
            "task_indicator_links": sum(
                len(item["affected_indicator_instances"]) for item in final_tasks
            ),
        },
        "tasks": final_tasks,
        "decision_contract": {
            "allowed_values": list(DECISION_VALUES),
            "required_for_non_pending": ["annotator_id", "reviewed_at"],
            "export_semantics": (
                "browser CSV is manual review input only; Python validation and adjudication "
                "are required before any quality-policy change"
            ),
        },
        "safety": {
            "accuracy_claim": False,
            "diagnostic_candidates_are_ground_truth": False,
            "review_decisions_generated": False,
            "pose_coordinates_are_manual_corrections": False,
            "quality_gate_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
        },
    }
    validate_pose_diagnostic_review_queue(queue)
    return queue


def validate_pose_diagnostic_review_queue(queue: Mapping[str, Any]) -> None:
    if queue.get("schema_version") != "1.0.0":
        raise PoseDiagnosticReviewError("unsupported queue schema_version")
    if queue.get("queue_version") != QUEUE_VERSION:
        raise PoseDiagnosticReviewError("unsupported queue_version")
    tasks = queue.get("tasks")
    if not isinstance(tasks, list):
        raise PoseDiagnosticReviewError("tasks must be a list")
    expected_status = "review_required" if tasks else "no_candidates"
    if queue.get("status") != expected_status:
        raise PoseDiagnosticReviewError("queue status/task invariant failed")
    ids: set[str] = set()
    previous_order: tuple[Any, ...] | None = None
    for task in tasks:
        if not isinstance(task, Mapping):
            raise PoseDiagnosticReviewError("task must be an object")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in ids:
            raise PoseDiagnosticReviewError("task_id values must be unique")
        ids.add(task_id)
        if task.get("diagnostic_type") not in DIAGNOSTIC_FLAGS:
            raise PoseDiagnosticReviewError("unknown diagnostic_type")
        if task.get("diagnostic_flag") != DIAGNOSTIC_FLAGS[task["diagnostic_type"]]:
            raise PoseDiagnosticReviewError("diagnostic flag/type mismatch")
        if task.get("review_status") != "pending" or task.get("decision") is not None:
            raise PoseDiagnosticReviewError("generated queue cannot contain review decisions")
        if not isinstance(task.get("timestamp_ms"), int) or task["timestamp_ms"] < 0:
            raise PoseDiagnosticReviewError("task timestamp_ms invalid")
        if not (
            isinstance(task.get("clip_start_ms"), int)
            and isinstance(task.get("clip_end_ms"), int)
            and task["clip_start_ms"] <= task["timestamp_ms"] <= task["clip_end_ms"]
        ):
            raise PoseDiagnosticReviewError("task clip boundary invalid")
        for field in (
            "joints",
            "joint_pairs",
            "event_refs",
            "affected_indicator_ids",
            "affected_indicator_instances",
            "advisory_only_indicator_ids",
            "joint_observations",
        ):
            if not isinstance(task.get(field), list):
                raise PoseDiagnosticReviewError(f"task.{field} must be a list")
        order = (task["timestamp_ms"], task["diagnostic_type"], task_id)
        if previous_order is not None and order < previous_order:
            raise PoseDiagnosticReviewError("tasks must be deterministically sorted")
        previous_order = order
    counts = queue.get("counts")
    if not isinstance(counts, Mapping) or counts.get("tasks") != len(tasks):
        raise PoseDiagnosticReviewError("task count mismatch")
    if counts.get("pending") != len(tasks):
        raise PoseDiagnosticReviewError("generated pending count mismatch")
    actual_by_type = dict(sorted(Counter(item["diagnostic_type"] for item in tasks).items()))
    if counts.get("by_diagnostic_type") != actual_by_type:
        raise PoseDiagnosticReviewError("diagnostic type count mismatch")
    safety = queue.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(field) is not False
        for field in (
            "accuracy_claim",
            "diagnostic_candidates_are_ground_truth",
            "review_decisions_generated",
            "pose_coordinates_are_manual_corrections",
            "quality_gate_modified",
            "grades_generated",
            "thresholds_generated",
        )
    ):
        raise PoseDiagnosticReviewError("unsafe review queue claims")


def validate_pose_diagnostic_review_queue_sources(
    queue: Mapping[str, Any]
) -> dict[str, Any]:
    """Recompute every bound source hash before a queue is reviewed."""

    validate_pose_diagnostic_review_queue(queue)
    source = queue.get("source")
    artifacts = source.get("artifacts") if isinstance(source, Mapping) else None
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise PoseDiagnosticReviewError("queue source artifacts are missing")
    canonical_artifacts: dict[str, dict[str, str]] = {}
    for name, binding in artifacts.items():
        if not isinstance(name, str) or not isinstance(binding, Mapping):
            raise PoseDiagnosticReviewError("queue artifact binding is malformed")
        path_value = binding.get("path")
        expected_sha = binding.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected_sha, str):
            raise PoseDiagnosticReviewError("queue artifact path/hash is malformed")
        path = Path(path_value)
        if not path.is_file():
            raise PoseDiagnosticReviewError(f"queue source artifact is missing: {name}")
        actual_sha = _sha256(path)
        if actual_sha != expected_sha.upper():
            raise PoseDiagnosticReviewError(f"queue source artifact SHA mismatch: {name}")
        canonical_artifacts[name] = {"path": str(path), "sha256": actual_sha}
    expected_binding = source.get("artifact_binding_sha256")
    if _canonical_hash(canonical_artifacts) != expected_binding:
        raise PoseDiagnosticReviewError("queue aggregate artifact binding mismatch")
    review_media = queue.get("review_media")
    if not isinstance(review_media, Mapping):
        raise PoseDiagnosticReviewError("review media binding is missing")
    media_path = Path(str(review_media.get("path", "")))
    if not media_path.is_file() or _sha256(media_path) != review_media.get("sha256"):
        raise PoseDiagnosticReviewError("review media SHA mismatch")
    source_video_path = source.get("source_video_path")
    source_video_sha = source.get("source_video_sha256")
    if source_video_path is not None:
        source_video = Path(str(source_video_path))
        if not source_video.is_file() or _sha256(source_video) != source_video_sha:
            raise PoseDiagnosticReviewError("source video SHA mismatch")
    return {
        "status": "passed",
        "validated_artifacts": len(canonical_artifacts),
        "review_media_validated": True,
        "source_video_validated": source_video_path is not None,
        "task_count": len(queue["tasks"]),
    }


def build_pose_diagnostic_review_queue_from_run(
    *,
    run_dir: Path,
    review_video_path: Path,
    review_video_offset_ms: int = 0,
    context_ms: int = 800,
    generated_at: str | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    review_video_path = review_video_path.resolve()
    if not review_video_path.is_file():
        raise PoseDiagnosticReviewError("review video does not exist")
    validation = validate_run_artifacts(run_dir)
    if validation.get("scoring_artifacts_status") != "passed":
        raise PoseDiagnosticReviewError("run scoring artifacts did not validate")
    summary_path = run_dir / "scoring-loop-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    artifacts = {
        "frames_jsonl": run_dir / "frames.jsonl",
        "primary_player_jsonl": run_dir / "primary-player.jsonl",
        "events_jsonl": run_dir / "events.jsonl",
        "scores_jsonl": run_dir / "scores.jsonl",
        "summary_json": summary_path,
    }
    source_artifacts = {
        name: {"path": str(path), "sha256": _sha256(path)}
        for name, path in artifacts.items()
    }
    source = {
        "run_directory": str(run_dir),
        "scoring_loop_version": summary.get("loop_version"),
        "pose_model": summary.get("model_versions", {}),
        "source_video_path": summary.get("provenance", {}).get("video_path"),
        "source_video_sha256": summary.get("provenance", {}).get("video_sha256"),
        "artifacts": source_artifacts,
        "artifact_binding_sha256": _canonical_hash(source_artifacts),
    }
    review_media = {
        "path": str(review_video_path),
        "sha256": _sha256(review_video_path),
        "video_time_offset_ms": review_video_offset_ms,
        "semantics": (
            "human-visible review media only; source artifacts remain authoritative"
        ),
    }
    return build_pose_diagnostic_review_queue(
        video_id=summary["video_id"],
        events=_load_jsonl(artifacts["events_jsonl"]),
        scores=_load_jsonl(artifacts["scores_jsonl"]),
        frames=_load_jsonl(artifacts["frames_jsonl"]),
        primary_timeline=_load_jsonl(artifacts["primary_player_jsonl"]),
        source=source,
        review_media=review_media,
        context_ms=context_ms,
        generated_at=generated_at,
    )


def render_pose_diagnostic_review_html(
    queue: Mapping[str, Any], *, output_path: Path
) -> str:
    validate_pose_diagnostic_review_queue(queue)
    output_path = output_path.resolve()
    media_path = Path(str(queue["review_media"]["path"])).resolve()
    media_url = Path(os.path.relpath(media_path, output_path.parent)).as_posix()
    web_queue = json.loads(json.dumps(queue, ensure_ascii=False, allow_nan=False))
    web_queue["review_media"]["path"] = media_url
    source = web_queue.get("source", {})
    for field in ("run_directory", "source_video_path"):
        value = source.get(field)
        if isinstance(value, str) and value:
            source[field] = Path(
                os.path.relpath(Path(value).resolve(), output_path.parent)
            ).as_posix()
    for artifact in source.get("artifacts", {}).values():
        value = artifact.get("path") if isinstance(artifact, dict) else None
        if isinstance(value, str) and value:
            artifact["path"] = Path(
                os.path.relpath(Path(value).resolve(), output_path.parent)
            ).as_posix()
    payload = json.dumps(
        web_queue, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).replace("<", "\\u003c")
    title = html.escape(f"RallyMate Pose 诊断复核 · {queue['video_id']}")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}}main{{max-width:1500px;margin:auto;padding:18px}}h1{{font-size:22px}}.warning{{background:#3f2a08;border:1px solid #a16207;padding:12px;border-radius:8px}}.layout{{display:grid;grid-template-columns:minmax(360px,1.1fr) minmax(420px,1fr);gap:16px}}video{{width:100%;max-height:72vh;background:#000;position:sticky;top:12px}}table{{width:100%;border-collapse:collapse;background:#111827}}th,td{{border:1px solid #334155;padding:7px;vertical-align:top}}th{{position:sticky;top:0;background:#1e293b}}button,select,input{{font:inherit;padding:5px}}code{{color:#93c5fd}}.muted{{color:#94a3b8}}.active{{outline:3px solid #38bdf8}}@media(max-width:900px){{.layout{{grid-template-columns:1fr}}video{{position:static}}}}
</style></head><body><main>
<h1>{title}</h1><p class="warning">这些条目是启发式候选，不是真值。浏览器选择只导出人工复核 CSV，不会修改 Pose、评分门禁、等级或阈值；正式使用前仍需 Python 校验和独立裁决。</p>
<p>任务 <strong>{queue['counts']['tasks']}</strong> · 唯一候选帧 <strong>{queue['counts']['unique_candidate_frames']}</strong> · 受影响指标实例 <strong>{queue['counts']['unique_affected_indicator_instances']}</strong> · loop <code>{html.escape(str(queue['source']['scoring_loop_version']))}</code></p>
<div class="layout"><section><video id="reviewVideo" controls preload="metadata" src="{html.escape(media_url)}"></video><p><label>标注者 ID <input id="annotator" autocomplete="off"></label> <button id="export">导出复核 CSV</button></p><p class="muted">当前视频时间会按队列记录的 offset 自动换算。草稿仅保存在本浏览器 localStorage。</p></section><section><table><thead><tr><th># / 时间</th><th>诊断与影响</th><th>人工复核</th></tr></thead><tbody id="tasks"></tbody></table></section></div>
<script id="queueData" type="application/json">{payload}</script>
<script>
const q=JSON.parse(document.getElementById('queueData').textContent),video=document.getElementById('reviewVideo'),tbody=document.getElementById('tasks'),annotator=document.getElementById('annotator');
const key='rallymate-pose-review:'+q.source.artifact_binding_sha256,draft=JSON.parse(localStorage.getItem(key)||'{{}}');annotator.value=draft.annotator_id||'';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function save(){{draft.annotator_id=annotator.value;localStorage.setItem(key,JSON.stringify(draft));}}
function seek(task,row){{document.querySelectorAll('tr.active').forEach(x=>x.classList.remove('active'));row.classList.add('active');video.currentTime=task.review_media_time_ms/1000;video.play().catch(()=>{{}});}}
q.tasks.forEach((task,i)=>{{const tr=document.createElement('tr'),state=draft[task.task_id]||{{decision:'pending',notes:'',reviewed_at:''}};tr.innerHTML=`<td><button class="seek">▶ ${{i+1}}</button><br>${{(task.timestamp_ms/1000).toFixed(3)}}s<br><code>${{esc(task.candidate_source_frame_index)}}</code></td><td><strong>${{esc(task.diagnostic_type)}}</strong><br>关节：${{esc(task.joints.join(', ')||'—')}}<br>事件：${{esc(task.event_refs.map(x=>x.event_code).join(', '))}}<br>阻断指标：${{esc(task.affected_indicator_ids.join(', ')||'—')}}<br>仅告警：${{esc(task.advisory_only_indicator_ids.join(', ')||'—')}}</td><td><select>${{q.decision_contract.allowed_values.map(x=>`<option value="${{esc(x)}}" ${{x===state.decision?'selected':''}}>${{esc(x)}}</option>`).join('')}}</select><br><input class="notes" placeholder="备注" value="${{esc(state.notes)}}"></td>`;tr.querySelector('.seek').onclick=()=>seek(task,tr);tr.querySelector('select').onchange=e=>{{draft[task.task_id]=draft[task.task_id]||{{}};draft[task.task_id].decision=e.target.value;draft[task.task_id].reviewed_at=e.target.value==='pending'?'':new Date().toISOString();save();}};tr.querySelector('.notes').oninput=e=>{{draft[task.task_id]=draft[task.task_id]||{{}};draft[task.task_id].notes=e.target.value;save();}};tbody.appendChild(tr);}});annotator.oninput=save;
document.getElementById('export').onclick=()=>{{save();const rows=[['task_id','diagnostic_type','candidate_source_frame_index','timestamp_ms','decision','annotator_id','reviewed_at','notes']];q.tasks.forEach(t=>{{const s=draft[t.task_id]||{{decision:'pending',notes:'',reviewed_at:''}};rows.push([t.task_id,t.diagnostic_type,t.candidate_source_frame_index??'',t.timestamp_ms,s.decision||'pending',draft.annotator_id||'',s.reviewed_at||'',s.notes||'']);}});const csv=rows.map(r=>r.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\\r\\n'),a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\ufeff'+csv],{{type:'text/csv'}}));a.download='pose-diagnostic-review-decisions.csv';a.click();URL.revokeObjectURL(a.href);}};
</script></main></body></html>"""


def write_pose_diagnostic_review_artifacts(
    queue: Mapping[str, Any], *, json_path: Path, html_path: Path, overwrite: bool = False
) -> None:
    validate_pose_diagnostic_review_queue(queue)
    for path in (json_path, html_path):
        if path.exists() and not overwrite:
            raise PoseDiagnosticReviewError(f"refusing to overwrite {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    html_path.write_text(
        render_pose_diagnostic_review_html(queue, output_path=html_path),
        encoding="utf-8",
    )


def decisions_csv_header() -> list[str]:
    return [
        "task_id",
        "diagnostic_type",
        "candidate_source_frame_index",
        "timestamp_ms",
        "decision",
        "annotator_id",
        "reviewed_at",
        "notes",
    ]


def validate_decisions_csv(text: str, queue: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exported decisions without treating them as adjudicated truth."""

    validate_pose_diagnostic_review_queue(queue)
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames != decisions_csv_header():
        raise PoseDiagnosticReviewError("decision CSV header mismatch")
    task_ids = {task["task_id"] for task in queue["tasks"]}
    seen: set[str] = set()
    decision_counts: Counter[str] = Counter()
    for line_number, row in enumerate(reader, start=2):
        task_id = row["task_id"]
        if task_id not in task_ids or task_id in seen:
            raise PoseDiagnosticReviewError(
                f"decision CSV line {line_number} task_id invalid or duplicate"
            )
        seen.add(task_id)
        decision = row["decision"]
        if decision not in DECISION_VALUES:
            raise PoseDiagnosticReviewError(
                f"decision CSV line {line_number} decision invalid"
            )
        if decision != "pending" and not row["annotator_id"].strip():
            raise PoseDiagnosticReviewError(
                f"decision CSV line {line_number} requires annotator_id"
            )
        reviewed_at = row["reviewed_at"].strip()
        if decision != "pending" and not reviewed_at:
            raise PoseDiagnosticReviewError(
                f"decision CSV line {line_number} requires reviewed_at"
            )
        if reviewed_at:
            try:
                datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise PoseDiagnosticReviewError(
                    f"decision CSV line {line_number} reviewed_at is invalid"
                ) from exc
        decision_counts[decision] += 1
    if seen != task_ids:
        raise PoseDiagnosticReviewError("decision CSV must contain every queue task")
    return {
        "status": "review_in_progress" if decision_counts["pending"] else "review_complete_not_adjudicated",
        "task_count": len(seen),
        "decision_counts": dict(sorted(decision_counts.items())),
        "truth_status": "not_adjudicated",
        "quality_gate_modified": False,
    }
