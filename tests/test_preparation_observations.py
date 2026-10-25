from __future__ import annotations

import copy
import unittest

from rallymate_scoring.process_motion import analyze_process_motion
from rallymate_scoring.rotation_analysis import analyze_rotation
from rallymate_service.preparation_observations import preparation_candidate_observations, preparation_translation_observations
from rallymate_service.process_evaluation import build_process_evaluation
from tests.test_process_motion import samples, phases


def fixture():
    data, stages = samples(), phases()
    stages = [{**p, "boundary_semantics": "estimated_motion_phase", "anchor_is_contact": False} for p in stages]
    return {"episode_id": "motion-1", "person_track_id": 1, "start_ms": 0, "end_ms": 1500,
            "classification": {"label": "unclassified"}, "phases": stages,
            "evidence": {"process_continuity": {"selection_epoch": 0, "camera_reference_epoch": None,
                        "camera_compensated": False, "normalization_basis": "full_torso"}},
            "process_motion": analyze_process_motion(data, stages, "right"),
            "rotation_analysis": analyze_rotation(data, phases=stages)}


def translation_episode(*, model=False, phase_start=0, occluded=False, single_side=None, bilateral_hips=False):
    from tests.test_preparation_translation import fixture as translation_fixture
    from rallymate_scoring.pose_observations import sample_source_reference
    from rallymate_scoring.stroke_candidates import _sample
    data, stages = translation_fixture()
    if single_side:
        data = []
        for i in range(7):
            points = {f"{single_side}_{joint}": (290+i, y) for joint, y in
                      (("shoulder", 250), ("hip", 350), ("knee", 390), ("ankle", 430))}
            if bilateral_hips:
                other = "right" if single_side == "left" else "left"
                points.update({f"{other}_{joint}": (310+i, y) for joint, y in (("hip", 350), ("knee", 390), ("ankle", 430))})
            pose = {"person_track_id": 1, "keypoints": [{"name": name, "x_px": x, "y_px": y, "confidence": .9} for name, (x, y) in points.items()]}
            frame = {"index": i, "processed_index": i, "timestamp_ms": i*40, "timestamp_source": "decoder_pts",
                     "camera_motion_status": "compensated", "camera_motion": {"compensation_valid": True,
                     "reference_epoch": 0, "matrix_to_reference": [[1, 0, 0], [0, 1, 0]]}}
            data.append(_sample(pose, frame))
    stages[0]["start_ms"] = phase_start
    for sample in data:
        if model:
            sample.pose_source = "sam3d"
            sample.observation_semantics = "monocular_model_estimate"
            sample.point_confidences = {}
            sample.source_binding = {"video_sha256": "a"*64, "pts": 75000+sample.time, "time_base_num": 1, "time_base_den": 1000,
                "timestamp_ms": 75000+sample.time, "frame_index": sample.frame_index, "processed_index": sample.frame_index,
                "person_track_id": 1, "selection_epoch": 0, "camera_reference_epoch": 0}
    if occluded:
        del data[3].reference_points["left_knee"]
    return {"episode_id": "translation-test", "person_track_id": 1, "start_ms": 0, "end_ms": 240,
            "phases": stages, "evidence": {"process_continuity": {"selection_epoch": 0, "camera_reference_epoch": 0,
                "camera_compensated": True, "normalization_basis": data[0].normalization_basis, **sample_source_reference(data[0])}},
            "process_motion": analyze_process_motion(data, stages, "right"), "rotation_analysis": analyze_rotation(data, phases=stages)}


def translation_windows(episode):
    return [window for metric in episode["process_motion"]["preparation_translation"]["measurements"] for window in metric["windows"]]


