import copy
import math
import statistics
from types import SimpleNamespace
from pathlib import Path
import unittest

from rallymate_scoring.pose_only_swing_review import build_pose_only_review_candidates, EXPERIMENTAL, REGISTERED_IN_PRODUCTION


def observed_arc():
    samples, timeline = [], []
    for i in range(21):
        t = i / 20
        theta = math.radians(20*t)
        ease = (1-math.cos(math.pi*t))/2
        points = {"left_shoulder": (-.3*math.cos(theta), -.3*math.sin(theta)),
                  "right_shoulder": (.3*math.cos(theta), .3*math.sin(theta)),
                  "left_hip": (-.2, 1.), "right_hip": (.2, 1.),
                  "left_elbow": (-.3+.6*ease, .3), "left_wrist": (-1+2*ease, .5)}
        row = dict(frame_index=i, pts=i*50, time_base_num=1, time_base_den=1000,
                   video_sha256="a"*64, person_track_id=1, selection_epoch=2, camera_reference_epoch=3)
        samples.append(SimpleNamespace(time=i*50, frame_index=i, track=1, selection_epoch=2,
            camera_reference_epoch=3, camera_compensated=True, timestamp_source="decoder_pts",
            normalization_basis="full_torso", reference_points=points,
            point_confidences={name:.9 for name in points}, source_binding=dict(row)))
        timeline.append(row)
    return samples, timeline


