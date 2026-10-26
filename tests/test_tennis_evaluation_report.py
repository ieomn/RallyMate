"""Source-bound comparisons and public report redaction regressions."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.render_tennis_evaluation_report import main, validate_comparison


def sample(identifier="sample-a", **extra):
    return {
        "sample_id": identifier, "source_group": identifier, "split": "holdout",
        "evaluation_label": "ordinary", "evaluation_status": "evaluated", "status": "scored",
        "source_video_sha256": "a" * 64,
        "artifact_sha256": {"summary.json": "b" * 64, "primary-player.jsonl": "c" * 64, "sam3d_frames.jsonl": "d" * 64},
        "score": 82., "episodes": 3, **extra,
    }


def report(*samples):
    return {"samples": list(samples), "distributions": {}, "rubric_version": "test-rubric",
            "engine_sha256": {"tennis_assessment.py": "e" * 64}, "manifest_sha256": "f" * 64,
            "created_at_utc": "2026-10-26T00:00:00+00:00"}


class EvaluationReportTests(unittest.TestCase):
    def test_only_scores_may_change_when_source_and_pose_inputs_are_identical(self):
        before = report(sample(), sample("sample-b"))
        after = deepcopy(before)
        after["samples"].reverse()
        after["samples"][0].update(score=70., source_video_sha256="A" * 64)
        validate_comparison(before, after)

    def test_different_video_cannot_be_presented_as_a_rubric_change(self):
        with self.assertRaisesRegex(ValueError, "source video"):
            validate_comparison(report(sample()), report(sample(source_video_sha256="1" * 64)))

    def test_changed_pose_or_target_selection_cannot_be_presented_as_same_inputs(self):
        for artifact in ("summary.json", "primary-player.jsonl", "sam3d_frames.jsonl"):
            before, after = report(sample()), report(sample())
            after["samples"][0]["artifact_sha256"][artifact] = "1" * 64
            with self.subTest(artifact=artifact), self.assertRaisesRegex(ValueError, "artifact"):
                validate_comparison(before, after)

    def test_scored_comparison_requires_verifiable_pose_hashes_on_both_sides(self):
        for sides in ("before", "after", "both"):
            before, after = report(sample()), report(sample())
            if sides in {"before", "both"}:
                before["samples"][0].pop("artifact_sha256")
            if sides in {"after", "both"}:
                after["samples"][0].pop("artifact_sha256")
            with self.subTest(sides=sides), self.assertRaises(ValueError):
                validate_comparison(before, after)

    def test_sample_set_and_completion_must_match(self):
        with self.assertRaisesRegex(ValueError, "sample sets"):
            validate_comparison(report(sample()), report(sample(), sample("new-sample")))
        with self.assertRaisesRegex(ValueError, "completion"):
            validate_comparison(report(sample()), report(sample(evaluation_status="unavailable", score=None)))

    def test_both_unavailable_failures_are_retained_but_queued_jobs_are_not_final(self):
        failed = sample(evaluation_status="unavailable", status="failed", score=None)
        failed.pop("artifact_sha256")
        validate_comparison(report(failed), report(deepcopy(failed)))
        for state in ("queued", "running", "pending"):
            pending = deepcopy(failed)
            pending["status"] = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate_comparison(report(pending), report(deepcopy(pending)))

    def test_rendered_public_payload_omits_private_paths_and_processing_commands(self):
        private = r"C:\Users\PrivateUser\Documents\private-video.mp4"
        original = sample(input_path=private, source_path=private, original_path=private,
                          command=[r"C:\Users\PrivateUser\ffmpeg.exe", "-i", private])
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            before, after, output = directory / "before.json", directory / "after.json", directory / "report.html"
            before.write_text(json.dumps(report(original)), encoding="utf8")
            after.write_text(json.dumps(report(original)), encoding="utf8")
            with patch.object(sys, "argv", ["render", "--before", str(before), "--after", str(after), "--output", str(output)]), redirect_stdout(io.StringIO()):
                main()
            for path in (output, output.with_suffix(".json")):
                data = path.read_text(encoding="utf8")
                self.assertNotIn("PrivateUser", data)
                self.assertNotIn("private-video.mp4", data)
                self.assertNotIn("input_path", data)
                self.assertNotIn("command", data)


if __name__ == "__main__":
    unittest.main()
