from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from rallymate_features.event_features import FEATURE_DEFINITIONS


OBSERVER_SCHEMA_VERSION = "1.1.0"
OBSERVER_VERSION = "dynamic-scoring-observer-v1.1.0"

INDICATOR_LABELS = {
    "FS01-M02": "重心预加载",
    "FS01-M03": "双脚轻微离地代理",
    "FS01-M04": "双脚分开落地代理",
    "FS01-M05": "重心重新分配并准备启动",
    "FS02-M02": "重心向目标方向转换",
    "FS02-M03": "支撑侧伸展与身体加速代理",
    "FS02-M04": "启动脚运动起点代理",
    "FS02-M05": "第一步稳定与方向建立代理",
    "FS09-M01": "判断身体惯性方向",
    "FS09-M02": "制动脚减速代理",
    "FS09-M03": "下肢吸收身体惯性",
    "FS09-M04": "重心重新稳定",
    "FS09-M05": "身体进入稳定控制状态",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.upper()
        and all(character in "0123456789ABCDEF" for character in value)
    )


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            records.append(value)
    return records


def _round_optional(value: Any, digits: int) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _required_joints(indicator: Mapping[str, Any]) -> list[str]:
    joints: set[str] = set()
    for feature_name in indicator.get("required_features", []):
        definition = FEATURE_DEFINITIONS.get(str(feature_name))
        if not isinstance(definition, Mapping):
            continue
        required = definition.get("required_joints")
        if isinstance(required, list):
            joints.update(str(item) for item in required)
    return sorted(joints)


def _compact_feature(feature: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(feature.get("feature_name", "")),
        "version": feature.get("feature_version"),
        "value": feature.get("value"),
        "unit": feature.get("unit"),
        "valid": bool(feature.get("valid", False)),
        "confidence": _round_optional(feature.get("confidence"), 4),
        "reason": feature.get("reason"),
        "source_frames": list(feature.get("source_frames") or []),
    }


def _select_pose(frame: Mapping[str, Any], source_track_id: Any) -> Mapping[str, Any] | None:
    if source_track_id is None:
        return None
    matches = [
        pose
        for pose in frame.get("poses", [])
        if pose.get("person_track_id") == source_track_id
    ]
    if len(matches) > 1:
        raise ValueError(f"multiple poses resolve source Track {source_track_id}")
    return matches[0] if matches else None


def _diagnostics_by_frame(events: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, set[str]]]:
    diagnostics: dict[int, dict[str, set[str]]] = defaultdict(
        lambda: {"jump": set(), "swap": set()}
    )
    for event in events:
        track = event.get("track_diagnostics") or {}
        by_joint = track.get("keypoint_jump_candidate_frames_by_joint") or {}
        if isinstance(by_joint, Mapping):
            for joint, frames in by_joint.items():
                for frame in frames or []:
                    diagnostics[int(frame)]["jump"].add(str(joint))
        swap_joints: set[str] = set()
        for pair in track.get("left_right_swap_candidate_joint_pairs") or []:
            if isinstance(pair, list):
                swap_joints.update(str(joint) for joint in pair)
        for frame in track.get("left_right_swap_candidate_frames") or []:
            diagnostics[int(frame)]["swap"].update(swap_joints)
    return diagnostics


