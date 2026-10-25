from __future__ import annotations

from copy import deepcopy
import json
import math
import unittest

from rallymate_vision.sam3d_evaluation import (
    CORE_JOINTS, RTMPOSE_RAW_SCORE_SEMANTICS, evaluate_sam3d_records, measure_sam3d_frame,
)


def _frame(index=0, *, time_ms=None, offset=0):
    points3d = {}
    for side, x in (("left", -.5), ("right", .5)):
        points3d.update({f"{side}_shoulder": [x, 1, 0], f"{side}_elbow": [x, .5, 0],
                         f"{side}_wrist": [x, .5, .5], f"{side}_hip": [x, 0, 0],
                         f"{side}_knee": [x, -1, 0], f"{side}_ankle": [x, -1, 1]})
    points2d = {name: [300 + p[0] * 100 + offset, 300 - p[1] * 100] for name, p in points3d.items()}
    time_ms = index * 40 if time_ms is None else time_ms
    return {"schema_version": "sam3d-frame-v1", "video_sha256": "a" * 64,
            "frame_index": index, "processed_index": index, "pts": time_ms,
            "time_base_num": 1, "time_base_den": 1000, "timestamp_ms": time_ms,
            "person_track_id": 1, "selection_epoch": 0, "camera_reference_epoch": 0,
            "image_size": [800, 600], "status": "ok", "reason": None,
            "inference_ms": 12.5, "peak_memory_bytes": 1024,
            "coordinate_system_2d": "image_pixels_x_right_y_down",
            "coordinate_system_3d": "sam3d_pred_keypoints_3d", "units_3d": "model_units",
            "keypoints_2d": points2d, "keypoints_3d": points3d,
            "baseline": {"keypoints_2d": deepcopy(points2d),
                         "keypoint_scores": {name: .9 for name in points2d},
                         "provenance": "same_frame_existing_pose"}}


