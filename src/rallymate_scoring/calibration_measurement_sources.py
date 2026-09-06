from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from rallymate_tracking import (
    PRIMARY_PLAYER_ALGORITHM_VERSION,
    build_primary_player_artifacts,
    primary_timeline_algorithm_version,
    registry_required_primary_player_version,
)


SCHEMA_VERSION = "1.0.0"
MANIFEST_VERSION = "calibration-measurement-sources-v1.0.0"
LATEST_VERSION = "calibration-measurement-sources-latest-v1.0.0"


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _artifact(path: Path) -> dict[str, str]:
    path = path.resolve()
    return {"path": str(path), "sha256": _sha256_file(path)}


def _jsonl_metadata(path: Path, *, video_id: str) -> dict[str, Any]:
    frame_count = 0
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    formats: set[str] = set()
    keypoint_counts: set[int] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"frames record must be an object: {path}:{line_number}")
            frame = record.get("frame")
            if not isinstance(frame, dict):
                raise ValueError(f"frames record lacks frame object: {path}:{line_number}")
            processed_index = frame.get("processed_index")
            timestamp = frame.get("timestamp_ms")
            if processed_index != frame_count or not isinstance(timestamp, int):
                raise ValueError(f"frames indices/timestamps are not canonical: {path}:{line_number}")
            if last_timestamp is not None and timestamp < last_timestamp:
                raise ValueError(f"frames timestamps are not monotonic: {path}:{line_number}")
            job_id = record.get("job_id")
            if not isinstance(job_id, str) or video_id not in job_id:
                raise ValueError(f"frames job_id does not bind video_id: {path}:{line_number}")
            if first_timestamp is None:
                first_timestamp = timestamp
            last_timestamp = timestamp
            poses = record.get("poses")
            if isinstance(poses, list):
                for pose in poses:
                    if not isinstance(pose, dict):
                        continue
                    keypoint_format = pose.get("keypoint_format")
                    keypoints = pose.get("keypoints")
                    if isinstance(keypoint_format, str):
                        formats.add(keypoint_format)
                    if isinstance(keypoints, list):
                        keypoint_counts.add(len(keypoints))
            frame_count += 1
    if frame_count == 0:
        raise ValueError(f"frames JSONL is empty: {path}")
    if len(formats) != 1 or len(keypoint_counts) != 1:
        raise ValueError(f"frames topology is missing or mixed: {path}")
    return {
        "frame_count": frame_count,
        "first_timestamp_ms": first_timestamp,
        "last_timestamp_ms": last_timestamp,
        "native_keypoint_format": next(iter(formats)),
        "native_keypoint_count": next(iter(keypoint_counts)),
    }


def _validate_timeline_against_frames(frames_path: Path, timeline_path: Path) -> int:
    timeline_records: list[dict[str, Any]] = []
    with timeline_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("primary timeline record must be an object")
                timeline_records.append(value)
    primary_timeline_algorithm_version(
        timeline_records,
        expected_version=PRIMARY_PLAYER_ALGORITHM_VERSION,
        require_declared=True,
    )
    index = 0
    with frames_path.open("r", encoding="utf-8") as frames:
        for line in frames:
            if not line.strip():
                continue
            if index >= len(timeline_records):
                raise ValueError("primary timeline is shorter than frames")
            record = json.loads(line)
            frame = record["frame"]
            timeline = timeline_records[index]
            if (
                timeline.get("processed_index") != frame.get("processed_index")
                or timeline.get("source_frame_index") != frame.get("index")
                or timeline.get("timestamp_ms") != frame.get("timestamp_ms")
            ):
                raise ValueError("primary timeline does not align with frames")
            index += 1
    if index != len(timeline_records):
        raise ValueError("primary timeline is longer than frames")
    return index


def _pose_contract(summary: Mapping[str, Any]) -> dict[str, Any]:
    models = summary.get("models")
    if not isinstance(models, Mapping):
        raise ValueError("pose summary lacks models")
    backend = models.get("pose_backend")
    if not isinstance(backend, Mapping):
        raise ValueError("pose summary lacks pose_backend")
    required = (
        "backend",
        "runtime",
        "model_name",
        "model_sha256",
        "native_keypoint_format",
        "native_keypoint_count",
    )
    if any(backend.get(name) in (None, "") for name in required):
        raise ValueError("pose summary backend contract is incomplete")
    return {
        "backend": backend["backend"],
        "runtime": backend["runtime"],
        "profile": backend.get("profile", "unknown"),
        "model_name": backend["model_name"],
        "model_sha256": str(backend["model_sha256"]).upper(),
        "native_keypoint_format": backend["native_keypoint_format"],
        "native_keypoint_count": int(backend["native_keypoint_count"]),
        "input_size": backend.get("input_size"),
        "config_sha256": (
            str(backend["config_sha256"]).upper()
            if backend.get("config_sha256")
            else None
        ),
    }


