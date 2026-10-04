from __future__ import annotations

import copy
import unittest

from rallymate_service.analysis_report import build_analysis_report


class AnalysisReportTests(unittest.TestCase):
    def fixture(self):
        return {"training_evaluation": {"available": True, "score_0_to_100": 79,
                    "score_semantics": "measurement_evidence_quality", "evaluated_indicator_count": 1},
                "footwork_review": {"episode_count": 1, "episodes": [{"event_id": "f1", "start_ms": 100,
                    "end_ms": 900, "name_zh": "启动", "indicators": [{"indicator_id": "FS02-M02",
                    "feature_status": "unavailable", "measurements": [
                        {"feature_name": "body_center_speed_body_s", "name_zh": "身体移动速度", "status": "measured", "value": .6},
                        {"feature_name": "target_direction_alignment_error_deg", "status": "unavailable", "value": None}]}]}]},
                "action_recognition": {"motion_analysis": {"families": {"baseline": {"episodes": [{
                    "start_ms": 100, "end_ms": 900, "classification": {"status": "unclassified"},
                    "rotation_analysis": {"is_3d_rotation": False, "metrics": {"hip_line_change_deg": 12, "shoulder_line_change_deg": None},
                        "metric_evidence": {"hip_line_change_deg": {"status": "measured", "unit": "deg", "start_ms": 200, "end_ms": 800}},
                        "local_windows": []}}]}}}}}

    def test_independent_measurements_survive_unknown_class_and_partial_vector(self):
        source = self.fixture()
        before = copy.deepcopy(source)
        report = build_analysis_report(source)
        self.assertEqual(source, before)
        self.assertEqual(report["reference_score_0_to_100"], 79)
        self.assertEqual(report["measurement_summary"]["footwork_measured_feature_instances"], 1)
        self.assertEqual(report["measurement_summary"]["footwork_missing_feature_instances"], 1)
        self.assertEqual(report["measurement_summary"]["rotation_measured_metric_instances"], 1)
        self.assertEqual([item["id"] for item in report["focus_areas"]], ["footwork-review", "rotation-review", "recognition-review"])
        self.assertEqual(report["focus_areas"][1]["start_ms"], 200)
        self.assertEqual(report["focus_areas"][0]["status"], "partial")
        self.assertIsNone(report["technical_score_0_to_100"])
        self.assertFalse(any(item["is_technical_error_diagnosis"] for item in report["focus_areas"]))

    def test_empty_evidence_does_not_generate_score_or_diagnosis(self):
        report = build_analysis_report({})
        self.assertIsNone(report["reference_score_0_to_100"])
        self.assertEqual(report["focus_areas"][0]["kind"], "capture")
        self.assertIsNone(report["focus_areas"][0]["start_ms"])

    def test_score_requires_explicit_semantics_and_availability(self):
        for change in ({"score_semantics": None}, {"available": False}, {"score_0_to_100": True},
                       {"score_0_to_100": float("nan")}, {"score_0_to_100": 200}):
            with self.subTest(change=change):
                source = self.fixture()
                source["training_evaluation"].update(change)
                self.assertIsNone(build_analysis_report(source)["reference_score_0_to_100"])

    def test_local_window_not_counted_as_full_episode_measurement(self):
        source = self.fixture()
        rotation = source["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["rotation_analysis"]
        rotation["metrics"] = {"hip_line_change_deg": None}
        rotation["metric_evidence"] = {}
        rotation["local_windows"] = [{"status": "measured", "scope": "continuous_local_window", "continuous_samples": 8,
                                     "start_ms": 350, "end_ms": 600, "metrics": {"hip_line_change_deg": 8},
                                     "metric_evidence": {"hip_line_change_deg": {"status": "measured", "unit": "deg",
                                         "start_ms": 350, "end_ms": 600, "value": 8}}}]
        report = build_analysis_report(source)
        self.assertEqual(report["measurement_summary"]["rotation_measured_metric_instances"], 0)
        self.assertEqual(report["measurement_summary"]["rotation_local_window_count"], 1)
        item = report["focus_areas"][1]
        self.assertEqual(item["status"], "partial")
        self.assertEqual(item["start_ms"], 350)
        self.assertEqual(item["end_ms"], 600)

    def test_unavailable_or_out_of_episode_local_windows_do_not_create_review_evidence(self):
        for change in ({"status": "unavailable"}, {"start_ms": 9000, "end_ms": 10000},
                       {"continuous_samples": 6}, {"metrics": {"hip_line_change_deg": None}},
                       {"metric_evidence": {}}, {"scope": "episode"}):
            with self.subTest(change=change):
                source = self.fixture()
                rotation = source["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["rotation_analysis"]
                rotation["metrics"] = {}
                rotation["local_windows"] = [{"status": "measured", "scope": "continuous_local_window", "continuous_samples": 8,
                    "start_ms": 350, "end_ms": 600, "metrics": {"hip_line_change_deg": 8},
                    "metric_evidence": {"hip_line_change_deg": {"status": "measured", "unit": "deg",
                        "start_ms": 350, "end_ms": 600, "value": 8}}, **change}]
                report = build_analysis_report(source)
                self.assertEqual(report["measurement_summary"]["rotation_local_window_count"], 0)
                self.assertEqual(report["focus_areas"][1]["kind"], "capture")

    def test_invalid_numbers_or_status_cannot_revive_missing_measurement(self):
        source = self.fixture()
        indicator = source["footwork_review"]["episodes"][0]["indicators"][0]
        indicator["measurements"][0]["value"] = float("inf")
        indicator["measurements"][1]["value"] = 25
        report = build_analysis_report(source)
        self.assertEqual(report["measurement_summary"]["footwork_measured_feature_instances"], 0)
        self.assertEqual(report["focus_areas"][0]["kind"], "capture")

    def test_truncation_is_explicit_and_unscoped_intervals_do_not_link(self):
        source = self.fixture()
        source["footwork_review"]["is_truncated"] = True
        source["footwork_review"]["episodes"][0]["start_ms"] = True
        report = build_analysis_report(source)
        self.assertTrue(report["measurement_summary"]["is_truncated"])
        self.assertIsNone(report["focus_areas"][0]["start_ms"])

    def test_legacy_features_still_link_to_replay(self):
        source = self.fixture()
        indicator = source["footwork_review"]["episodes"][0]["indicators"][0]
        indicator["features"] = indicator.pop("measurements")[:1]
        indicator["feature_status"] = "measured"
        report = build_analysis_report(source)
        self.assertEqual(report["measurement_summary"]["footwork_measured_feature_instances"], 1)
        self.assertEqual(report["focus_areas"][0]["start_ms"], 100)

    def test_return_projection_cannot_double_count_the_same_motion_measurements(self):
        source = self.fixture()
        families = source["action_recognition"]["motion_analysis"]["families"]
        episode = families["baseline"]["episodes"][0]
        episode.update({"person_track_id": 1, "episode_id": "swing-1"})
        families["return"] = {"episodes": [copy.deepcopy(episode)]}
        families["return"]["episodes"][0]["episode_id"] = "return-swing-1"
        report = build_analysis_report(source)
        self.assertEqual(report["measurement_summary"]["rotation_measured_metric_instances"], 1)
