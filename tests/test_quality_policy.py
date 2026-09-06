from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rallymate_scoring.loop import run_minimum_scoring_loop
from rallymate_scoring.quality_policy import (
    QUALITY_POLICY_VERSION,
    evaluate_indicator_event_quality,
    indicator_event_quality_flags,
)
from rallymate_scoring.scoring import score_indicator


ROOT = Path(__file__).resolve().parents[1]


def _valid_feature() -> dict:
    return {
        "feature_name": "hip_center_y_body",
        "feature_version": "1.0.0",
        "value": 0.72,
        "unit": "body",
        "valid": True,
        "confidence": 0.91,
        "reason": "ok",
        "source_frames": [1, 2, 3],
    }


def _point(index: int, name: str, x: float, y: float) -> dict:
    return {
        "index": index,
        "name": name,
        "x_normalized": x,
        "y_normalized": y,
        "confidence": 0.95,
    }


def _write_pose_fixture(frames: Path, timeline: Path) -> None:
    names = [
        "nose", "left_eye", "right_eye", "left_ear", "right_ear",
        "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
        "left_wrist", "right_wrist", "left_hip", "right_hip",
        "left_knee", "right_knee", "left_ankle", "right_ankle",
    ]
    frame_records = []
    timeline_records = []
    for index in range(16):
        shift = 0.02 * min(index, 8)
        coords = [(0.5, 0.15)] * 17
        coords[5:7] = [(0.43 + shift, 0.35), (0.57 + shift, 0.35)]
        coords[11:17] = [
            (0.45 + shift, 0.58), (0.55 + shift, 0.58),
            (0.44 + shift, 0.75), (0.56 + shift, 0.75),
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
                "keypoint_valid_fraction": 1.0,
            }
        )
    frames.write_text(
        "".join(json.dumps(item) + "\n" for item in frame_records), encoding="utf-8"
    )
    timeline.write_text(
        "".join(json.dumps(item) + "\n" for item in timeline_records),
        encoding="utf-8",
    )


