from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from rallymate_events import detect_pose_events
from rallymate_features import (
    EventInterval,
    clear_feature_cache,
    compute_event_features,
    pose_sequence_from_records,
)
from rallymate_scoring.feasibility import validate_feasibility_registry


VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
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
        description="Replay the frozen 2026-08-13 six-indicator scoring loop"
    )
    parser.add_argument(
        "--historical-replay",
        action="store_true",
        help="explicitly authorize the frozen six-indicator historical workflow",
    )
    parser.add_argument("--video-id", action="append", dest="video_ids")
    parser.add_argument("--reuse-complete", action="store_true")
    args = parser.parse_args(argv)
    if not args.historical_replay:
        parser.error(
            "--historical-replay is required; this script uses the frozen "
            "2026-08-13 six-indicator registry, not the current registry"
        )
    return args


def _load_historical_registry(root: Path) -> dict:
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


def _historical_replay_metadata() -> dict:
    return {
        "execution_mode": "historical_replay",
        "registry_version": HISTORICAL_REGISTRY_VERSION,
        "registry_sha256": HISTORICAL_REGISTRY_SHA256,
        "indicator_ids": list(HISTORICAL_INDICATOR_IDS),
        "semantics": "frozen_six_indicator_baseline_not_current_production_registry",
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _require_reused_historical_bundle(
    *,
    video_id: str,
    registry: dict,
    events: list[dict],
    features: list[dict],
    records: list[dict],
    summary: dict,
) -> None:
    if summary.get("video_id") != video_id:
        raise RuntimeError("historical replay summary video_id mismatch")

    expected_by_event: dict[str, set[str]] = defaultdict(set)
    for indicator in registry["indicators"]:
        expected_by_event[indicator["indicator_id"].split("-")[0]].add(
            indicator["indicator_id"]
        )

    events_by_id: dict[str, dict] = {}
    for event in events:
        event_id = event.get("event_id")
        event_code = event.get("event_code")
        if (
            not isinstance(event_id, str)
            or event_id in events_by_id
            or event_code not in expected_by_event
            or event.get("provenance", {}).get("source_id") != video_id
        ):
            raise RuntimeError(
                "historical replay event identity/provenance is outside the frozen scope"
            )
        events_by_id[event_id] = event

    for feature in features:
        event = events_by_id.get(feature.get("event_id"))
        if (
            feature.get("video_id") != video_id
            or event is None
            or feature.get("event_code") != event.get("event_code")
        ):
            raise RuntimeError(
                "historical replay feature is outside the frozen event bundle"
            )

    record_keys: set[tuple[str, str, str]] = set()
    indicators_by_event_id: dict[str, set[str]] = {
        event_id: set() for event_id in events_by_id
    }
    for record in records:
        event_id = record.get("event_id")
        indicator_id = record.get("indicator_id")
        event = events_by_id.get(event_id)
        key = (video_id, str(event_id), str(indicator_id))
        if (
            record.get("video_id") != video_id
            or event is None
            or record.get("event_code") != event.get("event_code")
            or indicator_id not in expected_by_event[event["event_code"]]
            or record.get("provenance", {}).get(
                "feasibility_registry_version"
            )
            != HISTORICAL_REGISTRY_VERSION
            or key in record_keys
        ):
            raise RuntimeError(
                "historical replay indicator record is outside the frozen registry bundle"
            )
        record_keys.add(key)
        indicators_by_event_id[event_id].add(indicator_id)

    for event_id, event in events_by_id.items():
        if indicators_by_event_id[event_id] != expected_by_event[event["event_code"]]:
            raise RuntimeError(
                "historical replay indicator records do not cover the exact frozen set"
            )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    root = Path.cwd()
    feasibility = _load_historical_registry(root)
    indicators = feasibility["indicators"]
    required_by_event: dict[str, set[str]] = defaultdict(set)
    indicators_by_event: dict[str, list[dict]] = defaultdict(list)
    for indicator in indicators:
        event_code = indicator["indicator_id"].split("-")[0]
        required_by_event[event_code].update(indicator["required_features"])
        indicators_by_event[event_code].append(indicator)
    baseline = json.loads(
        (root / "reports" / "pose-model-baseline.json").read_text(encoding="utf-8")
    )
    baseline_by_video = {item["id"]: item["analysis"] for item in baseline["videos"]}
    summaries = []
    annotation_candidates = []
    selected_video_ids = args.video_ids or list(VIDEO_IDS)
    for video_id in selected_video_ids:
        output_dir = root / "reports" / "scoring-loop" / video_id
        completed_paths = (
            output_dir / "events.jsonl",
            output_dir / "features.jsonl",
            output_dir / "indicator-features.jsonl",
            output_dir / "summary.json",
        )
        if args.reuse_complete and all(path.exists() for path in completed_paths):
            summary = json.loads(completed_paths[-1].read_text(encoding="utf-8"))
            reused_events = _load_jsonl(completed_paths[0])
            reused_features = _load_jsonl(completed_paths[1])
            reused_records = _load_jsonl(completed_paths[2])
            _require_reused_historical_bundle(
                video_id=video_id,
                registry=feasibility,
                events=reused_events,
                features=reused_features,
                records=reused_records,
                summary=summary,
            )
            summary["historical_replay"] = _historical_replay_metadata()
            completed_paths[-1].write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summaries.append(summary)
            for event in reused_events:
                annotation_candidates.append(
                    {
                        "video_id": video_id,
                        "candidate_event_id": event["event_id"],
                        "event_code": event["event_code"],
                        "candidate_start_ms": event["start_ms"],
                        "candidate_end_ms": event["end_ms"],
                        "candidate_key_phases_ms": event["key_phases_ms"],
                        "annotation_status": "pending",
                        "annotator_id": None,
                        "manual_start_ms": None,
                        "manual_end_ms": None,
                        "manual_key_phases_ms": None,
                        "instructions": "review video independently; candidate boundaries are hints, not truth",
                    }
                )
            continue
        records = [
            json.loads(line)
            for line in (
                root / "runs" / "full-test" / video_id / "frames.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        timeline = [
            json.loads(line)
            for line in (
                root / "reports" / "primary-player" / video_id / "primary-player.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        sequence = pose_sequence_from_records(records, timeline)
        events = detect_pose_events(sequence, source_id=video_id, video_id=video_id)
        for event in events:
            event.setdefault("provenance", {})[
                "historical_replay"
            ] = _historical_replay_metadata()
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(output_dir / "events.jsonl", events)
        feature_records = []
        feature_lookup: dict[tuple[str, str], dict] = {}
        for event in events:
            interval = EventInterval(
                event_id=event["event_id"],
                event_code=event["event_code"],
                start_ms=event["start_ms"],
                end_ms=event["end_ms"],
                person_track_id=event["person_track_id"],
                key_phases=event["key_phases_ms"],
            )
            names = sorted(required_by_event[event["event_code"]])
            for result in compute_event_features(sequence, interval, names):
                payload = result.to_dict()
                payload.update(
                    {
                        "schema_version": "1.0.0",
                        "video_id": video_id,
                        "event_id": event["event_id"],
                        "event_code": event["event_code"],
                        "person_track_id": event["person_track_id"],
                    }
                )
                payload["provenance"].update(
                    {
                        "video_sha256": baseline_by_video[video_id]["video"]["sha256"],
                        "frames_sha256": baseline_by_video[video_id]["artifacts"]["frames_sha256"],
                        "pose_backend": "yolo",
                        "pose_model_sha256": baseline["models"]["pose"]["sha256"],
                        "primary_player_algorithm_version": "primary-player-v0.1.0",
                        "event_detector_version": event["provenance"]["detector_version"],
                        "historical_replay": _historical_replay_metadata(),
                    }
                )
                feature_records.append(payload)
                feature_lookup[(event["event_id"], result.feature_name)] = payload
        _write_jsonl(output_dir / "features.jsonl", feature_records)
        indicator_records = []
        for event in events:
            for indicator in indicators_by_event[event["event_code"]]:
                features = [
                    feature_lookup[(event["event_id"], feature_name)]
                    for feature_name in indicator["required_features"]
                ]
                all_valid = all(item["valid"] for item in features)
                indicator_records.append(
                    {
                        "schema_version": "1.0.0",
                        "video_id": video_id,
                        "indicator_id": indicator["indicator_id"],
                        "feasibility_level": "F2",
                        "event_id": event["event_id"],
                        "event_code": event["event_code"],
                        "person_track_id": event["person_track_id"],
                        "feature_status": "measured" if all_valid else "unavailable",
                        "features": [
                            {
                                "feature_name": item["feature_name"],
                                "feature_version": item["feature_version"],
                                "value": item["value"],
                                "unit": item["unit"],
                                "valid": item["valid"],
                                "confidence": item["confidence"],
                                "reason": item["reason"],
                                "source_frames": item["source_frames"],
                            }
                            for item in features
                        ],
                        "scoring_status": (
                            "calibration_required" if all_valid else "unavailable"
                        ),
                        "grade": None,
                        "reason_codes": (
                            ["coach_calibration_missing", "independent_test_missing"]
                            if all_valid
                            else ["required_feature_unavailable"]
                        ),
                        "provenance": {
                            "video_id": video_id,
                            "event_detector_version": event["provenance"]["detector_version"],
                            "primary_player_algorithm_version": "primary-player-v0.1.0",
                            "pose_backend": "yolo",
                            "pose_model_sha256": baseline["models"]["pose"]["sha256"],
                            "feasibility_registry_version": feasibility["registry_version"],
                            "historical_replay": _historical_replay_metadata(),
                        },
                    }
                )
        _write_jsonl(output_dir / "indicator-features.jsonl", indicator_records)
        event_counts = Counter(event["event_code"] for event in events)
        valid_counts = Counter(
            record["indicator_id"]
            for record in indicator_records
            if record["feature_status"] == "measured"
        )
        total_counts = Counter(record["indicator_id"] for record in indicator_records)
        summary = {
            "schema_version": "1.0.0",
            "video_id": video_id,
            "historical_replay": _historical_replay_metadata(),
            "status": "candidate_events_features_measured_calibration_required",
            "event_counts": dict(event_counts),
            "event_evaluation": {
                "status": "ground_truth_required",
                "event_f1": None,
                "segment_iou": None,
                "boundary_mae_ms": None,
                "boundary_p95_ms": None,
                "phase_boundary_mae_ms": None,
                "phase_boundary_p95_ms": None,
                "phase_boundary_valid_rate": None,
                "phase_boundary_by_name": {},
            },
            "indicator_feature_validity": {
                indicator_id: {
                    "valid": valid_counts[indicator_id],
                    "total": total_counts[indicator_id],
                    "valid_rate": round(
                        valid_counts[indicator_id] / max(total_counts[indicator_id], 1), 6
                    ),
                }
                for indicator_id in sorted(total_counts)
            },
            "artifacts": {
                "events_jsonl": str(output_dir / "events.jsonl"),
                "features_jsonl": str(output_dir / "features.jsonl"),
                "indicator_features_jsonl": str(output_dir / "indicator-features.jsonl"),
            },
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summaries.append(summary)
        clear_feature_cache(sequence)
        for event in events:
            annotation_candidates.append(
                {
                    "video_id": video_id,
                    "candidate_event_id": event["event_id"],
                    "event_code": event["event_code"],
                    "candidate_start_ms": event["start_ms"],
                    "candidate_end_ms": event["end_ms"],
                    "candidate_key_phases_ms": event["key_phases_ms"],
                    "annotation_status": "pending",
                    "annotator_id": None,
                    "manual_start_ms": None,
                    "manual_end_ms": None,
                    "manual_key_phases_ms": None,
                    "instructions": "review video independently; candidate boundaries are hints, not truth",
                }
            )
    manifest = {
        "schema_version": "1.0.0",
        "manifest_version": "event-labeling-2026-08-13.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "historical_replay": _historical_replay_metadata(),
        "status": "manual_annotation_required",
        "output_contract": "contracts/events.schema.json with annotation_source=manual and annotator_id",
        "candidates": annotation_candidates,
    }
    manifest_path = root / "data" / "annotations" / "event-labeling-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": "minimum-scoring-loop-baseline-2026-08-13.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "historical_replay": _historical_replay_metadata(),
        "status": "F2_features_calibration_required_event_ground_truth_required",
        "videos": summaries,
        "event_labeling_manifest": str(manifest_path),
    }
    report_path = root / "reports" / "minimum-scoring-loop-baseline.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"report": str(report_path), "execution_mode": "historical_replay", "videos": len(summaries), "annotation_candidates": len(annotation_candidates)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
