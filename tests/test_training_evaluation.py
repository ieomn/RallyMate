from __future__ import annotations

import json
import unittest

from rallymate_service.training_evaluation import build_training_evaluation


class TrainingEvaluationTests(unittest.TestCase):
    @staticmethod
    def _record(event_id: str, *, jump_warning: bool = False) -> dict:
        flags = ["pose_keypoint_jump_unverified"] if jump_warning else []
        return {
            "video_id": "test-video",
            "person_track_id": 1,
            "indicator_id": "FS01-M03",
            "event_code": "FS01",
            "event_id": event_id,
            "feature_status": "measured",
            "features": [
                {
                    "feature_name": "bilateral_foot_rise_min_body",
                    "value": 0.12,
                    "unit": "body",
                    "valid": True,
                    "confidence": 0.8,
                },
                {
                    "feature_name": "bilateral_foot_rise_synchrony_ms",
                    "value": 80.0,
                    "unit": "ms",
                    "valid": True,
                    "confidence": 0.8,
                },
                {
                    "feature_name": "bilateral_foot_rise_proxy_duration_ms",
                    "value": 140.0,
                    "unit": "ms",
                    "valid": True,
                    "confidence": 0.8,
                },
                {
                    "feature_name": "hip_center_vertical_velocity_body_s",
                    "value": 1.1,
                    "unit": "body/s",
                    "valid": True,
                    "confidence": 0.8,
                },
            ],
            "quality_gate": {
                "measurement_allowed": True,
                "scoring_allowed": True,
                "advisory_flags": flags,
            },
        }

    def test_builds_thirteen_evidence_reference_scores_without_technical_grade(self) -> None:
        result = build_training_evaluation(
            [self._record("fs01-001"), self._record("fs01-002")]
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["score_0_to_100"], 97)
        self.assertEqual(result["evaluated_indicator_count"], 1)
        self.assertEqual(result["total_indicator_count"], 13)
        self.assertEqual(len(result["indicator_evaluations"]), 13)
        evaluated = result["indicator_evaluations"][1]
        self.assertEqual(evaluated["indicator_id"], "FS01-M03")
        self.assertEqual(evaluated["name_zh"], "双脚轻微离地")
        self.assertIn("人工复核", evaluated["suggestion_zh"])
        self.assertEqual(result["label_zh"], "测量证据参考分（Beta）")
        self.assertEqual(result["score_semantics"], "measurement_evidence_quality")
        self.assertEqual(evaluated["score_semantics"], result["score_semantics"])
        self.assertEqual(result["action_evaluations"]["FS01"]["score_semantics"], result["score_semantics"])
        self.assertEqual(evaluated["technical_score_status"], "calibration_required")
        self.assertIsNone(evaluated["technical_score_0_to_100"])
        self.assertIsNone(result["technical_grade"])
        self.assertEqual(
            evaluated["components"],
            {
                "measured_instance_ratio_percent": 100,
                "required_feature_coverage_percent": 100,
                "median_feature_confidence_percent": 80,
                "repeatability_percent": 100,
                "scoring_evidence_ratio_percent": 100,
            },
        )
        self.assertEqual(result["action_evaluations"]["FS01"]["score_0_to_100"], 97)
        self.assertEqual(evaluated["score_0_to_100"], 97)
        self.assertEqual(evaluated["effective_component_weights"], evaluated["component_weights"])
        self.assertIsNone(result["action_evaluations"]["FS02"]["score_0_to_100"])
        self.assertIsNone(result["formal_grade"])
        self.assertFalse(result["is_formal_coach_score"])

    def test_single_sample_has_no_invented_repeatability_or_technical_praise(self) -> None:
        result = build_training_evaluation([self._record("fs01-001")])
        item = result["indicator_evaluations"][1]

        self.assertIsNone(item["components"]["repeatability_percent"])
        self.assertEqual(item["repeatability_status"], "insufficient_samples")
        self.assertEqual(item["repeatability_sample_count"], 1)
        self.assertNotIn("repeatability", item["effective_component_weights"])
        self.assertAlmostEqual(sum(item["effective_component_weights"].values()), 1.0)
        self.assertAlmostEqual(item["effective_component_weights"]["measured_instance_ratio"], 0.4)
        self.assertEqual(item["score_0_to_100"], 96)
        self.assertIn("独立可测片段不足", item["observation_zh"])
        self.assertNotIn("可继续保持", item["suggestion_zh"])
        self.assertNotIn("表现较稳定", json.dumps(result, ensure_ascii=False))

    def test_repeated_extreme_measurements_do_not_generate_coaching_approval(self) -> None:
        records = [self._record("fs01-001"), self._record("fs01-002")]
        for record in records:
            record["features"][1]["value"] = 5000.0
        result = build_training_evaluation(records)
        item = result["indicator_evaluations"][1]

        self.assertEqual(item["score_0_to_100"], 97)
        self.assertEqual(item["components"]["repeatability_percent"], 100)
        self.assertIn("不代表技术正确性", item["observation_zh"])
        self.assertEqual(item["technical_score_status"], "calibration_required")
        self.assertIsNone(item["technical_score_0_to_100"])
        self.assertNotIn("双脚出现同步", item["suggestion_zh"])

    def test_unmeasured_record_cannot_add_scoring_evidence(self) -> None:
        invalid = self._record("fs01-002")
        invalid["feature_status"] = "unavailable"
        result = build_training_evaluation([self._record("fs01-001"), invalid])
        item = result["indicator_evaluations"][1]

        self.assertEqual(item["components"]["scoring_evidence_ratio_percent"], 50)
        self.assertIsNone(item["components"]["repeatability_percent"])

    def test_missing_features_cannot_claim_complete_scoring_evidence(self) -> None:
        record = self._record("fs01-001")
        record["features"].pop()
        result = build_training_evaluation([record])
        item = result["indicator_evaluations"][1]
        self.assertEqual(item["components"]["scoring_evidence_ratio_percent"], 0)

    def test_direction_summary_respects_wraparound_and_ambiguous_opposites(self) -> None:
        for directions, expected in [([179.0, -179.0], 180.0), ([0.0, 180.0], None)]:
            records = [
                {
                    "indicator_id": "FS02-M02",
                    "event_code": "FS02",
                    "video_id": "test-video",
                    "person_track_id": 1,
                    "event_id": f"fs02-{index}",
                    "feature_status": "measured",
                    "features": [{
                        "feature_name": "launch_direction_deg",
                        "value": direction,
                        "unit": "deg",
                        "valid": True,
                        "confidence": 0.9,
                    }],
                }
                for index, direction in enumerate(directions)
            ]
            item = next(
                value for value in build_training_evaluation(records)["indicator_evaluations"]
                if value["indicator_id"] == "FS02-M02"
            )
            measurements = item["representative_measurements"]
            if expected is None:
                self.assertEqual(measurements, [])
            else:
                self.assertAlmostEqual(abs(measurements[0]["median_value"]), expected)
                self.assertEqual(measurements[0]["aggregation"], "circular_mean")
                self.assertNotIn("typical_range", measurements[0])

    def test_independent_target_direction_context_is_included_when_present(self) -> None:
        features = [
            {"feature_name": name, "value": value, "unit": unit, "valid": True, "confidence": 0.9}
            for name, value, unit in [
                ("body_center_speed_body_s", 1.0, "body/s"),
                ("hip_center_relative_to_ankle_support", 0.1, "body"),
                ("torso_lean_deg", 5.0, "deg"),
                ("launch_direction_deg", 20.0, "deg"),
            ]
        ]
        alignment = {"feature_name": "target_direction_alignment_error_deg", "value": 12.0, "unit": "deg", "valid": True, "confidence": 0.9}
        record = {
            "indicator_id": "FS02-M02",
            "event_code": "FS02",
            "video_id": "test-video",
            "person_track_id": 1,
            "event_id": "fs02-001",
            "feature_status": "measured",
            "features": features,
            "scoring_features": [*features, alignment],
            "quality_gate": {"measurement_allowed": True, "scoring_allowed": True},
        }
        for with_context in (True, False):
            if not with_context:
                alignment["valid"] = False
                alignment["value"] = None
                record["quality_gate"]["scoring_allowed"] = False
            item = next(value for value in build_training_evaluation([record])["indicator_evaluations"] if value["indicator_id"] == "FS02-M02")
            names = [value["feature_name"] for value in item["representative_measurements"]]
            self.assertEqual("target_direction_alignment_error_deg" in names, with_context)
            self.assertEqual(item["components"]["required_feature_coverage_percent"], 100 if with_context else 80)
            self.assertIsNone(item["technical_score_0_to_100"])

    def test_duplicate_event_is_one_observation_not_a_repeat(self) -> None:
        record = self._record("fs01-001")
        result = build_training_evaluation([record, dict(record)])
        item = result["indicator_evaluations"][1]
        self.assertEqual(item["total_instance_count"], 1)
        self.assertEqual(item["repeatability_sample_count"], 1)
        self.assertIsNone(item["components"]["repeatability_percent"])
        self.assertEqual(result["input_validation"]["duplicate_record_count"], 1)

    def test_conflicting_duplicate_event_is_rejected_in_either_order(self) -> None:
        first = self._record("fs01-001")
        conflicting = self._record("fs01-001")
        conflicting["features"][0]["value"] = 0.75
        forward = build_training_evaluation([first, conflicting])
        reverse = build_training_evaluation([conflicting, first])
        self.assertEqual(forward, reverse)
        self.assertIsNone(forward["score_0_to_100"])
        self.assertEqual(forward["input_validation"]["conflicting_record_count"], 2)

    def test_event_family_mismatch_cannot_contribute_to_indicator(self) -> None:
        record = self._record("fs01-001")
        record["event_code"] = "FS09"
        result = build_training_evaluation([record])
        self.assertIsNone(result["score_0_to_100"])
        self.assertEqual(result["input_validation"]["event_code_mismatch_record_count"], 1)

    def test_legacy_records_without_identity_never_establish_independent_repeats(self) -> None:
        for missing in ("video_id", "person_track_id", "event_id"):
            with self.subTest(missing=missing):
                record = self._record("fs01-001")
                record.pop(missing)
                result = build_training_evaluation([record, dict(record)])
                item = result["indicator_evaluations"][1]
                self.assertEqual(item["score_0_to_100"], 96)
                self.assertTrue(item["available"])
                self.assertEqual(item["total_instance_count"], 1)
                self.assertEqual(item["repeatability_sample_count"], 0)
                self.assertEqual(item["repeatability_status"], "independent_event_identity_required")
                self.assertIsNone(item["components"]["repeatability_percent"])

    def test_unmeasured_and_out_of_scope_records_never_become_numeric_zero(self) -> None:
        result = build_training_evaluation(
            [
                {
                    "indicator_id": "FS99-M99",
                    "event_code": "FS99",
                    "feature_status": "measured",
                    "features": [],
                },
                {
                    "indicator_id": "FS01-M02",
                    "event_code": "FS01",
                    "feature_status": "unavailable",
                    "features": [],
                },
            ]
        )

        self.assertFalse(result["available"])
        self.assertIsNone(result["score_0_to_100"])
        self.assertTrue(
            all(
                item["score_0_to_100"] is None
                for item in result["indicator_evaluations"]
            )
        )
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("FS99-M99", rendered)
        self.assertNotIn("状态为 unavailable", rendered)

    def test_different_absolute_measurements_are_described_without_technical_rank(self) -> None:
        outputs = []
        for synchrony in (80.0, 5000.0):
            records = [self._record("a"), self._record("b")]
            for record in records:
                record["features"][1]["value"] = synchrony
            result = build_training_evaluation(records)
            item = result["indicator_evaluations"][1]
            outputs.append(item["representative_measurements"])
            self.assertEqual(result["score_0_to_100"], 97)
            self.assertEqual(item["score_0_to_100"], 97)
            self.assertIsNone(item["technical_score_0_to_100"])
            self.assertEqual(result["strengths_zh"], [])
            self.assertEqual(result["priorities_zh"], [])
            self.assertTrue(result["available"])
        self.assertNotEqual(outputs[0], outputs[1])

    def test_partial_evidence_scores_never_become_technical_scores_or_missing_zero(self) -> None:
        first, second = self._record("a"), self._record("b")
        for feature in second["features"]:
            feature["value"] *= 1000
        missing = self._record("missing")
        missing["feature_status"] = "unavailable"
        missing["indicator_id"] = "FS01-M02"
        for records in ([first, second], [first], [first, missing], [missing], []):
            with self.subTest(count=len(records)):
                result = build_training_evaluation(records)
                self.assertIsNone(result["technical_score_0_to_100"])
                self.assertEqual(result["component_weights"]["repeatability"], 0.25)
                self.assertEqual(result["evaluated_indicator_count"], int(first in records))
                self.assertEqual(result["score_0_to_100"] is not None, first in records)
                for item in result["indicator_evaluations"]:
                    self.assertIsNone(item["technical_score_0_to_100"])
                    if item["available"]:
                        self.assertAlmostEqual(sum(item["effective_component_weights"].values()), 1.0)
                    else:
                        self.assertIsNone(item["score_0_to_100"])
                        self.assertEqual(item["effective_component_weights"], {})

    def test_missing_confidence_is_not_zero_and_is_excluded_from_effective_weights(self) -> None:
        record = self._record("a")
        for feature in record["features"]:
            feature.pop("confidence")
        item = build_training_evaluation([record])["indicator_evaluations"][1]
        self.assertIsNone(item["components"]["median_feature_confidence_percent"])
        self.assertNotIn("median_feature_confidence", item["effective_component_weights"])
        self.assertNotIn("repeatability", item["effective_component_weights"])
        self.assertAlmostEqual(sum(item["effective_component_weights"].values()), 1.0)
        self.assertEqual(item["score_0_to_100"], 100)
        self.assertIsNone(item["technical_score_0_to_100"])

    def test_internal_reason_codes_are_translated_not_exposed(self) -> None:
        result = build_training_evaluation(
            [self._record("fs01-001", jump_warning=True)]
        )
        item = next(
            value
            for value in result["indicator_evaluations"]
            if value["indicator_id"] == "FS01-M03"
        )

        self.assertTrue(any("骨架连续性" in text for text in item["limitations_zh"]))
        self.assertNotIn(
            "pose_keypoint_jump_unverified",
            json.dumps(item, ensure_ascii=False),
        )


if __name__ == "__main__":
    unittest.main()
