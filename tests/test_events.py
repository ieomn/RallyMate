from __future__ import annotations

import json
import copy
import unittest
from pathlib import Path

import numpy as np

from rallymate_events import detect_pose_events, evaluate_events, validate_event_record
from rallymate_events.rules import (
    EVENT_KINEMATIC_MIN_COVERAGE,
    _event_motion_reference,
    _event_kinematic_coverage,
    _event_quality_flags,
)
from rallymate_features.schemas import PoseSequence
from rallymate_scoring.indicator_requirements import required_phase_keys


NAMES = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)

ROOT = Path(__file__).resolve().parents[1]


def _motion_sequence() -> PoseSequence:
    timestamps = np.arange(0, 2400, 40, dtype=np.int64)
    base = {
        "left_shoulder": (0.40, 0.30),
        "right_shoulder": (0.60, 0.30),
        "left_hip": (0.45, 0.55),
        "right_hip": (0.55, 0.55),
        "left_knee": (0.44, 0.72),
        "right_knee": (0.56, 0.72),
        "left_ankle": (0.42, 0.90),
        "right_ankle": (0.58, 0.90),
    }
    trajectory = []
    for timestamp in timestamps:
        if timestamp < 600:
            shift = 0.0
        elif timestamp < 1400:
            phase = (timestamp - 600) / 800
            shift = 0.18 * (3 * phase**2 - 2 * phase**3)
        else:
            shift = 0.18
        trajectory.append(shift)
    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(timestamps.size),
        keypoints_xy={
            name: np.asarray([[x + shift, y] for shift in trajectory])
            for name, (x, y) in base.items()
        },
        confidence={name: np.full(timestamps.size, 0.9) for name in NAMES},
    )


def _static_sequence() -> PoseSequence:
    moving = _motion_sequence()
    return PoseSequence(
        timestamp_ms=moving.timestamp_ms.copy(),
        source_frames=moving.source_frames.copy(),
        keypoints_xy={
            name: np.repeat(values[:1], values.shape[0], axis=0)
            for name, values in moving.keypoints_xy.items()
        },
        confidence={
            name: values.copy() for name, values in moving.confidence.items()
        },
    )


def _common_mode_jitter_sequence(amplitude: float = 0.01) -> PoseSequence:
    base = _static_sequence()
    timestamps = np.arange(150, dtype=np.int64) * 40
    jitter = np.random.default_rng(7).normal(
        0.0, amplitude, size=(timestamps.size, 2)
    )
    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(timestamps.size, dtype=np.int64),
        keypoints_xy={name: values[:1] + jitter for name, values in base.keypoints_xy.items()},
        confidence={
            name: np.full(timestamps.size, 0.9) for name in base.confidence
        },
    )


def _modest_coherent_motion_sequence() -> PoseSequence:
    base = _motion_sequence()
    return PoseSequence(
        timestamp_ms=base.timestamp_ms.copy(),
        source_frames=base.source_frames.copy(),
        keypoints_xy={
            name: values[:1] + 0.15 * (values - values[:1])
            for name, values in base.keypoints_xy.items()
        },
        confidence={name: values.copy() for name, values in base.confidence.items()},
    )


