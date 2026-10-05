"""Index observed successor events without inventing missing event classes."""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping, Sequence
import math
from typing import Any


def finite_time(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def event_identity(event: Mapping[str, Any]) -> tuple[str, int, str] | None:
    video, person, identifier = (event.get(key) for key in ("video_id", "person_track_id", "event_id"))
    if (isinstance(video, str) and video.strip() and isinstance(person, int) and not isinstance(person, bool)
            and isinstance(identifier, str) and identifier.strip()):
        return video, person, identifier
    return None


class SuccessorEventIndex:
    """One immutable snapshot, sorted once by video, subject, class and time."""

    def __init__(self, events: Sequence[Mapping[str, Any]]) -> None:
        grouped = defaultdict(list)
        for event in events:
            if isinstance(event, Mapping) and (key := event_identity(event)) is not None:
                grouped[key].append(event)
        self.conflicts = {key for key, rows in grouped.items() if any(row != rows[0] for row in rows[1:])}
        self.events = {key: rows[0] for key, rows in grouped.items() if key not in self.conflicts}
        self.by_kind: dict[tuple, list] = defaultdict(list)
        for key, event in self.events.items():
            start, end = finite_time(event.get("start_ms")), finite_time(event.get("end_ms"))
            code = event.get("event_code")
            if start is not None and end is not None and start < end and isinstance(code, str):
                self.by_kind[(*key[:2], code)].append(event)
        for rows in self.by_kind.values():
            rows.sort(key=lambda row: (row["start_ms"], row["event_id"]))
        self.starts = {key: [row["start_ms"] for row in rows] for key, rows in self.by_kind.items()}

    def next_event(self, event: Mapping[str, Any], *, after_ms: float,
                   allowed_codes: tuple[str, ...]) -> dict[str, Any]:
        identity = event_identity(event)
        result = {"status": "unavailable", "event": None, "reason": "source_event_identity_unverified",
                  "allowed_event_codes": list(allowed_codes), "absence_is_not_confirmed": True}
        if identity is None or finite_time(after_ms) is None:
            return result
        if identity in self.conflicts or identity not in self.events or self.events[identity] != event:
            return {**result, "reason": "source_event_conflicting_or_not_in_snapshot"}
        candidates = []
        for code in allowed_codes:
            key = (*identity[:2], code)
            rows = self.by_kind.get(key, [])
            start = bisect_left(self.starts.get(key, []), after_ms)
            first_time = None
            for index in range(start, len(rows)):
                candidate = rows[index]
                if first_time is not None and candidate["start_ms"] != first_time:
                    break
                if candidate["event_id"] != identity[2] and candidate["start_ms"] > event["start_ms"]:
                    candidates.append(candidate)
                    first_time = candidate["start_ms"]
        if not candidates:
            return {**result, "status": "not_observed", "reason": "same_player_successor_not_observed"}
        earliest = min(candidate["start_ms"] for candidate in candidates)
        first = [candidate for candidate in candidates if candidate["start_ms"] == earliest]
        if len(first) != 1:
            return {**result, "reason": "simultaneous_successor_candidates_ambiguous"}
        return {**result, "status": "observed_candidate", "event": first[0],
                "reason": "same_video_same_player_observed_successor", "absence_is_not_confirmed": False}
