from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rallymate_scoring.loop import run_minimum_scoring_loop
from rallymate_scoring.calibration_dataset import (
    _feature_vector,
    _manual_target_alignment_feature,
)
from rallymate_scoring.calibration_fitting import canonical_sha256
from rallymate_scoring.indicator_feature_qualification import (
    SYNTHETIC_SOURCE_STATUS,
    build_qualification_snapshot,
    derive_feature_qualification,
    nonproduction_source_metadata,
)
from rallymate_scoring.quality_policy import evaluate_indicator_event_quality
from rallymate_scoring.scoring_context import (
    bind_scoring_context_evidence_to_video,
    build_scoring_reference_worklist,
    load_scoring_reference_context,
    resolve_target_direction_context,
    signed_angle_delta_deg,
    validate_resolved_target_direction_context,
    validate_scoring_reference_context,
)
from rallymate_scoring.batch import score_indicator_records
from rallymate_tracking import PRIMARY_PLAYER_ALGORITHM_VERSION


def _event(event_id: str = "fs02-test-001") -> dict:
    return {
        "event_id": event_id,
        "event_code": "FS02",
        "start_ms": 1000,
        "end_ms": 1600,
    }


def _accepted_worklist() -> dict:
    payload = build_scoring_reference_worklist(
        events=[_event()],
        video_id="video-1",
        video_sha256="a" * 64,
        created_at="2026-08-22T00:00:00+00:00",
    )
    payload["source"]["created_by"] = "coach-1"
    observation = payload["observations"][0]
    observation.update(
        {
            "status": "accepted",
            "target_direction_deg": -179.0,
            "coordinate_frame": "image_plane",
            "confidence": 0.9,
            "observer_id": "coach-1",
            "reviewer_id": "reviewer-2",
            "reason": None,
        }
    )
    return payload


def _point(index: int, name: str, x: float, y: float) -> dict:
    return {
        "index": index,
        "name": name,
        "x_normalized": x,
        "y_normalized": y,
        "confidence": 0.95,
    }


