"""Source-bound, body-relative 3D swing observations from saved SAM outputs.

No model is loaded. These are observed extrema in a selected motion window,
not contact events, verified anatomy, a kinetic-chain diagnosis or a score.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
import json
import hashlib
import math
from pathlib import Path
import statistics

from rallymate_vision.sam3d_evaluation import _binding_error, _time, COORDINATES_3D

VERSION = "sam3d-swing-kinematics-v1"
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_RECORDS = 100_000
TORSO = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
METRIC_UNITS = {"shoulder_hip_separation_deg": "deg", "hip_angular_speed_deg_s": "deg/s",
                "shoulder_angular_speed_deg_s": "deg/s", "wrist_speed_torso_s": "torso/s"}
LIMITATION = "单目模型的三维估计；显示该片段已观测最大值，峰值时刻不是触球时刻，不能据此判断真实受力或技术等级。"
CAMERA_LIMITATION = "已去除共同平移；相机旋转未校正，速度用于本段回放定位，不用于跨视频绝对速度排名。"


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _empty_metric(unit):
    return {"value": None, "unit": unit, "timestamp_ms": None, "source_frames": [],
            "window_start_ms": None, "window_end_ms": None, "status": "unavailable"}


def unavailable(reason="本次没有可绑定到动作片段的三维关节记录。"):
    return {"version": VERSION, "status": "unavailable", "measurement_semantics": "monocular_3d_model_estimate",
            "source_run_id": None, "source_video_sha256": None, "source_timestamp_semantics": "original_decoder_pts",
            "coordinate_system": "sam3d_pred_keypoints_3d", "coordinate_units": "model_units",
            "wrist_reference": "midhip_relative_fixed_median_torso_per_continuous_window",
            "camera_reference": "same_camera_epoch_uncalibrated_monocular_estimate",
            "camera_rotation_compensated": False, "cross_video_absolute_speed_comparable": False,
            "score": None, "reason_zh": reason, "episodes": [], "limitations_zh": [LIMITATION, CAMERA_LIMITATION]}


def _point(record, name):
    point = _mapping(record.get("keypoints_3d")).get(name)
    return tuple(point) if isinstance(point, (list, tuple)) and len(point) == 3 and all(_finite(v) for v in point) else None


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _mid(a, b):
    return tuple((x + y) / 2 for x, y in zip(a, b))


def _norm(a):
    return math.sqrt(sum(x * x for x in a))


def _angle(a, b):
    lengths = _norm(a) * _norm(b)
    if lengths <= 1e-12:
        return None
    cosine = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / lengths))
    return math.degrees(math.acos(cosine))


def _read_rows(path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("swing_input_too_large")
    with path.open(encoding="utf-8") as stream:
        for index, line in enumerate(stream):
            if index >= MAX_RECORDS:
                raise ValueError("swing_record_limit")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("swing_invalid_record")
            yield row


def _motion_episodes(summary):
    motion = _mapping(_mapping(summary.get("action_recognition")).get("motion_analysis"))
    families = motion.get("families", {})
    unique, conflicts = {}, set()
    for family in families.values() if isinstance(families, Mapping) else []:
        for row in family.get("episodes", []) if isinstance(family, Mapping) else []:
            if not isinstance(row, Mapping):
                continue
            start, end, identifier = row.get("start_ms"), row.get("end_ms"), row.get("episode_id")
            if not isinstance(identifier, str) or not identifier or not _finite(start) or not _finite(end) or not 0 <= start < end:
                continue
            continuity = _mapping(_mapping(row.get("evidence")).get("process_continuity"))
            reduced = {key: row.get(key) for key in ("episode_id", "start_ms", "end_ms", "person_track_id", "racket_hand")}
            reduced["continuity"] = continuity
            if identifier in unique and unique[identifier] != reduced:
                conflicts.add(identifier)
            unique[identifier] = reduced
    return [row for identifier, row in sorted(unique.items(), key=lambda item: (item[1]["start_ms"], item[0])) if identifier not in conflicts]


def _episode(episode, timeline, records, expected_sha, pts_origin_ms=0.):
    result = {key: episode[key] for key in ("episode_id", "start_ms", "end_ms", "person_track_id", "racket_hand")}
    result.update(frame_count=0, valid_frame_count=0, coverage=0.0, reason_zh="", quality_reason_counts={},
                  metrics={key: _empty_metric(unit) for key, unit in METRIC_UNITS.items()})
    continuity = _mapping(episode.get("continuity"))
    source_epoch, camera_epoch = continuity.get("source_selection_epoch"), continuity.get("camera_reference_epoch")
    if (not isinstance(continuity, Mapping) or continuity.get("pose_source") != "sam3d"
            or str(continuity.get("video_sha256", "")).lower() != expected_sha
            or type(source_epoch) is not int or source_epoch < 0 or type(camera_epoch) is not int or camera_epoch < 0
            or type(episode.get("person_track_id")) is not int):
        result["reason_zh"] = "动作片段缺少同视频、主体和相机参考绑定。"
        result["quality_reason_counts"] = {"episode_source_binding_unavailable": 1}
        return result
    selected = [row for row in timeline if _finite(row.get("timestamp_ms")) and episode["start_ms"] <= row["timestamp_ms"] <= episode["end_ms"]]
    # Motion episodes use the detector's raw track ID; the primary timeline has
    # a separate stable player ID. Binding these two ID domains by equality
    # rejected valid re-associated tracks (e.g. raw 89, canonical primary 1).
    primary_ids = {row.get("primary_player_id") for row in selected
                   if row.get("selection_status") == "selected" and row.get("source_track_id") == episode["person_track_id"]
                   and type(row.get("primary_player_id")) is int}
    primary_id = next(iter(primary_ids)) if len(primary_ids) == 1 else None
    result["primary_player_id"] = primary_id
    result["frame_count"] = len(selected)
    reasons = Counter()
    valid, previous = [], None
    segments, current = [], []
    frame_counts = Counter(row.get("source_frame_index") for row in selected)
    for row in selected:
        frame = row.get("source_frame_index")
        raw_track = row.get("source_track_id")
        candidates = records.get((frame, raw_track), [])
        record = candidates[0] if len(candidates) == 1 else None
        reason = None
        if (type(frame) is not int or frame_counts[frame] != 1 or row.get("selection_status") != "selected"
                or primary_id is None or row.get("primary_player_id") != primary_id
                or raw_track != episode["person_track_id"] or type(raw_track) is not int
                or row.get("identity_ambiguous") is not False):
            reason = "selected_subject_unverified"
        elif record is None:
            reason = "source_record_missing_or_duplicate"
        elif (_binding_error(record) is not None or record.get("status") != "ok"
              or str(record.get("video_sha256", "")).lower() != expected_sha
              or record.get("selection_epoch") != source_epoch or record.get("camera_reference_epoch") != camera_epoch
              or record.get("coordinate_system_3d") != COORDINATES_3D or record.get("units_3d") != "model_units"
              or record.get("relative_timestamp_ms", record.get("timestamp_ms")) != row.get("timestamp_ms")
              or abs(float(_time(record) * 1000) - pts_origin_ms - row.get("timestamp_ms", -1e9)) > 1.01):
            reason = "source_clock_identity_or_reference_mismatch"
        points = {name: _point(record, name) for name in TORSO} if not reason else {}
        if not reason and any(point is None for point in points.values()):
            reason = "torso_joint_missing"
        if not reason:
            hip, shoulder = _mid(points["left_hip"], points["right_hip"]), _mid(points["left_shoulder"], points["right_shoulder"])
            torso = math.dist(hip, shoulder)
            axes = {name: _sub(points[f"right_{name}"], points[f"left_{name}"]) for name in ("hip", "shoulder")}
            if torso <= 1e-9 or any(_norm(axis) <= 1e-9 for axis in axes.values()):
                reason = "degenerate_torso_geometry"
        if reason:
            reasons[reason] += 1
            if current:
                segments.append(current)
            current, previous = [], None
            continue
        source_time = float(_time(record) * 1000) - pts_origin_ms
        if not episode["start_ms"] <= source_time <= episode["end_ms"]:
            # Episode boundaries are integer milliseconds. An exact decoder PTS
            # just outside that window is not moved onto its rounded boundary.
            if current:
                segments.append(current)
            current, previous = [], None
            continue
        item = {"record": record, "time": source_time, "hip": hip,
                "torso": torso, "axes": axes, "wrist": _point(record, f"{episode['racket_hand']}_wrist") if episode["racket_hand"] in {"left", "right"} else None}
        connected = previous is not None and (record["frame_index"] == previous["record"]["frame_index"] + 1
            and record["person_track_id"] == previous["record"]["person_track_id"]
            and 0 < item["time"] - previous["time"] <= 160)
        if previous is not None and not connected:
            reasons["source_frame_or_time_gap"] += 1
            segments.append(current)
            current = []
        current.append(item)
        valid.append(item)
        previous = item
    if current:
        segments.append(current)
    result["valid_frame_count"] = len(valid)
    result["coverage"] = len(valid) / len(selected) if selected else 0.0
    observed = {key: [] for key in METRIC_UNITS}
    for segment in segments:
        # No one-frame peak or derivatives over missing frames. Every value has
        # its own actual support window, even when the episode is incomplete.
        if len(segment) < 5 or segment[-1]["time"] - segment[0]["time"] < 120:
            reasons["continuous_window_too_short"] += 1
            continue
        scale = statistics.median(item["torso"] for item in segment)
        start, end = segment[0]["time"], segment[-1]["time"]
        for item in segment:
            angle = _angle(item["axes"]["hip"], item["axes"]["shoulder"])
            observed["shoulder_hip_separation_deg"].append((angle, item["time"], [item["record"]["frame_index"]], start, end))
        for a, b in zip(segment, segment[1:]):
            dt = (b["time"] - a["time"]) / 1000
            frames = [a["record"]["frame_index"], b["record"]["frame_index"]]
            time = (a["time"] + b["time"]) / 2
            for axis in ("hip", "shoulder"):
                angle = _angle(a["axes"][axis], b["axes"][axis])
                observed[f"{axis}_angular_speed_deg_s"].append((angle / dt, time, frames, start, end))
            if a["wrist"] is not None and b["wrist"] is not None:
                relative_a, relative_b = _sub(a["wrist"], a["hip"]), _sub(b["wrist"], b["hip"])
                speed = math.dist(relative_a, relative_b) / scale / dt
                observed["wrist_speed_torso_s"].append((speed, time, frames, start, end))
            else:
                reasons["wrist_joint_missing_or_hand_unconfirmed"] += 1
    for key, values in observed.items():
        if values:
            value, time, frames, start, end = max(values, key=lambda item: item[0])
            result["metrics"][key].update(value=round(value, 4), timestamp_ms=round(time, 3), source_frames=frames,
                                          window_start_ms=round(start, 3), window_end_ms=round(end, 3), status="estimated")
    result["quality_reason_counts"] = dict(reasons)
    available = sum(metric["value"] is not None for metric in result["metrics"].values())
    result["reason_zh"] = ("部分时段或关节不可用，仅显示连续可测窗口。" if available and reasons else
                           "已按原视频时间绑定连续三维模型测量。" if available else "缺少足够连续且主体一致的三维测量。")
    return result


def analyze_swing_kinematics(episodes, timeline, records, *, video_sha256, source_run_id=None):
    """Pure measurement entrypoint; source metadata is validated before geometry."""
    result = unavailable()
    result.update(source_run_id=source_run_id, source_video_sha256=video_sha256.lower())
    indexed = {}
    for row in records:
        if isinstance(row, Mapping):
            indexed.setdefault((row.get("frame_index"), row.get("person_track_id")), []).append(row)
    origins = [float(_time(r) * 1000) - r["relative_timestamp_ms"] for rows in indexed.values() for r in rows
               if _binding_error(r) is None and _finite(r.get("relative_timestamp_ms"))
               and str(r.get("video_sha256", "")).lower() == video_sha256.lower()]
    origin = statistics.median(origins) if origins else 0.
    result["episodes"] = [_episode(row, timeline, indexed, video_sha256.lower(), origin) for row in episodes]
    count = sum(any(metric["value"] is not None for metric in row["metrics"].values()) for row in result["episodes"])
    if count:
        result.update(status="available" if count == len(episodes) and all(not row["quality_reason_counts"] for row in result["episodes"]) else "partial",
                      reason_zh=f"{count} 个动作片段有连续三维模型测量；不参与技术评分。")
    return result


def _signature(path):
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        raise ValueError("native_input_changed_during_hash")
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


@lru_cache(maxsize=2)
def _cached(directory_name, signature, episodes_json, sha):
    directory = Path(directory_name)
    timeline = list(_read_rows(directory / "primary-player.jsonl"))
    episodes = json.loads(episodes_json)
    selected_frames = {row.get("source_frame_index") for row in timeline if _finite(row.get("timestamp_ms"))
                       and any(episode["start_ms"] <= row["timestamp_ms"] <= episode["end_ms"] for episode in episodes)}
    records = [row for row in _read_rows(directory / "sam3d_frames.jsonl") if row.get("frame_index") in selected_frames]
    result = analyze_swing_kinematics(episodes, timeline, records, video_sha256=sha, source_run_id=directory.name)
    if signature != tuple(_signature(directory / name) for name in ("primary-player.jsonl", "sam3d_frames.jsonl")):
        raise ValueError("swing_inputs_changed_during_read")
    return result


def build_swing_kinematics(run_directory, summary):
    directory = Path(run_directory).resolve()
    try:
        if summary.get("job_id") != directory.name:
            return unavailable("任务与三维记录的来源不一致。")
        sha = _mapping(_mapping(summary.get("minimum_scoring_loop")).get("provenance")).get("video_sha256")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha):
            return unavailable()
        episodes = _motion_episodes(summary)
        if not episodes:
            return unavailable()
        signature = tuple(_signature(directory / name) for name in ("primary-player.jsonl", "sam3d_frames.jsonl"))
        return deepcopy(_cached(str(directory), signature, json.dumps(episodes, sort_keys=True), sha.lower()))
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return unavailable("三维关节记录缺失或未通过来源校验。")
