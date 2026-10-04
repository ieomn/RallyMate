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

from .rotation_analysis import analyze_rotation
from .temporal_recognition import (LABELS, RuleTemporalBackend, TemporalRecognitionBackend,
                                   _classification, build_temporal_window, samples_are_continuous,
                                   validate_recognition, wrist_kinematics)

ANALYSIS_VERSION = "stroke-motion-analysis-v2.0.0"
LIMITATIONS = [
    "阶段和类型由姿态、球拍及时间顺序规则推断，尚未经过专项标注集准确率验证。",
    "手腕速度以躯干长度/秒表示，轴角速度以度/秒表示；肩髋轴和肘角为画面二维测量，不是球速、三维转体角或技术评分。",
    "动作片段不等于确认击球；未确认球拍触球，底线场区位置也未校准。",
    "运动峰值是平滑后的二维手腕运动参考时刻，不是触球时刻；阶段边界仍需人工复核。",
]


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



def _episode(candidate: Mapping[str, Any], all_samples: list[Any], hand_evidence: Mapping[str, Any] | None = None, *,
             temporal_backend: TemporalRecognitionBackend | None = None) -> dict[str, Any] | None:
    side = candidate["racket_hand_candidate"]
    # Context expands preparation/follow-through, but never crosses a missing
    # wrist, identity break, implausible pose jump, or >160 ms observation gap.
    samples = [sample for sample in all_samples if candidate["start_ms"] - 650 <= sample.time <= candidate["end_ms"] + 900]
    segments: list[list[Any]] = [[]]
    for sample in samples:
        valid = (sample.track == candidate["person_track_id"]
                 and f"{side}_wrist" in sample.points)
        prev = segments[-1][-1] if segments[-1] else None
        continuous = prev is None or samples_are_continuous(prev, sample)
        if not valid or not continuous:
            segments.append([])
        if valid:
            segments[-1].append(sample)
    samples = next((segment for segment in segments if segment and segment[0].time <= candidate["start_ms"]
                    and segment[-1].time >= candidate["end_ms"]), [])
    if len(samples) < 7:
        return None
    # Hand ownership is scoped to this continuous local context. A distant
    # idle section, identity reuse or the other end of the video cannot vote.
    hand_evidence = {**racket_hand_evidence(samples), "scope": "continuous_episode_context",
                     "start_ms": samples[0].time, "end_ms": samples[-1].time}
    window = build_temporal_window(samples)
    backend = temporal_backend or RuleTemporalBackend()
    requested_backend = dict(backend.metadata())
    fallback_reason = None
    try:
        prediction = backend.recognize(window, candidate, hand_evidence)
        if prediction is not None:
            validate_recognition(prediction, window)
    except (ValueError, TypeError, KeyError, RuntimeError, OSError):
        if type(backend) is RuleTemporalBackend:
            raise
        prediction = None
        fallback_reason = "backend_failed_or_invalid_output"
    if prediction is None and type(backend) is not RuleTemporalBackend:
        # A unavailable model adapter must not erase independently observable
        # motion. Report the actual fallback source, never pretend the model ran.
        fallback_reason = fallback_reason or "backend_unavailable"
        backend = RuleTemporalBackend()
        prediction = backend.recognize(window, candidate, hand_evidence)
    if prediction is None:
        return None
    validate_recognition(prediction, window)
    wrists, speed = wrist_kinematics(samples, side)
    preparation_start = next(i for i, sample in enumerate(samples) if sample.time == prediction["start_ms"])
    follow_end = next(i for i, sample in enumerate(samples) if sample.time == prediction["end_ms"])
    phases = prediction["phases"]
    preparation_complete = phases[0]["status"] == "measured"
    follow_complete = phases[-1]["status"] == "measured"
    motion_peak_ms = prediction["peak_ms"]
    analyzed_speed = speed[preparation_start:follow_end + 1]
    wrists = wrists[preparation_start:follow_end + 1]
    samples = samples[preparation_start:follow_end + 1]
    elbow_angles = [_angle(sample.points[f"{side}_shoulder"], sample.points[f"{side}_elbow"], sample.points[f"{side}_wrist"])
                    if all(f"{side}_{joint}" in sample.points for joint in ("shoulder", "elbow", "wrist")) else None
                    for sample in samples]
    elbow_segments: list[list[float]] = [[]]
    for angle in elbow_angles:
        if angle is None:
            if elbow_segments[-1]:
                elbow_segments.append([])
        else:
            elbow_segments[-1].append(angle)
    valid_elbows = max(elbow_segments, key=len)
    filtered_elbows = [statistics.median(valid_elbows[max(0, i - 1):i + 2]) for i in range(len(valid_elbows))]
    rotation_analysis = analyze_rotation(samples, phases=phases)
    metric_notes = []
    elbow_range = round(max(filtered_elbows) - min(filtered_elbows), 1) if len(valid_elbows) >= len(samples) * 0.8 else None
    if elbow_range is None:
        metric_notes.append("肘部存在明显投影缩短或遮挡，未输出肘角变化。")
    if rotation_analysis["status"] != "measured_2d":
        metric_notes.append("部分肩髋轴缺少足够连续的二维观测；投影缩短、缺测和轴跳变不跨段补全。")
    return {
        "episode_id": candidate["candidate_id"], "family": candidate["family"], "person_track_id": candidate["person_track_id"],
        "start_ms": samples[0].time, "peak_ms": motion_peak_ms, "end_ms": samples[-1].time,
        "candidate_peak_ms": candidate["peak_ms"], "phase_timing_status": prediction["phase_timing_status"],
        "analysis_status": "complete" if preparation_complete and follow_complete else "partial",
        "classification": prediction["classification"],
        "temporal_recognition": {"schema_version": "1.0.0", "backend": dict(backend.metadata()),
                                 "requested_backend": requested_backend, "fallback_reason": fallback_reason,
                                 "input_summary": window.summary(), "anchor_is_contact": False,
                                 "classification_status": prediction["classification"]["status"],
                                 "phase_timing_status": prediction["phase_timing_status"]},
        "method": "rule_based" if backend.metadata().get("kind") == "deterministic_rules" else "temporal_backend",
        "contact_confirmed": False, "racket_hand": side,
        "phases": phases,
        "metrics": {"duration_ms": samples[-1].time - samples[0].time,
                    "peak_wrist_speed_torso_per_s": round(max(analyzed_speed), 3),
                    "wrist_path_torso": round(sum(_distance(a, b) for a, b in zip(wrists, wrists[1:])), 3),
                    "elbow_extension_deg": elbow_range, **rotation_analysis["metrics"]},
        "rotation_analysis": rotation_analysis,
        "metric_notes_zh": metric_notes + ([] if preparation_complete and follow_complete else ["连续画面尚未覆盖完整准备或随挥，缺失阶段不作补全。"]),
        "evidence": {"pose_samples": len(samples), "racket_associated_frames": candidate["evidence"]["racket_associated_frames"],
                     "hand_evidence": dict(hand_evidence), "phase_method": prediction["phase_method"],
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
    camera_epoch = getattr(receiver, "camera_reference_epoch", None)
    if camera_epoch != getattr(server, "camera_reference_epoch", None):
        return None
    separation = _distance(server.center, receiver.center)
    if separation < max(server.scale, receiver.scale) * 4:
        return None
    by_track: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for ball in balls:
        if (ball.get("camera_reference_epoch") == camera_epoch
                and serve["peak_ms"] - 100 <= ball["time"] <= episode["peak_ms"] + 100):
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
                          ball_observations: list[dict[str, Any]] | None = None,
                          temporal_backend: TemporalRecognitionBackend | None = None) -> dict[str, Any]:
    families = {family: {"status": "insufficient_evidence", "reason_zh": "", "episodes": [], "summary": {}}
                for family in ("baseline", "serve", "return")}
    backend = temporal_backend or RuleTemporalBackend()
    for candidate in candidates:
        episode = _episode(candidate, primary_tracks.get(candidate["person_track_id"], []), temporal_backend=backend)
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
    return {"schema_version": "1.1.0", "analysis_version": ANALYSIS_VERSION, "temporal_backend": dict(backend.metadata()),
            "method": "rule_based_pose_racket_temporal", "status": "available" if any(f["episodes"] for f in families.values()) else "insufficient_evidence",
            "contact_confirmed": False, "families": families, "limitations_zh": LIMITATIONS}
