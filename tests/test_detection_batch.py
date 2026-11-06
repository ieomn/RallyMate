import unittest

import numpy as np

from rallymate_vision.detection_batch import detected_observations


class Detector:
    def __init__(self):
        self.calls = []
        self.last_detection_timing = {"total_wall_ms": 2}
        self.last_detection_timings = []

    def detect(self, image):
        self.calls.append([int(image[0, 0, 0])])
        return self.calls[-1]

    def detect_batch(self, images):
        self.calls.append([int(image[0, 0, 0]) for image in images])
        self.last_detection_timings = [{"total_wall_ms": 2} for _ in images]
        return [[value] for value in self.calls[-1]]


def observations(count):
    for index in range(count):
        yield {"image": np.full((4, 4, 3), index, dtype=np.uint8),
               "source_frame_index": index * 2, "decoded_binding": {"pts": 101 + index * 33}}


class DetectionBatchTests(unittest.TestCase):
    def run_batch(self, count, **options):
        detector, stats, timings = Detector(), {}, {}
        rows = list(detected_observations(observations(count), detector, statistics=stats,
                                          timings=timings, **options))
        self.assertEqual([row["source_frame_index"] for row in rows], list(range(0, 2 * count, 2)))
        self.assertEqual([row["decoded_binding"]["pts"] for row in rows], [101 + i * 33 for i in range(count)])
        self.assertEqual([row["detections"] for row in rows], [[i] for i in range(count)])
        return detector, stats, timings

    def test_partial_last_batch_and_pts_order(self):
        detector, stats, timings = self.run_batch(9, batch_size=4)
        self.assertEqual(detector.calls, [[0, 1, 2, 3], [4, 5, 6, 7], [8]])
        self.assertEqual(stats["peak_batch_frames"], 4)
        self.assertAlmostEqual(timings["detection_component_total_wall_seconds"], .018)

    def test_memory_cap_and_oversized_frame(self):
        detector, stats, _ = self.run_batch(5, batch_size=4, max_buffer_bytes=96)
        self.assertEqual(detector.calls, [[0, 1], [2, 3], [4]])
        self.assertLessEqual(stats["peak_batch_image_bytes"], 96)
        detector, stats, _ = self.run_batch(3, batch_size=4, max_buffer_bytes=1)
        self.assertEqual(detector.calls, [[0], [1], [2]])

    def test_default_preserves_one_frame_at_a_time(self):
        detector, _, _ = self.run_batch(3)
        self.assertEqual(detector.calls, [[0], [1], [2]])

    def test_cancel_during_collection_does_not_infer_partial_batch(self):
        detector = Detector()
        def source():
            yield from observations(2)
            raise InterruptedError("cancel")
        with self.assertRaises(InterruptedError):
            list(detected_observations(source(), detector, batch_size=4, statistics={}, timings={}))
        self.assertEqual(detector.calls, [])

    def test_wrong_batch_result_count_never_misbinds_frames(self):
        detector = Detector()
        detector.detect_batch = lambda images: [[]]
        with self.assertRaisesRegex(ValueError, "number of frames"):
            list(detected_observations(observations(3), detector, batch_size=3, statistics={}, timings={}))

    def test_empty_and_invalid_config(self):
        detector, stats, _ = self.run_batch(0, batch_size=4)
        self.assertEqual(detector.calls, [])
        self.assertEqual(stats["batches"], 0)
        for size in (0, 17, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.run_batch(1, batch_size=size)


if __name__ == "__main__":
    unittest.main()
