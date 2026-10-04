from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from rallymate_scoring.stroke_candidates import _associate_rackets, _sample, detect_stroke_candidates
from rallymate_scoring.temporal_recognition import (
    HALPE26_JOINTS, RuleTemporalBackend, build_temporal_window, validate_recognition,
)
from test_stroke_analysis import make_frame, swing


def samples_from(frames):
    samples = []
    for record in frames:
        sample = _sample(record["poses"][0], record["frame"])
        _associate_rackets([sample], record["detections"])
        samples.append(sample)
    return samples


class TemporalWindowTests(unittest.TestCase):
    def test_halpe_order_matches_the_existing_pose_adapter_contract(self):
        path = Path(__file__).resolve().parents[1] / "src/rallymate_vision/pose/data/keypoint_schemas.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(list(HALPE26_JOINTS), [item["name"] for item in schema["formats"]["halpe26"]["keypoints"]])

    def test_model_layout_has_real_confidence_and_explicit_missing_mask(self):
        frames = swing()
        frames[4]["poses"][0]["keypoints"] = [p for p in frames[4]["poses"][0]["keypoints"] if p["name"] != "right_elbow"]
        window = build_temporal_window(samples_from(frames))
        data = window.model_input
        self.assertEqual(data["tensor_layout"], "M,T,V,C")
        self.assertEqual((len(data["keypoint"]), len(data["keypoint"][0]), len(data["keypoint"][0][0])), (1, 60, 26))
        wrist, elbow, toe = (HALPE26_JOINTS.index(name) for name in ("right_wrist", "right_elbow", "left_big_toe"))
        self.assertEqual(data["keypoint_score"][0][4][wrist], .95)
        self.assertTrue(data["observed_mask"][0][4][wrist])
        for index in (elbow, toe):
            self.assertEqual(data["keypoint"][0][4][index], [0.0, 0.0])
            self.assertFalse(data["observed_mask"][0][4][index])
            self.assertEqual(data["keypoint_score"][0][4][index], 0)
        json.dumps(data, allow_nan=False)

    def test_sampling_uses_real_milliseconds_and_never_fills_a_missing_observation(self):
        samples = samples_from([make_frame(i) for i in range(5)])
        for sample, time in zip(samples, [1000, 1030, 1130, 1210, 1260]):
            sample.time = time
            sample.timestamp_source = "decoder_pts"
        data = build_temporal_window(samples).model_input
        self.assertEqual(data["timestamp_ms"], [1000, 1050, 1100, 1150, 1200, 1250])
        self.assertEqual(data["source_timestamp_ms"], [1000, 1030, None, 1130, 1210, 1260])
        self.assertFalse(any(data["observed_mask"][0][2]))
        self.assertEqual(data["source_time_status"], "declared_source_timestamps")
        # Frame numbers have no bearing on elapsed time sampling.
        for sample in samples:
            sample.frame_index *= 3
        self.assertEqual(build_temporal_window(samples).model_input["timestamp_ms"], data["timestamp_ms"])

    def test_missing_confidence_does_not_turn_into_certainty(self):
        samples = samples_from([make_frame(0), make_frame(1)])
        samples[0].point_confidences = {}
        data = build_temporal_window(samples).model_input
        wrist = HALPE26_JOINTS.index("right_wrist")
        self.assertTrue(data["observed_mask"][0][0][wrist])
        self.assertFalse(data["confidence_available_mask"][0][0][wrist])
        self.assertEqual(data["keypoint_score"][0][0][wrist], 0)

    def test_identity_camera_time_and_normalization_boundaries_are_rejected(self):
        for field, value in (("track", 2), ("selection_epoch", 2), ("camera_reference_epoch", 1),
                             ("normalization_basis", "right_torso"), ("time", 0), ("time", 1000),
                             ("timestamp_source", "fps_fallback")):
            with self.subTest(field=field, value=value):
                samples = samples_from([make_frame(0), make_frame(1)])
                setattr(samples[1], field, value)
                with self.assertRaises(ValueError):
                    build_temporal_window(samples)

    def test_nonfinite_joint_is_masked_and_does_not_leak_nan(self):
        samples = samples_from([make_frame(0), make_frame(1)])
        samples[0].points["right_elbow"] = (float("nan"), 0)
        data = build_temporal_window(samples).model_input
        self.assertFalse(data["observed_mask"][0][0][HALPE26_JOINTS.index("right_elbow")])
        json.dumps(data, allow_nan=False)


