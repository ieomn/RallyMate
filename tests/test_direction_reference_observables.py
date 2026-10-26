"""Synthetic raw records exercise contracts, not real tennis judgments."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from rallymate_scoring.direction_reference_observables import REFERENCE_VERSION, REFERENCE_SEMANTICS
from rallymate_service.directional_micro_adjustment import (
    ANNOTATION_VERSION, build_directional_micro_adjustment_report, compute_directional_micro_adjustment,
)
from rallymate_service.gs_card_execution import _prepare_samples
from tests.test_fs_step_motion_windows import fixture as raw_fixture


def fixture(identifier="FS05-M03", *, axis=(1, 0), target=True):
    records, primary, events, decoder, doc = raw_fixture()
    row = doc["windows"][0]
    row["indicator_id"] = identifier
    first, last = row["start_source_frame"], row["end_source_frame"]
    progress = [0]*4+[i/10 for i in range(1, 11)]+[1]*4
    for record in records:
        index = record["frame"]["index"]
        p = progress[index-first] if first <= index <= last else 0
        for point in record["poses"][0]["keypoints"]:
            if point["name"] == "right_ankle":
                point["y_px"] = 140
            point["x_px"] += 5*p*axis[0]
            point["y_px"] += 5*p*axis[1]
            if point["name"] == "right_ankle":
                point["x_px"] += 15*p*axis[0]
                point["y_px"] += 15*p*axis[1]
    samples, timeline = _prepare_samples(records, primary, decoder, video_sha256=doc["video_sha256"], job_id=doc["source_run_id"])
    anchor = next(entry for entry in timeline if entry["frame_index"] == first+1)
    reference = {"version": REFERENCE_VERSION, "method": "manual_visible_line", "semantics": REFERENCE_SEMANTICS,
        "axis_role": "longitudinal_projection" if identifier == "FS05-M03" else "transverse_projection",
        "video_sha256": doc["video_sha256"], "person_track_id": 1,
        "selection_epoch": anchor["selection_epoch"], "derived_selection_epoch": anchor["derived_selection_epoch"],
        "camera_reference_epoch": anchor["camera_reference_epoch"],
        "source_frame": {key: anchor[key] for key in ("frame_index", "pts", "time_base_num", "time_base_den")},
        "axis_points_px": [[100, 100], [100+100*axis[0], 100+100*axis[1]]],
        "uncertainty_px": .5, "source_description": "Synthetic line and target; no real court or ball"}
    if target:
        reference.update(target_kind="visible_ball_center", target_point_px=[200+50*axis[0], 90+50*axis[1]])
    row["direction_reference"] = reference
    doc["version"] = ANNOTATION_VERSION
    return [records, primary, events, decoder, doc]


def compute(args):
    return compute_directional_micro_adjustment(*args, source_job_id=args[-1]["source_run_id"])


def instance(report):
    return next(i for card in report["cards"] for i in card["instances"])


def real_fixture():
    return json.loads((Path(__file__).parent / "fixtures/directional-micro-adjustment-native-e420-v1.json").read_text(encoding="utf-8"))


def real_args(data):
    return [data[key] for key in ("records", "primary_timeline", "source_events", "decoder_timeline", "annotation_document")]


class DirectionReferenceTests(unittest.TestCase):
    def test_committed_real_source_records_recompute_both_measured_failures(self):
        data = real_fixture()
        report = compute(real_args(data))
        self.assertFalse(report["input"]["artifact_files_verified"])
        self.assertEqual(report["source_run_id"], "e4204ec3-7d84-4a90-a961-9dad01b43c2e")
        for card in report["cards"]:
            result = card["instances"][0]
            for key, expected in data["expected"][card["indicator_id"]].items():
                if key == "score":
                    self.assertEqual(expected, 60)  # Frozen v1 fixture remains unchanged.
                else:
                    self.assertEqual(result[key], expected)
            self.assertEqual(result["score"], 200 / 3)
            self.assertEqual([condition["passed"] for condition in result["conditions"]], [False, True, True])
            self.assertEqual(result["quality"]["valid_frame_count"], 25)
            self.assertLess(result["measurements"]["hip_axis_target_error_reduction_torso"], 0)
            self.assertIsNone(result["original_grade"])

    def test_real_source_missing_axis_target_or_wrong_epoch_never_scores(self):
        for mutation in ("axis", "target", "epoch", "joint"):
            args = real_args(real_fixture())
            for row in args[-1]["windows"]:
                if mutation == "axis": del row["direction_reference"]
                if mutation == "target":
                    del row["direction_reference"]["target_point_px"]
                    del row["direction_reference"]["target_kind"]
                if mutation == "epoch": row["direction_reference"]["derived_selection_epoch"] += 1
            if mutation == "joint":
                pose = args[0][8]["poses"][0]
                pose["keypoints"] = [p for p in pose["keypoints"] if p["name"] != "left_shoulder"]
            with self.subTest(mutation=mutation):
                report = compute(args)
                self.assertTrue(all(i["score"] is None for c in report["cards"] for i in c["instances"]))

    def test_original_media_integration_preserves_global_input_and_local_basis(self):
        data = real_fixture()
        root = Path(os.environ.get("RALLYMATE_REAL_ARTIFACT_ROOT", Path(__file__).resolve().parents[1]))
        source = root / data["provenance"]["source_run_directory"]
        video = root / data["provenance"]["source_video_relative_path"]
        if not video.is_file() or not all((source / name).is_file() for name in data["provenance"]["input_artifact_sha256"]):
            self.skipTest("optional original-media integration: native e420 source artifacts unavailable")
        report = build_directional_micro_adjustment_report(source, data["annotation_document"], video_path=video)
        self.assertTrue(report["input"]["service_eligible"])
        self.assertEqual(report["input"]["source_frame_count"], 2911)
        for card in report["cards"]:
            result = card["instances"][0]
            self.assertEqual(result["score"], 200 / 3)
            self.assertEqual(result["measurements"], data["expected"][card["indicator_id"]]["measurements"])
            proof = result["source_window_evidence"]["directional_micro_adjustment"]["local_normalization"]
            self.assertEqual(len(proof), 25)
            self.assertTrue(all(p["input_geometry"]["normalization_basis"] == "right_shoulder_right_torso" and p["output_basis"] == "full_torso" for p in proof))

    def test_two_short_steps_do_not_require_other_foot_stationary_or_smaller(self):
        args = fixture()
        first = args[-1]["windows"][0]["start_source_frame"]
        progress = [0]*4+[i/10 for i in range(1, 11)]+[1]*4
        for record in args[0]:
            index = record["frame"]["index"]-first
            if 0 <= index < len(progress):
                next(p for p in record["poses"][0]["keypoints"] if p["name"] == "left_ankle")["x_px"] += 17*progress[index]
        result = instance(compute(args))
        self.assertEqual(result["score"], 100, result["reason_codes"])
        self.assertGreater(result["measurements"]["both_ankles_max_path_length_torso"], result["measurements"]["moving_ankle_path_length_torso"])

    def test_local_reexpression_preserves_original_basis_and_rejects_fake_points(self):
        def prepare(*args, **kwargs):
            groups, timeline = _prepare_samples(*args, **kwargs)
            for sample in groups[1]:
                center = sample.reference_points["right_shoulder"]
                scale = math.dist(center, sample.reference_points["right_hip"])
                sample.center, sample.scale, sample.normalization_basis = center, scale, "right_shoulder_right_torso"
                sample.points = {j: tuple((v[i]-center[i])/scale for i in (0, 1)) for j, v in sample.reference_points.items()}
            return groups, timeline
        with patch("rallymate_service.directional_micro_adjustment._prepare_samples", prepare):
            result = instance(compute(fixture()))
        self.assertEqual(result["score"], 100)
        evidence = result["source_window_evidence"]["directional_micro_adjustment"]
        self.assertTrue(result["quality"]["geometry_gate_passed"])
        self.assertEqual(evidence["local_normalization"][0]["input_geometry"]["normalization_basis"], "right_shoulder_right_torso")
        self.assertEqual(evidence["local_normalization"][0]["output_basis"], "full_torso")
        def corrupted(*args, **kwargs):
            groups, timeline = prepare(*args, **kwargs)
            groups[1][8].points["left_ankle"] = (999, 999)
            return groups, timeline
        with patch("rallymate_service.directional_micro_adjustment._prepare_samples", corrupted):
            result = instance(compute(fixture()))
        self.assertIsNone(result["score"])
        self.assertFalse(result["quality"]["geometry_gate_passed"])
        args = fixture()
        record = args[0][8]
        record["poses"][0]["keypoints"] = [p for p in record["poses"][0]["keypoints"] if p["name"] != "left_shoulder"]
        result = instance(compute(args))
        self.assertIsNone(result["score"])
        self.assertEqual(result["quality"]["total_frame_count"], 18)

    def test_other_foot_large_return_path_is_failed_condition_not_missing(self):
        args = fixture()
        first = args[-1]["windows"][0]["start_source_frame"]
        for record in args[0]:
            index = record["frame"]["index"]-first
            if 5 <= index <= 12:
                point = next(p for p in record["poses"][0]["keypoints"] if p["name"] == "left_ankle")
                point["y_px"] += 30 if index % 2 else 0
        result = instance(compute(args))
        self.assertEqual(result["status"], "scored", result["reason_codes"])
        self.assertFalse(result["conditions"][1]["passed"])
        self.assertGreater(result["measurements"]["both_ankles_max_path_length_torso"], .5)
        self.assertLess(result["measurements"]["both_ankles_max_net_displacement_torso"], .5)
        self.assertEqual(result["score"], 200 / 3)

    def test_raw_record_two_card_entry_computes_values_not_pass_flags(self):
        for identifier, axis in (("FS05-M03", (1, 0)), ("FS05-M04", (0, 1))):
            args = fixture(identifier, axis=axis)
            before = deepcopy(args)
            report = compute(args)
            result = instance(report)
            self.assertEqual(args, before)
            self.assertEqual(result["status"], "scored", result["reason_codes"])
            self.assertEqual(result["grade"], "A")
            self.assertEqual(result["score"], 100)
            self.assertIsNone(result["original_grade"])
            self.assertFalse(report["input"]["service_eligible"])
            self.assertFalse(result["source_window_evidence"]["directional_micro_adjustment"]["court_metric_direction_verified"])
            self.assertAlmostEqual(result["measurements"]["hip_axis_target_error_reduction_torso"], .1)
            self.assertAlmostEqual(result["measurements"]["moving_ankle_net_displacement_torso"], .4)
            self.assertAlmostEqual(result["measurements"]["hip_axis_alignment_fraction"], 1)
            self.assertEqual(result["quality"]["total_frame_count"], 18)
            json.dumps(report, allow_nan=False)

    def test_missing_visible_target_or_axis_never_supplies_grade(self):
        args = fixture(target=False)
        result = instance(compute(args))
        self.assertIsNone(result["score"])
        self.assertIn("micro_adjustment_visible_target_missing", result["reason_codes"])
        self.assertEqual(result["source_window_evidence"]["directional_micro_adjustment"]["status"], "measured_directional_motion")
        del args[-1]["windows"][0]["direction_reference"]
        self.assertIsNone(instance(compute(args))["score"])

    def test_projection_axis_identity_clock_and_source_pixel_tampering(self):
        mutations = [lambda r:r.update(video_sha256="a"*64), lambda r:r.update(person_track_id=2),
            lambda r:r.update(selection_epoch=9), lambda r:r.update(derived_selection_epoch=9),
            lambda r:r.update(camera_reference_epoch=9), lambda r:r["source_frame"].update(pts=1),
            lambda r:r["source_frame"].update(frame_index=1000), lambda r:r.update(axis_role="transverse_projection"),
            lambda r:r.update(axis_points_px=[[1, 1], [1.1, 1.1]]),
            lambda r:r.update(axis_points_px=[[-1, 20], [50, 20]])]
        for mutate in mutations:
            args = fixture(); mutate(args[-1]["windows"][0]["direction_reference"])
            with self.subTest(mutation=str(mutate)):
                self.assertIsNone(instance(compute(args))["score"])

    def test_original_quality_and_source_inventory_cannot_be_bypassed(self):
        for kind in ("confirmed", "wrong_track", "coverage", "missing_first", "missing_last", "source_job"):
            args = fixture()
            if kind == "confirmed": args[2][0]["track_diagnostics"]["confirmed_id_switch_count"] = 1
            if kind == "wrong_track": args[2][0]["track_diagnostics"]["source_track_ids"] = [2]
            if kind == "coverage": args[2][0]["start_ms"] += 100
            if kind == "source_job": args[0][0]["job_id"] = "other"
            if kind in {"missing_first", "missing_last"}:
                index = args[-1]["windows"][0]["start_source_frame" if kind == "missing_first" else "end_source_frame"]
                args[0][:] = [r for r in args[0] if r["frame"]["index"] != index]
            with self.subTest(kind=kind):
                try: report = compute(args)
                except ValueError: continue
                self.assertIsNone(instance(report)["score"])

    def test_advisory_quality_is_retained_without_inventing_contact(self):
        args = fixture(); args[2][0]["quality_flags"] = ["keypoint_jump_candidates_present"]
        args[2][0]["track_diagnostics"]["keypoint_jump_candidate_joints"] = ["left_ankle"]
        result = instance(compute(args))
        self.assertEqual(result["status"], "scored", result["reason_codes"])
        self.assertIn("keypoint_jump_candidates_present", result["advisory_flags"])
        self.assertFalse(result["source_window_evidence"]["directional_micro_adjustment"]["contact_verified"])

    def test_opposite_target_and_oblique_axis_are_measured_failures_not_missing(self):
        args = fixture(); args[-1]["windows"][0]["direction_reference"]["target_point_px"] = [100, 90]
        result = instance(compute(args))
        self.assertEqual(result["status"], "scored")
        self.assertFalse(result["conditions"][0]["passed"])
        self.assertAlmostEqual(result["conditions"][0]["value"], -.1)
        args = fixture(); args[-1]["windows"][0]["direction_reference"]["axis_points_px"] = [[100, 50], [100, 200]]
        result = instance(compute(args))
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["conditions"][2]["value"], 0)

    def test_source_camera_transform_rotates_both_reference_and_motion_once(self):
        args = fixture(); angle = .1
        for record in args[0]:
            record["camera_motion"]["matrix_to_reference"] = [[math.cos(angle), -math.sin(angle), 10], [math.sin(angle), math.cos(angle), -3]]
        result = instance(compute(args))
        self.assertEqual(result["status"], "scored", result["reason_codes"])
        self.assertAlmostEqual(result["measurements"]["hip_axis_target_error_reduction_torso"], .1)
        self.assertAlmostEqual(result["measurements"]["hip_axis_alignment_fraction"], 1)

    def test_saved_values_and_fictitious_target_kind_are_rejected(self):
        for key, value in (("passed", True), ("score", 100), ("target_kind", "predicted_impact")):
            args = fixture();args[-1]["windows"][0]["direction_reference"][key] = value
            with self.assertRaises(ValueError):compute(args)
