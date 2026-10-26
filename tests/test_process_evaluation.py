from __future__ import annotations

import copy
import json
import unittest

from rallymate_scoring.scoring_context import resolve_target_direction_context
from rallymate_service.process_evaluation import build_process_evaluation
from rallymate_service.user_demo import build_user_demo_result
from rallymate_service.footwork_review import build_footwork_review


def episode(identifier="motion-1", start=100, increase=.8, decrease=.7):
    phases = []
    for index, name in enumerate(("preparation", "acceleration", "follow_through")):
        phases.append({"phase": name, "status": "measured", "start_ms": start + index * 200,
            "end_ms": start + (index + 1) * 200, "duration_ms": 200,
            "wrist": {"direction_angle_deg": -30 if index == 0 else 45,
                      "speed_increase_fraction": 1 - decrease if index == 2 else increase,
                      "speed_decrease_fraction": decrease if index == 2 else 1 - increase,
                      "speed_sample_count": 7}})
    return {"episode_id": identifier, "person_track_id": 1, "start_ms": start, "end_ms": start + 600,
        "process_motion": {"version": "process-motion-v1.0.0", "status": "measured_2d",
            "coordinate_system": "body_relative_image_x_right_y_down", "phases": phases,
            "sequence": {"status": "measured", "semantics": "image_plane_speed_peak_order",
                "hip_peak_ms": start + 200, "shoulder_peak_ms": start + 250, "wrist_peak_ms": start + 300,
                "hip_to_shoulder_ms": 50, "shoulder_to_wrist_ms": 50},
            "direction": {"status": "measured", "phase": "acceleration",
                "semantics": "projected_path_consistency_not_direction_correctness",
                "direction_consistency_ratio": .6, "reversal_count": 1,
                "reversal_definition": "adjacent_observed_steps_turn_more_than_120_degrees"},
            "follow_through": {"status": "measured", "end_to_peak_speed_ratio": .2}}}


def summary(*episodes):
    return {"status": "completed", "job_id": "video-1",
        "minimum_scoring_loop": {"coordinate_contract": {"contract_version": "isotropic-frame-long-edge-v1.1.0"}},
        "action_recognition": {"motion_analysis": {"families": {"baseline": {"episodes": list(episodes)}}}}}


def dimensions(report):
    return {row["id"]: row for row in report["dimensions"]}


