from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.calibration_dataset import compile_calibration_dataset
from rallymate_scoring.manual_event_features import build_manual_event_features


NAMES = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


def _event(event_id: str, code: str, start: int, end: int, phases: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "video_id": "video-manual",
        "event_id": event_id,
        "person_track_id": 1,
        "event_code": code,
        "start_ms": start,
        "end_ms": end,
        "key_phases_ms": phases,
        "confidence": 1.0,
        "boundary_uncertainty_ms": 20,
        "quality_flags": [],
        "view_group": "fixed-rear",
        "annotation_source": "manual",
        "annotator_id": "event-annotator",
        "reviewer_id": "event-reviewer",
        "adjudication_status": "accepted",
        "provenance": {"truth_pack_version": "synthetic-manual-v1"},
    }


class ManualEventFeaturesTests(unittest.TestCase):
    def _inputs(self, directory: Path) -> tuple[Path, Path, Path]:
        frames = directory / "frames.jsonl"
        timeline = directory / "primary-player.jsonl"
        events = directory / "manual-events.jsonl"
        frame_records = []
        timeline_records = []
        for index in range(25):
            timestamp = index * 40
            progress = index / 24.0
            hip_shift = 0.10 * progress * progress
            ankle_rise = 0.04 * max(0.0, 1.0 - abs(progress - 0.35) / 0.35)
            coordinates = {
                "nose": (0.50 + hip_shift, 0.15),
                "left_eye": (0.48 + hip_shift, 0.14),
                "right_eye": (0.52 + hip_shift, 0.14),
                "left_ear": (0.46 + hip_shift, 0.16),
                "right_ear": (0.54 + hip_shift, 0.16),
                "left_shoulder": (0.43 + hip_shift, 0.35),
                "right_shoulder": (0.57 + hip_shift, 0.35),
                "left_elbow": (0.40 + hip_shift, 0.48),
                "right_elbow": (0.60 + hip_shift, 0.48),
                "left_wrist": (0.39 + hip_shift, 0.60),
                "right_wrist": (0.61 + hip_shift, 0.60),
                "left_hip": (0.45 + hip_shift, 0.58),
                "right_hip": (0.55 + hip_shift, 0.58),
                "left_knee": (0.43 + hip_shift, 0.75),
                "right_knee": (0.57 + hip_shift, 0.75),
                "left_ankle": (0.40 + hip_shift * 1.2, 0.92 - ankle_rise),
                "right_ankle": (0.60 + hip_shift * 0.8, 0.92 - ankle_rise * 0.9),
            }
            frame_records.append(
                {
                    "frame": {
                        "processed_index": index,
                        "index": 100 + index,
                        "timestamp_ms": timestamp,
                    },
                    "poses": [
                        {
                            "person_track_id": 10,
                            "keypoint_format": "coco17",
                            "keypoints": [
                                {
                                    "index": point_index,
                                    "name": name,
                                    "x_normalized": coordinates[name][0],
                                    "y_normalized": coordinates[name][1],
                                    "confidence": 0.95,
                                    "in_frame": True,
                                }
                                for point_index, name in enumerate(NAMES)
                            ],
                        }
                    ],
                }
            )
            timeline_records.append(
                {
                    "schema_version": "1.0.0",
                    "processed_index": index,
                    "source_frame_index": 100 + index,
                    "timestamp_ms": timestamp,
                    "primary_player_id": 1,
                    "selection_status": "selected",
                    "source_track_id": 10,
                    "bbox_px": [0, 0, 100, 200],
                    "pose_present": True,
                    "keypoint_valid_fraction": 1.0,
                    "selection_algorithm_version": "primary-player-v0.3.0",
                }
            )
        _write_jsonl(frames, frame_records)
        _write_jsonl(timeline, timeline_records)
        _write_jsonl(
            events,
            [
                _event(
                    "manual-fs01",
                    "FS01",
                    0,
                    320,
                    {
                        "preload_ms": 80,
                        "takeoff_proxy_ms": 120,
                        "landing_proxy_ms": 240,
                        "redistribution_ms": 280,
                        "initiation_ms": 320,
                    },
                ),
                _event(
                    "manual-fs02",
                    "FS02",
                    320,
                    640,
                    {
                        "direction_conversion_ms": 360,
                        "support_extension_proxy_ms": 440,
                        "lead_foot_motion_onset_proxy_ms": 520,
                        "first_step_slowdown_proxy_ms": 600,
                    },
                ),
                _event(
                    "manual-fs09",
                    "FS09",
                    640,
                    960,
                    {
                        "peak_speed_ms": 680,
                        "deceleration_peak_ms": 760,
                        "restabilization_onset_ms": 840,
                        "stable_control_onset_ms": 920,
                    },
                ),
            ],
        )
        return frames, timeline, events

    def test_manual_boundaries_emit_all_dynamic_registry_indicators_without_grades(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames, timeline, events = self._inputs(directory)
            result = build_manual_event_features(
                frames_path=frames,
                primary_timeline_path=timeline,
                manual_events_path=events,
                feasibility_registry_path=root / "metric-feasibility-pose-wave-v2.json",
                video_id="video-manual",
                output_dir=directory / "output",
                pose_model={
                    "backend": "synthetic",
                    "runtime": "test",
                    "profile": "coco17",
                    "model_sha256": "a" * 64,
                    "native_keypoint_format": "coco17",
                    "native_keypoint_count": 17,
                },
            )
            self.assertEqual(len(result["events"]), 3)
            self.assertEqual(len(result["indicator_records"]), 13)
            self.assertEqual(
                {item["event_id"] for item in result["indicator_records"]},
                {"manual-fs01", "manual-fs02", "manual-fs09"},
            )
            self.assertTrue(all(item["grade"] is None for item in result["indicator_records"]))
            self.assertTrue(
                all(item["scoring_status"] in {"calibration_required", "unavailable"} for item in result["indicator_records"])
            )
            self.assertTrue(
                all(
                    item["provenance"]["candidate_event_detector_used"] is False
                    and len(item["provenance"]["manual_event_sha256"]) == 64
                    for item in result["indicator_records"]
                )
            )
            self.assertFalse(result["summary"]["safety"]["generated_thresholds"])
            self.assertFalse(result["summary"]["safety"]["any_non_null_grade"])
            self.assertEqual(
                result["summary"]["registry_version"],
                "pose-wave-2026-08-22.17",
            )
            self.assertTrue(result["features"])
            self.assertTrue(all("raw_value" in item and "smoothed_value" in item for item in result["features"]))

    def test_manual_feature_output_exactly_joins_calibration_compiler(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames, timeline, events = self._inputs(directory)
            feature_output = directory / "manual-feature-output"
            build_manual_event_features(
                frames_path=frames,
                primary_timeline_path=timeline,
                manual_events_path=events,
                feasibility_registry_path=root / "metric-feasibility-pose-wave-v2.json",
                video_id="video-manual",
                output_dir=feature_output,
            )
            semantics = directory / "semantics.jsonl"
            labels = directory / "labels.jsonl"
            manifest_path = directory / "truth-manifest.json"
            validation = directory / "truth-validation.json"
            metadata = directory / "metadata.json"
            policy = directory / "policy.json"
            fs02_event = next(
                item
                for item in (json.loads(line) for line in events.read_text().splitlines())
                if item["event_code"] == "FS02"
            )
            _write_jsonl(
                semantics,
                [
                    {
                        "schema_version": "1.0.0",
                        "annotation_id": "target-direction-manual-1",
                        "video_id": "video-manual",
                        "event_id": fs02_event["event_id"],
                        "indicator_id": "FS02-M02",
                        "semantic_key": "target_direction",
                        "semantic_type": "direction",
                        "observable": True,
                        "value": {
                            "direction_deg": 0.0,
                            "coordinate_frame": "image_plane",
                        },
                        "null_reason": None,
                        "annotation_confidence": 0.9,
                        "annotator_id": "target-coach",
                        "reviewer_id": "target-reviewer",
                        "adjudication_status": "accepted",
                    }
                ],
            )
            _write_jsonl(labels, [])
            manifest_path.write_text(
                json.dumps({"pack_version": "synthetic-v1", "videos": [{"video_id": "video-manual"}]}),
                encoding="utf-8",
            )
            validation.write_text(
                json.dumps({"readiness": {"semantic_coverage": []}}), encoding="utf-8"
            )
            metadata.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "records": [
                            {
                                "video_id": "video-manual",
                                "person_track_id": 1,
                                "player_id": "player-1",
                                "session_id": "session-1",
                                "view_group": "fixed-rear",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            policy.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "policy_id": "single-video-test-policy",
                        "policy_version": "1.0.0",
                        "assignments": [{"split": "train", "video_ids": ["video-manual"]}],
                    }
                ),
                encoding="utf-8",
            )
            compiled_output = directory / "compiled"
            compiled = compile_calibration_dataset(
                feasibility_registry_path=root / "metric-feasibility-pose-wave-v2.json",
                indicator_feature_paths=[feature_output / "indicator-features.jsonl"],
                manual_events_path=events,
                manual_semantics_path=semantics,
                coach_labels_path=labels,
                truth_manifest_path=manifest_path,
                truth_validation_report_path=validation,
                group_metadata_path=metadata,
                split_policy_path=policy,
                output_dir=compiled_output,
                generated_at="2026-08-13T00:00:00+00:00",
            )
            self.assertEqual(compiled["counts"]["samples"], 13)
            samples = [
                json.loads(line)
                for line in (compiled_output / "samples.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(samples), 13)
            self.assertTrue(
                all(item["lineage"]["indicator_feature_sha256"] for item in samples)
            )
            self.assertTrue(
                all(item["lineage"]["join_policy"] == "exact_manual_event_id_only_v1" for item in samples)
            )
            target_sample = next(
                item for item in samples if item["indicator_id"] == "FS02-M02"
            )
            self.assertEqual(
                [item["feature_name"] for item in target_sample["feature_vector"]],
                [
                    "body_center_speed_body_s",
                    "hip_center_relative_to_ankle_support",
                    "torso_lean_deg",
                    "launch_direction_deg",
                    "target_direction_alignment_error_deg",
                ],
            )
            self.assertTrue(target_sample["feature_vector"][-1]["valid"])
            self.assertEqual(target_sample["feature_vector"][-1]["unit"], "deg")

    def test_rejects_multi_video_manual_input_and_candidate_only_flags(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames, timeline, events = self._inputs(directory)
            records = [json.loads(line) for line in events.read_text().splitlines()]
            records[1]["video_id"] = "foreign-video"
            _write_jsonl(events, records)
            with self.assertRaisesRegex(ValueError, "other videos"):
                build_manual_event_features(
                    frames_path=frames,
                    primary_timeline_path=timeline,
                    manual_events_path=events,
                    feasibility_registry_path=root / "metric-feasibility-pose-wave-v2.json",
                    video_id="video-manual",
                    output_dir=directory / "output-a",
                )
            records[1]["video_id"] = "video-manual"
            records[0]["quality_flags"] = ["provisional_rule_baseline"]
            _write_jsonl(events, records)
            with self.assertRaisesRegex(ValueError, "candidate-only"):
                build_manual_event_features(
                    frames_path=frames,
                    primary_timeline_path=timeline,
                    manual_events_path=events,
                    feasibility_registry_path=root / "metric-feasibility-pose-wave-v2.json",
                    video_id="video-manual",
                    output_dir=directory / "output-b",
                )

    def test_missing_manual_phase_cannot_be_reported_as_measured(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames, timeline, events = self._inputs(directory)
            records = [json.loads(line) for line in events.read_text().splitlines()]
            records[0]["key_phases_ms"]["landing_proxy_ms"] = None
            _write_jsonl(events, records)
            result = build_manual_event_features(
                frames_path=frames,
                primary_timeline_path=timeline,
                manual_events_path=events,
                feasibility_registry_path=root / "metric-feasibility-pose-wave-v2.json",
                video_id="video-manual",
                output_dir=directory / "output",
            )
            landing = next(
                item
                for item in result["indicator_records"]
                if item["indicator_id"] == "FS01-M04"
            )
            self.assertEqual(landing["feature_status"], "unavailable")
            self.assertEqual(landing["scoring_status"], "unavailable")
            self.assertIn(
                "required_phase_missing:landing_proxy_ms",
                landing["quality_gate"]["hard_fail_flags"],
            )
            preload = next(
                item
                for item in result["indicator_records"]
                if item["indicator_id"] == "FS01-M02"
            )
            self.assertNotIn(
                "required_phase_missing:landing_proxy_ms",
                preload["quality_gate"]["input_quality_flags"],
            )


if __name__ == "__main__":
    unittest.main()
