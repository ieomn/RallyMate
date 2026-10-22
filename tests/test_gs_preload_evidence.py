"""Synthetic math and source-binding tests; no real M01 evidence is claimed."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from rallymate_scoring.gs_preload_evidence import ANNOTATION_VERSION, ANNOTATION_SEMANTICS, SUPPORTED_CARDS
from rallymate_service.gs_card_execution import compute_gs_card_execution, validate_stage_annotations
from tests.test_gs_racket_side_binding import synthetic_case


def fixture(count=12, code="GS01"):
    case = synthetic_case(count=count, technique=code, side=None)
    stage = case["annotations"]["stages"][0]
    stage.update(annotation_id="synthetic-m01", stage_code=code + "-M01")
    identifier = next(card for card in SUPPORTED_CARDS if card.startswith(code + "-"))
    frame = lambda index: {key: case["decoder_timeline"][index][key]
        for key in ("frame_index", "pts", "time_base_num", "time_base_den")}
    stage["local_preload_observation"] = {"version": ANNOTATION_VERSION, "method": "manual_anchor",
        "annotation_id": stage["annotation_id"], "action_id": stage["action_id"], "indicator_id": identifier,
        "video_sha256": case["annotations"]["video_sha256"], "person_track_id": 1,
        "start_frame": frame(0), "end_frame": frame(count - 1),
        "source": "synthetic mathematical test only", "source_description": "Declared local descent for math validation, not real tennis evidence.",
        "uncertainty_ms": 80, "semantics": ANNOTATION_SEMANTICS}
    for index, record in enumerate(case["frame_records"]):
        for point in record["poses"][0]["keypoints"]:
            if point["name"] in ("left_hip", "right_hip"):
                point["y_px"] += index
    return case


def compute(case):
    return compute_gs_card_execution(case["frame_records"], case["primary_timeline"], case["decoder_timeline"],
        case["annotations"], source_job_id=case["source_job_id"])


def target(report):
    return next(instance for row in report["cards"] for instance in row["instances"] if "auxiliary_evidence" in instance)


class GSPreloadEvidenceTests(unittest.TestCase):
    def test_requested_parent_bounds_are_preserved_only_for_auxiliary_target(self):
        case = fixture(count=15)
        stage = case["annotations"]["stages"][0]
        stage.update(start_ms=15, end_ms=545)
        for key, index in (("start_frame", 1), ("end_frame", 13)):
            stage["local_preload_observation"][key] = {field: case["decoder_timeline"][index][field]
                for field in ("frame_index", "pts", "time_base_num", "time_base_den")}
        report = compute(case)
        row = target(report)
        self.assertEqual(row["source_annotation"]["requested_window_ms"], {"start_ms": 15, "end_ms": 545})
        self.assertEqual(row["source_annotation"]["requested_window_ms"], row["auxiliary_evidence"]["source_annotation"]["stage_window_ms"])
        self.assertEqual((row["window"]["start_ms"], row["window"]["end_ms"]), (40, 520))
        self.assertEqual(row["auxiliary_evidence"]["status"], "measured_auxiliary")
        self.assertIsNone(row["score"])
        for card in report["cards"]:
            for instance in card["instances"]:
                if "auxiliary_evidence" not in instance:
                    self.assertNotIn("requested_window_ms", instance["source_annotation"])
        del stage["local_preload_observation"]
        without = compute(case)
        self.assertTrue(all("requested_window_ms" not in instance["source_annotation"]
            for card in without["cards"] for instance in card["instances"]))

    def test_all_five_cards_stay_manual_and_only_target_card_gets_auxiliary_evidence(self):
        for code in ("GS01", "GS02", "GS03", "GS04", "GS05"):
            report = compute(fixture(code=code)); row = target(report)
            self.assertEqual(row["status"], "review_required")
            self.assertIsNone(row["score"]); self.assertIsNone(row["proxy_grade"])
            self.assertIsNone(row["original_grade"]); self.assertFalse(row["source_grade_supported"])
            self.assertEqual(row["conditions"], [])
            self.assertEqual(sum("auxiliary_evidence" in i for c in report["cards"] for i in c["instances"]), 1)
            aux = row["auxiliary_evidence"]
            self.assertEqual(aux["status"], "measured_auxiliary")
            self.assertFalse(aux["split_step_confirmed"])
            self.assertTrue(all(o["passed"] is None and o["scoring_eligible"] is False for o in aux["auxiliary_observations"]))

    def test_endpoint_medians_use_twenty_percent_and_at_least_three_frames(self):
        aux = target(compute(fixture()))["auxiliary_evidence"]
        self.assertEqual(aux["series"]["endpoint_frame_count"], 3)
        self.assertAlmostEqual(aux["auxiliary_observations"][0]["value"], 9 / 55.5)
        self.assertNotAlmostEqual(aux["auxiliary_observations"][0]["value"], 11 / 55.5)
        self.assertAlmostEqual(aux["joint_descent_fraction"], 1.)
        self.assertEqual(aux["quality"]["valid_frame_count"], 12)
        self.assertEqual(len(aux["quality"]["required_joint_names"]), 8)

    def test_local_window_is_measured_instead_of_whole_stage_endpoints(self):
        case = fixture(count=24); stage = case["annotations"]["stages"][0]
        for boundary, index in (("start_frame", 4), ("end_frame", 15)):
            stage["local_preload_observation"][boundary] = {key: case["decoder_timeline"][index][key]
                for key in ("frame_index", "pts", "time_base_num", "time_base_den")}
        for i, record in enumerate(case["frame_records"]):
            displacement = i - 4 if 4 <= i <= 15 else (max(0, 11 - (i - 15) * 3) if i > 15 else 0)
            for point in record["poses"][0]["keypoints"]:
                if point["name"] in ("left_hip", "right_hip"):
                    point["y_px"] = 110 + displacement
        aux = target(compute(case))["auxiliary_evidence"]
        self.assertEqual(aux["series"]["source_frames"], list(range(4, 16)))
        self.assertEqual(aux["window"]["start_ms"], 160)
        self.assertEqual(aux["window"]["end_ms"], 600)
        self.assertAlmostEqual(aux["auxiliary_observations"][0]["value"], 9 / 55.5)
        self.assertEqual(aux["source_annotation"]["stage_window_ms"], {"start_ms": 0, "end_ms": 920})

    def test_seven_frame_boundary_computes_but_six_does_not(self):
        for count in (3, 5, 6, 7):
            aux = target(compute(fixture(count=count)))["auxiliary_evidence"]
            self.assertEqual(aux["status"], "measured_auxiliary" if count == 7 else "unavailable")
            self.assertEqual(len(aux["series"]["source_frames"]), count)
            if count < 7:
                self.assertTrue(all(row["value"] is None for row in aux["auxiliary_observations"]))
                self.assertIsNone(aux["joint_descent_fraction"])

    def test_hip_and_knees_changed_at_different_times_do_not_become_joint_descent(self):
        case = fixture()
        for i, record in enumerate(case["frame_records"]):
            points = {p["name"]: p for p in record["poses"][0]["keypoints"]}
            for side, direction in (("left", -1), ("right", 1)):
                hip, knee, ankle = (points[side + "_" + part] for part in ("hip", "knee", "ankle"))
                hip["y_px"] = 110 + min(i, 5) * 2
                knee["y_px"] = (hip["y_px"] + ankle["y_px"]) / 2
                knee["x_px"] = (hip["x_px"] + ankle["x_px"]) / 2 + direction * max(0, i - 5) * 1.5
        aux = target(compute(case))["auxiliary_evidence"]
        self.assertEqual(aux["status"], "measured_auxiliary")
        self.assertTrue(all(o["value"] > o["engineering_reference"]["value"] for o in aux["auxiliary_observations"]))
        self.assertLess(aux["joint_descent_fraction"], .7)
        self.assertIsNone(target(compute(case))["score"])

    def test_joint_descent_uses_actual_pts_duration_not_interval_count_or_zero_origin(self):
        case = fixture()
        times = [9000 + i * 20 if i <= 5 else 9100 + (i - 5) * 40 for i in range(12)]
        for i, timestamp in enumerate(times):
            case["frame_records"][i]["frame"]["timestamp_ms"] = timestamp
            case["primary_timeline"][i]["timestamp_ms"] = timestamp
            case["decoder_timeline"][i]["pts"] = timestamp
            for point in case["frame_records"][i]["poses"][0]["keypoints"]:
                if point["name"] in ("left_hip", "right_hip"):
                    point["y_px"] = 110 + min(i, 5)
        stage = case["annotations"]["stages"][0]
        for row in (stage, case["annotations"]["actions"][0]):
            row.update(start_ms=times[0], end_ms=times[-1])
        stage["local_preload_observation"]["start_frame"]["pts"] = times[0]
        stage["local_preload_observation"]["end_frame"]["pts"] = times[-1]
        aux = target(compute(case))["auxiliary_evidence"]
        self.assertEqual(aux["status"], "measured_auxiliary")
        self.assertEqual(aux["series"]["source_timestamps_ms"], times)
        self.assertEqual(aux["series"]["simultaneous_descent_intervals"], [True] * 5 + [False] * 6)
        self.assertAlmostEqual(aux["joint_descent_fraction"], 100 / 340)
        self.assertNotAlmostEqual(aux["joint_descent_fraction"], 5 / 11)

    def test_missing_frame_keeps_original_denominator_and_null_values(self):
        case = fixture(); case["frame_records"].pop(5); case["primary_timeline"].pop(5)
        aux = target(compute(case))["auxiliary_evidence"]
        self.assertEqual(aux["status"], "unavailable")
        self.assertEqual(aux["quality"]["total_frame_count"], 12)
        self.assertEqual(aux["quality"]["valid_frame_count"], 11)
        self.assertEqual(aux["series"]["source_frames"], list(range(12)))
        self.assertIsNone(aux["series"]["hip_y_camera_reference"][5])
        self.assertTrue(all(row["value"] is None for row in aux["auxiliary_observations"]))

    def test_above_seventy_percent_but_one_joint_gap_still_blocks_continuous_measurement(self):
        case = fixture()
        points = case["frame_records"][5]["poses"][0]["keypoints"]
        next(p for p in points if p["name"] == "left_ankle")["confidence"] = .1
        aux = target(compute(case))["auxiliary_evidence"]
        self.assertTrue(aux["quality"]["geometry_gate_passed"])
        self.assertFalse(aux["quality"]["continuous_joint_geometry_complete"])
        self.assertEqual(aux["status"], "unavailable")
        self.assertIsNone(aux["joint_descent_fraction"])

    def test_invalid_confidence_and_degenerate_knee_never_become_zero_change(self):
        for defect in (None, True, "coincident_joint"):
            case = fixture()
            points = {p["name"]: p for p in case["frame_records"][5]["poses"][0]["keypoints"]}
            if defect == "coincident_joint":
                for coordinate in ("x_px", "y_px"):
                    points["left_knee"][coordinate] = points["left_hip"][coordinate]
            else:
                points["left_ankle"]["confidence"] = defect
            aux = target(compute(case))["auxiliary_evidence"]
            self.assertEqual(aux["status"], "unavailable")
            self.assertTrue(all(row["value"] is None for row in aux["auxiliary_observations"]))
            self.assertIsNone(aux["joint_descent_fraction"])

    def test_selection_or_camera_epoch_change_cannot_be_bridged(self):
        for defect in ("camera", "selection"):
            case = fixture()
            if defect == "camera":
                case["frame_records"][5]["camera_motion"]["reference_epoch"] = 9
            else:
                case["primary_timeline"][5]["identity_ambiguous"] = True
            aux = target(compute(case))["auxiliary_evidence"]
            self.assertEqual(aux["status"], "unavailable")
            self.assertIsNone(aux["joint_descent_fraction"])

    def test_invalid_parent_stage_blocks_even_a_complete_local_window(self):
        case = fixture(count=15)
        stage = case["annotations"]["stages"][0]
        stage["local_preload_observation"]["end_frame"] = {key: case["decoder_timeline"][9][key]
            for key in ("frame_index", "pts", "time_base_num", "time_base_den")}
        case["frame_records"].pop(13); case["primary_timeline"].pop(13)
        aux = target(compute(case))["auxiliary_evidence"]
        self.assertIn("parent_gs_stage_unavailable", aux["reason_codes"])
        self.assertTrue(all(row["value"] is None for row in aux["auxiliary_observations"]))

    def test_cross_action_person_video_annotation_and_non_m01_card_rejected(self):
        for key, value in (("action_id", "other"), ("annotation_id", "other"), ("person_track_id", 2),
                           ("video_sha256", "b" * 64), ("indicator_id", "GS01-M03-03"), ("indicator_id", "GS02-M01-04")):
            case = fixture(); case["annotations"]["stages"][0]["local_preload_observation"][key] = value
            with self.assertRaises(ValueError):
                compute(case)

    def test_caller_cannot_supply_grade_quality_force_or_split_confirmation(self):
        for key, value in (("score", 100), ("quality", {"passed": True}), ("passed", True),
                           ("split_step_confirmed", True), ("force", 99), ("linked_fs01_event_id", "fs01-1")):
            case = fixture(); case["annotations"]["stages"][0]["local_preload_observation"][key] = value
            with self.assertRaisesRegex(ValueError, "annotation_schema_fields_invalid"):
                compute(case)

    def test_changed_catalogue_references_cannot_silently_reuse_the_old_engine(self):
        from rallymate_service import gs_card_execution as service
        catalogue = deepcopy(service.load_card_rules())
        target_rule = next(rule for rule in catalogue["cards"] if rule["indicator_id"] == "GS01-M01-03")
        target_rule["evaluation"]["auxiliary_observations"][0]["value"] = .9
        with patch.object(service, "load_card_rules", return_value=catalogue):
            with self.assertRaisesRegex(ValueError, "auxiliary_contract_mismatch"):
                compute(fixture())

    def test_exact_pts_timebase_and_window_containment_are_required(self):
        for change in ({"pts": 1}, {"time_base_num": 2, "time_base_den": 2000}, {"frame_index": 99}):
            case = fixture(); case["annotations"]["stages"][0]["local_preload_observation"]["start_frame"].update(change)
            with self.assertRaises(ValueError):
                compute(case)
        case = fixture(); case["annotations"]["stages"][0]["start_ms"] = 40
        with self.assertRaisesRegex(ValueError, "window_outside_source_stage"):
            compute(case)

    def test_boundary_uncertainty_does_not_expand_window_or_rescue_missing_endpoint(self):
        case = fixture(); before = target(compute(case))["auxiliary_evidence"]
        case["annotations"]["stages"][0]["local_preload_observation"]["uncertainty_ms"] = 100000
        after = target(compute(case))["auxiliary_evidence"]
        self.assertEqual(before["window"], after["window"])
        self.assertEqual(before["auxiliary_observations"], after["auxiliary_observations"])
        case["frame_records"].pop(0); case["primary_timeline"].pop(0)
        self.assertEqual(target(compute(case))["auxiliary_evidence"]["status"], "unavailable")

    def test_original_pts_and_full_observation_document_are_bound(self):
        case = fixture(); saved = deepcopy(case)
        report = compute(case); aux = target(report)["auxiliary_evidence"]
        self.assertEqual(case, saved)
        self.assertEqual(aux["source_annotation"]["document_sha256"], report["input"]["annotation_document_sha256"])
        self.assertEqual(aux["source_annotation"]["observation"], case["annotations"]["stages"][0]["local_preload_observation"])
        self.assertFalse(report["input"]["artifact_files_verified"])
        for i, row in enumerate(aux["source_frame_bindings"]):
            self.assertEqual(row["pts"], i * 40)
            self.assertEqual(row["selected_person_track_id"], 1)
            self.assertEqual(row["selection_epoch"], 1)

    def test_no_optional_binding_preserves_the_old_manual_result(self):
        case = fixture(); case["annotations"]["stages"][0].pop("local_preload_observation")
        report = compute(case)
        self.assertFalse(any("auxiliary_evidence" in i for c in report["cards"] for i in c["instances"]))

    def test_existing_real_fixtures_have_no_m01_labels_and_are_not_relabelled(self):
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((root / "tests/fixtures/gs-card-real-mit-preparation-v1.json").read_text(encoding="utf-8"))
        for case in payload["cases"]:
            self.assertTrue(all(stage["stage_code"].endswith("-M03") for stage in case["annotations"]["stages"]))
            self.assertEqual(validate_stage_annotations(case["annotations"]), case["annotations"])


if __name__ == "__main__":
    unittest.main()
