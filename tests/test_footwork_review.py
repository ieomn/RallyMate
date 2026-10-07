from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service.footwork_review import build_footwork_review, load_footwork_review
from tests import test_trajectory_api as fixture


def event(**changes) -> dict:
    return {
        "event_id": "fs01-001",
        "video_id": "video-a",
        "event_code": "FS01",
        "person_track_id": 1,
        "start_ms": 100,
        "end_ms": 500,
        **changes,
    }


def record(**changes) -> dict:
    return {
        "event_id": "fs01-001",
        "video_id": "video-a",
        "event_code": "FS01",
        "person_track_id": 1,
        "indicator_id": "FS01-M02",
        "feature_status": "measured",
        "scoring_status": "calibration_required",
        "grade": "A",
        "quality_gate": {"measurement_allowed": True, "scoring_allowed": True},
        "features": [{
            "feature_name": "stance_width_body",
            "value": .7,
            "unit": "body",
            "confidence": .9,
            "valid": True,
        }],
        **changes,
    }


class FootworkReviewTests(unittest.TestCase):
    def test_bound_measurements_keep_interval_units_and_no_grade(self):
        result = build_footwork_review([event()], [record()], video_id="video-a")
        self.assertEqual(result["status"], "available")
        episode = result["episodes"][0]
        self.assertEqual((episode["start_ms"], episode["end_ms"]), (100, 500))
        self.assertEqual(episode["person_track_id"], 1)
        indicator = episode["indicators"][0]
        self.assertEqual(indicator["scoring_status"], "calibration_required")
        self.assertEqual(indicator["features"][0], {
            "feature_name": "stance_width_body", "name_zh": "双脚支撑宽度",
            "value": .7, "unit": "body", "confidence": .9,
        })
        rendered = json.dumps(result)
        self.assertNotIn('"grade"', rendered)
        self.assertNotIn('"score"', rendered)

    def test_all_identity_dimensions_and_indicator_family_must_match(self):
        for changes in [
            {"video_id": "other"}, {"person_track_id": 2}, {"person_track_id": True},
            {"event_code": "FS02"}, {"event_id": "other"}, {"indicator_id": "FS02-M02"},
            {"indicator_id": "FS01-M99"}, {"video_id": None},
        ]:
            with self.subTest(changes=changes):
                result = build_footwork_review([event()], [record(**changes)], video_id="video-a")
                self.assertEqual(result["episodes"][0]["indicators"], [])
                self.assertEqual(result["rejected_indicator_count"], 1)

    def test_no_event_file_or_valid_interval_never_invents_an_episode(self):
        self.assertEqual(build_footwork_review([], [record()], video_id="video-a")["episodes"], [])
        for changes in [{"start_ms": True}, {"start_ms": -1}, {"end_ms": 100},
                        {"person_track_id": True}, {"event_code": []}, {"video_id": "other"}]:
            with self.subTest(changes=changes):
                result = build_footwork_review([event(**changes)], [record()], video_id="video-a")
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["episodes"], [])

    def test_video_identity_is_inferred_only_when_unambiguous(self):
        self.assertEqual(build_footwork_review([event()], [record()])["status"], "available")
        ambiguous = build_footwork_review([event(), event(video_id="video-b")], [record()])
        self.assertEqual(ambiguous["reason"], "video_identity_unavailable")

    def test_quality_gate_and_feature_status_suppress_values(self):
        for changes in [
            {"quality_gate": {"measurement_allowed": False}},
            {"quality_gate": {"measurement_allowed": 0}},
            {"quality_gate": []}, {"feature_status": "unavailable"},
        ]:
            with self.subTest(changes=changes):
                result = build_footwork_review([event()], [record(**changes)])
                item = result["episodes"][0]["indicators"][0]
                self.assertEqual(item["feature_status"], "unavailable")
                self.assertEqual(item["scoring_status"], "unavailable")
                self.assertEqual(item["features"], [])
        result = build_footwork_review([event()], [record(quality_gate={
            "measurement_allowed": True, "scoring_allowed": False,
        })])
        item = result["episodes"][0]["indicators"][0]
        self.assertEqual(item["feature_status"], "measured")
        self.assertEqual(item["scoring_status"], "unavailable")
        self.assertEqual(len(item["features"]), 1)

    def test_bad_values_units_and_confidence_are_not_displayed(self):
        for changes in [
            {"value": float("nan")}, {"value": True}, {"value": 10 ** 500},
            {"confidence": -1}, {"confidence": 2}, {"confidence": True},
            {"unit": "m"}, {"valid": False}, {"feature_name": "invented"},
        ]:
            with self.subTest(changes=list(changes)):
                source = record()
                source["features"][0].update(changes)
                item = build_footwork_review([event()], [source])["episodes"][0]["indicators"][0]
                self.assertEqual(item["feature_status"], "unavailable")
                self.assertEqual(item["features"], [])

    def test_duplicate_records_collapse_but_conflicts_are_rejected(self):
        source = record()
        result = build_footwork_review([event(), event()], [source, deepcopy(source)])
        self.assertEqual(len(result["episodes"]), 1)
        self.assertEqual(len(result["episodes"][0]["indicators"]), 1)

        conflicted = deepcopy(source)
        conflicted["features"][0]["value"] = .9
        result = build_footwork_review([event()], [source, conflicted, source])
        self.assertEqual(result["episodes"][0]["indicators"], [])
        self.assertEqual(result["rejected_indicator_count"], 1)
        result = build_footwork_review([event(), event(end_ms=600), event()], [source])
        self.assertEqual(result["episodes"], [])
        self.assertEqual(result["rejected_event_count"], 1)

    def test_conflicting_duplicate_feature_does_not_win_by_order(self):
        source = record()
        source["features"].append({**source["features"][0], "value": 1.5})
        item = build_footwork_review([event()], [source])["episodes"][0]["indicators"][0]
        self.assertEqual(item["features"], [])

    def test_valid_sibling_feature_survives_aggregate_unavailable_without_a_grade(self):
        source = record(feature_status="unavailable", camera_view="unknown")
        source["features"][0].update(feature_version="1.1.0-isotropic", source_frames=[4, 5, 6])
        source["features"].append({
            "feature_name": "left_knee_flexion_deg", "value": None, "unit": "deg",
            "confidence": 0, "valid": False, "reason": "valid_fraction_below_quality_gate",
        })
        item = build_footwork_review([event()], [source])["episodes"][0]["indicators"][0]
        self.assertEqual(item["feature_status"], "unavailable")
        self.assertEqual(item["scoring_status"], "unavailable")
        self.assertEqual(item["features"], [])
        self.assertEqual(item["measurement_status"], "partial")
        measurements = {row["feature_name"]: row for row in item["measurements"]}
        stance = measurements["stance_width_body"]
        self.assertEqual(stance["value"], .7)
        self.assertEqual(stance["status"], "measured")
        self.assertEqual(stance["unit"], "body")
        self.assertEqual(stance["feature_version"], "1.1.0-isotropic")
        self.assertEqual(stance["source_frames"], [4, 5, 6])
        self.assertEqual(stance["window"], {"start_ms": 100, "end_ms": 500, "scope": "event_interval"})
        self.assertEqual(stance["required_joints"], ["left_ankle", "right_ankle"])
        self.assertFalse(stance["view_label_required"])
        knee = measurements["left_knee_flexion_deg"]
        self.assertIsNone(knee["value"])
        self.assertEqual(knee["reason_codes"], ["valid_fraction_below_quality_gate"])
        self.assertIn("关键点", knee["reason_zh"])
        self.assertEqual((item["measured_feature_count"], item["expected_feature_count"]), (1, 5))

    def test_hard_gate_blocks_even_individually_valid_features_with_specific_reason(self):
        source = record(quality_gate={"measurement_allowed": False, "hard_fail_flags": ["confirmed_id_switch"]})
        item = build_footwork_review([event()], [source])["episodes"][0]["indicators"][0]
        self.assertEqual(item["measurement_status"], "unavailable")
        self.assertTrue(all(row["value"] is None for row in item["measurements"]))
        stance = next(row for row in item["measurements"] if row["feature_name"] == "stance_width_body")
        self.assertEqual(stance["reason_codes"], ["confirmed_id_switch"])
        self.assertIn("主体连续性", stance["reason_zh"])

    def test_conflict_only_suppresses_its_feature_and_never_picks_first(self):
        source = record(feature_status="unavailable")
        source["features"].extend([
            {**source["features"][0], "value": .9},
            {"feature_name": "left_knee_flexion_deg", "value": 20, "unit": "deg", "confidence": .8, "valid": True},
        ])
        item = build_footwork_review([event()], [source])["episodes"][0]["indicators"][0]
        measurements = {row["feature_name"]: row for row in item["measurements"]}
        self.assertIsNone(measurements["stance_width_body"]["value"])
        self.assertIn("conflicting_feature_records", measurements["stance_width_body"]["reason_codes"])
        self.assertEqual(measurements["left_knee_flexion_deg"]["value"], 20)

    def test_independent_measurements_still_reject_bad_numeric_payloads(self):
        for changes in [{"value": float("nan")}, {"confidence": True}, {"unit": "m"},
                        {"value": 10 ** 500}, {"confidence": 1.01}]:
            with self.subTest(changes=list(changes)):
                source = record(feature_status="unavailable")
                source["features"][0].update(changes)
                result = build_footwork_review([event()], [source])
                item = result["episodes"][0]["indicators"][0]
                self.assertEqual(item["measured_feature_count"], 0)
                self.assertTrue(all(row["value"] is None for row in item["measurements"]))
                json.dumps(result, allow_nan=False)

    def test_unknown_view_does_not_erase_image_plane_features(self):
        source = record(camera_view="unknown", quality_gate={
            "measurement_allowed": True, "scoring_allowed": False,
            "advisory_flags": ["camera_view_unknown"],
        })
        result = build_footwork_review([event()], [source])
        item = result["episodes"][0]["indicators"][0]
        self.assertEqual(item["measured_feature_count"], 1)
        self.assertEqual(item["scoring_status"], "unavailable")
        self.assertEqual(result["measurement_summary"]["partial_indicator_count"], 1)
        self.assertEqual(result["measurement_summary"]["measured_feature_count"], 1)

    def test_independent_target_direction_feature_respects_scoring_quality(self):
        alignment = {
            "feature_name": "target_direction_alignment_error_deg", "value": 12.0,
            "unit": "deg", "confidence": .9, "valid": True,
        }
        source = record(event_code="FS02", indicator_id="FS02-M02", features=[],
                        scoring_features=[alignment], scoring_feature_status="measured")
        for scoring_allowed, scoring_feature_status, expected_count in [
            (True, "measured", 1), (False, "measured", 0), (True, "unavailable", 0),
        ]:
            with self.subTest(scoring_allowed=scoring_allowed, scoring_feature_status=scoring_feature_status):
                changed = deepcopy(source)
                changed["quality_gate"]["scoring_allowed"] = scoring_allowed
                changed["scoring_feature_status"] = scoring_feature_status
                result = build_footwork_review([event(event_code="FS02")], [changed])
                values = result["episodes"][0]["indicators"][0]["features"]
                self.assertEqual(len(values), expected_count)
                if values:
                    self.assertEqual(values[0]["value"], 12)
                    self.assertEqual(values[0]["unit"], "deg")

    def test_target_direction_cannot_bypass_context_gate_through_measurement_features(self):
        alignment = {"feature_name": "target_direction_alignment_error_deg", "value": 12,
                     "unit": "deg", "confidence": .9, "valid": True}
        source = record(event_code="FS02", indicator_id="FS02-M02", features=[alignment])
        item = build_footwork_review([event(event_code="FS02")], [source])["episodes"][0]["indicators"][0]
        self.assertEqual(item["features"], [])
        target = next(row for row in item["measurements"] if row["feature_name"] == alignment["feature_name"])
        self.assertIsNone(target["value"])
        self.assertIn("target_direction_not_observed", target["reason_codes"])

    def test_scoring_and_measurement_feature_conflicts_remain_unavailable(self):
        source = record(scoring_feature_status="measured")
        source["scoring_features"] = [{**source["features"][0], "value": .9}]
        result = build_footwork_review([event()], [source])
        self.assertEqual(result["episodes"][0]["indicators"][0]["features"], [])
        source["scoring_features"] = deepcopy(source["features"])
        result = build_footwork_review([event()], [source])
        self.assertEqual(len(result["episodes"][0]["indicators"][0]["features"]), 1)

    def test_episode_limit_is_explicit_and_sorted(self):
        events = [event(event_id=f"event-{i}", start_ms=i * 100, end_ms=i * 100 + 50) for i in [3, 1, 2]]
        with patch("rallymate_service.footwork_review.MAX_RETURNED_EPISODES", 2):
            result = build_footwork_review(events, [], video_id="video-a")
        self.assertEqual(result["episode_count"], 3)
        self.assertEqual(result["returned_episode_count"], 2)
        self.assertTrue(result["is_truncated"])
        self.assertEqual([row["start_ms"] for row in result["episodes"]], [100, 200])
        with patch("rallymate_service.footwork_review.MAX_EVENTS", 1):
            self.assertEqual(build_footwork_review(events, [])["reason"], "record_limit_exceeded")

    def test_event_loader_fails_closed_without_private_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            self.assertEqual(load_footwork_review(path, [record()])["reason"], "events_artifact_missing")
            for contents in ['{broken}\n', '[]\n', '{"event_id":"a","event_id":"b"}\n', '{"value":NaN}\n']:
                with self.subTest(contents=contents):
                    path.write_text(contents, encoding="utf-8")
                    result = load_footwork_review(path, [record()])
                    self.assertEqual(result["reason"], "events_artifact_invalid")
                    self.assertIn("损坏", result["reason_zh"])
                    self.assertNotIn(directory, json.dumps(result))
            path.write_text(json.dumps(event()) + "\n", encoding="utf-8")
            with patch("rallymate_service.footwork_review.MAX_EVENT_FILE_BYTES", 1):
                self.assertEqual(load_footwork_review(path, [record()])["reason"], "events_artifact_limit_exceeded")


