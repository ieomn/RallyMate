"""Replaceable temporal recognition, with explicit observed-data contracts.

MMAction2's documented M,T,V,C skeleton layout is the interoperability model:
https://mmaction2.readthedocs.io/en/latest/dataset_zoo/skeleton.html
FineDiving motivates keeping corresponding stages separate from quality:
https://github.com/xujinglin/FineDiving
No code, dataset, weights or claimed tennis accuracy is imported from either.
Coordinates here are torso-normalized 2D observations, NOT a ready-made input
to a pretrained COCO/PoseC3D model. An adapter must honor the joint order,
coordinate units, missing mask and source timestamps before using such a model.
"""
from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import statistics
from typing import Any, Protocol
from .pose_observations import MODEL_ESTIMATE, measurement_provenance, sample_source_reference, source_time_is_continuous
from .vector_kinematics import WristKinematicsUnavailable, vector_trajectory


CONTRACT_VERSION = "temporal-pose-window-v1.0.0"
HALPE26_JOINTS = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee",
    "right_knee", "left_ankle", "right_ankle", "head", "neck", "hip",
    "left_big_toe", "right_big_toe", "left_small_toe", "right_small_toe",
    "left_heel", "right_heel",
)
LABELS = {"forehand": "正手挥拍", "backhand": "单手反手挥拍", "two_handed_backhand": "双手反手挥拍",
          "unclassified": "挥拍（类型待确认）", "serve_motion": "发球动作", "return_motion": "接发动作"}


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def samples_are_continuous(before: Any, after: Any, *, max_gap_ms: int = 160) -> bool:
    """Use source time, identity and camera reference; never sort across resets."""
    return (0 < after.time - before.time <= max_gap_ms
            and before.track == after.track
            and before.selection_epoch == after.selection_epoch
            and getattr(before, "camera_reference_epoch", None) == getattr(after, "camera_reference_epoch", None)
            and getattr(before, "normalization_basis", "full_torso") == getattr(after, "normalization_basis", "full_torso")
            and sample_source_reference(before) == sample_source_reference(after)
            and source_time_is_continuous(before, after)
            and before.scale > 0 and after.scale > 0
            and _distance(before.center, after.center) / after.scale < 0.8
            and 0.65 < after.scale / before.scale < 1.55)


@dataclass(frozen=True)
class TemporalPoseWindow:
    """One athlete in one continuous camera/selection/normalization epoch.

    ``samples`` preserve the original irregular observations for geometry.
    ``model_input`` provides fixed-rate nearest observations with an explicit
    mask. Missing positions have storage zeroes, which MUST be masked out;
    no interpolation, extra observations or neutral-confidence values exist.
    """

    samples: tuple[Any, ...]
    model_input: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        data = self.model_input
        return {key: data[key] for key in (
            "schema_version", "joint_layout", "tensor_layout", "coordinate_system",
            "coordinate_unit", "person_track_id", "selection_epoch", "camera_reference_epoch",
            "normalization_basis", "sample_rate_hz", "sampling_method", "observed_sample_count",
            "total_frames", "start_ms", "end_ms", "observed_joint_fraction", "source_time_status",
            "measurement_provenance",
        )}