class PreparationTranslationObservationTests(unittest.TestCase):
    def test_partial_sam_windows_use_window_provenance_and_preserve_both_clocks(self):
        for options, expected in (({"phase_start": 80}, 6), ({"occluded": True}, 7)):
            episode = translation_episode(model=True, **options)
            before = copy.deepcopy(episode)
            result = preparation_translation_observations([episode])
            self.assertEqual(len(result), expected)
            for row in result:
                self.assertEqual(row["source_timestamps_ms"], [75000+t for t in row["video_timestamps_ms"]])
                self.assertEqual(row["source_time_origin_ms"], 75000)
                self.assertIsNone(row["score"])
                self.assertFalse(row["full_source_rule_satisfied"])
            self.assertEqual(episode, before)

    def test_actual_single_side_normalization_retains_independently_available_joints(self):
        for side in ("left", "right"):
            for bilateral in (False, True):
                episode = translation_episode(single_side=side, bilateral_hips=bilateral)
                result = preparation_translation_observations([episode])
                self.assertEqual(len(result), 6 if bilateral else 1)
                self.assertEqual(len(result), len(translation_windows(episode)))
                self.assertTrue(all(row["reference"]["normalization_basis"] == f"{side}_shoulder_{side}_torso" for row in result))
                if not bilateral:
                    self.assertEqual(result[0]["feature_name"], f"{side}_knee_flexion_change_deg")
                    self.assertEqual(result[0]["value"], 0)
                changed = copy.deepcopy(episode)
                changed["evidence"]["process_continuity"]["normalization_basis"] = "full_torso"
                self.assertEqual(preparation_translation_observations([changed]), [])

    def test_corrupted_model_clocks_bindings_and_identity_never_project(self):
        for defect in ("double_clock", "timestamp", "processed_index", "decoder_downgrade", "video", "frame", "camera", "epoch"):
            episode = translation_episode(model=True)
            for window in translation_windows(episode):
                if defect == "double_clock":
                    window["source_timestamps_ms"] = [75000+2*t for t in window["video_timestamps_ms"]]
                    window["source_duration_ms"] *= 2
                    window["source_binding_end"].update(pts=window["source_timestamps_ms"][-1], timestamp_ms=window["source_timestamps_ms"][-1])
                elif defect == "timestamp": window["source_binding_end"]["timestamp_ms"] += 1000
                elif defect == "processed_index": del window["source_binding_start"]["processed_index"]
                elif defect == "decoder_downgrade":
                    window["time_basis"] = "decoder_timestamp_ms"
                    window["source_timestamps_ms"] = window["video_timestamps_ms"][:]
                else:
                    key = {"video": "video_sha256", "frame": "frame_index", "camera": "camera_reference_epoch", "epoch": "selection_epoch"}[defect]
                    window["source_binding_start"][key] = "b"*64 if defect == "video" else 99
            self.assertEqual(preparation_translation_observations([episode]), [], defect)

    def test_signed_values_are_preserved_without_score_or_stage_promotion(self):
        episode = translation_episode()
        for window in translation_windows(episode): window["value"] *= -1
        result = preparation_translation_observations([episode])
        self.assertEqual(len(result), 6)
        self.assertLess(next(row["value"] for row in result if row["feature_name"] == "hip_center_dx_torso"), 0)
        self.assertTrue(all(row["score"] is None and row["source_card_applicability"] == "unconfirmed" for row in result))


