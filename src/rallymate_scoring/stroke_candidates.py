"""Reviewable swing/serve motion candidates from tracked pose and racket boxes.

This is a deterministic, image-plane candidate detector, not a contact detector
or a trained tennis technique classifier. Its output must never be added to GS
event counts or treated as confirmed hits. Every candidate retains its player
identity and source-video interval for review.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any

from .temporal_recognition import TemporalRecognitionBackend, samples_are_continuous
from .pose_observations import MODEL_ESTIMATE, measurement_provenance, sam_source_bound_points, sample_source_reference

DETECTOR_VERSION = "pose-racket-stroke-candidates-v2.3.1"
MIN_KEYPOINT_CONFIDENCE = 0.35
MAX_POSE_GAP_MS = 160
SEQUENCE_RULE_VERSION = "observed-counter-sweep-association-v1.1"
# Engineering action-proposal rules, not technique-score thresholds. The
# comparisons use actual source time and one continuously observed wrist.
SEQUENCE_RULES = {
    "opposite_arm_lift_onset_fraction": .2,
    "counter_sweep_peak_separation_ms": [400, 2000],
    "maximum_supporting_to_main_speed_ratio": .75,
    "minimum_main_upward_excursion_torso": .35,
    "low_hand_y_torso": .35,
    "terminal_low_hand_window_ms": 100,
    "terminal_low_hand_fraction": .8,
    "reset_body_side_margin_torso": .2,
    "maximum_recovery_arm_extension_range_torso": .2,
    "minimum_low_arm_range_for_context_only_racket_support_torso": .2,
    "maximum_raised_preparation_terminal_y_torso": .05,
    "minimum_raise_vertical_to_horizontal_ratio": .8,
}
LIMITATIONS = [
    "姿态与近手球拍时序规则只提出动作候选，未经专项标注集验证。",
    "候选次数不是击球次数；规则运动类型与真实触球分别处理，不输出确认击球次数。",
    "侧视角、遮挡、球拍漏检或球员跟踪中断会漏掉动作，需结合候选时间窗复核。",
]


@dataclass
class Sample:
    time: int
    frame_index: int
    track: int
    center: tuple[float, float]
    scale: float
    points: dict[str, tuple[float, float]]
    racket_sides: set[str]
    selection_epoch: int = 0
    raw_wrists_px: dict[str, tuple[float, float]] = field(default_factory=dict)
    raw_scale: float | None = None
    camera_reference_epoch: int | None = None
    camera_compensated: bool = False
    point_confidences: dict[str, float] = field(default_factory=dict)
    timestamp_source: str | None = None
    normalization_basis: str = "full_torso"
    reference_points: dict[str, tuple[float, float]] = field(default_factory=dict)
    pose_source: str | None = None
    observation_semantics: str | None = None
    source_binding: dict[str, Any] = field(default_factory=dict)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) else None


def _point(item: Mapping[str, Any]) -> tuple[float, float] | None:
    x, y, confidence = (_number(item.get(key)) for key in ("x_px", "y_px", "confidence"))
    if x is None or y is None or confidence is None or confidence < MIN_KEYPOINT_CONFIDENCE:
        return None
    if item.get("in_frame") is False:
        return None
    return x, y


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _midpoint(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


def _normalization(points: Mapping[str, tuple[float, float]], side: str | None = None) -> tuple[tuple[float, float], float, str] | None:
    torso = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
    if side is None and all(name in points for name in torso):
        center = _midpoint(points["left_shoulder"], points["right_shoulder"])
        hips = _midpoint(points["left_hip"], points["right_hip"])
        scale = max(_distance(center, hips), _distance(points["left_shoulder"], points["right_shoulder"]) * 0.75)
        return (center, scale, "full_torso") if scale >= 12 else None
    sides = (side,) if side else ("left", "right")
    for candidate_side in sides:
        shoulder, hip = (points.get(f"{candidate_side}_{joint}") for joint in ("shoulder", "hip"))
        if shoulder is not None and hip is not None:
            scale = _distance(shoulder, hip)
            if scale >= 12:
                return shoulder, scale, f"{candidate_side}_shoulder_{candidate_side}_torso"
    return None


def _sample(pose: Mapping[str, Any], frame: Mapping[str, Any]) -> Sample | None:
    if frame.get("timestamp_source") == "fps_fallback":
        return None
    if frame.get("camera_motion_status") in {"moving", "unavailable"}:
        return None
    track, timestamp = pose.get("person_track_id"), frame.get("timestamp_ms")
    if not isinstance(track, int) or isinstance(track, bool) or _number(timestamp) is None:
        return None
    is_sam = pose.get("source") == "sam3d" or pose.get("keypoint_format") == "mhr70"
    binding = {}
    if is_sam:
        validated = sam_source_bound_points(pose, frame)
        if validated is None:
            return None
        raw, binding = validated
    else:
        raw = {item.get("name"): point for item in pose.get("keypoints", [])
               if isinstance(item, Mapping) and (point := _point(item)) is not None}
    normalization = _normalization(raw)
    if normalization is None:
        return None
    center, scale, basis = normalization
    raw_wrists = {side: raw[f"{side}_wrist"] for side in ("left", "right") if f"{side}_wrist" in raw}
    raw_scale = scale
    camera = frame.get("camera_motion")
    if isinstance(camera, Mapping) and camera.get("compensation_valid") is True:
        from rallymate_features.coordinates import validated_camera_transform
        transform = validated_camera_transform(camera)
        if transform is None:
            return None
        raw = {name: (float(transform[0, 0] * x + transform[0, 1] * y + transform[0, 2]),
                      float(transform[1, 0] * x + transform[1, 1] * y + transform[1, 2]))
               for name, (x, y) in raw.items()}
        normalization = _normalization(raw)
        if normalization is None:
            return None
        center, scale, basis = normalization
    points = {name: ((point[0] - center[0]) / scale, (point[1] - center[1]) / scale)
              for name, point in raw.items()}
    return Sample(timestamp if is_sam else int(timestamp), int(frame.get("index", 0)), track, center, scale, points, set(),
                  raw_wrists_px=raw_wrists, raw_scale=raw_scale,
                  camera_reference_epoch=camera.get("reference_epoch") if isinstance(camera, Mapping) else binding.get("camera_reference_epoch"),
                  camera_compensated=isinstance(camera, Mapping) and camera.get("compensation_valid") is True,
                  point_confidences={} if is_sam else {item["name"]: float(item["confidence"]) for item in pose.get("keypoints", [])
                                     if isinstance(item, Mapping) and item.get("name") in raw},
                  timestamp_source=frame.get("timestamp_source"), normalization_basis=basis,
                  reference_points=raw, pose_source="sam3d" if is_sam else pose.get("source"),
                  observation_semantics=MODEL_ESTIMATE if is_sam else None, source_binding=binding)


def _stabilize_partial_torso_windows(by_track: Mapping[int, list[Sample]]) -> None:
    """Choose one genuinely observed normalization pair for a partial window.

    Reusing the same observed shoulder/hip pair throughout a continuous window
    prevents a missing contralateral point from moving the origin mid-swing.
    No joint is reconstructed. Fully observed windows retain the original
    bilateral scale. Without a common pair, basis changes remain hard breaks.
    """
    for samples in by_track.values():
        windows: list[list[Sample]] = []
        current: list[Sample] = []
        for sample in samples:
            previous = current[-1] if current else None
            continuous = previous is None or (
                0 < sample.time - previous.time <= MAX_POSE_GAP_MS
                and sample.selection_epoch == previous.selection_epoch
                and sample.camera_reference_epoch == previous.camera_reference_epoch
                and sample_source_reference(sample) == sample_source_reference(previous)
                and _distance(sample.center, previous.center) / sample.scale < 0.8
                and 0.65 < sample.scale / previous.scale < 1.55)
            if not continuous and current:
                windows.append(current)
                current = []
            current.append(sample)
        if current:
            windows.append(current)
        for window in windows:
            if all(sample.normalization_basis == "full_torso" for sample in window):
                continue
            side = next((side for side in ("left", "right")
                         if all(_normalization(sample.reference_points, side) is not None for sample in window)), None)
            if side is None:
                continue
            for sample in window:
                sample.center, sample.scale, sample.normalization_basis = _normalization(sample.reference_points, side)
                sample.points = {name: ((x - sample.center[0]) / sample.scale, (y - sample.center[1]) / sample.scale)
                                 for name, (x, y) in sample.reference_points.items()}


def _associate_rackets(samples: list[Sample], detections: Iterable[Mapping[str, Any]]) -> None:
    """Associate a box to one nearby wrist, rejecting ambiguous player ownership."""
    for detection in detections:
        if not isinstance(detection, Mapping) or detection.get("class_name") != "racket":
            continue
        confidence = _number(detection.get("confidence"))
        box = detection.get("bbox_px")
        if confidence is None or confidence < 0.25 or not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        if any(_number(value) is None for value in box) or box[2] < box[0] or box[3] < box[1]:
            continue
        choices = []
        for sample in samples:
            for side in ("left", "right"):
                wrist = sample.points.get(f"{side}_wrist")
                if wrist is None:
                    continue
                x, y = sample.raw_wrists_px.get(side, (wrist[0] * sample.scale + sample.center[0],
                                                       wrist[1] * sample.scale + sample.center[1]))
                distance = math.hypot(max(box[0] - x, 0, x - box[2]), max(box[1] - y, 0, y - box[3])) / (sample.raw_scale or sample.scale)
                choices.append((distance, sample, side))
        choices.sort(key=lambda value: value[0])
        if not choices or choices[0][0] > 0.35:
            continue
        distance, owner, side = choices[0]
        competitor = next((value for value in choices[1:] if value[1].track != owner.track), None)
        if competitor is not None and competitor[0] - distance < 0.15:
            continue
        # A box covering both wrists has no evidence for choosing one hand.
        # Stable sort order must not turn this geometric tie into a left vote.
        if any(value[1].track == owner.track and value[2] != side
               and math.isclose(value[0], distance, rel_tol=0, abs_tol=1e-12)
               for value in choices[1:]):
            continue
        owner.racket_sides.add(side)


def _smooth_segment(segment: list[Sample], side: str) -> list[tuple[float, float]]:
    name = f"{side}_wrist"
    # Segments already exclude missing wrists and timestamp gaps. A three-frame
    # median removes isolated pose spikes without filling any absent samples.
    result = []
    for index in range(len(segment)):
        points = [sample.points[name] for sample in segment[max(0, index - 1):index + 2]]
        result.append((statistics.median(p[0] for p in points), statistics.median(p[1] for p in points)))
    return result


def _segments(samples: list[Sample], side: str) -> Iterable[list[Sample]]:
    segment: list[Sample] = []
    for sample in samples:
        valid = f"{side}_wrist" in sample.points
        continuous = not segment or samples_are_continuous(segment[-1], sample, max_gap_ms=MAX_POSE_GAP_MS)
        if not valid or not continuous:
            if len(segment) >= 7:
                yield segment
            segment = []
        if valid:
            segment.append(sample)
    if len(segment) >= 7:
        yield segment


def _candidate(segment: list[Sample], side: str, start: int, peak: int, end: int,
               family: str, evidence: dict[str, Any]) -> dict[str, Any]:
    first, maximum, last = segment[start], segment[peak], segment[end]
    candidate_type = "serve_motion" if family == "serve" else "groundstroke_swing"
    identity = f"{DETECTOR_VERSION}:{first.track}:{candidate_type}:{maximum.time}"
    return {
        "candidate_id": hashlib.sha256(identity.encode()).hexdigest()[:20],
        "family": family, "candidate_type": candidate_type, "status": "candidate",
        "person_track_id": first.track, "racket_hand_candidate": side,
        "start_ms": first.time, "peak_ms": maximum.time, "end_ms": last.time,
        "start_frame": first.frame_index, "peak_frame": maximum.frame_index, "end_frame": last.frame_index,
        "contact_confirmed": False,
        "measurement_provenance": measurement_provenance(segment[start:end + 1]),
        "evidence": {"source": "tracked_pose_and_near_wrist_racket_boxes", **evidence},
        "limitations_zh": LIMITATIONS[:2],
    }


def _racket_support(segment: list[Sample], side: str, start: int, end: int) -> int:
    return sum(side in sample.racket_sides for sample in segment[start:end + 1])


def _detect_segment(segment: list[Sample], side: str) -> list[dict[str, Any]]:
    points = _smooth_segment(segment, side)
    times = [sample.time for sample in segment]
    result = []
    other = "right" if side == "left" else "left"
    # A serve candidate needs a sequential opposite-arm lift, racket-arm
    # overhead extension and follow-through. A lone raised arm is insufficient.
    for peak in range(2, len(segment) - 2):
        if points[peak][1] > -0.65 or not (points[peak][1] <= points[peak - 1][1] and points[peak][1] < points[peak + 1][1]):
            continue
        before = [i for i in range(peak) if 150 <= times[peak] - times[i] <= 2000]
        after = [i for i in range(peak + 1, len(segment)) if 120 <= times[i] - times[peak] <= 1200]
        toss = [i for i in before if (w := segment[i].points.get(f"{other}_wrist")) is not None
                and w[1] < -0.45 and points[i][1] > -0.35]
        if not toss or not after:
            continue
        # The last lifted-arm frame is near racket acceleration, not the
        # beginning of a serve. Preserve the observed lift from below shoulder
        # level; otherwise a fast serve is rejected as too short and its early
        # racket observations are discarded. Never extrapolate a missing lift.
        toss_index = min(toss, key=lambda i: segment[i].points[f"{other}_wrist"][1])
        setup = [i for i in before if i <= toss_index and points[i][1] - points[peak][1] >= 0.9]
        follow = [i for i in after if points[i][1] > 0.05 and points[i][1] - points[peak][1] >= 0.9]
        if not setup or not follow:
            continue
        lift_points = [(i, segment[i].points[f"{other}_wrist"][1]) for i in setup
                       if f"{other}_wrist" in segment[i].points]
        lift_bottom = max((y for _, y in lift_points), default=None)
        lift_top = segment[toss_index].points[f"{other}_wrist"][1]
        # The initial 20% of the observed lift defines its onset. Using the
        # shoulder crossing would still crop most of a long arm raise.
        lift_start = [i for i, y in lift_points if lift_bottom is not None
                      and y >= lift_bottom - SEQUENCE_RULES["opposite_arm_lift_onset_fraction"] * (lift_bottom - lift_top)]
        start, end = (lift_start[-1] if lift_start else setup[0]), follow[0]
        support = _racket_support(segment, side, start, end)
        duration = times[end] - times[start]
        if support < 2 or duration < 450 or duration > 3200:
            continue
        result.append(_candidate(segment, side, start, peak, end, "serve", {
            "racket_associated_frames": support, "pose_samples": end - start + 1,
            "opposite_arm_lift_ms": times[toss_index],
            "preparation_start_evidence": "observed_opposite_arm_lift_onset",
            "overhead_height_torso_units": round(-points[peak][1], 3),
            "racket_arm_upward_excursion_torso_units": round(points[start][1] - points[peak][1], 3),
            "follow_through_drop_torso_units": round(points[end][1] - points[peak][1], 3),
            "ball_toss_confirmed": False, "court_location_confirmed": False,
        }))

    # Groundstroke-like cross-body sweeps: body-relative wrist motion, sustained
    # displacement and racket association. Do not infer handedness or contact.
    speed = [0.0] * len(segment)
    for i in range(1, len(segment) - 1):
        speed[i] = _distance(points[i + 1], points[i - 1]) * 1000 / (times[i + 1] - times[i - 1])
    for peak in range(2, len(segment) - 2):
        if speed[peak] < 1.5 or not (speed[peak] >= speed[peak - 1] and speed[peak] > speed[peak + 1]):
            continue
        before = [i for i in range(peak) if 80 <= times[peak] - times[i] <= 650]
        after = [i for i in range(peak + 1, len(segment)) if 80 <= times[i] - times[peak] <= 650]
        pairs = [(start, end) for start in before for end in after
                 if points[start][0] * points[end][0] < 0
                 and abs(points[end][0] - points[start][0]) >= 0.9
                 and 200 <= times[end] - times[start] <= 1200]
        if not pairs:
            continue
        # Choose the shortest window that satisfies *all* evidence gates. A
        # minimal crossing can omit a real earlier racket observation (for
        # example when the racket becomes blurred during the fast sweep).
        # Rejecting that one pair must not reject every supported window of
        # the same observed peak. Existing crossing, path and duration limits
        # remain in force; low rigid-arm motion needs independent local support.
        supported = None
        ordered_pairs = sorted(pairs, key=lambda pair: times[pair[1]] - times[pair[0]])
        shortest_support = _racket_support(segment, side, *ordered_pairs[0])
        for start, end in ordered_pairs:
            window = points[start:end + 1]
            horizontal = abs(points[end][0] - points[start][0])
            vertical = max(point[1] for point in window) - min(point[1] for point in window)
            if min(point[1] for point in window) < -0.5 or horizontal < vertical * 0.8:
                continue
            path = sum(_distance(a, b) for a, b in zip(window, window[1:]))
            if path > horizontal * 2.5:
                continue
            support = _racket_support(segment, side, start, end)
            if support >= 2:
                if shortest_support < 2 and min(point[1] for point in window) > SEQUENCE_RULES["low_hand_y_torso"]:
                    # An earlier racket box does not distinguish a new stroke
                    # from walking/turning while the arm hangs almost rigidly.
                    # Such low-arm context stays in the separate review route;
                    # we do not label it as retrieval or pretend no stroke took
                    # place. A visibly articulated low stroke remains eligible.
                    extension = [_distance(s.points[f"{side}_wrist"], s.points[f"{side}_shoulder"])
                                 for s in segment[start:end + 1] if f"{side}_shoulder" in s.points]
                    if (len(extension) != end - start + 1
                            or max(extension) - min(extension) < SEQUENCE_RULES["minimum_low_arm_range_for_context_only_racket_support_torso"]):
                        continue
                supported = start, end, horizontal, support
                break
        if supported is None:
            continue
        start, end, horizontal, support = supported
        result.append(_candidate(segment, side, start, peak, end, "baseline", {
            "racket_associated_frames": support, "pose_samples": end - start + 1,
            "horizontal_excursion_torso_units": round(horizontal, 3),
            "peak_wrist_speed_torso_units_per_second": round(speed[peak], 3),
            "court_location_confirmed": False, "forehand_backhand_classification": "unavailable",
        }))
    return result


def _detect_tracks(by_track: Mapping[int, list[Sample]]) -> list[dict[str, Any]]:
    from .stroke_analysis import candidate_hand_evidence

    candidates = []
    for samples in by_track.values():
        # Both hands can produce reviewable candidates using local racket
        # support. A track-wide vote cannot veto an observed later motion.
        # Keep source order so a timestamp reset cannot be silently reordered.
        for side in ("left", "right"):
            for segment in _segments(samples, side):
                candidates.extend(_detect_segment(segment, side))
    # Check ownership before competing hands can suppress each other. The
    # context and voting gates are exactly those used by episode analysis.
    # An unresolved alternative is safer than a known hand-side conflict;
    # this selects a hypothesis, never confirms handedness or technique.
    ranks = {"matched": 0, "unresolved": 1, "conflict": 2, "unavailable_context": 3}
    for candidate in candidates:
        candidate["evidence"]["hand_consistency_before_suppression"] = candidate_hand_evidence(
            candidate, by_track[candidate["person_track_id"]])
    # Suppress duplicate peaks and both-hand detections of the same motion.
    # Serve wins an overlapping generic swing; identities never suppress each other.
    candidates.sort(key=lambda item: (item["family"] != "serve",
                    0 if item["family"] == "serve" else ranks[item["evidence"]["hand_consistency_before_suppression"]["status"]],
                    -item["evidence"]["racket_associated_frames"]))
    accepted: list[dict[str, Any]] = []
    for candidate in candidates:
        if any(other["person_track_id"] == candidate["person_track_id"]
               and (abs(other["peak_ms"] - candidate["peak_ms"]) < 1200
                    or (other["family"] == "serve" and candidate["family"] == "baseline"
                        and other["start_ms"] - 300 < candidate["peak_ms"] < other["end_ms"] + 1200)
                    or max(other["start_ms"], candidate["start_ms"]) < min(other["end_ms"], candidate["end_ms"]))
               for other in accepted):
            continue
        accepted.append(candidate)
    accepted = _group_supporting_sweeps(accepted, by_track)
    accepted.sort(key=lambda item: (item["start_ms"], item["person_track_id"]))
    return accepted


def _group_supporting_sweeps(candidates: list[dict[str, Any]], by_track: Mapping[int, list[Sample]]) -> list[dict[str, Any]]:
    """Keep a slower, low-arm reversal as context for the main upward swing.

    A body-side crossing alone also occurs during takeback and recovery. The
    same observed wrist must reverse direction, move substantially faster in
    the principal sweep, and finish that sweep higher. The weaker low-arm
    motion is retained as evidence, never reported as an extra stroke. This is
    a conservative sequence rule, not a learned action/contact classifier.
    """
    from .stroke_analysis import _episode_context
    from .temporal_recognition import wrist_kinematics
    from .vector_kinematics import WristKinematicsUnavailable

    def feature(candidate, side):
        context = _episode_context(candidate, by_track[candidate["person_track_id"]])
        if not context or any(f"{side}_wrist" not in s.points for s in context):
            return None
        try:
            points, speed = wrist_kinematics(context, side)
        except WristKinematicsUnavailable:
            return None
        search = [i for i in range(2, len(context) - 2)
                  if candidate["start_ms"] <= context[i].time <= candidate["end_ms"]]
        if not search:
            return None
        peak = max(search, key=lambda i: speed[i])
        before = min(search, key=lambda i: abs(context[i].time - (context[peak].time - 150)))
        after = min(search, key=lambda i: abs(context[i].time - (context[peak].time + 150)))
        sweep = [i for i in range(len(context)) if context[peak].time - 150 <= context[i].time <= context[peak].time + 650]
        own = [i for i in range(len(context)) if candidate["start_ms"] <= context[i].time <= candidate["end_ms"]]
        if before == after or not own or not sweep:
            return None
        terminal = [i for i in own if context[i].time >= candidate["end_ms"] - SEQUENCE_RULES["terminal_low_hand_window_ms"]]
        extension = [_distance(context[i].points[f"{side}_wrist"], context[i].points[f"{side}_shoulder"])
                     for i in own if f"{side}_shoulder" in context[i].points]
        return {"context": context, "peak": context[peak].time, "speed": speed[peak],
                "direction": points[after][0] - points[before][0],
                "minimum_y": min(points[i][1] for i in sweep),
                "upward_excursion": points[before][1] - min(points[i][1] for i in sweep),
                "terminal_low_arm_fraction": sum(points[i][1] > SEQUENCE_RULES["low_hand_y_torso"] for i in terminal) / len(terminal),
                "start_y": points[own[0]][1], "end_y": points[own[-1]][1],
                "rise": points[own[0]][1] - points[own[-1]][1],
                "horizontal_excursion": abs(points[own[-1]][0] - points[own[0]][0]),
                "arm_extension_range": max(extension) - min(extension) if len(extension) == len(own) else None}

    baseline = [c for c in candidates if c["family"] == "baseline"]
    features = {(c["candidate_id"], side): feature(c, side) for c in baseline for side in ("left", "right")}
    removed = set()
    for main in sorted(baseline, key=lambda c: -(features[c["candidate_id"], c["racket_hand_candidate"]] or {}).get("speed", 0)):
        if main["candidate_id"] in removed:
            continue
        side = main["racket_hand_candidate"]
        strong = features[main["candidate_id"], side]
        if not strong:
            continue
        upward_main = (strong["upward_excursion"] >= SEQUENCE_RULES["minimum_main_upward_excursion_torso"]
                       and strong["minimum_y"] <= SEQUENCE_RULES["low_hand_y_torso"])
        for other in baseline:
            if other is main or other["candidate_id"] in removed or other["person_track_id"] != main["person_track_id"]:
                continue
            weak = features[other["candidate_id"], side]
            gap_min, gap_max = SEQUENCE_RULES["counter_sweep_peak_separation_ms"]
            if (not weak or not gap_min <= abs(weak["peak"] - strong["peak"]) <= gap_max
                    or weak["speed"] > strong["speed"] * SEQUENCE_RULES["maximum_supporting_to_main_speed_ratio"]
                    or weak["direction"] * strong["direction"] >= 0):
                continue
            relation = "preparation" if weak["peak"] < strong["peak"] else "recovery"
            low_support = upward_main and weak["terminal_low_arm_fraction"] >= SEQUENCE_RULES["terminal_low_hand_fraction"]
            raised_support = (relation == "preparation"
                and weak["start_y"] > SEQUENCE_RULES["low_hand_y_torso"]
                and weak["end_y"] <= SEQUENCE_RULES["maximum_raised_preparation_terminal_y_torso"]
                and weak["rise"] >= SEQUENCE_RULES["minimum_main_upward_excursion_torso"]
                and weak["rise"] >= weak["horizontal_excursion"] * SEQUENCE_RULES["minimum_raise_vertical_to_horizontal_ratio"])
            if not low_support and not raised_support:
                continue
            # Distinct independently supported racket hands can describe two
            # real strokes. Do not relabel that evidence as one-hand takeback.
            other_hand = other["evidence"].get("hand_consistency_before_suppression", {}).get("hand_evidence", {}).get("hand")
            if other_hand is not None and other_hand != side:
                continue
            # Candidate IDs and track numbers do not establish continuity.
            # Recheck the entire intervening source interval, including wrists.
            begin, end = sorted((weak["peak"], strong["peak"]))
            interval = [s for s in by_track[main["person_track_id"]] if begin <= s.time <= end]
            if (not interval or any(f"{side}_wrist" not in s.points for s in interval)
                    or any(not samples_are_continuous(a, b) for a, b in zip(interval, interval[1:]))):
                continue
            try:
                wrist_kinematics(interval, side)
            except WristKinematicsUnavailable:
                # Also reject a missing native source frame shorter than the
                # absolute 160 ms cap (for example one hole at 60 fps).
                continue
            if raised_support:
                # A raised setup is not an independent follow-through when
                # the same wrist stays raised until the faster reverse sweep.
                # A completed first stroke followed by a lower reset remains
                # separate, even if the next stroke is faster.
                hold = [s for s in interval if other["end_ms"] <= s.time <= strong["peak"] - 150]
                if (len(hold) < 2 or hold[-1].time - hold[0].time < 100
                        or any(s.points[f"{side}_wrist"][1] > SEQUENCE_RULES["low_hand_y_torso"] for s in hold)):
                    continue
            if relation == "preparation":
                # A return across the torso and another takeback before the
                # next acceleration is independent reset/new-preparation
                # evidence, even if the first low stroke was slower.
                middle = [s.points[f"{side}_wrist"][0] for s in interval
                          if weak["peak"] + 150 <= s.time <= strong["peak"] - 150]
                sign = 1 if weak["direction"] > 0 else -1
                margin = SEQUENCE_RULES["reset_body_side_margin_torso"]
                reset = next((i for i, x in enumerate(middle) if x * sign < -margin), None)
                if reset is not None and any(x * sign > margin for x in middle[reset + 1:]):
                    continue
            elif (weak["arm_extension_range"] is None
                  or weak["arm_extension_range"] >= SEQUENCE_RULES["maximum_recovery_arm_extension_range_torso"]):
                # A low forward swing may still articulate the arm. Only a
                # nearly fixed-length low-arm return is supporting recovery.
                continue
            evidence = {"candidate_id": other["candidate_id"], "start_ms": other["start_ms"],
                        "peak_ms": other["peak_ms"], "end_ms": other["end_ms"], "relation": relation,
                        "reason_code": ("raised_preparation_before_faster_counter_sweep" if raised_support
                                        else "slower_low_arm_counter_sweep_in_same_sequence"),
                        "wrist_side_compared": side, "main_motion_peak_ms": strong["peak"],
                        "supporting_motion_peak_ms": weak["peak"],
                        "speed_ratio": round(weak["speed"] / strong["speed"], 4),
                        "rule_version": SEQUENCE_RULE_VERSION,
                        "automatic_scoring_allowed": False}
            main["evidence"].setdefault("supporting_motion_candidates", []).append(evidence)
            if relation == "preparation":
                main["evidence"]["linked_preparation_start_ms"] = min(other["start_ms"], main["evidence"].get("linked_preparation_start_ms", main["start_ms"]))
            removed.add(other["candidate_id"])
    return [c for c in candidates if c["candidate_id"] not in removed]


def detect_stroke_candidates(
    frames: Iterable[Mapping[str, Any]], *,
    primary_timeline: Iterable[Mapping[str, Any]] | None = None,
    temporal_backend: TemporalRecognitionBackend | None = None,
) -> dict[str, Any]:
    """Read tracked motion and measurements without claiming confirmed contact.

    Primary-player selection gates every measured episode. Other tracks are
    only context for a potential return and must never become primary episodes.
    """
    from .stroke_analysis import build_motion_analysis

    selected = None if primary_timeline is None else {
        item.get("processed_index"): item.get("source_track_id")
        for item in primary_timeline if isinstance(item, Mapping) and item.get("selection_status") == "selected"
        and item.get("identity_ambiguous") is not True
    }
    by_track: dict[int, list[Sample]] = defaultdict(list)
    all_tracks: dict[int, list[Sample]] = defaultdict(list)
    ball_observations = []
    seen: set[tuple[int, int]] = set()
    frame_count = 0
    selection_epoch, previous_selected, previous_camera_epoch = 0, None, None
    for record in frames:
        if not isinstance(record, Mapping) or not isinstance(record.get("frame"), Mapping):
            continue
        frame = record["frame"]
        camera = record.get("camera_motion")
        camera_transform = None
        if isinstance(camera, Mapping):
            from rallymate_features.coordinates import validated_camera_transform
            camera_status = camera.get("status", "unavailable")
            camera_epoch = camera.get("reference_epoch")
            camera_transform = validated_camera_transform(camera)
            declared_compensation = "compensation_valid" in camera or "matrix_to_reference" in camera
            if declared_compensation and (camera_transform is None
                    or isinstance(camera_epoch, bool) or not isinstance(camera_epoch, int) or camera_epoch < 0
                    or camera_status not in {"reference", "stationary", "moving"}):
                camera_status = "unavailable"
            elif camera_status == "moving" and camera_transform is not None:
                camera_status = "compensated"
            if camera_status not in {"reference", "stationary", "compensated", "moving", "unavailable"}:
                camera_status = "unavailable"
            frame = {**frame, "camera_motion_status": camera_status, "camera_motion": camera}
            if previous_camera_epoch is not None and camera_epoch != previous_camera_epoch:
                selection_epoch += 1
            previous_camera_epoch = camera_epoch
        frame_count += 1
        current_selected = selected.get(frame.get("processed_index")) if selected is not None else None
        if selected is not None and current_selected != previous_selected:
            selection_epoch += 1
        previous_selected = current_selected
        if frame.get("timestamp_source") == "fps_fallback" or frame.get("camera_motion_status") in {"moving", "unavailable"}:
            # A skipped sample must still split later motion/rotation windows.
            selection_epoch += 1
        samples = [sample for pose in record.get("poses", []) if isinstance(pose, Mapping)
                   and (sample := _sample(pose, frame)) is not None]
        _associate_rackets(samples, record.get("detections", []))
        for sample in samples:
            sample.selection_epoch = selection_epoch
            key = sample.track, sample.time
            if key in seen:
                continue
            seen.add(key)
            all_tracks[sample.track].append(sample)
            if selected is None or current_selected == sample.track:
                sample.selection_epoch = selection_epoch
                by_track[sample.track].append(sample)
        for detection in record.get("detections", []):
            if frame.get("timestamp_source") == "fps_fallback" or frame.get("camera_motion_status") in {"moving", "unavailable"}:
                continue
            if not isinstance(detection, Mapping) or detection.get("class_name") != "ball":
                continue
            track, confidence = detection.get("track_id"), _number(detection.get("confidence"))
            box, timestamp = detection.get("bbox_px"), _number(frame.get("timestamp_ms"))
            if (isinstance(track, int) and not isinstance(track, bool) and confidence is not None and confidence >= 0.45
                    and timestamp is not None and isinstance(box, (list, tuple)) and len(box) == 4
                    and all(_number(v) is not None for v in box) and box[2] > box[0] and box[3] > box[1]):
                x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                if camera_transform is not None:
                    x, y = (float(camera_transform[0, 0] * x + camera_transform[0, 1] * y + camera_transform[0, 2]),
                            float(camera_transform[1, 0] * x + camera_transform[1, 1] * y + camera_transform[1, 2]))
                ball_observations.append({"track": track, "time": int(timestamp), "point": (x, y),
                                          "camera_reference_epoch": camera.get("reference_epoch") if isinstance(camera, Mapping) else None})
    # The dictionaries share Sample instances: normalize once before either
    # primary detection or opponent-context detection uses their geometry.
    _stabilize_partial_torso_windows(all_tracks)
    accepted = _detect_tracks(by_track)
    from .stroke_review_fallback import build_review_fallback_candidates
    review_candidates = build_review_fallback_candidates(by_track, accepted)
    # Need the opponent's full tracked serve as context, not the primary filter.
    context_serves = [candidate for candidate in _detect_tracks(all_tracks) if candidate["family"] == "serve"] if selected is not None else []
    counts = Counter(item["family"] for item in accepted)
    valid_samples = sum(len(samples) for samples in by_track.values())
    return {
        "schema_version": "1.0.0", "detector_version": DETECTOR_VERSION,
        "status": "candidates_detected" if accepted else "no_candidates" if valid_samples >= 7 else "insufficient_pose",
        "scope": "primary_player_timeline" if selected is not None else "all_tracks_independently",
        "candidate_count": len(accepted), "by_family": {"baseline": counts["baseline"], "serve": counts["serve"]},
        "supporting_motion_candidate_count": sum(len(c["evidence"].get("supporting_motion_candidates", [])) for c in accepted),
        "sequence_rules": {"version": SEQUENCE_RULE_VERSION, "thresholds": SEQUENCE_RULES,
                           "semantics": "engineering_motion_proposals_not_technique_score_thresholds"},
        "confirmed_contact_count": None, "candidates": accepted,
        "review_candidates": review_candidates, "review_candidate_count": len(review_candidates),
        "review_count_semantics": "secondary_motion_windows_for_review_not_confirmed_strokes_or_scores",
        "motion_analysis": build_motion_analysis(accepted, by_track, all_tracks=all_tracks,
                                                  context_serves=context_serves, ball_observations=ball_observations,
                                                  temporal_backend=temporal_backend),
        "diagnostics": {"frame_count": frame_count, "valid_pose_samples": valid_samples, "track_count": len(by_track)},
        "count_semantics": "reviewable_motion_candidates_not_hits_or_confirmed_ball_racket_contacts",
        "limitations_zh": LIMITATIONS,
    }


def recognize_strokes_from_artifacts(frames_path: str | Path, primary_timeline_path: str | Path) -> dict[str, Any]:
    """Derive candidates for new or completed runs without loading GPU models.

    Missing/corrupt artifacts mean unavailable analysis, never zero hits. A
    primary timeline is required here so API backfills use the same athlete as
    the original scoring pipeline.
    """
    try:
        with Path(primary_timeline_path).open(encoding="utf-8") as handle:
            timeline = [json.loads(line) for line in handle if line.strip()]
        with Path(frames_path).open(encoding="utf-8") as handle:
            return detect_stroke_candidates((json.loads(line) for line in handle if line.strip()), primary_timeline=timeline)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        from .stroke_analysis import build_motion_analysis
        motion_analysis = build_motion_analysis([], {})
        motion_analysis["status"] = "unavailable"
        for family in motion_analysis["families"].values():
            family["reason_zh"] = "本次分析所需的逐帧姿态或主球员时间线不可用，无法测量动作；未据此判断动作不存在。"
        return {"schema_version": "1.0.0", "detector_version": DETECTOR_VERSION,
                "status": "unavailable", "candidate_count": None,
                "by_family": {"baseline": None, "serve": None}, "candidates": [],
                "motion_analysis": motion_analysis,
                "confirmed_contact_count": None, "error_kind": type(exc).__name__,
                "limitations_zh": ["动作候选分析所需的逐帧姿态或主球员时间线不可用；未据此判定动作不存在。", *LIMITATIONS]}