def build_temporal_window(samples: Sequence[Any], *, sample_rate_hz: int = 20) -> TemporalPoseWindow:
    """Sample real elapsed time, with <= half-period nearest-observation error.

    This returns serializable M=1 tensors rather than importing a model runtime.
    Confidence remains the original pose confidence. A legacy point lacking its
    confidence remains a valid observed point but has a zero model score and is
    separately marked in ``confidence_available_mask``.
    """
    if not samples or not isinstance(sample_rate_hz, int) or isinstance(sample_rate_hz, bool) or not 1 <= sample_rate_hz <= 120:
        raise ValueError("A nonempty window and an integer sample rate from 1 to 120 are required")
    samples = tuple(samples)
    if any(isinstance(s.time, bool) or not isinstance(s.time, (int, float)) or not math.isfinite(s.time) for s in samples):
        raise ValueError("Every observation requires a finite source timestamp")
    if any(getattr(sample, "timestamp_source", None) == "fps_fallback" for sample in samples):
        raise ValueError("Fallback frame-rate time is not a measured timebase")
    if any(not samples_are_continuous(a, b) for a, b in zip(samples, samples[1:])):
        raise ValueError("Temporal window crosses an observation/identity/reference boundary")
    times = [sample.time for sample in samples]
    period = 1000 / sample_rate_hz
    targets = [round(times[0] + index * period, 6) for index in range(math.floor((times[-1] - times[0]) / period) + 1)]
    keypoints, scores, masks, confidence_masks, source_times, source_frames = [], [], [], [], [], []
    geometry_masks, estimate_masks = [], []
    for target in targets:
        insertion = bisect_left(times, target)
        options = [index for index in (insertion - 1, insertion) if 0 <= index < len(times)]
        nearest = min(options, key=lambda index: (abs(times[index] - target), index))
        sample = samples[nearest] if abs(times[nearest] - target) <= period / 2 else None
        points = sample.points if sample else {}
        confidence = getattr(sample, "point_confidences", {}) if sample else {}
        row, score_row, mask_row, confidence_row, geometry_row, estimate_row = [], [], [], [], [], []
        estimated = getattr(sample, "observation_semantics", None) == MODEL_ESTIMATE
        for name in HALPE26_JOINTS:
            point = points.get(name)
            valid = (isinstance(point, (tuple, list)) and len(point) == 2
                     and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in point))
            score = confidence.get(name)
            score_valid = valid and isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score) and 0 <= score <= 1
            row.append(list(point) if valid else [0.0, 0.0])
            score_row.append(float(score) if score_valid else 0.0)
            mask_row.append(bool(valid and not estimated))
            geometry_row.append(bool(valid))
            estimate_row.append(bool(valid and estimated))
            confidence_row.append(bool(score_valid))
        keypoints.append(row)
        scores.append(score_row)
        masks.append(mask_row)
        confidence_masks.append(confidence_row)
        geometry_masks.append(geometry_row)
        estimate_masks.append(estimate_row)
        source_times.append(sample.time if sample else None)
        source_frames.append(sample.frame_index if sample else None)
    first = samples[0]
    data = {
        "schema_version": CONTRACT_VERSION, "joint_layout": "halpe26",
        "joint_names": list(HALPE26_JOINTS), "tensor_layout": "M,T,V,C",
        "coordinate_system": "body_relative_image_x_right_y_down",
        "coordinate_unit": "torso_length", "normalization_basis": getattr(first, "normalization_basis", "full_torso"),
        "person_track_id": first.track, "selection_epoch": first.selection_epoch,
        "camera_reference_epoch": getattr(first, "camera_reference_epoch", None),
        "start_ms": times[0], "end_ms": times[-1], "sample_rate_hz": sample_rate_hz,
        "sampling_method": "nearest_observation_with_half_period_tolerance_no_interpolation",
        "source_time_status": "declared_source_timestamps" if all(getattr(s, "timestamp_source", None) for s in samples) else "legacy_source_timestamps",
        "observed_sample_count": len(samples), "total_frames": len(targets),
        "timestamp_ms": targets, "source_timestamp_ms": source_times, "source_frame_index": source_frames,
        "keypoint": [keypoints], "keypoint_score": [scores], "observed_mask": [masks],
        "confidence_available_mask": [confidence_masks],
        "geometry_available_mask": [geometry_masks], "model_estimate_mask": [estimate_masks],
        "measurement_provenance": measurement_provenance(samples),
        "observed_joint_fraction": round(sum(sum(row) for row in masks) / (len(targets) * len(HALPE26_JOINTS)), 4),
    }
    return TemporalPoseWindow(samples, data)


class TemporalRecognitionBackend(Protocol):
    """Model adapters return source-time stages, never contact events/scores."""

    def metadata(self) -> Mapping[str, Any]: ...

    def recognize(self, window: TemporalPoseWindow, candidate: Mapping[str, Any],
                  hand_evidence: Mapping[str, Any]) -> dict[str, Any] | None: ...


def wrist_kinematics(samples: Sequence[Any], side: str) -> tuple[list[tuple[float, float]], list[float]]:
    trajectory = vector_trajectory([s.time for s in samples], [s.points.get(f"{side}_wrist") for s in samples],
        continuous=[samples_are_continuous(a,b) for a,b in zip(samples,samples[1:])],
        frame_indices=[s.frame_index for s in samples])
    if not trajectory.interval_speed or any(p is None for p in trajectory.points) or any(v is None for v in trajectory.interval_speed):
        raise WristKinematicsUnavailable("Wrist kinematics requires a complete continuous observed window")
    # The rule backend selects original frames. Associate each interval with
    # its ending frame; the first frame uses the first observed interval.
    # No endpoint is assigned an artificial zero velocity.
    return trajectory.points, [trajectory.interval_speed[0], *trajectory.interval_speed]