class TemporalPipelineTests(unittest.TestCase):
    def test_rules_are_explicitly_untrained_and_phase_contract_is_serializable(self):
        result = detect_stroke_candidates(swing())
        metadata = result["motion_analysis"]["temporal_backend"]
        self.assertFalse(metadata["trained_tennis_model"])
        self.assertIsNone(metadata["weights"])
        self.assertEqual(metadata["accuracy_validation"], "not_established")
        episode = result["motion_analysis"]["families"]["baseline"]["episodes"][0]
        self.assertFalse(episode["temporal_recognition"]["anchor_is_contact"])
        for phase in episode["phases"]:
            self.assertFalse(phase["anchor_is_contact"])
            self.assertEqual(phase["boundary_semantics"], "estimated_motion_phase")
        self.assertEqual(episode["evidence"]["hand_evidence"]["scope"], "continuous_episode_context")
        json.dumps(result, allow_nan=False)

    def test_custom_backend_is_used_in_the_real_candidate_to_episode_path(self):
        class ReviewOnlyBackend(RuleTemporalBackend):
            calls = 0

            def metadata(self):
                return {**super().metadata(), "backend_id": "test-review-adapter"}

            def recognize(self, window, candidate, hand_evidence):
                self.calls += 1
                self.input = window.model_input
                result = super().recognize(window, candidate, hand_evidence)
                result["classification"] = {"label": "unclassified", "label_zh": "类型待确认",
                                            "status": "unclassified", "reason_zh": "测试后端保留未知。"}
                return result

        backend = ReviewOnlyBackend()
        result = detect_stroke_candidates(swing(), temporal_backend=backend)
        self.assertGreater(backend.calls, 0)
        self.assertEqual(backend.input["joint_layout"], "halpe26")
        episode = result["motion_analysis"]["families"]["baseline"]["episodes"][0]
        self.assertEqual(episode["classification"]["status"], "unclassified")
        self.assertEqual(episode["temporal_recognition"]["backend"]["backend_id"], "test-review-adapter")
        self.assertGreater(episode["metrics"]["wrist_path_torso"], .9)
        self.assertIsNotNone(episode["metrics"]["hip_line_change_deg"])

    def test_missing_elbow_keeps_wrist_motion_and_body_axes_but_no_elbow_measurement(self):
        frames = swing()
        for frame in frames:
            frame["poses"][0]["keypoints"] = [p for p in frame["poses"][0]["keypoints"] if p["name"] != "right_elbow"]
        result = detect_stroke_candidates(frames)
        self.assertEqual(result["candidate_count"], 1)
        episode = result["motion_analysis"]["families"]["baseline"]["episodes"][0]
        self.assertGreater(episode["metrics"]["wrist_path_torso"], .9)
        self.assertIsNone(episode["metrics"]["elbow_extension_deg"])
        self.assertIsNotNone(episode["metrics"]["hip_line_change_deg"])

    def test_persistent_missing_shoulder_or_hip_keeps_the_other_axis_and_wrist(self):
        for missing, absent_metric, observed_metric in (
            ("left_shoulder", "shoulder_line_change_deg", "hip_line_change_deg"),
            ("left_hip", "hip_line_change_deg", "shoulder_line_change_deg"),
        ):
            frames = swing()
            for frame in frames:
                frame["poses"][0]["keypoints"] = [p for p in frame["poses"][0]["keypoints"] if p["name"] != missing]
            with self.subTest(missing=missing):
                result = detect_stroke_candidates(frames)
                self.assertEqual(result["candidate_count"], 1)
                episode = result["motion_analysis"]["families"]["baseline"]["episodes"][0]
                self.assertEqual(episode["classification"]["status"], "unclassified")
                self.assertGreater(episode["metrics"]["wrist_path_torso"], .9)
                self.assertIsNone(episode["metrics"][absent_metric])
                self.assertEqual(episode["metrics"][observed_metric], 0)
                self.assertIsNone(episode["metrics"]["shoulder_hip_separation_change_deg"])
                self.assertEqual(episode["temporal_recognition"]["input_summary"]["normalization_basis"], "right_shoulder_right_torso")

    def test_partial_torso_normalization_never_invents_a_swing_from_a_static_pose(self):
        frames = [make_frame(i) for i in range(60)]
        for i, frame in enumerate(frames):
            if i % 3 == 0:
                frame["poses"][0]["keypoints"] = [p for p in frame["poses"][0]["keypoints"] if p["name"] != "left_shoulder"]
        self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)

    def test_missing_elbow_inside_a_motion_is_not_stitched_into_an_elbow_range(self):
        frames = swing()
        frames[28]["poses"][0]["keypoints"] = [p for p in frames[28]["poses"][0]["keypoints"] if p["name"] != "right_elbow"]
        episode = detect_stroke_candidates(frames)["motion_analysis"]["families"]["baseline"]["episodes"][0]
        self.assertIsNone(episode["metrics"]["elbow_extension_deg"])
        self.assertGreater(episode["metrics"]["wrist_path_torso"], .9)
        self.assertIsNotNone(episode["metrics"]["hip_line_change_deg"])

    def test_unavailable_or_broken_adapter_preserves_measurement_with_explicit_rule_fallback(self):
        for mode in ("absent", "throws", "invalid"):
            class BrokenBackend(RuleTemporalBackend):
                def metadata(self):
                    return {**super().metadata(), "backend_id": "test-broken-adapter"}

                def recognize(self, window, candidate, hand_evidence):
                    if mode == "throws":
                        raise RuntimeError("weights not loaded")
                    if mode == "absent":
                        return None
                    return {"contact_confirmed": True}

            with self.subTest(mode=mode):
                episode = detect_stroke_candidates(swing(), temporal_backend=BrokenBackend())["motion_analysis"]["families"]["baseline"]["episodes"][0]
                evidence = episode["temporal_recognition"]
                self.assertEqual(evidence["backend"]["backend_id"], "pose-racket-temporal-rules")
                self.assertEqual(evidence["requested_backend"]["backend_id"], "test-broken-adapter")
                self.assertIsNotNone(evidence["fallback_reason"])
                self.assertGreater(episode["metrics"]["wrist_path_torso"], .9)
                self.assertFalse(episode["contact_confirmed"])

    def test_non_mapping_adapter_output_uses_existing_invalid_output_fallback(self):
        class ListOutputBackend(RuleTemporalBackend):
            def metadata(self):
                return {**super().metadata(), "backend_id": "test-list-output-adapter"}

            def recognize(self, window, candidate, hand_evidence):
                return [{"classification": {"label": "forehand"}}]

        result = detect_stroke_candidates(swing(), temporal_backend=ListOutputBackend())
        episode = result["motion_analysis"]["families"]["baseline"]["episodes"][0]
        evidence = episode["temporal_recognition"]
        self.assertEqual(evidence["requested_backend"]["backend_id"], "test-list-output-adapter")
        self.assertEqual(evidence["backend"]["backend_id"], "pose-racket-temporal-rules")
        self.assertEqual(evidence["fallback_reason"], "backend_failed_or_invalid_output")
        self.assertGreater(episode["metrics"]["wrist_path_torso"], .9)
        self.assertFalse(episode["contact_confirmed"])

    def test_unrelated_earlier_hand_votes_cannot_hide_or_relabel_a_later_swing(self):
        idle = [make_frame(i) for i in range(180)]
        for frame in idle:
            left = next(p for p in frame["poses"][0]["keypoints"] if p["name"] == "left_wrist")
            x, y = left["x_px"], left["y_px"]
            frame["detections"][0]["bbox_px"] = [x - 3, y - 3, x + 15, y + 30]
        later = deepcopy(swing())
        for frame in later:
            frame["frame"]["index"] += 200
            frame["frame"]["processed_index"] += 200
            frame["frame"]["timestamp_ms"] += 10000
        result = detect_stroke_candidates(idle + later)
        self.assertEqual(result["candidate_count"], 1)
        episode = result["motion_analysis"]["families"]["baseline"]["episodes"][0]
        self.assertEqual(episode["classification"]["label"], "forehand")
        self.assertEqual(episode["evidence"]["hand_evidence"]["hand"], "right")
        self.assertGreater(episode["evidence"]["hand_evidence"]["start_ms"], 10000)

    def test_backend_cannot_invent_contact_timestamps_or_missing_stage_bounds(self):
        frames = swing()
        candidate = detect_stroke_candidates(frames)["candidates"][0]
        window = build_temporal_window(samples_from(frames))
        valid = RuleTemporalBackend().recognize(window, candidate, {"hand": "right"})
        for kind in ("contact", "timestamp", "stage", "phase_order"):
            result = deepcopy(valid)
            if kind == "contact":
                result["contact_confirmed"] = True
            elif kind == "timestamp":
                result["peak_ms"] += 1
            elif kind == "stage":
                result["phases"][0]["status"] = "unavailable"
            else:
                result["phases"] = list(reversed(result["phases"]))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_recognition(result, window)


if __name__ == "__main__":
    unittest.main()
