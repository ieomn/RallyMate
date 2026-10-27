"""Safety checks execute before loading any detector or GPU model."""
import tempfile
from copy import deepcopy
from pathlib import Path
import unittest

from scripts.validate_tennis_identity_pipeline import main, ReviewedSubjectSelection


class ReviewedSubjectSelectionTests(unittest.TestCase):
    def document(self):
        return {"version": "reviewed-inference-subject-v1", "source_video_sha256": "a" * 64,
                "method": "agent_visual_review", "reviewer": "source-review", "automatic_subject_selection": False,
                "frames": [{"timestamp_ms": 1000, "source_frame_index": 25, "source_track_id": 7,
                            "camera_reference_epoch": 0, "pts": 25, "time_base_num": 1, "time_base_den": 25,
                            "width": 1280, "height": 720, "bbox_px": [600, 140, 650, 200]}]}

    def update(self, selector, *, track=7, camera=0, time_ms=1000, box=None):
        return selector.update([{"class_name": "player", "track_id": track,
                                 "bbox_px": box or [600, 140, 650, 200]}],
                               timestamp_ms=time_ms, camera_epoch=camera, width=1280, height=720)

    def test_requires_matching_source_and_manual_semantics(self):
        for key, value in (("source_video_sha256", "b" * 64), ("automatic_subject_selection", True),
                           ("reviewer", ""), ("frames", [])):
            doc = self.document(); doc[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                ReviewedSubjectSelection(doc, "a" * 64)

    def test_target_clock_camera_and_box_cannot_drift(self):
        for kwargs in ({"track": 1}, {"camera": 1}, {"time_ms": 1040}, {"box": [601, 140, 651, 200]},
                       {"box": [float("nan"), 140, 650, 200]}, {"box": [True, 140, 650, 200]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.update(ReviewedSubjectSelection(self.document(), "a" * 64), **kwargs)

    def test_missing_reviewed_detection_never_uses_other_player(self):
        doc = self.document()
        doc["frames"][0].update(source_track_id=None, bbox_px=None, reason_code="reviewed_target_not_detected")
        result = self.update(ReviewedSubjectSelection(doc, "a" * 64), track=1)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["source_track_id"])

    def test_final_artifacts_must_retain_exact_pts_and_full_coverage(self):
        selector = ReviewedSubjectSelection(self.document(), "a" * 64)
        result = self.update(selector)
        self.assertEqual(result["source_track_id"], 7)
        self.assertIn("manual_subject_selection", result["semantics"])
        frame = deepcopy(self.document()["frames"][0])
        frame.update(index=frame.pop("source_frame_index"), video_sha256="a" * 64)
        selector.validate_artifacts([{"frame": frame}])
        frame["pts"] += 1
        with self.assertRaises(ValueError):
            selector.validate_artifacts([{"frame": frame}])
        with self.assertRaises(ValueError):
            selector.validate_artifacts([])
        with self.assertRaises(ValueError):
            self.update(selector)


class IdentityValidationRunnerTests(unittest.TestCase):
    def arguments(self, root):
        request = root / "request.json"
        request.write_text("{}", encoding="utf-8")
        runtime = root / "sam"
        runtime.mkdir()
        python = root / "python.exe"
        python.write_bytes(b"not executed")
        return ["--request", str(request), "--output-root", str(root / "output"),
                "--run-id", "identity-test", "--sam-python", str(python), "--sam-runtime", str(runtime)]

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.arguments(root)
            output = root / "output"
            output.mkdir()
            marker = output / "native.jsonl"
            marker.write_bytes(b"original evidence")
            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                main(args)
            self.assertEqual(marker.read_bytes(), b"original evidence")

    def test_colliding_request_names_fail_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.arguments(root)
            other = root / "other"
            other.mkdir()
            (other / "request.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stems must be unique"):
                main(args + ["--request", str(other / "request.json")])
            self.assertFalse((root / "output").exists())

    def test_run_id_cannot_be_a_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.arguments(root)
            args[args.index("--run-id") + 1] = "../other"
            with self.assertRaisesRegex(ValueError, "run-id must contain"):
                main(args)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
