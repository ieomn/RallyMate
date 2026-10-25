"""Pure reuse of the accepted native SAM primary epoch join.

Only private timeline copies receive proven missing epochs. No source files,
geometry, decoder clocks or primary selection decisions are changed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re


PRIMARY_TIMELINE_ADAPTER_VERSION = "native-sam-primary-epochs-v1.0.0"


class NativePrimaryTimelineError(ValueError):
    """A fixed native input conflict reason; never includes source payloads."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise NativePrimaryTimelineError(reason)


def _digest(value) -> str:
    _require(isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None, "invalid_expected_digest")
    return value.lower()


def adapt_native_primary_timeline(records: list, timeline: list, *, video_sha256: str) -> tuple[list, dict]:
    """Join recorded SAM epochs into a private copy of the native v0.3 timeline.

    Native primary-player exports omit the request and camera epochs. Neither
    epoch can be inferred from a track number, row position, timestamp or zero.
    Accept only the original selected track's unique request AND pose binding,
    cross-checked against this exact decoder frame and camera record. Missing
    evidence stays unavailable; conflicting evidence fails publication closed.
    No decoder values, geometry, source files or selection decisions are changed.
    """
    from rallymate_features.coordinates import _source_time_binding

    expected_video = _digest(video_sha256)
    _require(isinstance(records, list) and isinstance(timeline, list)
             and all(isinstance(row, dict) for row in timeline), "invalid_native_primary_inputs")
    by_processed = defaultdict(list)
    frame_counts = Counter()
    for record in records:
        _require(isinstance(record, dict) and isinstance(record.get("frame"), dict), "invalid_native_frame")
        frame = record["frame"]
        if type(frame.get("processed_index")) is int and type(frame.get("index")) is int:
            by_processed[frame["processed_index"]].append(record)
            frame_counts[frame["index"]] += 1
    timeline_counts = Counter(row.get("processed_index") for row in timeline)
    result, reasons = [], Counter()
    report = {"adapter_version": PRIMARY_TIMELINE_ADAPTER_VERSION, "input_rows": len(timeline),
              "adapted_rows": 0, "added_selection_epochs": 0, "added_camera_reference_epochs": 0,
              "evidence": "unique_same_frame_selected_track_SAM_request_and_pose_binding_and_camera",
              "source_files_changed": False}
    for original in timeline:
        selected = dict(original)
        result.append(selected)
        if (selected.get("schema_version") != "1.0.0"
                or selected.get("selection_algorithm_version") != "primary-player-v0.3.0"):
            reasons["different_input_schema_unchanged"] += 1
            continue
        if (selected.get("selection_status") != "selected" or selected.get("identity_ambiguous") is not False
                or type(selected.get("primary_player_id")) is not int or selected["primary_player_id"] != 1
                or type(selected.get("source_track_id")) is not int or selected["source_track_id"] < 0):
            reasons["selected_identity_unavailable"] += 1
            continue
        processed = selected.get("processed_index")
        _require(type(processed) is int and timeline_counts[processed] == 1
                 and len(by_processed.get(processed, [])) == 1, "native_primary_frame_join_ambiguous")
        record = by_processed[processed][0]
        frame, track = record["frame"], selected["source_track_id"]
        _require(frame_counts[frame["index"]] == 1, "native_primary_source_frame_duplicated")
        poses, requests = record.get("poses"), record.get("sam3d_requests")
        if not isinstance(poses, list) or not isinstance(requests, list):
            reasons["request_or_pose_evidence_missing"] += 1
            continue
        poses = [p for p in poses if isinstance(p, dict) and p.get("person_track_id") == track]
        requests = [r for r in requests if isinstance(r, dict) and r.get("person_track_id") == track]
        if not poses or not requests:
            reasons["selected_track_request_or_pose_missing"] += 1
            continue
        _require(len(poses) == len(requests) == 1, "native_primary_request_or_pose_ambiguous")
        pose, request = poses[0], requests[0]
        if pose.get("source") != "sam3d" or pose.get("status") != "ok" or request.get("status") != "ok":
            reasons["selected_SAM_evidence_unavailable"] += 1
            continue
        binding, camera = pose.get("source_binding"), record.get("camera_motion")
        required = {"frame_index", "processed_index", "video_sha256", "pts", "time_base_num", "time_base_den",
                    "timestamp_ms", "relative_timestamp_ms", "timestamp_source", "person_track_id",
                    "selection_epoch", "camera_reference_epoch"}
        if (not isinstance(binding, dict) or not required.issubset(binding)
                or "selection_epoch" not in request or not isinstance(camera, dict) or "reference_epoch" not in camera):
            reasons["epoch_or_source_binding_evidence_missing"] += 1
            continue
        _require(type(pose.get("person_track_id")) is int and type(request.get("person_track_id")) is int,
                 "native_primary_track_type_conflict")
        for key, frame_key, minimum in (("frame_index", "index", 0), ("processed_index", "processed_index", 0),
                                         ("pts", "pts", 0), ("time_base_num", "time_base_num", 1),
                                         ("time_base_den", "time_base_den", 1)):
            _require(type(binding[key]) is int and type(frame.get(frame_key)) is int
                     and binding[key] >= minimum and binding[key] == frame[frame_key], "native_primary_decoder_binding_conflict")
        _require(type(binding["person_track_id"]) is int and binding["person_track_id"] == track,
                 "native_primary_pose_track_conflict")
        _require(_digest(binding["video_sha256"]) == _digest(frame.get("video_sha256")) == expected_video,
                 "native_primary_video_identity_conflict")
        _require(binding["timestamp_source"] == frame.get("timestamp_source") == "decoder_pts"
                 and not isinstance(binding["timestamp_ms"], bool)
                 and binding["timestamp_ms"] == frame.get("source_timestamp_ms")
                 and type(binding["relative_timestamp_ms"]) is int
                 and binding["relative_timestamp_ms"] == frame.get("timestamp_ms"), "native_primary_clock_conflict")
        epoch, camera_epoch = binding["selection_epoch"], binding["camera_reference_epoch"]
        _require(type(epoch) is int and epoch >= 0 and type(request["selection_epoch"]) is int
                 and request["selection_epoch"] == epoch, "native_primary_selection_epoch_conflict")
        _require(type(camera_epoch) is int and camera_epoch >= 0 and type(camera["reference_epoch"]) is int
                 and camera["reference_epoch"] == camera_epoch, "native_primary_camera_epoch_conflict")
        additions = 0
        for key, value, counter in (("selection_epoch", epoch, "added_selection_epochs"),
                                     ("camera_reference_epoch", camera_epoch, "added_camera_reference_epochs")):
            if key in selected:
                _require(type(selected[key]) is int and selected[key] == value, "native_primary_existing_epoch_conflict")
            else:
                selected[key] = value
                report[counter] += 1
                additions += 1
        verified, reason = _source_time_binding(record, selected, unique_source=True)
        _require(verified is not None, "native_primary_binding_" + str(reason))
        # Newer request exports can repeat decoder identity fields. They must
        # agree too; absence does not manufacture those fields in an old ledger.
        for key, expected in binding.items():
            if key in request and key in required:
                actual = request[key]
                if key == "video_sha256":
                    _require(_digest(actual) == _digest(expected), "native_primary_request_binding_conflict")
                else:
                    _require(not isinstance(actual, bool) and actual == expected
                             and (type(expected) is not int or type(actual) is int), "native_primary_request_binding_conflict")
        report["adapted_rows"] += bool(additions)
    report["not_adapted_reasons"] = dict(sorted(reasons.items()))
    return result, report