class IndicatorEventQualityPolicyTests(unittest.TestCase):
    def test_phase_peak_fallback_preserves_measurement_but_blocks_only_dependent_indicator(self) -> None:
        landing_flag = "phase_proxy_right_censored_peak:landing_proxy_ms"
        dependent = evaluate_indicator_event_quality("FS01-M04", [landing_flag])
        self.assertTrue(dependent["measurement_allowed"])
        self.assertFalse(dependent["scoring_allowed"])
        self.assertEqual(dependent["scoring_block_flags"], [landing_flag])

        sibling = evaluate_indicator_event_quality("FS01-M02", [landing_flag])
        self.assertTrue(sibling["measurement_allowed"])
        self.assertTrue(sibling["scoring_allowed"])
        self.assertEqual(sibling["scoring_block_flags"], [])

        slowdown_flag = (
            "phase_proxy_low_sample_peak:first_step_slowdown_proxy_ms"
        )
        dependent = evaluate_indicator_event_quality("FS02-M05", [slowdown_flag])
        self.assertTrue(dependent["measurement_allowed"])
        self.assertFalse(dependent["scoring_allowed"])
        self.assertEqual(dependent["scoring_block_flags"], [slowdown_flag])
        sibling = evaluate_indicator_event_quality("FS02-M02", [slowdown_flag])
        self.assertTrue(sibling["scoring_allowed"])

        ambiguous_side = "lead_foot_side_proxy_ambiguous"
        dependent = evaluate_indicator_event_quality("FS02-M03", [ambiguous_side])
        self.assertTrue(dependent["measurement_allowed"])
        self.assertFalse(dependent["scoring_allowed"])
        sibling = evaluate_indicator_event_quality("FS02-M02", [ambiguous_side])
        self.assertTrue(sibling["scoring_allowed"])

        landing_score = score_indicator(
            indicator_id="FS01-M04",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=[landing_flag],
        )
        self.assertIn(
            "event_phase_proxy_right_censored_unverified",
            landing_score["reason_codes"],
        )
        self.assertNotIn(
            "event_scoring_context_unverified", landing_score["reason_codes"]
        )
        self.assertIn("右边界", landing_score["feedback"])

        slowdown_score = score_indicator(
            indicator_id="FS02-M05",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=[slowdown_flag],
        )
        self.assertIn(
            "event_phase_proxy_low_sample_unverified",
            slowdown_score["reason_codes"],
        )
        self.assertIn("两样本", slowdown_score["feedback"])

        ambiguous_score = score_indicator(
            indicator_id="FS02-M03",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=[
                "keypoint_jump_candidates_present",
                ambiguous_side,
            ],
        )
        self.assertIn(
            "lead_foot_side_assignment_unverified",
            ambiguous_score["reason_codes"],
        )
        self.assertIn(
            "keypoint_jump_diagnostic_unverified",
            ambiguous_score["reason_codes"],
        )
        self.assertNotIn(
            "event_identity_continuity_unverified",
            ambiguous_score["reason_codes"],
        )
        self.assertIn("启动脚侧别", ambiguous_score["feedback"])

    def test_malformed_phase_fallback_flag_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, r"non-empty \*_ms"):
            evaluate_indicator_event_quality(
                "FS01-M04", ["phase_proxy_right_censored_peak:landing_proxy"]
            )

    def test_truth_and_provisional_flags_do_not_block_F2_measurement(self) -> None:
        result = evaluate_indicator_event_quality(
            "FS01-M02",
            ["event_ground_truth_missing", "provisional_rule_baseline"],
        )
        self.assertEqual(result["policy_version"], QUALITY_POLICY_VERSION)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["measurement_allowed"])
        self.assertEqual(
            result["non_blocking_flags"],
            ["event_ground_truth_missing", "provisional_rule_baseline"],
        )

    def test_event_boundary_coverage_preserves_measurement_but_blocks_grade(self) -> None:
        result = evaluate_indicator_event_quality(
            "FS09-M03", ["pose_kinematic_coverage_low"]
        )
        self.assertFalse(result["hard_fail"])
        self.assertTrue(result["measurement_allowed"])
        self.assertFalse(result["scoring_allowed"])
        self.assertEqual(
            result["scoring_block_flags"], ["pose_kinematic_coverage_low"]
        )

        scored_contract = score_indicator(
            indicator_id="FS09-M03",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=["pose_kinematic_coverage_low"],
        )
        self.assertEqual(scored_contract["status"], "unavailable")
        self.assertIsNone(scored_contract["grade"])
        self.assertIn(
            "event_boundary_evidence_low", scored_contract["reason_codes"]
        )
        self.assertNotIn(
            "event_identity_continuity_unverified",
            scored_contract["reason_codes"],
        )
        self.assertIn("事件边界", scored_contract["feedback"])

    def test_primary_track_low_coverage_and_confirmed_id_switch_are_hard_failures(self) -> None:
        for flag in (
            "primary_track_coverage_low",
            "confirmed_id_switch_present",
        ):
            with self.subTest(flag=flag):
                result = evaluate_indicator_event_quality("FS09-M03", [flag])
                self.assertTrue(result["hard_fail"])
                self.assertFalse(result["measurement_allowed"])
                self.assertEqual(result["hard_fail_flags"], [flag])

    def test_primary_pose_low_coverage_preserves_valid_F2_measurement_but_blocks_grade(self) -> None:
        flag = "primary_pose_coverage_low"
        gate = evaluate_indicator_event_quality("FS01-M02", [flag])
        self.assertFalse(gate["hard_fail"])
        self.assertTrue(gate["measurement_allowed"])
        self.assertFalse(gate["scoring_allowed"])
        self.assertEqual(gate["scoring_block_flags"], [flag])

        result = score_indicator(
            indicator_id="FS01-M02",
            features=[
                {
                    "feature_name": "hip_center_y_body",
                    "feature_version": "1.0.0",
                    "value": 0.25,
                    "unit": "body",
                    "confidence": 0.8,
                    "valid": True,
                    "reason": "valid",
                    "source_frames": [3, 4, 5],
                }
            ],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=[flag],
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["grade"])
        self.assertTrue(result["quality_gate"]["measurement_allowed"])
        self.assertIn("primary_pose_observation_coverage_low", result["reason_codes"])
        self.assertIn(flag, result["reason_codes"])

    def test_restabilization_is_indicator_specific(self) -> None:
        for indicator_id in ("FS09-M04", "FS09-M05"):
            with self.subTest(indicator_id=indicator_id):
                result = evaluate_indicator_event_quality(
                    indicator_id, ["restabilization_not_observed"]
                )
                self.assertTrue(result["hard_fail"])
        other = evaluate_indicator_event_quality(
            "FS09-M03", ["restabilization_not_observed"]
        )
        self.assertFalse(other["hard_fail"])
        self.assertEqual(
            other["advisory_flags"], ["restabilization_not_observed"]
        )

    def test_required_phase_missing_is_an_indicator_measurement_hard_fail(self) -> None:
        indicator = {
            "indicator_id": "FS01-M04",
            "required_events": ["FS01.landing_proxy"],
        }
        missing_event = {
            "quality_flags": ["event_ground_truth_missing"],
            "key_phases_ms": {"landing_proxy_ms": None},
        }
        flags = indicator_event_quality_flags(missing_event, indicator)
        self.assertIn("required_phase_missing:landing_proxy_ms", flags)
        result = evaluate_indicator_event_quality("FS01-M04", flags)
        self.assertTrue(result["hard_fail"])
        self.assertFalse(result["measurement_allowed"])
        self.assertFalse(result["scoring_allowed"])
        self.assertIn(
            "required_phase_missing:landing_proxy_ms",
            result["hard_fail_flags"],
        )

        observed_event = {
            "quality_flags": ["event_ground_truth_missing"],
            "key_phases_ms": {"landing_proxy_ms": 1240},
        }
        observed_flags = indicator_event_quality_flags(observed_event, indicator)
        self.assertNotIn("required_phase_missing:landing_proxy_ms", observed_flags)
        observed = evaluate_indicator_event_quality("FS01-M04", observed_flags)
        self.assertTrue(observed["measurement_allowed"])

    def test_malformed_required_phase_flag_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must name a non-empty"):
            evaluate_indicator_event_quality(
                "FS01-M04", ["required_phase_missing:landing_proxy"]
            )

    def test_candidate_diagnostics_are_advisory_and_traceable(self) -> None:
        flags = [
            "source_track_switch_candidates_present",
            "left_right_swap_candidates_present",
            "keypoint_jump_candidates_present",
        ]
        result = evaluate_indicator_event_quality("FS01-M05", flags)
        self.assertEqual(result["status"], "advisory")
        self.assertTrue(result["measurement_allowed"])
        self.assertFalse(result["scoring_allowed"])
        self.assertEqual(
            result["scoring_block_flags"],
            [
                "keypoint_jump_candidates_present",
                "source_track_switch_candidates_present",
            ],
        )
        self.assertEqual(result["advisory_flags"], sorted(flags))
        self.assertEqual(result["input_quality_flags"], sorted(flags))

    def test_unconfirmed_source_track_switch_keeps_measurement_but_blocks_grade(self) -> None:
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=["source_track_switch_candidates_present"],
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["grade"])
        self.assertTrue(result["quality_gate"]["measurement_allowed"])
        self.assertFalse(result["quality_gate"]["scoring_allowed"])
        self.assertIn(
            "event_identity_continuity_unverified", result["reason_codes"]
        )

    def test_ambiguous_primary_identity_keeps_measurement_but_blocks_grade(self) -> None:
        gate = evaluate_indicator_event_quality(
            "FS01-M02", ["primary_identity_ambiguous"]
        )
        self.assertTrue(gate["measurement_allowed"])
        self.assertFalse(gate["scoring_allowed"])
        self.assertEqual(
            gate["scoring_block_flags"], ["primary_identity_ambiguous"]
        )

    def test_fs02_target_direction_context_blocks_only_its_formal_grade(self) -> None:
        missing_target = "tactical_target_direction_not_observed"
        fs02 = evaluate_indicator_event_quality("FS02-M02", [missing_target])
        other = evaluate_indicator_event_quality("FS02-M03", [missing_target])
        self.assertTrue(fs02["measurement_allowed"])
        self.assertFalse(fs02["scoring_allowed"])
        self.assertEqual(fs02["scoring_block_flags"], [missing_target])
        self.assertTrue(other["measurement_allowed"])
        self.assertTrue(other["scoring_allowed"])

        scored_contract = score_indicator(
            indicator_id="FS02-M02",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=[missing_target],
        )
        self.assertEqual(scored_contract["status"], "unavailable")
        self.assertIn(
            "tactical_target_direction_required",
            scored_contract["reason_codes"],
        )
        self.assertNotIn(
            "event_identity_continuity_unverified",
            scored_contract["reason_codes"],
        )
        self.assertIn("目标方向", scored_contract["feedback"])

    def test_pose_diagnostic_blocks_affected_grades_but_not_F2_measurement(self) -> None:
        knee = evaluate_indicator_event_quality(
            "FS01-M02", ["left_right_swap_candidates_present"]
        )
        center_only = evaluate_indicator_event_quality(
            "FS09-M01", ["left_right_swap_candidates_present"]
        )
        jump = evaluate_indicator_event_quality(
            "FS09-M01", ["keypoint_jump_candidates_present"]
        )
        self.assertTrue(knee["measurement_allowed"])
        self.assertFalse(knee["scoring_allowed"])
        self.assertTrue(center_only["scoring_allowed"])
        self.assertTrue(jump["measurement_allowed"])
        self.assertFalse(jump["scoring_allowed"])

        jump_score = score_indicator(
            indicator_id="FS09-M01",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=["keypoint_jump_candidates_present"],
        )
        self.assertIn(
            "keypoint_jump_diagnostic_unverified", jump_score["reason_codes"]
        )
        self.assertNotIn(
            "event_identity_continuity_unverified", jump_score["reason_codes"]
        )

    def test_joint_scoped_jump_only_blocks_features_that_use_the_joint(self) -> None:
        indicator = {
            "indicator_id": "FS09-M01",
            "required_events": ["FS09"],
            "required_features": ["hip_center_speed_body_s"],
        }
        irrelevant = {
            "quality_flags": ["keypoint_jump_candidates_present"],
            "key_phases_ms": {},
            "track_diagnostics": {
                "keypoint_jump_candidate_joints": ["left_wrist"],
            },
        }
        irrelevant_flags = indicator_event_quality_flags(irrelevant, indicator)
        self.assertNotIn("keypoint_jump_candidates_present", irrelevant_flags)
        self.assertIn(
            "keypoint_jump_candidates_outside_indicator_joints",
            irrelevant_flags,
        )
        irrelevant_gate = evaluate_indicator_event_quality(
            indicator["indicator_id"], irrelevant_flags
        )
        self.assertTrue(irrelevant_gate["scoring_allowed"])

        relevant = copy.deepcopy(irrelevant)
        relevant["track_diagnostics"]["keypoint_jump_candidate_joints"] = [
            "left_hip"
        ]
        relevant_flags = indicator_event_quality_flags(relevant, indicator)
        self.assertIn("keypoint_jump_candidates_present", relevant_flags)
        self.assertFalse(
            evaluate_indicator_event_quality(
                indicator["indicator_id"], relevant_flags
            )["scoring_allowed"]
        )

        legacy = {**irrelevant, "track_diagnostics": {}}
        legacy_flags = indicator_event_quality_flags(legacy, indicator)
        self.assertIn("keypoint_jump_candidates_present", legacy_flags)

    def test_joint_scoped_swap_only_blocks_features_that_use_the_pair(self) -> None:
        indicator = {
            "indicator_id": "FS01-M02",
            "required_events": ["FS01.preload"],
            "required_features": ["hip_center_y_body"],
        }
        event = {
            "quality_flags": ["left_right_swap_candidates_present"],
            "key_phases_ms": {"preload_ms": 100},
            "track_diagnostics": {
                "left_right_swap_candidate_joint_pairs": [
                    {
                        "left_joint": "left_wrist",
                        "right_joint": "right_wrist",
                        "frames": [7],
                    }
                ]
            },
        }
        irrelevant_flags = indicator_event_quality_flags(event, indicator)
        self.assertNotIn("left_right_swap_candidates_present", irrelevant_flags)
        self.assertIn(
            "left_right_swap_candidates_outside_indicator_joints",
            irrelevant_flags,
        )
        self.assertTrue(
            evaluate_indicator_event_quality(
                indicator["indicator_id"], irrelevant_flags
            )["scoring_allowed"]
        )

        relevant = copy.deepcopy(event)
        relevant["track_diagnostics"][
            "left_right_swap_candidate_joint_pairs"
        ][0].update(left_joint="left_hip", right_joint="right_hip")
        relevant_flags = indicator_event_quality_flags(relevant, indicator)
        self.assertIn("left_right_swap_candidates_present", relevant_flags)
        self.assertFalse(
            evaluate_indicator_event_quality(
                indicator["indicator_id"], relevant_flags
            )["scoring_allowed"]
        )

    def test_unclassified_flag_is_preserved_as_non_blocking_advisory(self) -> None:
        result = evaluate_indicator_event_quality(
            "FS02-M02", ["future_pose_diagnostic"]
        )
        self.assertFalse(result["hard_fail"])
        self.assertEqual(result["unclassified_flags"], ["future_pose_diagnostic"])
        self.assertEqual(result["advisory_flags"], ["future_pose_diagnostic"])

    def test_scoring_consumes_quality_without_inventing_grade_thresholds(self) -> None:
        non_blocking = score_indicator(
            indicator_id="FS01-M02",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=[
                "event_ground_truth_missing",
                "provisional_rule_baseline",
            ],
        )
        self.assertEqual(non_blocking["status"], "calibration_required")
        self.assertIsNone(non_blocking["grade"])
        self.assertIsNone(non_blocking["threshold_version"])
        self.assertTrue(non_blocking["quality_gate"]["measurement_allowed"])

        hard_fail = score_indicator(
            indicator_id="FS01-M02",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=["primary_track_coverage_low"],
        )
        self.assertEqual(hard_fail["status"], "unavailable")
        self.assertEqual(hard_fail["confidence"], 0.0)
        self.assertIsNone(hard_fail["grade"])
        self.assertIn("event_quality_hard_fail", hard_fail["reason_codes"])

    def test_invalid_feature_preserves_all_concurrent_score_only_blocks(self) -> None:
        invalid_feature = _valid_feature()
        invalid_feature.update(valid=False, value=None, reason="joint_missing")
        result = score_indicator(
            indicator_id="FS02-M02",
            features=[invalid_feature],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=[
                "keypoint_jump_candidates_present",
                "tactical_target_direction_not_observed",
            ],
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("required_feature_unavailable", result["reason_codes"])
        self.assertIn("event_scoring_quality_blocked", result["reason_codes"])
        self.assertIn(
            "keypoint_jump_diagnostic_unverified", result["reason_codes"]
        )
        self.assertIn(
            "tactical_target_direction_required", result["reason_codes"]
        )
        self.assertNotIn(
            "event_identity_continuity_unverified", result["reason_codes"]
        )
        self.assertIn("关键点跳变", result["feedback"])
        self.assertIn("目标方向", result["feedback"])

    def test_hard_failure_preserves_concurrent_identity_and_swap_blocks(self) -> None:
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_valid_feature()],
            calibration=None,
            model_versions={"feature": "test"},
            evidence=[],
            feasibility_level="F2",
            event_quality_flags=[
                "primary_track_coverage_low",
                "source_track_switch_candidates_present",
                "left_right_swap_candidates_present",
            ],
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("event_quality_hard_fail", result["reason_codes"])
        self.assertIn("primary_track_coverage_low", result["reason_codes"])
        self.assertIn(
            "event_identity_continuity_unverified", result["reason_codes"]
        )
        self.assertIn("left_right_assignment_unverified", result["reason_codes"])
        self.assertIn("主球员身份连续性", result["feedback"])
        self.assertIn("左右关键点", result["feedback"])

    def test_loop_marks_hard_failed_measurements_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            frames = output / "frames.jsonl"
            timeline = output / "primary-player.jsonl"
            _write_pose_fixture(frames, timeline)
            diagnostics = {
                "quality_flags": ["primary_track_coverage_low"],
                "track_coverage_fraction": 0.5,
            }
            with patch(
                "rallymate_scoring.loop.diagnose_primary_timeline",
                return_value=diagnostics,
            ):
                result = run_minimum_scoring_loop(
                    frames_path=frames,
                    primary_timeline_path=timeline,
                    output_dir=output,
                    feasibility_registry_path=ROOT / "metric-feasibility.json",
                    source_id="quality-test-video",
                    pose_model={"backend": "test", "runtime": "cpu", "profile": "unit"},
                )
            self.assertTrue(result["scores"])
            self.assertTrue(
                all(item["status"] == "unavailable" for item in result["scores"])
            )
            self.assertTrue(
                all(
                    item["quality_gate"]["hard_fail"]
                    for item in result["indicator_records"]
                )
            )
            self.assertTrue(
                all(
                    item["feature_status"] == "unavailable"
                    for item in result["indicator_records"]
                )
            )
            self.assertEqual(
                result["summary"]["result_state"]["measurement_status"],
                "unavailable",
            )
            self.assertFalse(any(item["grade"] for item in result["scores"]))

    def test_loop_keeps_complete_features_under_sparse_primary_pose_as_F2_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            frames = output / "frames.jsonl"
            timeline = output / "primary-player.jsonl"
            _write_pose_fixture(frames, timeline)
            diagnostics = {
                "quality_flags": ["primary_pose_coverage_low"],
                "pose_coverage_fraction": 0.5,
            }
            with patch(
                "rallymate_scoring.loop.diagnose_primary_timeline",
                return_value=diagnostics,
            ):
                result = run_minimum_scoring_loop(
                    frames_path=frames,
                    primary_timeline_path=timeline,
                    output_dir=output,
                    feasibility_registry_path=ROOT / "metric-feasibility.json",
                    source_id="sparse-pose-quality-test-video",
                    pose_model={"backend": "test", "runtime": "cpu", "profile": "unit"},
                )
            measured = [
                item
                for item in result["indicator_records"]
                if item["feature_status"] == "measured"
            ]
            self.assertTrue(measured)
            self.assertTrue(
                all(item["quality_gate"]["measurement_allowed"] for item in measured)
            )
            self.assertTrue(
                all(not item["quality_gate"]["scoring_allowed"] for item in measured)
            )
            self.assertTrue(
                all(item["status"] == "unavailable" for item in result["scores"])
            )
            self.assertTrue(
                all(
                    "primary_pose_observation_coverage_low" in item["reason_codes"]
                    for item in result["scores"]
                )
            )
            self.assertEqual(
                result["summary"]["result_state"]["measurement_status"],
                "measured",
            )
            self.assertEqual(
                result["summary"]["result_state"]["scoring_status"],
                "unavailable",
            )
            self.assertFalse(any(item["grade"] for item in result["scores"]))


if __name__ == "__main__":
    unittest.main()
