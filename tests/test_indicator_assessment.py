from __future__ import annotations

import copy
import json
import unittest

from rallymate_scoring.indicator_assessment import build_indicator_assessment
from rallymate_scoring.indicator_observations import analyze_indicator_observations
from rallymate_scoring.source_assessment import source_binding
from tests.test_indicator_observations import samples


def summary(label="forehand"):
    data = samples()
    phases = [{"phase": "preparation", "status": "measured", "start_ms": 0, "end_ms": 500}]
    episode = {"episode_id": "swing-1", "family": "baseline", "person_track_id": 1,
               "start_ms": 0, "end_ms": 500, "classification": {"label": label, "status": "rule_inferred"},
               "evidence": {"process_continuity": {"selection_epoch": 0, "camera_reference_epoch": None,
                                                   "camera_compensated": False, "normalization_basis": "full_torso"}},
               "process_motion": {"version": "process-motion-v1.1.0",
                                  "coordinate_system": "body_relative_image_x_right_y_down", "coordinate_unit": "torso_length",
                                  "source_observations": analyze_indicator_observations(data, phases, "right")}}
    return {"job_id": "video-1", "action_recognition": {"motion_analysis": {"families": {"baseline": {"episodes": [episode]}}}}}


def source():
    return {"version": "source-aligned-assessment-v1.0.0", "source_binding": source_binding(),
            "indicators": [{"indicator_id": "FS01-M05", "windows": [
                {"event_id": "fs01-1", "person_track_id": 1, "start_ms": 0, "end_ms": 500,
                 "measurements": [{"feature_name": "landing_proxy_to_next_fs02_ms", "label_zh": "衔接间隔",
                                   "source_ref": "CARD-FS:word/document.xml:P0220",
                                   "measurement_window": {"start_ms": 400, "end_ms": 400},
                                   "status": "measured", "value": 0, "unit": "ms"}]}]}]}


def legacy():
    return {"video_id": "video-1", "event_id": "fs02-1", "event_code": "FS02", "indicator_id": "FS02-M03",
            "features": [{"feature_name": "support_knee_extension_velocity_deg_s", "value": 30,
                          "valid": True, "unit": "deg/s", "source_frames": [2, 3, 4]}]}


def external_candidate_without_valid_measurement(identifier="FS03-M01", event_code="FS03"):
    record = legacy()
    record.update(indicator_id=identifier, event_code=event_code, event_id=f"external-{event_code}-1")
    record["features"][0].update(valid=False, value=None)
    return record