class PoseOnlyReviewTests(unittest.TestCase):
    def test_experiment_is_unregistered_and_not_imported_by_production(self):
        self.assertTrue(EXPERIMENTAL)
        self.assertFalse(REGISTERED_IN_PRODUCTION)
        root = Path(__file__).resolve().parents[1]
        for path in (root/"src").rglob("*.py"):
            if path.name != "pose_only_swing_review.py":
                self.assertNotIn("pose_only_swing_review", path.read_text(encoding="utf-8"), str(path))

    def test_continuous_arc_only_produces_unclassified_unscorable_review(self):
        samples, timeline = observed_arc()
        report = build_pose_only_review_candidates(samples, timeline)
        self.assertEqual(len(report["candidates"]), 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["wrist_side"], "left")
        self.assertEqual(candidate["status"], "manual_review_required")
        self.assertIsNone(candidate["technique_label"])
        self.assertIsNone(candidate["dominant_hand"])
        self.assertFalse(candidate["contact_confirmed"])
        self.assertFalse(candidate["automatic_scoring_allowed"])
        for key in ("score", "proxy_grade", "original_grade"):
            self.assertIsNone(candidate[key])
        self.assertGreaterEqual(candidate["measurements"]["elbow_wrist_net_cosine"], .25)
        self.assertIn("raw_body_relative_wrist", candidate["evidence"])
        self.assertIn("filtered_body_relative_wrist", candidate["evidence"])
        context = candidate["evidence"]["filter_context"]
        for joint in ("wrist", "elbow"):
            raw = context["raw_body_relative_"+joint]
            rebuilt = []
            for i in range(context["window_offset"], context["window_offset"]+context["window_count"]):
                rebuilt.append(tuple(statistics.median(p[axis] for p in raw[max(0,i-1):i+2]) for axis in (0,1)))
            self.assertEqual(rebuilt, candidate["evidence"]["filtered_body_relative_"+joint])
        normalization = candidate["evidence"]["segment_normalization"]
        self.assertEqual(statistics.median(normalization["torso_scale_reference_px_values"]),
                         candidate["evidence"]["segment_fixed_torso_scale_reference_px"])

    def test_wrist_gesture_without_elbow_cooperation_is_rejected(self):
        samples, timeline = observed_arc()
        for sample in samples:
            sample.reference_points["left_elbow"] = (-.3, .3)
        self.assertEqual(build_pose_only_review_candidates(samples, timeline)["candidates"], [])

    def test_stationary_shoulder_axis_does_not_pass_motion_conditions(self):
        samples, timeline = observed_arc()
        for sample in samples:
            sample.reference_points.update(left_shoulder=(-.3,0), right_shoulder=(.3,0))
        self.assertEqual(build_pose_only_review_candidates(samples, timeline)["candidates"], [])

    def test_translation_alone_does_not_make_arm_motion(self):
        samples, timeline = observed_arc()
        initial = dict(samples[0].reference_points)
        for i, sample in enumerate(samples):
            sample.reference_points = {k:(x+i*.2,y) for k,(x,y) in initial.items()}
        self.assertEqual(build_pose_only_review_candidates(samples, timeline)["candidates"], [])

    def test_overhead_motion_is_not_admitted_by_waist_level_proxy(self):
        samples, timeline = observed_arc()
        for sample in samples:
            x,_ = sample.reference_points["left_wrist"]
            sample.reference_points["left_wrist"] = (x,-.8)
        self.assertEqual(build_pose_only_review_candidates(samples, timeline)["candidates"], [])

    def test_original_frame_gap_or_pts_reset_refuses_timeline(self):
        samples, timeline = observed_arc()
        for broken in (timeline[:10]+timeline[11:], [*timeline[:10],dict(timeline[10],pts=0),*timeline[11:]]):
            result = build_pose_only_review_candidates(samples, broken)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["candidates"], [])

    def test_missing_joint_low_confidence_or_pose_breaks_filter_segments(self):
        for change in ("missing", "confidence", "pose"):
            samples, timeline = observed_arc()
            if change == "missing":
                del samples[10].reference_points["left_wrist"]
            elif change == "confidence":
                samples[10].point_confidences["left_wrist"] = .1
            else:
                samples.pop(10)
            report = build_pose_only_review_candidates(samples, timeline)
            self.assertEqual(report["diagnostics"]["source_frame_count"], 21)
            for candidate in report["candidates"]:
                self.assertFalse(candidate["start_frame"] < 10 < candidate["end_frame"])
                self.assertFalse(candidate["evidence"]["interpolation_used"])

    def test_unknown_selection_frame_stays_in_denominator_and_breaks(self):
        samples, timeline = observed_arc()
        timeline[10] = dict(timeline[10], selection_epoch=None)
        report = build_pose_only_review_candidates(samples, timeline)
        self.assertEqual(report["diagnostics"]["source_frame_count"], 21)
        for candidate in report["candidates"]:
            self.assertFalse(candidate["start_frame"] < 10 < candidate["end_frame"])

    def test_clock_hash_camera_or_selection_disagreement_cannot_be_smoothed(self):
        for attr, value in (("camera_compensated",False), ("timestamp_source","fps_fallback"),
                            ("selection_epoch",9), ("normalization_basis","partial_torso")):
            samples, timeline = observed_arc()
            for sample in samples:
                setattr(sample,attr,value)
            self.assertEqual(build_pose_only_review_candidates(samples,timeline)["candidates"],[])
        samples,timeline=observed_arc()
        for sample in samples:
            sample.source_binding["video_sha256"]="b"*64
        self.assertEqual(build_pose_only_review_candidates(samples,timeline)["candidates"],[])

    def test_existing_other_wrist_or_other_identity_never_suppresses(self):
        samples,timeline=observed_arc()
        reference=build_pose_only_review_candidates(samples,timeline)["candidates"][0]
        self.assertEqual(build_pose_only_review_candidates(samples,timeline,[reference])["candidates"],[])
        for key,value in (("wrist_side","right"),("video_sha256","b"*64),("selection_epoch",4),
                          ("person_track_id",2),("camera_reference_epoch",5)):
            other=dict(reference,**{key:value})
            self.assertEqual(len(build_pose_only_review_candidates(samples,timeline,[other])["candidates"]),1)
        legacy={k:reference[k] for k in ("start_ms","end_ms","wrist_side")}
        self.assertEqual(len(build_pose_only_review_candidates(samples,timeline,[legacy])["candidates"]),1)

    def test_opposite_wrist_candidates_are_not_suppressed_by_each_other(self):
        samples,timeline=observed_arc()
        for sample in samples:
            for joint in ("wrist","elbow"):
                x,y=sample.reference_points["left_"+joint]
                sample.reference_points["right_"+joint]=(-x,y)
                sample.point_confidences["right_"+joint]=.9
        result=build_pose_only_review_candidates(samples,timeline)
        self.assertEqual({c["wrist_side"] for c in result["candidates"]},{"left","right"})


if __name__ == "__main__":
    unittest.main()