class ProcessEvaluationTests(unittest.TestCase):
    def test_preparation_joint_geometry_is_exposed_with_original_frames_and_no_score(self):
        from tests.test_indicator_assessment import summary as joint_summary

        report = build_process_evaluation(joint_summary())
        preparation = dimensions(report)["preparation"]
        rows = [row for row in preparation["criteria"] if row["id"].startswith("preparation_joint_")]
        self.assertEqual(len(rows), 13)
        self.assertEqual(preparation["measured_criterion_count"], 13)
        self.assertEqual(preparation["sample_count"], 1)
        self.assertEqual(preparation["status_reason"], "rule_not_implemented")
        self.assertIsNone(preparation["score"])
        self.assertEqual(preparation["scoring_rule_count"], 0)
        width = next(row for row in rows if row["feature_name"] == "ankle_distance_over_hip_width")
        self.assertEqual(width["value"], 2)
        self.assertEqual(width["source_frames"], list(range(11)))
        self.assertEqual(width["assessment_status"], "measurement_only")
        self.assertTrue(all(row["score"] is None and row["full_source_rule_satisfied"] is False for row in rows))

    def test_hidden_wrist_does_not_hide_independent_preparation_knees_or_join_windows(self):
        from rallymate_scoring.indicator_observations import analyze_indicator_observations
        from tests.test_indicator_assessment import summary as joint_summary
        from tests.test_indicator_observations import samples

        data, state = samples(), joint_summary()
        del data[5].points["right_wrist"]
        process = state["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["process_motion"]
        process["source_observations"] = analyze_indicator_observations(data, [{"phase": "preparation", "status": "measured", "start_ms": 0, "end_ms": 500}], "right")
        preparation = dimensions(build_process_evaluation(state))["preparation"]
        rows = [row for row in preparation["criteria"] if row.get("feature_name") == "wrist_separation_torso"]
        self.assertEqual([(row["start_ms"], row["end_ms"]) for row in rows], [(0, 200), (300, 500)])
        self.assertEqual([row["source_frames"] for row in rows], [list(range(5)), list(range(6, 11))])
        knee = next(row for row in preparation["criteria"] if row.get("feature_name") == "right_knee_flexion_deg")
        self.assertEqual((knee["start_ms"], knee["end_ms"]), (0, 500))
        self.assertIsNone(preparation["score"])

    def test_foreign_or_malformed_preparation_window_is_not_reported(self):
        from tests.test_indicator_assessment import summary as joint_summary

        for defect in ("reference", "frames", "version"):
            state = joint_summary()
            process = state["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["process_motion"]
            metric = process["source_observations"]["phases"][0]["measurements"]["wrist_separation_torso"]
            if defect == "reference":
                metric["windows"][0]["reference"]["person_track_id"] = 999
            elif defect == "frames":
                metric["windows"][0]["source_frames"] = []
            else:
                process["source_observations"]["version"] = "unverified"
            rows = dimensions(build_process_evaluation(state))["preparation"]["criteria"]
            self.assertFalse(any(row.get("feature_name") == "wrist_separation_torso" for row in rows), defect)

    def test_current_motion_partial_phase_preserves_independent_reference(self):
        item = episode()
        item["process_motion"]["version"] = "process-motion-v1.1.0"
        item["process_motion"]["status"] = "partial"
        item["process_motion"]["phases"][0].update(status="unavailable", wrist={})
        report = build_process_evaluation(summary(item))
        by_id = dimensions(report)
        self.assertIsNone(by_id["preparation"]["score"])
        self.assertIsNone(by_id["swing"]["score"])
        self.assertIsNone(by_id["follow_through"]["score"])
        self.assertEqual(by_id["swing"]["legacy_diagnostic"]["score"], 80.)
        self.assertEqual(by_id["follow_through"]["legacy_diagnostic"]["score"], 70.)
        self.assertEqual(report["display_dimension_ids"], ["movement", "preparation", "swing", "contact", "follow_through"])
        self.assertEqual(report["supplementary_dimension_ids"], ["transition"])

    def test_unknown_motion_version_cannot_supply_scores(self):
        item = episode()
        item["process_motion"]["version"] = "future-unsupported-version"
        report = build_process_evaluation(summary(item))
        self.assertTrue(all(row["score"] is None for row in report["dimensions"]))

    def test_missing_wrist_peak_keeps_independent_hip_shoulder_interval(self):
        item = episode()
        item["process_motion"]["version"] = "process-motion-v1.1.0"
        item["process_motion"]["sequence"].update(status="partial", wrist_peak_ms=None,
            shoulder_to_wrist_ms=None, ordering_resolved=True, timing_resolution_ms=30)
        rows = {row["id"]: row for row in dimensions(build_process_evaluation(summary(item)))["swing"]["criteria"]}
        self.assertEqual(rows["hip_to_shoulder_peak_delay"]["value"], 50)
        self.assertIsNone(rows["shoulder_to_wrist_peak_delay"]["value"])
        self.assertFalse(rows["hip_to_shoulder_peak_delay"]["ordering_resolved"])

    def test_real_motion_output_preserves_independent_pair_through_service_projection(self):
        from rallymate_scoring.process_motion import analyze_process_motion
        from tests.test_process_motion import samples, phases

        for missing in ("wrist_one_frame", "wrist_all_frames", "hip_one_frame"):
            with self.subTest(missing=missing):
                data = samples()
                if missing == "wrist_all_frames":
                    for sample in data:
                        del sample.points["right_wrist"]
                elif missing == "wrist_one_frame":
                    del data[10].points["right_wrist"]
                else:
                    del data[10].points["left_hip"]
                motion = analyze_process_motion(data, phases(), "right")
                self.assertEqual(motion["status"], "partial")
                self.assertEqual(motion["sequence"]["status"], "partial")
                self.assertFalse(motion["sequence"]["ordering_resolved"])
                item = {"episode_id": "real-motion", "person_track_id": 1, "start_ms": 0, "end_ms": 1500,
                        "process_motion": motion}
                rows = {row["id"]: row for row in dimensions(build_process_evaluation(summary(item)))["swing"]["criteria"]}
                observed = "shoulder_to_wrist_peak_delay" if missing == "hip_one_frame" else "hip_to_shoulder_peak_delay"
                absent = "hip_to_shoulder_peak_delay" if missing == "hip_one_frame" else "shoulder_to_wrist_peak_delay"
                self.assertEqual(rows[observed]["value"], 200)
                self.assertIsNone(rows[absent]["value"])
                self.assertIsNone(rows[observed]["score"])
                self.assertFalse(rows[observed]["ordering_resolved"])

    def test_empty_report_has_six_axes_and_never_fills_missing_with_zero(self):
        report = build_process_evaluation(summary())
        self.assertEqual(list(dimensions(report)), ["movement", "preparation", "swing", "contact", "follow_through", "transition"])
        self.assertIsNone(report["overall_score"])
        self.assertFalse(report["is_calibrated"])
        for dimension in report["dimensions"]:
            self.assertIsNone(dimension["score"])
            self.assertEqual(dimension["status"], "unavailable")
            self.assertEqual(dimension["scored_rule_count"], 0)
            self.assertTrue(dimension["criteria"])
            self.assertTrue(all(row["value"] is None and row["score"] is None for row in dimension["criteria"]))

    def test_reference_scores_change_with_real_curve_trends_not_confidence(self):
        low, high = episode(increase=.6, decrease=.55), episode(increase=.95, decrease=.9)
        low["confidence"], high["confidence"] = 1., .3
        a, b = dimensions(build_process_evaluation(summary(low))), dimensions(build_process_evaluation(summary(high)))
        self.assertEqual((a["swing"]["legacy_diagnostic"]["score"], b["swing"]["legacy_diagnostic"]["score"]), (60., 95.))
        self.assertEqual((a["follow_through"]["legacy_diagnostic"]["score"], b["follow_through"]["legacy_diagnostic"]["score"]), (55., 90.))
        self.assertTrue(all(d[axis]["score"] is None for d in (a, b) for axis in ("swing", "follow_through")))
        self.assertEqual(a["swing"]["scored_rule_count"], 0)
        self.assertEqual(a["swing"]["total_rule_count"], 6)
        self.assertIsNone(a["preparation"]["score"])
        self.assertEqual(a["preparation"]["status"], "partial")
        self.assertEqual(a["preparation"]["sample_count"], 1)
        self.assertTrue(all(row["source"] and row["rule_zh"] for row in a["swing"]["criteria"]))

    def test_legacy_diagnostic_preserves_curve_measurement_without_scoring_it(self):
        source = summary(episode())
        original = copy.deepcopy(source)
        result = build_process_evaluation(source)
        self.assertEqual(source, original)
        for identifier, rule, expected, bounds in (("swing", "acceleration_trend", 80, (300, 500)),
                                                    ("follow_through", "deceleration_trend", 70, (500, 700))):
            dimension = dimensions(result)[identifier]
            row = next(row for row in dimension["criteria"] if row["id"] == rule)
            self.assertEqual(row["value"], expected)
            self.assertEqual(row["unit"], "%")
            self.assertEqual((row["start_ms"], row["end_ms"]), bounds)
            self.assertEqual(row["sample_count"], 7)
            self.assertIsNone(row["score"])
            self.assertEqual(row["legacy_diagnostic"]["score"], expected)
            self.assertFalse(row["legacy_diagnostic"]["contributes_to_current_score"])
            self.assertIn("已退出当前评分", row["rule_zh"])
            self.assertEqual(dimension["scored_sample_count"], 0)
            self.assertGreater(dimension["measured_criterion_count"], 0)

    def test_source_grouping_does_not_turn_subcriterion_reference_into_full_axis_score(self):
        report = build_process_evaluation(summary(episode()))
        rows = dimensions(report)
        self.assertEqual([rows[key]["name_zh"] for key in report["display_dimension_ids"]],
                         ["移动质量", "准备与引拍", "挥拍协调", "触球控制", "随挥与衔接"])
        self.assertEqual([rows[key]["source_basis"]["card_count"] for key in report["display_dimension_ids"]],
                         [70, 78, 72, 25, 53])
        self.assertEqual((rows["swing"]["legacy_diagnostic"]["score"], rows["follow_through"]["legacy_diagnostic"]["score"]), (80., 70.))
        self.assertIsNone(rows["preparation"]["score"])
        self.assertEqual(rows["preparation"]["status_reason"], "rule_not_implemented")
        self.assertEqual(rows["movement"]["status_reason"], "independent_reference_missing")
        for row in report["dimensions"]:
            self.assertFalse(row["score_covers_all_source_cards"])
            self.assertTrue(row["score_scope_zh"])
            self.assertTrue(row["status_reason_zh"])
        self.assertEqual(rows["swing"]["score_scope"], "observation_only")
        self.assertEqual(rows["swing"]["status_reason"], "selection_biased_proxy_excluded")
        empty_preparation = dimensions(build_process_evaluation(summary()))["preparation"]
        self.assertEqual(empty_preparation["status_reason"], "rule_not_implemented")

    def test_contact_source_scope_is_control_not_landing_target_accuracy(self):
        contact = dimensions(build_process_evaluation(summary(episode())))["contact"]
        self.assertEqual(contact["name_zh"], "触球控制")
        self.assertEqual(contact["status_reason"], "rule_not_implemented")
        self.assertEqual({row["id"] for row in contact["criteria"]},
                         {"confirmed_contact", "contact_posture_control", "racket_face_control"})
        self.assertIsNone(contact["score"])
        self.assertEqual(contact["scoring_rule_count"], 0)
        self.assertNotIn("落点", contact["status_reason_zh"])
        self.assertNotIn("目标", " ".join(row["reason_zh"] for row in contact["criteria"]))

    def _footwork_observation(self):
        event = {"event_id": "fs01-1", "video_id": "video-1", "event_code": "FS01",
                 "person_track_id": 1, "start_ms": 100, "end_ms": 500}
        record = {**event, "indicator_id": "FS01-M02", "feature_status": "unavailable",
                  "quality_gate": {"measurement_allowed": True, "scoring_allowed": False},
                  "features": [{"feature_name": "hip_center_y_body", "value": .43219,
                                "unit": "body", "confidence": .8, "valid": True,
                                "source_frames": [3, 5, 8], "feature_version": "1.1.0-isotropic"},
                               {"feature_name": "left_knee_flexion_deg", "value": None,
                                "unit": "deg", "confidence": 0, "valid": False,
                                "source_frames": [], "reason": "required_keypoints_unavailable"}]}
        return event, record

    def test_movement_keeps_real_independent_measurement_when_other_feature_and_target_are_missing(self):
        event, record = self._footwork_observation()
        review = build_footwork_review([event], [record], video_id="video-1")
        movement = dimensions(build_process_evaluation(summary(), [record], footwork_review=review))["movement"]
        self.assertIsNone(movement["score"])
        self.assertEqual(movement["status"], "partial")
        self.assertEqual(movement["sample_count"], 1)
        self.assertEqual(movement["measured_criterion_count"], 1)
        self.assertEqual(movement["status_reason"], "independent_reference_missing")
        row = next(row for row in movement["criteria"] if row["status"] == "measured")
        self.assertEqual(row["id"], "footwork_FS01-M02_hip_center_y_body")
        self.assertEqual((row["value"], row["unit"], row["start_ms"], row["end_ms"]), (.4322, "body", 100, 500))
        self.assertEqual(row["source_frames"], [3, 5, 8])
        self.assertEqual(row["quality"]["aggregate_feature_status"], "unavailable")
        self.assertEqual(row["source"], "indicator_features.FS01-M02.features.hip_center_y_body")
        self.assertIsNone(row["score"])

    def test_movement_observations_revalidate_binding_gate_units_and_source_frames(self):
        for defect in ("different_video", "different_track", "bad_gate", "bad_unit", "nonfinite",
                       "no_frames", "unknown_feature", "conflicting_record", "conflicting_feature"):
            event, record = self._footwork_observation()
            records = [record]
            if defect == "different_video": record["video_id"] = "another-video"
            elif defect == "different_track": record["person_track_id"] = 2
            elif defect == "bad_gate": record["quality_gate"]["measurement_allowed"] = False
            elif defect == "bad_unit": record["features"][0]["unit"] = "m"
            elif defect == "nonfinite": record["features"][0]["value"] = float("nan")
            elif defect == "no_frames": record["features"][0]["source_frames"] = []
            elif defect == "unknown_feature": record["features"][0]["feature_name"] = "invented_score"
            elif defect == "conflicting_record":
                records.append(copy.deepcopy(record))
                records[-1]["features"][0]["value"] = .9
            else:
                record["features"].append({**record["features"][0], "value": .9})
            with self.subTest(defect=defect):
                # Even a review containing invented ready-made measurements
                # must be joined to and revalidated against the source records.
                review = {"episodes": [{**event, "indicators": [{"measurements": [{"value": 100}]}]}]}
                movement = dimensions(build_process_evaluation(summary(), records, footwork_review=review))["movement"]
                self.assertIsNone(movement["score"])
                self.assertEqual(movement["measured_criterion_count"], 0)

    def test_display_sampling_preserves_full_observation_counts_and_actual_endpoints(self):
        events, records = [], []
        for index in range(20):
            event, record = self._footwork_observation()
            event.update(event_id=f"fs01-{index}", start_ms=100 + 500 * index, end_ms=500 + 500 * index)
            record.update(event)
            record["features"][0]["value"] = index / 10
            events.append(event)
            records.append(record)
        review = build_footwork_review(events, records, video_id="video-1")
        # Model the review's independently disclosed artifact return limit.
        review["episode_count"] = 25
        review["is_truncated"] = True
        movement = dimensions(build_process_evaluation(summary(), records, footwork_review=review))["movement"]
        measured = [row for row in movement["criteria"] if row["status"] == "measured"]
        self.assertEqual(len(measured), 12)
        self.assertEqual((measured[0]["start_ms"], measured[-1]["start_ms"]), (100, 9600))
        self.assertEqual(movement["sample_count"], 20)
        self.assertEqual(movement["measured_criterion_count"], 20)
        self.assertEqual(movement["criteria_selection"]["full_count"], 21)
        self.assertEqual(movement["criteria_selection"]["returned_count"], 13)
        self.assertTrue(movement["criteria_selection"]["is_truncated"])
        self.assertEqual(movement["measurement_input_scope"]["available_event_count"], 25)
        self.assertEqual(movement["measurement_input_scope"]["returned_event_count"], 20)
        self.assertTrue(movement["measurement_input_scope"]["is_truncated"])
        self.assertEqual(movement["scoring_audit"]["measured_criterion_count"], 12)
        self.assertEqual(movement["scoring_audit"]["count_scope"], "validated_returned_criteria")
        self.assertIsNone(movement["score"])

    def test_direction_is_observed_without_calling_left_right_correct(self):
        first = episode()
        second = copy.deepcopy(first)
        second["process_motion"]["phases"][1]["wrist"]["direction_angle_deg"] = -135
        a, b = dimensions(build_process_evaluation(summary(first))), dimensions(build_process_evaluation(summary(second)))
        self.assertEqual(a["swing"]["score"], b["swing"]["score"])
        directions = [row for row in b["swing"]["criteria"] if row["id"] == "swing_direction"]
        self.assertEqual(directions[0]["value"], -135)
        self.assertIsNone(directions[0]["score"])

    def test_direction_path_observations_keep_actual_values_without_technical_score(self):
        swing = dimensions(build_process_evaluation(summary(episode())))["swing"]
        rows = {row["id"]: row for row in swing["criteria"]}
        for identifier, value, field in (("swing_direction_consistency", .6, "direction_consistency_ratio"),
                                         ("swing_direction_reversals", 1, "reversal_count")):
            row = rows[identifier]
            self.assertEqual(row["value"], value)
            self.assertIsNone(row["score"])
            self.assertEqual(row["status"], "measured")
            self.assertEqual((row["start_ms"], row["end_ms"]), (300, 500))
            self.assertEqual(row["source"], "action_recognition.motion_analysis.episodes.process_motion.direction." + field)
            self.assertIn("合理", row["reason_zh"])
        self.assertIsNone(swing["score"])
        self.assertEqual(swing["legacy_diagnostic"]["score"], 80.)
        self.assertEqual(swing["scored_rule_count"], 0)

    def test_direction_path_missing_invalid_and_boundary_values(self):
        cases = (("direction_consistency_ratio", 0, True), ("direction_consistency_ratio", 1, True),
                 ("direction_consistency_ratio", -.001, False), ("direction_consistency_ratio", 1.001, False),
                 ("direction_consistency_ratio", None, False), ("direction_consistency_ratio", float("inf"), False),
                 ("reversal_count", 0, True), ("reversal_count", 6, True), ("reversal_count", 7, False),
                 ("reversal_count", -1, False), ("reversal_count", 1.2, False), ("reversal_count", True, False))
        for field, value, valid in cases:
            with self.subTest(field=field, value=value):
                motion = episode()
                motion["process_motion"]["direction"][field] = value
                swing = dimensions(build_process_evaluation(summary(motion)))["swing"]
                identifier = "swing_direction_consistency" if field == "direction_consistency_ratio" else "swing_direction_reversals"
                row = next(row for row in swing["criteria"] if row["id"] == identifier)
                self.assertEqual(row["status"], "measured" if valid else "unavailable")
                self.assertEqual(row["value"], value if valid else None)
                self.assertIsNone(row["score"])
        for defect in ("unavailable", "wrong_phase", "bad_window", "missing"):
            motion = episode()
            if defect == "unavailable":
                motion["process_motion"]["direction"]["status"] = "unavailable"
            elif defect == "wrong_phase":
                motion["process_motion"]["direction"]["phase"] = "preparation"
            elif defect == "bad_window":
                motion["process_motion"]["phases"][1]["end_ms"] = 701
            else:
                del motion["process_motion"]["direction"]
            swing = dimensions(build_process_evaluation(summary(motion)))["swing"]
            rows = [row for row in swing["criteria"] if row["id"] in {"swing_direction_consistency", "swing_direction_reversals"}]
            self.assertTrue(all(row["value"] is None and row["status"] == "unavailable" for row in rows), defect)

    def test_missing_rule_does_not_reweight_other_observations_into_score(self):
        motion = episode()
        motion["process_motion"]["phases"][1]["wrist"]["speed_increase_fraction"] = None
        result = dimensions(build_process_evaluation(summary(motion)))
        self.assertIsNone(result["swing"]["score"])
        self.assertEqual(result["swing"]["status"], "partial")
        self.assertEqual(result["swing"]["scored_rule_count"], 0)
        self.assertIsNone(result["follow_through"]["score"])
        self.assertIsNotNone(result["follow_through"]["legacy_diagnostic"]["score"])

    def test_unknown_version_gate_bad_window_and_nonfinite_are_rejected(self):
        for modification in ("version", "status", "window", "nonfinite", "sample_count", "inconsistent_fraction"):
            with self.subTest(modification=modification):
                motion = episode()
                process = motion["process_motion"]
                if modification == "version":
                    process["version"] = "legacy"
                elif modification == "status":
                    process["status"] = "unavailable"
                elif modification == "window":
                    process["phases"][1]["end_ms"] = motion["end_ms"] + 1
                elif modification == "sample_count":
                    process["phases"][1]["wrist"]["speed_sample_count"] = 2
                elif modification == "inconsistent_fraction":
                    process["phases"][1]["wrist"]["speed_decrease_fraction"] = .8
                else:
                    process["phases"][1]["wrist"]["speed_increase_fraction"] = float("nan")
                report = build_process_evaluation(summary(motion))
                self.assertIsNone(dimensions(report)["swing"]["score"])
                trend = next(row for row in dimensions(report)["swing"]["criteria"] if row["id"] == "acceleration_trend")
                self.assertIsNone(trend["value"])
                self.assertIsNone(trend["legacy_diagnostic"]["score"])
                json.dumps(report, allow_nan=False)

    def test_contact_never_uses_pose_peak_or_run_level_ball_presence(self):
        source = summary(episode())
        source["coverage"] = {"ball_frame_fraction": 1, "racket_frame_fraction": 1}
        source["hit_statistics"] = {"candidate_count": 20}
        contact = dimensions(build_process_evaluation(source))["contact"]
        self.assertIsNone(contact["score"])
        self.assertEqual(contact["status"], "unavailable")

    def test_same_motion_in_return_family_is_not_double_counted_and_conflicts_fail_closed(self):
        motion = episode()
        source = summary(motion, episode("motion-2", 1000, increase=.6))
        source["action_recognition"]["motion_analysis"]["families"]["return"] = {"episodes": [copy.deepcopy(motion)]}
        report = build_process_evaluation(source)
        self.assertEqual(report["episode_count"], 2)
        self.assertEqual(dimensions(report)["swing"]["legacy_diagnostic"]["score"], 70.)
        self.assertEqual(dimensions(report)["swing"]["legacy_diagnostic"]["sample_count"], 2)
        self.assertEqual(dimensions(report)["swing"]["scored_sample_count"], 0)
        source["action_recognition"]["motion_analysis"]["families"]["return"]["episodes"][0]["process_motion"]["version"] = "conflict"
        report = build_process_evaluation(source)
        self.assertEqual(report["episode_count"], 1)
        self.assertEqual(dimensions(report)["swing"]["legacy_diagnostic"]["score"], 60.)

    def test_two_motion_spacing_is_observed_but_not_recovery_quality(self):
        first, second = episode(), episode("motion-2", 1000)
        transition = dimensions(build_process_evaluation(summary(first, second)))["transition"]
        self.assertEqual(transition["status"], "unavailable", "same track alone does not prove continuity")
        second["process_motion"]["transition_from_previous"] = {
            "status": "measured", "semantics": "observed_inter_motion_interval_not_readiness",
            "previous_episode_id": first["episode_id"], "start_ms": 700, "end_ms": 1000, "interval_ms": 300}
        transition = dimensions(build_process_evaluation(summary(first, second)))["transition"]
        self.assertIsNone(transition["score"])
        row = transition["criteria"][0]
        self.assertEqual(row["value"], 300)
        self.assertEqual(row["episode_id"], "motion-2")
        self.assertEqual((row["start_ms"], row["end_ms"]), (700, 1000))
        self.assertIn("不等于回位", row["reason_zh"])
        second["person_track_id"] = 2
        transition = dimensions(build_process_evaluation(summary(first, second)))["transition"]
        self.assertEqual(transition["status"], "unavailable")

    def test_subframe_peak_delay_reports_resolution_without_reliable_order_claim(self):
        motion = episode()
        motion["process_motion"]["sequence"].update({"timing_resolution_ms": 50, "ordering_resolved": False})
        swing = dimensions(build_process_evaluation(summary(motion)))["swing"]
        row = next(row for row in swing["criteria"] if row["id"] == "hip_to_shoulder_peak_delay")
        self.assertEqual(row["value"], 50)
        self.assertIn("不能区分可靠先后", row["reason_zh"])
        self.assertIsNone(row["score"])
        self.assertEqual(row["timing_resolution_ms"], 50)
        self.assertFalse(row["ordering_resolved"])

    def test_inconsistent_sequence_timestamps_cannot_produce_delay_observation(self):
        motion = episode()
        motion["process_motion"]["sequence"]["hip_to_shoulder_ms"] = 999
        swing = dimensions(build_process_evaluation(summary(motion)))["swing"]
        row = next(row for row in swing["criteria"] if row["id"] == "hip_to_shoulder_peak_delay")
        self.assertEqual(row["status"], "unavailable")
        self.assertIsNone(row["score"])

    def _target_evidence(self):
        event = {"video_id": "video-1", "event_id": "fs02-1", "event_code": "FS02", "person_track_id": 1,
                 "start_ms": 0, "end_ms": 300}
        launch = {"feature_name": "launch_direction_deg", "value": 90., "valid": True, "unit": "deg",
                  "confidence": .9, "source_frames": [0, 1, 2, 3]}
        observation = {**event, "observation_id": "target-1", "status": "accepted", "target_direction_deg": 0.,
                       "confidence": .9, "observer_id": "coach-1", "reviewer_id": "coach-2"}
        context = resolve_target_direction_context(event=event, launch_direction_feature=launch,
                                                   observation=observation, source_sha256="a" * 64)
        record = {**event, "indicator_id": "FS02-M02", "feature_status": "measured", "scoring_feature_status": "measured",
                  "features": [launch], "quality_gate": {"measurement_allowed": True, "scoring_allowed": True},
                  "scoring_context": context}
        return record, {"episodes": [event]}

    def test_movement_score_requires_independent_approved_target_and_bound_event(self):
        record, review = self._target_evidence()
        movement = dimensions(build_process_evaluation(summary(), [record], footwork_review=review))["movement"]
        self.assertEqual(movement["score"], 50.)
        self.assertEqual(movement["criteria"][0]["value"], 90.)
        self.assertIsNone(dimensions(build_process_evaluation(summary(), [record]))["movement"]["score"])
        for defect in ("no_gate", "mismatch", "old_coordinates", "conflict"):
            bad = copy.deepcopy(record)
            source = summary()
            records = [bad]
            if defect == "self_review":
                bad["scoring_context"]["reviewer_id"] = bad["scoring_context"]["observer_id"]
            elif defect == "no_gate":
                bad["quality_gate"]["scoring_allowed"] = False
            elif defect == "mismatch":
                bad["scoring_context"]["event_id"] = "other"
            elif defect == "old_coordinates":
                source["minimum_scoring_loop"]["coordinate_contract"] = {}
            else:
                records.append(copy.deepcopy(record))
                records[1]["features"][0]["value"] = 45.
            with self.subTest(defect=defect):
                movement = dimensions(build_process_evaluation(source, records, footwork_review=review))["movement"]
                self.assertIsNone(movement["score"])

    def test_bound_model_target_geometry_can_compute_without_confidence_or_reviewers(self):
        from rallymate_features.coordinates import pose_sequence_from_records
        from rallymate_features.schemas import model_geometry_evidence
        from tests.test_sam_footwork import sam_motion_fixture

        records, timeline = sam_motion_fixture()
        proof = model_geometry_evidence(pose_sequence_from_records(records, timeline), range(4))
        event = {"video_id": "video-1", "event_id": "fs02-1", "event_code": "FS02", "person_track_id": 1,
                 "start_ms": 0, "end_ms": 120, "track_diagnostics": {"source_track_ids": [7], "primary_player_id": 1}, **proof}
        launch = {"feature_name": "launch_direction_deg", "value": 90., "valid": True, "unit": "deg",
                  "confidence": None, "source_frames": [0, 1, 2, 3], **proof}
        observation = {**event, "observation_id": "explicit-target-1", "status": "provided", "target_direction_deg": 0.}
        context = resolve_target_direction_context(event=event, launch_direction_feature=launch,
            observation=observation, source_sha256="a" * 64)
        record = {**event, "indicator_id": "FS02-M02", "feature_status": "measured", "scoring_feature_status": "measured",
                  "features": [launch], "quality_gate": {"measurement_allowed": True, "scoring_allowed": True},
                  "scoring_context": context, "provenance": {"video_sha256": "a" * 64}}
        review = {"episodes": [event]}
        movement = dimensions(build_process_evaluation(summary(), [record], footwork_review=review))["movement"]
        self.assertEqual(movement["score"], 50.)
        row = movement["criteria"][0]
        self.assertIsNone(row["confidence"])
        self.assertFalse(row["measurement_provenance"]["visibility_verified"])
        for defect in ("confidence", "clock", "mask", "video"):
            broken = copy.deepcopy(record)
            if defect == "confidence": broken["features"][0]["confidence"] = .9
            elif defect == "clock": broken["features"][0]["source_window"]["source_bindings"][1]["pts"] += 20
            elif defect == "mask": broken["features"][0]["geometry_mask"]["left_hip"] = [False] * 4
            else: broken["provenance"]["video_sha256"] = "b" * 64
            self.assertIsNone(dimensions(build_process_evaluation(summary(), [broken], footwork_review=review))["movement"]["score"], defect)

    def test_demo_result_preserves_legacy_fields_and_adds_process_contract(self):
        result = build_user_demo_result(summary(episode()), [])
        self.assertEqual(result["result_version"], "rallymate-user-demo-result-v1.6.0")
        self.assertEqual(result["process_evaluation"]["version"], "process-evaluation-v2.0.0")
        self.assertIsNone(result["process_evaluation"]["overall_score"])
        self.assertIn("training_evaluation", result)
        self.assertIn("analysis_report", result)
        self.assertIn("final_demo_score", result)


if __name__ == "__main__":
    unittest.main()
