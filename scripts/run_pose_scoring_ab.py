from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_features import EventInterval, compute_event_features, pose_sequence_from_records
from rallymate_scoring.feasibility import validate_feasibility_registry
from rallymate_scoring.loop import run_minimum_scoring_loop
from rallymate_tracking import build_primary_player_artifacts


VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)
BACKENDS = (
    "yolo",
    "rtmpose-s-halpe26-256x192",
    "rtmpose-m-halpe26-256x192",
    "rtmpose-m-halpe26-384x288",
)
FOOT_FEATURES = (
    "left_ankle_shank_foot_angle_deg",
    "right_ankle_shank_foot_angle_deg",
)
HISTORICAL_REGISTRY_VERSION = "minimum-scoring-loop-2026-08-13.1"
HISTORICAL_REGISTRY_SHA256 = (
    "5f7952c6d9514856ad979e27a556cfa2d80b188eaeffd8333a25f32aacf5dfcc"
)
HISTORICAL_INDICATOR_IDS = (
    "FS01-M02",
    "FS01-M05",
    "FS02-M02",
    "FS09-M03",
    "FS09-M04",
    "FS09-M05",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the frozen six-indicator scoring loop across YOLO and "
            "RTMPose backends"
        )
    )
    parser.add_argument(
        "--historical-replay",
        action="store_true",
        help="explicitly authorize the frozen six-indicator historical workflow",
    )
    parser.add_argument("--video-id", action="append", dest="video_ids")
    args = parser.parse_args(argv)
    if not args.historical_replay:
        parser.error(
            "--historical-replay is required; this script uses the frozen "
            "2026-08-13 six-indicator registry, not the current registry"
        )
    return args


def _load_historical_registry(root: Path) -> dict[str, Any]:
    path = root / "metric-feasibility.json"
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != HISTORICAL_REGISTRY_SHA256:
        raise RuntimeError(
            "historical replay registry SHA-256 mismatch; refusing to substitute "
            "a current or modified registry"
        )
    registry = json.loads(raw.decode("utf-8"))
    validate_feasibility_registry(registry)
    indicator_ids = tuple(item["indicator_id"] for item in registry["indicators"])
    if (
        registry.get("registry_version") != HISTORICAL_REGISTRY_VERSION
        or indicator_ids != HISTORICAL_INDICATOR_IDS
    ):
        raise RuntimeError(
            "historical replay requires the frozen 2026-08-13 six-indicator registry"
        )
    return registry


def _historical_replay_metadata() -> dict[str, Any]:
    return {
        "execution_mode": "historical_replay",
        "registry_version": HISTORICAL_REGISTRY_VERSION,
        "registry_sha256": HISTORICAL_REGISTRY_SHA256,
        "indicator_ids": list(HISTORICAL_INDICATOR_IDS),
        "semantics": "frozen_six_indicator_baseline_not_current_production_registry",
    }


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _frames_path(root: Path, backend: str, video_id: str) -> Path:
    if backend == "yolo":
        return root / "runs" / "full-test" / video_id / "frames.jsonl"
    return root / "runs" / "pose-ab" / backend / video_id / "frames.jsonl"