class FootworkReviewApiTests(unittest.TestCase):
    def test_demo_result_attaches_bound_review_and_bad_events_degrade_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = fixture.TrajectoryApiTests()
            app, settings, database = helper._app(Path(directory))
            job_id = helper._succeeded_job(settings, database)
            output = settings.runs_dir / job_id
            (output / "indicator-features.jsonl").write_text(json.dumps(record(video_id=job_id)) + "\n", encoding="utf-8")
            with TestClient(app) as client:
                url = f"/v1/jobs/{job_id}/demo-result"
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["footwork_review"]["reason"], "events_artifact_missing")

                (output / "events.jsonl").write_text(json.dumps(event(video_id=job_id)) + "\n", encoding="utf-8")
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
                review = response.json()["footwork_review"]
                self.assertEqual(review["status"], "available")
                self.assertEqual(review["episodes"][0]["video_id"], job_id)
                self.assertEqual(len(review["episodes"][0]["indicators"]), 1)

                (output / "events.jsonl").write_text("{broken}\n", encoding="utf-8")
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["footwork_review"]["reason"], "events_artifact_invalid")

    def test_demo_result_does_not_adopt_unrelated_event_video_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = fixture.TrajectoryApiTests()
            app, settings, database = helper._app(Path(directory))
            job_id = helper._succeeded_job(settings, database)
            output = settings.runs_dir / job_id
            (output / "indicator-features.jsonl").write_text(json.dumps(record()) + "\n", encoding="utf-8")
            (output / "events.jsonl").write_text(json.dumps(event()) + "\n", encoding="utf-8")
            with TestClient(app) as client:
                response = client.get(f"/v1/jobs/{job_id}/demo-result")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["footwork_review"]["episodes"], [])


if __name__ == "__main__":
    unittest.main()
