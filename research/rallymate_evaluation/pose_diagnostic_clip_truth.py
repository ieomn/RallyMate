from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from rallymate_evaluation.fs09_phase_truth import probe_fs09_phase_review_clip
from rallymate_evaluation.pose_diagnostic_review import (
    validate_pose_diagnostic_review_queue_sources,
)


PACK_VERSION = "pose-diagnostic-clip-truth-pack-v1.2.2"
PLANNER_VERSION = "score-relevant-pose-diagnostic-clips-v1.1.0"
EVALUATION_VERSION = "pose-diagnostic-candidate-precision-v1.0.0"
POSE_EVIDENCE_VERSION = "pose-diagnostic-adjudication-overlay-v1.0.0"
TARGET_DIAGNOSTIC_TYPES = ("keypoint_jump", "left_right_swap")

COVERAGE_HEADERS = (
    "annotation_id",
    "clip_id",
    "video_id",
    "diagnostic_type",
    "start_source_frame_index",
    "end_source_frame_index",
    "status",
    "annotator_id",
    "annotated_at",
    "null_reason",
    "notes",
)
POSITIVE_HEADERS = (
    "positive_id",
    "coverage_annotation_id",
    "video_id",
    "diagnostic_type",
    "source_frame_index",
    "joint",
    "left_joint",
    "right_joint",
    "notes",
)
ADJUDICATION_HEADERS = (
    "adjudication_id",
    "task_id",
    "decision",
    "source_coverage_annotation_ids",
    "source_positive_annotation_ids",
    "reviewer_id",
    "adjudicated_at",
    "reason",
    "notes",
)


class PoseDiagnosticClipTruthError(ValueError):
    pass


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def source_binding(path: str | Path) -> dict[str, str]:
    resolved = Path(path).resolve()
    return {"path": str(resolved), "sha256": file_sha256(resolved)}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PoseDiagnosticClipTruthError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise PoseDiagnosticClipTruthError(
                f"expected JSON object at {path}:{line_number}"
            )
        output.append(value)
    return output


def _parse_iso8601(value: str, field: str) -> None:
    if not value:
        raise PoseDiagnosticClipTruthError(f"{field} must be non-empty ISO-8601")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PoseDiagnosticClipTruthError(f"{field} must be ISO-8601") from exc


