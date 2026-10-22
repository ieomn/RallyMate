"""Synthetic source-record plumbing; no relabelled tennis clip is step truth."""
from copy import deepcopy
import json
import math
import unittest

from rallymate_service.fs_card_windows import validate_fs_card_annotations
from tests.test_fs_card_windows import fixture as source_fixture, compute, instances
from tests.test_step_motion_evidence import fixture as motion_fixture


def fixture(identifier="FS08-M02"):
    records, primary, _, decoder, document = source_fixture(identifier)
    row = document["windows"][0]
    first, last = row["start_source_frame"], row["end_source_frame"]
    _, synthetic, window, _ = motion_fixture()
    progress = [0]*4 + [.1*i for i in range(1, 11)] + [1]*4
    points = synthetic[0].points
    camera = deepcopy(records[10]["camera_motion"])
    camera["matrix_to_reference"] = [[1, 0, 0], [0, 1, 0]]
    for record in records:
        index = record["frame"]["index"]
        amount = progress[index-first] if first <= index <= last else 0
        record["camera_motion"] = deepcopy(camera)
        record["poses"] = [{"person_track_id": 1, "confidence": .9, "coordinate_space": "original_frame",
            "keypoint_format": "coco17", "keypoints": [
                {"name": name, "x_px": 200+50*x,
                    "y_px": 40+50*(y+(amount if name == "right_ankle" else 0)), "confidence": .9}
                for name, (x, y) in points.items()]}]
    primary = [{"processed_index": record["frame"]["processed_index"],
        "source_frame_index": record["frame"]["index"], "timestamp_ms": record["frame"]["timestamp_ms"],
        "source_track_id": 1, "selection_status": "selected", "identity_ambiguous": False} for record in records]
    observation = deepcopy(window["step_motion_observation"])
    original = next(item for item in decoder if item["frame_index"] == first+5)
    observation["source_frames"] = [{key: original[key] for key in ("frame_index", "pts", "time_base_num", "time_base_den")}]
    row["step_motion_observation"] = observation
    events = [{"video_id": document["source_run_id"], "person_track_id": 1001, "event_id": "synthetic-source-event",
        "start_ms": row["start_ms"], "end_ms": row["end_ms"], "quality_flags": [],
        "track_diagnostics": {"source_track_ids": [1], "primary_identity_ambiguous": False,
            "source_track_switch_candidate_count": 0}}]
    return [records, primary, events, decoder, document]


