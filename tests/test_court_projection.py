from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from rallymate_vision.court_projection import build_court_projection, validated_calibration
from rallymate_vision.trajectory import build_trajectory_preview
from tests.test_trajectory import _frame
from tests.test_manual_court import camera


def court():
    return {"status": "calibrated", "calibration_usable": True,
            "polygon_px": [[100, 100], [900, 100], [900, 450], [100, 450]],
            "homography_image_to_court_normalized": [[1/800, 0, -100/800], [0, 1/350, -100/350], [0, 0, 1]]}


class CourtProjectionTests(unittest.TestCase):
    def preview(self, rows):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "frames.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            return build_trajectory_preview(path)

    def test_pixel_matrix_uses_pixel_input_and_retains_zero(self):
        row = _frame(0, ball_x=.1)
        row["detections"][0]["center_normalized"] = [.1, .2]
        row["court"] = court()
        result = self.preview([row])["court_projection"]
        self.assertEqual(result["status"], "calibrated")
        self.assertEqual(result["points"][0]["x"], 0)
        self.assertEqual(result["points"][0]["y"], 0)
        self.assertEqual(result["semantics"], "ground_plane_projection_not_ball_landing")

    def test_each_frame_needs_its_own_calibration(self):
        rows = [_frame(i, ball_x=.2+i*.01) for i in range(3)]
        rows[0]["court"] = court()
        rows[2]["court"] = court()
        result = self.preview(rows)["court_projection"]
        self.assertEqual([p["timestamp_ms"] for p in result["points"]], [0, 200])
        self.assertNotEqual(result["points"][0]["segment_id"], result["points"][1]["segment_id"])

    def test_disabled_and_visible_region_do_not_create_court_tracks(self):
        for observation in ({}, {"status": "detected", "region_usable": True}, {**court(), "calibration_usable": False}):
            row = _frame(0, ball_x=.5)
            row["court"] = observation
            result = self.preview([row])["court_projection"]
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["points"], [])

    def test_invalid_matrices_and_corner_correspondences_are_rejected(self):
        mutations = [
            {"homography_image_to_court_normalized": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
            {"homography_image_to_court_normalized": [[0]*3]*3},
            {"homography_image_to_court_normalized": [[float('nan'), 0, 0], [0, 1, 0], [0, 0, 1]]},
            {"polygon_px": [[100, 100], [900, 450], [900, 100], [100, 450]]},
            {"polygon_px": [[100, 100]]*4},
            {"polygon_px": [[-1, 100], [900, 100], [900, 450], [100, 450]]},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertIsNone(validated_calibration({**court(), **mutation}, 1000, 500))

    def test_out_of_court_point_is_not_clamped_to_boundary(self):
        row = _frame(0, ball_x=.05)
        row["court"] = court()
        result = self.preview([row])["court_projection"]
        self.assertLess(result["points"][0]["x"], 0)

    def test_projection_is_resolution_invariant(self):
        row = _frame(0, ball_x=.3)
        row["court"] = court()
        enlarged = deepcopy(row)
        enlarged["frame"].update(width=2000, height=1000)
        enlarged["court"]["polygon_px"] = [[x*2, y*2] for x, y in row["court"]["polygon_px"]]
        for i in range(3):
            for j in range(2):
                enlarged["court"]["homography_image_to_court_normalized"][i][j] /= 2
        a, b = [self.preview([item])["court_projection"]["points"][0] for item in [row, enlarged]]
        self.assertEqual((a["x"], a["y"]), (b["x"], b["y"]))

    def test_no_interpolated_points_or_predictions_are_promoted(self):
        point = {"frame_index": 0, "processed_index": 0, "timestamp_ms": 0, "x": .5, "y": .5, "source": "interpolated"}
        result = build_court_projection({"segments": [{"points": [point]}]}, {(0, 0, 0): validated_calibration(court(), 1000, 500)})
        self.assertEqual(result["points"], [])

    def test_contract_accepts_calibrated_and_unavailable(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/trajectory-preview.schema.json").read_text(encoding="utf-8"))
        row = _frame(0, ball_x=.5)
        for calibrated in [False, True]:
            if calibrated:
                row["court"] = court()
            Draft202012Validator(schema).validate(self.preview([row]))

    def test_manual_calibration_requires_verified_camera_reference(self):
        for motion in (None, camera("moving"), camera("unavailable"), camera("stationary")):
            row = _frame(0, ball_x=.3)
            row["court"] = {**court(), "method": "manual_court_outer_doubles_corners"}
            row["camera_motion"] = motion
            with self.subTest(motion=motion):
                self.assertEqual(self.preview([row])["court_projection"]["points"], [])
        row["camera_motion"] = camera()
        self.assertEqual(len(self.preview([row])["court_projection"]["points"]), 1)

    def test_missing_ball_or_court_does_not_hide_camera_break(self):
        for motion in (None, camera("moving"), camera("unavailable"),
                       camera("stationary", epoch=1), camera("stationary", dx=5)):
            rows = [_frame(i, ball_x=.2+i*.01) for i in range(3)]
            for row in rows:
                row["court"] = {**court(), "method": "manual_court_outer_doubles_corners"}
                row["camera_motion"] = camera("reference" if row["frame"]["index"] == 0 else "stationary")
            rows[1]["camera_motion"] = motion
            rows[1]["detections"] = []
            rows[1].pop("court")
            with self.subTest(motion=motion):
                points = self.preview(rows)["court_projection"]["points"]
                self.assertEqual([p["timestamp_ms"] for p in points], [0])

    def test_manual_declared_epoch_must_match_and_resolution_must_stay_fixed(self):
        row = _frame(0, ball_x=.3)
        row["court"] = {**court(), "method": "manual_court_outer_doubles_corners"}
        row["camera_motion"] = camera()
        for epoch in (True, 1, "0"):
            row["court"]["camera_reference_epoch"] = epoch
            with self.subTest(epoch=epoch):
                self.assertEqual(self.preview([row])["court_projection"]["points"], [])
        row["court"]["camera_reference_epoch"] = 0
        resized = deepcopy(row)
        resized["frame"].update(index=1, processed_index=1, timestamp_ms=100, width=1001)
        resized["camera_motion"] = camera("stationary")
        self.assertEqual(len(self.preview([row, resized])["court_projection"]["points"]), 1)


if __name__ == "__main__":
    unittest.main()
