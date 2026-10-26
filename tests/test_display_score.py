from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from rallymate_vision.display_score import (
    DISPLAY_SCORE_SEMANTICS, load_display_score_policy, recover_rtmpose_display_points,
)


def summary():
    return {"job_id": "test-job", "models": {"pose_backend": {
        "backend": "rtmpose", "runtime": "pytorch", "native_keypoint_format": "halpe26",
        "native_keypoint_count": 26, "model_name": "rtmpose-m_halpe26_256x192",
        "model_sha256": "a" * 64, "config_sha256": "b" * 64,
    }}}


def point(**changes):
    return {"name": "hip", "x_px": 160, "y_px": 90, "confidence": 1,
            "raw_confidence": 1.153117, "confidence_in_range": False, **changes}


def pose(points=None, **changes):
    return {"coordinate_space": "original_frame", "keypoint_format": "halpe26",
            "keypoints": points if points is not None else [point()], **changes}


class DisplayScoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "frames.jsonl"
        self.summary_path = self.path.with_name("summary.json")
        self.summary_path.write_text(json.dumps(summary()), encoding="utf-8")
        self.policy = load_display_score_policy(self.path)

    def recover(self, value=None, **changes):
        return recover_rtmpose_display_points(value if value is not None else pose(),
            640, 360, frame_job_id=changes.get("job_id", "test-job"),
            policy=changes.get("policy", self.policy))

    def test_positive_simcc_response_recovers_exact_observation_without_mutation(self):
        source = pose([point(x_normalized=.25, y_normalized=.25)])
        before = deepcopy(source)
        self.assertEqual(self.recover(source), [{"name": "hip", "x": .25, "y": .25,
            "confidence": 1.0, "source": "observed", "score_semantics": DISPLAY_SCORE_SEMANTICS,
            "raw_confidence": 1.153117}])
        self.assertEqual(source, before)

    def test_backend_and_model_provenance_must_match_recorded_rtmpose(self):
        for field, value in [("backend", "yolo"), ("runtime", "onnx"),
                ("native_keypoint_format", "coco17"), ("native_keypoint_count", 17),
                ("model_name", "another-model"), ("model_sha256", ""), ("config_sha256", None)]:
            with self.subTest(field=field):
                document = summary()
                document["models"]["pose_backend"][field] = value
                self.summary_path.write_text(json.dumps(document), encoding="utf-8")
                self.assertIsNone(load_display_score_policy(self.path))
        self.assertEqual(self.recover(job_id="another-job"), [])
        self.assertEqual(self.recover(job_id=None), [])
        self.assertEqual(self.recover(policy=None), [])

    def test_missing_malformed_duplicate_and_nonfinite_summaries_fail_closed(self):
        self.summary_path.unlink()
        self.assertIsNone(load_display_score_policy(self.path))
        for raw in ['{', '[]', '{"job_id":"a","job_id":"b"}', '{"x":NaN}']:
            self.summary_path.write_text(raw, encoding="utf-8")
            self.assertIsNone(load_display_score_policy(self.path))

    def test_invalid_offscreen_and_low_scores_do_not_get_recovered(self):
        for changes in [
            {"confidence": .9}, {"confidence": True}, {"confidence_in_range": True},
            {"raw_confidence": 1}, {"raw_confidence": -1}, {"raw_confidence": None},
            {"raw_confidence": float("inf")}, {"raw_confidence": float("nan")},
            {"raw_confidence": True}, {"in_frame": False}, {"raw_x_px": -1},
            {"raw_y_px": 500}, {"x_px": -1}, {"y_px": 361}, {"x_px": True},
            {"x_px": float("nan")}, {"name": "invented"},
            {"x_normalized": .9, "y_normalized": .25}, {"x_normalized": .25},
        ]:
            with self.subTest(changes=changes):
                self.assertEqual(self.recover(pose([point(**changes)])), [])

    def test_conflicting_duplicate_joint_does_not_pick_one(self):
        self.assertEqual(self.recover(pose([point(), point(x_px=100)])), [])
        self.assertEqual(len(self.recover(pose([point(), point()]))), 1)

    def test_dimensions_and_coordinate_contract_are_required(self):
        for width, height in [(0, 360), (640, 0), (True, 360), (640, 70_000), (640.0, 360)]:
            self.assertEqual(recover_rtmpose_display_points(pose(), width, height,
                frame_job_id="test-job", policy=self.policy), [])
        self.assertEqual(self.recover(pose(coordinate_space="roi")), [])
        self.assertEqual(self.recover(pose(keypoint_format="coco17")), [])


if __name__ == "__main__":
    unittest.main()
