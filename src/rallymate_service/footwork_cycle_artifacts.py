"""Verify original artifacts before executing manual FS03/FS04 candidates.

Annotations supply source-frame boundaries, never quality flags, trajectories,
conditions or grades. Decoder/selection epochs are explicitly derived from the
verified source snapshot; they do not establish human identity ground truth.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

from rallymate_features.coordinates import _bound_model_points, pose_sequence_from_records
from rallymate_features.source_alignment import clear_source_aligned_feature_cache
from rallymate_scoring.card_kinematics import finite
from rallymate_service.footwork_cycle_execution import compute_footwork_cycle_execution
from rallymate_service.gs_card_execution import (
    _canonical, _decode_video_timeline, _digest, _integer, _keys, _prepare_samples,
    _read_json, _records, _sha, _text,
)

VERSION = "footwork-cycle-artifacts-v1.0.0"
ANNOTATION_VERSION = "footwork-cycle-annotations-v1.0.0"
SOURCE_FILES = ("frames.jsonl", "primary-player.jsonl", "events.jsonl", "summary.json")
SEMANTICS = "manual_visual_candidate_window_not_expert_technique_or_contact_truth"
MAX_WINDOWS = 2000


def validate_cycle_annotations(document):
    _keys(document, ("version", "source_run_id", "video_sha256", "input_artifact_sha256", "windows"))
    if document["version"] != ANNOTATION_VERSION or not _text(document["source_run_id"]):
        raise ValueError("cycle_annotation_version_or_run_invalid")
    _digest(document["video_sha256"])
    _keys(document["input_artifact_sha256"], SOURCE_FILES)
    for value in document["input_artifact_sha256"].values():
        _digest(value)
    if not isinstance(document["windows"], list) or len(document["windows"]) > MAX_WINDOWS:
        raise ValueError("cycle_annotation_inventory_invalid")
    seen, accepted = set(), []
    for window in document["windows"]:
        _keys(window, ("event_id", "event_code", "primary_player_id", "start_source_frame", "end_source_frame",
                       "start_ms", "end_ms", "binding"))
        identifier = window["event_id"]
        if not _text(identifier) or identifier in seen:
            raise ValueError("cycle_duplicate_or_invalid_annotation_id")
        seen.add(identifier)
        if (window["event_code"] not in ("FS03", "FS04") or not _integer(window["primary_player_id"])
                or not _integer(window["start_source_frame"]) or not _integer(window["end_source_frame"])
                or window["start_source_frame"] >= window["end_source_frame"]
                or not finite(window["start_ms"]) or not finite(window["end_ms"])
                or not 0 <= window["start_ms"] < window["end_ms"]):
            raise ValueError("cycle_annotation_identity_or_window_invalid")
        binding = window["binding"]
        _keys(binding, ("method", "version", "source", "uncertainty_ms", "semantics"))
        if (binding["method"] != "manual_anchor" or binding["semantics"] != SEMANTICS
                or not _text(binding["version"]) or not _text(binding["source"])
                or not finite(binding["uncertainty_ms"]) or binding["uncertainty_ms"] < 0):
            raise ValueError("cycle_manual_candidate_binding_invalid")
        for previous in accepted:
            if (window["primary_player_id"] == previous["primary_player_id"]
                    and max(window["start_source_frame"], previous["start_source_frame"])
                    < min(window["end_source_frame"], previous["end_source_frame"])):
                raise ValueError("cycle_overlapping_candidate_windows")
        accepted.append(window)
    return deepcopy(document)


def _bind_verified_decoder_snapshot(records, primary, derived):
    """Add absent clock/epoch metadata without changing existing model binds."""
    prepared_records, prepared_primary = deepcopy(records), deepcopy(primary)
    derived_by_frame = {row["frame_index"]: row for row in derived}
    by_frame = {}
    for record in prepared_records:
        frame = record["frame"]
        source = derived_by_frame[frame["index"]]
        actual_ms = float(Fraction(source["pts"] * source["time_base_num"] * 1000, source["time_base_den"]))
        source_ms = frame.get("source_timestamp_ms", frame["timestamp_ms"])
        if not finite(source_ms) or abs(source_ms - actual_ms) > .51:
            raise ValueError("cycle_original_source_timestamp_mismatch")
        frame.update({key: source[key] for key in ("video_sha256", "pts", "time_base_num", "time_base_den")})
        # SAM binds its rounded export timestamp exactly. Precision for all
        # derivatives comes from integer PTS/timebase, never this export value.
        frame["source_timestamp_ms"] = source_ms
        by_frame[frame["index"]] = record
    for row in prepared_primary:
        record = by_frame[row["source_frame_index"]]
        if "selection_epoch" not in row:
            model = _bound_model_points(record, row, unique_source=True)
            row["selection_epoch"] = (model[1]["selection_epoch"] if model is not None else
                derived_by_frame[row["source_frame_index"]]["selection_epoch"])
    return prepared_records, prepared_primary


def compute_verified_cycle_snapshot(records, primary, source_events, decoded, annotation_document):
    """Internal replay only; the public artifact entry certifies actual files.

    A missing first, last or interior source frame rejects the snapshot instead
    of shrinking a requested window. No input pose coordinates are changed.
    """
    document = validate_cycle_annotations(annotation_document)
    run_id, video_sha = document["source_run_id"], _digest(document["video_sha256"])
    # This existing verifier checks exact decoder PTS, original job IDs, unique
    # source/processed frames, selected person and camera reference transitions.
    _, derived = _prepare_samples(records, primary, decoded, video_sha256=video_sha, job_id=run_id)
    if not isinstance(source_events, list) or any(not isinstance(event, Mapping)
            or event.get("video_id") != run_id or not _text(event.get("event_id"))
            or not finite(event.get("start_ms")) or not finite(event.get("end_ms"))
            or event["start_ms"] > event["end_ms"] for event in source_events):
        raise ValueError("cycle_source_event_inventory_invalid")
    if any(any(key in row and row[key] != run_id for key in ("job_id", "video_id")) for row in primary):
        raise ValueError("cycle_primary_source_run_mismatch")
    unique_events = {}
    for event in source_events:
        identifier = event["event_id"]
        if identifier in unique_events and unique_events[identifier] != event:
            raise ValueError("cycle_duplicate_original_event_conflict")
        unique_events[identifier] = event
    source_events = list(unique_events.values())
    by_frame = {record["frame"]["index"]: record for record in records}
    derived_by_frame = {row["frame_index"]: row for row in derived}
    selected_by_frame = {row["source_frame_index"]: row for row in primary}
    prepared_records, prepared_primary = _bind_verified_decoder_snapshot(records, primary, derived)
    events, bindings = [], []
    for window in document["windows"]:
        first, last = window["start_source_frame"], window["end_source_frame"]
        expected = list(range(first, last + 1))
        if last >= len(decoded) or any(index not in by_frame for index in expected):
            raise ValueError("cycle_source_window_frames_missing")
        for edge, index in (("start_ms", first), ("end_ms", last)):
            actual = decoded[index]
            actual_ms = float(Fraction(actual["pts"] * actual["time_base_num"] * 1000, actual["time_base_den"]))
            if abs(window[edge] - actual_ms) > .51:
                raise ValueError("cycle_annotation_frame_time_mismatch")
        if any(index not in selected_by_frame or selected_by_frame[index].get("primary_player_id")
                != window["primary_player_id"] for index in expected):
            raise ValueError("cycle_annotation_primary_identity_mismatch")
        start, end = by_frame[first]["frame"]["timestamp_ms"], by_frame[last]["frame"]["timestamp_ms"]
        involved = [event for event in source_events if event["start_ms"] <= end and event["end_ms"] >= start]
        flags, jumps, swaps = set(), [], []
        jump_complete = swap_complete = True
        for original in involved:
            raw_flags, diagnostics = original.get("quality_flags"), original.get("track_diagnostics")
            if (not isinstance(raw_flags, list) or any(not isinstance(flag, str) for flag in raw_flags)
                    or not isinstance(diagnostics, Mapping)):
                raise ValueError("cycle_original_quality_diagnostics_invalid")
            flags.update(raw_flags)
            if "keypoint_jump_candidates_present" in raw_flags:
                entries = diagnostics.get("keypoint_jump_candidate_joints")
                if not isinstance(entries, list) or not entries or any(not _text(name) for name in entries):
                    jump_complete = False
                else:
                    jumps.extend(entries)
            if "left_right_swap_candidates_present" in raw_flags:
                entries = diagnostics.get("left_right_swap_candidate_joint_pairs")
                if (not isinstance(entries, list) or not entries or any(not isinstance(pair, Mapping)
                        or not all(_text(pair.get(key)) for key in ("left_joint", "right_joint")) for pair in entries)):
                    swap_complete = False
                else:
                    swaps.extend(deepcopy(entries))
        tracks = {derived_by_frame[index]["selected_person_track_id"] for index in expected}
        event = {"event_id": window["event_id"], "event_code": window["event_code"], "video_id": run_id,
            "person_track_id": window["primary_player_id"], "start_ms": start, "end_ms": end,
            "quality_flags": sorted(flags), "track_diagnostics": {
                "primary_identity_ambiguous": None in tracks or len(tracks) != 1,
                "source_track_switch_candidate_count": max(0, len(tracks) - 1),
                "source_track_ids": sorted(track for track in tracks if track is not None),
                "keypoint_jump_candidate_joints": sorted(set(jumps)) if jump_complete else [],
                "left_right_swap_candidate_joint_pairs": swaps if swap_complete else []},
            "source_event_ids": [original["event_id"] for original in involved]}
        events.append(event)
        bindings.append({"method": "manual_anchor", "version": window["binding"]["version"],
            "evidence_reference": window["binding"]["source"], "event_id": window["event_id"],
            "video_id": run_id, "video_sha256": video_sha, "primary_player_id": window["primary_player_id"],
            "start_source_frame": first, "end_source_frame": last})
    sequence = pose_sequence_from_records(prepared_records, prepared_primary)
    sequence.coordinate_metadata["source_run_id"] = run_id
    try:
        report = compute_footwork_cycle_execution(sequence, events, prepared_primary, bindings, source_run_id=run_id)
    finally:
        clear_source_aligned_feature_cache(sequence)
    annotations = {window["event_id"]: window for window in document["windows"]}
    by_event = {event["event_id"]: event for event in events}
    for row in report["cards"]:
        for instance in row["instances"]:
            annotation = annotations[instance["event_id"]]
            instance["source_annotation"] = {"document_sha256": _canonical(document), "document_version": ANNOTATION_VERSION,
                "event_id": annotation["event_id"], "event_code": annotation["event_code"],
                "start_source_frame": annotation["start_source_frame"], "end_source_frame": annotation["end_source_frame"],
                "requested_window_ms": {key: annotation[key] for key in ("start_ms", "end_ms")},
                "binding": deepcopy(annotation["binding"])}
            instance["evidence"]["quality_source_event_ids"] = by_event[instance["event_id"]]["source_event_ids"]
    report["input"].update(annotation_document_sha256=_canonical(document), annotation_version=ANNOTATION_VERSION,
        input_artifact_sha256=deepcopy(document["input_artifact_sha256"]), source_video_sha256=video_sha,
        artifact_files_verified=False, decoder_verified_from_original_video=False, service_eligible=False,
        source_epoch_derivation="preserve_bound_original_epoch_else_derive_selection_camera_transitions_not_human_identity_truth",
        annotation_window_count=len(document["windows"]))
    report["cycle_windows"] = deepcopy(document["windows"])
    return report


def build_footwork_cycle_artifact_report(run_dir, annotation_document, *, video_path=None):
    """Strict production path chosen by the service, with immutable originals."""
    document = validate_cycle_annotations(annotation_document)
    directory = Path(run_dir).resolve()
    if not directory.is_dir() or directory.name != document["source_run_id"]:
        raise ValueError("cycle_source_directory_binding_mismatch")
    expected = {name: _digest(value) for name, value in document["input_artifact_sha256"].items()}
    for name in SOURCE_FILES:
        if not (directory / name).is_file() or _sha(directory / name) != expected[name]:
            raise ValueError("cycle_source_artifact_digest_mismatch:" + name)
    summary = _read_json(directory / "summary.json")
    if summary.get("job_id") != document["source_run_id"]:
        raise ValueError("cycle_source_summary_job_mismatch")
    original = Path(summary.get("input", {}).get("video_path", ""))
    source = Path(video_path).resolve() if video_path is not None else original.resolve()
    video_sha = _digest(document["video_sha256"])
    if not source.is_file() or _sha(source) != video_sha:
        raise ValueError("cycle_source_video_digest_mismatch")
    provenance = summary.get("minimum_scoring_loop", {}).get("provenance", {})
    if _digest(provenance.get("video_sha256")) != video_sha or _digest(provenance.get("frames_sha256")) != expected["frames.jsonl"]:
        raise ValueError("cycle_source_summary_provenance_mismatch")
    decoded = _decode_video_timeline(source, video_sha)
    report = compute_verified_cycle_snapshot(_records(directory / "frames.jsonl"),
        _records(directory / "primary-player.jsonl", 64 * 1024 * 1024),
        _records(directory / "events.jsonl", 64 * 1024 * 1024), decoded, document)
    if any(_sha(directory / name) != expected[name] for name in SOURCE_FILES) or _sha(source) != video_sha:
        raise ValueError("cycle_source_changed_during_execution")
    report["input"].update(artifact_files_verified=True, decoder_verified_from_original_video=True, service_eligible=True,
        artifact_digest_kind="raw_file_sha256", source_job_id=summary["job_id"], artifact_verifier_version=VERSION)
    return report