class Sam3dEvaluationTests(unittest.TestCase):
    def test_3d_angles_are_estimates_without_scores_or_fake_confidence(self):
        result = measure_sam3d_frame(_frame())
        for name in ("left_knee_flexion_deg", "right_elbow_angle_deg"):
            self.assertAlmostEqual(result["measurements"][name]["value"], 90)
            self.assertEqual(result["measurements"][name]["status"], "estimated")
            self.assertIsNone(result["measurements"][name]["score"])
            self.assertIsNone(result["measurements"][name]["grade"])
        self.assertIsNone(result["confidence"])
        self.assertFalse(result["word_grade_eligible"])
        self.assertFalse(result["source_stage_confirmed"])

    def test_missing_joint_does_not_discard_other_measurements(self):
        frame = _frame()
        del frame["keypoints_3d"]["left_wrist"]
        values = measure_sam3d_frame(frame)["measurements"]
        self.assertIsNone(values["left_elbow_angle_deg"]["value"])
        self.assertEqual(values["left_knee_flexion_deg"]["value"], 90)
        self.assertEqual(values["right_elbow_angle_deg"]["value"], 90)

    def test_degenerate_and_nonfinite_geometry_remains_unavailable(self):
        frame = _frame()
        frame["keypoints_3d"]["left_knee"] = frame["keypoints_3d"]["left_hip"]
        frame["keypoints_3d"]["right_wrist"][0] = math.nan
        values = measure_sam3d_frame(frame)["measurements"]
        self.assertEqual(values["left_knee_flexion_deg"]["reason"], "degenerate_geometry")
        self.assertEqual(values["right_elbow_angle_deg"]["reason"], "required_joint_not_finite")

    def test_failed_and_skipped_frames_remain_in_coverage_denominator(self):
        records = [_frame(i) for i in range(3)]
        records[1]["status"] = "failed"
        records[2]["status"] = "skipped"
        report = evaluate_sam3d_records(records)
        self.assertEqual(report["coverage"]["denominator"], 3)
        self.assertEqual(report["coverage"]["joints"]["left_wrist"]["finite_2d_fraction"], 1 / 3)
        self.assertEqual(report["counts"]["status_failed"], 1)
        self.assertEqual(report["counts"]["status_skipped"], 1)
        self.assertEqual(report["coverage"]["joints"]["left_wrist"]["baseline_confidence_eligible_2d_fraction"], 1)
        self.assertEqual(report["same_frame_2d_comparison"]["distance_px"]["count"], len(CORE_JOINTS))

    def test_empty_or_failed_only_input_is_not_a_success(self):
        failed = _frame()
        failed["status"] = "failed"
        for records in ([], [failed]):
            report = evaluate_sam3d_records(records)
            self.assertEqual(report["status"], "not_executed_no_valid_outputs")
            self.assertFalse(report["promotion_allowed"])
            self.assertFalse(report["accuracy_validated"])
            self.assertIsNone(report["technical_score"])
            self.assertEqual(report["measurements_3d"], [])

    def test_model_difference_uses_image_pixels_and_diagonal(self):
        frame = _frame()
        for point in frame["baseline"]["keypoints_2d"].values():
            point[0] += 30
            point[1] += 40
        report = evaluate_sam3d_records([frame])
        comparison = report["same_frame_2d_comparison"]
        self.assertEqual(comparison["distance_px"]["mean"], 50)
        self.assertAlmostEqual(comparison["distance_image_diagonal"]["mean"], .05)
        self.assertEqual(comparison["interpretation"], "model_disagreement_not_accuracy_error")

    def test_absent_baseline_confidence_is_not_replaced_by_one(self):
        frame = _frame()
        del frame["baseline"]["keypoint_scores"]
        report = evaluate_sam3d_records([frame])
        self.assertEqual(report["counts"]["paired_frames"], 0)
        self.assertEqual(report["counts"]["frames_with_all_core_3d"], 1)
        self.assertIn("baseline_keypoint_scores_missing", report["reason_counts"])

    def test_baseline_confidence_threshold_applies_only_to_baseline(self):
        frame = _frame()
        frame["baseline"]["keypoint_scores"]["left_wrist"] = .1
        report = evaluate_sam3d_records([frame])
        comparison = report["same_frame_2d_comparison"]["per_joint_distance_image_diagonal"]
        self.assertEqual(comparison["left_wrist"]["count"], 0)
        self.assertEqual(comparison["right_wrist"]["count"], 1)
        self.assertEqual(report["coverage"]["joints"]["left_wrist"]["finite_2d"], 1)

    def test_declared_rtmpose_raw_response_is_normalized_without_changing_raw_or_sam(self):
        frame = _frame()
        baseline = frame["baseline"]
        baseline.update(model="rtmpose_rerun_on_same_manifest", source_binding={
            key: frame[key] for key in ("video_sha256","frame_index","pts","time_base_num","time_base_den",
                                        "timestamp_ms","person_track_id","selection_epoch","camera_reference_epoch")})
        baseline["keypoint_scores"]["left_wrist"] = 1.23
        original = deepcopy(frame)
        report = evaluate_sam3d_records([frame])
        self.assertEqual(report["coverage"]["joints"]["left_wrist"]["baseline_confidence_eligible_2d"],1)
        self.assertEqual(frame,original)
        self.assertIsNone(report["same_frame_2d_comparison"]["sam_confidence"])
        for value in (None,-.1,float('nan'),float('inf'),True):
            baseline["keypoint_scores"]["left_wrist"] = value
            self.assertEqual(evaluate_sam3d_records([frame])["coverage"]["joints"]["left_wrist"]["baseline_confidence_eligible_2d"],0)

    def test_unknown_or_contradictory_score_contract_cannot_accept_above_one(self):
        for change in ({}, {"model":"another_model"}, {"model":"rtmpose_rerun_on_same_manifest"},
                       {"model":"sam3d","confidence_semantics":RTMPOSE_RAW_SCORE_SEMANTICS},
                       {"model":"rtmpose_rerun_on_same_manifest","confidence_semantics":"probability"}):
            frame=_frame(); frame["baseline"].update(change)
            frame["baseline"]["keypoint_scores"]["left_wrist"]=1.1
            self.assertEqual(evaluate_sam3d_records([frame])["coverage"]["joints"]["left_wrist"]["baseline_confidence_eligible_2d"],0)

    def test_explicit_baseline_binding_must_match_every_field(self):
        fields = ("video_sha256", "frame_index", "pts", "time_base_num", "time_base_den", "timestamp_ms",
                  "person_track_id", "selection_epoch", "camera_reference_epoch")
        for changed in fields:
            with self.subTest(changed=changed):
                frame = _frame()
                binding = {field: frame[field] for field in fields}
                binding[changed] = "wrong" if isinstance(binding[changed], str) else binding[changed] + 1
                frame["baseline"]["source_binding"] = binding
                report = evaluate_sam3d_records([frame])
                self.assertEqual(report["counts"]["paired_frames"], 0)
                self.assertIn("baseline_source_binding_mismatch", report["reason_counts"])

    def test_unknown_baseline_provenance_or_coordinates_cannot_compare(self):
        for field, value in (("provenance", "nearest_frame"), ("coordinate_system_2d", "roi_pixels")):
            frame = _frame()
            frame["baseline"][field] = value
            self.assertEqual(evaluate_sam3d_records([frame])["counts"]["paired_frames"], 0)

    def test_original_pts_is_required_and_not_derived_from_average_fps(self):
        for field, value in (("pts", None), ("time_base_den", 0), ("timestamp_ms", 5)):
            frame = _frame()
            frame[field] = value
            report = evaluate_sam3d_records([frame])
            self.assertEqual(report["counts"]["invalid_binding"], 1)
            self.assertEqual(report["coverage"]["denominator"], 1)
            self.assertEqual(report["measurements_3d"], [])

    def test_variable_pts_uses_time_weighted_interpolation(self):
        records = [_frame(i, time_ms=t, offset=t / 10) for i, t in enumerate((0, 30, 100))]
        report = evaluate_sam3d_records(records)
        stat = report["temporal_2d"]["sam_2d_all_finite"]["joint_statistics"]["left_wrist"]
        self.assertEqual(stat["linear_interpolation_residual"]["count"], 1)
        self.assertAlmostEqual(stat["linear_interpolation_residual"]["mean"], 0)
        self.assertAlmostEqual(stat["acceleration"]["mean"], 0)

    def test_reversal_count_is_diagnostic_and_does_not_score(self):
        records = [_frame(i, offset=x) for i, x in enumerate((0, 30, 0))]
        report = evaluate_sam3d_records(records)
        metric = report["temporal_2d"]["sam_2d_all_finite"]["joint_statistics"]["left_wrist"]
        self.assertEqual(metric["reversal_count"], 1)
        self.assertGreater(metric["linear_interpolation_residual"]["mean"], 0)
        self.assertIsNone(report["technical_score"])

    def test_identity_and_reference_changes_never_form_triplets_or_bone_series(self):
        changes = (("person_track_id", 2), ("selection_epoch", 1), ("camera_reference_epoch", 1),
                   ("video_sha256", "b" * 64), ("image_size", [1600, 1200]))
        for field, value in changes:
            with self.subTest(field=field):
                records = [_frame(i) for i in range(3)]
                records[1][field] = value
                report = evaluate_sam3d_records(records)
                self.assertEqual(len(report["continuity"]["windows"]), 3)
                self.assertEqual(report["bone_stability_3d"], [])
                metric = report["temporal_2d"]["sam_2d_all_finite"]["joint_statistics"]["left_wrist"]
                self.assertEqual(metric["linear_interpolation_residual"]["count"], 0)

    def test_failure_or_joint_missing_does_not_bridge_time(self):
        records = [_frame(i) for i in range(3)]
        records[1]["status"] = "failed"
        report = evaluate_sam3d_records(records)
        self.assertEqual(len(report["continuity"]["windows"]), 2)
        self.assertEqual(report["bone_stability_3d"], [])
        records[1]["status"] = "ok"
        del records[1]["keypoints_2d"]["left_wrist"]
        del records[1]["keypoints_3d"]["left_wrist"]
        report = evaluate_sam3d_records(records)
        joints = report["temporal_2d"]["sam_2d_all_finite"]["joint_statistics"]
        self.assertEqual(joints["left_wrist"]["speed"]["count"], 0)
        self.assertEqual(joints["right_wrist"]["speed"]["count"], 2)
        self.assertFalse(any(row["bone"] == "left_forearm" for row in report["bone_stability_3d"]))

    def test_sparse_frames_nonincreasing_pts_and_large_gap_split_windows(self):
        for records in ([_frame(0), _frame(3), _frame(6)],
                        [_frame(0), _frame(1, time_ms=0), _frame(2)],
                        [_frame(0), _frame(1, time_ms=500), _frame(2, time_ms=1000)]):
            report = evaluate_sam3d_records(records)
            metric = report["temporal_2d"]["sam_2d_all_finite"]["joint_statistics"]["left_wrist"]
            self.assertEqual(metric["linear_interpolation_residual"]["count"], 0)
        report = evaluate_sam3d_records([_frame(0), _frame(2), _frame(4)], max_frame_gap=2)
        self.assertEqual(len(report["continuity"]["windows"]), 1)

    def test_bone_length_cv_retains_distinct_windows(self):
        records = [_frame(i) for i in range(4)]
        for frame in records[2:]:
            frame["selection_epoch"] = 1
            frame["keypoints_3d"] = {name: [v * 2 for v in point] for name, point in frame["keypoints_3d"].items()}
        report = evaluate_sam3d_records(records)
        rows = [row for row in report["bone_stability_3d"] if row["bone"] == "left_thigh"]
        self.assertEqual([row["length"]["mean"] for row in rows], [1, 2])
        self.assertEqual([row["coefficient_of_variation"] for row in rows], [0, 0])

    def test_3d_jitter_ignores_global_translation_but_records_articulation(self):
        records = [_frame(i) for i in range(3)]
        for index, frame in enumerate(records):
            for point in frame["keypoints_3d"].values():
                point[0] += index * 10
        report = evaluate_sam3d_records(records)
        self.assertAlmostEqual(report["temporal_3d"]["joint_statistics"]["left_wrist"]["speed"]["mean"], 0)
        records[1]["keypoints_3d"]["left_wrist"][2] += 1
        report = evaluate_sam3d_records(records)
        self.assertGreater(report["temporal_3d"]["joint_statistics"]["left_wrist"]["linear_interpolation_residual"]["mean"], 0)

    def test_duplicate_source_frame_is_not_counted_twice(self):
        report = evaluate_sam3d_records([_frame(), _frame()])
        self.assertEqual(report["counts"]["invalid_binding"], 2)
        self.assertEqual(report["coverage"]["joints"]["left_wrist"]["finite_3d"], 0)

    def test_runtime_reports_missing_as_missing_not_zero(self):
        records = [_frame(i) for i in range(3)]
        del records[1]["inference_ms"]
        records[2]["inference_ms"] = math.nan
        records[1]["peak_memory_bytes"] = None
        records[2]["status"] = "failed"
        runtime = evaluate_sam3d_records(records)["runtime"]
        self.assertEqual(runtime["inference_ms"]["count"], 1)
        self.assertEqual(runtime["inference_ms"]["mean"], 12.5)
        self.assertEqual(runtime["missing_inference_records"], 2)
        self.assertEqual(runtime["peak_memory_bytes"]["count"], 2)

    def test_coordinate_mismatch_does_not_turn_pixels_into_model_units(self):
        frame = _frame()
        frame["coordinate_system_2d"] = "roi_local"
        frame["units_3d"] = "pixels"
        report = evaluate_sam3d_records([frame])
        self.assertEqual(report["counts"]["frames_with_any_model_geometry"], 0)
        self.assertEqual(report["status"], "not_executed_no_valid_outputs")

    def test_out_of_image_points_remain_estimates_not_visibility(self):
        frame = _frame()
        frame["keypoints_2d"]["left_wrist"] = [-100, 10]
        report = evaluate_sam3d_records([frame])
        self.assertEqual(report["counts"]["core_2d_inside_image"], len(CORE_JOINTS) - 1)
        self.assertEqual(report["counts"]["core_2d_finite"], len(CORE_JOINTS))

    def test_input_is_unchanged_and_report_is_strict_json(self):
        records = [_frame(i) for i in range(3)]
        original = deepcopy(records)
        report = evaluate_sam3d_records(records)
        self.assertEqual(records, original)
        json.dumps(report, allow_nan=False)

    def test_invalid_parameter_or_malformed_record_is_explicit(self):
        with self.assertRaises(ValueError):
            evaluate_sam3d_records([], max_gap_ms=0)
        with self.assertRaises(ValueError):
            evaluate_sam3d_records([], baseline_min_confidence=1.1)
        report = evaluate_sam3d_records([None, {}, {"schema_version": "wrong"}])
        self.assertEqual(report["counts"]["invalid_binding"], 3)
        self.assertEqual(report["coverage"]["denominator"], 3)


if __name__ == "__main__":
    unittest.main()
