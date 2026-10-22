"""Conservative off-axis swing review windows, never confirmed strokes or grades.

This secondary route deliberately returns its own review queue. It relaxes the
primary route's image-horizontal, cross-body requirement, not its identity,
camera, source-time, observed-joint or racket-evidence gates. Thresholds are
engineering candidates evaluated on MIT forehand/two-hand-backhand clips; they
are not Word A--E thresholds and are not calibrated tennis accuracy claims.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from .pose_observations import measurement_provenance
from .temporal_recognition import samples_are_continuous

VERSION = "off-axis-swing-review-v1.0.1"


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _reference(sample: Any) -> tuple:
    binding = getattr(sample, "source_binding", {}) or {}
    return (sample.track, sample.selection_epoch, getattr(sample, "camera_reference_epoch", None),
            getattr(sample, "normalization_basis", "full_torso"), getattr(sample, "pose_source", None),
            getattr(sample, "observation_semantics", None), binding.get("video_sha256"),
            binding.get("selection_epoch"))


def _bound_primary_windows(samples: Sequence[Any], candidates: Sequence[Mapping[str, Any]]) -> list[tuple]:
    """Bind legacy primary records to this invocation's actual source samples.

    A track number and nearby timestamp alone cannot identify the same selected
    player/camera epoch. Missing or ambiguous source anchors never suppress a
    review window. No caller-supplied epoch is trusted over the sample anchors.
    """
    by_anchor: dict[tuple, list[int]] = {}
    for index, sample in enumerate(samples):
        by_anchor.setdefault((sample.track, sample.frame_index, sample.time), []).append(index)
    bound = []
    for candidate in candidates:
        indexes = [by_anchor.get((candidate.get("person_track_id"), candidate.get(f"{part}_frame"),
                                  candidate.get(f"{part}_ms")), []) for part in ("start", "peak", "end")]
        if any(len(matches) != 1 for matches in indexes):
            continue
        start, peak, end = (matches[0] for matches in indexes)
        if not start < peak < end:
            continue
        interval = samples[start:end + 1]
        reference = _reference(interval[0])
        if (any(_reference(sample) != reference for sample in interval)
                or any(not samples_are_continuous(a, b) for a, b in zip(interval, interval[1:]))):
            continue
        bound.append((candidate["start_ms"], candidate["end_ms"], reference))
    return bound


def _segments(samples: Sequence[Any], side: str):
    segment = []
    for sample in samples:
        point = sample.points.get(f"{side}_wrist")
        valid = (isinstance(point, (tuple, list)) and len(point) == 2
                 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                         and math.isfinite(v) for v in point))
        if (not valid or segment and (not samples_are_continuous(segment[-1], sample)
                                      or _reference(segment[-1]) != _reference(sample))):
            if len(segment) >= 7:
                yield segment
            segment = []
        if valid:
            segment.append(sample)
    if len(segment) >= 7:
        yield segment


def build_review_fallback_candidates(
    by_track: Mapping[int, Sequence[Any]],
    primary_candidates: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Return missing continuous off-axis motion windows for explicit review.

    A caller must provide the same selected-player samples as its main route.
    No interpolation, ID reassociation or source-boundary bridging occurs here.
    Near-wrist racket boxes constrain false candidates; they do not establish
    racket ownership, grip, contact, handedness, a GS event or a score.
    """
    proposed = []
    for track, samples in by_track.items():
        bound_primary = _bound_primary_windows(samples, primary_candidates)
        for side in ("left", "right"):
            for segment in _segments(samples, side):
                points = []
                for i in range(len(segment)):
                    local = [s.points[f"{side}_wrist"] for s in segment[max(0, i - 1):i + 2]]
                    points.append(tuple(statistics.median(p[axis] for p in local) for axis in (0, 1)))
                speeds = [0.0] * len(segment)
                for i in range(1, len(segment) - 1):
                    speeds[i] = _distance(points[i - 1], points[i + 1]) * 1000 / (segment[i + 1].time - segment[i - 1].time)
                for peak in range(2, len(segment) - 2):
                    speed = speeds[peak]
                    if speed < 2.0 or not (speed >= speeds[peak - 1] and speed > speeds[peak + 1]):
                        continue
                    before = [i for i in range(peak) if 100 <= segment[peak].time - segment[i].time <= 500]
                    after = [i for i in range(peak + 1, len(segment)) if 100 <= segment[i].time - segment[peak].time <= 500]
                    windows = []
                    for start in before:
                        for end in after:
                            duration = segment[end].time - segment[start].time
                            if not 300 <= duration <= 1000 or end - start + 1 < 5:
                                continue
                            local = points[start:end + 1]
                            excursion = _distance(local[0], local[-1])
                            path = sum(_distance(a, b) for a, b in zip(local, local[1:]))
                            support = sum(side in s.racket_sides for s in segment[start:end + 1])
                            # Reject overhead actions and nearly stationary/reversing jitter.
                            if (excursion < .8 or path <= 0 or excursion / path < .55
                                    or min(p[1] for p in local) < -.6 or support < 2
                                    or speeds[start] > speed * .8 or speeds[end] > speed * .8):
                                continue
                            windows.append((duration, start, end, excursion, path, support))
                    if not windows:
                        continue
                    _, start, end, excursion, path, support = min(windows)
                    first, last, maximum = segment[start], segment[end], segment[peak]
                    if any(reference == _reference(maximum) and begin - 800 <= maximum.time <= finish + 800
                           for begin, finish, reference in bound_primary):
                        continue
                    identity = f"{VERSION}:{track}:{first.selection_epoch}:{side}:{maximum.frame_index}:{maximum.time}"
                    proposed.append({
                        "candidate_id": hashlib.sha256(identity.encode()).hexdigest()[:20],
                        "route": VERSION, "family": "unclassified_swing_review",
                        "status": "manual_review_required", "automatic_scoring_allowed": False,
                        "contact_confirmed": False, "technique_label": None,
                        "person_track_id": track, "wrist_side": side,
                        "start_ms": first.time, "peak_ms": maximum.time, "end_ms": last.time,
                        "start_frame": first.frame_index, "peak_frame": maximum.frame_index, "end_frame": last.frame_index,
                        "selection_epoch": first.selection_epoch,
                        "camera_reference_epoch": getattr(first, "camera_reference_epoch", None),
                        "measurement_provenance": measurement_provenance(segment[start:end + 1]),
                        "trigger": "primary_route_has_no_interval_within_800_ms_of_review_peak",
                        "evidence": {"pose_samples": end - start + 1,
                                     "racket_associated_frames": support,
                                     "peak_wrist_speed_torso_per_s": round(speed, 4),
                                     "net_excursion_torso": round(excursion, 4),
                                     "path_efficiency": round(excursion / path, 4),
                                     "interpolated_samples": 0},
                        "limitations_zh": ["离轴运动仅进入复核队列，不能证明击球、持拍手或技术类型。",
                                           "邻近主候选800毫秒及复核峰900毫秒内合并以抑制准备/回拍重复，快速衔接可能被合并。",
                                           "未经人工核验的候选不能计入正式评分。"],
                    })
    # Both arms and neighbouring local peaks may describe one review interval.
    accepted = []
    for candidate in sorted(proposed, key=lambda p: (-p["evidence"]["peak_wrist_speed_torso_per_s"],
                                                    -p["evidence"]["racket_associated_frames"])):
        if any(p["person_track_id"] == candidate["person_track_id"]
               and p["selection_epoch"] == candidate["selection_epoch"]
               and p["camera_reference_epoch"] == candidate["camera_reference_epoch"]
               and abs(p["peak_ms"] - candidate["peak_ms"]) <= 900 for p in accepted):
            continue
        accepted.append(candidate)
    return sorted(accepted, key=lambda p: (p["peak_ms"], p["person_track_id"]))
