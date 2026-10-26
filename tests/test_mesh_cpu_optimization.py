from fractions import Fraction
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rallymate_vision.sam3d_mesh import _open_video_outputs


class MeshEncoderTests(unittest.TestCase):
    def fake_av(self, failed_open=2):
        containers = []

        def make_container(*args, **kwargs):
            context = SimpleNamespace(open=Mock())
            if len(containers) + 1 == failed_open:
                context.open.side_effect = RuntimeError("NVENC session unavailable")
            stream = SimpleNamespace(codec_context=context)
            container = SimpleNamespace(add_stream=Mock(return_value=stream), close=Mock())
            containers.append(container)
            return container

        return SimpleNamespace(open=Mock(side_effect=make_container)), containers

    def test_second_nvenc_failure_closes_both_before_software_fallback(self):
        av, created = self.fake_av()
        outputs, details = _open_video_outputs(av, ["front", "side"], width=64, height=48,
                                               rate=30, time_base=Fraction(1, 90000),
                                               preference="auto", thread_count=2)
        self.assertEqual(len(created), 4)
        self.assertEqual(len(outputs), 2)
        for old in created[:2]:
            old.close.assert_called_once()
        self.assertEqual(details["codec"], "libx264")
        self.assertEqual(details["fallback_reason"], "RuntimeError")
        for container, stream in outputs:
            container.add_stream.assert_called_once_with("libx264", rate=30)
            self.assertEqual(stream.codec_context.max_b_frames, 0)
            self.assertEqual(stream.codec_context.thread_count, 2)
            self.assertEqual(stream.codec_context.time_base, Fraction(1, 90000))

    def test_explicit_hardware_test_cannot_silently_fallback(self):
        av, created = self.fake_av()
        with self.assertRaisesRegex(RuntimeError, "NVENC"):
            _open_video_outputs(av, ["front", "side"], width=64, height=48,
                                rate=30, time_base=Fraction(1, 30),
                                preference="h264_nvenc", thread_count=2)
        self.assertEqual(len(created), 2)
        for container in created:
            container.close.assert_called_once()

    def test_default_tries_hardware_with_bounded_software_fallback(self):
        av, created = self.fake_av()
        with patch.dict(os.environ, {}, clear=True):
            outputs, details = _open_video_outputs(av, ["front", "side"], width=64, height=48,
                                                   rate=30, time_base=Fraction(1, 30))
        self.assertEqual(details["requested"], "auto")
        self.assertEqual(details["codec"], "libx264")
        self.assertEqual(details["thread_count"], 2)
        for container in created[:2]:
            container.add_stream.assert_called_once_with("h264_nvenc", rate=30)
            container.close.assert_called_once()
        self.assertEqual(len(outputs), 2)

    def test_invalid_configuration_is_rejected_before_opening(self):
        av, _ = self.fake_av()
        for settings in ({"preference": "unexpected"}, {"thread_count": 0}, {"thread_count": 9}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                _open_video_outputs(av, ["front", "side"], width=64, height=48,
                                    rate=30, time_base=Fraction(1, 30), **settings)
        av.open.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("av"), "PyAV is installed in the SAM runtime")
    def test_real_software_encoders_preserve_irregular_pts_in_both_views(self):
        import av
        time_base = Fraction(1, 90000)
        timestamps = [0, 3000, 7500, 10500, 19500, 24000]
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{name}.mp4" for name in ("front", "side")]
            outputs, details = _open_video_outputs(av, paths, width=64, height=48,
                                                  rate=30, time_base=time_base,
                                                  preference="libx264", thread_count=2)
            self.assertEqual(details["codec"], "libx264")
            try:
                for pts in timestamps:
                    for container, stream in outputs:
                        frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), pts % 255, np.uint8),
                                                           format="bgr24")
                        frame.pts, frame.time_base = pts, time_base
                        for packet in stream.encode(frame):
                            container.mux(packet)
                for container, stream in outputs:
                    for packet in stream.encode():
                        container.mux(packet)
            finally:
                for container, _ in outputs:
                    container.close()
            for path in paths:
                with av.open(str(path)) as container:
                    self.assertEqual(container.streams.video[0].codec_context.name, "h264")
                    actual = [frame.pts * frame.time_base for frame in container.decode(video=0)]
                self.assertEqual(actual, [pts * time_base for pts in timestamps])


if __name__ == "__main__":
    unittest.main()