class ScoringContextTests(unittest.TestCase):
    def test_score_evidence_identity_overrides_untrusted_context_fields(self) -> None:
        evidence = bind_scoring_context_evidence_to_video(
            {
                "status": "available",
                "event_id": "fs02-test-001",
                "video_id": "untrusted-video",
                "video_sha256": "0" * 64,
                "evidence_type": "untrusted-type",
            },
            video_id="video-1",
            video_sha256="a" * 64,
        )
        self.assertEqual(evidence["evidence_type"], "scoring_reference_context")
        self.assertEqual(evidence["video_id"], "video-1")
        self.assertEqual(evidence["video_sha256"], "a" * 64)
        with self.assertRaisesRegex(ValueError, "video_sha256 must be SHA-256"):
            bind_scoring_context_evidence_to_video(
                {"status": "available"},
                video_id="video-1",
                video_sha256="not-a-sha",
            )

    def test_pending_worklist_is_safe_and_machine_valid(self) -> None:
        payload = build_scoring_reference_worklist(
            events=[_event(), {"event_id": "fs01-x", "event_code": "FS01", "start_ms": 0, "end_ms": 900}],
            video_id="video-1",
            video_sha256="a" * 64,
            created_at="2026-08-22T00:00:00+00:00",
        )
        observations = validate_scoring_reference_context(
            payload,
            expected_video_id="video-1",
            expected_video_sha256="a" * 64,
        )
        self.assertEqual(list(observations), ["fs02-test-001"])
        self.assertEqual(observations["fs02-test-001"]["status"], "pending")
        self.assertIsNone(observations["fs02-test-001"]["target_direction_deg"])
        self.assertFalse(payload["safety"]["is_grade"])
        self.assertFalse(payload["safety"]["contains_A_to_E_thresholds"])

    def test_accepted_reference_computes_wrapped_alignment_not_grade(self) -> None:
        payload = _accepted_worklist()
        observation = validate_scoring_reference_context(payload)["fs02-test-001"]
        launch = {
            "feature_name": "launch_direction_deg",
            "feature_version": "pose-proxy-v1",
            "value": 179.0,
            "unit": "deg",
            "confidence": 0.8,
            "valid": True,
            "reason": "valid",
            "source_frames": [30, 31],
        }
        context = resolve_target_direction_context(
            event=_event(),
            launch_direction_feature=launch,
            observation=observation,
            source_sha256="b" * 64,
        )
        self.assertEqual(signed_angle_delta_deg(179.0, -179.0), -2.0)
        self.assertEqual(context["status"], "available")
        self.assertEqual(context["context_feature"]["value"], 2.0)
        self.assertEqual(context["context_feature"]["signed_value"], -2.0)
        self.assertEqual(context["context_feature"]["confidence"], 0.8)
        self.assertFalse(context["is_grade"])
        self.assertIsNone(context["threshold_version"])
        validate_resolved_target_direction_context(
            context,
            event=_event(),
            launch_direction_feature=launch,
        )
        for field, value, message in (
            ("schema_version", "0.0.0", "schema_version"),
            ("context_version", "stale", "context_version"),
            ("semantic_key", "movement_direction", "semantic_key"),
            ("source_sha256", "not-a-sha", "source_sha256"),
            ("observation_event_id", "other-event", "observation_event_id"),
            ("reviewer_id", context["observer_id"], "independent reviewer"),
        ):
            tampered = copy.deepcopy(context)
            tampered[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                validate_resolved_target_direction_context(
                    tampered,
                    event=_event(),
                    launch_direction_feature=launch,
                )

        for mutate, message in (
            (lambda item: item.__setitem__("grade", "A"), "fields are invalid"),
            (
                lambda item: item.__setitem__("video_id", "untrusted-video"),
                "fields are invalid",
            ),
            (
                lambda item: item["context_feature"].__setitem__(
                    "observed_motion_direction_deg", 170.0
                ),
                "observed motion direction mismatch",
            ),
            (
                lambda item: item["context_feature"].__setitem__(
                    "direction_convention", "clockwise_world_bearing"
                ),
                "direction convention",
            ),
            (
                lambda item: item["context_feature"].__setitem__("confidence", 1.1),
                "confidence is invalid",
            ),
            (
                lambda item: item["context_feature"].__setitem__(
                    "reason", "fabricated_trace"
                ),
                "feature reason is invalid",
            ),
            (
                lambda item: item["context_feature"].__setitem__(
                    "unexpected_grade", "A"
                ),
                "feature fields are invalid",
            ),
        ):
            tampered = copy.deepcopy(context)
            mutate(tampered)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_resolved_target_direction_context(
                    tampered,
                    event=_event(),
                    launch_direction_feature=launch,
                )

    def test_reference_loader_hashes_the_exact_parsed_byte_snapshot(self) -> None:
        payload = _accepted_worklist()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            source_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            path.write_bytes(source_bytes)
            with patch(
                "rallymate_scoring.scoring_context.file_sha256",
                side_effect=AssertionError("loader must not reopen the source to hash it"),
            ):
                loaded, observations, source_sha256 = load_scoring_reference_context(
                    path,
                    expected_video_id="video-1",
                    expected_video_sha256="a" * 64,
                )
            self.assertEqual(loaded, payload)
            self.assertEqual(set(observations), {"fs02-test-001"})
            self.assertEqual(
                source_sha256,
                hashlib.sha256(source_bytes).hexdigest().upper(),
            )
            with self.assertRaisesRegex(ValueError, "video_id mismatch"):
                load_scoring_reference_context(
                    path,
                    expected_video_id="wrong-video",
                    expected_video_sha256="a" * 64,
                )
            with self.assertRaisesRegex(ValueError, "video_sha256 mismatch"):
                load_scoring_reference_context(
                    path,
                    expected_video_id="video-1",
                    expected_video_sha256="f" * 64,
                )

    def test_reference_rejects_self_review_and_stale_event_binding(self) -> None:
        payload = _accepted_worklist()
        payload["observations"][0]["reviewer_id"] = "coach-1"
        with self.assertRaisesRegex(ValueError, "independent reviewer"):
            validate_scoring_reference_context(payload)

        payload = _accepted_worklist()
        observation = validate_scoring_reference_context(payload)["fs02-test-001"]
        with self.assertRaisesRegex(ValueError, "event binding mismatch"):
            resolve_target_direction_context(
                event={**_event(), "end_ms": 1601},
                launch_direction_feature={
                    "valid": True,
                    "value": 10.0,
                    "confidence": 0.9,
                    "source_frames": [1, 2],
                },
                observation=observation,
                source_sha256="c" * 64,
            )

    def test_loop_consumes_accepted_reference_without_inventing_grade(self) -> None:
        root = Path(__file__).resolve().parents[1]
        names = [
            "nose", "left_eye", "right_eye", "left_ear", "right_ear",
            "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
            "left_wrist", "right_wrist", "left_hip", "right_hip",
            "left_knee", "right_knee", "left_ankle", "right_ankle",
        ]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            frames = work / "frames.jsonl"
            timeline = work / "primary-player.jsonl"
            frame_records = []
            timeline_records = []
            for index in range(48):
                phase = min(index, 24)
                shift = 0.0035 * phase
                knee_delta = 0.012 * (1.0 - abs(12 - phase) / 12.0) if phase <= 24 else 0.0
                coords = [(0.5 + shift, 0.15)] * 17
                coords[5:7] = [(0.43 + shift, 0.35), (0.57 + shift, 0.35)]
                coords[11:17] = [
                    (0.45 + shift, 0.58), (0.55 + shift, 0.58),
                    (0.44 + shift, 0.75 + knee_delta),
                    (0.56 + shift, 0.75 + knee_delta),
                    (0.42 + shift, 0.92), (0.58 + shift, 0.92),
                ]
                frame_records.append(
                    {
                        "frame": {
                            "processed_index": index,
                            "index": index,
                            "timestamp_ms": index * 40,
                        },
                        "poses": [
                            {
                                "person_track_id": 10,
                                "keypoints": [
                                    _point(i, name, coords[i][0], coords[i][1])
                                    for i, name in enumerate(names)
                                ],
                            }
                        ],
                    }
                )
                timeline_records.append(
                    {
                        "processed_index": index,
                        "source_track_id": 10,
                        "primary_player_id": 1,
                        "selection_status": "selected",
                        "selection_algorithm_version": PRIMARY_PLAYER_ALGORITHM_VERSION,
                        "keypoint_valid_fraction": 1.0,
                    }
                )
            frames.write_text(
                "".join(json.dumps(item) + "\n" for item in frame_records),
                encoding="utf-8",
            )
            timeline.write_text(
                "".join(json.dumps(item) + "\n" for item in timeline_records),
                encoding="utf-8",
            )
            first = run_minimum_scoring_loop(
                frames_path=frames,
                primary_timeline_path=timeline,
                output_dir=work / "first",
                feasibility_registry_path=root / "metric-feasibility-pose-wave-v2.json",
                source_id="context-first",
                video_id="video-context",
                pose_model={"backend": "test", "runtime": "cpu"},
                source_provenance={"video_sha256": "d" * 64},
            )
            fs02_events = [
                event for event in first["events"] if event["event_code"] == "FS02"
            ]
            self.assertTrue(fs02_events)
            first_records = [
                record
                for record in first["indicator_records"]
                if record["indicator_id"] == "FS02-M02"
            ]
            self.assertTrue(all(record["feature_status"] == "measured" for record in first_records))
            self.assertTrue(
                all(record["scoring_feature_status"] == "unavailable" for record in first_records)
            )
            context = build_scoring_reference_worklist(
                events=first["events"],
                video_id="video-context",
                video_sha256="d" * 64,
                created_at="2026-08-22T00:00:00+00:00",
            )
            context["source"]["created_by"] = "coach-1"
            for observation in context["observations"]:
                observation.update(
                    {
                        "status": "accepted",
                        "target_direction_deg": 0.0,
                        "coordinate_frame": "image_plane",
                        "confidence": 0.9,
                        "observer_id": "coach-1",
                        "reviewer_id": "reviewer-2",
                        "reason": None,
                    }
                )
            context_path = work / "reference.json"
            context_path.write_text(json.dumps(context), encoding="utf-8")
            second = run_minimum_scoring_loop(
                frames_path=frames,
                primary_timeline_path=timeline,
                output_dir=work / "second",
                feasibility_registry_path=root / "metric-feasibility-pose-wave-v2.json",
                source_id="context-second",
                video_id="video-context",
                pose_model={"backend": "test", "runtime": "cpu"},
                source_provenance={"video_sha256": "d" * 64},
                scoring_reference_context_path=context_path,
            )
            records = [
                record
                for record in second["indicator_records"]
                if record["indicator_id"] == "FS02-M02"
            ]
            self.assertEqual(len(records), len(fs02_events))
            self.assertTrue(all(record["scoring_context"]["status"] == "available" for record in records))
            self.assertTrue(
                all(
                    "tactical_target_direction_not_observed"
                    not in record["quality_gate"]["input_quality_flags"]
                    for record in records
                )
            )
            self.assertTrue(all(record["grade"] is None for record in records))
            self.assertTrue(
                all(
                    record["scoring_context"]["context_feature"]["valid"]
                    for record in records
                )
            )
            self.assertTrue(
                all(record["feature_status"] == "measured" for record in records)
            )
            self.assertTrue(
                all(record["scoring_feature_status"] == "measured" for record in records)
            )
            self.assertTrue(
                all(
                    [item["feature_name"] for item in record["features"]]
                    == [
                        "body_center_speed_body_s",
                        "hip_center_relative_to_ankle_support",
                        "torso_lean_deg",
                        "launch_direction_deg",
                    ]
                    and [item["feature_name"] for item in record["scoring_features"]]
                    == [
                        "body_center_speed_body_s",
                        "hip_center_relative_to_ankle_support",
                        "torso_lean_deg",
                        "launch_direction_deg",
                        "target_direction_alignment_error_deg",
                    ]
                    for record in records
                )
            )
            self.assertEqual(
                second["summary"]["scoring_reference_context"]["status"],
                "all_FS02_references_available",
            )
            self.assertFalse(
                second["summary"]["scoring_reference_context"][
                    "grades_or_thresholds_generated"
                ]
            )
            context_scores = [
                score
                for score in second["scores"]
                if score["indicator_id"] == "FS02-M02"
            ]
            self.assertTrue(context_scores)
            for score in context_scores:
                context_evidence = next(
                    item
                    for item in score["evidence"]
                    if item.get("evidence_type") == "scoring_reference_context"
                )
                self.assertEqual(context_evidence["video_id"], "video-context")
                self.assertEqual(context_evidence["video_sha256"], "d" * 64)

            rescored, _ = score_indicator_records(
                [records[0]],
                calibrations={},
                model_versions=second["summary"]["model_versions"],
            )
            rescored_context_evidence = next(
                item
                for item in rescored[0]["evidence"]
                if item.get("evidence_type") == "scoring_reference_context"
            )
            self.assertEqual(rescored_context_evidence["video_id"], "video-context")
            self.assertEqual(rescored_context_evidence["video_sha256"], "d" * 64)

    def test_manual_target_semantic_enters_complete_calibration_vector(self) -> None:
        pose_features = [
            {
                "feature_name": name,
                "feature_version": "pose-v1",
                "value": value,
                "unit": unit,
                "confidence": 0.9,
                "valid": True,
                "reason": "valid",
                "source_frames": [1, 2],
            }
            for name, value, unit in (
                ("body_center_speed_body_s", 1.2, "body/s"),
                ("hip_center_relative_to_ankle_support", 0.6, "ratio"),
                ("torso_lean_deg", 8.0, "deg"),
                ("launch_direction_deg", 170.0, "deg"),
            )
        ]
        feature_record = {
            "feature_status": "measured",
            "features": pose_features,
            "quality_gate": evaluate_indicator_event_quality(
                "FS02-M02", ["tactical_target_direction_not_observed"]
            ),
        }
        semantics = {
            "target_direction": {
                "observable": True,
                "value": {"direction_deg": -175.0, "coordinate_frame": "image_plane"},
                "annotation_confidence": 0.8,
                "null_reason": None,
            }
        }
        derived = _manual_target_alignment_feature(feature_record, semantics)
        vector = _feature_vector(
            feature_record,
            [
                "body_center_speed_body_s",
                "hip_center_relative_to_ankle_support",
                "torso_lean_deg",
                "launch_direction_deg",
                "target_direction_alignment_error_deg",
            ],
            derived_items={"target_direction_alignment_error_deg": derived},
        )
        qualification_snapshot = build_qualification_snapshot(
            indicator_id="FS02-M02",
            feature_record=feature_record,
            source_feature_canonical_sha256=canonical_sha256(feature_record),
            source_status=SYNTHETIC_SOURCE_STATUS,
            source_metadata=nonproduction_source_metadata(
                indicator_features_raw_sha256="a" * 64,
                record_count=1,
                video_id="video-context",
            ),
            resolved_target_direction=derived["valid"] is True,
        )
        complete, reasons = derive_feature_qualification(
            snapshot=qualification_snapshot,
            indicator_id="FS02-M02",
            feature_vector=vector,
            require_verified_source=False,
        )
        self.assertTrue(complete)
        self.assertEqual(reasons, [])
        self.assertEqual(vector[-1]["value"], 15.0)
        self.assertEqual(vector[-1]["unit"], "deg")
        self.assertTrue(vector[-1]["valid"])

    def test_schema_accepts_pending_and_rejects_grade_fields(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema optional dependency is unavailable")
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "contracts/scoring-reference-context.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validator = Draft202012Validator(schema)
        payload = build_scoring_reference_worklist(
            events=[_event()],
            video_id="video-1",
            video_sha256="a" * 64,
            created_at="2026-08-22T00:00:00+00:00",
        )
        self.assertEqual(list(validator.iter_errors(payload)), [])
        tampered = copy.deepcopy(payload)
        tampered["grade"] = "A"
        self.assertTrue(list(validator.iter_errors(tampered)))


if __name__ == "__main__":
    unittest.main()
