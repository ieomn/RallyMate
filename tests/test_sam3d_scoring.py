"""Small synthetic contract/geometry tests, never SAM recognition accuracy."""
from __future__ import annotations

import copy
import unittest

from rallymate_scoring.indicator_assessment import build_indicator_assessment
from rallymate_scoring.pose_observations import SOURCE_FIELDS
from rallymate_scoring.stroke_candidates import _sample, detect_stroke_candidates
from rallymate_scoring.temporal_recognition import build_temporal_window, samples_are_continuous
from rallymate_service.process_evaluation import build_process_evaluation
from rallymate_vision.pose.metadata import keypoint_schema
from tests.test_stroke_candidates import frame, swing


def sam_frame(record):
    record = copy.deepcopy(record)
    pose, source = record["poses"][0], record["frame"]
    existing = {point["name"]: point for point in pose["keypoints"]}
    source.update(width=1280, height=720, timestamp_source="decoder_pts")
    binding = {"video_sha256": "a" * 64, "frame_index": source["index"], "processed_index": source["processed_index"],
               "pts": source["timestamp_ms"], "time_base_num": 1, "time_base_den": 1000,
               "timestamp_ms": source["timestamp_ms"], "person_track_id": pose["person_track_id"],
               "selection_epoch": 0, "camera_reference_epoch": 0}
    points = []
    for definition in keypoint_schema("mhr70")["keypoints"]:
        value = existing.get(definition["name"])
        x, y = (value["x_px"], value["y_px"]) if value else (-100., -100.)
        points.append({**definition, "x_px": max(0, x), "y_px": max(0, y), "raw_x_px": x, "raw_y_px": y,
                       "confidence": None, "confidence_semantics": "not_provided_by_sam3d", "source": "sam3d",
                       "observation_semantics": "model_estimate", "observed": None, "visibility": None,
                       "in_frame": value is not None, "model_coordinates_3d": [10000., -10000., 9000.]})
    pose.update(source="sam3d", keypoint_format="mhr70", confidence=None, status="ok", coordinate_space="original_frame",
                confidence_semantics="not_provided_by_sam3d", observation_semantics="monocular_model_estimate",
                source_binding=binding, keypoints=points)
    return record


