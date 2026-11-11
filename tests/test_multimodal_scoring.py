from copy import deepcopy
import json
import unittest

from rallymate_scoring.modality_features import build_multimodal_assessment as _build
from rallymate_scoring.tactical_analyzer import analyze_shot_placement, contextual_technique_profile, score_compact_control
from tests.test_shot_statistics import frame, bounce_frames, SHA


def build_multimodal_assessment(*args, **kwargs):
    return _build(*args, expected_video_sha256=SHA, **kwargs)


def episode(**values):
    return {"episode_id": "swing-1", "start_ms": 0, "peak_ms": 100, "end_ms": 200, "family": "baseline", **values}


class MultimodalScoringTests(unittest.TestCase):
    def test_ball_motion_uses_actual_time_and_does_not_claim_physical_speed(self):
        rows = [frame(i, (100 + i * 10, 100), racket=True) for i in range(5)]
        result = build_multimodal_assessment(iter(rows), [episode()])
        ball = result["per_episode"][0]["ball"]
        self.assertAlmostEqual(ball["tracks"][0]["median_image_speed"], .2)
        self.assertEqual(ball["observed_frame_count"], 5)
        self.assertIsNone(ball["physical_speed_m_s"])
        self.assertIsNone(ball["spin_rpm"])
        self.assertFalse(result["missing_modality_disables_pose_score"])
        self.assertFalse(result["per_episode"][0]["racket"]["sweet_spot_available"])
        json.dumps(result, allow_nan=False)

    def test_camera_compensation_removes_camera_translation(self):
        rows = [frame(i, (100 + i * 10, 100)) for i in range(5)]
        for i, row in enumerate(rows):
            row["camera_motion"]["matrix_to_reference"][0][2] = -i * 10
            row["camera_motion"]["status"] = "compensated"
        value = build_multimodal_assessment(rows, [episode()])["per_episode"][0]["ball"]["tracks"][0]["median_image_speed"]
        self.assertAlmostEqual(value, 0)

    def test_gap_epoch_and_ambiguous_ball_never_become_velocity(self):
        rows = [frame(0, (100, 100)), frame(4, (200, 100))]
        self.assertEqual(build_multimodal_assessment(rows, [episode()])["per_episode"][0]["ball"]["tracks"], [])
        rows = [frame(0, (100, 100)), frame(1, (200, 100), epoch=1)]
        self.assertEqual(build_multimodal_assessment(rows, [episode()])["per_episode"][0]["ball"]["tracks"], [])
        rows[1]["detections"].append(deepcopy(rows[1]["detections"][0]))
        self.assertEqual(build_multimodal_assessment(rows, [episode()])["per_episode"][0]["ball"]["ambiguous_frame_count"], 1)

    def test_confirmed_bounce_only_is_projected_as_landing(self):
        rows = bounce_frames(calibrated=True)
        candidate = {"kind": "bounce", "status": "candidate", "frame_index": 2, "timestamp_ms": 100,
                     "video_sha256": SHA, "evidence": {"ball_track_id": 7}}
        self.assertEqual(build_multimodal_assessment(rows, [episode()], shot_events=[candidate])["summary"]["confirmed_landing_count"], 0)
        candidate["status"] = "confirmed"
        result = build_multimodal_assessment(rows, [episode()], shot_events=[candidate])
        self.assertEqual(result["summary"]["confirmed_landing_count"], 1)
        point = result["per_episode"][0]["court"]["confirmed_landings"][0]
        self.assertAlmostEqual(point["x"], .11)
        self.assertAlmostEqual(point["y"], .26)

    def test_racket_keypoints_are_only_present_when_observed(self):
        rows = [frame(i, (100, 100), racket=True) for i in range(5)]
        rows[2]["detections"][1]["keypoints"] = [{"id": "RK005", "xy_px": [110, 130], "confidence": .9}]
        racket = build_multimodal_assessment(rows, [episode()])["per_episode"][0]["racket"]
        self.assertEqual(racket["keypoint_ids_observed"], ["RK005"])
        self.assertTrue(racket["sweet_spot_available"])
        self.assertFalse(racket["throat_available"])

    def test_placement_patterns_and_opponent_require_real_positions(self):
        context = {"episode_id": "s", "family": "baseline", "contact_position_court": {"x": .2, "y": .9},
                   "landing_position_court": {"x": .9, "y": .05},
                   "opponent_position_court": {"x": .2, "y": .05},
                   "landing_evidence": {"bounce_confirmed": True, "source_frame": 8}}
        result = analyze_shot_placement(context)
        codes = {tag["id"] for tag in result["tags"]}
        self.assertEqual(codes, {"TS08-01", "TS08-04", "TS08-06", "TS08-08"})
        self.assertEqual(result["module"], "TS03")
        context["landing_evidence"]["bounce_confirmed"] = False
        self.assertEqual(analyze_shot_placement(context)["tags"], [])

    def test_half_volley_is_not_low_volley_without_bounce_evidence(self):
        context = {"family": "volley", "contact_height_zone": "below_waist"}
        self.assertIsNone(contextual_technique_profile(context)["technique"])
        self.assertEqual(contextual_technique_profile({**context, "confirmed_bounce_to_contact_ms": 80})["technique"], "NS03")
        self.assertEqual(contextual_technique_profile({**context, "no_precontact_bounce_confirmed": True})["technique"], "NS04")

    def test_defensive_and_return_profiles_reward_compact_control(self):
        for family in ("defense", "return", "volley"):
            self.assertEqual(contextual_technique_profile({"family": family})["swing_policy"], "compact_control")
        self.assertEqual(contextual_technique_profile({"family": "defense", "running_rescue_observed": True})["technique"], "DS03")
        self.assertEqual(contextual_technique_profile({"family": "overhead", "overhead_chain_observed": True, "backward_approach": True})["technique"], "OS02")
        self.assertIsNone(contextual_technique_profile({"family": "serve"})["technique"])

    def test_cross_video_frames_and_events_are_rejected(self):
        rows = bounce_frames(calibrated=True)
        event = {"kind": "bounce", "status": "confirmed", "frame_index": 2, "timestamp_ms": 100,
                 "video_sha256": "b" * 64, "evidence": {"ball_track_id": 7}}
        self.assertEqual(build_multimodal_assessment(rows, [episode()], shot_events=[event])["summary"]["confirmed_landing_count"], 0)
        rows[2]["frame"]["video_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            build_multimodal_assessment(rows, [episode()])

    def test_unbound_position_and_unrelated_next_bounce_do_not_create_tactics(self):
        rows = bounce_frames(calibrated=True)
        candidate = {"id": "b", "kind": "bounce", "status": "confirmed", "frame_index": 3, "timestamp_ms": 150,
                     "video_sha256": SHA, "evidence": {"ball_track_id": 7}, "position_court": {"x": .9, "y": .01}}
        context = {"contact_position_court": {"x": .2, "y": .9},
                   "landing_position_court": {"x": .9, "y": .01},
                   "landing_evidence": {"bounce_confirmed": True, "source_frame": 3}}
        result = build_multimodal_assessment(rows, [episode(context_features=context)], shot_events=[candidate])
        self.assertEqual(result["tactics"]["measured_placement_count"], 0)
        actual = result["per_episode"][0]["court"]["confirmed_landings"][0]
        self.assertNotEqual(actual["x"], .9)  # Unproven supplied court position is ignored.

    def test_compact_control_reverses_large_swing_reward_and_rejects_still_pose(self):
        compact = score_compact_control(family="defense", preparation_path_body=.3, striking_path_body=1, follow_through_path_body=.4)
        large = score_compact_control(family="defense", preparation_path_body=1.7, striking_path_body=1, follow_through_path_body=1.8)
        self.assertGreater(compact["score"], large["score"])
        self.assertIsNone(score_compact_control(family="defense", preparation_path_body=0, striking_path_body=0, follow_through_path_body=0)["score"])

    def test_tactics_bind_same_video_player_contact_track_and_next_bounce(self):
        rows = bounce_frames(calibrated=True)
        hit = {"id": "h", "kind": "hit", "status": "confirmed", "frame_index": 2, "timestamp_ms": 100,
               "video_sha256": SHA, "person_track_id": 1, "evidence": {"ball_track_id": 7}}
        bounce = {"id": "b", "kind": "bounce", "status": "confirmed", "frame_index": 4, "timestamp_ms": 200,
                  "video_sha256": SHA, "evidence": {"ball_track_id": 7}}
        action = episode(video_sha256=SHA, person_track_id=1, context_features={"contact_event_id": "h",
            "contact_position_court": {"x": .2, "y": .9},
            "contact_position_evidence": {"video_sha256": SHA, "source_frame": 2,
                                          "person_track_id": 1, "source": "observed_ground_contact"}})
        result = build_multimodal_assessment(rows, [action], shot_events=[hit, bounce])
        self.assertEqual(result["tactics"]["measured_placement_count"], 1)
        hit["person_track_id"] = 2
        result = build_multimodal_assessment(rows, [action], shot_events=[hit, bounce])
        self.assertEqual(result["tactics"]["measured_placement_count"], 0)


if __name__ == "__main__":
    unittest.main()
