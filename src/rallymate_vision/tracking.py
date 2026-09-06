from __future__ import annotations

from dataclasses import dataclass

from rallymate_vision.utils import box_iou, normalized_center_distance


@dataclass
class _Track:
    track_id: int
    class_name: str
    bbox_px: list[float]
    missed: int = 0


class SimpleMultiClassTracker:
    """Small deterministic tracker for the demo handoff contract.

    It combines IoU and center-distance matching. It is intentionally isolated
    behind a class so ByteTrack/BoT-SORT can replace it without changing output.
    """

    def __init__(self, max_missed: int = 8) -> None:
        self.max_missed = max_missed
        self.max_missed_by_class = {
            "player": max(30, max_missed),
            "racket": max(12, max_missed),
            "ball": max_missed,
        }
        self._tracks: list[_Track] = []
        self._next_id = 1

    def _miss_limit(self, class_name: str) -> int:
        return self.max_missed_by_class.get(class_name, self.max_missed)

    def update(
        self,
        detections: list[dict],
        frame_width: int,
        frame_height: int,
    ) -> list[dict]:
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
                    or track.missed > self._miss_limit(track.class_name)
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

            assigned_track_ids.add(selected.track_id)
            detection["track_id"] = selected.track_id

        for track in self._tracks:
            if track.track_id not in assigned_track_ids:
                track.missed += 1
        self._tracks = [
            track
            for track in self._tracks
            if track.missed <= self._miss_limit(track.class_name)
        ]
        return detections
