import copy
import unittest

from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_service.footwork_review import build_footwork_review
from rallymate_service.process_evaluation import build_process_evaluation
from tests.test_model_measurement_contract import model_window_fixture


def fixture():
    proof = model_window_fixture()
    proof["source_window"]["person_track_id"] = 17
    for binding in proof["source_window"]["source_bindings"]:
        binding["person_track_id"] = 17
    event = {"event_id": "model-fs02", "event_code": "FS02", "video_id": "video-a", "person_track_id": 1,
             "start_ms": 300, "end_ms": 400, "track_diagnostics": {"source_track_ids": [17]},
             **{key: copy.deepcopy(proof[key]) for key in ("measurement_provenance", "source_window")}}
    features = []
    for name, value in (("body_center_speed_body_s", .5), ("launch_direction_deg", 0)):
        features.append({"feature_name": name, "valid": True, "value": value,
                         "unit": FEATURE_DEFINITIONS[name]["unit"], "confidence": None,
                         "feature_version": "test-model-footwork-v1", "source_frames": [9, 10, 11, 12],
                         "geometry_mask": {joint: [True]*4 for joint in FEATURE_DEFINITIONS[name]["required_joints"]},
                         **{key: copy.deepcopy(proof[key]) for key in ("measurement_provenance", "source_window")}})
    record = {"event_id": event["event_id"], "event_code": "FS02", "video_id": "video-a", "person_track_id": 1,
              "indicator_id": "FS02-M02", "feature_status": "measured", "scoring_status": "unavailable",
              "quality_gate": {"measurement_allowed": True, "scoring_allowed": False},
              "provenance": {"video_sha256": "a"*64},
              "measurement_provenance": copy.deepcopy(proof["measurement_provenance"]), "features": features}
    event["geometry_mask"] = {name: [True]*4 for feature in features for name in feature["geometry_mask"]}
    return event, record