class IndicatorAssessmentTests(unittest.TestCase):
    def test_all_298_source_cards_have_explicit_status_and_source_binding(self):
        result = build_indicator_assessment({})
        self.assertEqual(result["counts"]["total"], 298)
        self.assertEqual(result["counts"]["formal_graded"], 0)
        self.assertEqual(result["counts"]["statuses"]["source_rule_conflict"], 3)
        self.assertEqual(result["counts"]["statuses"]["source_boundaries_missing"], 97)
        self.assertEqual(len({row["indicator_id"] for row in result["indicators"]}), 298)
        self.assertTrue(all(row["status_label_zh"] and row["explanation_zh"] and row["grade"]["reason_zh"] for row in result["indicators"]))
        self.assertEqual({d["id"] for d in result["source_binding"]["source_documents"]}, {"CARD-FS", "CARD-GS"})
        json.dumps(result, allow_nan=False)

    def test_related_gs_observations_are_neither_formal_stage_nor_complete_rule(self):
        result = build_indicator_assessment(summary())
        rows = {r["indicator_id"]: r for r in result["indicators"]}
        self.assertEqual(rows["GS01-M03-01"]["status"], "contextual_measurement")
        self.assertTrue(rows["GS01-M03-01"]["observation_ids"])
        self.assertEqual(rows["GS02-M02-01"]["observation_ids"], [])
        self.assertEqual(result["counts"]["motion_joint_measurement_types"], 13)
        self.assertTrue(all(o["full_source_rule_satisfied"] is False for o in result["observations"]))
        self.assertEqual(result["counts"]["formal_graded"], 0)
        self.assertEqual(result["counts"]["source_aligned_measured"], 0)

    def test_unclassified_swing_does_not_measure_all_gs_types(self):
        result = build_indicator_assessment(summary("unclassified"))
        self.assertEqual(result["counts"]["contextual_measured"], 0)
        self.assertEqual(len(result["observations"]), 13)
        self.assertTrue(all(not r["observation_ids"] for r in result["indicators"]))

    def test_unimplemented_types_are_distinct_from_supported_but_unobserved_types(self):
        unsupported = {"FS03", "FS04", "FS05", "FS06", "FS07", "FS08", "FS10", "GS04", "GS05"}
        for state in ({}, summary("unclassified")):
            result = build_indicator_assessment(state)
            missing = [row for row in result["indicators"]
                       if any(b["code"] == "event_type_recognizer_not_implemented" for b in row["blockers"])]
            self.assertEqual(len(missing), 134)
            self.assertEqual({row["event_code"] for row in missing}, unsupported)
            for row in missing:
                self.assertIn("当前算法尚未支持该动作类型识别", row["explanation_zh"])
                self.assertFalse(row["observation_ids"])
                if row["status"] == "source_boundaries_missing":
                    self.assertEqual(row["status_label_zh"], "动作阶段边界不完整")
                else:
                    self.assertEqual(row["status_label_zh"], "算法尚未支持该类型")
            rows = {row["indicator_id"]: row for row in result["indicators"]}
            self.assertEqual(rows["FS02-M01"]["status"], "not_observed")
            self.assertNotIn("当前算法尚未支持", rows["GS02-M02-01"]["explanation_zh"])
            self.assertNotIn("当前算法尚未支持", rows["GS03-M02-01"]["explanation_zh"])

    def test_missing_full_rule_implementation_is_explicit_on_every_card(self):
        result = build_indicator_assessment(summary())
        for row in result["indicators"]:
            codes = {b["code"] for b in row["blockers"]}
            self.assertIn("complete_grade_verifier_not_implemented", codes)
            self.assertIn("尚未实现并验证本项完整动作条件", row["grade"]["reason_zh"])
            self.assertNotIn("value", row["grade"])
        self.assertEqual(result["counts"]["formal_graded"], 0)
        self.assertEqual(result["counts"]["statuses"]["source_rule_conflict"], 3)
        self.assertEqual(result["counts"]["statuses"]["source_boundaries_missing"], 97)

    def test_missing_automatic_recognizer_does_not_discard_external_measurements(self):
        record = legacy()
        record.update(indicator_id="FS03-M01", event_code="FS03")
        record["features"][0]["value"] = 0
        result = build_indicator_assessment(summary(), [record])
        row = next(row for row in result["indicators"] if row["indicator_id"] == "FS03-M01")
        self.assertEqual(row["status"], "legacy_proxy_measured")
        self.assertEqual(row["observation_count"], 1)
        self.assertIn("已有 1 项次历史事件代理测量", row["explanation_zh"])
        self.assertIn("当前算法尚未支持", row["explanation_zh"])
        observed = next(o for o in result["observations"] if o["observation_id"] in row["observation_ids"])
        self.assertEqual(observed["value"], 0)
        self.assertEqual(observed["unit"], record["features"][0]["unit"])
        self.assertNotIn("value", row["grade"])

    def test_external_candidate_without_valid_measurement_keeps_evidence_gap_primary(self):
        expected = "已找到相关动作候选，但本项所需的有效关节、连续窗口或专用证据不足。"
        for identifier, event_code in (("FS03-M01", "FS03"), ("GS05-M01-01", "GS05")):
            with self.subTest(indicator_id=identifier):
                record = external_candidate_without_valid_measurement(identifier, event_code)
                result = build_indicator_assessment({"job_id": "video-1"}, [record])
                row = next(row for row in result["indicators"] if row["indicator_id"] == identifier)
                self.assertEqual(row["status"], "evidence_insufficient")
                self.assertEqual(row["status_label_zh"], "当前证据不足")
                gap = next(b for b in row["blockers"] if b["code"] == "event_type_recognizer_not_implemented")
                self.assertEqual(row["explanation_zh"], expected + gap["message_zh"])
                self.assertEqual(row["observation_count"], 0)
                self.assertEqual(row["observation_ids"], [])
                self.assertEqual(result["observations"], [])
                self.assertEqual(result["counts"]["formal_graded"], 0)
                self.assertNotIn("value", row["grade"])

    def test_external_candidate_keeps_source_priority_and_rejects_foreign_bindings(self):
        for identifier, event_code, status in (("GS04-M01-01", "GS04", "source_boundaries_missing"),
                                               ("GS01-M10-04", "GS01", "source_rule_conflict")):
            record = external_candidate_without_valid_measurement(identifier, event_code)
            result = build_indicator_assessment({"job_id": "video-1"}, [record])
            row = next(row for row in result["indicators"] if row["indicator_id"] == identifier)
            self.assertEqual(row["status"], status)
            self.assertNotIn("已找到相关动作候选", row["explanation_zh"])
        for change in ({"video_id": "foreign-video"}, {"event_code": "FS02"}):
            record = external_candidate_without_valid_measurement()
            record.update(change)
            result = build_indicator_assessment({"job_id": "video-1"}, [record])
            row = next(row for row in result["indicators"] if row["indicator_id"] == "FS03-M01")
            self.assertEqual(row["status"], "not_observed")
            self.assertEqual(row["status_label_zh"], "算法尚未支持该类型")
            self.assertEqual(result["observations"], [])

    def test_exact_zero_source_interval_is_preserved_without_fake_grade(self):
        result = build_indicator_assessment({}, source_assessment=source())
        row = next(r for r in result["indicators"] if r["indicator_id"] == "FS01-M05")
        self.assertEqual(row["status"], "measured_partial_rule")
        self.assertEqual(result["observations"][0]["value"], 0)
        self.assertEqual(row["grade"]["status"], "calibration_required")
        self.assertNotIn("value", row["grade"])

    def test_foreign_source_binding_or_video_is_rejected(self):
        data = copy.deepcopy(source())
        data["source_binding"]["document"]["sha256"] = "0" * 64
        self.assertEqual(build_indicator_assessment({}, source_assessment=data)["observations"], [])
        record = legacy()
        record["video_id"] = "foreign-video"
        self.assertEqual(build_indicator_assessment(summary(), [record])["counts"]["legacy_measured"], 0)

    def test_legacy_proxy_has_own_explicit_status_and_rejects_invalid_values(self):
        result = build_indicator_assessment(summary(), [legacy()])
        row = next(r for r in result["indicators"] if r["indicator_id"] == "FS02-M03")
        self.assertEqual(row["status"], "legacy_proxy_measured")
        record = legacy()
        record["features"][0]["value"] = float("nan")
        result = build_indicator_assessment(summary(), [record])
        self.assertEqual(result["counts"]["legacy_measured"], 0)
        self.assertEqual(next(r for r in result["indicators"] if r["indicator_id"] == "FS02-M03")["status"], "evidence_insufficient")
        json.dumps(result, allow_nan=False)

    def test_duplicate_conflicts_do_not_select_last_score_or_episode(self):
        a, b = legacy(), legacy()
        b["features"][0]["value"] = 90
        result = build_indicator_assessment(summary(), [a, b])
        self.assertEqual(result["counts"]["legacy_measured"], 0)
        data = summary()
        episode = copy.deepcopy(data["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0])
        episode["process_motion"]["source_observations"]["racket_side"] = "left"
        data["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"].append(episode)
        self.assertEqual(build_indicator_assessment(data)["observations"], [])

    def test_arithmetic_direction_is_explained_without_grading_good_or_bad(self):
        result = build_indicator_assessment(summary())
        wrist = next(o for o in result["observations"] if o["feature_name"] == "wrist_separation_torso")
        self.assertEqual(wrist["statistics"]["delta"], -.4)
        self.assertEqual(wrist["condition"]["status"], "observed_decrease")
        self.assertEqual(wrist["condition"]["semantics"], "arithmetic_observation_not_technical_grade")
        self.assertIn("不能据此确认", wrist["condition"]["reason_zh"])

    def test_missing_joint_returns_partial_real_windows_without_full_delta(self):
        data = samples()
        del data[5].points["right_wrist"]
        state = summary()
        process = state["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["process_motion"]
        process["source_observations"] = analyze_indicator_observations(data, [{"phase": "preparation", "status": "measured", "start_ms": 0, "end_ms": 500}], "right")
        result = build_indicator_assessment(state)
        wrist = next(o for o in result["observations"] if o["feature_name"] == "wrist_separation_torso")
        self.assertEqual(wrist["status"], "partial")
        self.assertIsNone(wrist["statistics"]["delta"])
        self.assertIsNone(wrist["condition"])
        self.assertEqual(len(wrist["windows"]), 2)

    def test_joint_windows_retain_exact_original_frames_and_counts(self):
        result = build_indicator_assessment(summary())
        wrist = next(o for o in result["observations"] if o["feature_name"] == "wrist_separation_torso")
        self.assertEqual(wrist["windows"][0]["source_frames"], list(range(11)))
        self.assertEqual(wrist["windows"][0]["sample_count"], 11)

    def test_complete_window_values_are_not_overwritten_by_an_unbound_phase_summary(self):
        state = summary()
        metric = state["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["process_motion"]["source_observations"]["phases"][0]["measurements"]["wrist_separation_torso"]
        metric["median"] = 999
        wrist = next(o for o in build_indicator_assessment(state)["observations"] if o["feature_name"] == "wrist_separation_torso")
        self.assertEqual(wrist["value"], wrist["windows"][0]["median"])
        self.assertNotEqual(wrist["value"], 999)

    def test_missing_duplicate_reversed_or_inconsistent_window_frames_are_rejected(self):
        for frames, count in ((None, 11), ([], 11), ([0, 0, 10], 3), ([10, 5, 0], 3),
                              ([0, True, 10], 3), ([0, 5, 10], 11), ([0, 10], 2)):
            state = summary()
            metric = state["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["process_motion"]["source_observations"]["phases"][0]["measurements"]["wrist_separation_torso"]
            metric["windows"][0].update(source_frames=frames, sample_count=count)
            result = build_indicator_assessment(state)
            self.assertFalse(any(o["feature_name"] == "wrist_separation_torso" for o in result["observations"]), (frames, count))

    def test_invalid_window_statistics_do_not_create_a_measured_card(self):
        for field, value in (("min", 100), ("max", -100), ("range", -1), ("mean", 100), ("median", -100)):
            state = summary()
            metric = state["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["process_motion"]["source_observations"]["phases"][0]["measurements"]["wrist_separation_torso"]
            metric["windows"][0][field] = value
            result = build_indicator_assessment(state)
            self.assertFalse(any(o["feature_name"] == "wrist_separation_torso" for o in result["observations"]), field)

    def test_rejected_local_window_cannot_leak_its_phase_summary(self):
        data = samples()
        del data[5].points["right_wrist"]
        state = summary()
        process = state["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["process_motion"]
        process["source_observations"] = analyze_indicator_observations(data, [{"phase": "preparation", "status": "measured", "start_ms": 0, "end_ms": 500}], "right")
        process["source_observations"]["phases"][0]["measurements"]["wrist_separation_torso"]["windows"][1]["delta"] = 999
        result = build_indicator_assessment(state)
        wrist = next(o for o in result["observations"] if o["feature_name"] == "wrist_separation_torso")
        self.assertEqual(len(wrist["windows"]), 1)
        self.assertEqual(wrist["windows"][0]["source_frames"], list(range(5)))
        self.assertIsNone(wrist["value"])
        self.assertTrue(all(v is None for v in wrist["statistics"].values()))

    def test_duplicate_or_overlapping_windows_cannot_repeat_one_piece_of_evidence(self):
        state = summary()
        metric = state["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["process_motion"]["source_observations"]["phases"][0]["measurements"]["wrist_separation_torso"]
        metric["windows"].append(copy.deepcopy(metric["windows"][0]))
        result = build_indicator_assessment(state)
        wrist = next(o for o in result["observations"] if o["feature_name"] == "wrist_separation_torso")
        self.assertEqual(wrist["status"], "partial")
        self.assertEqual(len(wrist["windows"]), 1)
        self.assertIsNone(wrist["value"])

    def test_process_trial_score_is_not_a_word_grade(self):
        result = build_indicator_assessment(summary(), process_evaluation={"dimensions": [{"id": "swing", "score": 100}]})
        self.assertEqual(result["counts"]["formal_graded"], 0)
        self.assertTrue(all(r["grade"]["status"] != "graded" for r in result["indicators"]))

    def test_rule_specificity_does_not_substitute_support_force_contact_or_source_conflicts(self):
        result = build_indicator_assessment(summary())
        rows = {row["indicator_id"]: row for row in result["indicators"]}
        observations = {row["observation_id"]: row for row in result["observations"]}
        self.assertEqual(rows["GS01-M10-04"]["observation_ids"], [])  # copied section title is not an executable rule
        self.assertEqual(rows["GS01-M08-03"]["observation_ids"], [])  # wrist/pose never proves ball-racket contact
        force_features = {observations[key]["feature_name"] for key in rows["GS01-M04-04"]["observation_ids"]}
        self.assertEqual(force_features, {"left_knee_flexion_deg", "right_knee_flexion_deg"})
        self.assertNotIn("ankle_distance_over_hip_width", force_features)
        self.assertNotIn("hip_inside_ankle_support_fraction", force_features)
        self.assertIn("force_unobservable", {b["code"] for b in rows["GS01-M04-04"]["blockers"]})
        forward_features = {observations[key]["feature_name"] for key in rows["GS01-M05-02"]["observation_ids"]}
        self.assertNotIn("hip_to_ankle_midpoint_torso", forward_features)  # ankle-relative displacement is not forward transfer

    def test_force_limitations_cover_push_off_and_weight_bearing_without_erasing_geometry(self):
        result = build_indicator_assessment(summary(), [legacy()])
        rows = {row["indicator_id"]: row for row in result["indicators"]}
        for identifier in ("FS02-M03", "FS03-M02", "FS04-M02", "FS06-M02", "FS08-M03"):
            self.assertIn("force_unobservable", {b["code"] for b in rows[identifier]["blockers"]}, identifier)
        self.assertEqual(rows["FS02-M03"]["status"], "legacy_proxy_measured")
        self.assertTrue(rows["FS02-M03"]["observation_ids"])

    def test_source_unit_and_locator_are_bound_to_exact_indicator(self):
        for key, value in (("source_ref", "CARD-FS:word/document.xml:P0115"), ("unit", "deg"), ("feature_name", "invented_measurement")):
            data = source()
            data["indicators"][0]["windows"][0]["measurements"][0][key] = value
            self.assertEqual(build_indicator_assessment({}, source_assessment=data)["observations"], [], key)

    def test_generic_backhand_slice_or_incorrect_coordinate_units_are_not_assigned_to_word_type(self):
        for label in ("backhand", "forehand_slice", "backhand_slice"):
            self.assertEqual(build_indicator_assessment(summary(label))["counts"]["contextual_measured"], 0)
        data = summary("backhand")
        episode = data["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]
        episode["classification"]["label_zh"] = "单手反手挥拍"
        result = build_indicator_assessment(data)
        self.assertTrue(any(r["observation_ids"] for r in result["indicators"] if r["event_code"] == "GS03"))
        self.assertTrue(all(not r["observation_ids"] for r in result["indicators"] if r["event_code"] != "GS03"))
        episode["process_motion"]["coordinate_unit"] = "px"
        self.assertEqual(build_indicator_assessment(data)["observations"], [])

    def test_local_windows_from_another_person_or_reference_cannot_be_borrowed(self):
        for key, value in (("person_track_id", 2), ("selection_epoch", 3), ("normalization_basis", "another_basis")):
            data = summary()
            source = data["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["process_motion"]["source_observations"]
            source["phases"][0]["measurements"]["wrist_separation_torso"]["windows"][0]["reference"][key] = value
            result = build_indicator_assessment(data)
            self.assertFalse(any(o["feature_name"] == "wrist_separation_torso" for o in result["observations"]))

    def test_bounded_evidence_keeps_full_counts_and_uniform_endpoint_examples(self):
        records = []
        for i in range(100):
            row = legacy()
            row["event_id"] = f"fs02-{i}"
            row["features"][0].update(value=100 - i, source_frames=[3 * i, 3 * i + 1, 3 * i + 2])
            records.append(row)
        result = build_indicator_assessment({"job_id": "video-1"}, records)
        self.assertEqual(result["counts"]["observations"], 100)
        self.assertEqual(result["counts"]["returned_observations"], 12)
        self.assertTrue(result["observation_selection"]["is_truncated"])
        self.assertEqual(result["observations"][0]["event_id"], "fs02-0")
        self.assertEqual(result["observations"][-1]["event_id"], "fs02-99")
        indicator = next(i for i in result["indicators"] if i["indicator_id"] == "FS02-M03")
        self.assertEqual(indicator["observation_count"], 100)
        self.assertEqual(indicator["returned_observation_count"], 12)
        self.assertEqual(indicator["status"], "legacy_proxy_measured")


if __name__ == "__main__":
    unittest.main()
