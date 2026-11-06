"""Chronological identity and output-slot tests; no Torch/model loading."""
from __future__ import annotations

from copy import deepcopy
import unittest

import numpy as np

from rallymate_vision.sam3d_adapter import Sam3DPoseAdapter


def binding(index, *, sha="a" * 64):
    # Exact source PTS differ from playback-relative milliseconds.
    return {"video_sha256": sha, "frame_index": index * 2, "processed_index": index,
            "camera_reference_epoch": 4, "pts": 100 + index * 23,
            "time_base_num": 1, "time_base_den": 600,
            "timestamp_ms": (100 + index * 23) * 1000 / 600,
            "relative_timestamp_ms": index * 38}


def player(track=7, box=None):
    return {"track_id": track, "bbox_px": box or [5., 5., 45., 45.]}


def request(index, *, players=None, sha="a" * 64, shape=(64, 96, 3)):
    return {"frame": np.full(shape, index, dtype=np.uint8),
            "player_detections": [player()] if players is None else players,
            "max_players": 2, "source_binding": binding(index, sha=sha)}


class FixtureEngine:
    def __init__(self):
        self.single_calls, self.batch_calls = [], []

    @staticmethod
    def result(frame, boxes):
        marker = float(frame[0, 0, 0])
        return [{"status": "ok", "keypoints_2d": [[marker + box[0], 10.] for _ in range(70)],
                 "keypoints_3d": [[marker, box[0], 1.] for _ in range(70)],
                 "mesh_artifact": {"path": f"mesh-{marker}-{box[0]}.npz", "sha256": "b" * 64}}
                for box in boxes]

    def infer(self, frame, boxes):
        self.single_calls.append((frame, boxes))
        return self.result(frame, boxes)

    def infer_batch(self, requests):
        self.batch_calls.append(requests)
        return [self.result(item["frame"], item["boxes"]) for item in requests]


def adapter(engine=None):
    result = Sam3DPoseAdapter.__new__(Sam3DPoseAdapter)
    result.engine = engine or FixtureEngine()
    result._last_seen, result._epochs = {}, {}
    result.reset_sequence()
    return result


