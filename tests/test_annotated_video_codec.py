import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rallymate_vision.pipeline import _transcode_annotated_video_for_browser


class AnnotatedVideoCodecTests(unittest.TestCase):
    def test_missing_ffmpeg_keeps_opencv_file_and_reports_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "annotated.mp4"
            source.write_bytes(b"mp4v")
            with patch("rallymate_vision.pipeline._find_ffmpeg_executable", return_value=None):
                result = _transcode_annotated_video_for_browser(source)
            self.assertEqual(result["status"], "fallback")
            self.assertEqual(result["codec"], "mp4v")
            self.assertEqual(source.read_bytes(), b"mp4v")

    def test_successful_transcode_replaces_file_atomically_with_h264(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "annotated.mp4"
            source.write_bytes(b"mp4v")

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                # ffmpeg writes to the final argument; emulate a valid output.
                Path(command[-1]).write_bytes(b"avc1-yuv420p")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("rallymate_vision.pipeline._find_ffmpeg_executable", return_value="ffmpeg"),
                patch("rallymate_vision.pipeline.subprocess.run", side_effect=fake_run),
            ):
                result = _transcode_annotated_video_for_browser(source)

            self.assertEqual(result["status"], "transcoded")
            self.assertEqual(result["codec"], "h264")
            self.assertEqual(result["pixel_format"], "yuv420p")
            self.assertEqual(source.read_bytes(), b"avc1-yuv420p")
            self.assertFalse((source.parent / ".annotated.h264.tmp.mp4").exists())

    def test_transcode_timeout_preserves_original_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "annotated.mp4"
            source.write_bytes(b"mp4v")

            def fake_timeout(command: list[str], **_: object) -> None:
                Path(command[-1]).write_bytes(b"partial")
                raise subprocess.TimeoutExpired(command, 1)

            with (
                patch("rallymate_vision.pipeline._find_ffmpeg_executable", return_value="ffmpeg"),
                patch("rallymate_vision.pipeline.subprocess.run", side_effect=fake_timeout),
            ):
                result = _transcode_annotated_video_for_browser(source)

            self.assertEqual(result["status"], "fallback")
            self.assertEqual(source.read_bytes(), b"mp4v")
            self.assertFalse((source.parent / ".annotated.h264.tmp.mp4").exists())


if __name__ == "__main__":
    unittest.main()