def _axis(sample: Any) -> tuple[float, float] | None:
    left, right = sample.points.get("left_shoulder"), sample.points.get("right_shoulder")
    if left is None or right is None:
        return None
    dx, dy = right[0] - left[0], right[1] - left[1]
    span = math.hypot(dx, dy)
    if span < 0.3:
        return None
    hip_left, hip_right = sample.points.get("left_hip"), sample.points.get("right_hip")
    if hip_left is None or hip_right is None:
        return None
    hx, hy = hip_right[0] - hip_left[0], hip_right[1] - hip_left[1]
    hip_span = math.hypot(hx, hy)
    if hip_span < 0.18 or (dx * hx + dy * hy) / span / hip_span < 0.65:
        return None
    return dx / span, dy / span


def _classification(samples: list[Any], side: str, hand_evidence: Mapping[str, Any], family: str) -> dict[str, Any]:
    if family == "serve":
        return {"label": "serve_motion", "label_zh": LABELS["serve_motion"], "status": "rule_inferred",
                "reason_zh": "已测得抛球侧手臂先抬起、持拍臂过顶及随后随挥的顺序；未确认触球。"}
    def unknown(reason: str) -> dict[str, Any]:
        return {"label": "unclassified", "label_zh": LABELS["unclassified"], "status": "unclassified", "reason_zh": reason}
    if len(samples) < 5:
        return unknown("完整的类型判定时间窗未被连续观测覆盖，保留运动测量。")
    if hand_evidence.get("hand") not in {"left", "right"}:
        return unknown("持拍手的独立球拍关联不足，保留挥拍测量，暂不判断正反手。")
    if hand_evidence["hand"] != side:
        return unknown("局部持拍手证据与当前挥拍候选侧不一致，保留该侧运动测量，暂不判断正反手。")
    axes = [_axis(sample) for sample in samples]
    if sum(axis is not None for axis in axes) < len(samples) * 0.85:
        return unknown("身体侧轴过于侧向或肩髋方向不一致，正反手暂不可区分。")
    valid_axes = [axis for axis in axes if axis is not None]
    ref = valid_axes[0]
    if any(axis[0] * ref[0] + axis[1] * ref[1] < 0.65 for axis in valid_axes):
        return unknown("肩部侧轴在动作中变化较大，二维画面不足以稳定区分正反手。")
    sign = 1 if side == "right" else -1
    projections = [sign * ((sample.points[f"{side}_wrist"][0] - (sample.points["left_shoulder"][0] + sample.points["right_shoulder"][0]) / 2) * axis[0]
                          + (sample.points[f"{side}_wrist"][1] - (sample.points["left_shoulder"][1] + sample.points["right_shoulder"][1]) / 2) * axis[1])
                   for sample, axis in zip(samples, axes) if axis is not None]
    edge = max(2, len(projections) // 4)
    before, after = statistics.median(projections[:edge]), statistics.median(projections[-edge:])
    other = "left" if side == "right" else "right"
    wrist_pairs = [(sample.points[f"{side}_wrist"], sample.points.get(f"{other}_wrist")) for sample in samples]
    distances = [_distance(a, b) for a, b in wrist_pairs if b is not None]
    if len(distances) < len(samples) * 0.85:
        return unknown("另一侧手腕覆盖不足，不能判断单手或双手挥拍。")
    coupled = sum(distance < 0.45 for distance in distances) / len(distances)
    separated = sum(distance > 0.6 for distance in distances) / len(distances)
    # Require a directionally coherent sweep, not a one-frame body-side crossing.
    if before > 0.3 and after < before - 0.65 and after < 0.1 and separated >= 0.6:
        label, reason = "forehand", "持拍手明确；手腕由持拍侧向身体另一侧运动，另一手保持分离。"
    elif before < -0.3 and after > before + 0.65 and after > -0.1:
        # Nearby wrists alone are insufficient: both must travel together.
        paired = [(a, b) for a, b in wrist_pairs if b is not None]
        delta_a = (paired[-1][0][0] - paired[0][0][0], paired[-1][0][1] - paired[0][0][1])
        delta_b = (paired[-1][1][0] - paired[0][1][0], paired[-1][1][1] - paired[0][1][1])
        length_a, length_b = math.hypot(*delta_a), math.hypot(*delta_b)
        common_direction = ((delta_a[0] * delta_b[0] + delta_a[1] * delta_b[1]) / length_a / length_b
                            if min(length_a, length_b) > 0.01 else -1)
        if coupled >= 0.7 and length_b >= length_a * 0.5 and common_direction >= 0.7:
            label, reason = "two_handed_backhand", "持拍手明确；双腕持续靠近并由非持拍侧向持拍侧运动。"
        elif separated >= 0.7:
            label, reason = "backhand", "持拍手明确；手腕由非持拍侧向持拍侧运动，双腕保持分离。"
        else:
            return unknown("反手方向有支持，但双腕关系不足以区分单手与双手。")
    else:
        return unknown("挥拍时序可测，身体侧向位移或双腕关系尚不足以可靠区分类型。")
    return {"label": label, "label_zh": LABELS[label], "status": "rule_inferred", "reason_zh": reason}



def _external_peak_sweep_support(samples, side, start, pivot, end):
    """An expanded-context peak must own the original candidate's geometry.

    Use the detector's same three-observation median and admissible windows.
    This prevents a short preparatory lift from borrowing a later crossing,
    while a real low sweep may retain racket support through blurred frames.
    No ball contact or ideal body-joint sequence is inferred.
    """
    points = []
    for i in range(len(samples)):
        nearby = [s.points[f"{side}_wrist"] for s in samples[max(0, i - 1):i + 2]]
        points.append(tuple(statistics.median(p[axis] for p in nearby) for axis in (0, 1)))
    times = [s.time for s in samples]
    before = [i for i in range(start, pivot) if 80 <= times[pivot] - times[i] <= 650]
    after = [i for i in range(pivot + 1, end + 1) if 80 <= times[i] - times[pivot] <= 650]
    tested = 0
    for a in before:
        for b in after:
            if not 200 <= times[b] - times[a] <= 1200:
                continue
            tested += 1
            window = points[a:b + 1]
            horizontal = abs(points[b][0] - points[a][0])
            vertical = max(p[1] for p in window) - min(p[1] for p in window)
            path = sum(_distance(p, q) for p, q in zip(window, window[1:]))
            if (points[a][0] * points[b][0] < 0 and horizontal >= .9
                    and min(p[1] for p in window) >= -.5 and horizontal >= vertical * .8
                    and path <= horizontal * 2.5):
                return None
    window = points[start:end + 1]
    return {"status": "not_supported", "tested_window_count": tested,
            "crosses_body_midline": points[start][0] * points[end][0] < 0,
            "horizontal_excursion_torso_units": round(abs(points[end][0] - points[start][0]), 6),
            "vertical_range_torso_units": round(max(p[1] for p in window) - min(p[1] for p in window), 6),
            "path_torso_units": round(sum(_distance(p, q) for p, q in zip(window, window[1:])), 6),
            "minimum_wrist_y_torso_units": round(min(p[1] for p in window), 6)}


def _low_rigid_arm_phase(samples, side, start, end):
    """A low fixed-length arm needs its own sweep evidence after re-centering.

    This is not a walking classifier or a ban on straight-arm strokes. The
    existing low-arm/extension limits only trigger a check of the reported
    phase's own geometry; a sufficiently wide low straight-arm sweep passes.
    """
    from .stroke_candidates import SEQUENCE_RULES
    phase = samples[start:end + 1]
    if not phase or any(f"{side}_shoulder" not in s.points for s in phase):
        return False
    extension = [_distance(s.points[f"{side}_wrist"], s.points[f"{side}_shoulder"]) for s in phase]
    return (all(s.points[f"{side}_wrist"][1] > SEQUENCE_RULES["low_hand_y_torso"] for s in phase)
            and max(extension) - min(extension)
            < SEQUENCE_RULES["minimum_low_arm_range_for_context_only_racket_support_torso"])


def _rule_recognition(window: TemporalPoseWindow, candidate: Mapping[str, Any], hand_evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    samples = list(window.samples)
    side = candidate["racket_hand_candidate"]
    wrists, speed = wrist_kinematics(samples, side)
    if candidate["family"] == "serve":
        # The already ordered overhead-extension pivot is distinct from the
        # fastest wrist motion and is visually validated for serve sequences.
        pivot = min(range(len(samples)), key=lambda i: abs(samples[i].time - candidate["peak_ms"]))
    else:
        # A geometric crossing can occur late in recovery. Use the already
        # validated local preparation context instead of clipping its first
        # 400 ms and accidentally selecting a smaller recovery burst.
        search = list(range(2, len(samples) - 2))
        if not search:
            return None
        pivot = max(search, key=lambda i: speed[i])
        if samples[pivot].time < candidate["start_ms"] - 250:
            # A sustained observed reset separates an earlier independent
            # action. Do not let its larger peak take over the next candidate.
            quiet = None
            independent_reset = False
            for i in range(pivot + 1, len(samples)):
                if samples[i].time > candidate["start_ms"]:
                    break
                if speed[i] <= speed[pivot] * .35:
                    quiet = i if quiet is None else quiet
                    if samples[i].time - samples[quiet].time >= 250:
                        independent_reset = True
                        break
                else:
                    quiet = None
            if independent_reset:
                local = [i for i in search if samples[i].time >= candidate["start_ms"] - 250]
                if not local:
                    return None
                pivot = max(local, key=lambda i: speed[i])
        if samples[pivot].time > candidate["end_ms"] + 400:
            # Symmetric to the earlier-context guard above: a genuinely new
            # action after a 250 ms observed reset cannot replace this one.
            # Otherwise a late forward sweep remains in the same already
            # validated context, rather than being cut off at end + 400 ms.
            local = [i for i in search if candidate["start_ms"] - 250 <= samples[i].time <= candidate["end_ms"] + 400]
            if local:
                local_peak = max(local, key=lambda i: speed[i])
                quiet = None
                for i in range(local_peak + 1, pivot):
                    if speed[i] <= speed[local_peak] * .35:
                        quiet = i if quiet is None else quiet
                        if samples[i].time - samples[quiet].time >= 250:
                            pivot = local_peak
                            break
                    else:
                        quiet = None
    if pivot < 2 or pivot >= len(samples) - 1:
        return None
    acceleration_window = [i for i in range(1, pivot + 1) if samples[pivot].time - samples[i].time <= 650]
    acceleration_peak = max(acceleration_window, key=lambda i: speed[i])
    acceleration_start = acceleration_peak
    while acceleration_start > 1 and speed[acceleration_start - 1] > speed[acceleration_peak] * 0.35:
        acceleration_start -= 1
    acceleration_start = min(acceleration_start, pivot - 1)
    preparation_start = min(range(acceleration_start + 1), key=lambda i: abs(samples[i].time - (samples[acceleration_start].time - 250)))
    linked_start = candidate.get("evidence", {}).get("linked_preparation_start_ms")
    if linked_start is not None:
        preparation_start = min(preparation_start, min(range(acceleration_start + 1), key=lambda i: abs(samples[i].time - linked_start)))
    if candidate["family"] == "serve":
        # Keep the earlier opposite-arm lift in the serve preparation instead
        # of cropping the episode to only its last acceleration burst.
        toss_context_start = min(range(acceleration_start + 1), key=lambda i: abs(samples[i].time - (candidate["start_ms"] - 200)))
        preparation_start = min(preparation_start, toss_context_start)
    preparation_complete = samples[acceleration_start].time - samples[preparation_start].time >= 100
    # End only after observed slowdown persists for >=100 ms; a clipped input
    # or interrupted identity must not invent a completed follow-through.
    quiet_start = None
    follow_end = len(samples) - 1
    follow_complete = False
    for i in range(pivot + 1, len(samples) - 1):
        if samples[i].time - samples[pivot].time < 120:
            continue
        if speed[i] <= speed[acceleration_peak] * 0.35:
            if quiet_start is None:
                quiet_start = i
            if samples[i].time - samples[quiet_start].time >= 100:
                follow_end, follow_complete = i, True
                break
        else:
            quiet_start = None
    from .paired_motion import paired_wrist_phase_evidence, paired_arc_sweep_evidence
    paired_acceleration = paired_wrist_phase_evidence(samples, start_ms=samples[acceleration_start].time,
                                                     end_ms=samples[pivot].time, motion_wrist=side)
    motion_sequence_evidence = None
    phase_ambiguity = None
    if (candidate["family"] == "baseline" and preparation_complete and follow_complete
            and not candidate["start_ms"] <= samples[pivot].time <= candidate["end_ms"]
            and (samples[follow_end].time <= candidate["start_ms"]
                 or samples[preparation_start].time >= candidate["end_ms"]
                 or _low_rigid_arm_phase(samples, side, preparation_start, follow_end))):
        # A peak just beyond the candidate is still supported by overlapping
        # observations. This guard concerns a separate earlier/later pulse
        # whose reported stages borrow the other interval's evidence. A low,
        # nearly fixed-length arm also requires its own sweep: a few overlapping
        # endpoint frames cannot lend a later arm excursion to an earlier pulse.
        geometry = _external_peak_sweep_support(samples, side, preparation_start, pivot, follow_end)
        if geometry is not None:
            motion_sequence_evidence = paired_arc_sweep_evidence(samples,
                start_ms=samples[preparation_start].time, acceleration_start_ms=samples[acceleration_start].time,
                peak_ms=samples[pivot].time, end_ms=samples[follow_end].time, motion_wrist=side)
        if geometry is not None and motion_sequence_evidence["status"] != "supported":
            phase_ambiguity = {"reason_code": "external_peak_motion_geometry_unverified",
                "candidate_wrist": side, "candidate_start_ms": candidate["start_ms"],
                "candidate_end_ms": candidate["end_ms"], "candidate_motion_peak_ms": samples[pivot].time,
                "motion_window_start_ms": samples[preparation_start].time,
                "motion_window_end_ms": samples[follow_end].time, "local_sweep_geometry": geometry,
                "reason_zh": "主运动峰落在原候选窗口之外；该峰附近的连续观测未独立支持跨身挥拍，暂不能把局部举手或准备动作判为完整挥拍。"}
            preparation_complete = follow_complete = False
    # Short local racket support can retain an already measured wrist sequence
    # without meeting the stronger eight-vote requirement for stroke taxonomy.
    # Reuse the detector's two-frame evidence floor and ownership dominance:
    # a normal balancing-arm burst cannot erase a supported hitting-arm burst.
    votes = hand_evidence.get("separated_wrist_racket_votes", {})
    other_side = "left" if side == "right" else "right"
    side_supported = (votes.get(side, 0) >= 2
                      and votes.get(side, 0) >= max(1, votes.get(other_side, 0)) * 3)
    if (candidate["family"] == "baseline" and hand_evidence.get("hand") is None
            and not side_supported and follow_complete):
        other = "left" if side == "right" else "right"
        try:
            other_wrists, other_speed = wrist_kinematics(samples, other)
        except WristKinematicsUnavailable:
            other_wrists, other_speed = [], []
        for i in range(2, len(samples) - 2) if other_speed else ():
            # Without independent racket-hand evidence, two separated hands
            # making strong sequential sweeps do not identify which burst is
            # the forward stroke. Do not call the first hand's pause a complete
            # follow-through while the other starts its own strong excursion.
            if (not 100 <= samples[i].time - samples[pivot].time
                    or samples[i].time > samples[follow_end].time + 150
                    or other_speed[i] < speed[pivot] * .8
                    or speed[i] > speed[pivot] * .35
                    or _distance(wrists[i], other_wrists[i]) <= .55
                    or not (other_speed[i] >= other_speed[i - 1] and other_speed[i] > other_speed[i + 1])):
                continue
            a = min(range(i + 1), key=lambda j: abs(samples[j].time - (samples[i].time - 150)))
            b = min(range(i, len(samples)), key=lambda j: abs(samples[j].time - (samples[i].time + 150)))
            if _distance(other_wrists[a], other_wrists[b]) < .9:
                continue
            phase_ambiguity = {"reason_code": "unresolved_racket_hand_with_competing_arm_sequence",
                "candidate_wrist": side, "competing_wrist": other,
                "candidate_motion_peak_ms": samples[pivot].time,
                "competing_motion_peak_ms": samples[i].time,
                "reason_zh": "持拍手的独立球拍证据不足；两侧手腕先后出现较强运动，暂不能把其中一侧的停顿判作完整随挥。"}
            preparation_complete = follow_complete = False
            break
    phases = [
        {"phase": "preparation", "label_zh": "准备", "start_ms": samples[preparation_start].time if preparation_complete else None,
         "end_ms": samples[acceleration_start].time if preparation_complete else None, "status": "measured" if preparation_complete else "unavailable"},
        {"phase": "acceleration", "label_zh": "加速挥拍", "start_ms": samples[acceleration_start].time, "end_ms": samples[pivot].time, "status": "measured"},
        {"phase": "follow_through", "label_zh": "随挥", "start_ms": samples[pivot].time if follow_complete else None,
         "end_ms": samples[follow_end].time if follow_complete else None, "status": "measured" if follow_complete else "unavailable"},
    ]
    motion_peak_ms = samples[pivot].time
    for item in phases:
        if phase_ambiguity:
            item.update(start_ms=None, end_ms=None, status="unavailable")
        item.update(boundary_semantics="estimated_motion_phase", anchor_is_contact=False,
                    reason_code=(phase_ambiguity["reason_code"] if phase_ambiguity else
                                 "observed_motion_sequence" if item["status"] == "measured" else "stage_not_fully_observed"))
    candidate_samples = [sample for sample in samples if candidate["start_ms"] <= sample.time <= candidate["end_ms"]]
    return {"classification": _classification(candidate_samples, side, hand_evidence, candidate["family"]),
            "start_ms": samples[preparation_start].time, "peak_ms": motion_peak_ms, "end_ms": samples[follow_end].time,
            "phases": phases, "phase_timing_status": "estimated_from_2d_motion",
            "analysis_status": "complete" if preparation_complete and follow_complete else "partial",
            "phase_ambiguity": phase_ambiguity,
            "paired_wrist_acceleration": paired_acceleration,
            "motion_sequence_evidence": motion_sequence_evidence,
            "phase_method": "smoothed_wrist_speed_with_observed_slowdown", "contact_confirmed": False}


class RuleTemporalBackend:
    """Honest runnable baseline until a tennis-trained adapter is validated."""

    def metadata(self) -> dict[str, Any]:
        return {"backend_id": "pose-racket-temporal-rules", "version": "2.4.0", "kind": "deterministic_rules",
                "trained_tennis_model": False, "weights": None, "accuracy_validation": "not_established",
                "input_contract": CONTRACT_VERSION, "phase_comparison": "corresponding_observed_stage_only"}

    def recognize(self, window: TemporalPoseWindow, candidate: Mapping[str, Any],
                  hand_evidence: Mapping[str, Any]) -> dict[str, Any] | None:
        return _rule_recognition(window, candidate, hand_evidence)


def validate_recognition(result: Mapping[str, Any], window: TemporalPoseWindow) -> None:
    """Reject adapter output that invents time, crosses bounds or claims contact."""
    if not isinstance(result, Mapping):
        raise ValueError("Temporal backend output must be a mapping")
    times = {sample.time for sample in window.samples}
    if result.get("contact_confirmed") is not False:
        raise ValueError("Temporal skeleton backends cannot confirm racket contact")
    start, peak, end = (result.get(key) for key in ("start_ms", "peak_ms", "end_ms"))
    if any(isinstance(t, bool) or not isinstance(t, (int, float)) or t not in times for t in (start, peak, end)) or not start < peak < end:
        raise ValueError("Backend analysis bounds must be ordered observed source times")
    classification = result.get("classification")
    if (not isinstance(classification, Mapping) or classification.get("label") not in LABELS
            or classification.get("status") not in {"unclassified", "rule_inferred", "model_inferred"}):
        raise ValueError("Backend classification is outside the supported taxonomy")
    phases = result.get("phases")
    if (not isinstance(phases, list) or any(not isinstance(p, Mapping) for p in phases)
            or [p.get("phase") for p in phases] != ["preparation", "acceleration", "follow_through"]):
        raise ValueError("Backend must expose the three corresponding motion stages")
    previous_end = start
    for phase in phases:
        a, b = phase.get("start_ms"), phase.get("end_ms")
        if phase.get("status") == "unavailable":
            if a is not None or b is not None:
                raise ValueError("Missing stages cannot contain invented bounds")
        elif (phase.get("status") != "measured" or isinstance(a, bool) or isinstance(b, bool)
              or not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or a not in times or b not in times
              or not previous_end <= a < b <= end):
            raise ValueError("Measured stages must be ordered observed intervals")
        else:
            previous_end = b
        if phase.get("anchor_is_contact") is not False:
            raise ValueError("A motion phase anchor is not contact evidence")
    if result.get("analysis_status") not in {"complete", "partial"}:
        raise ValueError("Missing analysis completeness status")
    if not isinstance(result.get("phase_method"), str) or not isinstance(result.get("phase_timing_status"), str):
        raise ValueError("Temporal stage outputs must state their timing method")