def _read_csv(path: Path, headers: Sequence[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != tuple(headers):
            raise PoseDiagnosticClipTruthError(f"CSV headers drifted: {path}")
        return [dict(row) for row in reader]


def _write_empty_csv(path: Path, headers: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerow(headers)


def _median_frame_rate(timestamps_ms: Sequence[int]) -> float:
    deltas = sorted(
        right - left
        for left, right in zip(timestamps_ms, timestamps_ms[1:])
        if right > left
    )
    if not deltas:
        raise PoseDiagnosticClipTruthError("timeline needs at least two timestamps")
    median = deltas[len(deltas) // 2]
    raw = 1000.0 / median
    for expected in (24.0, 25.0, 30.0, 50.0, 60.0):
        if abs(raw - expected) <= 0.75:
            return expected
    return round(raw, 6)


def _merge_task_windows(tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        tasks,
        key=lambda row: (
            int(row["clip_start_ms"]),
            int(row["clip_end_ms"]),
            str(row["task_id"]),
        ),
    )
    clusters: list[dict[str, Any]] = []
    for task in ordered:
        start_ms = int(task["clip_start_ms"])
        end_ms = int(task["clip_end_ms"])
        if not clusters or start_ms > clusters[-1]["requested_end_ms"]:
            clusters.append(
                {
                    "requested_start_ms": start_ms,
                    "requested_end_ms": end_ms,
                    "task_ids": [str(task["task_id"])],
                }
            )
        else:
            clusters[-1]["requested_end_ms"] = max(
                clusters[-1]["requested_end_ms"], end_ms
            )
            clusters[-1]["task_ids"].append(str(task["task_id"]))
    return clusters


def _timeline_frame_slice(
    timeline: Sequence[Mapping[str, Any]], start_ms: int, end_ms: int
) -> list[dict[str, int]]:
    selected = [
        {
            "source_frame_index": int(row["source_frame_index"]),
            "timestamp_ms": int(row["timestamp_ms"]),
        }
        for row in timeline
        if start_ms <= int(row["timestamp_ms"]) <= end_ms
    ]
    if not selected:
        raise PoseDiagnosticClipTruthError(
            f"review interval contains no timeline frames: {start_ms}..{end_ms}"
        )
    indexes = [row["source_frame_index"] for row in selected]
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise PoseDiagnosticClipTruthError("review timeline source frames are not contiguous")
    return selected


def build_video_clip_pose_evidence(
    video: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Replay primary-player model poses into adjudicator-only clip sidecars.

    The returned evidence is model output, never ground truth.  It deliberately
    includes every source frame in each clip so the reviewer can inspect motion
    before and after a sealed candidate instead of judging a single screenshot.
    """

    frames_path = Path(str(video["frames_observations"]["path"])).resolve()
    timeline_path = Path(str(video["primary_timeline"]["path"])).resolve()
    frame_rows = _jsonl(frames_path)
    timeline_rows = _jsonl(timeline_path)
    frames_by_index = {
        int(row["frame"]["index"]): row
        for row in frame_rows
        if isinstance(row.get("frame"), Mapping)
    }
    timeline_by_index = {
        int(row["source_frame_index"]): row for row in timeline_rows
    }
    if len(frames_by_index) != len(frame_rows) or len(timeline_by_index) != len(
        timeline_rows
    ):
        raise PoseDiagnosticClipTruthError(
            "pose evidence source frame indexes must be unique"
        )

    output: dict[str, dict[str, Any]] = {}
    for segment in video["segments"]:
        evidence_frames: list[dict[str, Any]] = []
        observed_formats: set[str] = set()
        widths: set[int] = set()
        heights: set[int] = set()
        for source_index, expected_timestamp in zip(
            range(
                int(segment["source_start_frame_index"]),
                int(segment["source_end_frame_index"]) + 1,
            ),
            segment["source_timestamps_ms"],
        ):
            frame_row = frames_by_index.get(source_index)
            timeline_row = timeline_by_index.get(source_index)
            if frame_row is None or timeline_row is None:
                raise PoseDiagnosticClipTruthError(
                    f"pose evidence source frame is missing: {source_index}"
                )
            frame_meta = frame_row["frame"]
            frame_timestamp = int(frame_meta["timestamp_ms"])
            timeline_timestamp = int(timeline_row["timestamp_ms"])
            if frame_timestamp != int(expected_timestamp) or timeline_timestamp != int(
                expected_timestamp
            ):
                raise PoseDiagnosticClipTruthError(
                    f"pose evidence timestamp mismatch at frame {source_index}"
                )
            width = int(frame_meta["width"])
            height = int(frame_meta["height"])
            if width <= 0 or height <= 0:
                raise PoseDiagnosticClipTruthError("pose evidence frame size is invalid")
            widths.add(width)
            heights.add(height)
            source_track_id = timeline_row.get("source_track_id")
            matching = [
                pose
                for pose in frame_row.get("poses", [])
                if pose.get("person_track_id") == source_track_id
            ]
            if len(matching) > 1:
                raise PoseDiagnosticClipTruthError(
                    f"multiple primary poses at frame {source_index}"
                )
            pose = matching[0] if matching else None
            if bool(timeline_row.get("pose_present")) != (pose is not None):
                raise PoseDiagnosticClipTruthError(
                    f"primary timeline/pose mismatch at frame {source_index}"
                )
            compact_keypoints: list[list[Any]] = []
            pose_confidence: float | None = None
            if pose is not None:
                keypoint_format = str(pose.get("keypoint_format") or "")
                if not keypoint_format:
                    raise PoseDiagnosticClipTruthError(
                        f"pose evidence keypoint format missing at frame {source_index}"
                    )
                observed_formats.add(keypoint_format)
                pose_confidence = float(pose.get("confidence", 0.0))
                if not math.isfinite(pose_confidence):
                    raise PoseDiagnosticClipTruthError(
                        f"pose evidence confidence is non-finite at frame {source_index}"
                    )
                seen_names: set[str] = set()
                for keypoint in pose.get("keypoints", []):
                    name = str(keypoint.get("name") or "")
                    if not name or name in seen_names:
                        raise PoseDiagnosticClipTruthError(
                            f"pose evidence keypoint names are invalid at frame {source_index}"
                        )
                    seen_names.add(name)
                    values = (
                        float(keypoint["x_px"]),
                        float(keypoint["y_px"]),
                        float(keypoint["confidence"]),
                    )
                    if not all(math.isfinite(value) for value in values):
                        raise PoseDiagnosticClipTruthError(
                            f"pose evidence keypoint is non-finite at frame {source_index}"
                        )
                    compact_keypoints.append(
                        [name, round(values[0], 3), round(values[1], 3), round(values[2], 5)]
                    )
            evidence_frames.append(
                {
                    "source_frame_index": source_index,
                    "timestamp_ms": int(expected_timestamp),
                    "source_track_id": source_track_id,
                    "pose_present": pose is not None,
                    "pose_confidence": (
                        None if pose_confidence is None else round(pose_confidence, 5)
                    ),
                    "keypoints": compact_keypoints,
                }
            )
        if len(widths) != 1 or len(heights) != 1 or len(observed_formats) != 1:
            raise PoseDiagnosticClipTruthError(
                f"pose evidence geometry/topology drifted within {segment['clip_id']}"
            )
        output[str(segment["clip_id"])] = {
            "schema_version": "1.0.0",
            "evidence_version": POSE_EVIDENCE_VERSION,
            "clip_id": str(segment["clip_id"]),
            "video_id": str(video["video_id"]),
            "frame_width_px": next(iter(widths)),
            "frame_height_px": next(iter(heights)),
            "keypoint_format": next(iter(observed_formats)),
            "source": {
                "frames_observations_sha256": str(
                    video["frames_observations"]["sha256"]
                ),
                "primary_timeline_sha256": str(video["primary_timeline"]["sha256"]),
            },
            "frames": evidence_frames,
            "safety": {
                "model_output_is_ground_truth": False,
                "loaded_by_blind_review_page": False,
                "quality_gate_modified": False,
                "grade_generated": False,
                "threshold_generated": False,
            },
        }
    return output


def build_clip_plan(
    queue_paths: Sequence[str | Path],
    *,
    m78_audit_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not queue_paths:
        raise PoseDiagnosticClipTruthError("at least one source queue is required")
    audit_path = Path(m78_audit_path).resolve()
    audit = _json(audit_path)
    if audit.get("status") != "taxonomy_centralized_behavior_preserved_no_gate_change":
        raise PoseDiagnosticClipTruthError("M78 audit is not behavior-preserved")

    videos: list[dict[str, Any]] = []
    sealed_tasks: list[dict[str, Any]] = []
    seen_videos: set[str] = set()
    seen_tasks: set[str] = set()
    for queue_value in queue_paths:
        queue_path = Path(queue_value).resolve()
        queue = _json(queue_path)
        validate_pose_diagnostic_review_queue_sources(queue)
        video_id = str(queue["video_id"])
        if video_id in seen_videos:
            raise PoseDiagnosticClipTruthError(f"duplicate video queue: {video_id}")
        seen_videos.add(video_id)
        tasks = [
            task
            for task in queue["tasks"]
            if task["diagnostic_type"] in TARGET_DIAGNOSTIC_TYPES
            and task["affected_indicator_instances"]
        ]
        if not tasks:
            raise PoseDiagnosticClipTruthError(
                f"queue has no score-relevant jump/swap candidates: {queue_path}"
            )
        timeline_path = Path(
            queue["source"]["artifacts"]["primary_player_jsonl"]["path"]
        ).resolve()
        frames_path = Path(
            queue["source"]["artifacts"]["frames_jsonl"]["path"]
        ).resolve()
        timeline = _jsonl(timeline_path)
        timestamps = [int(row["timestamp_ms"]) for row in timeline]
        fps = _median_frame_rate(timestamps)
        clusters = _merge_task_windows(tasks)
        task_to_clip: dict[str, str] = {}
        segments: list[dict[str, Any]] = []
        reel_frame_cursor = 0
        for index, cluster in enumerate(clusters, start=1):
            clip_id = f"m79-{video_id}-clip-{index:03d}"
            frames = _timeline_frame_slice(
                timeline,
                cluster["requested_start_ms"],
                cluster["requested_end_ms"],
            )
            source_start = frames[0]["source_frame_index"]
            source_end = frames[-1]["source_frame_index"]
            frame_count = len(frames)
            segment = {
                "clip_id": clip_id,
                "sequence_index": index - 1,
                "requested_start_ms": cluster["requested_start_ms"],
                "requested_end_ms": cluster["requested_end_ms"],
                "source_start_frame_index": source_start,
                "source_end_frame_index": source_end,
                "source_start_timestamp_ms": frames[0]["timestamp_ms"],
                "source_end_timestamp_ms": frames[-1]["timestamp_ms"],
                "source_timestamps_ms": [row["timestamp_ms"] for row in frames],
                "frame_count": frame_count,
                "reel_start_frame_index": reel_frame_cursor,
                "reel_end_frame_index": reel_frame_cursor + frame_count - 1,
                "candidate_task_count": len(cluster["task_ids"]),
            }
            segments.append(segment)
            reel_frame_cursor += frame_count
            for task_id in cluster["task_ids"]:
                if task_id in task_to_clip:
                    raise PoseDiagnosticClipTruthError(
                        f"task assigned to multiple clips: {task_id}"
                    )
                task_to_clip[task_id] = clip_id
        task_by_id = {str(task["task_id"]): task for task in tasks}
        if set(task_to_clip) != set(task_by_id):
            raise PoseDiagnosticClipTruthError("not every target task was assigned a clip")
        for task_id, task in task_by_id.items():
            if task_id in seen_tasks:
                raise PoseDiagnosticClipTruthError(f"duplicate task_id: {task_id}")
            seen_tasks.add(task_id)
            sealed_tasks.append(
                {
                    "task_id": task_id,
                    "clip_id": task_to_clip[task_id],
                    "video_id": video_id,
                    "diagnostic_type": str(task["diagnostic_type"]),
                    "diagnostic_flag": str(task["diagnostic_flag"]),
                    "source_frame_index": int(task["candidate_source_frame_index"]),
                    "timestamp_ms": int(task["timestamp_ms"]),
                    "joints": [str(value) for value in task["joints"]],
                    "joint_pairs": [
                        [str(pair[0]), str(pair[1])]
                        for pair in task["joint_pairs"]
                    ],
                    "affected_indicator_instances": sorted(
                        str(value) for value in task["affected_indicator_instances"]
                    ),
                }
            )
        videos.append(
            {
                "video_id": video_id,
                "queue": {
                    **source_binding(queue_path),
                    "queue_version": queue["queue_version"],
                    "artifact_binding_sha256": queue["source"][
                        "artifact_binding_sha256"
                    ],
                },
                "source_video": source_binding(queue["review_media"]["path"]),
                "primary_timeline": source_binding(timeline_path),
                "frames_observations": source_binding(frames_path),
                "fps": fps,
                "segments": segments,
                "reel_frame_count": reel_frame_cursor,
            }
        )

    videos.sort(key=lambda item: item["video_id"])
    sealed_tasks.sort(
        key=lambda item: (
            item["video_id"],
            item["timestamp_ms"],
            item["diagnostic_type"],
            item["task_id"],
        )
    )
    sealed = {
        "schema_version": "1.0.0",
        "sealed_version": "pose-diagnostic-score-relevant-candidates-v1.0.0",
        "tasks": sealed_tasks,
        "safety": {
            "candidate_tasks_are_truth": False,
            "loaded_by_blind_review_page": False,
            "quality_gate_modified": False,
            "grade_generated": False,
            "threshold_generated": False,
        },
    }
    unique_instances = {
        instance
        for task in sealed_tasks
        for instance in task["affected_indicator_instances"]
    }
    plan = {
        "schema_version": "1.0.0",
        "planner_version": PLANNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "m78_audit": source_binding(audit_path),
            "taxonomy": dict(audit["source"]["taxonomy"]),
        },
        "diagnostic_types": list(TARGET_DIAGNOSTIC_TYPES),
        "videos": videos,
        "counts": {
            "videos": len(videos),
            "clips": sum(len(video["segments"]) for video in videos),
            "candidate_tasks": len(sealed_tasks),
            "candidate_tasks_by_type": dict(
                sorted(Counter(task["diagnostic_type"] for task in sealed_tasks).items())
            ),
            "unique_affected_indicator_instances": len(unique_instances),
            "source_frame_observations": sum(
                video["reel_frame_count"] for video in videos
            ),
        },
        "safety": {
            "candidate_only_review_measures_recall": False,
            "candidate_tasks_exposed_to_blind_annotators": False,
            "model_overlay_rendered": False,
            "quality_gate_modified": False,
            "grade_generated": False,
            "threshold_generated": False,
            "accuracy_claim": False,
        },
    }
    return plan, sealed


def validate_clip_plan(plan: Mapping[str, Any], sealed: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != "1.0.0" or plan.get(
        "planner_version"
    ) != PLANNER_VERSION:
        raise PoseDiagnosticClipTruthError("clip plan version is invalid")
    if plan.get("diagnostic_types") != list(TARGET_DIAGNOSTIC_TYPES):
        raise PoseDiagnosticClipTruthError("clip plan diagnostic scope drifted")
    if any(plan.get("safety", {}).values()):
        raise PoseDiagnosticClipTruthError("clip plan contains unsafe claims")
    if sealed.get("sealed_version") != "pose-diagnostic-score-relevant-candidates-v1.0.0":
        raise PoseDiagnosticClipTruthError("sealed candidate version is invalid")
    if any(sealed.get("safety", {}).values()):
        raise PoseDiagnosticClipTruthError("sealed candidates contain unsafe claims")
    tasks = sealed.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise PoseDiagnosticClipTruthError("sealed candidates are empty")
    task_ids = [task.get("task_id") for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise PoseDiagnosticClipTruthError("sealed candidate task IDs are not unique")
    clip_ids: set[str] = set()
    candidate_count = 0
    frame_count = 0
    for video in plan.get("videos", []):
        for source_name in (
            "queue",
            "source_video",
            "primary_timeline",
            "frames_observations",
        ):
            binding = video.get(source_name)
            if not isinstance(binding, Mapping) or not binding.get("path") or not binding.get(
                "sha256"
            ):
                raise PoseDiagnosticClipTruthError(
                    f"clip plan video source binding is missing: {source_name}"
                )
        previous_end = -1
        for segment in video.get("segments", []):
            clip_id = segment.get("clip_id")
            if not isinstance(clip_id, str) or not clip_id or clip_id in clip_ids:
                raise PoseDiagnosticClipTruthError("clip IDs must be unique")
            clip_ids.add(clip_id)
            if segment.get("reel_start_frame_index") != previous_end + 1:
                raise PoseDiagnosticClipTruthError("reel segments are not contiguous")
            if segment.get("frame_count") != len(segment.get("source_timestamps_ms", [])):
                raise PoseDiagnosticClipTruthError("segment timestamp count mismatch")
            previous_end = int(segment["reel_end_frame_index"])
            candidate_count += int(segment["candidate_task_count"])
            frame_count += int(segment["frame_count"])
        if previous_end + 1 != video.get("reel_frame_count"):
            raise PoseDiagnosticClipTruthError("reel frame count mismatch")
    if any(task.get("clip_id") not in clip_ids for task in tasks):
        raise PoseDiagnosticClipTruthError("sealed task references unknown clip")
    counts = plan.get("counts", {})
    if (
        counts.get("clips") != len(clip_ids)
        or counts.get("candidate_tasks") != len(tasks)
        or candidate_count != len(tasks)
        or counts.get("source_frame_observations") != frame_count
    ):
        raise PoseDiagnosticClipTruthError("clip plan counts drifted")


def _split_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _positive_matches_task(positive: Mapping[str, str], task: Mapping[str, Any]) -> bool:
    if (
        positive["video_id"] != task["video_id"]
        or positive["diagnostic_type"] != task["diagnostic_type"]
        or int(positive["source_frame_index"]) != task["source_frame_index"]
    ):
        return False
    if task["diagnostic_type"] == "keypoint_jump":
        return positive["joint"] in task["joints"]
    pair = {positive["left_joint"], positive["right_joint"]}
    return any(pair == set(candidate) for candidate in task["joint_pairs"])


def compile_clip_truth(pack_directory: str | Path) -> dict[str, Any]:
    pack = Path(pack_directory).resolve()
    manifest = _json(pack / "manifest.json")
    validate_clip_truth_manifest_sources(manifest)
    sealed = _json(Path(manifest["artifacts"]["sealed_candidates"]["path"]))
    plan = manifest["plan"]
    validate_clip_plan(plan, sealed)
    coverage = _read_csv(pack / "coverage-annotations.csv", COVERAGE_HEADERS)
    positives = _read_csv(pack / "positive-annotations.csv", POSITIVE_HEADERS)
    adjudications = _read_csv(
        pack / "candidate-adjudications.csv", ADJUDICATION_HEADERS
    )

    clips = {
        segment["clip_id"]: (video, segment)
        for video in plan["videos"]
        for segment in video["segments"]
    }
    coverage_by_id: dict[str, dict[str, str]] = {}
    coverage_key_annotators: set[tuple[str, str, str]] = set()
    for row in coverage:
        annotation_id = row["annotation_id"]
        clip_id = row["clip_id"]
        if not annotation_id or annotation_id in coverage_by_id or clip_id not in clips:
            raise PoseDiagnosticClipTruthError("coverage annotation ID/clip is invalid")
        video, segment = clips[clip_id]
        if row["video_id"] != video["video_id"]:
            raise PoseDiagnosticClipTruthError("coverage video binding mismatch")
        if row["diagnostic_type"] not in TARGET_DIAGNOSTIC_TYPES:
            raise PoseDiagnosticClipTruthError("coverage diagnostic type is invalid")
        if (
            int(row["start_source_frame_index"])
            != segment["source_start_frame_index"]
            or int(row["end_source_frame_index"])
            != segment["source_end_frame_index"]
        ):
            raise PoseDiagnosticClipTruthError("coverage interval must equal the full clip")
        if row["status"] not in {"completed", "unobservable"}:
            raise PoseDiagnosticClipTruthError("coverage status is invalid")
        if not row["annotator_id"]:
            raise PoseDiagnosticClipTruthError("coverage annotator_id is required")
        _parse_iso8601(row["annotated_at"], "coverage.annotated_at")
        if row["status"] == "completed" and row["null_reason"]:
            raise PoseDiagnosticClipTruthError("completed coverage cannot have null_reason")
        if row["status"] == "unobservable" and not row["null_reason"]:
            raise PoseDiagnosticClipTruthError("unobservable coverage needs null_reason")
        key = (clip_id, row["diagnostic_type"], row["annotator_id"])
        if key in coverage_key_annotators:
            raise PoseDiagnosticClipTruthError("duplicate annotator coverage for clip/type")
        coverage_key_annotators.add(key)
        coverage_by_id[annotation_id] = row

    positive_by_id: dict[str, dict[str, str]] = {}
    for row in positives:
        positive_id = row["positive_id"]
        coverage_id = row["coverage_annotation_id"]
        if not positive_id or positive_id in positive_by_id:
            raise PoseDiagnosticClipTruthError("positive IDs must be unique")
        source_coverage = coverage_by_id.get(coverage_id)
        if source_coverage is None or source_coverage["status"] != "completed":
            raise PoseDiagnosticClipTruthError("positive needs completed source coverage")
        if (
            row["video_id"] != source_coverage["video_id"]
            or row["diagnostic_type"] != source_coverage["diagnostic_type"]
        ):
            raise PoseDiagnosticClipTruthError("positive/coverage scope mismatch")
        frame = int(row["source_frame_index"])
        if not (
            int(source_coverage["start_source_frame_index"])
            <= frame
            <= int(source_coverage["end_source_frame_index"])
        ):
            raise PoseDiagnosticClipTruthError("positive lies outside source coverage")
        if row["diagnostic_type"] == "keypoint_jump":
            if not row["joint"] or row["left_joint"] or row["right_joint"]:
                raise PoseDiagnosticClipTruthError("jump positive joint fields are invalid")
        else:
            if row["joint"] or not row["left_joint"] or not row["right_joint"]:
                raise PoseDiagnosticClipTruthError("swap positive joint fields are invalid")
        positive_by_id[positive_id] = row

    task_by_id = {task["task_id"]: task for task in sealed["tasks"]}
    adjudication_by_task: dict[str, dict[str, Any]] = {}
    for row in adjudications:
        task = task_by_id.get(row["task_id"])
        if not row["adjudication_id"] or task is None or row["task_id"] in adjudication_by_task:
            raise PoseDiagnosticClipTruthError("adjudication ID/task is invalid")
        if row["decision"] not in {
            "confirmed_true",
            "confirmed_false",
            "unobservable",
        }:
            raise PoseDiagnosticClipTruthError("adjudication decision is invalid")
        coverage_ids = _split_ids(row["source_coverage_annotation_ids"])
        if len(coverage_ids) != 2 or len(set(coverage_ids)) != 2:
            raise PoseDiagnosticClipTruthError(
                "adjudication requires exactly two coverage annotations"
            )
        sources = [coverage_by_id.get(value) for value in coverage_ids]
        if any(source is None for source in sources):
            raise PoseDiagnosticClipTruthError("adjudication coverage source is missing")
        assert all(source is not None for source in sources)
        if any(
            source["clip_id"] != task["clip_id"]
            or source["video_id"] != task["video_id"]
            or source["diagnostic_type"] != task["diagnostic_type"]
            for source in sources
        ):
            raise PoseDiagnosticClipTruthError("adjudication coverage scope mismatch")
        annotators = {source["annotator_id"] for source in sources}
        if len(annotators) != 2:
            raise PoseDiagnosticClipTruthError(
                "adjudication requires two independent annotators"
            )
        reviewer = row["reviewer_id"]
        if not reviewer or reviewer in annotators:
            raise PoseDiagnosticClipTruthError(
                "adjudication requires an independent reviewer"
            )
        if (
            any(source["status"] == "unobservable" for source in sources)
            and row["decision"] != "unobservable"
        ):
            raise PoseDiagnosticClipTruthError(
                "unobservable source coverage cannot produce an observable decision"
            )
        _parse_iso8601(row["adjudicated_at"], "adjudication.adjudicated_at")
        if row["decision"] == "unobservable":
            if not row["reason"]:
                raise PoseDiagnosticClipTruthError("unobservable decision needs reason")
        elif row["reason"]:
            raise PoseDiagnosticClipTruthError("observable decision reason must be empty")
        allowed_positive_ids = {
            positive_id
            for positive_id, positive in positive_by_id.items()
            if positive["coverage_annotation_id"] in coverage_ids
            and _positive_matches_task(positive, task)
        }
        declared_positive_ids = set(_split_ids(row["source_positive_annotation_ids"]))
        if declared_positive_ids != allowed_positive_ids:
            raise PoseDiagnosticClipTruthError(
                "adjudication positive source IDs do not exactly replay raw annotations"
            )
        if row["decision"] == "confirmed_true" and not allowed_positive_ids:
            raise PoseDiagnosticClipTruthError(
                "confirmed true needs at least one independent raw positive"
            )
        adjudication_by_task[row["task_id"]] = {
            **row,
            "source_coverage_annotation_ids": coverage_ids,
            "source_positive_annotation_ids": sorted(allowed_positive_ids),
            "annotator_ids": sorted(annotators),
        }

    counts_by_type: dict[str, dict[str, int]] = {}
    metrics_by_type: dict[str, Any] | None = None
    for diagnostic_type in TARGET_DIAGNOSTIC_TYPES:
        tasks = [
            task for task in sealed["tasks"] if task["diagnostic_type"] == diagnostic_type
        ]
        decisions = [
            adjudication_by_task[task["task_id"]]
            for task in tasks
            if task["task_id"] in adjudication_by_task
        ]
        counter = Counter(item["decision"] for item in decisions)
        counts_by_type[diagnostic_type] = {
            "candidate_tasks": len(tasks),
            "adjudicated_tasks": len(decisions),
            "confirmed_true": counter["confirmed_true"],
            "confirmed_false": counter["confirmed_false"],
            "unobservable": counter["unobservable"],
        }
    complete = len(adjudication_by_task) == len(sealed["tasks"])
    if complete:
        metrics_by_type = {}
        for diagnostic_type, counts in counts_by_type.items():
            denominator = counts["confirmed_true"] + counts["confirmed_false"]
            metrics_by_type[diagnostic_type] = {
                "candidate_precision": (
                    None
                    if denominator == 0
                    else round(counts["confirmed_true"] / denominator, 8)
                ),
                "recall": None,
                "f1": None,
                "recall_unavailable_reason": "candidate_window_review_not_full_timeline_coverage",
            }

    return {
        "schema_version": "1.0.0",
        "evaluation_version": EVALUATION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed_candidate_precision_only" if complete else "annotation_required",
        "source": {
            "manifest": source_binding(pack / "manifest.json"),
            "sealed_candidates": source_binding(
                Path(manifest["artifacts"]["sealed_candidates"]["path"])
            ),
            "coverage_annotations": source_binding(pack / "coverage-annotations.csv"),
            "positive_annotations": source_binding(pack / "positive-annotations.csv"),
            "candidate_adjudications": source_binding(
                pack / "candidate-adjudications.csv"
            ),
        },
        "counts": {
            "candidate_tasks": len(sealed["tasks"]),
            "coverage_annotations": len(coverage),
            "positive_annotations": len(positives),
            "candidate_adjudications": len(adjudication_by_task),
            "by_diagnostic_type": counts_by_type,
        },
        "metrics_by_diagnostic_type": metrics_by_type,
        "limitations": {
            "candidate_precision_evaluable_when_complete": True,
            "full_timeline_recall_evaluable": False,
            "candidate_windows_reveal_a_candidate_exists_somewhere_in_window": True,
            "blind_page_reveals_candidate_frame_or_joint": False,
        },
        "acceptance": {
            "quality_gate_change_allowed": False,
            "runtime_candidate_override_allowed": False,
            "f2_to_f3_allowed": False,
        },
        "safety": {
            "partial_annotations_report_metrics": False,
            "candidate_precision_is_accuracy": False,
            "recall_claim": False,
            "quality_gate_modified": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }


def validate_clip_truth_manifest_sources(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != "1.0.0" or manifest.get(
        "pack_version"
    ) != PACK_VERSION:
        raise PoseDiagnosticClipTruthError("M79 manifest version is invalid")
    if manifest.get("status") != "annotation_required":
        raise PoseDiagnosticClipTruthError("M79 pack must start annotation_required")
    if any(manifest.get("safety", {}).values()):
        raise PoseDiagnosticClipTruthError("M79 manifest contains unsafe claims")
    plan = manifest.get("plan")
    artifacts = manifest.get("artifacts")
    if not isinstance(plan, Mapping) or not isinstance(artifacts, Mapping):
        raise PoseDiagnosticClipTruthError("M79 manifest plan/artifacts missing")
    sealed_binding = artifacts.get("sealed_candidates")
    if not isinstance(sealed_binding, Mapping):
        raise PoseDiagnosticClipTruthError("sealed candidate binding missing")
    sealed_path = Path(str(sealed_binding.get("path"))).resolve()
    if file_sha256(sealed_path) != sealed_binding.get("sha256"):
        raise PoseDiagnosticClipTruthError("sealed candidate hash mismatch")
    sealed = _json(sealed_path)
    if canonical_sha256(sealed) != sealed_binding.get("canonical_sha256"):
        raise PoseDiagnosticClipTruthError("sealed candidate canonical hash mismatch")
    validate_clip_plan(plan, sealed)
    for source_name in ("m78_audit", "taxonomy"):
        binding = plan["sources"][source_name]
        if file_sha256(binding["path"]) != binding["sha256"]:
            raise PoseDiagnosticClipTruthError(f"M79 {source_name} source hash mismatch")
    for video in plan["videos"]:
        for binding_name in (
            "queue",
            "source_video",
            "primary_timeline",
            "frames_observations",
        ):
            binding = video[binding_name]
            if file_sha256(binding["path"]) != binding["sha256"]:
                raise PoseDiagnosticClipTruthError(
                    f"M79 {video['video_id']} {binding_name} hash mismatch"
                )
        queue = _json(Path(video["queue"]["path"]))
        validate_pose_diagnostic_review_queue_sources(queue)
        expected_pose_evidence = build_video_clip_pose_evidence(video)
        for segment in video["segments"]:
            clip_media = segment.get("clip_media")
            if not isinstance(clip_media, Mapping):
                raise PoseDiagnosticClipTruthError("M79 clip media binding is missing")
            if file_sha256(clip_media["path"]) != clip_media["sha256"]:
                raise PoseDiagnosticClipTruthError("M79 clip media hash mismatch")
            current_probe = probe_fs09_phase_review_clip(clip_media["path"])
            if current_probe != clip_media.get("probe"):
                raise PoseDiagnosticClipTruthError("M79 clip media probe drifted")
            if current_probe["frame_count"] != segment["frame_count"]:
                raise PoseDiagnosticClipTruthError("M79 clip media frame count mismatch")
            if str(current_probe["codec_fourcc"]).lower() != "h264":
                raise PoseDiagnosticClipTruthError("M79 clip media is not browser H.264")
            pose_binding = segment.get("adjudication_pose_evidence")
            if not isinstance(pose_binding, Mapping):
                raise PoseDiagnosticClipTruthError(
                    "M79 adjudication pose evidence binding is missing"
                )
            pose_path = Path(str(pose_binding.get("path"))).resolve()
            if file_sha256(pose_path) != pose_binding.get("sha256"):
                raise PoseDiagnosticClipTruthError(
                    "M79 adjudication pose evidence hash mismatch"
                )
            pose_evidence = _json(pose_path)
            if canonical_sha256(pose_evidence) != pose_binding.get(
                "canonical_sha256"
            ):
                raise PoseDiagnosticClipTruthError(
                    "M79 adjudication pose evidence canonical hash mismatch"
                )
            expected_evidence = expected_pose_evidence[str(segment["clip_id"])]
            if pose_evidence != expected_evidence:
                raise PoseDiagnosticClipTruthError(
                    "M79 adjudication pose evidence does not replay from source frames"
                )
            if (
                int(pose_binding.get("frame_count", -1))
                != len(pose_evidence["frames"])
                or int(pose_binding.get("pose_frame_count", -1))
                != sum(bool(row["pose_present"]) for row in pose_evidence["frames"])
                or pose_binding.get("keypoint_format")
                != pose_evidence["keypoint_format"]
            ):
                raise PoseDiagnosticClipTruthError(
                    "M79 adjudication pose evidence summary drifted"
                )

    # Rebuild the entire plan and sealed candidate projection from the bound queues.
    # Matching hashes alone only prove internal consistency; this replay prevents a
    # caller from editing a candidate and then updating all self-declared hashes.
    expected_plan, expected_sealed = build_clip_plan(
        [Path(video["queue"]["path"]) for video in plan["videos"]],
        m78_audit_path=plan["sources"]["m78_audit"]["path"],
    )
    if expected_sealed != sealed:
        raise PoseDiagnosticClipTruthError(
            "M79 sealed candidates do not replay from source queues"
        )

    def replay_projection(value: Mapping[str, Any]) -> dict[str, Any]:
        projected = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
        projected.pop("generated_at", None)
        for video in projected.get("videos", []):
            for segment in video.get("segments", []):
                segment.pop("clip_media", None)
                segment.pop("media_url", None)
                segment.pop("adjudication_pose_evidence", None)
        return projected

    if replay_projection(expected_plan) != replay_projection(plan):
        raise PoseDiagnosticClipTruthError(
            "M79 clip plan does not replay from source queues"
        )


def validate_empty_clip_truth_report(report: Mapping[str, Any]) -> None:
    if (
        report.get("schema_version") != "1.0.0"
        or report.get("evaluation_version") != EVALUATION_VERSION
        or report.get("status") != "annotation_required"
        or report.get("metrics_by_diagnostic_type") is not None
    ):
        raise PoseDiagnosticClipTruthError("M79 empty report state is invalid")
    counts = report.get("counts", {})
    if any(
        int(counts.get(field, -1)) != 0
        for field in (
            "coverage_annotations",
            "positive_annotations",
            "candidate_adjudications",
        )
    ):
        raise PoseDiagnosticClipTruthError("M79 published report is not empty")
    if any(report.get("acceptance", {}).values()) or any(
        report.get("safety", {}).values()
    ):
        raise PoseDiagnosticClipTruthError("M79 empty report has unsafe claims")


def initialize_empty_annotation_csvs(pack_directory: str | Path) -> None:
    pack = Path(pack_directory)
    _write_empty_csv(pack / "coverage-annotations.csv", COVERAGE_HEADERS)
    _write_empty_csv(pack / "positive-annotations.csv", POSITIVE_HEADERS)
    _write_empty_csv(pack / "candidate-adjudications.csv", ADJUDICATION_HEADERS)


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
