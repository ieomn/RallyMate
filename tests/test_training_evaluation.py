from __future__ import annotations

import json
import unittest

from rallymate_service.training_evaluation import build_training_evaluation


class TrainingEvaluationTests(unittest.TestCase):
    @staticmethod
    def _record(event_id: str, *, jump_warning: bool = False) -> dict:
        flags = ["pose_keypoint_jump_unverified"] if jump_warning else []
        return {
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

    def test_builds_thirteen_user_rubric_evaluations_and_beta_score(self) -> None:
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
        self.assertIn("双脚出现同步", evaluated["suggestion_zh"])
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
        self.assertIsNone(result["action_evaluations"]["FS02"]["score_0_to_100"])
        self.assertIsNone(result["formal_grade"])
        self.assertFalse(result["is_formal_coach_score"])

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
