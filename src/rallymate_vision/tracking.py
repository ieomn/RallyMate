from __future__ import annotations

from dataclasses import dataclass

from rallymate_vision.utils import box_iou, normalized_center_distance


@dataclass
class _Track:
    track_id: int
    class_name: str
    bbox_px: list[float]
    missed: int = 0
    last_timestamp_ms: int | None = None


class SimpleMultiClassTracker:
    """Small deterministic tracker for the demo handoff contract.

    It combines IoU and center-distance matching. It is intentionally isolated
    behind a class so ByteTrack/BoT-SORT can replace it without changing output.
    """

    def __init__(self, max_missed: int = 8, *, ball_max_gap_ms: int = 300) -> None:
        if isinstance(ball_max_gap_ms, bool) or not isinstance(ball_max_gap_ms, int) or ball_max_gap_ms <= 0:
            raise ValueError("ball_max_gap_ms must be a positive integer")
        self.max_missed = max_missed
        self.ball_max_gap_ms = ball_max_gap_ms
        self._last_timestamp_ms: int | None = None
        self.max_missed_by_class = {
            "player": max(30, max_missed),
            "racket": max(12, max_missed),
            "ball": max_missed,
        }
        self._tracks: list[_Track] = []
        self._next_id = 1

    def _miss_limit(self, class_name: str) -> int:
        return self.max_missed_by_class.get(class_name, self.max_missed)

    def _active(self, track: _Track, timestamp_ms: int | None) -> bool:
        # Source-video time makes the ball's identity lifetime independent of
        # input FPS and frame stride. Legacy callers retain the frame fallback.
        if track.class_name == "ball" and timestamp_ms is not None and track.last_timestamp_ms is not None:
            return 0 <= timestamp_ms - track.last_timestamp_ms <= self.ball_max_gap_ms
        return track.missed <= self._miss_limit(track.class_name)

    def update(
        self,
        detections: list[dict],
        frame_width: int,
        frame_height: int,
        *,
        timestamp_ms: int | None = None,
    ) -> list[dict]:
        if timestamp_ms is not None:
            if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
                raise ValueError("timestamp_ms must be a nonnegative integer")
            if self._last_timestamp_ms is not None and timestamp_ms < self._last_timestamp_ms:
                # A seek/new source cannot inherit identities from its future.
                self._tracks = []
            self._last_timestamp_ms = timestamp_ms
        assigned_track_ids: set[int] = set()
        ordered = sorted(
            enumerate(detections),
            key=lambda item: float(item[1].get("confidence", 0)),
            reverse=True,
        )

        for _, detection in ordered:
            class_name = detection["class_name"]
            candidates: list[tuple[float, _Track]] = []
            distance_gate = 0.28 if class_name == "ball" else 0.16
            for track in self._tracks:
                if (
                    track.class_name != class_name
                    or track.track_id in assigned_track_ids
                    or not self._active(track, timestamp_ms)
                ):
                    continue
                iou = box_iou(track.bbox_px, detection["bbox_px"])
                distance = normalized_center_distance(
                    track.bbox_px,
                    detection["bbox_px"],
                    frame_width,
                    frame_height,
                )
                if iou < 0.01 and distance > distance_gate:
                    continue
                cost = (1.0 - iou) * 0.65 + distance * 0.35
                candidates.append((cost, track))

            if candidates:
                _, selected = min(candidates, key=lambda pair: pair[0])
                selected.bbox_px = detection["bbox_px"]
                selected.missed = 0
            else:
                selected = _Track(
                    track_id=self._next_id,
                    class_name=class_name,
                    bbox_px=detection["bbox_px"],
                )
                self._next_id += 1
                self._tracks.append(selected)

            selected.last_timestamp_ms = timestamp_ms
            assigned_track_ids.add(selected.track_id)
            detection["track_id"] = selected.track_id

        for track in self._tracks:
            if track.track_id not in assigned_track_ids:
                track.missed += 1
        self._tracks = [
            track
            for track in self._tracks
            if self._active(track, timestamp_ms)
        ]
        return detections
