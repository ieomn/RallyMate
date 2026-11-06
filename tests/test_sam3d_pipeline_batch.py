"""Bounded SAM pipeline staging: original frame/time identity and cancellation."""
from copy import deepcopy
import unittest

import numpy as np

from rallymate_service.worker import JobCancelled
from rallymate_vision.sam3d_pipeline_batch import sam_pose_observations


def person(track, box):
    return {"track_id": track, "bbox_px": box, "confidence": .9}


def observation(index, *, target=7, no_players=False):
    return {"image": np.full((8, 8, 3), index, dtype=np.uint8),
            "players": [] if no_players else [person(8, [0, 0, 8, 8]), person(7, [1, 1, 5, 5]),
                                                person(9, [2, 2, 4, 4])],
            "subject_identity": {"source_track_id": target},
            "decoded_binding": {"pts": 6000 + index * 23, "time_base_num": 1,
                                "time_base_den": 600, "timestamp_ms": (6000 + index * 23) * 1000 / 600},
            "source_frame_index": index * 3 + 10,
            "camera_motion": {"reference_epoch": 2},
            "timestamp_ms": index * 38, "timestamp_source": "decoder_pts"}


class Perception:
    def __init__(self):
        self.single_calls, self.batch_calls = [], []
        self.records = []
        self.after_inference = lambda: None

    @staticmethod
    def output(request):
        binding = deepcopy(request["source_binding"])
        marker = int(request["frame"][0, 0, 0])
        people = [p["track_id"] for p in request["player_detections"]]
        return {"poses": [{"marker": marker, "people": people, "binding": binding}] if people else [],
                "records": [{"marker": marker, "people": people, "binding": binding}]}

    def estimate_poses(self, frame, players, max_players, *, source_binding):
        request = {"frame": frame, "player_detections": players, "max_players": max_players,
                   "source_binding": source_binding}
        self.single_calls.append(request)
        output = self.output(request)
        self.records = output["records"]
        self.after_inference()
        return output["poses"]

    def pose_frame_records(self):
        return self.records

    def estimate_pose_batch(self, requests):
        self.batch_calls.append(requests)
        output = [self.output(request) for request in requests]
        self.after_inference()
        return output