def build_calibration_measurement_sources(
    *,
    truth_manifest_path: str | Path,
    feasibility_registry_path: str | Path,
    source_specs: Iterable[Mapping[str, Any]],
    output_root: str | Path,
    source_set_id: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    truth_manifest_path = Path(truth_manifest_path).resolve()
    registry_path = Path(feasibility_registry_path).resolve()
    output_root = Path(output_root).resolve()
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    if not source_set_id or any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        for character in source_set_id
    ):
        raise ValueError("source_set_id is invalid")
    run_dir = output_root / source_set_id
    if run_dir.exists():
        raise FileExistsError(f"measurement source set already exists: {run_dir}")
    truth_manifest = _read_json(truth_manifest_path)
    videos = truth_manifest.get("videos")
    if not isinstance(videos, list) or not videos:
        raise ValueError("truth manifest contains no videos")
    video_by_id = {str(item["video_id"]): item for item in videos}
    if len(video_by_id) != len(videos):
        raise ValueError("truth manifest video_id values must be unique")
    specs = [dict(item) for item in source_specs]
    spec_by_id = {str(item.get("video_id")): item for item in specs}
    if len(spec_by_id) != len(specs) or set(spec_by_id) != set(video_by_id):
        raise ValueError("measurement source specs must exactly cover truth manifest videos")
    registry = _read_json(registry_path)
    required_primary = registry_required_primary_player_version(registry["indicators"])
    if required_primary != PRIMARY_PLAYER_ALGORITHM_VERSION:
        raise ValueError("registry primary-player version differs from current implementation")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir()
    try:
        source_rows: list[dict[str, Any]] = []
        shared_contract: dict[str, Any] | None = None
        for video_id in sorted(video_by_id):
            spec = spec_by_id[video_id]
            frames_path = Path(str(spec.get("frames_path", ""))).resolve()
            pose_summary_path = Path(str(spec.get("pose_summary_path", ""))).resolve()
            if not frames_path.is_file() or not pose_summary_path.is_file():
                raise ValueError(f"measurement source files are missing for {video_id}")
            summary = _read_json(pose_summary_path)
            if summary.get("status") != "completed":
                raise ValueError(f"pose source is not completed: {video_id}")
            input_video = Path(str(summary.get("input", {}).get("video_path", ""))).resolve()
            truth_video = Path(str(video_by_id[video_id]["path"])).resolve()
            if input_video != truth_video:
                raise ValueError(f"pose summary video path differs from truth manifest: {video_id}")
            metadata = _jsonl_metadata(frames_path, video_id=video_id)
            if summary.get("processing", {}).get("processed_frames") != metadata[
                "frame_count"
            ]:
                raise ValueError(f"pose summary frame count mismatch: {video_id}")
            pose_contract = _pose_contract(summary)
            if (
                pose_contract["native_keypoint_format"]
                != metadata["native_keypoint_format"]
                or pose_contract["native_keypoint_count"]
                != metadata["native_keypoint_count"]
            ):
                raise ValueError(f"pose summary topology mismatch: {video_id}")
            if shared_contract is None:
                shared_contract = pose_contract
            elif pose_contract != shared_contract:
                raise ValueError("all calibration measurement videos must use one pose contract")
            video_dir = run_dir / video_id
            video_dir.mkdir()
            timeline_path = video_dir / "primary-player.jsonl"
            primary_summary_path = video_dir / "primary-player-summary.json"
            primary_summary = build_primary_player_artifacts(
                frames_path, timeline_path, primary_summary_path
            )
            timeline_count = _validate_timeline_against_frames(frames_path, timeline_path)
            if timeline_count != metadata["frame_count"]:
                raise ValueError(f"primary timeline length mismatch: {video_id}")
            source_rows.append(
                {
                    "video_id": video_id,
                    "source_video": {
                        "path": str(truth_video),
                        "sha256": str(video_by_id[video_id]["sha256"]).upper(),
                    },
                    "frames": _artifact(frames_path),
                    "pose_summary": _artifact(pose_summary_path),
                    "primary_timeline": _artifact(timeline_path),
                    "primary_summary": _artifact(primary_summary_path),
                    "frame_count": metadata["frame_count"],
                    "first_timestamp_ms": metadata["first_timestamp_ms"],
                    "last_timestamp_ms": metadata["last_timestamp_ms"],
                    "pose_contract": pose_contract,
                    "primary_player_version": primary_summary["algorithm_version"],
                }
            )
        assert shared_contract is not None
        fingerprint_payload = {
            "truth_manifest_sha256": _sha256_file(truth_manifest_path),
            "registry_sha256": _sha256_file(registry_path),
            "sources": [
                {
                    "video_id": item["video_id"],
                    "frames_sha256": item["frames"]["sha256"],
                    "pose_summary_sha256": item["pose_summary"]["sha256"],
                    "primary_timeline_sha256": item["primary_timeline"]["sha256"],
                }
                for item in source_rows
            ],
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "manifest_version": MANIFEST_VERSION,
            "source_set_id": source_set_id,
            "generated_at": generated_at,
            "status": "ready_for_manual_event_feature_measurement",
            "source_fingerprint_sha256": _canonical_sha256(fingerprint_payload),
            "truth_manifest": _artifact(truth_manifest_path),
            "feasibility_registry": {
                **_artifact(registry_path),
                "version": registry["registry_version"],
            },
            "shared_pose_contract": shared_contract,
            "required_primary_player_version": required_primary,
            "video_count": len(source_rows),
            "total_frame_count": sum(item["frame_count"] for item in source_rows),
            "sources": source_rows,
            "safety": {
                "gpu_inference_executed": False,
                "existing_pose_frames_reused": True,
                "candidate_events_used": False,
                "manual_truth_generated": False,
                "grades_generated": False,
                "thresholds_generated": False,
                "accuracy_claim": False,
                "automatic_F3_or_F4_promotion": False,
            },
        }
        manifest_path = run_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        validate_calibration_measurement_sources(manifest, verify_sources=True)
        latest = {
            "schema_version": SCHEMA_VERSION,
            "latest_version": LATEST_VERSION,
            "updated_at": generated_at,
            "source_set_id": source_set_id,
            "manifest": _artifact(manifest_path),
        }
        temporary = output_root / f".latest-{source_set_id}.tmp"
        _write_json(temporary, latest)
        os.replace(temporary, output_root / "latest.json")
        return manifest
    except Exception:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        raise