def build_video_observer_payload(
    *,
    bundle_dir: Path,
    video_path: Path,
    registry: Mapping[str, Any],
    display_name: str,
    video_href: str,
) -> dict[str, Any]:
    bundle_dir = bundle_dir.resolve()
    video_path = video_path.resolve()
    required_files = {
        "frames": bundle_dir / "frames.jsonl",
        "primary_timeline": bundle_dir / "primary-player.jsonl",
        "events": bundle_dir / "events.jsonl",
        "indicator_features": bundle_dir / "indicator-features.jsonl",
        "scores": bundle_dir / "scores.jsonl",
        "summary": bundle_dir / "scoring-loop-summary.json",
    }
    for name, path in required_files.items():
        if not path.is_file():
            raise ValueError(f"missing {name}: {path}")
    if not video_path.is_file():
        raise ValueError(f"missing video: {video_path}")

    frames = _read_jsonl(required_files["frames"])
    timeline = _read_jsonl(required_files["primary_timeline"])
    events = _read_jsonl(required_files["events"])
    indicator_records = _read_jsonl(required_files["indicator_features"])
    scores = _read_jsonl(required_files["scores"])
    summary = json.loads(required_files["summary"].read_text(encoding="utf-8"))
    if not frames or len(frames) != len(timeline):
        raise ValueError("frames and primary timeline must be non-empty and have equal length")

    video_id = str(summary.get("video_id"))
    if not video_id or video_path.stem != video_id:
        raise ValueError("video filename and scoring summary video_id must match")
    registry_indicators = registry.get("indicators") or []
    registry_by_id = {str(item["indicator_id"]): item for item in registry_indicators}
    if len(registry_by_id) != len(registry_indicators):
        raise ValueError("registry indicator IDs must be unique")

    score_by_key = {
        (str(item.get("event_id")), str(item.get("indicator_id"))): item
        for item in scores
    }
    indicator_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed_indicator_ids: set[str] = set()
    for record in indicator_records:
        indicator_id = str(record.get("indicator_id"))
        event_id = str(record.get("event_id"))
        if indicator_id not in registry_by_id:
            raise ValueError(f"unknown indicator {indicator_id}")
        key = (event_id, indicator_id)
        score = score_by_key.get(key)
        if score is None:
            raise ValueError(f"missing score for {event_id}/{indicator_id}")
        if score.get("status") != record.get("scoring_status"):
            raise ValueError(f"score status drift for {event_id}/{indicator_id}")
        if score.get("grade") is not None or score.get("threshold_version") is not None:
            raise ValueError("uncalibrated observer source must not contain grades or thresholds")
        registry_item = registry_by_id[indicator_id]
        feature_source = record.get("scoring_features") or record.get("features") or []
        features = [_compact_feature(item) for item in feature_source]
        feature_names = [item["name"] for item in features]
        if feature_names != list(registry_item.get("required_features") or []):
            raise ValueError(f"required feature order drift for {event_id}/{indicator_id}")
        gate = record.get("quality_gate") or {}
        indicator_by_event[event_id].append(
            {
                "indicator_id": indicator_id,
                "label": INDICATOR_LABELS.get(indicator_id, indicator_id),
                "feasibility_level": record.get("feasibility_level"),
                "required_joints": _required_joints(registry_item),
                "feature_status": record.get("feature_status"),
                "scoring_feature_status": record.get("scoring_feature_status"),
                "scoring_status": record.get("scoring_status"),
                "grade": record.get("grade"),
                "confidence": _round_optional(score.get("confidence"), 4),
                "quality_status": gate.get("status"),
                "hard_fail_flags": list(gate.get("hard_fail_flags") or []),
                "scoring_block_flags": list(gate.get("scoring_block_flags") or []),
                "advisory_flags": list(gate.get("advisory_flags") or []),
                "reason_codes": list(score.get("reason_codes") or []),
                "feedback": score.get("feedback"),
                "features": features,
            }
        )
        observed_indicator_ids.add(indicator_id)
    if observed_indicator_ids != set(registry_by_id):
        raise ValueError("observer source must cover every current registry indicator")

    diagnostics = _diagnostics_by_frame(events)
    timeline_by_index = {int(item["processed_index"]): item for item in timeline}
    if len(timeline_by_index) != len(timeline):
        raise ValueError("primary timeline processed_index values must be unique")

    joint_names: list[str] | None = None
    compact_frames: list[dict[str, Any]] = []
    for expected_index, frame in enumerate(frames):
        frame_meta = frame.get("frame") or {}
        processed_index = int(frame_meta.get("processed_index"))
        if processed_index != expected_index:
            raise ValueError("frames must be contiguous in processed_index order")
        primary = timeline_by_index.get(processed_index)
        if primary is None:
            raise ValueError(f"missing timeline row {processed_index}")
        if int(primary.get("timestamp_ms")) != int(frame_meta.get("timestamp_ms")):
            raise ValueError(f"timestamp drift at processed frame {processed_index}")
        pose = _select_pose(frame, primary.get("source_track_id"))
        if bool(primary.get("pose_present")) != bool(pose is not None):
            raise ValueError(f"pose presence drift at processed frame {processed_index}")
        points = None
        pose_confidence = None
        if pose is not None:
            names = [str(item.get("name")) for item in pose.get("keypoints", [])]
            if joint_names is None:
                joint_names = names
            elif names != joint_names:
                raise ValueError("keypoint topology/order changed within one video")
            points = [
                [
                    _round_optional(item.get("x_normalized"), 6),
                    _round_optional(item.get("y_normalized"), 6),
                    _round_optional(item.get("confidence"), 4),
                ]
                for item in pose.get("keypoints", [])
            ]
            pose_confidence = _round_optional(pose.get("confidence"), 4)
        frame_diagnostics = diagnostics.get(int(frame_meta.get("index")))
        compact_frames.append(
            {
                "i": processed_index,
                "s": int(frame_meta.get("index")),
                "t": int(frame_meta.get("timestamp_ms")),
                "track": primary.get("source_track_id"),
                "pose_confidence": pose_confidence,
                "keypoint_valid_fraction": _round_optional(
                    primary.get("keypoint_valid_fraction"), 4
                ),
                "selection_margin": _round_optional(
                    primary.get("selection_score_margin"), 5
                ),
                "points": points,
                "jump": sorted(frame_diagnostics["jump"]) if frame_diagnostics else [],
                "swap": sorted(frame_diagnostics["swap"]) if frame_diagnostics else [],
            }
        )
    if joint_names is None:
        raise ValueError("no selected pose exists in the scoring window")

    compact_events: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event.get("event_id"))
        rows = sorted(
            indicator_by_event.get(event_id, []), key=lambda item: item["indicator_id"]
        )
        expected_count = sum(
            1
            for indicator_id in registry_by_id
            if indicator_id.startswith(str(event.get("event_code")) + "-")
        )
        if len(rows) != expected_count:
            raise ValueError(f"indicator coverage drift for event {event_id}")
        track = event.get("track_diagnostics") or {}
        compact_events.append(
            {
                "event_id": event_id,
                "event_code": event.get("event_code"),
                "start_ms": int(event.get("start_ms")),
                "end_ms": int(event.get("end_ms")),
                "key_phases_ms": event.get("key_phases_ms") or {},
                "confidence": _round_optional(event.get("confidence"), 4),
                "boundary_uncertainty_ms": int(event.get("boundary_uncertainty_ms") or 0),
                "quality_flags": list(event.get("quality_flags") or []),
                "track_diagnostics": {
                    "track_coverage_fraction": _round_optional(
                        track.get("track_coverage_fraction"), 4
                    ),
                    "pose_coverage_fraction": _round_optional(
                        track.get("pose_coverage_fraction"), 4
                    ),
                    "source_track_switch_candidate_count": int(
                        track.get("source_track_switch_candidate_count") or 0
                    ),
                    "confirmed_id_switch_status": track.get("confirmed_id_switch_status"),
                    "keypoint_jump_candidate_frames": list(
                        track.get("keypoint_jump_candidate_frames") or []
                    ),
                    "left_right_swap_candidate_frames": list(
                        track.get("left_right_swap_candidate_frames") or []
                    ),
                    "longest_pose_missing_ms": int(track.get("longest_pose_missing_ms") or 0),
                },
                "indicators": rows,
            }
        )
    compact_events.sort(key=lambda item: (item["start_ms"], item["event_code"], item["event_id"]))

    feature_status_counts = Counter(
        str(record.get("feature_status")) for record in indicator_records
    )
    scoring_status_counts = Counter(str(record.get("scoring_status")) for record in indicator_records)
    scoring_block_counts: Counter[str] = Counter()
    for record in indicator_records:
        scoring_block_counts.update(
            str(item)
            for item in (record.get("quality_gate") or {}).get("scoring_block_flags", [])
        )

    payload = {
        "schema_version": OBSERVER_SCHEMA_VERSION,
        "observer_version": OBSERVER_VERSION,
        "video_id": video_id,
        "display_name": display_name,
        "video_href": video_href,
        "video": {
            "width": int((frames[0].get("frame") or {}).get("width")),
            "height": int((frames[0].get("frame") or {}).get("height")),
            "duration_ms": int(compact_frames[-1]["t"]),
            "frame_count": len(compact_frames),
        },
        "model": {
            "pose_profile": summary["model_versions"].get("pose_profile"),
            "pose_model_sha256": summary["model_versions"].get("pose_model_sha256"),
            "native_keypoint_format": summary["model_versions"].get("native_keypoint_format"),
            "native_keypoint_count": summary["model_versions"].get("native_keypoint_count"),
            "primary_player": summary["model_versions"].get("primary_player"),
            "event": summary["model_versions"].get("event"),
            "quality_policy": summary["model_versions"].get("quality_policy"),
            "feasibility_registry": summary["model_versions"].get("feasibility_registry"),
        },
        "joint_names": joint_names,
        "frames": compact_frames,
        "events": compact_events,
        "summary": {
            "event_count": len(compact_events),
            "indicator_instance_count": len(indicator_records),
            "feature_status_counts": dict(sorted(feature_status_counts.items())),
            "scoring_status_counts": dict(sorted(scoring_status_counts.items())),
            "scoring_block_flag_counts": dict(sorted(scoring_block_counts.items())),
            "grade_count": sum(1 for record in indicator_records if record.get("grade") is not None),
            "threshold_count": sum(
                1 for score in scores if score.get("threshold_version") is not None
            ),
        },
        "safety": {
            "pose_overlay_is_model_output_not_truth": True,
            "event_intervals_are_candidates_not_ground_truth": True,
            "feature_measurement_is_not_accuracy": True,
            "grades_or_thresholds_generated": False,
            "aggregate_score_defined": False,
        },
        "source_sha256": {
            "video": _sha256(video_path),
            **{name: _sha256(path) for name, path in required_files.items()},
        },
    }
    payload["content_sha256"] = _canonical_sha256(payload)
    validate_video_observer_payload(payload, registry)
    return payload