def _pose_metadata(root: Path, backend: str, video_id: str) -> dict[str, Any]:
    if backend == "yolo":
        baseline = json.loads((root / "reports" / "pose-model-baseline.json").read_text(encoding="utf-8"))
        return {
            "backend": "yolo",
            "runtime": "pytorch",
            "profile": "realtime",
            "model_sha256": baseline["models"]["pose"]["sha256"],
            "native_keypoint_format": "coco17",
        }
    summary = json.loads((root / "runs" / "pose-ab" / backend / video_id / "summary.json").read_text(encoding="utf-8"))
    return summary["models"]["pose_backend"]


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"valid_count": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "valid_count": len(values),
        "mean": round(statistics.mean(values), 6),
        "median": round(statistics.median(values), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    root = Path.cwd()
    historical_registry = _load_historical_registry(root)
    output_root = root / "reports" / "pose-scoring-ab"
    output_root.mkdir(parents=True, exist_ok=True)
    videos = args.video_ids or list(VIDEO_IDS)
    model_summaries = []
    for backend in BACKENDS:
        backend_results = []
        aggregate_status: Counter[str] = Counter()
        aggregate_indicator_valid: Counter[str] = Counter()
        aggregate_indicator_total: Counter[str] = Counter()
        foot_values: dict[str, list[float]] = defaultdict(list)
        foot_eligible: Counter[str] = Counter()
        for video_id in videos:
            frames_path = _frames_path(root, backend, video_id)
            output_dir = output_root / backend / video_id
            output_dir.mkdir(parents=True, exist_ok=True)
            timeline_path = output_dir / "primary-player.jsonl"
            primary_summary_path = output_dir / "primary-player-summary.json"
            build_primary_player_artifacts(frames_path, timeline_path, primary_summary_path)
            metadata = _pose_metadata(root, backend, video_id)
            result = run_minimum_scoring_loop(
                frames_path=frames_path,
                primary_timeline_path=timeline_path,
                output_dir=output_dir,
                feasibility_registry_path=root / "metric-feasibility.json",
                feasibility_registry=historical_registry,
                source_id=f"{video_id}:{backend}",
                video_id=video_id,
                pose_model=metadata,
                source_provenance={
                    "video_id": video_id,
                    "backend": backend,
                    "frames_path": str(frames_path),
                    "historical_replay": _historical_replay_metadata(),
                },
                calibrations=None,
            )
            records = _load(frames_path)
            timeline = _load(timeline_path)
            sequence = pose_sequence_from_records(records, timeline)
            diagnostics = []
            for event in result["events"]:
                interval = EventInterval(
                    event["event_id"], event["event_code"], event["start_ms"], event["end_ms"],
                    event["person_track_id"], event.get("key_phases_ms"),
                )
                for feature in compute_event_features(sequence, interval, list(FOOT_FEATURES)):
                    payload = feature.to_dict()
                    payload.update({"video_id": video_id, "backend": backend, "event_id": event["event_id"], "event_code": event["event_code"]})
                    diagnostics.append(payload)
                    foot_eligible[feature.feature_name] += 1
                    if feature.valid and isinstance(feature.value, (int, float)):
                        foot_values[feature.feature_name].append(float(feature.value))
            (output_dir / "foot-angle-diagnostics.jsonl").write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in diagnostics), encoding="utf-8"
            )
            statuses = Counter(item["status"] for item in result["scores"])
            aggregate_status.update(statuses)
            for record in result["indicator_records"]:
                aggregate_indicator_total[record["indicator_id"]] += 1
                if record["feature_status"] == "measured":
                    aggregate_indicator_valid[record["indicator_id"]] += 1
            backend_results.append({
                "video_id": video_id,
                "event_counts": result["summary"]["event_counts"],
                "score_status_counts": dict(statuses),
                "indicator_feature_validity": result["summary"]["indicator_feature_validity"],
                "foot_angle_validity": {
                    name: {
                        "valid": sum(1 for item in diagnostics if item["feature_name"] == name and item["valid"]),
                        "total": sum(1 for item in diagnostics if item["feature_name"] == name),
                    }
                    for name in FOOT_FEATURES
                },
                "output_dir": str(output_dir),
            })
        model_summaries.append({
            "backend": backend,
            "native_keypoint_format": "coco17" if backend == "yolo" else "halpe26",
            "score_status_counts": dict(aggregate_status),
            "indicator_feature_validity": {
                indicator: {
                    "valid": aggregate_indicator_valid[indicator],
                    "total": total,
                    "valid_rate": round(aggregate_indicator_valid[indicator] / max(total, 1), 6),
                }
                for indicator, total in sorted(aggregate_indicator_total.items())
            },
            "foot_angle_diagnostics": {
                name: {
                    **_stats(foot_values[name]),
                    "eligible_count": foot_eligible[name],
                    "valid_rate": round(len(foot_values[name]) / max(foot_eligible[name], 1), 6),
                    "semantics": "2D knee-ankle-forefoot angle diagnostic_not_A_to_E",
                }
                for name in FOOT_FEATURES
            },
            "videos": backend_results,
        })
    report = {
        "schema_version": "1.0.0",
        "report_version": "pose-scoring-ab-2026-08-13.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "historical_replay": _historical_replay_metadata(),
        "status": "six_indicator_pipeline_executed_no_coach_calibration",
        "protocol": {
            "videos": videos,
            "backends": list(BACKENDS),
            "same_frozen_detection_source": True,
            "events": ["FS01", "FS02", "FS09"],
            "indicators": list(HISTORICAL_INDICATOR_IDS),
            "calibration": None,
        },
        "models": model_summaries,
        "safety": {"non_null_grades": 0, "fake_thresholds_generated": False},
    }
    output = output_root / "pose-scoring-ab.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "execution_mode": "historical_replay", "models": len(model_summaries), "videos": len(videos)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