class Sam3DAdapterBatchTests(unittest.TestCase):
    def test_batch_matches_ordered_single_with_skips_video_resets_and_epoch_gaps(self):
        inputs = [request(0), request(1, players=[]), request(2),
                  request(3, players=[player(8), player(7, [10., 10., 30., 30.])]),
                  request(4, sha="c" * 64)]
        sequential = adapter()
        expected = []
        for item in inputs:
            poses = sequential.estimate(item["frame"], item["player_detections"], item["max_players"],
                                        source_binding=item["source_binding"])
            expected.append({"poses": poses, "records": deepcopy(sequential.last_records)})
        batched = adapter()
        actual = batched.estimate_batch(inputs)
        self.assertEqual(actual, expected)
        self.assertEqual(batched._last_seen, sequential._last_seen)
        self.assertEqual(batched._epochs, sequential._epochs)
        self.assertIs(batched.last_records, actual[-1]["records"])
        self.assertEqual(len(batched.engine.batch_calls), 1)
        self.assertEqual(len(batched.engine.batch_calls[0]), 4)
        self.assertFalse(batched.engine.single_calls)
        self.assertEqual(actual[2]["records"][0]["selection_epoch"], 1)
        self.assertEqual(actual[4]["records"][0]["selection_epoch"], 0)

    def test_invalid_bbox_and_source_do_not_shift_native_or_mesh_binding(self):
        first = request(0, players=[player(5, [-2., 0., 60., 50.]), player(9)])
        invalid = request(1)
        invalid["source_binding"]["timestamp_ms"] += 5
        last = request(2, shape=(80, 120, 3))
        instance = adapter()
        result = instance.estimate_batch([first, invalid, last])
        self.assertEqual(len(instance.engine.batch_calls[0]), 2)
        self.assertEqual(result[0]["records"][0]["reason"], "invalid_source_bbox")
        self.assertEqual(result[0]["records"][1]["status"], "ok")
        self.assertEqual(result[0]["poses"][0]["person_track_id"], 9)
        self.assertEqual(result[1]["records"][0]["reason"], "source_binding_unverified")
        self.assertEqual(result[1]["poses"], [])
        self.assertEqual(result[2]["records"][0]["image_size"], [120, 80])
        self.assertEqual(result[2]["records"][0]["keypoints_2d"]["left_hip"], [7., 10.])
        self.assertEqual(result[2]["poses"][0]["source_binding"]["pts"], 146)
        self.assertEqual(result[2]["records"][0]["mesh_artifact"]["path"], "mesh-2.0-5.0.npz")

    def test_missing_identity_and_empty_subjects_are_not_sent_to_engine(self):
        instance = adapter()
        rows = instance.estimate_batch([request(0, players=[]), request(1, players=[player(None)])])
        self.assertEqual([row["poses"] for row in rows], [[], []])
        self.assertEqual(rows[0]["records"][0]["reason"], "no_detected_subject")
        self.assertEqual(rows[1]["records"][0]["reason"], "source_binding_unverified")
        self.assertFalse(instance.engine.batch_calls)

    def test_frame_count_mismatch_fails_accepted_requests_without_rebinding(self):
        instance = adapter()
        instance.engine.infer_batch = lambda _: []
        rows = instance.estimate_batch([request(0), request(1, players=[]), request(2)])
        self.assertEqual([row["records"][0]["status"] for row in rows], ["failed", "skipped", "failed"])
        self.assertEqual(rows[0]["records"][0]["error_type"], "ValueError")
        self.assertEqual([row["poses"] for row in rows], [[], [], []])
        self.assertEqual(rows[2]["records"][0]["pts"], 146)

    def test_subject_count_mismatch_does_not_discard_other_frames(self):
        instance = adapter()
        def infer(items):
            return [[], FixtureEngine.result(items[1]["frame"], items[1]["boxes"])]
        instance.engine.infer_batch = infer
        rows = instance.estimate_batch([request(0), request(1)])
        self.assertEqual(rows[0]["records"][0]["status"], "failed")
        self.assertEqual(rows[0]["poses"], [])
        self.assertEqual(rows[1]["records"][0]["status"], "ok")
        self.assertEqual(rows[1]["poses"][0]["source_binding"]["frame_index"], 2)

    def test_engine_failure_retains_each_valid_request_and_epoch(self):
        instance = adapter()
        def infer(_):
            raise RuntimeError("fixture engine unavailable")
        instance.engine.infer_batch = infer
        rows = instance.estimate_batch([request(0), request(1), request(3)])
        self.assertEqual([row["records"][0]["selection_epoch"] for row in rows], [0, 0, 1])
        for row in rows:
            self.assertEqual(row["records"][0]["status"], "failed")
            self.assertEqual(row["records"][0]["reason"], "sam_worker_error")
            self.assertEqual(row["poses"], [])

    def test_malformed_subject_envelope_fails_only_its_frame(self):
        instance = adapter()
        instance.engine.infer_batch = lambda items: [None, FixtureEngine.result(items[1]["frame"], items[1]["boxes"])]
        rows = instance.estimate_batch([request(0), request(1)])
        self.assertEqual(rows[0]["records"][0]["status"], "failed")
        self.assertEqual(rows[0]["records"][0]["error_type"], "TypeError")
        self.assertEqual(rows[1]["records"][0]["status"], "ok")

    def test_individual_model_failure_keeps_other_subjects_and_frames(self):
        instance = adapter()
        def infer(items):
            rows = [FixtureEngine.result(item["frame"], item["boxes"]) for item in items]
            rows[0][0] = {"status": "failed", "reason": "fixture_model_failure"}
            return rows
        instance.engine.infer_batch = infer
        rows = instance.estimate_batch([request(0, players=[player(7), player(9, [10., 10., 30., 30.])]), request(1)])
        self.assertEqual([r["status"] for r in rows[0]["records"]], ["failed", "ok"])
        self.assertEqual([p["person_track_id"] for p in rows[0]["poses"]], [9])
        self.assertEqual(rows[1]["records"][0]["status"], "ok")

    def test_previous_result_survives_following_single_frame_call(self):
        instance = adapter()
        rows = instance.estimate_batch([request(0), request(1)])
        snapshot = deepcopy(rows)
        item = request(2)
        instance.estimate(item["frame"], item["player_detections"], 1, source_binding=item["source_binding"])
        self.assertEqual(rows, snapshot)
        self.assertEqual(instance.last_records[0]["frame_index"], 4)
        self.assertEqual(len(instance.engine.single_calls), 1)

    def test_empty_batch_and_limit_do_not_advance_sequence(self):
        instance = adapter()
        self.assertEqual(instance.estimate_batch([]), [])
        with self.assertRaisesRegex(ValueError, "at most 8"):
            instance.estimate_batch([request(i) for i in range(9)])
        self.assertFalse(instance._last_seen)
        self.assertFalse(instance.engine.batch_calls)


if __name__ == "__main__":
    unittest.main()
