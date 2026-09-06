from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from rallymate_scoring.scoring import score_indicator


VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


def main() -> None:
    root = Path.cwd()
    baseline = json.loads(
        (root / "reports" / "pose-model-baseline.json").read_text(encoding="utf-8")
    )
    all_records = []
    coach_candidates = []
    keypoint_candidate_frames: dict[str, set[int]] = {video_id: set() for video_id in VIDEO_IDS}
    per_video = []
    for video_id in VIDEO_IDS:
        source = (
            root
            / "reports"
            / "scoring-loop"
            / video_id
            / "indicator-features.jsonl"
        )
        records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
        scores = []
        for record in records:
            features = record["features"]
            source_frames = sorted(
                {
                    frame
                    for feature in features
                    for frame in feature.get("source_frames", [])
                }
            )
            result = score_indicator(
                indicator_id=record["indicator_id"],
                features=features,
                calibration=None,
                model_versions={
                    "pose_backend": "yolo",
                    "pose_model_sha256": baseline["models"]["pose"]["sha256"],
                    "primary_player": "primary-player-v0.1.0",
                    "event": "pose-motion-bout-v0.1.0",
                    "feature": "rallymate-features-v0.1.0",
                    "calibration": None,
                },
                evidence=[
                    {
                        "video_id": video_id,
                        "event_id": record["event_id"],
                        "person_track_id": record["person_track_id"],
                        "source_frames": source_frames,
                    }
                ],
            )
            result.update(
                {
                    "video_id": video_id,
                    "event_id": record["event_id"],
                    "event_code": record["event_code"],
                    "indicator_id": record["indicator_id"],
                    "feasibility_level": "F2",
                }
            )
            scores.append(result)
            all_records.append(result)
            coach_candidates.append(
                {
                    "video_id": video_id,
                    "event_id": record["event_id"],
                    "indicator_id": record["indicator_id"],
                    "feature_status": record["feature_status"],
                    "source_frames": source_frames,
                    "requested_labels": ["A_to_E", "ranking"],
                    "annotation_status": "pending",
                    "annotator_ids": [],
                }
            )
            keypoint_candidate_frames[video_id].update(source_frames)
        output = source.parent / "scores.jsonl"
        output.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in scores),
            encoding="utf-8",
        )
        per_video.append(
            {
                "video_id": video_id,
                "score_count": len(scores),
                "status_counts": dict(Counter(item["status"] for item in scores)),
                "scores_jsonl": str(output),
            }
        )
    manifest = {
        "schema_version": "1.0.0",
        "manifest_version": "coach-labeling-2026-08-13.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "coach_labels_required",
        "output_contract": "contracts/coach-label.schema.json",
        "candidates": coach_candidates,
    }
    manifest_path = root / "data" / "annotations" / "coach-labeling-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    keypoint_candidates = []
    for video_id, selected_frames in keypoint_candidate_frames.items():
        frame_records = [
            json.loads(line)
            for line in (
                root / "runs" / "full-test" / video_id / "frames.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        by_source_frame = {
            int(item["frame"]["index"]): item["frame"] for item in frame_records
        }
        for frame in sorted(selected_frames):
            metadata = by_source_frame.get(frame)
            if metadata is None:
                continue
            keypoint_candidates.append(
                {
                    "video_id": video_id,
                    "source_frame_index": frame,
                    "timestamp_ms": int(metadata["timestamp_ms"]),
                    "primary_player_id": 1,
                    "view_group": None,
                    "annotator_id": None,
                    "annotation_status": "pending",
                    "required_joints": [
                        "left_shoulder",
                        "right_shoulder",
                        "left_hip",
                        "right_hip",
                        "left_knee",
                        "right_knee",
                        "left_ankle",
                        "right_ankle",
                    ],
                }
            )
    keypoint_manifest = {
        "schema_version": "1.0.0",
        "manifest_version": "keypoint-ground-truth-2026-08-13.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "manual_correction_required",
        "output_contract": "contracts/keypoint-ground-truth.schema.json",
        "missing_value_policy": "invisible joints require null coordinates; never zero-fill",
        "candidates": keypoint_candidates,
    }
    keypoint_manifest_path = (
        root / "data" / "annotations" / "keypoint-ground-truth-labeling-manifest.json"
    )
    keypoint_manifest_path.write_text(
        json.dumps(keypoint_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    status_counts = Counter(item["status"] for item in all_records)
    report = {
        "schema_version": "1.0.0",
        "report_version": "calibration-interface-2026-08-13.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "calibration_required",
        "threshold_backend": {
            "interface": "implemented",
            "calibration_artifact": None,
            "threshold_version": None,
            "reason": "coach_ground_truth_missing_no_thresholds_generated",
        },
        "ordinal_regression_backend": {
            "interface": "implemented",
            "calibration_artifact": None,
            "model_version": None,
            "reason": "coach_ground_truth_missing_no_model_trained",
        },
        "annotator_agreement": {
            "status": "coach_labels_required",
            "quadratic_weighted_kappa": None,
            "kendall_tau": None,
        },
        "score_status_counts": dict(status_counts),
        "grade_counts": {},
        "videos": per_video,
        "coach_labeling_manifest": str(manifest_path),
        "keypoint_labeling_manifest": str(keypoint_manifest_path),
        "safety_assertions": {
            "any_non_null_grade": any(item["grade"] is not None for item in all_records),
            "any_threshold_version": any(item["threshold_version"] is not None for item in all_records),
            "fake_thresholds_generated": False,
        },
    }
    output = root / "reports" / "calibration-interface-report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output), "scores": len(all_records), "status_counts": dict(status_counts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
