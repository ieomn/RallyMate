from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rallymate_features import EventInterval, clear_feature_cache, compute_event_features, pose_sequence_from_records
from rallymate_scoring.feasibility import validate_feasibility_registry
from rallymate_scoring.loop import SUPPLEMENTAL_FEATURES_BY_INDICATOR


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
            "Evaluate common-event features for the frozen 2026-08-13 "
            "six-indicator replay"
        )
    )
    parser.add_argument(
        "--historical-replay",
        action="store_true",
        help="explicitly authorize the frozen six-indicator historical workflow",
    )
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


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _frames(root: Path, backend: str, video_id: str) -> Path:
    if backend == "yolo":
        return root / "runs" / "full-test" / video_id / "frames.jsonl"
    return root / "runs" / "pose-ab" / backend / video_id / "frames.jsonl"


def main(argv: list[str] | None = None) -> None:
    _parse_args(argv)
    root = Path.cwd()
    registry = _load_historical_registry(root)
    output_root = root / "reports" / "pose-scoring-ab"
    report_path = output_root / "pose-scoring-ab.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    indicators_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for indicator in registry["indicators"]:
        indicators_by_event[indicator["indicator_id"].split("-")[0]].append(indicator)
    model_results = []
    for backend in BACKENDS:
        valid_counts: Counter[str] = Counter()
        total_counts: Counter[str] = Counter()
        supplemental_valid: Counter[str] = Counter()
        supplemental_total: Counter[str] = Counter()
        videos = []
        for video_id in VIDEO_IDS:
            records = _load(_frames(root, backend, video_id))
            timeline = _load(output_root / backend / video_id / "primary-player.jsonl")
            sequence = pose_sequence_from_records(records, timeline)
            common_events = _load(output_root / "yolo" / video_id / "events.jsonl")
            video_valid: Counter[str] = Counter()
            video_total: Counter[str] = Counter()
            for event in common_events:
                interval = EventInterval(
                    event["event_id"], event["event_code"], event["start_ms"], event["end_ms"],
                    1, event.get("key_phases_ms"),
                )
                for indicator in indicators_by_event[event["event_code"]]:
                    indicator_id = indicator["indicator_id"]
                    results = compute_event_features(sequence, interval, indicator["required_features"])
                    total_counts[indicator_id] += 1
                    video_total[indicator_id] += 1
                    if all(item.valid for item in results):
                        valid_counts[indicator_id] += 1
                        video_valid[indicator_id] += 1
                    supplemental_names = SUPPLEMENTAL_FEATURES_BY_INDICATOR.get(indicator_id, [])
                    if supplemental_names:
                        supplemental = compute_event_features(sequence, interval, supplemental_names)
                        supplemental_total[indicator_id] += 1
                        if all(item.valid for item in supplemental):
                            supplemental_valid[indicator_id] += 1
            videos.append(
                {
                    "video_id": video_id,
                    "common_event_count": len(common_events),
                    "indicator_feature_validity": {
                        name: {
                            "valid": video_valid[name],
                            "total": total,
                            "valid_rate": round(video_valid[name] / max(total, 1), 6),
                        }
                        for name, total in sorted(video_total.items())
                    },
                }
            )
            clear_feature_cache(sequence)
        total_indicator_records = sum(total_counts.values())
        valid_indicator_records = sum(valid_counts.values())
        model_results.append(
            {
                "backend": backend,
                "common_event_source": "YOLO event candidates reused identically for all pose backends",
                "indicator_records": total_indicator_records,
                "valid_indicator_records": valid_indicator_records,
                "unavailable_indicator_records": total_indicator_records - valid_indicator_records,
                "unavailable_rate": round((total_indicator_records - valid_indicator_records) / max(total_indicator_records, 1), 6),
                "indicator_feature_validity": {
                    name: {
                        "valid": valid_counts[name],
                        "total": total,
                        "valid_rate": round(valid_counts[name] / max(total, 1), 6),
                    }
                    for name, total in sorted(total_counts.items())
                },
                "supplemental_ankle_granularity": {
                    name: {
                        "valid": supplemental_valid[name],
                        "total": total,
                        "valid_rate": round(supplemental_valid[name] / max(total, 1), 6),
                    }
                    for name, total in sorted(supplemental_total.items())
                },
                "videos": videos,
            }
        )
    report["common_event_feature_comparison"] = {
        "execution_mode": "historical_replay",
        "historical_registry_version": HISTORICAL_REGISTRY_VERSION,
        "historical_registry_sha256": HISTORICAL_REGISTRY_SHA256,
        "historical_indicator_ids": list(HISTORICAL_INDICATOR_IDS),
        "status": "evaluated_on_identical_candidate_boundaries",
        "event_source_backend": "yolo",
        "event_truth_status": "candidate_boundaries_not_manual_ground_truth",
        "models": model_results,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(report_path), "execution_mode": "historical_replay", "models": len(model_results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
