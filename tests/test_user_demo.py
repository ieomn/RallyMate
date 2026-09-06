from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rallymate_service.user_demo import (
    UserDemoResultError,
    build_user_demo_result,
    load_indicator_feature_records,
)


class UserDemoResultTests(unittest.TestCase):
    def _summary(self, *, status: str = "completed") -> dict:
        return {
            "status": status,
            "job_id": "job-demo-001",
            "minimum_scoring_loop": {
                "event_counts": {"FS01": 2, "FS02": 1, "FS09": 0},
                "indicator_feature_validity": {
                    "FS01-M02": {"valid": 1, "total": 1},
                    "FS01-M03": {"valid": 1, "total": 1},
                    "FS01-M04": {"valid": 0, "total": 1},
                    "FS01-M05": {"valid": 0, "total": 1},
                    "FS02-M02": {"valid": 1, "total": 2},
                    "FS02-M03": {"valid": 0, "total": 2},
                },
            },
            "scoring_state": {
                "scoring_status": "calibration_required",
                "grade": None,
            },
        }

    def _record(
        self,
        indicator_id: str,
        event_code: str,
        *,
        features: list[dict] | None = None,
    ) -> dict:
        return {
            "indicator_id": indicator_id,
            "event_id": f"{event_code.lower()}-001",
            "event_code": event_code,
            "feature_status": "measured",
            "features": features or [],
        }

    def test_builds_separate_formation_and_analysis_scores_without_formal_grade(self) -> None:
        records = [
            self._record("FS01-M02", "FS01"),
            self._record(
                "FS01-M03",
                "FS01",
                features=[
                    {
                        "feature_name": "bilateral_foot_rise_min_body",
                        "value": 0.1,
                        "valid": True,
                    },
                    {
                        "feature_name": "bilateral_foot_rise_min_body",
                        "value": 0.2,
                        "valid": True,
                    },
                ],
            ),
            self._record("FS01-M04", "FS01"),
            self._record("FS01-M05", "FS01"),
            self._record("FS02-M02", "FS02"),
            self._record("FS02-M03", "FS02"),
        ]

        result = build_user_demo_result(self._summary(), records)

        self.assertEqual(result["schema_version"], "1.2.0")
        self.assertEqual(
            result["result_version"], "rallymate-user-demo-result-v1.2.0"
        )
        self.assertEqual(result["final_demo_score"]["value_0_to_100"], 42)
        self.assertEqual(
            result["final_demo_score"]["semantics"],
            "recognizable_motion_outline_and_amplitude_information_formation_only",
        )
        self.assertFalse(result["final_demo_score"]["is_formal_technique_score"])
        self.assertFalse(result["final_demo_score"]["is_coach_score"])
        self.assertFalse(result["final_demo_score"]["is_recognition_accuracy"])

        # Analysis quality: 30% * 2/3 observed actions + 45% * 6/13 measured
        # indicators + 25% * mean(50%, 25%, 0%) coverage = 47 after rounding.
        self.assertEqual(result["analysis_quality"]["value_0_to_100"], 47)
        self.assertEqual(
            result["analysis_quality"]["components"],
            {
                "observed_action_family_ratio_percent": 67,
                "measured_unique_indicator_ratio_percent": 46,
                "mean_action_measurement_coverage_percent": 25,
            },
        )
        self.assertEqual(result["display_score"]["value_0_to_100"], 47)
        self.assertEqual(result["display_score"]["label_zh"], "分析完成度")
        self.assertEqual(
            result["display_score"]["compatibility_alias_for"], "analysis_quality"
        )
        self.assertFalse(result["display_score"]["is_formal_technique_score"])
        self.assertFalse(result["formal_scoring"]["available"])
        self.assertIsNone(result["formal_scoring"]["grade"])
        self.assertEqual(
            result["training_evaluation"]["label_zh"],
            "动作表现参考分（Beta）",
        )
        self.assertEqual(result["training_evaluation"]["total_indicator_count"], 13)
        self.assertEqual(
            [len(action["indicator_evaluations"]) for action in result["actions"]],
            [4, 4, 5],
        )
        self.assertTrue(result["safety"]["measurement_completion_is_not_accuracy"])
        self.assertEqual(
            [action["status"] for action in result["actions"]],
            ["measured", "partially_measured", "not_observed"],
        )
        self.assertEqual(
            [
                action["formation_assessment"]["status"]
                for action in result["actions"]
            ],
            [
                "partially_formed_information",
                "limited_information",
                "not_observed",
            ],
        )
        self.assertEqual(
            [
                action["formation_assessment"]["reference_score_0_to_100"]
                for action in result["actions"]
            ],
            [77, 50, 0],
        )
        self.assertTrue(all(action["summary_zh"] for action in result["actions"]))
        self.assertEqual(
            result["actions"][0]["amplitudes"][0]["median_value"], 15.0
        )
        self.assertEqual(
            result["actions"][0]["amplitudes"][0]["sample_count"], 1
        )

    def test_amplitudes_exclude_unavailable_and_gate_denied_records(self) -> None:
        records = [
            {
                **self._record(
                    "FS01-M03",
                    "FS01",
                    features=[
                        {
                            "feature_name": "bilateral_foot_rise_min_body",
                            "value": 9.9,
                            "valid": True,
                        }
                    ],
                ),
                "feature_status": "unavailable",
            },
            {
                **self._record(
                    "FS01-M04",
                    "FS01",
                    features=[
                        {
                            "feature_name": "stance_width_body",
                            "value": 8.8,
                            "valid": True,
                        }
                    ],
                ),
                "quality_gate": {"measurement_allowed": False},
            },
        ]

        result = build_user_demo_result(self._summary(), records)

        self.assertEqual(result["actions"][0]["amplitudes"], [])
        self.assertEqual(result["actions"][0]["measured_indicator_count"], 0)

    def test_amplitudes_deduplicate_the_same_feature_within_each_event(self) -> None:
        records = [
            self._record(
                "FS09-M01",
                "FS09",
                features=[
                    {
                        "feature_name": "stability_duration_ms",
                        "value": 100.0,
                        "valid": True,
                    }
                ],
            ),
            self._record(
                "FS09-M02",
                "FS09",
                features=[
                    {
                        "feature_name": "stability_duration_ms",
                        "value": 200.0,
                        "valid": True,
                    }
                ],
            ),
            {
                **self._record(
                    "FS09-M03",
                    "FS09",
                    features=[
                        {
                            "feature_name": "stability_duration_ms",
                            "value": 300.0,
                            "valid": True,
                        }
                    ],
                ),
                "event_id": "fs09-002",
            },
        ]

        result = build_user_demo_result(self._summary(), records)
        amplitude = result["actions"][2]["amplitudes"][0]

        self.assertEqual(amplitude["sample_count"], 2)
        self.assertEqual(amplitude["median_value"], 225.0)

    def test_formal_grade_in_summary_is_never_exposed_by_demo_v1(self) -> None:
        summary = self._summary()
        summary["scoring_state"] = {
            "scoring_status": "scored",
            "grade": "A",
        }

        result = build_user_demo_result(summary, [])

        self.assertFalse(result["formal_scoring"]["available"])
        self.assertIsNone(result["formal_scoring"]["score_0_to_100"])
        self.assertIsNone(result["formal_scoring"]["grade"])
        self.assertEqual(
            result["formal_scoring"]["status"], "calibration_required"
        )
        self.assertIn("正式教练标定分", result["formal_scoring"]["message_zh"])
        self.assertFalse(result["training_evaluation"]["available"])
        self.assertIsNone(result["training_evaluation"]["score_0_to_100"])

    def test_full_recognizable_information_reaches_100_without_becoming_a_grade(self) -> None:
        summary = self._summary()
        indicator_ids = {
            "FS01": ("M02", "M03", "M04", "M05"),
            "FS02": ("M02", "M03", "M04", "M05"),
            "FS09": ("M01", "M02", "M03", "M04", "M05"),
        }
        summary["minimum_scoring_loop"]["event_counts"] = {
            event_code: 1 for event_code in indicator_ids
        }
        summary["minimum_scoring_loop"]["indicator_feature_validity"] = {
            f"{event_code}-{metric_code}": {"valid": 1, "total": 1}
            for event_code, metric_codes in indicator_ids.items()
            for metric_code in metric_codes
        }
        feature_names = {
            "FS01": (
                "bilateral_foot_rise_min_body",
                "bilateral_foot_rise_synchrony_ms",
                "stance_width_body",
            ),
            "FS02": (
                "first_step_displacement_body",
                "launch_foot_speed_peak_body_s",
                "launch_foot_motion_duration_ms",
            ),
            "FS09": (
                "hip_center_speed_drop_body_s",
                "left_knee_flexion_change_deg",
                "stability_duration_ms",
            ),
        }
        records = []
        for event_code, metric_codes in indicator_ids.items():
            for index, metric_code in enumerate(metric_codes):
                features = []
                if index == 0:
                    features = [
                        {"feature_name": name, "value": 1.0, "valid": True}
                        for name in feature_names[event_code]
                    ]
                records.append(
                    self._record(
                        f"{event_code}-{metric_code}",
                        event_code,
                        features=features,
                    )
                )

        result = build_user_demo_result(summary, records)

        self.assertEqual(result["final_demo_score"]["value_0_to_100"], 100)
        self.assertTrue(
            all(
                action["formation_assessment"]["reference_score_0_to_100"]
                == 100
                for action in result["actions"]
            )
        )
        self.assertFalse(result["formal_scoring"]["available"])
        self.assertIsNone(result["formal_scoring"]["grade"])

    def test_unknown_indicator_ids_cannot_inflate_demo_scores_or_amplitudes(self) -> None:
        summary = self._summary()
        summary["minimum_scoring_loop"]["event_counts"] = {
            "FS01": 1,
            "FS02": 0,
            "FS09": 0,
        }
        summary["minimum_scoring_loop"]["indicator_feature_validity"] = {
            "FS01-FAKE": {"valid": 99, "total": 99}
        }
        records = [
            self._record(
                "FS01-FAKE",
                "FS01",
                features=[
                    {
                        "feature_name": "stance_width_body",
                        "value": 9.9,
                        "valid": True,
                    }
                ],
            )
        ]

        result = build_user_demo_result(summary, records)

        first_action = result["actions"][0]
        self.assertEqual(first_action["measured_indicator_count"], 0)
        self.assertEqual(first_action["measurement_coverage_percent"], 0)
        self.assertEqual(first_action["amplitudes"], [])
        self.assertEqual(
            first_action["formation_assessment"]["reference_score_0_to_100"],
            30,
        )
        self.assertEqual(result["final_demo_score"]["value_0_to_100"], 10)
        self.assertEqual(
            result["analysis_quality"]["components"][
                "measured_unique_indicator_ratio_percent"
            ],
            0,
        )
        self.assertFalse(result["training_evaluation"]["available"])

    def test_frontend_supports_batches_restore_and_user_facing_assessments(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        html = (
            repository_root / "src/rallymate_service/assets/user-demo.html"
        ).read_text(encoding="utf-8")
        javascript = (
            repository_root / "src/rallymate_service/assets/user-demo.js"
        ).read_text(encoding="utf-8")

        self.assertIn('id="video-input" name="video" type="file"', html)
        self.assertIn("multiple required", html)
        self.assertIn('id="analysis-quality-value"', html)
        self.assertIn('id="runtime-badge"', html)
        self.assertIn('id="training-insights"', html)
        self.assertIn('id="batch-results"', html)
        self.assertNotIn('id="raw-output"', html)
        self.assertIn("for (const [index, file] of files.entries())", javascript)
        self.assertIn('data.delete("video")', javascript)
        self.assertIn('data.set("video", file, file.name)', javascript)
        self.assertIn("action.performance_assessment", javascript)
        self.assertIn("action.indicator_evaluations", javascript)
        self.assertIn('new URLSearchParams(window.location.search).get("job_id")', javascript)
        self.assertIn('/assets/user-demo.js?v=1.2.0', html)
        self.assertNotIn('scoring-loop-report.html', javascript)
        self.assertNotIn('analysis-report.html', javascript)
        self.assertIn("restoreSavedBatch", javascript)

        persisted_start = javascript.index("window.localStorage.setItem")
        persisted_end = javascript.index("}));", persisted_start) + len("}));")
        persisted_payload = javascript[persisted_start:persisted_end]
        self.assertNotIn("token", persisted_payload.lower())
        self.assertNotIn("original_filename", persisted_payload)
        self.assertNotIn("file.name", persisted_payload)
        self.assertNotIn("result", persisted_payload)

    def test_rejects_summary_that_is_not_completed(self) -> None:
        with self.assertRaisesRegex(UserDemoResultError, "not completed"):
            build_user_demo_result(self._summary(status="failed"), [])

    def test_loads_valid_jsonl_and_skips_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            path.write_text(
                '\n{"indicator_id":"FS01-M02","feature_status":"measured"}\n',
                encoding="utf-8",
            )

            records = load_indicator_feature_records(path)

        self.assertEqual(records[0]["indicator_id"], "FS01-M02")

    def test_rejects_empty_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            path.write_text("\n  \n", encoding="utf-8")

            with self.assertRaisesRegex(UserDemoResultError, "are empty"):
                load_indicator_feature_records(path)

    def test_rejects_duplicate_json_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            path.write_text('{"indicator_id":"a","indicator_id":"b"}\n', encoding="utf-8")

            with self.assertRaisesRegex(UserDemoResultError, "duplicate JSON key"):
                load_indicator_feature_records(path)

    def test_rejects_nonfinite_json_numbers(self) -> None:
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "indicator-features.jsonl"
                path.write_text(f'{{"value":{value}}}\n', encoding="utf-8")

                with self.assertRaisesRegex(
                    UserDemoResultError, "non-finite JSON number"
                ):
                    load_indicator_feature_records(path)


if __name__ == "__main__":
    unittest.main()