class SamPipelineBatchTests(unittest.TestCase):
    def run_helper(self, source, *, batch=2, perception=None, check=lambda: None, **kwargs):
        perception = perception or Perception()
        timings, stats = {}, {}
        rows = list(sam_pose_observations(source, perception, max_players=2, video_sha256="a" * 64,
            batch_frames=batch, timings=timings, statistics=stats, check_cancelled=check, **kwargs))
        return rows, perception, timings, stats

    def test_batch_and_tail_match_sequential_pixels_pts_and_subject_selection(self):
        source = [observation(0), observation(1, no_players=True), observation(2, target=None),
                  observation(3), observation(4)]
        single, _, _, _ = self.run_helper(source, batch=1)
        rows, perception, timings, stats = self.run_helper(source, batch=2)
        self.assertEqual([row["sam_records"] for row in rows], [row["sam_records"] for row in single])
        self.assertEqual([row["sam_poses"] for row in rows], [row["sam_poses"] for row in single])
        self.assertEqual([[int(r["frame"][0, 0, 0]) for r in batch] for batch in perception.batch_calls], [[0, 1], [2, 3]])
        self.assertEqual(len(perception.single_calls), 1)
        self.assertEqual(stats["calls"], 3)
        self.assertEqual(stats["peak_frames"], 2)
        self.assertIn("pose_seconds", timings)
        for index, row in enumerate(rows):
            record = row["sam_records"][0]
            bound = record["binding"]
            self.assertEqual(record["marker"], index)
            self.assertEqual(bound["processed_index"], index)
            self.assertEqual(bound["frame_index"], index * 3 + 10)
            self.assertEqual(bound["pts"], 6000 + index * 23)
            self.assertEqual(bound["timestamp_ms"], round((6000 + index * 23) * 1000 / 600))
            self.assertEqual(bound["relative_timestamp_ms"], index * 38)
            self.assertEqual(bound["camera_reference_epoch"], 2)
            self.assertEqual(bound["video_sha256"], "a" * 64)
            self.assertEqual(record["people"], [] if index in (1, 2) else [7, 8] if index == 4 else [7])
            self.assertEqual(bound["subject_selection"], row["sam_subject_selection"])

    def test_secondary_confirmation_is_identical_in_scalar_and_eight_frame_batches(self):
        source = [observation(i) for i in range(12)]
        scalar, _, _, _ = self.run_helper(source, batch=1)
        for batch in (4, 8):
            rows, _, _, _ = self.run_helper(source, batch=batch)
            self.assertEqual([r["sam_subject_selection"] for r in rows],
                             [r["sam_subject_selection"] for r in scalar])
            self.assertEqual([r["sam_records"] for r in rows], [r["sam_records"] for r in scalar])

    def test_byte_bound_flushes_before_next_batch_and_oversized_frame_runs_alone(self):
        rows, perception, _, stats = self.run_helper([observation(i) for i in range(5)], batch=4,
                                                    max_buffer_bytes=2 * 8 * 8 * 3)
        self.assertEqual(len(rows), 5)
        self.assertEqual([len(batch) for batch in perception.batch_calls], [2, 2])
        self.assertEqual(len(perception.single_calls), 1)
        self.assertLessEqual(stats["peak_source_bytes"], 384)
        rows, perception, _, stats = self.run_helper([observation(i) for i in range(3)], batch=4,
                                                    max_buffer_bytes=1)
        self.assertEqual(len(rows), 3)
        self.assertFalse(perception.batch_calls)
        self.assertEqual(len(perception.single_calls), 3)
        self.assertEqual(stats["peak_frames"], 1)

    def test_mutable_decoder_binding_is_copied_before_future_frames_advance(self):
        shared_binding = {}
        def source():
            for index in range(4):
                row = observation(index)
                shared_binding.update(row["decoded_binding"])
                row["decoded_binding"] = shared_binding
                yield row
        rows, _, _, _ = self.run_helper(source(), batch=4)
        self.assertEqual([r["sam_records"][0]["binding"]["pts"] for r in rows], [6000, 6023, 6046, 6069])

    def test_cancel_while_collecting_never_runs_incomplete_batch(self):
        perception = Perception()
        def source():
            yield observation(0)
            raise JobCancelled("cancel during collection")
        with self.assertRaises(JobCancelled):
            self.run_helper(source(), batch=4, perception=perception)
        self.assertFalse(perception.batch_calls)
        self.assertFalse(perception.single_calls)

    def test_cancel_after_forward_prevents_any_result_publication(self):
        perception = Perception()
        state = {"cancel": False}
        perception.after_inference = lambda: state.update(cancel=True)
        def check():
            if state["cancel"]:
                raise JobCancelled("cancel after forward")
        published = []
        iterator = sam_pose_observations([observation(0), observation(1)], perception,
            max_players=2, video_sha256="a" * 64, batch_frames=2,
            timings={}, statistics={}, check_cancelled=check)
        with self.assertRaises(JobCancelled):
            for row in iterator:
                published.append(row)
        self.assertEqual(published, [])
        self.assertEqual(len(perception.batch_calls), 1)

    def test_malformed_adapter_slots_never_rebind_frames(self):
        for output in (None, [], [{"poses": [], "records": []}], [None, None],
                       [{"poses": None, "records": []}, {"poses": [], "records": []}]):
            perception = Perception()
            perception.estimate_pose_batch = lambda requests: output
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "mismatched frame slots"):
                self.run_helper([observation(0), observation(1)], perception=perception)

    def test_empty_input_and_invalid_limits(self):
        rows, perception, _, stats = self.run_helper([], batch=8)
        self.assertEqual(rows, [])
        self.assertEqual(stats["calls"], 0)
        self.assertFalse(perception.batch_calls)
        for batch in (0, 9, True):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                self.run_helper([], batch=batch)
        with self.assertRaises(ValueError):
            self.run_helper([], max_buffer_bytes=0)


if __name__ == "__main__":
    unittest.main()
