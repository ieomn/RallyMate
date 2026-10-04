from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cv2

from rallymate_vision.pipeline import _find_ffmpeg_executable
from rallymate_vision.source_replay import export_source_replay


SCRATCH = Path(__file__).resolve().parents[1] / ".codex_tmp"


def _timestamps(path: Path) -> list[float]:
    capture = cv2.VideoCapture(str(path))
    values = []
    try:
        while capture.read()[0]:
            values.append(capture.get(cv2.CAP_PROP_POS_MSEC))
    finally:
        capture.release()
    return values


class SourceReplayFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=SCRATCH, prefix="replay-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source.mp4"
        self.output = self.root / "replay.mp4"
        self.output.write_bytes(b"previous-readable-replay")

    def _export(self, ffmpeg: str | None = "ffmpeg") -> dict:
        return export_source_replay(self.source, self.output, ffmpeg=ffmpeg,
                                    first_frame=1, last_frame=6, stride=2)

    def test_no_encoder_preserves_existing_output(self) -> None:
        self.assertFalse(self._export(None)["timing_preserved"])
        self.assertEqual(self.output.read_bytes(), b"previous-readable-replay")

    def test_empty_success_does_not_publish_stale_or_empty_output(self) -> None:
        old_temporary = self.output.with_name(self.output.stem + ".source-timing.tmp.mp4")
        old_temporary.write_bytes(b"stale-partial-encode")
        with patch("rallymate_vision.source_replay.subprocess.run"):
            result = self._export()
        self.assertFalse(result["timing_preserved"])
        self.assertEqual(result["reason"], "empty_encoder_output")
        self.assertEqual(self.output.read_bytes(), b"previous-readable-replay")
        self.assertEqual(old_temporary.read_bytes(), b"stale-partial-encode")
        self.assertEqual(list(self.root.glob(".replay.source-timing-*.mp4")), [])

    def test_timeout_and_encoder_failure_keep_old_output_and_clean_partial_file(self) -> None:
        for error in [subprocess.TimeoutExpired("ffmpeg", 180), subprocess.CalledProcessError(1, "ffmpeg")]:
            def failed(command, **_kwargs):
                Path(command[-1]).write_bytes(b"partial-encode")
                raise error
            with self.subTest(error=type(error).__name__), patch("rallymate_vision.source_replay.subprocess.run", side_effect=failed):
                self.assertFalse(self._export()["timing_preserved"])
                self.assertEqual(self.output.read_bytes(), b"previous-readable-replay")
                self.assertEqual(list(self.root.glob(".replay.source-timing-*.mp4")), [])

    def test_replay_never_overwrites_its_source(self) -> None:
        with self.assertRaises(ValueError):
            export_source_replay(self.output, self.output, ffmpeg="ffmpeg", first_frame=0, last_frame=1, stride=1)
        self.assertEqual(self.output.read_bytes(), b"previous-readable-replay")


class SourceReplayVariableTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ffmpeg = _find_ffmpeg_executable()
        if not cls.ffmpeg:
            raise unittest.SkipTest("installed ffmpeg unavailable; no installation or network required")
        SCRATCH.mkdir(exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(dir=SCRATCH, prefix="vfr-replay-test-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.source = cls.root / "nonuniform-source.mp4"
        cls.source_ms = [0, 40, 120, 140, 260, 310, 500]
        selection = "+".join(f"eq(n,{value})" for value in cls.source_ms)
        subprocess.run([cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc=size=64x64:rate=1000:duration=0.501",
                        "-vf", f"select='{selection}'", "-fps_mode", "vfr", "-enc_time_base", "1/1000",
                        "-an", "-c:v", "libx264", "-bf", "0", "-pix_fmt", "yuv420p",
                        "-bsf:v", "setts=duration='if(eq(DURATION,0),1,DURATION)'", str(cls.source)],
                       check=True, capture_output=True, timeout=30)

    def test_real_vfr_clip_and_stride_preserve_all_selected_pts_including_last_frame(self) -> None:
        source_times = _timestamps(self.source)
        self.assertEqual(len(source_times), len(self.source_ms))
        for actual, expected in zip(source_times, self.source_ms):
            self.assertAlmostEqual(actual, expected, delta=.1)
        for first, last, stride in [(0, 6, 1), (1, 6, 2), (2, 5, 1), (4, 4, 1)]:
            with self.subTest(first=first, last=last, stride=stride):
                output = self.root / f"clip-{first}-{last}-{stride}.mp4"
                result = export_source_replay(self.source, output, ffmpeg=self.ffmpeg,
                                              first_frame=first, last_frame=last, stride=stride)
                self.assertTrue(result["timing_preserved"])
                expected = [value - source_times[first] for value in source_times[first:last + 1:stride]]
                actual = _timestamps(output)
                self.assertEqual(len(actual), len(expected), "MP4 must keep the last selected frame, not just its packet")
                for timestamp, reference in zip(actual, expected):
                    self.assertAlmostEqual(timestamp, reference, delta=.1)


if __name__ == "__main__":
    unittest.main()
