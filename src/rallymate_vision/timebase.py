"""Source presentation timestamps with explicit, auditable decoder fallback."""
from __future__ import annotations

import math


class SourceVideoClock:
    def __init__(self, fps: float) -> None:
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("fps must be finite and positive")
        self.fps = fps
        self.origin: float | None = None
        self.previous: int | None = None
        self.last_index: int | None = None
        self.decoder_count = 0
        self.fallback_count = 0

    def resolve(self, frame_index: int, decoder_ms: float) -> tuple[int, str]:
        if self.last_index is not None and frame_index <= self.last_index:
            raise ValueError("source frames must be strictly increasing")
        valid = math.isfinite(decoder_ms) and decoder_ms >= 0
        if self.origin is None and valid and frame_index == 0:
            self.origin = decoder_ms
        candidate = round(decoder_ms - self.origin) if valid and self.origin is not None else -1
        if candidate >= 0 and (self.previous is None or candidate > self.previous):
            timestamp, source = candidate, "decoder_pts"
            self.decoder_count += 1
        else:
            # Never label average-FPS approximation as an actual presentation
            # timestamp; downstream motion calculations can reject this frame.
            timestamp = round(frame_index * 1000 / self.fps)
            if self.previous is not None:
                step = max(1, round((frame_index - self.last_index) * 1000 / self.fps))
                timestamp = max(timestamp, self.previous + step)
            source = "fps_fallback"
            self.fallback_count += 1
        self.previous, self.last_index = timestamp, frame_index
        return timestamp, source

    def summary(self) -> dict:
        return {
            "method": "decoder_presentation_timestamps",
            "decoder_timestamp_frames": self.decoder_count,
            "fallback_timestamp_frames": self.fallback_count,
            "source_origin_ms": self.origin,
            "timing_verified": self.fallback_count == 0 and self.decoder_count > 1,
            "fallback_semantics": "average_fps_approximation_not_verified_motion_timing",
        }