class ModelFootworkReviewTests(unittest.TestCase):
    def test_bound_geometry_preserves_unknown_confidence_and_canonical_vs_source_identity(self):
        event, record = fixture()
        review = build_footwork_review([event], [record], video_id="video-a")
        self.assertEqual(review["measurement_summary"]["measured_feature_count"], 2)
        episode = review["episodes"][0]
        self.assertEqual(episode["person_track_id"], 1)
        self.assertEqual(episode["source_window"]["person_track_id"], 17)
        for row in episode["indicators"][0]["measurements"]:
            if row["status"] != "measured": continue
            self.assertIsNone(row["confidence"])
            self.assertFalse(row["measurement_provenance"]["visibility_verified"])
        again = build_footwork_review(review["episodes"], [record], video_id="video-a")
        self.assertEqual(again, review)
        process = build_process_evaluation({"job_id": "video-a"}, [record], footwork_review=review)
        movement = next(item for item in process["dimensions"] if item["id"] == "movement")
        self.assertEqual(movement["measured_criterion_count"], 2)
        self.assertIsNone(movement["score"])
        for row in movement["criteria"]:
            if row["status"] != "measured": continue
            self.assertIsNone(row["quality"]["confidence"])
            self.assertEqual(row["source_window"], event["source_window"])
            self.assertFalse(row["measurement_provenance"]["scoring_allowed"])

    def test_missing_or_forged_model_proof_cannot_fall_back_to_numeric_confidence(self):
        for defect in ("confidence", "provenance", "visibility", "clock", "track", "frame", "measurement_gate", "missing_event_proof", "mask"):
            event, record = fixture()
            if defect == "measurement_gate": record["quality_gate"]["measurement_allowed"] = False
            elif defect == "missing_event_proof": del event["source_window"]
            for feature in record["features"]:
                if defect == "confidence": feature["confidence"] = .9
                elif defect == "provenance": del feature["measurement_provenance"]
                elif defect == "visibility": feature["measurement_provenance"]["visibility_verified"] = True
                elif defect == "clock": feature["source_window"]["source_bindings"][1]["pts"] += 1000
                elif defect == "track": event["track_diagnostics"]["source_track_ids"] = [1]
                elif defect == "frame": feature["source_frames"] = [900, 901]
                elif defect == "mask": feature["geometry_mask"] = {"left_hip": [1, 1, 1, 1]}
            review = build_footwork_review([event], [record], video_id="video-a")
            self.assertEqual(review["measurement_summary"]["measured_feature_count"], 0, defect)

    def test_eligible_math_gate_does_not_hide_valid_raw_model_measurements(self):
        event, record = fixture()
        before = build_footwork_review([event], [record], video_id="video-a")
        record["quality_gate"]["scoring_allowed"] = True
        after = build_footwork_review([event], [record], video_id="video-a")
        self.assertEqual(after["measurement_summary"]["measured_feature_count"], 2)
        self.assertEqual(after["episodes"][0]["indicators"][0]["measurements"],
                         before["episodes"][0]["indicators"][0]["measurements"])
        for row in after["episodes"][0]["indicators"][0]["measurements"]:
            if row["status"] == "measured":
                self.assertIsNone(row["confidence"])
                self.assertFalse(row["measurement_provenance"]["scoring_allowed"])

    def test_child_masks_cannot_override_parent_or_bypass_joint_coverage(self):
        for defect in ("all_false", "parent_false", "too_few", "no_joint_overlap", "foreign_sha", "canonical_conflict"):
            event, record = fixture()
            if defect == "foreign_sha": record["provenance"]["video_sha256"] = "b"*64
            elif defect == "canonical_conflict": event["track_diagnostics"]["primary_player_id"] = 17
            elif defect == "parent_false":
                event["geometry_mask"] = {name: [False]*4 for name in event["geometry_mask"]}
            else:
                for feature in record["features"]:
                    feature["geometry_mask"] = {name: ([False]*4 if defect == "all_false" else
                        [True, False, False, False] if defect == "too_few" else
                        [i % 2 == j % 2 for i in range(4)]) for j, name in enumerate(feature["geometry_mask"])}
            review = build_footwork_review([event], [record], video_id="video-a")
            self.assertEqual(review["measurement_summary"]["measured_feature_count"], 0, defect)

    def test_model_tags_block_independent_target_score_even_with_forged_numeric_confidence(self):
        from tests.test_process_evaluation import ProcessEvaluationTests, summary, dimensions
        from rallymate_scoring.model_measurement_contract import MODEL_MEASUREMENT_PROVENANCE
        for location in ("record", "event", "feature"):
            record, review = ProcessEvaluationTests()._target_evidence()
            target = {"record": record, "event": review["episodes"][0], "feature": record["features"][0]}[location]
            target["measurement_provenance"] = dict(MODEL_MEASUREMENT_PROVENANCE)
            movement = dimensions(build_process_evaluation(summary(), [record], footwork_review=review))["movement"]
            self.assertIsNone(movement["score"], location)

    def test_selected_ankle_features_preserve_valid_single_side_without_inventing_joint_substitutes(self):
        event, record = fixture()
        record["indicator_id"] = "FS02-M04"
        feature = record["features"][0]
        feature.update(feature_name="launch_foot_speed_peak_body_s", required_joints_used=["left_hip", "right_hip", "right_ankle"], selected_side="right")
        feature["geometry_mask"] = {name: [True]*4 for name in feature["required_joints_used"]}
        feature["geometry_mask"]["left_ankle"] = [False]*4
        record["features"] = [feature]
        event["geometry_mask"] = copy.deepcopy(feature["geometry_mask"])
        review = build_footwork_review([event], [record], video_id="video-a")
        measured = [row for row in review["episodes"][0]["indicators"][0]["measurements"] if row["status"] == "measured"]
        self.assertEqual(len(measured), 1)
        self.assertEqual(measured[0]["required_joints"], ["left_hip", "right_hip", "right_ankle"])
        for defect in ("side", "joint", "name"):
            bad = copy.deepcopy(record)
            if defect == "side": bad["features"][0]["selected_side"] = "left"
            elif defect == "joint": bad["features"][0]["required_joints_used"] = ["right_ankle"]
            else: bad["features"][0]["feature_name"] = "launch_foot_relative_displacement_body"
            self.assertEqual(build_footwork_review([event], [bad], video_id="video-a")["measurement_summary"]["measured_feature_count"], 0, defect)

    def test_single_frame_stability_representative_keeps_full_bound_source_window(self):
        event, record = fixture()
        event.update(event_code="FS09")
        record.update(event_code="FS09", indicator_id="FS09-M04")
        feature = record["features"][0]
        feature.update(feature_name="stability_duration_ms", value=33, unit="ms", source_frames=[10])
        feature["geometry_mask"] = {name: [True]*4 for name in FEATURE_DEFINITIONS["stability_duration_ms"]["required_joints"]}
        record["features"] = [feature]
        event["geometry_mask"] = copy.deepcopy(feature["geometry_mask"])
        for representative in ([10], [10, 10]):
            feature["source_frames"] = representative
            review = build_footwork_review([event], [record], video_id="video-a")
            rows = [row for row in review["episodes"][0]["indicators"][0]["measurements"] if row["status"] == "measured"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_frames"], [10])
            self.assertEqual(rows[0]["source_window"]["source_frames"], [9, 10, 11, 12])


if __name__ == "__main__":
    unittest.main()