def _verify_artifact(binding: Mapping[str, Any], name: str) -> Path:
    path = Path(str(binding.get("path", ""))).resolve()
    if not path.is_file() or _sha256_file(path) != str(binding.get("sha256", "")).upper():
        raise ValueError(f"measurement source artifact mismatch: {name}")
    return path


def validate_calibration_measurement_sources(
    manifest: Mapping[str, Any], *, verify_sources: bool = False
) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get(
        "manifest_version"
    ) != MANIFEST_VERSION:
        raise ValueError("unsupported calibration measurement source manifest")
    if manifest.get("status") != "ready_for_manual_event_feature_measurement":
        raise ValueError("measurement sources are not ready")
    safety = manifest.get("safety")
    if not isinstance(safety, Mapping):
        raise ValueError("measurement source safety block is missing")
    if safety.get("existing_pose_frames_reused") is not True:
        raise ValueError("measurement source reuse declaration is missing")
    for field in (
        "gpu_inference_executed",
        "candidate_events_used",
        "manual_truth_generated",
        "grades_generated",
        "thresholds_generated",
        "accuracy_claim",
        "automatic_F3_or_F4_promotion",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"unsafe measurement source claim: {field}")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("measurement sources are empty")
    ids = [item.get("video_id") for item in sources if isinstance(item, Mapping)]
    if len(ids) != len(sources) or len(set(ids)) != len(ids) or ids != sorted(ids):
        raise ValueError("measurement source video IDs must be unique and sorted")
    if manifest.get("video_count") != len(sources) or manifest.get(
        "total_frame_count"
    ) != sum(int(item.get("frame_count", -1)) for item in sources):
        raise ValueError("measurement source counts are inconsistent")
    if manifest.get("required_primary_player_version") != PRIMARY_PLAYER_ALGORITHM_VERSION:
        raise ValueError("measurement source primary-player version is stale")
    if verify_sources:
        truth_path = _verify_artifact(manifest["truth_manifest"], "truth_manifest")
        registry_path = _verify_artifact(
            manifest["feasibility_registry"], "feasibility_registry"
        )
        truth = _read_json(truth_path)
        registry = _read_json(registry_path)
        truth_by_id = {str(item["video_id"]): item for item in truth["videos"]}
        truth_ids = sorted(truth_by_id)
        if truth_ids != ids:
            raise ValueError("measurement source set differs from truth manifest")
        if registry["registry_version"] != manifest["feasibility_registry"].get(
            "version"
        ):
            raise ValueError("measurement source registry version mismatch")
        required = registry_required_primary_player_version(registry["indicators"])
        if required != manifest["required_primary_player_version"]:
            raise ValueError("measurement source registry primary version mismatch")
        contracts: list[dict[str, Any]] = []
        fingerprint_sources: list[dict[str, Any]] = []
        for item in sources:
            video_id = item["video_id"]
            source_video = _verify_artifact(
                item["source_video"], f"{video_id}.source_video"
            )
            truth_video = truth_by_id[video_id]
            if (
                source_video != Path(str(truth_video["path"])).resolve()
                or item["source_video"]["sha256"]
                != str(truth_video["sha256"]).upper()
            ):
                raise ValueError(
                    f"measurement source video differs from truth manifest: {video_id}"
                )
            frames = _verify_artifact(item["frames"], f"{video_id}.frames")
            pose_summary_path = _verify_artifact(
                item["pose_summary"], f"{video_id}.pose_summary"
            )
            timeline = _verify_artifact(
                item["primary_timeline"], f"{video_id}.primary_timeline"
            )
            primary_summary_path = _verify_artifact(
                item["primary_summary"], f"{video_id}.primary_summary"
            )
            metadata = _jsonl_metadata(frames, video_id=video_id)
            if metadata["frame_count"] != item["frame_count"]:
                raise ValueError(f"measurement source frame count drift: {video_id}")
            if _validate_timeline_against_frames(frames, timeline) != item["frame_count"]:
                raise ValueError(f"measurement source timeline drift: {video_id}")
            pose_summary = _read_json(pose_summary_path)
            if (
                pose_summary.get("status") != "completed"
                or Path(str(pose_summary.get("input", {}).get("video_path", ""))).resolve()
                != source_video
                or pose_summary.get("processing", {}).get("processed_frames")
                != item["frame_count"]
            ):
                raise ValueError(f"measurement source pose summary drift: {video_id}")
            pose_contract = _pose_contract(pose_summary)
            if pose_contract != item["pose_contract"]:
                raise ValueError(f"measurement source pose contract drift: {video_id}")
            primary_summary = _read_json(primary_summary_path)
            if (
                primary_summary.get("algorithm_version")
                != PRIMARY_PLAYER_ALGORITHM_VERSION
                or Path(str(primary_summary.get("timeline", ""))).resolve() != timeline
                or primary_summary.get("diagnostics", {}).get("interval", {}).get(
                    "frame_count"
                )
                != item["frame_count"]
            ):
                raise ValueError(f"measurement source primary summary drift: {video_id}")
            contracts.append(pose_contract)
            fingerprint_sources.append(
                {
                    "video_id": video_id,
                    "frames_sha256": item["frames"]["sha256"],
                    "pose_summary_sha256": item["pose_summary"]["sha256"],
                    "primary_timeline_sha256": item["primary_timeline"]["sha256"],
                }
            )
        if any(contract != manifest["shared_pose_contract"] for contract in contracts):
            raise ValueError("measurement source shared pose contract mismatch")
        expected_fingerprint = _canonical_sha256(
            {
                "truth_manifest_sha256": manifest["truth_manifest"]["sha256"],
                "registry_sha256": manifest["feasibility_registry"]["sha256"],
                "sources": fingerprint_sources,
            }
        )
        if manifest.get("source_fingerprint_sha256") != expected_fingerprint:
            raise ValueError("measurement source fingerprint mismatch")


def load_latest_calibration_measurement_sources(
    latest_path: str | Path,
) -> dict[str, Any]:
    latest = _read_json(Path(latest_path).resolve())
    if latest.get("schema_version") != SCHEMA_VERSION or latest.get(
        "latest_version"
    ) != LATEST_VERSION:
        raise ValueError("unsupported measurement source latest pointer")
    manifest_path = _verify_artifact(latest["manifest"], "latest.manifest")
    manifest = _read_json(manifest_path)
    if manifest.get("source_set_id") != latest.get("source_set_id"):
        raise ValueError("measurement source latest ID mismatch")
    validate_calibration_measurement_sources(manifest, verify_sources=True)
    return manifest


__all__ = [
    "LATEST_VERSION",
    "MANIFEST_VERSION",
    "build_calibration_measurement_sources",
    "load_latest_calibration_measurement_sources",
    "validate_calibration_measurement_sources",
]
