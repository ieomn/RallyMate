"""Measured image-plane stroke motion; no contact events or calibrated scores.

This module consumes tracked pose samples, not a second neural model. Anatomical
body-side coordinates make a left/right label independent of camera mirroring;
foreshortened or unstable axes and uncertain racket ownership remain unclassified.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
import math
import statistics
from typing import Any

ANALYSIS_VERSION = "stroke-motion-analysis-v1.0.1"
LIMITATIONS = [
    "阶段和类型由姿态、球拍及时间顺序规则推断，尚未经过专项标注集准确率验证。",
    "速度以躯干长度/秒表示；肩线变化和肘角为画面二维测量，不是实际球速、三维转体角或技术评分。",
    "动作片段不等于确认击球；未确认球拍触球，底线场区位置也未校准。",
    "运动峰值是平滑后的二维手腕运动参考时刻，不是触球时刻；阶段边界仍需人工复核。",
]
LABELS = {"forehand": "正手挥拍", "backhand": "单手反手挥拍", "two_handed_backhand": "双手反手挥拍",
          "unclassified": "挥拍（类型待确认）", "serve_motion": "发球动作", "return_motion": "接发动作"}


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def racket_hand_evidence(samples: list[Any]) -> dict[str, Any]:
    """Vote only when wrists are separated, so a two-hand grip cannot flip hand."""
    votes: Counter[str] = Counter()
    for sample in samples:
        left, right = sample.points.get("left_wrist"), sample.points.get("right_wrist")
        if left and right and _distance(left, right) > 0.55:
            votes.update(sample.racket_sides)
    common = votes.most_common()
    hand = None
    if common and common[0][1] >= 8 and common[0][1] >= max(1, votes["right" if common[0][0] == "left" else "left"]) * 3:
        hand = common[0][0]
    return {"hand": hand, "separated_wrist_racket_votes": dict(votes)}


def _angle(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float | None:
    u, v = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    upper, fore = math.hypot(*u), math.hypot(*v)
    lengths = upper * fore
    if min(upper, fore) < 0.15 or max(upper, fore) > min(upper, fore) * 4:
        return None
    return math.degrees(math.acos(max(-1, min(1, (u[0] * v[0] + u[1] * v[1]) / lengths))))


def _axis(sample: Any) -> tuple[float, float] | None:
    left, right = sample.points["left_shoulder"], sample.points["right_shoulder"]
    dx, dy = right[0] - left[0], right[1] - left[1]
    span = math.hypot(dx, dy)
    if span < 0.3:
        return None
    hip_left, hip_right = sample.points["left_hip"], sample.points["right_hip"]
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
    if hand_evidence.get("hand") != side:
        return unknown("持拍手的独立球拍关联不足，保留挥拍测量，暂不判断正反手。")
    axes = [_axis(sample) for sample in samples]
    if sum(axis is not None for axis in axes) < len(samples) * 0.85:
        return unknown("身体侧轴过于侧向或肩髋方向不一致，正反手暂不可区分。")
    valid_axes = [axis for axis in axes if axis is not None]
    ref = valid_axes[0]
    if any(axis[0] * ref[0] + axis[1] * ref[1] < 0.65 for axis in valid_axes):
        return unknown("肩部侧轴在动作中变化较大，二维画面不足以稳定区分正反手。")
    sign = 1 if side == "right" else -1
    projections = [sign * (sample.points[f"{side}_wrist"][0] * axis[0] + sample.points[f"{side}_wrist"][1] * axis[1])
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


def _episode(candidate: Mapping[str, Any], all_samples: list[Any], hand_evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    side = candidate["racket_hand_candidate"]
    # Context expands preparation/follow-through, but never crosses a missing
    # wrist, identity break, implausible pose jump, or >160 ms observation gap.
    samples = [sample for sample in all_samples if candidate["start_ms"] - 650 <= sample.time <= candidate["end_ms"] + 900]
    segments: list[list[Any]] = [[]]
    for sample in samples:
        valid = all(f"{side}_{joint}" in sample.points for joint in ("wrist", "elbow"))
        prev = segments[-1][-1] if segments[-1] else None
        continuous = prev is None or (0 < sample.time - prev.time <= 160 and sample.selection_epoch == prev.selection_epoch
                                      and _distance(sample.center, prev.center) / sample.scale < 0.8
                                      and 0.65 < sample.scale / prev.scale < 1.55)
        if not valid or not continuous:
            segments.append([])
        if valid:
            segments[-1].append(sample)
    samples = next((segment for segment in segments if segment and segment[0].time <= candidate["start_ms"]
                    and segment[-1].time >= candidate["end_ms"]), [])
    if len(samples) < 7:
        return None
    wrists = []
    for i in range(len(samples)):
        # Five samples and a wider central difference suppress left/right pose
        # jitter that previously moved the motion peak into racket recovery.
        nearby = [s.points[f"{side}_wrist"] for s in samples[max(0, i - 2):i + 3]]
        wrists.append((statistics.median(p[0] for p in nearby), statistics.median(p[1] for p in nearby)))
    speed = [0.0] * len(samples)
    for i in range(1, len(samples) - 1):
        before, after = max(0, i - 2), min(len(samples) - 1, i + 2)
        speed[i] = _distance(wrists[before], wrists[after]) * 1000 / (samples[after].time - samples[before].time)
    if candidate["family"] == "serve":
        # The already ordered overhead-extension pivot is distinct from the
        # fastest wrist motion and is visually validated for serve sequences.
        pivot = min(range(len(samples)), key=lambda i: abs(samples[i].time - candidate["peak_ms"]))
    else:
        search = [i for i in range(2, len(samples) - 2)
                  if candidate["start_ms"] - 250 <= samples[i].time <= candidate["end_ms"] + 400]
        if not search:
            return None
        pivot = max(search, key=lambda i: speed[i])
    if pivot < 2 or pivot >= len(samples) - 1:
        return None
    acceleration_window = [i for i in range(1, pivot + 1) if samples[pivot].time - samples[i].time <= 650]
    acceleration_peak = max(acceleration_window, key=lambda i: speed[i])
    acceleration_start = acceleration_peak
    while acceleration_start > 1 and speed[acceleration_start - 1] > speed[acceleration_peak] * 0.35:
        acceleration_start -= 1
    acceleration_start = min(acceleration_start, pivot - 1)
    preparation_start = min(range(acceleration_start + 1), key=lambda i: abs(samples[i].time - (samples[acceleration_start].time - 250)))
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
    phases = [
        {"phase": "preparation", "label_zh": "准备", "start_ms": samples[preparation_start].time if preparation_complete else None,
         "end_ms": samples[acceleration_start].time if preparation_complete else None, "status": "measured" if preparation_complete else "unavailable"},
        {"phase": "acceleration", "label_zh": "加速挥拍", "start_ms": samples[acceleration_start].time, "end_ms": samples[pivot].time, "status": "measured"},
        {"phase": "follow_through", "label_zh": "随挥", "start_ms": samples[pivot].time if follow_complete else None,
         "end_ms": samples[follow_end].time if follow_complete else None, "status": "measured" if follow_complete else "unavailable"},
    ]
    motion_peak_ms = samples[pivot].time
    analyzed_speed = speed[preparation_start:follow_end + 1]
    wrists = wrists[preparation_start:follow_end + 1]
    samples = samples[preparation_start:follow_end + 1]
    elbow_angles = [_angle(sample.points[f"{side}_shoulder"], sample.points[f"{side}_elbow"], sample.points[f"{side}_wrist"]) for sample in samples]
    valid_elbows = [angle for angle in elbow_angles if angle is not None]
    filtered_elbows = [statistics.median(valid_elbows[max(0, i - 1):i + 2]) for i in range(len(valid_elbows))]
    shoulder_angles = [math.degrees(math.atan2(sample.points["right_shoulder"][1] - sample.points["left_shoulder"][1],
                                             sample.points["right_shoulder"][0] - sample.points["left_shoulder"][0]))
                       for sample in samples if _distance(sample.points["right_shoulder"], sample.points["left_shoulder"]) >= 0.3]
    unwrapped = [0.0]
    for a, b in zip(shoulder_angles, shoulder_angles[1:]):
        # A shoulder *line* is undirected: facing through side-on can reverse
        # endpoint order by 180 degrees without a 180-degree in-plane turn.
        unwrapped.append(unwrapped[-1] + (b - a + 90) % 180 - 90)
    metric_notes = []
    elbow_range = round(max(filtered_elbows) - min(filtered_elbows), 1) if len(valid_elbows) >= len(samples) * 0.8 else None
    shoulder_range = round(max(unwrapped) - min(unwrapped), 1) if len(shoulder_angles) >= len(samples) * 0.8 else None
    if elbow_range is None:
        metric_notes.append("肘部存在明显投影缩短或遮挡，未输出肘角变化。")
    if shoulder_range is None:
        metric_notes.append("肩线较多帧呈侧向缩短，未输出画面肩线变化。")
    candidate_samples = [sample for sample in samples if candidate["start_ms"] <= sample.time <= candidate["end_ms"]]
    return {
        "episode_id": candidate["candidate_id"], "family": candidate["family"], "person_track_id": candidate["person_track_id"],
        "start_ms": samples[0].time, "peak_ms": motion_peak_ms, "end_ms": samples[-1].time,
        "candidate_peak_ms": candidate["peak_ms"], "phase_timing_status": "estimated_from_2d_motion",
        "analysis_status": "complete" if preparation_complete and follow_complete else "partial",
        "classification": _classification(candidate_samples, side, hand_evidence, candidate["family"]),
        "method": "rule_based", "contact_confirmed": False, "racket_hand": side,
        "phases": phases,
        "metrics": {"duration_ms": samples[-1].time - samples[0].time,
                    "peak_wrist_speed_torso_per_s": round(max(analyzed_speed), 3),
                    "wrist_path_torso": round(sum(_distance(a, b) for a, b in zip(wrists, wrists[1:])), 3),
                    "elbow_extension_deg": elbow_range, "shoulder_line_change_deg": shoulder_range},
        "metric_notes_zh": metric_notes + ([] if preparation_complete and follow_complete else ["连续画面尚未覆盖完整准备或随挥，缺失阶段不作补全。"]),
        "evidence": {"pose_samples": len(samples), "racket_associated_frames": candidate["evidence"]["racket_associated_frames"],
                     "hand_evidence": dict(hand_evidence), "phase_method": "smoothed_wrist_speed_with_observed_slowdown",
                     "court_location_confirmed": False}, "limitations_zh": LIMITATIONS,
    }


def _incoming_ball_context(episode: Mapping[str, Any], serve: Mapping[str, Any], tracks: Mapping[int, list[Any]], balls: list[dict[str, Any]]) -> dict[str, Any] | None:
    receiver_track, server_track = episode["person_track_id"], serve["person_track_id"]
    if receiver_track == server_track or not 250 <= episode["peak_ms"] - serve["peak_ms"] <= 3000:
        return None
    receiver = min(tracks.get(receiver_track, []), key=lambda sample: abs(sample.time - episode["peak_ms"]), default=None)
    server = min(tracks.get(server_track, []), key=lambda sample: abs(sample.time - serve["peak_ms"]), default=None)
    if receiver is None or server is None:
        return None
    separation = _distance(server.center, receiver.center)
    if separation < max(server.scale, receiver.scale) * 4:
        return None
    by_track: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for ball in balls:
        if serve["peak_ms"] - 100 <= ball["time"] <= episode["peak_ms"] + 100:
            by_track[ball["track"]].append(ball)
    for observations in by_track.values():
        observations.sort(key=lambda item: item["time"])
        for end in range(2, len(observations)):
            window = observations[max(0, end - 29):end + 1]
            # Split when detections disappear or a track makes an impossible jump.
            last_gap = max((i + 1 for i, (a, b) in enumerate(zip(window, window[1:]))
                            if not 0 < b["time"] - a["time"] <= 180
                            or _distance(a["point"], b["point"]) * 1000 / (b["time"] - a["time"]) > separation * 8), default=0)
            window = window[last_gap:]
            if len(window) < 3:
                continue
            first, last = window[0], window[-1]
            if (not -100 <= first["time"] - serve["peak_ms"] <= 450
                    or _distance(first["point"], server.center) > server.scale * 2.5
                    or _distance(last["point"], receiver.center) > receiver.scale * 2.5
                    or last["time"] < episode["peak_ms"] - 350
                    or _distance(first["point"], last["point"]) < separation * 0.35):
                continue
            distances = [_distance(ball["point"], receiver.center) for ball in window]
            toward = sum(b <= a + receiver.scale * 0.15 for a, b in zip(distances, distances[1:])) / (len(distances) - 1)
            if toward >= 0.8 and distances[-1] < distances[0] * 0.55:
                return {"opponent_serve_id": serve["candidate_id"], "server_track_id": server_track,
                        "ball_track_id": first["track"], "ball_observations": len(window),
                        "incoming_start_ms": first["time"], "incoming_end_ms": last["time"]}
    return None


def build_motion_analysis(candidates: list[dict[str, Any]], primary_tracks: Mapping[int, list[Any]], *,
                          all_tracks: Mapping[int, list[Any]] | None = None,
                          context_serves: list[dict[str, Any]] | None = None,
                          ball_observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    families = {family: {"status": "insufficient_evidence", "reason_zh": "", "episodes": [], "summary": {}}
                for family in ("baseline", "serve", "return")}
    hands = {track: racket_hand_evidence(samples) for track, samples in primary_tracks.items()}
    for candidate in candidates:
        episode = _episode(candidate, primary_tracks.get(candidate["person_track_id"], []), hands.get(candidate["person_track_id"], {}))
        if episode is not None:
            episodes = families[episode["family"]]["episodes"]
            duplicate = next((i for i, other in enumerate(episodes)
                              if other["person_track_id"] == episode["person_track_id"]
                              and abs(other["peak_ms"] - episode["peak_ms"]) < 400
                              and min(other["end_ms"], episode["end_ms"]) - max(other["start_ms"], episode["start_ms"])
                              > min(other["end_ms"] - other["start_ms"], episode["end_ms"] - episode["start_ms"]) * 0.5), None)
            if duplicate is None:
                episodes.append(episode)
            else:
                # Re-centering a preparation and recovery candidate can reveal
                # the same real swing. Show and count that measured motion once.
                other = episodes[duplicate]
                selected = episode if other["classification"]["label"] == "unclassified" else other
                ids = set(other["evidence"].get("merged_candidate_ids", [other["episode_id"]]))
                ids.add(episode["episode_id"])
                selected["evidence"]["merged_candidate_ids"] = sorted(ids)
                episodes[duplicate] = selected
    for value in families.values():
        value["episodes"].sort(key=lambda episode: (episode["start_ms"], episode["person_track_id"]))
    for episode in families["baseline"]["episodes"]:
        for serve in context_serves or []:
            context = _incoming_ball_context(episode, serve, all_tracks or primary_tracks, ball_observations or [])
            if context:
                families["return"]["episodes"].append({**episode, "episode_id": episode["episode_id"] + "-return", "family": "return",
                    "classification": {"label": "return_motion", "label_zh": LABELS["return_motion"], "status": "rule_inferred",
                                       "reason_zh": "对手发球动作后，同一球轨迹向主球员移动并衔接本次挥拍；真实触球仍未确认。"},
                    "evidence": {**episode["evidence"], "return_context": context, "stroke_classification": episode["classification"]}})
                break
    for family, value in families.items():
        episodes = value["episodes"]
        value["summary"] = {"analyzed_count": len(episodes), "classifications": dict(Counter(e["classification"]["label"] for e in episodes))}
        if episodes:
            value["status"] = "analyzed"
            value["reason_zh"] = "已按时间窗测量动作阶段与二维运动指标；类型为规则推断，触球未确认。"
        elif family == "return":
            value["reason_zh"] = "缺少可连续关联的对手发球、来球轨迹与主球员挥拍证据，暂不能判断接发；不代表没有接发。"
        else:
            value["reason_zh"] = "当前视频未获得足够连续的" + ("发球时序" if family == "serve" else "持拍挥拍") + "证据，暂不能测量；不代表没有该动作。"
    return {"schema_version": "1.0.0", "analysis_version": ANALYSIS_VERSION,
            "method": "rule_based_pose_racket_temporal", "status": "available" if any(f["episodes"] for f in families.values()) else "insufficient_evidence",
            "contact_confirmed": False, "families": families, "limitations_zh": LIMITATIONS}
