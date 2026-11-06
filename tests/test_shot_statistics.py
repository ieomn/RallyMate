"""Observed turns propose review windows; only explicit reviews create counts."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from rallymate_service.shot_statistics import (
    ShotStatisticsError, build_shot_statistics, validate_review,
)


SHA = "a" * 64


def frame(index, point, *, racket=False, epoch=0, calibrated=False):
    x, y = point
    ball = {"class_name": "ball", "track_id": 7, "confidence": .9,
            "bbox_px": [x - 3, y - 3, x + 3, y + 3]}
    detections = [ball]
    if racket:
        detections.append({"class_name": "racket", "track_id": 8, "confidence": .9,
                           "bbox_px": [100, 120, 120, 140]})
    court = {"status": "disabled"}
    if calibrated:
        court = {"status": "calibrated", "method": "manual_court_outer_doubles_corners",
                 "calibration_usable": True, "polygon_px": [[0, 0], [1000, 0], [1000, 500], [0, 500]],
                 "homography_image_to_court_normalized": [[.001, 0, 0], [0, .002, 0], [0, 0, 1]]}
    return {"frame": {"index": index, "processed_index": index, "width": 1000, "height": 500,
                       "timestamp_ms": index * 50, "video_sha256": SHA, "timestamp_source": "decoder_pts"},
            "camera_motion": {"status": "reference" if index == 0 else "stationary",
                              "compensation_valid": True, "reference_epoch": epoch,
                              "matrix_to_reference": [[1, 0, 0], [0, 1, 0]]},
            "detections": detections, "court": court}


def bounce_frames(**kwargs):
    return [frame(i, p, **kwargs) for i, p in enumerate(((100, 100), (105, 115), (110, 130), (115, 115), (120, 100)))]


class ShotStatisticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "frames.jsonl"

    def write(self, rows):
        self.path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def read(self, **kwargs):
        return build_shot_statistics(self.path, job_id="example", **kwargs)

    def review(self, event, **changes):
        return {k: changes.get(k, event.get(k)) for k in (
            "id", "kind", "status", "timestamp_ms", "frame_index", "position_image", "position_court")}

    def test_turn_is_candidate_not_confirmed_hit_or_landing(self):
        self.write(bounce_frames(calibrated=True))
        result = self.read()
        self.assertEqual(result["status"], "unreviewed")
        self.assertEqual(result["summary"]["candidate_bounce_count"], 1)
        self.assertEqual(result["summary"]["confirmed_bounce_count"], 0)
        self.assertEqual(result["summary"]["mapped_bounce_count"], 0)
        self.assertIsNone(result["events"][0]["position_court"])
        self.assertFalse(result["events"][0]["evidence"]["uses_interpolation"])
        self.assertFalse(any(key.startswith("_") for key in result["events"][0]))

    def test_confirmed_bounce_maps_only_same_frame_calibration(self):
        self.write(bounce_frames(calibrated=True))
        candidate = self.read()["events"][0]
        result = self.read(reviews=[self.review(candidate, status="confirmed")], revision=1)
        self.assertEqual(result["summary"]["confirmed_bounce_count"], 1)
        self.assertEqual(result["summary"]["mapped_bounce_count"], 1)
        self.assertEqual(result["events"][0]["position_court"],
                         {"x": .11, "y": .26, "source": "calibrated_ground_projection"})
        self.assertEqual(self.read()["events"][0]["status"], "candidate")  # Cache not mutated.
        echoed = validate_review(self.review(result["events"][0]), result)
        self.assertIsNone(echoed["position_court"])
        self.assertEqual(self.read(reviews=[echoed])["events"][0]["position_court"]["source"],
                         "calibrated_ground_projection")

    def test_hit_needs_racket_neighbor_and_review_before_counting(self):
        self.write(bounce_frames(racket=True))
        base = self.read()
        self.assertEqual(base["summary"]["candidate_hit_count"], 1)
        self.assertEqual(base["summary"]["confirmed_hit_count"], 0)
        review = self.review(base["events"][0], status="confirmed")
        confirmed = self.read(reviews=[review])
        self.assertEqual(confirmed["summary"]["confirmed_hit_count"], 1)
        review["status"] = "candidate"
        self.assertEqual(self.read(reviews=[review])["summary"]["confirmed_hit_count"], 0)
        review["status"] = "dismissed"
        dismissed = self.read(reviews=[review])
        self.assertEqual(dismissed["summary"]["candidate_hit_count"], 0)
        self.assertEqual(dismissed["summary"]["dismissed_count"], 1)

    def test_manual_bounce_supports_outside_court_without_clamping(self):
        self.write(bounce_frames())
        review = {"id": "manual-out", "kind": "bounce", "status": "confirmed", "timestamp_ms": 150,
                  "position_court": {"x": 1.1, "y": -.1, "source": "manual"}}
        result = self.read(reviews=[review])
        manual = next(e for e in result["events"] if e["id"] == "manual-out")
        self.assertEqual(manual["position_court"], {"x": 1.1, "y": -.1, "source": "manual"})
        self.assertIsNone(manual["frame_index"])
        self.assertEqual(result["summary"]["mapped_bounce_count"], 1)

    def test_camera_changes_long_gaps_multiple_balls_and_static_tracks_do_not_make_candidates(self):
        variants = []
        rows = bounce_frames()
        rows[2]["camera_motion"]["reference_epoch"] = 1
        variants.append(rows)
        rows = bounce_frames()
        for row in rows[2:]:
            row["frame"]["timestamp_ms"] += 1000
        variants.append(rows)
        rows = bounce_frames()
        rows[2]["detections"].append({**rows[2]["detections"][0], "track_id": 99})
        variants.append(rows)
        variants.append([frame(i, (100, 100)) for i in range(5)])
        rows = bounce_frames()
        rows[2]["camera_motion"]["compensation_valid"] = False
        variants.append(rows)
        rows = bounce_frames()
        for row in rows:
            row["frame"]["timestamp_source"] = "frame_index_fps"
        variants.append(rows)
        for rows in variants:
            self.write(rows)
            self.assertEqual(self.read()["events"], [])

    def test_unavailable_data_and_incomplete_live_line_are_not_total_zero_claims(self):
        self.write([])
        result = self.read()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["summary"]["count_semantics"], "review_confirmed_events_not_total_video")
        self.write(bounce_frames())
        with self.path.open("ab") as stream:
            stream.write(b'{"frame":')
        result = self.read(allow_partial=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["source"]["frame_count"], 5)

    def test_reviews_are_source_bound_and_cannot_rewrite_candidate_geometry(self):
        self.write(bounce_frames())
        base = self.read()
        event = base["events"][0]
        good = self.review(event, status="confirmed")
        for change in ({"id": "made-up"}, {"kind": "hit"}, {"timestamp_ms": 101},
                       {"frame_index": 99}, {"position_image": {"x": .7, "y": .7}},
                       {"position_court": {"x": 2., "y": .5}}, {"status": "observed"}):
            with self.subTest(change=change), self.assertRaises(ShotStatisticsError):
                validate_review({**good, **change}, base)
        with self.assertRaisesRegex(ShotStatisticsError, "source_video_mismatch"):
            self.read(expected_video_sha256="b" * 64)
        with self.assertRaisesRegex(ShotStatisticsError, "duplicate_review_id"):
            self.read(reviews=[good, good])

    def test_manual_review_bounds_and_nonfinite_coordinates(self):
        self.write(bounce_frames())
        base = self.read()
        good = {"id": "manual-hit", "kind": "hit", "status": "confirmed", "timestamp_ms": 50}
        for change in ({"timestamp_ms": 201}, {"timestamp_ms": float("nan")}, {"frame_index": 99},
                       {"position_court": {"x": .5, "y": .5}}, {"note": "x" * 501}):
            with self.subTest(change=change), self.assertRaises(ShotStatisticsError):
                validate_review({**good, **change}, base)

    def test_duplicate_confirmation_is_rejected_but_same_id_is_idempotent(self):
        self.write(bounce_frames(racket=True))
        event = self.read()["events"][0]
        review = self.review(event, status="confirmed")
        current = self.read(reviews=[review])
        self.assertEqual(validate_review(review, current)["id"], event["id"])
        manual = {"id": "manual-second", "kind": "hit", "status": "confirmed",
                  "timestamp_ms": event["timestamp_ms"] + 40}
        with self.assertRaisesRegex(ShotStatisticsError, "duplicate_confirmed"):
            validate_review(manual, current)
        with self.assertRaisesRegex(ShotStatisticsError, "duplicate_confirmed"):
            self.read(reviews=[review, manual])
        # A hit and a bounce have distinct semantics, even at nearby times.
        manual["kind"] = "bounce"
        result = self.read(reviews=[review, manual])
        self.assertEqual(result["summary"]["confirmed_hit_count"], 1)
        self.assertEqual(result["summary"]["confirmed_bounce_count"], 1)

    def test_same_id_does_not_bridge_camera_motion_and_calibration_not_carried(self):
        rows = bounce_frames(calibrated=True)
        rows[0]["camera_motion"]["matrix_to_reference"][0][2] = 50
        self.write(rows)
        base = self.read()
        if base["events"]:
            result = self.read(reviews=[self.review(base["events"][0], status="confirmed")])
            self.assertIsNone(result["events"][0]["position_court"])

    def test_persisted_historical_candidate_survives_rule_changes_but_new_unknown_id_does_not(self):
        self.write(bounce_frames(calibrated=True))
        original = self.read()
        saved = validate_review(self.review(original["events"][0], status="confirmed"), original)
        rows = bounce_frames(calibrated=True)
        for row in rows:
            row["detections"] = []
        self.write(rows)
        with self.assertRaisesRegex(ShotStatisticsError, "unknown_candidate"):
            self.read(reviews=[saved])
        with self.assertRaisesRegex(ShotStatisticsError, "source_binding"):
            self.read(reviews=[saved], trusted_reviews=True)
        result = self.read(reviews=[saved], trusted_reviews=True, expected_video_sha256=SHA)
        self.assertEqual(result["summary"]["confirmed_bounce_count"], 1)
        self.assertEqual(result["summary"]["mapped_bounce_count"], 0)
        historical = result["events"][0]
        self.assertIn("historical_candidate_source_changed", historical["reason_codes"])
        self.assertEqual(historical["frame_index"], saved["frame_index"])
        self.assertIsNone(historical["position_court"])
        for change in ({"timestamp_ms": saved["timestamp_ms"] + 1}, {"kind": "hit"},
                       {"frame_index": saved["frame_index"] + 1}, {"position_image": {"x": .7, "y": .7}}):
            with self.subTest(change=change), self.assertRaises(ShotStatisticsError):
                validate_review({**saved, **change}, result)
        with self.assertRaisesRegex(ShotStatisticsError, "unknown_candidate"):
            validate_review({**saved, "id": "shot-" + "f" * 20}, result)
        self.assertEqual(validate_review({**saved, "status": "candidate"}, result)["status"], "candidate")

    def test_historical_manual_court_mark_is_preserved_and_stale_projection_is_dropped(self):
        self.write(bounce_frames())
        base = self.read()
        saved = validate_review(self.review(base["events"][0], status="confirmed",
            position_court={"x": 1.1, "y": .6, "source": "manual"}), base)
        rows = bounce_frames()
        for row in rows:
            row["detections"] = []
        self.write(rows)
        result = self.read(reviews=[saved], trusted_reviews=True, expected_video_sha256=SHA)
        self.assertEqual(result["events"][0]["position_court"], saved["position_court"])
        self.assertEqual(result["summary"]["mapped_bounce_count"], 1)
        saved["position_court"]["source"] = "calibrated_ground_projection"
        result = self.read(reviews=[saved], trusted_reviews=True, expected_video_sha256=SHA)
        self.assertIsNone(result["events"][0]["position_court"])
        for change in ({"source": "manual"}, {"frame_index": None}, {"position_image": None}):
            with self.subTest(change=change), self.assertRaisesRegex(ShotStatisticsError, "persisted_candidate"):
                self.read(reviews=[{**saved, **change}], trusted_reviews=True, expected_video_sha256=SHA)
        with self.assertRaisesRegex(ShotStatisticsError, "source_video_mismatch"):
            self.read(reviews=[saved], trusted_reviews=True, expected_video_sha256="b" * 64)

    def test_manual_time_at_fractional_source_boundary_is_not_rounded_outside(self):
        base = {"events": [], "source": {"start_ms": 10.0001, "end_ms": 20.0009}}
        for value in (10.0001, 20.00089, 20.0009):
            review = validate_review({"id": "manual-time", "kind": "hit", "status": "confirmed",
                                      "timestamp_ms": value}, base)
            self.assertEqual(review["timestamp_ms"], value)
            self.assertLessEqual(review["timestamp_ms"], base["source"]["end_ms"])
            self.assertGreaterEqual(review["timestamp_ms"], base["source"]["start_ms"])


if __name__ == "__main__":
    unittest.main()
