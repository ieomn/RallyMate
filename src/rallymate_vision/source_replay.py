"""Export selected source frames without flattening their presentation times."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile


def export_source_replay(source: Path, output: Path, *, ffmpeg: str | None,
                         first_frame: int, last_frame: int, stride: int) -> dict:
    if first_frame < 0 or last_frame < first_frame or stride < 1:
        raise ValueError("invalid replay frame interval")
    if not ffmpeg:
        return {"status": "unavailable", "timing_preserved": False,
                "reason": "ffmpeg_unavailable"}
    if source.resolve() == output.resolve():
        raise ValueError("replay output must not replace the source video")
    temporary: Path | None = None
    selection = f"between(n,{first_frame},{last_frame})*not(mod(n-{first_frame},{stride}))"
    try:
        # A fresh file prevents a crashed previous encoder's partial output
        # from being mistaken for this attempt's successful replay.
        with tempfile.NamedTemporaryFile(prefix=f".{output.stem}.source-timing-", suffix=".mp4",
                                         dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                   "-vf", f"select='{selection}',setpts=PTS-STARTPTS", "-fps_mode", "vfr",
                   # Retain demuxer time precision instead of quantizing PTS
                   # to a guessed average frame rate. Disable B-frame reorder
                   # so presentation starts at zero without an MP4 edit offset.
                   "-enc_time_base", "-1", "-bf", "0",
                   # Some VFR selections have zero final packet duration. An
                   # MP4 edit list then discards that last decoded frame. Give
                   # only zero-duration packets one time-base tick; PTS and all
                   # measured inter-frame intervals remain unchanged.
                   "-bsf:v", "setts=duration='if(eq(DURATION,0),1,DURATION)'",
                   "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                   "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)]
        subprocess.run(command, capture_output=True, check=True, timeout=180)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            return {"status": "unavailable", "timing_preserved": False, "reason": "empty_encoder_output"}
        temporary.replace(output)
        return {"status": "transcoded", "codec": "h264", "pixel_format": "yuv420p",
                "timing_preserved": True, "timebase": "source_pts_relative_to_first_selected_frame",
                "terminal_frame_duration_policy": "zero_duration_packets_receive_one_source_timebase_tick",
                "message": "回放保留原始帧间隔；无人体/检测框覆盖。"}
    except (OSError, subprocess.SubprocessError):
        return {"status": "unavailable", "timing_preserved": False, "reason": "source_replay_encoder_failed"}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
