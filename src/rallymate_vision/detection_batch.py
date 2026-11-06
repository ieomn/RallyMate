"""Bounded stateless detection ahead of chronological tracking/pose updates."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
import time


def detected_observations(
    observations: Iterable[dict], perception, *, batch_size: int = 1,
    max_buffer_bytes: int = 64 * 1024 * 1024,
    check_cancelled: Callable[[], None] = lambda: None,
    timings: dict, statistics: dict,
) -> Iterator[dict]:
    """Keep pixels and PTS bound; only the stateless detector runs in batches.

    A batch is bounded by count and source-image bytes. One oversized image is
    still processed alone. The input iterator/decoder may hold one additional
    image, and its prefetch and subject-lock queues have independent limits.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 16:
        raise ValueError("detection batch size must be between 1 and 16")
    if not isinstance(max_buffer_bytes, int) or max_buffer_bytes <= 0:
        raise ValueError("detection buffer bytes must be positive")
    statistics.update(requested_batch_size=batch_size, max_buffer_bytes=max_buffer_bytes,
                      batches=0, peak_batch_frames=0, peak_batch_image_bytes=0)

    def infer(batch):
        check_cancelled()
        started = time.perf_counter()
        if len(batch) == 1:
            outputs = [perception.detect(batch[0]["image"])]
            components = [getattr(perception, "last_detection_timing", {})]
        else:
            outputs = perception.detect_batch([item["image"] for item in batch])
            components = getattr(perception, "last_detection_timings", [])
        timings["detection_seconds"] = timings.get("detection_seconds", 0) + time.perf_counter() - started
        check_cancelled()
        if len(outputs) != len(batch):
            raise ValueError("detector returned a different number of frames")
        if isinstance(components, list):
            for component in components:
                if isinstance(component, dict):
                    for key, value in component.items():
                        if isinstance(value, (int, float)):
                            name = f"detection_component_{key.removesuffix('_ms')}_seconds"
                            timings[name] = timings.get(name, 0) + value / 1000
        statistics["batches"] += 1
        statistics["peak_batch_frames"] = max(statistics["peak_batch_frames"], len(batch))
        statistics["peak_batch_image_bytes"] = max(statistics["peak_batch_image_bytes"],
                                                 sum(item["image"].nbytes for item in batch))
        for observation, detections in zip(batch, outputs):
            check_cancelled()
            yield {**observation, "detections": detections}

    pending, pending_bytes = [], 0
    for observation in observations:
        check_cancelled()
        size = observation["image"].nbytes
        if pending and pending_bytes + size > max_buffer_bytes:
            yield from infer(pending)
            pending, pending_bytes = [], 0
        pending.append(observation)
        pending_bytes += size
        if len(pending) >= batch_size or pending_bytes >= max_buffer_bytes:
            yield from infer(pending)
            pending, pending_bytes = [], 0
    if pending:
        yield from infer(pending)