def validate_video_observer_payload(
    payload: Mapping[str, Any], registry: Mapping[str, Any]
) -> None:
    if payload.get("schema_version") != OBSERVER_SCHEMA_VERSION:
        raise ValueError("unsupported observer payload schema")
    if payload.get("observer_version") != OBSERVER_VERSION:
        raise ValueError("unsupported observer version")
    safety = payload.get("safety") or {}
    if safety != {
        "pose_overlay_is_model_output_not_truth": True,
        "event_intervals_are_candidates_not_ground_truth": True,
        "feature_measurement_is_not_accuracy": True,
        "grades_or_thresholds_generated": False,
        "aggregate_score_defined": False,
    }:
        raise ValueError("observer safety contract drift")
    expected_hash = payload.get("content_sha256")
    unhashed = dict(payload)
    unhashed.pop("content_sha256", None)
    if expected_hash != _canonical_sha256(unhashed):
        raise ValueError("observer payload content hash mismatch")
    source_sha256 = payload.get("source_sha256")
    expected_source_names = {
        "video",
        "frames",
        "primary_timeline",
        "events",
        "indicator_features",
        "scores",
        "summary",
    }
    if (
        not isinstance(source_sha256, Mapping)
        or set(source_sha256) != expected_source_names
        or any(not _is_sha256(value) for value in source_sha256.values())
    ):
        raise ValueError("observer payload source SHA-256 contract mismatch")
    frames = payload.get("frames") or []
    if len(frames) != int((payload.get("video") or {}).get("frame_count", -1)):
        raise ValueError("observer frame count mismatch")
    if [int(item["i"]) for item in frames] != list(range(len(frames))):
        raise ValueError("observer frames must be contiguous")
    registry_ids = {str(item["indicator_id"]) for item in registry.get("indicators", [])}
    observed_ids = {
        str(indicator["indicator_id"])
        for event in payload.get("events", [])
        for indicator in event.get("indicators", [])
    }
    if observed_ids != registry_ids:
        raise ValueError("observer indicator membership mismatch")
    for event in payload.get("events", []):
        if int(event["end_ms"]) < int(event["start_ms"]):
            raise ValueError("observer event interval is invalid")
        for indicator in event.get("indicators", []):
            if indicator.get("grade") is not None:
                raise ValueError("observer cannot expose uncalibrated grade")
    summary = payload.get("summary") or {}
    if int(summary.get("grade_count", -1)) != 0 or int(summary.get("threshold_count", -1)) != 0:
        raise ValueError("observer must remain grade/threshold free")