class PreparationObservationTests(unittest.TestCase):
    def test_sam_candidates_require_original_window_bindings_and_preserve_nonzero_pts(self):
        from tests.test_sam3d_scoring import sam_frame, swing
        from rallymate_scoring.stroke_candidates import detect_stroke_candidates
        records = [sam_frame(record) for record in swing()]
        for record in records:
            binding = record["poses"][0]["source_binding"]
            binding["pts"] += 75000
            binding["timestamp_ms"] += 75000
            record["frame"]["source_timestamp_ms"] = binding["timestamp_ms"]
        episode = detect_stroke_candidates(records)["motion_analysis"]["families"]["baseline"]["episodes"][0]
        accepted = preparation_candidate_observations([episode])
        self.assertTrue(accepted)
        for row in accepted:
            self.assertEqual(row["source_time_origin_ms"], 75000)
            self.assertEqual(row["source_binding_start"]["frame_index"], row["source_frames"][0])
            self.assertEqual(row["source_binding_start"]["timestamp_ms"], row["start_ms"]+75000)
            self.assertEqual(row["source_binding_end"]["timestamp_ms"], row["end_ms"]+75000)
            self.assertFalse(row["measurement_provenance"]["confidence_available"])
        for field, value in (("video_sha256", "b"*64), ("source_selection_epoch", 99), ("observation_semantics", "independently_observed")):
            changed = copy.deepcopy(episode)
            changed["evidence"]["process_continuity"][field] = value
            self.assertEqual(preparation_candidate_observations([changed]), [], field)
        changed = copy.deepcopy(episode)
        for row in changed["rotation_analysis"]["series"]:
            row["frame_index"] += 1000
        self.assertEqual(preparation_candidate_observations([changed]), [])

    def test_missing_camera_reference_rejects_without_raising(self):
        episode = fixture()
        del episode["evidence"]["process_continuity"]["camera_reference_epoch"]
        episode["temporal_recognition"] = {"input_summary": {"person_track_id": 1}}
        self.assertEqual(preparation_candidate_observations([episode]), [])

    def test_existing_trajectory_and_rotation_keep_values_frames_and_formulas_without_grading(self):
        episode = fixture()
        before = copy.deepcopy(episode)
        output = preparation_candidate_observations([episode])
        self.assertEqual(len(output), 9)
        for row in output:
            self.assertEqual(row["source_frames"], list(range(9)))
            self.assertEqual(row["source_timestamps_ms"], list(range(0, 401, 50)))
            self.assertEqual(row["person_track_id"], 1)
            self.assertIsNone(row["score"])
            self.assertFalse(row["full_source_rule_satisfied"])
            self.assertEqual(row["source_card_applicability"], "unconfirmed")
            self.assertNotIn("indicator_id", row)
            self.assertTrue(row["rule_zh"] and row["source_refs"])
            self.assertEqual(row["reference"]["selection_epoch"], 0)
        self.assertEqual(episode, before)

    def test_identity_clock_or_frame_discontinuity_cannot_be_joined_into_a_preparation_window(self):
        for field, value in (("person_track_id", 8), ("selection_epoch", 8), ("timestamp_ms", 500), ("frame_index", 100)):
            with self.subTest(field=field):
                episode = fixture()
                episode["rotation_analysis"]["series"][4][field] = value
                self.assertEqual(preparation_candidate_observations([episode]), [])

    def test_missing_shoulder_measurement_does_not_hide_hip_or_wrist(self):
        episode = fixture()
        phase = episode["rotation_analysis"]["phase_measurements"][0]
        for name in ("shoulder_line_change_deg", "peak_shoulder_angular_speed_deg_s"):
            phase["metric_evidence"][name]["status"] = "unavailable"
        rows = preparation_candidate_observations([episode])
        self.assertEqual(len(rows), 7)
        self.assertFalse(any("shoulder" in row["feature_name"] for row in rows))

    def test_phase_metric_and_evidence_must_agree_without_using_episode_summary(self):
        for mutation in ("value", "phase", "boundary", "segment", "duplicate"):
            with self.subTest(mutation=mutation):
                episode = fixture()
                rotation = episode["rotation_analysis"]
                phase = rotation["phase_measurements"][0]
                if mutation == "value":
                    phase["metric_evidence"]["hip_line_change_deg"]["value"] += 1
                elif mutation == "phase":
                    phase["phase"] = "acceleration"
                elif mutation == "boundary":
                    phase["end_ms"] = 800
                elif mutation == "segment":
                    rotation["series"][4]["hip_segment_id"] = 1
                else:
                    rotation["phase_measurements"].append(copy.deepcopy(phase))
                rows = preparation_candidate_observations([episode])
                self.assertFalse(any(row["feature_name"] == "hip_line_change_deg" for row in rows))
                self.assertEqual(sum("torso" in row["feature_name"] for row in rows), 5)

    def test_service_keeps_candidate_measurements_distinct_from_formal_source_card(self):
        episode = fixture()
        result = build_process_evaluation({"action_recognition": {"motion_analysis": {"families": {"baseline": {"episodes": [episode]}}}}})
        preparation = next(d for d in result["dimensions"] if d["id"] == "preparation")
        self.assertEqual(sum(row["id"].startswith("preparation_candidate_") for row in preparation["criteria"]), 9)
        self.assertIsNone(preparation["score"])
        self.assertEqual(preparation["scoring_rule_count"], 0)
        self.assertIn("正式阶段", preparation["status_reason_zh"])


if __name__ == "__main__":
    unittest.main()
