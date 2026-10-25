from collections import Counter
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_vision.sam3d_runner import PrefetchSourcePTSDecoder


class DecoderFixture:
    def __init__(self, _path):
        self.binding, self.timing_seconds = {}, Counter()
        self.index, self.closed, self.fail = 0, False, False

    def read(self):
        if self.fail and self.index == 1:
            raise ValueError("fixture decode failure")
        if self.index == 6:
            return False, None
        self.binding = {"pts": self.index * 2, "time_base_num": 1,
                        "time_base_den": 60, "timestamp_ms": round(self.index * 1000 / 30)}
        frame = np.full((2, 3, 3), self.index, dtype=np.uint8)
        self.index += 1
        return True, frame

    def release(self):
        self.closed = True


class SourcePrefetchTests(unittest.TestCase):
    def test_all_source_pixels_and_binding_order_preserved(self):
        for depth in (1, 2, 4):
            with self.subTest(depth=depth), patch("rallymate_vision.sam3d_runner.SourcePTSDecoder", DecoderFixture):
                decoder = PrefetchSourcePTSDecoder(Path("fixture"), depth=depth)
                seen = []
                try:
                    while True:
                        ok, frame = decoder.read()
                        if not ok:
                            break
                        index = len(seen)
                        self.assertTrue(np.array_equal(frame, np.full((2, 3, 3), index, np.uint8)))
                        self.assertEqual(decoder.binding["pts"], index * 2)
                        self.assertEqual(decoder.get(0), round(index * 1000 / 30))
                        seen.append(index)
                finally:
                    decoder.release()
                self.assertEqual(seen, list(range(6)))
                self.assertEqual(decoder.produced_frames, decoder.consumed_frames)
                self.assertFalse(decoder._thread.is_alive())
                self.assertTrue(decoder._decoder.closed)

    def test_early_close_cancels_full_queue_and_is_idempotent(self):
        with patch("rallymate_vision.sam3d_runner.SourcePTSDecoder", DecoderFixture):
            decoder = PrefetchSourcePTSDecoder(Path("fixture"), depth=1)
            decoder.read()
            decoder.release()
            decoder.release()
            self.assertFalse(decoder._thread.is_alive())
            self.assertTrue(decoder._decoder.closed)
            self.assertTrue(decoder._queue.empty())
            self.assertEqual(decoder.read(), (False, None))
            self.assertLessEqual(decoder.produced_frames - decoder.consumed_frames, 2)

    def test_decode_failure_is_propagated_and_closes(self):
        def failing(path):
            decoder = DecoderFixture(path)
            decoder.fail = True
            return decoder
        with patch("rallymate_vision.sam3d_runner.SourcePTSDecoder", failing):
            decoder = PrefetchSourcePTSDecoder(Path("fixture"), depth=1)
            self.assertTrue(decoder.read()[0])
            with self.assertRaisesRegex(ValueError, "fixture decode failure"):
                decoder.read()
            decoder.release()
            self.assertFalse(decoder._thread.is_alive())
            self.assertTrue(decoder._decoder.closed)

    def test_invalid_depth_is_rejected(self):
        for depth in (0, 9, True, 1.5):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                PrefetchSourcePTSDecoder(Path("fixture"), depth=depth)