def build_observer_manifest(
    video_payloads: Iterable[Mapping[str, Any]],
    *,
    data_file_sha256_by_video: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    videos = []
    for payload in video_payloads:
        video_id = str(payload["video_id"])
        video_record = {
                "video_id": payload["video_id"],
                "display_name": payload["display_name"],
                "data_file": f"data/{payload['video_id']}.json",
                "content_sha256": payload["content_sha256"],
                "source_summary_sha256": payload["source_sha256"]["summary"],
                "frame_count": payload["video"]["frame_count"],
                "duration_ms": payload["video"]["duration_ms"],
                "event_count": payload["summary"]["event_count"],
                "indicator_instance_count": payload["summary"]["indicator_instance_count"],
                "feature_status_counts": payload["summary"]["feature_status_counts"],
                "scoring_status_counts": payload["summary"]["scoring_status_counts"],
            }
        if data_file_sha256_by_video is not None:
            file_sha = data_file_sha256_by_video.get(video_id)
            if not isinstance(file_sha, str) or len(file_sha) != 64:
                raise ValueError(f"missing data file SHA-256 for {video_id}")
            video_record["data_file_sha256"] = file_sha.upper()
        videos.append(video_record)
    if not videos:
        raise ValueError("observer manifest requires at least one video")
    manifest = {
        "schema_version": OBSERVER_SCHEMA_VERSION,
        "observer_version": OBSERVER_VERSION,
        "title": "RallyMate 动态评分观察器",
        "videos": videos,
        "safety": {
            "accuracy_claim": False,
            "formal_scoring_claim": False,
            "model_pose_is_ground_truth": False,
        },
    }
    manifest["content_sha256"] = _canonical_sha256(manifest)
    return manifest


def validate_observer_manifest(
    manifest: Mapping[str, Any], output_dir: Path, registry: Mapping[str, Any]
) -> None:
    if manifest.get("schema_version") != OBSERVER_SCHEMA_VERSION:
        raise ValueError("unsupported observer manifest schema")
    if manifest.get("observer_version") != OBSERVER_VERSION:
        raise ValueError("unsupported observer manifest version")
    if manifest.get("safety") != {
        "accuracy_claim": False,
        "formal_scoring_claim": False,
        "model_pose_is_ground_truth": False,
    }:
        raise ValueError("observer manifest safety contract drift")
    unhashed = dict(manifest)
    expected_hash = unhashed.pop("content_sha256", None)
    if expected_hash != _canonical_sha256(unhashed):
        raise ValueError("observer manifest content hash mismatch")
    ids: set[str] = set()
    for video in manifest.get("videos", []):
        video_id = str(video.get("video_id"))
        if video_id in ids:
            raise ValueError("observer manifest video IDs must be unique")
        ids.add(video_id)
        data_path = (output_dir / str(video.get("data_file"))).resolve()
        if not data_path.is_file():
            raise ValueError(f"observer data file missing: {data_path}")
        payload = json.loads(data_path.read_text(encoding="utf-8"))
        validate_video_observer_payload(payload, registry)
        if payload.get("content_sha256") != video.get("content_sha256"):
            raise ValueError("observer manifest payload binding mismatch")
        if not _is_sha256(video.get("source_summary_sha256")):
            raise ValueError("observer manifest source summary SHA-256 is invalid")
        if (
            payload.get("source_sha256", {}).get("summary")
            != video.get("source_summary_sha256")
        ):
            raise ValueError("observer manifest source summary binding mismatch")
        declared_file_sha = video.get("data_file_sha256")
        if declared_file_sha is not None and _sha256(data_path) != declared_file_sha:
            raise ValueError("observer manifest data file SHA-256 mismatch")
