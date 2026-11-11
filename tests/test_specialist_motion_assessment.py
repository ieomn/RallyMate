from copy import deepcopy
import json
import unittest

import numpy as np

from rallymate_service.specialist_motion_assessment import assess_specialist_motion, SPECIALIST_METRICS


SHA = "c" * 64


def motion(preparation=.3, follow=.4):
    x = np.r_[np.linspace(0, -preparation, 8), np.linspace(-preparation, 1 - preparation, 8)[1:],
              np.linspace(1 - preparation, 1 - preparation + follow, 7)[1:]]
    wrist = np.column_stack([x, np.zeros(len(x)), np.zeros(len(x))])
    rows = [{"time": float(i * 40), "frame": i, "track": 4, "person": 1, "subject_id": "player-a",
             "reference": (2, 3), "local": {"right_wrist": point.copy()}} for i, point in enumerate(wrist)]
    return rows, wrist


def assess(data=None, family="return", context=None):
    rows, wrist = data or motion()
    proof = {"native_source_verified": True, "family_source_verified": True, "source_video_sha256": SHA,
             "camera_reference_verified": False, **(context or {})}
    return assess_specialist_motion(rows, wrist=wrist, prep_end=8, peak=14, side="right", family=family, source_context=proof)


def ball_context(**changes):
    return {"source_binding_verified": True, "video_sha256": SHA, "person_track_id": 4,
            "source_frame": 14, "selection_epoch": 2, "camera_reference_epoch": 3,
            "ball_track_id": 5, "contact_confirmed": True, **changes}


class SpecialistMotionTests(unittest.TestCase):
    def test_return_compact_paths_score_without_background_camera(self):
        result = assess()
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["metrics"]["specialist_compact_preparation"], (.3, 100.))
        self.assertAlmostEqual(result["metrics"]["specialist_short_follow_through"][0], .4)
        self.assertEqual(result["metrics"]["specialist_direct_drive"], (1., 100.))
        self.assertIn("coil", result["replace_metrics"])
        self.assertIn("follow_path", result["replace_metrics"])
        self.assertNotIn("head_control", result["replace_metrics"])
        self.assertNotIn("pattern_phase_continuity", result["replace_metrics"])
        self.assertNotIn("specialist_terminal_control", result["metrics"])
        self.assertEqual(result["evidence"]["striking_path_torso"], 1.)
        json.dumps(result, allow_nan=False)

    def test_long_swing_is_penalized_for_return_but_does_not_change_gs_rules(self):
        short, long = assess(), assess(motion(1.8, 2.))
        for key in ("specialist_compact_preparation", "specialist_short_follow_through"):
            self.assertGreater(short["metrics"][key][1], long["metrics"][key][1])
        self.assertEqual(assess(family="baseline")["metrics"], {})
        self.assertEqual(assess(motion(1.8, 2.), family="baseline")["replace_metrics"], [])

    def test_short_path_alone_does_not_classify_defense_or_net_play(self):
        for family in ("baseline", "defense", "volley", "overhead", "serve"):
            result = assess(family=family)
            self.assertEqual(result["metrics"], {}, family)
            self.assertEqual(result["family"], family)
        for family in ("volley", "defense"):
            self.assertEqual(assess(family=family, context={"ball_court_context": ball_context()})["metrics"], {})

    def test_native_identity_clock_and_source_remain_required(self):
        self.assertEqual(assess(context={"native_source_verified": False})["reason"], "native_source_binding_unverified")
        self.assertEqual(assess(context={"source_video_sha256": None})["reason"], "native_source_video_sha_missing")
        rows, wrist = motion()
        rows[10]["track"] = 99
        self.assertEqual(assess((rows, wrist))["reason"], "native_identity_or_reference_changed")
        rows, wrist = motion()
        rows[10]["time"] = rows[9]["time"]
        self.assertEqual(assess((rows, wrist))["reason"], "native_motion_time_gap")

    def test_missing_preparation_removes_only_that_contribution(self):
        result = assess(context={"preparation_start_clipped": True})
        self.assertNotIn("specialist_compact_preparation", result["metrics"])
        self.assertNotIn("coil", result["replace_metrics"])
        self.assertIn("specialist_short_follow_through", result["metrics"])
        rows, wrist = motion()
        wrist[2] = np.nan
        result = assess((rows, wrist))
        self.assertNotIn("specialist_compact_preparation", result["metrics"])
        self.assertIn("specialist_direct_drive", result["metrics"])

    def test_still_pose_cannot_earn_perfect_short_swing(self):
        rows, wrist = motion()
        wrist[:] = 0
        result = assess((rows, wrist))
        self.assertEqual(result["metrics"], {})
        self.assertEqual(result["reason"], "specialist_paths_unobserved_or_inactive")

    def test_net_classification_needs_calibrated_position_and_bounce_chronology(self):
        context = ball_context(court_calibration_verified=True, distance_to_net_court_length=.12,
                               no_precontact_bounce_confirmed=True, contact_height_zone="below_waist")
        result = assess(family="baseline", context={"ball_court_context": context})
        self.assertEqual(result["family"], "volley")
        self.assertEqual(result["context_profile"]["technique"], "NS04")
        self.assertEqual(result["status"], "scored")
        del context["no_precontact_bounce_confirmed"]
        self.assertEqual(assess(family="baseline", context={"ball_court_context": context})["family"], "baseline")
        context["confirmed_bounce_to_contact_ms"] = 100
        self.assertEqual(assess(family="baseline", context={"ball_court_context": context})["context_profile"]["technique"], "NS03")

    def test_defense_requires_ball_pressure_and_same_source_not_only_short_path(self):
        context = ball_context(defensive_pressure_observed=True, incoming_time_to_body_ms=180,
                               contact_to_body_torso=.5, body_jam_observed=True)
        result = assess(family="baseline", context={"ball_court_context": context})
        self.assertEqual(result["family"], "defense")
        self.assertEqual(result["context_profile"]["technique"], "DS06")
        context["video_sha256"] = "d" * 64
        self.assertEqual(assess(family="baseline", context={"ball_court_context": context})["metrics"], {})

    def test_overhead_is_not_inferred_from_raised_arm_or_changed_serve(self):
        context = ball_context(incoming_lob_observed=True, serve_toss_excluded=True)
        self.assertEqual(assess(family="serve", context={"ball_court_context": context})["family"], "serve")
        self.assertEqual(assess(family="baseline", context={"ball_court_context": context})["family"], "baseline")

    def test_metrics_are_registered_and_inputs_are_not_mutated(self):
        data = motion()
        original = deepcopy(data)
        result = assess(data)
        names = {item[0] for group in SPECIALIST_METRICS.values() for item in group}
        self.assertLessEqual(set(result["metrics"]), names)
        np.testing.assert_array_equal(data[1], original[1])
        for before, after in zip(data[0], original[0]):
            np.testing.assert_array_equal(before["local"]["right_wrist"], after["local"]["right_wrist"])


if __name__ == "__main__":
    unittest.main()
