from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from rallymate_vision.trajectory import build_trajectory_preview
from tests.test_manual_court import camera
from tests.test_trajectory import _frame


def stationary(index, *, status="stationary", dx=0, epoch=0):
    row = _frame(index, ball_x=.3)
    row["frame"]["timestamp_source"] = "decoder_pts"
    row["camera_motion"] = camera(status, dx=dx, epoch=epoch)
    return row


def window(start, end):
    return {"start_timestamp_ms": start, "end_timestamp_ms": end}


class CourtCalibrationWindowsTests(unittest.TestCase):
    def preview(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            before = path.read_bytes()
            result = build_trajectory_preview(path)
            self.assertEqual(before, path.read_bytes())
            return result

    def windows(self, rows):
        return self.preview(rows)["source"]["court_calibration_windows"]

    def test_initial_reference_then_stationary_form_one_span(self):
        rows = [stationary(0, status="reference"), stationary(1), stationary(2)]
        self.assertEqual(self.windows(rows), [window(0, 200)])

    def test_stationary_can_start_new_local_reference_after_moving(self):
        rows = [stationary(0), stationary(1, status="moving"), stationary(2, dx=20), stationary(3, dx=21)]
        self.assertEqual(self.windows(rows), [window(0, 0), window(200, 300)])

    def test_unknown_missing_and_invalid_camera_break_even_without_ball(self):
        invalid = (None, {}, camera("unavailable"), camera("unknown"), camera("moving"),
                   {**camera("stationary"), "compensation_valid": False},
                   {**camera("stationary"), "matrix_to_reference": [[1, 1, 0], [0, 1, 0]]},
                   {**camera("stationary"), "reference_epoch": None})
        for value in invalid:
            with self.subTest(camera=value):
                rows = [stationary(i) for i in range(3)]
                rows[1]["camera_motion"] = value
                rows[1]["detections"] = []
                self.assertEqual(self.windows(rows), [window(0, 0), window(200, 200)])

    def test_epoch_resolution_and_reference_reset_split(self):
        for mutation in ("epoch", "size", "reference"):
            with self.subTest(mutation=mutation):
                rows = [stationary(i) for i in range(3)]
                if mutation == "epoch":
                    rows[1]["camera_motion"]["reference_epoch"] = 1
                elif mutation == "size":
                    rows[1]["frame"]["width"] = 1200
                else:
                    rows[1]["camera_motion"]["status"] = "reference"
                expected = [window(0, 0), window(100, 200)] if mutation == "reference" else [window(0, 0), window(100, 100), window(200, 200)]
                self.assertEqual(self.windows(rows), expected)

    def test_accumulated_drift_is_measured_against_local_first_frame(self):
        # 1000x500 tolerance=3.354px; epoch translation itself can be nonzero.
        rows = [stationary(i, dx=10 + i * 2) for i in range(4)]
        self.assertEqual(self.windows(rows), [window(0, 100), window(200, 300)])

    def test_similarity_scale_and_rotation_use_relative_transform(self):
        rows = [stationary(i) for i in range(3)]
        for row in rows:
            row["camera_motion"]["matrix_to_reference"] = [[0, -1, 50], [1, 0, 70]]
        rows[1]["camera_motion"]["matrix_to_reference"][0][2] += 1
        rows[2]["camera_motion"]["matrix_to_reference"][0][2] += 5
        self.assertEqual(self.windows(rows), [window(0, 100), window(200, 200)])

    def test_frame_processed_and_pts_gaps_split(self):
        for field, value in (("index", 9), ("processed_index", 9), ("timestamp_ms", 500)):
            rows = [stationary(i) for i in range(3)]
            rows[1]["frame"][field] = value
            expected = [window(0, 0), window(500, 500)] if field == "timestamp_ms" else [window(0, 0), window(100, 100), window(200, 200)]
            self.assertEqual(self.windows(rows), expected)

    def test_duplicate_or_backward_timestamps_do_not_create_overlapping_ranges(self):
        rows = [stationary(i) for i in range(5)]
        for row, timestamp in zip(rows, (0, 100, 100, 50, 200)):
            row["frame"]["timestamp_ms"] = timestamp
        self.assertEqual(self.windows(rows), [window(0, 100), window(200, 200)])

    def test_legacy_missing_timing_or_camera_is_not_whole_video_eligible(self):
        self.assertEqual(self.windows([_frame(i) for i in range(3)]), [])
        rows = [stationary(i) for i in range(3)]
        for row in rows:
            row["frame"]["timestamp_source"] = "fps_fallback"
        self.assertEqual(self.windows(rows), [])

    def test_output_is_bounded_without_merging_over_unsafe_spans(self):
        rows = [stationary(i, status="stationary" if i % 2 == 0 else "moving") for i in range(7)]
        with patch("rallymate_vision.trajectory.MAX_COURT_CALIBRATION_WINDOWS", 2):
            source = self.preview(rows)["source"]
        self.assertEqual(source["court_calibration_windows"], [window(0, 0), window(200, 200)])
        self.assertTrue(source["court_calibration_windows_truncated"])

    def test_new_windows_do_not_create_historical_court_projection_or_alter_ball(self):
        rows = [stationary(i) for i in range(3)]
        legacy = deepcopy(rows)
        for row in legacy:
            row.pop("camera_motion")
        before, after = self.preview(legacy), self.preview(rows)
        self.assertEqual(before["ball"], after["ball"])
        self.assertEqual(before["court_projection"], after["court_projection"])
        self.assertEqual(after["court_projection"]["points"], [])
        self.assertEqual(after["source"]["court_calibration_windows"], [window(0, 200)])

    def test_contract_accepts_new_fields(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/trajectory-preview.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(self.preview([stationary(0), stationary(1)]))


if __name__ == "__main__":
    unittest.main()