class Sam3DScoringTests(unittest.TestCase):
    def test_exact_wrist_mapping_uses_raw_2d_and_never_3d_or_fake_confidence(self):
        record = sam_frame(frame(0))
        wrist = record["poses"][0]["keypoints"][41]
        wrist.update(raw_x_px=380.123456, x_px=380.123)
        sample = _sample(record["poses"][0], record["frame"])
        self.assertAlmostEqual(sample.raw_wrists_px["right"][0], 380.123456)
        self.assertAlmostEqual(sample.points["right_wrist"][0], .80123456)
        self.assertEqual(sample.point_confidences, {})
        self.assertEqual(sample.observation_semantics, "monocular_model_estimate")
        self.assertNotIn("left_big_toe", sample.points)
        self.assertNotIn("neck", sample.points)

    def test_missing_binding_field_or_foreign_source_is_rejected(self):
        for key in SOURCE_FIELDS:
            record = sam_frame(frame(0))
            del record["poses"][0]["source_binding"][key]
            self.assertIsNone(_sample(record["poses"][0], record["frame"]), key)
        for key, value in (("video_sha256", "invalid"), ("frame_index", 9), ("person_track_id", 9),
                           ("processed_index", 9), ("timestamp_ms", 10), ("pts", 100),
                           ("selection_epoch", True), ("time_base_den", 0)):
            record = sam_frame(frame(0))
            record["poses"][0]["source_binding"][key] = value
            self.assertIsNone(_sample(record["poses"][0], record["frame"]), key)

    def test_missing_raw_out_of_frame_nonfinite_or_visibility_claim_does_not_supply_wrist(self):
        for defect in ("missing_raw", "out_of_frame", "nan", "border", "visibility"):
            record = sam_frame(frame(0))
            wrist = record["poses"][0]["keypoints"][41]
            if defect == "missing_raw":
                del wrist["raw_x_px"]
            elif defect == "out_of_frame":
                wrist["in_frame"] = False
            elif defect == "nan":
                wrist["raw_x_px"] = float("nan")
            elif defect == "border":
                wrist["raw_x_px"] = 1280
            else:
                wrist["observed"] = True
            sample = _sample(record["poses"][0], record["frame"])
            self.assertTrue(sample is None or "right_wrist" not in sample.points, defect)

    def test_wrong_mapping_and_fabricated_confidence_cannot_upgrade_sam(self):
        for defect in ("format", "semantic", "confidence", "joint_confidence", "mapping"):
            record = sam_frame(frame(0))
            pose = record["poses"][0]
            if defect == "format":
                pose["keypoint_format"] = "halpe26"
            elif defect == "semantic":
                pose["observation_semantics"] = "observed"
            elif defect == "confidence":
                pose["confidence"] = 1
            elif defect == "joint_confidence":
                pose["keypoints"][41]["confidence"] = 1
            else:
                pose["keypoints"][41]["name"] = "left_wrist"
            self.assertIsNone(_sample(pose, record["frame"]), defect)

    def test_rtmpose_null_and_low_confidence_remain_rejected(self):
        for confidence in (None, .1):
            record = frame(0)
            for point in record["poses"][0]["keypoints"]:
                point["confidence"] = confidence
            self.assertIsNone(_sample(record["poses"][0], record["frame"]))

    def test_model_geometry_is_explicitly_not_observed_or_confidence_verified(self):
        records = [sam_frame(frame(i)) for i in range(8)]
        samples = [_sample(record["poses"][0], record["frame"]) for record in records]
        data = build_temporal_window(samples).model_input
        self.assertFalse(any(any(row) for row in data["observed_mask"][0]))
        self.assertFalse(any(any(row) for row in data["confidence_available_mask"][0]))
        self.assertTrue(any(any(row) for row in data["model_estimate_mask"][0]))
        self.assertEqual(data["geometry_available_mask"], data["model_estimate_mask"])
        self.assertFalse(data["measurement_provenance"]["scoring_allowed"])

    def test_source_video_epoch_and_model_changes_break_continuity(self):
        for defect in ("video", "source_epoch", "model"):
            first, second = sam_frame(frame(0)), sam_frame(frame(1))
            if defect == "video":
                second["poses"][0]["source_binding"]["video_sha256"] = "b" * 64
            elif defect == "source_epoch":
                second["poses"][0]["source_binding"]["selection_epoch"] = 1
            else:
                second = frame(1)
            a, b = (_sample(record["poses"][0], record["frame"]) for record in (first, second))
            self.assertFalse(samples_are_continuous(a, b), defect)

    def test_nonzero_pts_keeps_player_time_and_absolute_source_time_separate(self):
        records = [sam_frame(record) for record in swing()]
        for record in records:
            binding = record["poses"][0]["source_binding"]
            binding["pts"] += 75000
            binding["timestamp_ms"] += 75000
            record["frame"]["source_timestamp_ms"] = binding["timestamp_ms"]
        sample = _sample(records[0]["poses"][0], records[0]["frame"])
        self.assertEqual(sample.time, 0)
        self.assertEqual(sample.source_binding["timestamp_ms"], 75000)
        action = detect_stroke_candidates(records)
        self.assertEqual(action["candidate_count"], 1)
        episode = action["motion_analysis"]["families"]["baseline"]["episodes"][0]
        self.assertLess(episode["end_ms"], 2000)
        report = build_indicator_assessment({"action_recognition": action})
        self.assertTrue(report["observations"])
        window = report["observations"][0]["windows"][0]
        self.assertEqual(window["source_binding_start"]["timestamp_ms"], window["start_ms"] + 75000)
        self.assertEqual(window["source_binding_start"]["video_timestamp_ms"], window["start_ms"])
        self.assertEqual(window["source_time_origin_ms"], 75000)

    def test_abrupt_source_pts_jump_cannot_bridge_a_smooth_player_clock(self):
        first, second = sam_frame(frame(0)), sam_frame(frame(1))
        second["poses"][0]["source_binding"].update(pts=20050, timestamp_ms=20050)
        second["frame"]["source_timestamp_ms"] = 20050
        a, b = (_sample(record["poses"][0], record["frame"]) for record in (first, second))
        self.assertFalse(samples_are_continuous(a, b))

    def test_estimated_swing_reaches_measurements_and_never_numeric_scores(self):
        action = detect_stroke_candidates([sam_frame(record) for record in swing()])
        self.assertEqual(action["candidate_count"], 1)
        episodes = action["motion_analysis"]["families"]["baseline"]["episodes"]
        self.assertEqual(len(episodes), 1)
        episode = episodes[0]
        self.assertEqual(episode["measurement_provenance"]["assessment_status"], "measurement_only")
        summary = {"action_recognition": action}
        report = build_process_evaluation(summary)
        self.assertIsNone(report["overall_score"])
        self.assertEqual(report["scored_dimension_count"], 0)
        swing_dimension = next(row for row in report["dimensions"] if row["id"] == "swing")
        self.assertIsNone(swing_dimension["score"])
        self.assertIsNotNone(swing_dimension["legacy_diagnostic"]["score"])
        self.assertFalse(swing_dimension["score_covers_all_source_cards"])
        self.assertTrue(all(row["measurement_provenance"]["scoring_allowed"] is False for row in swing_dimension["criteria"]))
        preparation = next(row for row in report["dimensions"] if row["id"] == "preparation")
        rows = [row for row in preparation["criteria"] if row.get("feature_name")]
        self.assertTrue(rows)
        self.assertTrue(all(row["measurement_provenance"]["confidence_available"] is False for row in rows))
        assessment = build_indicator_assessment(summary)
        self.assertGreater(assessment["counts"]["motion_joint_measurement_types"], 0)
        self.assertEqual(assessment["counts"]["formal_graded"], 0)


if __name__ == "__main__":
    unittest.main()
