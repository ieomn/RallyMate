import json
from pathlib import Path
import tempfile
import unittest

from scripts.validate_full_test_scoring import matching_run, validate_native


class FullTestScoringValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run = Path(self.temporary.name)
        self.video = {"sha256": "a" * 64, "frames": 3}
        self.job = {"id": "a", "status": "succeeded", "video_sha256": "a" * 64,
                    "processed_frames": 3, "source_start_frame": 0, "source_end_frame_exclusive": 3,
                    "frame_stride": 1, "pose_backend": "sam3d", "output_dir": str(self.run)}
        self.native = [{"frame_index": i, "person_track_id": 1, "video_sha256": "a" * 64,
                        "status": "ok"} for i in range(3)]
        self.frames = [{"frame": {"index": i}, "poses": [{"id": 1}]} for i in range(3)]
        self.write()

    def write(self):
        for name, rows in (("sam3d_frames.jsonl", self.native), ("frames.jsonl", self.frames),
                           ("primary-player.jsonl", [])):
            (self.run / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_full_bound_video_is_eligible_and_measures_coverage(self):
        self.assertEqual(matching_run(self.video, [self.job]), self.job)
        result = validate_native(self.video, self.job)
        self.assertEqual(result["native_frame_count"], 3)
        self.assertEqual(result["pose_frame_fraction"], 1)

    def test_short_clip_cannot_satisfy_full_video_acceptance(self):
        for patch in ({"processed_frames": 2}, {"source_start_frame": 1}, {"frame_stride": 2},
                      {"source_end_frame_exclusive": 2}, {"video_sha256": "b" * 64}):
            with self.subTest(patch=patch):
                self.assertIsNone(matching_run(self.video, [{**self.job, **patch}]))

    def test_native_missing_frame_is_rejected_even_if_report_claims_full_length(self):
        self.native.pop(1)
        self.write()
        with self.assertRaisesRegex(ValueError, "every original frame"):
            validate_native(self.video, self.job)

    def test_native_wrong_video_hash_is_rejected(self):
        self.native[1]["video_sha256"] = "b" * 64
        self.write()
        with self.assertRaisesRegex(ValueError, "SHA differs"):
            validate_native(self.video, self.job)

    def test_projected_missing_or_reordered_frame_is_rejected(self):
        self.frames[1], self.frames[2] = self.frames[2], self.frames[1]
        self.write()
        with self.assertRaisesRegex(ValueError, "source-frame order"):
            validate_native(self.video, self.job)


if __name__ == "__main__":
    unittest.main()
