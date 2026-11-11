"""Training clips, match records and movement summaries with explicit sources.

Pose episodes establish movement, not tennis point outcomes or mental state.
Point statistics consume confirmed, video-bound records. Unobserved outcomes
remain unknown. Skeleton templates must be supplied with measured body lengths.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
import math
from numbers import Real
import re
import statistics

VERSION = "training-session-analysis-v1.0.0"


def _map(value):
    return value if isinstance(value, Mapping) else {}


def _finite(value):
    # Native kinematics retains NumPy scalar times until JSON serialization.
    # Accept real scalar implementations while still excluding boolean values.
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None


def _ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "percent": round(100 * numerator / denominator, 4) if denominator else None}


def _frames(episode):
    metrics = _map(_map(episode.get("joint_kinematics")).get("metrics"))
    return sorted({frame for metric in metrics.values() for frame in (_map(metric).get("source_frames") or [])
                   if type(frame) is int and frame >= 0})


def _clips(assessment):
    sha = assessment.get("source_video_sha256")
    selected = _map(assessment.get("subject_scope")).get("selected_subject_id")
    duration = assessment.get("source_duration_ms")
    duration = float(duration) if _finite(duration) and duration > 0 else None
    candidates, rejected = [], Counter()
    for raw in assessment.get("episodes", []):
        e = _map(raw)
        start, end, identifier = e.get("start_ms"), e.get("end_ms"), e.get("episode_id")
        if not _sha(sha):
            rejected["source_video_identity_missing"] += 1
            continue
        if not isinstance(identifier, str) or not identifier or not _finite(start) or not _finite(end) or not 0 <= start < end:
            rejected["episode_window_invalid"] += 1
            continue
        if duration is not None and end > duration:
            rejected["episode_outside_source_duration"] += 1
            continue
        if not selected or e.get("subject_id") != selected:
            rejected["episode_subject_mismatch"] += 1
            continue
        frames = _frames(e)
        if not frames:
            rejected["source_frame_binding_missing"] += 1
            continue
        reference = (e.get("subject_id"), e.get("source_track_id"), e.get("source_selection_epoch"), e.get("camera_reference_epoch"))
        if any(value is None for value in reference):
            rejected["source_reference_incomplete"] += 1
            continue
        candidates.append({"start_ms": start, "end_ms": end, "episode_ids": [identifier], "source_frames": frames,
                           "source_video_sha256": sha, "subject_id": reference[0], "source_track_id": reference[1],
                           "source_selection_epoch": reference[2], "camera_reference_epoch": reference[3], "reference": reference})
    merged = []
    for row in sorted(candidates, key=lambda c: (c["start_ms"], c["end_ms"], c["episode_ids"][0])):
        prior = next((clip for clip in reversed(merged) if clip["reference"] == row["reference"] and clip["end_ms"] >= row["start_ms"]), None)
        if prior:
            prior["end_ms"] = max(prior["end_ms"], row["end_ms"])
            prior["episode_ids"] = sorted(set(prior["episode_ids"] + row["episode_ids"]))
            prior["source_frames"] = sorted(set(prior["source_frames"] + row["source_frames"]))
        else:
            merged.append(row)
    for clip in merged:
        clip.pop("reference")
    intervals = []
    for clip in sorted(merged, key=lambda c: c["start_ms"]):
        if intervals and clip["start_ms"] <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], clip["end_ms"])
        else:
            intervals.append([clip["start_ms"], clip["end_ms"]])
    rest = []
    if duration is not None and intervals:
        cursor = 0.
        for start, end in intervals:
            if start > cursor:
                rest.append({"start_ms": cursor, "end_ms": start})
            cursor = end
        if cursor < duration:
            rest.append({"start_ms": cursor, "end_ms": duration})
    return {"status": "ready" if merged else "no_source_bound_action_clips", "active_clips": merged,
            "active_duration_ms": sum(end - start for start, end in intervals), "source_duration_ms": duration,
            "unselected_intervals": rest,
            "interval_semantics": "outside_accepted_motion_windows_not_verified_drinking_or_ball_pickup",
            "rest_intervals_status": "computed_from_known_source_duration" if duration is not None and intervals else "source_duration_or_action_coverage_missing",
            "rejected_episode_reasons": dict(rejected)}


def match_statistics(point_events, *, source_video_sha256, subject_id):
    """Confirmed point schema: point_id, SHA, subject_id, server_id, winner_id.

    Optional serve_number is 1 or 2. terminal_shot is ace/return_ace/
    unforced_error/other. A game is counted only when game_complete is true and
    game_id, game_server_id and game_winner_id are explicitly supplied.
    """
    rejected, by_id = Counter(), defaultdict(list)
    for raw in point_events:
        row = _map(raw)
        if row.get("confirmed") is not True:
            rejected["point_not_confirmed"] += 1
        elif not _sha(source_video_sha256) or row.get("source_video_sha256") != source_video_sha256:
            rejected["point_source_mismatch"] += 1
        elif not subject_id or row.get("subject_id") != subject_id:
            rejected["point_subject_mismatch"] += 1
        elif not isinstance(row.get("point_id"), str) or not row["point_id"]:
            rejected["point_id_missing"] += 1
        else:
            by_id[row["point_id"]].append(dict(row))
    points = []
    for rows in by_id.values():
        if any(row != rows[0] for row in rows[1:]):
            rejected["conflicting_point_records"] += len(rows)
        else:
            row = rows[0]
            server, winner, terminal = row.get("server_id"), row.get("winner_id"), row.get("terminal_shot")
            known = isinstance(server, str) and bool(server) and isinstance(winner, str) and bool(winner)
            if (known and terminal == "ace" and winner != server
                    or known and terminal == "return_ace" and winner == server
                    or terminal == "unforced_error" and isinstance(winner, str) and winner and row.get("error_player_id") == winner):
                rejected["terminal_outcome_conflict"] += 1
            else:
                points.append(row)
            rejected["duplicate_point_records"] += len(rows) - 1
    first, second = [], []
    aces, return_aces, unforced = [], [], []
    games = defaultdict(set)
    unknown_outcomes = 0
    for row in points:
        server, winner = row.get("server_id"), row.get("winner_id")
        known = isinstance(server, str) and bool(server) and isinstance(winner, str) and bool(winner)
        if not known:
            unknown_outcomes += 1
        if known and server == subject_id and type(row.get("serve_number")) is int:
            if row["serve_number"] == 1:
                first.append(winner == subject_id)
            elif row["serve_number"] == 2:
                second.append(winner == subject_id)
        terminal = row.get("terminal_shot")
        if terminal in {"ace", "return_ace", "unforced_error", "other"}:
            if known and server == subject_id:
                aces.append(terminal == "ace" and winner == subject_id)
            if known and server != subject_id:
                return_aces.append(terminal == "return_ace" and winner == subject_id)
            if terminal != "unforced_error" or isinstance(row.get("error_player_id"), str):
                unforced.append(terminal == "unforced_error" and row.get("error_player_id") == subject_id)
        if row.get("game_complete") is True:
            gid, gs, gw = (row.get(key) for key in ("game_id", "game_server_id", "game_winner_id"))
            if all(isinstance(v, str) and v for v in (gid, gs, gw)):
                games[gid].add((gs, gw))
    service_games, receiving_games = [], []
    for values in games.values():
        if len(values) != 1:
            rejected["conflicting_game_records"] += 1
            continue
        server, winner = next(iter(values))
        (service_games if server == subject_id else receiving_games).append(winner == subject_id)
    return {"status": "confirmed_records" if points else "no_confirmed_point_records", "confirmed_point_count": len(points),
            "unknown_outcome_count": unknown_outcomes, "rejected_records": {k: v for k, v in rejected.items() if v},
            "first_serve_points_won": _ratio(sum(first), len(first)), "second_serve_points_won": _ratio(sum(second), len(second)),
            "aces": {"count": sum(aces) if aces else None, "classified_service_points": len(aces)},
            "return_aces": {"count": sum(return_aces) if return_aces else None, "classified_return_points": len(return_aces),
                            **_ratio(sum(return_aces), len(return_aces))},
            "unforced_errors": {"count": sum(unforced) if unforced else None, "classified_points": len(unforced)},
            "hold_rate": _ratio(sum(service_games), len(service_games)), "break_rate": _ratio(sum(receiving_games), len(receiving_games)),
            "semantics": "rates_over_confirmed_outcomes_only_unknowns_are_not_losses"}


def movement_ranges(episodes):
    collected = defaultdict(list)
    for raw in episodes:
        e = _map(raw)
        for name, metric in _map(_map(e.get("joint_kinematics")).get("metrics")).items():
            m = _map(metric)
            if m.get("status") != "measured" or m.get("unit") != "deg" or not all(_finite(m.get(k)) for k in ("p10", "p90", "range")):
                continue
            if m["p90"] < m["p10"] or m["range"] < 0 or type(m.get("observation_count")) is not int or m["observation_count"] <= 0:
                continue
            collected[name].append({"episode_id": e.get("episode_id"), "start_ms": e.get("start_ms"), "end_ms": e.get("end_ms"),
                                    **{k: m.get(k) for k in ("min", "max", "p10", "p90", "range", "observation_count", "source_frames")}})
    output = {}
    for name, values in collected.items():
        extrema = [v for v in values if _finite(v["min"]) and _finite(v["max"]) and v["min"] <= v["max"]]
        output[name] = {"unit": "deg", "episode_count": len(values), "episodes": values,
                        "observed_min": min(v["min"] for v in extrema) if extrema else None,
                        "observed_max": max(v["max"] for v in extrema) if extrema else None,
                        "median_episode_p10": statistics.median(v["p10"] for v in values),
                        "median_episode_p90": statistics.median(v["p90"] for v in values),
                        "median_episode_range": statistics.median(v["range"] for v in values)}
    return {"status": "measured" if output else "no_joint_angle_measurements", "metrics": output,
            "semantics": "observed_exercise_ranges_not_clinical_range_of_motion_or_diagnosis",
            "quantile_semantics": "median_of_per_episode_quantiles_not_pooled_frame_quantiles"}


def scale_ideal_skeleton(template, measured_lengths):
    """Retarget a supplied tree skeleton using actual lengths in the same unit.

    template: template_id, root, length_unit, points {name:[x,y,z]},
    bones [{start,end,length_id}]. measured_lengths: {length_id:{value,unit}}.
    Directions come from the template; no supposedly ideal motion is invented.
    """
    t = _map(template)
    points, root, unit = _map(t.get("points")), t.get("root"), t.get("length_unit")
    if not t.get("template_id") or root not in points or not isinstance(unit, str) or not unit:
        raise ValueError("a supplied template with root, ID and length unit is required")
    if any(not isinstance(p, (list, tuple)) or len(p) != 3 or not all(_finite(v) for v in p) for p in points.values()):
        raise ValueError("template points must be finite 3D coordinates")
    bones = t.get("bones", [])
    if not isinstance(bones, list) or len(bones) != len(points) - 1:
        raise ValueError("template must define one connected bone tree")
    children = set()
    for raw in bones:
        bone = _map(raw)
        a, b, length_id = (bone.get(k) for k in ("start", "end", "length_id"))
        length = _map(_map(measured_lengths).get(length_id))
        if a not in points or b not in points or a == b or b in children or b == root:
            raise ValueError("invalid or repeated template bone")
        if length.get("unit") != unit or not _finite(length.get("value")) or length["value"] <= 0:
            raise ValueError(f"measured length with matching unit required: {length_id}")
        children.add(b)
    output, pending = {root: list(points[root])}, list(bones)
    while pending:
        ready = [bone for bone in pending if bone["start"] in output]
        if not ready:
            raise ValueError("template contains a cycle or disconnected bones")
        for bone in ready:
            a, b = bone["start"], bone["end"]
            vector = [y - x for x, y in zip(points[a], points[b])]
            norm = math.sqrt(sum(v*v for v in vector))
            if norm <= 1e-12:
                raise ValueError("template bone has zero length")
            factor = measured_lengths[bone["length_id"]]["value"] / norm
            output[b] = [origin + delta * factor for origin, delta in zip(output[a], vector)]
            pending.remove(bone)
    return {"template_id": t["template_id"], "root": root, "length_unit": unit, "points": output,
            "semantics": "supplied_template_retargeted_to_measured_segment_lengths", "bones": bones}


def compare_training_match(comparison):
    row = _map(comparison)
    train, match = _map(row.get("training")), _map(row.get("match"))
    if row.get("same_subject_confirmed") is not True or not row.get("subject_id") or any(
            source.get("subject_id") != row["subject_id"] or not _sha(source.get("source_video_sha256")) for source in (train, match)):
        return {"status": "same_subject_source_evidence_required", "metrics": [], "psychological_score": None}
    rating = row.get("rating")
    if not _finite(rating) or not 0 <= rating <= 5:
        return {"status": "rating_context_required", "metrics": [], "psychological_score": None}
    allowed = {"rhythm_ms", "ball_speed_m_s", "follow_through_deg", "split_step_fraction", "adjustment_steps_per_s", "unforced_error_fraction"}
    units = {"rhythm_ms": "ms", "ball_speed_m_s": "m/s", "follow_through_deg": "deg", "split_step_fraction": "ratio", "adjustment_steps_per_s": "1/s", "unforced_error_fraction": "ratio"}
    deltas = []
    for name in sorted(allowed):
        left, right = _map(_map(train.get("metrics")).get(name)), _map(_map(match.get("metrics")).get(name))
        if all(_finite(source.get("value")) and source.get("unit") == units[name] and type(source.get("sample_count")) is int and source["sample_count"] > 0 for source in (left, right)):
            a, b = left["value"], right["value"]
            if min(a, b) < 0 or units[name] == "ratio" and max(a, b) > 1:
                continue
            deltas.append({"metric": name, "unit": units[name], "training": a, "match": b, "delta": b-a,
                           "relative_change_percent": 100*(b-a)/a if a else None,
                           "training_samples": left["sample_count"], "match_samples": right["sample_count"]})
    return {"status": "compared" if deltas else "no_comparable_measurements", "mode": "basic_habits" if rating < 3 else "training_match_behavior_comparison",
            "metrics": deltas, "source_video_sha256": {"training": train["source_video_sha256"], "match": match["source_video_sha256"]},
            "psychological_score": None, "semantics": "observed_behavior_changes_not_mental_state_or_diagnosis",
            "habit_reminders_zh": ["完成随挥后恢复准备姿势", "保持发球前的固定准备节奏"] if rating < 3 else []}


def build_training_session_analysis(assessment, *, point_events=(), comparison=None):
    assessment = _map(assessment)
    clips = _clips(assessment)
    accepted = {eid for clip in clips["active_clips"] for eid in clip["episode_ids"]}
    subject = _map(assessment.get("subject_scope")).get("selected_subject_id")
    episodes, seen = [], set()
    for row in assessment.get("episodes", []):
        e = _map(row)
        key = (e.get("episode_id"), e.get("start_ms"), e.get("end_ms"), e.get("source_track_id"), e.get("source_selection_epoch"), e.get("camera_reference_epoch"))
        if e.get("episode_id") not in accepted or e.get("subject_id") != subject or key in seen:
            continue
        if any(all(e.get(k) == clip.get(k) for k in ("subject_id", "source_track_id", "source_selection_epoch", "camera_reference_epoch"))
               and _finite(e.get("start_ms")) and _finite(e.get("end_ms")) and clip["start_ms"] <= e["start_ms"] < e["end_ms"] <= clip["end_ms"] for clip in clips["active_clips"]):
            episodes.append(row)
            seen.add(key)
    return {"version": VERSION, "source_run_id": assessment.get("source_run_id"), "source_video_sha256": assessment.get("source_video_sha256"),
            "subject_id": subject, "training": clips,
            "match": match_statistics(point_events, source_video_sha256=assessment.get("source_video_sha256"), subject_id=subject),
            "movement_ranges": movement_ranges(episodes),
            "training_match_comparison": compare_training_match(comparison) if comparison is not None else {"status": "comparison_not_supplied", "metrics": [], "psychological_score": None},
            "ideal_template": {"status": "template_and_measured_lengths_required"}}