class FSStepMotionWindowTests(unittest.TestCase):
    def test_four_cards_use_actual_raw_series_and_keep_complete_source_proofs(self):
        for card in ("FS02-M04", "FS07-M02", "FS08-M02", "FS08-M03"):
            args = fixture(card)
            before = deepcopy(args)
            report = compute(args)
            row, = instances(report)
            self.assertEqual(args, before)
            self.assertEqual(row["status"], "proxy_scored", (card, row["reason_codes"], row["conditions"]))
            self.assertEqual(row["score"], 100)
            self.assertIsNone(row["original_grade"])
            self.assertFalse(report["input"]["service_eligible"])
            self.assertEqual(row["quality"]["total_frame_count"], 18)
            proof = row["source_window_evidence"]
            self.assertTrue(proof["step_cycle_bound"])
            self.assertTrue(proof["step_motion"]["complete_visual_motion_cycle"])
            self.assertEqual(proof["source_frames"], proof["step_motion"]["source_frames"])
            self.assertEqual(row["source_window_annotation"]["step_motion_observation"], args[-1]["windows"][0]["step_motion_observation"])
            self.assertFalse(proof["source_behavior_or_contact_verified"])
            self.assertEqual(len(proof["step_motion"]["feet"]["right"]["intervals"]), 17)
            self.assertEqual([entry["frame_index"] for entry in proof["step_motion"]["source_geometry_series"]], proof["source_frames"])
            json.dumps(report, allow_nan=False)

    def test_production_geometry_is_normalized_after_camera_compensation_and_retains_binding(self):
        args = fixture("FS08-M03")
        angle = .2
        cosine, sine = math.cos(angle), math.sin(angle)
        for record in args[0]:
            record["camera_motion"]["matrix_to_reference"] = [[cosine, -sine, 17], [sine, cosine, -9]]
        row, = instances(compute(args))
        proof = row["source_window_evidence"]
        motion = proof["step_motion"]
        self.assertTrue(motion["complete_visual_motion_cycle"])
        for normalized, reference, source in zip(motion["source_geometry_series"], motion["reference_series"], proof["source_pts"]):
            p = reference["points"]
            center = tuple((a+b)/2 for a,b in zip(p["left_shoulder"], p["right_shoulder"]))
            hips = tuple((a+b)/2 for a,b in zip(p["left_hip"], p["right_hip"]))
            scale = max(math.dist(center, hips), math.dist(p["left_shoulder"], p["right_shoulder"])*.75)
            self.assertEqual(normalized["normalization_basis"], "full_torso")
            for joint, coordinates in normalized["normalized_points"].items():
                for axis in (0, 1):
                    self.assertAlmostEqual(coordinates[axis], (p[joint][axis]-center[axis])/scale)
            for key in ("frame_index", "pts", "time_base_num", "time_base_den", "video_sha256"):
                self.assertEqual(normalized["source_binding"][key], source[key])
            self.assertEqual(normalized["source_binding"]["person_track_id"], 1)
        # Rotation affects both coordinate representations before the original
        # trunk-range formula; a constant rotation still has zero angular range.
        self.assertAlmostEqual(row["conditions"][1]["value"], 0)

    def test_original_quality_can_clear_score_after_motion_was_completely_measured(self):
        for target in ("jump", "unknown_joint", "identity", "coverage", "wrong_track"):
            args = fixture()
            event = args[2][0]
            if target in {"jump", "unknown_joint"}:
                event["quality_flags"] = ["keypoint_jump_candidates_present"]
                event["track_diagnostics"]["keypoint_jump_candidate_joints"] = ["right_ankle" if target == "jump" else "future_unknown_joint"]
            if target == "identity":
                event["track_diagnostics"]["primary_identity_ambiguous"] = True
            if target == "coverage":
                event["start_ms"] += 200
            if target == "wrong_track":
                event["track_diagnostics"]["source_track_ids"] = [2]
            row, = instances(compute(args))
            self.assertTrue(row["source_window_evidence"]["step_cycle_bound"])
            self.assertEqual(row["status"], "review_required", target)
            self.assertIsNone(row["score"])
            self.assertTrue(all(condition["passed"] is None for condition in row["conditions"]))
            self.assertEqual(row["source_window_evidence"]["original_event_quality"][0]["source_track_diagnostics"], event["track_diagnostics"])

    def test_invalid_observation_or_pts_is_not_repaired_by_uncertainty_or_saved_complete_flag(self):
        args = fixture()
        args[-1]["windows"][0]["binding"]["uncertainty_ms"] = 999999
        args[-1]["windows"][0]["step_motion_observation"]["source_frames"][0]["pts"] += 1
        row, = instances(compute(args))
        self.assertIsNone(row["score"])
        self.assertIn("step_motion_observation_pts_mismatch", row["source_window_evidence"]["step_motion"]["reason_codes"])
        args[-1]["windows"][0]["step_motion_observation"]["complete"] = True
        with self.assertRaises(ValueError):
            compute(args)

    def test_joint_missing_at_boundary_does_not_shrink_series(self):
        args = fixture()
        first = args[-1]["windows"][0]["start_source_frame"]
        record = next(row for row in args[0] if row["frame"]["index"] == first)
        point = next(point for point in record["poses"][0]["keypoints"] if point["name"] == "right_ankle")
        point["confidence"] = .1
        row, = instances(compute(args))
        self.assertIsNone(row["score"])
        proof = row["source_window_evidence"]["step_motion"]
        self.assertEqual(proof["total_frame_count"], 18)
        self.assertEqual(proof["valid_frame_count"], 17)
        self.assertFalse(proof["complete_visual_motion_cycle"])
        # The raw-record pose extractor already omits this low-confidence point.
        self.assertIsNone(proof["source_geometry_series"][0]["normalized_points"]["right_ankle"])

    def test_observation_is_only_allowed_for_four_cards_and_shared_crossing_window_must_agree(self):
        args = fixture("FS01-M04")
        with self.assertRaisesRegex(ValueError, "step_motion_card_not_supported"):
            validate_fs_card_annotations(args[-1])
        for target in ("person_track_id", "end_source_frame", "moving_foot"):
            document = fixture()[-1]
            second = deepcopy(document["windows"][0])
            second.update(annotation_id="synthetic-second-card", indicator_id="FS08-M03")
            if target == "moving_foot":
                second["step_motion_observation"][target] = "left"
            else:
                second[target] += 1
            document["windows"].append(second)
            with self.assertRaisesRegex(ValueError, "step_motion_shared_cycle_binding_conflict"):
                validate_fs_card_annotations(document)
        document = fixture()[-1]
        second = deepcopy(document["windows"][0])
        second.update(annotation_id="synthetic-second-card", indicator_id="FS08-M03")
        document["windows"].append(second)
        self.assertEqual(validate_fs_card_annotations(document), document)


if __name__ == "__main__":
    unittest.main()