class EventLayerTests(unittest.TestCase):
    def test_pose_kinematic_coverage_is_scoped_to_each_emitted_event(self) -> None:
        valid = np.asarray(
            [False, False, False, True, True, True, True, True, True, True],
            dtype=bool,
        )
        fs01 = _event_kinematic_coverage(valid, 0, 3)
        fs02 = _event_kinematic_coverage(valid, 3, 7)
        self.assertEqual(fs01["coverage_fraction"], 0.25)
        self.assertEqual(fs01["status"], "low")
        self.assertEqual(fs02["coverage_fraction"], 1.0)
        self.assertEqual(fs02["status"], "sufficient")
        self.assertEqual(
            fs02["minimum_required_fraction"], EVENT_KINEMATIC_MIN_COVERAGE
        )
        base = ["event_ground_truth_missing", "provisional_rule_baseline"]
        self.assertIn(
            "pose_kinematic_coverage_low", _event_quality_flags(base, fs01)
        )
        self.assertNotIn(
            "pose_kinematic_coverage_low", _event_quality_flags(base, fs02)
        )

    def test_v04x_event_provenance_contract_rejects_tampering(self) -> None:
        event = detect_pose_events(
            _motion_sequence(), source_id="coverage-contract"
        )[0]
        validate_event_record(event)

        invalid_fraction = copy.deepcopy(event)
        invalid_fraction["provenance"]["event_kinematic_coverage"][
            "coverage_fraction"
        ] = 0.123
        with self.assertRaisesRegex(ValueError, "coverage_fraction is inconsistent"):
            validate_event_record(invalid_fraction)

        invalid_flag = copy.deepcopy(event)
        invalid_flag["quality_flags"].append("pose_kinematic_coverage_low")
        with self.assertRaisesRegex(ValueError, "flag must match"):
            validate_event_record(invalid_flag)

        missing_coverage = copy.deepcopy(event)
        del missing_coverage["provenance"]["event_kinematic_coverage"]
        with self.assertRaisesRegex(ValueError, "requires event_kinematic_coverage"):
            validate_event_record(missing_coverage)

        missing_reference = copy.deepcopy(event)
        del missing_reference["provenance"]["event_motion_reference"]
        with self.assertRaisesRegex(ValueError, "requires event_motion_reference"):
            validate_event_record(missing_reference)

        invalid_reference_counts = copy.deepcopy(event)
        invalid_reference_counts["provenance"]["event_motion_reference"][
            "hip_center_fallback_sample_count"
        ] += 1
        with self.assertRaisesRegex(ValueError, "evidence counts are inconsistent"):
            validate_event_record(invalid_reference_counts)

    def test_static_pose_does_not_manufacture_motion_events(self) -> None:
        events = detect_pose_events(_static_sequence(), source_id="static")
        self.assertEqual(events, [])

    def test_missing_shoulders_use_direct_hip_motion_for_event_candidates(self) -> None:
        sequence = _motion_sequence()
        for name in ("left_shoulder", "right_shoulder"):
            sequence.keypoints_xy[name][:] = np.nan
            sequence.confidence[name][:] = np.nan
        events = detect_pose_events(sequence, source_id="hip-only-motion")
        self.assertEqual({item["event_code"] for item in events}, {"FS01", "FS02", "FS09"})
        for event in events:
            reference = event["provenance"]["event_motion_reference"]
            self.assertEqual(reference["mode"], "direct_hip_center_only")
            self.assertEqual(reference["body_center_sample_count"], 0)
            self.assertGreater(reference["hip_center_fallback_sample_count"], 0)
            self.assertIn("not_keypoint_imputation", reference["semantics"])

    def test_static_hip_only_pose_does_not_manufacture_motion_events(self) -> None:
        sequence = _static_sequence()
        for name in ("left_shoulder", "right_shoulder"):
            sequence.keypoints_xy[name][:] = np.nan
            sequence.confidence[name][:] = np.nan
        self.assertEqual(detect_pose_events(sequence, source_id="static-hip-only"), [])

    def test_aligned_hip_fallback_does_not_create_a_reference_jump(self) -> None:
        sequence = _motion_sequence()
        expected, expected_valid, _, _, _ = _event_motion_reference(sequence)
        missing = np.arange(18, 28)
        for name in ("left_shoulder", "right_shoulder"):
            sequence.keypoints_xy[name][missing] = np.nan
            sequence.confidence[name][missing] = np.nan
        actual, actual_valid, body_source, hip_fallback, diagnostics = (
            _event_motion_reference(sequence)
        )
        self.assertTrue(actual_valid.all())
        self.assertTrue(expected_valid.all())
        self.assertTrue(hip_fallback[missing].all())
        self.assertFalse(body_source[missing].any())
        np.testing.assert_allclose(actual, expected, atol=1e-12)
        self.assertEqual(
            diagnostics["alignment"]["status"], "estimated_from_direct_overlap"
        )

    def test_low_amplitude_common_mode_jitter_is_not_a_motion_event(self) -> None:
        events = detect_pose_events(
            _common_mode_jitter_sequence(), source_id="common-mode-jitter"
        )
        self.assertEqual(events, [])

    def test_noise_guard_preserves_modest_coherent_motion_candidate(self) -> None:
        events = detect_pose_events(
            _modest_coherent_motion_sequence(), source_id="modest-coherent-motion"
        )
        self.assertEqual({item["event_code"] for item in events}, {"FS01", "FS02", "FS09"})
        thresholds = events[0]["provenance"]["adaptive_thresholds"]
        self.assertGreaterEqual(
            thresholds["candidate_persistent_displacement_body"],
            thresholds["minimum_persistent_displacement_body"],
        )
        self.assertGreaterEqual(
            thresholds["candidate_path_efficiency"],
            thresholds["minimum_path_efficiency"],
        )
        self.assertEqual(
            events[0]["provenance"]["threshold_semantics"],
            "event_candidate_segmentation_only_not_A_to_E_scoring",
        )

    def test_rule_baseline_outputs_fs01_fs02_fs09_with_uncertainty(self) -> None:
        events = detect_pose_events(_motion_sequence(), source_id="synthetic")
        self.assertTrue(events)
        self.assertEqual({item["event_code"] for item in events}, {"FS01", "FS02", "FS09"})
        for event in events:
            self.assertEqual(event["person_track_id"], 1)
            self.assertLess(event["start_ms"], event["end_ms"])
            self.assertGreaterEqual(event["boundary_uncertainty_ms"], 40)
            self.assertIn("provisional_rule_baseline", event["quality_flags"])
            self.assertEqual(
                event["provenance"]["threshold_semantics"],
                "event_candidate_segmentation_only_not_A_to_E_scoring",
            )
            coverage = event["provenance"]["event_kinematic_coverage"]
            self.assertEqual(
                coverage["status"] == "low",
                "pose_kinematic_coverage_low" in event["quality_flags"],
            )
            self.assertEqual(
                coverage["scope"], "emitted_event_interval_inclusive"
            )
        fs09 = next(item for item in events if item["event_code"] == "FS09")
        self.assertIsNotNone(fs09["key_phases_ms"]["peak_speed_ms"])
        self.assertLessEqual(
            fs09["start_ms"], fs09["key_phases_ms"]["peak_speed_ms"]
        )
        self.assertLessEqual(
            fs09["key_phases_ms"]["peak_speed_ms"], fs09["end_ms"]
        )

    def test_detector_emits_every_registry_required_phase_key(self) -> None:
        registry = json.loads(
            (ROOT / "metric-feasibility-pose-wave-v2.json").read_text(
                encoding="utf-8"
            )
        )
        events = detect_pose_events(
            _motion_sequence(), source_id="registry-phase-closure"
        )
        events_by_code = {item["event_code"]: item for item in events}
        self.assertEqual(set(events_by_code), {"FS01", "FS02", "FS09"})
        for indicator in registry["indicators"]:
            event_code = indicator["indicator_id"].split("-M", 1)[0]
            phase_keys = events_by_code[event_code]["key_phases_ms"]
            for phase_key in required_phase_keys(indicator):
                self.assertIn(
                    phase_key,
                    phase_keys,
                    f"{indicator['indicator_id']} requires missing {phase_key}",
                )

    def test_event_metrics_compute_f1_iou_and_boundary_mae(self) -> None:
        truth = [
            {
                "event_id": "truth-1",
                "video_id": "synthetic-video",
                "event_code": "FS02",
                "person_track_id": 1,
                "start_ms": 1000,
                "end_ms": 2000,
                "key_phases_ms": {
                    "direction_conversion_ms": 1200,
                    "peak_speed_ms": 1700,
                    "unobserved_truth_phase_ms": 1800,
                },
            }
        ]
        prediction = [
            {
                "event_id": "pred-1",
                "video_id": "synthetic-video",
                "event_code": "FS02",
                "person_track_id": 1,
                "start_ms": 1100,
                "end_ms": 1900,
                "key_phases_ms": {
                    "direction_conversion_ms": 1250,
                    "peak_speed_ms": 1600,
                    "unobserved_truth_phase_ms": None,
                },
            }
        ]
        result = evaluate_events(prediction, truth)
        self.assertEqual(result["event_f1"], 1.0)
        self.assertEqual(result["mean_segment_iou"], 0.8)
        self.assertEqual(result["boundary_mae_ms"], 100.0)
        self.assertEqual(result["boundary_p95_ms"], 100.0)
        self.assertEqual(result["phase_boundary_mae_ms"], 75.0)
        self.assertEqual(result["phase_boundary_p95_ms"], 97.5)
        self.assertEqual(result["phase_boundary_valid_rate"], 0.66666667)
        self.assertEqual(
            result["phase_boundary_by_name"]["direction_conversion_ms"],
            {
                "eligible_truth_count": 1,
                "predicted_count": 1,
                "missing_prediction_count": 0,
                "valid_rate": 1.0,
                "mae_ms": 50.0,
                "p95_ms": 50.0,
            },
        )
        self.assertEqual(
            result["phase_boundary_by_name"]["unobserved_truth_phase_ms"],
            {
                "eligible_truth_count": 1,
                "predicted_count": 0,
                "missing_prediction_count": 1,
                "valid_rate": 0.0,
                "mae_ms": None,
                "p95_ms": None,
            },
        )
        self.assertEqual(
            result["matches"][0]["phase_errors_ms"],
            {"direction_conversion_ms": 50, "peak_speed_ms": -100},
        )

    def test_no_ground_truth_is_not_mislabeled_as_perfect(self) -> None:
        result = evaluate_events(
            [
                {
                    "event_id": "unverified-candidate",
                    "event_code": "FS01",
                    "person_track_id": 1,
                    "start_ms": 0,
                    "end_ms": 100,
                }
            ],
            [],
        )
        self.assertEqual(result["status"], "ground_truth_required")
        self.assertIsNone(result["event_f1"])
        self.assertIsNone(result["precision"])
        self.assertIsNone(result["recall"])
        self.assertIsNone(result["false_positive"])
        self.assertEqual(result["unverified_prediction_count"], 1)
        self.assertIsNone(result["boundary_mae_ms"])
        self.assertIsNone(result["phase_boundary_mae_ms"])
        self.assertIsNone(result["phase_boundary_valid_rate"])
        self.assertEqual(result["phase_boundary_by_name"], {})

    def test_global_matcher_prioritises_match_count_over_one_high_iou_pair(self) -> None:
        def event(event_id: str, start_ms: int, end_ms: int) -> dict:
            return {
                "event_id": event_id,
                "video_id": "video-a",
                "event_code": "FS01",
                "person_track_id": 1,
                "start_ms": start_ms,
                "end_ms": end_ms,
            }

        # Greedy descending IoU first takes pred-1 -> truth-1 (0.714...),
        # leaving only one match.  The global cardinality optimum contains two.
        predictions = [event("pred-1", 0, 140), event("pred-2", 0, 60)]
        truth = [event("truth-1", 0, 100), event("truth-2", 60, 160)]
        result = evaluate_events(predictions, truth)
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["event_f1"], 1.0)
        self.assertEqual(
            [
                (row["prediction_event_id"], row["ground_truth_event_id"])
                for row in result["matches"]
            ],
            [("pred-1", "truth-2"), ("pred-2", "truth-1")],
        )
        self.assertEqual(
            result["matching"]["objective_priority"],
            ["maximum_match_count", "maximum_total_segment_iou"],
        )

    def test_matching_is_stable_when_equal_iou_inputs_are_reordered(self) -> None:
        def event(event_id: str) -> dict:
            return {
                "event_id": event_id,
                "video_id": "video-a",
                "event_code": "FS09",
                "person_track_id": 1,
                "start_ms": 0,
                "end_ms": 100,
            }

        first = evaluate_events(
            [event("pred-1"), event("pred-2")],
            [event("truth-1"), event("truth-2")],
        )
        reordered = evaluate_events(
            [event("pred-2"), event("pred-1")],
            [event("truth-2"), event("truth-1")],
        )
        expected = [("pred-1", "truth-1"), ("pred-2", "truth-2")]
        for result in (first, reordered):
            self.assertEqual(
                [
                    (row["prediction_event_id"], row["ground_truth_event_id"])
                    for row in result["matches"]
                ],
                expected,
            )

    def test_global_matcher_maximises_total_iou_after_cardinality(self) -> None:
        def event(event_id: str, start_ms: int, end_ms: int) -> dict:
            return {
                "event_id": event_id,
                "video_id": "video-a",
                "event_code": "FS01",
                "person_track_id": 1,
                "start_ms": start_ms,
                "end_ms": end_ms,
            }

        result = evaluate_events(
            [event("pred-1", 0, 100), event("pred-2", 30, 130)],
            [event("truth-1", 0, 100), event("truth-2", 50, 150)],
            match_iou_min=0.3,
        )
        self.assertEqual(result["matched"], 2)
        self.assertEqual(
            [
                (row["prediction_event_id"], row["ground_truth_event_id"])
                for row in result["matches"]
            ],
            [("pred-1", "truth-1"), ("pred-2", "truth-2")],
        )
        self.assertEqual(result["mean_segment_iou"], 0.83333334)

    def test_matching_never_crosses_video_event_code_or_track(self) -> None:
        prediction = {
            "event_id": "prediction",
            "video_id": "video-a",
            "event_code": "FS01",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 100,
        }
        truth = []
        for event_id, video_id, event_code, track_id in (
            ("wrong-video", "video-b", "FS01", 1),
            ("wrong-code", "video-a", "FS02", 1),
            ("wrong-track", "video-a", "FS01", 2),
        ):
            truth.append(
                {
                    "event_id": event_id,
                    "video_id": video_id,
                    "event_code": event_code,
                    "person_track_id": track_id,
                    "start_ms": 0,
                    "end_ms": 100,
                }
            )
        result = evaluate_events([prediction], truth)
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["false_positive"], 1)
        self.assertEqual(result["false_negative"], 3)
        self.assertEqual(result["matches"], [])

    def test_provenance_source_id_resolves_prediction_video_identity(self) -> None:
        prediction = {
            "event_id": "prediction",
            "event_code": "FS02",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 100,
            "provenance": {"source_id": "video-a"},
        }
        truth = {
            **prediction,
            "event_id": "truth",
            "video_id": "video-a",
            "provenance": {},
        }
        result = evaluate_events([prediction], [truth])
        self.assertEqual(result["matched"], 1)

    def test_top_level_video_id_joins_truth_when_run_source_id_is_qualified(self) -> None:
        prediction = {
            "event_id": "prediction",
            "video_id": "video-a",
            "event_code": "FS02",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 100,
            "provenance": {"source_id": "video-a:wholebody133:window-31-51"},
        }
        truth = {
            **prediction,
            "event_id": "truth",
            "provenance": {},
        }
        result = evaluate_events([prediction], [truth])
        self.assertEqual(result["matched"], 1)

    def test_unscoped_prediction_is_rejected_from_truth_evaluation(self) -> None:
        prediction = {
            "event_id": "prediction",
            "event_code": "FS02",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 100,
        }
        truth = {**prediction, "event_id": "truth", "video_id": "video-a"}
        with self.assertRaisesRegex(ValueError, "requires video_id"):
            evaluate_events([prediction], [truth])


if __name__ == "__main__":
    unittest.main()
