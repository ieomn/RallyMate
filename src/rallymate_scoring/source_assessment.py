"""Persist additive source-aligned observations without changing legacy scores."""
from __future__ import annotations

import hashlib
import copy
import json
import re
import sys
import tempfile
from concurrent.futures import CancelledError as FutureCancelledError
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

from rallymate_features.schemas import PoseSequence, validated_source_time_binding
from rallymate_features.foot_crossing import VERSION as FOOT_CROSSING_VERSION, compute_foot_crossing_candidates
from rallymate_features.hip_path_ratio import VERSION as HIP_PATH_VERSION, SAMPLING_PROTOCOL, compute_hip_path_ratio
from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.native_primary import (
    PRIMARY_TIMELINE_ADAPTER_VERSION as NATIVE_ADAPTER_VERSION,
    adapt_native_primary_timeline, NativePrimaryTimelineError,
)
from rallymate_scoring.model_measurement_contract import validated_model_window
from rallymate_scoring.source_aligned_features import (
    clear_source_aligned_feature_cache, compute_source_aligned_features,
    prepare_source_aligned_context,
)

ARTIFACT_NAME = "source-aligned-measurements.json"
ARTIFACT_VERSION = "source-aligned-measurements-artifact-v1.0.0"
CROSSING_ARTIFACT_NAME = "foot-crossing-candidates.json"
CROSSING_ARTIFACT_VERSION = "foot-crossing-candidates-artifact-v1.0.1"
CONTEXTUAL_VERSION = "hip-contextual-measurement-v1.0.0"
CONTEXTUAL_TYPE = "hip_path_ratio_2d"


def _check(cancellation_callback: Callable[[], None] | None) -> None:
    if cancellation_callback is not None:
        cancellation_callback()


def _json_chunks(value: Any, *, sort_keys: bool = False,
                 cancellation_callback: Callable[[], None] | None = None):
    """Keep the existing JSON bytes, without materializing a second full copy."""
    _check(cancellation_callback)
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=sort_keys,
                               separators=(",", ":"), allow_nan=False)
    pending = bytearray()
    for index, token in enumerate(encoder.iterencode(value)):
        if index % 1024 == 0:
            _check(cancellation_callback)
        pending.extend(token.encode("utf-8"))
        if len(pending) >= 64 * 1024:
            yield bytes(pending)
            pending.clear()
    if pending:
        yield bytes(pending)
    _check(cancellation_callback)


def _write_json(path: Path, value: Any, *,
                cancellation_callback: Callable[[], None] | None = None) -> None:
    with path.open("wb") as handle:
        for chunk in _json_chunks(value, cancellation_callback=cancellation_callback):
            handle.write(chunk)
        handle.write(b"\n")


def contextual_input_digest(value: Any, *,
                            cancellation_callback: Callable[[], None] | None = None) -> str:
    """Pin the exact in-memory input, without serializing it into public output."""
    # These repeated, event-local digests keep the C encoder; streaming the
    # complete output below avoids its full extra copy without slowing every
    # small per-window identity check into Python token iteration.
    _check(cancellation_callback)
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    _check(cancellation_callback)
    return digest


def canonical_contextual_window(event: dict) -> dict | None:
    # Event subjects use the canonical primary ID; source_window uses the
    # decoder track ID. Their independent join is checked against raw rows below.
    window = validated_model_window(event)
    if window is None or (window["start_ms"], window["end_ms"]) != (event.get("start_ms"), event.get("end_ms")):
        return None
    window["video_sha256"] = window["video_sha256"].lower()
    for binding in window["source_bindings"]:
        binding["video_sha256"] = binding["video_sha256"].lower()
    return window


_CONTEXTUAL_ATOMIC_TYPES = (str, int, float, bool, type(None))


def _copy_contextual_inputs(value: Any, memo: dict | None = None) -> Any:
    """Own JSON containers once; retain immutable leaves without copy dispatch.

    The source frames stay private to each contextual event exactly as before.
    A shared memo preserves repeated references and cycles; non-JSON values
    keep deepcopy's established tuple/subclass/custom-object behavior.
    """
    if type(value) in _CONTEXTUAL_ATOMIC_TYPES:
        return value
    if memo is None:
        memo = {}
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if type(value) is list:
        result = []
        memo[identity] = result
        result.extend(item if type(item) in _CONTEXTUAL_ATOMIC_TYPES
                      else _copy_contextual_inputs(item, memo) for item in value)
        return result
    if type(value) is dict:
        result = {}
        memo[identity] = result
        for key, item in value.items():
            result[key if type(key) in _CONTEXTUAL_ATOMIC_TYPES else _copy_contextual_inputs(key, memo)] = (
                item if type(item) in _CONTEXTUAL_ATOMIC_TYPES else _copy_contextual_inputs(item, memo))
        return result
    return copy.deepcopy(value, memo)


def _contextual_hip_result(event: dict, *, video_id: str, source_id: str | None,
                           video_sha256: str | None, input_sha256: dict,
                           cancellation_callback: Callable[[], None] | None = None) -> dict:
    return {
        "type": CONTEXTUAL_TYPE, "version": CONTEXTUAL_VERSION, "helper_version": HIP_PATH_VERSION,
        "event_id": event.get("event_id"), "event_code": event.get("event_code"),
        "video_id": video_id, "source_id": source_id,
        "video_sha256": video_sha256.lower() if isinstance(video_sha256, str) else None,
        "person_track_id": event.get("person_track_id"),
        "parent_window_ms": {"start_ms": event.get("start_ms"), "end_ms": event.get("end_ms")},
        "measurement_window_video_ms": None, "source_frame_bounds": None, "source_window": None,
        "source_bindings": [], "source_reference": None,
        "status": "unavailable", "reason": None,
        "hip_path_length_px": None, "hip_net_displacement_px": None, "hip_path_over_net_ratio": None,
        "score": None, "grade": None, "formal_event_verified": False, "scoring_allowed": False,
        "assessment_status": "measurement_only", "coordinate_system": "stable_camera_reference_image_pixels",
        "sampling_protocol": SAMPLING_PROTOCOL,
        "adapter": {"version": NATIVE_ADAPTER_VERSION,
                    "provenance": "unique_same_frame_selected_track_SAM_request_and_pose_binding_and_camera",
                    "source_files_changed": False},
        "input_sha256": copy.deepcopy(input_sha256),
        "window_input_sha256": {"records": None, "original_primary": None,
                                "adapted_primary": None, "event": contextual_input_digest(
                                    event, cancellation_callback=cancellation_callback)},
    }


def compute_contextual_hip_measurement(raw_records: list[dict], primary_timeline: list[dict],
                                      event: dict, *, video_id: str, source_id: str | None,
                                      video_sha256: str | None, input_sha256: dict,
                                      cancellation_callback: Callable[[], None] | None = None) -> dict:
    """Measure a pre-existing whole event on independent native-primary copies.

    The legacy sequence, selected timeline, indicators and event discovery never
    receive this adapter's output. A failing window is retained as unavailable.
    """
    result = _contextual_hip_result(event, video_id=video_id, source_id=source_id,
                                    video_sha256=video_sha256, input_sha256=input_sha256,
                                    cancellation_callback=cancellation_callback)

    def reject(reason: str) -> dict:
        result["reason"] = reason
        return result

    if (not isinstance(event.get("event_id"), str) or not event["event_id"]
            or event.get("video_id") != video_id or not isinstance(source_id, str) or not source_id
            or not isinstance(event.get("provenance"), dict)
            or event["provenance"].get("source_id") != source_id
            or type(event.get("person_track_id")) is not int or event["person_track_id"] < 0
            or type(event["provenance"].get("primary_player_id")) is not int
            or event["person_track_id"] != event["provenance"]["primary_player_id"]):
        return reject("contextual_event_identity_mismatch")
    if (not isinstance(video_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", video_sha256) is None
            or not isinstance(input_sha256, dict)
            or set(input_sha256) != {"frames", "primary_timeline", "events"}
            or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                   for value in input_sha256.values())):
        return reject("contextual_input_identity_unavailable")
    window = canonical_contextual_window(event)
    if window is None:
        return reject("contextual_event_source_window_unavailable")
    if window["video_sha256"] != video_sha256.lower():
        return reject("contextual_event_video_mismatch")
    result.update(source_window=window,
        measurement_window_video_ms=dict(result["parent_window_ms"]),
        source_frame_bounds={"start_source_frame": window["source_frames"][0],
                             "end_source_frame": window["source_frames"][-1]})
    wanted = set(window["source_frames"])
    records, timeline = [], []
    for index, row in enumerate(raw_records):
        if index % 64 == 0:
            _check(cancellation_callback)
        if (isinstance(row, dict) and isinstance(row.get("frame"), dict)
                and type(row["frame"].get("index")) is int and row["frame"]["index"] in wanted):
            records.append(row)
    for index, row in enumerate(primary_timeline):
        if index % 64 == 0:
            _check(cancellation_callback)
        if (isinstance(row, dict) and type(row.get("source_frame_index")) is int
                and row["source_frame_index"] in wanted):
            timeline.append(row)
    result["window_input_sha256"].update(records=contextual_input_digest(
        records, cancellation_callback=cancellation_callback), original_primary=contextual_input_digest(
        timeline, cancellation_callback=cancellation_callback))
    if ([row["frame"]["index"] for row in records] != window["source_frames"]
            or [row["source_frame_index"] for row in timeline] != window["source_frames"]):
        return reject("contextual_source_window_incomplete")
    if any(row.get("job_id") != source_id for row in records):
        return reject("contextual_raw_source_identity_mismatch")
    # The adapter receives no writable aliases into legacy records or timeline.
    _check(cancellation_callback)
    try:
        owned_records = _copy_contextual_inputs(records)
        adapted, diagnostics = adapt_native_primary_timeline(owned_records, _copy_contextual_inputs(timeline),
                                                             video_sha256=video_sha256)
    except NativePrimaryTimelineError:
        return reject("contextual_native_adapter_rejected")
    result["window_input_sha256"]["adapted_primary"] = contextual_input_digest(
        adapted, cancellation_callback=cancellation_callback)
    if diagnostics.get("adapter_version") != NATIVE_ADAPTER_VERSION:
        return reject("contextual_native_adapter_version_mismatch")
    _check(cancellation_callback)
    try:
        sequence = pose_sequence_from_records(owned_records, adapted)
    except (ValueError, KeyError, TypeError, IndexError, ArithmeticError):
        return reject("contextual_source_loader_rejected")
    if sequence.source_frames.tolist() != window["source_frames"]:
        return reject("contextual_source_window_incomplete")
    bindings = []
    for index, frame in enumerate(window["source_frames"]):
        if index % 64 == 0:
            _check(cancellation_callback)
        bindings.append(validated_source_time_binding(sequence.source_time_bindings.get(frame)))
    if any(binding is None for binding in bindings):
        return reject("contextual_source_binding_unavailable")
    for index, (actual, expected) in enumerate(zip(bindings, window["source_bindings"])):
        if index % 64 == 0:
            _check(cancellation_callback)
        if (any(actual[key] != expected[key] for key in ("video_sha256", "frame_index", "processed_index",
                "pts", "time_base_num", "time_base_den", "person_track_id", "selection_epoch", "camera_reference_epoch"))
                or actual["video_timestamp_ms"] != window["video_timestamps_ms"][index]
                or actual["source_timestamp_ms"] != expected["timestamp_ms"]
                or actual["primary_player_id"] != event.get("provenance", {}).get("primary_player_id")):
            return reject("contextual_event_source_binding_mismatch")
    _check(cancellation_callback)
    measured = compute_hip_path_ratio(sequence, primary_timeline=adapted,
        start_source_frame=window["source_frames"][0], end_source_frame=window["source_frames"][-1],
        sampling_protocol=SAMPLING_PROTOCOL)
    _check(cancellation_callback)
    for key in ("status", "reason", "hip_path_length_px", "hip_net_displacement_px", "hip_path_over_net_ratio",
                "source_bindings", "source_reference"):
        if key in measured:
            result[key] = measured[key]
    if result["status"] != "measured" and result["reason"] != "zero_net_displacement":
        for key in ("hip_path_length_px", "hip_net_displacement_px", "hip_path_over_net_ratio"):
            result[key] = None
    return result


def _candidate_failure_must_propagate(error: Exception) -> bool:
    """Keep worker control flow intact without importing the worker/pipeline.

    Worker imports this module before defining its control exceptions. Resolve
    the real, already loaded classes only when an exception is handled; never
    classify an arbitrary RuntimeError by its class name or message.
    BaseException controls (async cancellation, KeyboardInterrupt, SystemExit)
    never enter the optional boundary's Exception handler at all.
    """
    if isinstance(error, (FutureCancelledError, MemoryError, SystemError)):
        return True
    resources = sys.modules.get("rallymate_vision.postprocess_resources")
    resource_limit = getattr(resources, "PostprocessResourceLimit", None)
    if isinstance(resource_limit, type) and isinstance(error, resource_limit):
        return True
    worker = sys.modules.get("rallymate_service.worker")
    for name in ("JobCancelled", "JobLeaseLost"):
        control = getattr(worker, name, None)
        if isinstance(control, type) and isinstance(error, control):
            return True
    return False


@lru_cache(maxsize=1)
def source_binding() -> dict:
    catalog = json.loads(files("rallymate_scoring").joinpath("data/technical_review_rules.json").read_text(encoding="utf-8"))
    return {"reference_version": catalog["source_reference_version"],
            "reference_sha256": catalog["source_reference_sha256"],
            "document": next(item for item in catalog["source_documents"] if item["id"] == "CARD-FS")}


def file_sha(path: Path, *, cancellation_callback: Callable[[], None] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            _check(cancellation_callback)
            digest.update(chunk)
    return digest.hexdigest()


def write_foot_crossing_candidates(output_dir: Path, sequence: PoseSequence,
                                   events: list[dict[str, Any]], *, video_id: str,
                                   video_sha256: str | None = None,
                                   frames_sha256: str | None = None,
                                   frames_path: Path | None = None,
                                   primary_timeline_path: Path | None = None,
                                   primary_timeline: list[dict] | None = None,
                                   cancellation_callback: Callable[[], None] | None = None) -> dict:
    """Write independent geometry evidence without invoking any score calculation."""
    if video_sha256 is not None:
        if not isinstance(video_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", video_sha256) is None:
            raise ValueError("candidate video_sha256 must be a SHA256 hex digest")
        expected_video = video_sha256.lower()
        for index, raw_binding in enumerate(sequence.source_time_bindings.values()):
            if index % 64 == 0:
                _check(cancellation_callback)
            binding = validated_source_time_binding(raw_binding)
            if binding is not None and binding["video_sha256"] != expected_video:
                raise ValueError("candidate video identity disagrees with source-time bindings")
    _check(cancellation_callback)
    candidate_result = compute_foot_crossing_candidates(
        sequence, primary_timeline=primary_timeline, events=events, video_id=video_id,
        cancellation_callback=cancellation_callback)
    artifact = {
        "artifact_version": CROSSING_ARTIFACT_VERSION,
        "pose_input_identity": "frames_sha256_pins_exact_model_observations_backbone_name_not_in_PoseSequence_contract",
        "source_binding_origin": "validated_saved_decoder_frame_and_primary_records_with_additional_SAM_request_binding_when_present",
        "video_id": video_id, "source_binding": source_binding(),
        "video_sha256": video_sha256.lower() if video_sha256 else None,
        "frames_sha256": frames_sha256.lower() if frames_sha256 else file_sha(
            frames_path or output_dir / "frames.jsonl", cancellation_callback=cancellation_callback),
        "events_sha256": file_sha(output_dir / "events.jsonl", cancellation_callback=cancellation_callback),
        "primary_timeline_sha256": file_sha(primary_timeline_path or output_dir / "primary-player.jsonl",
                                            cancellation_callback=cancellation_callback),
        **candidate_result,
    }
    path = output_dir / CROSSING_ARTIFACT_NAME
    temporary: Path | None = None
    try:
        # Each attempt owns only its uniquely named temporary. Do not delete a
        # previous complete artifact, or another attempt's unfinished file.
        with tempfile.NamedTemporaryFile(dir=output_dir, prefix=CROSSING_ARTIFACT_NAME + ".",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        _write_json(temporary, artifact, cancellation_callback=cancellation_callback)
        _check(cancellation_callback)
        temporary.replace(path)
        return {"status": "available", "path": CROSSING_ARTIFACT_NAME, "sha256": file_sha(
                    path, cancellation_callback=cancellation_callback),
                "artifact_version": CROSSING_ARTIFACT_VERSION,
                "version": candidate_result["version"], "candidate_count": candidate_result["counts"]["candidates"],
                "stored_candidate_count": len(candidate_result["candidates"]), "scoring_allowed": False}
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Cleanup must not replace the actual write error or a task
                # cancellation. No failed attempt is published as available.
                pass


def write_source_aligned_assessment(output_dir: Path, sequence: PoseSequence,
                                    events: list[dict[str, Any]], *, video_id: str,
                                    video_sha256: str | None = None,
                                    frames_sha256: str | None = None,
                                    frames_path: Path | None = None,
                                    primary_timeline_path: Path | None = None,
                                    primary_timeline: list[dict] | None = None,
                                    raw_records: list[dict] | None = None,
                                    source_id: str | None = None,
                                    cancellation_callback: Callable[[], None] | None = None,
                                    cpu_pool=None) -> dict:
    # Optional measurements may fail closed, but never consume callback control
    # flow, including caller-defined exceptions outside the known worker types.
    callback_error: Exception | None = None

    def check() -> None:
        nonlocal callback_error
        try:
            _check(cancellation_callback)
        except Exception as error:
            callback_error = error
            raise

    check()
    context = prepare_source_aligned_context(sequence, events, primary_timeline=primary_timeline,
                                              cancellation_callback=check)
    try:
        records = []
        for event in events:
            check()
            if event.get("event_code") in {"FS01", "FS02", "FS09"}:
                records.append(compute_source_aligned_features(sequence, event, events=events, context=context,
                                                                cancellation_callback=check))
    finally:
        clear_source_aligned_feature_cache(sequence)
    artifact = {
        "version": ARTIFACT_VERSION, "video_id": video_id,
        "source_binding": source_binding(),
        "video_sha256": video_sha256.lower() if video_sha256 else None,
        "frames_sha256": frames_sha256.lower() if frames_sha256 else file_sha(
            frames_path or output_dir / "frames.jsonl", cancellation_callback=check),
        "events_sha256": file_sha(output_dir / "events.jsonl", cancellation_callback=check),
        "primary_timeline_sha256": file_sha(primary_timeline_path or output_dir / "primary-player.jsonl",
                                            cancellation_callback=check),
        "score_semantics": "source_aligned_measurement_not_technical_grade",
        "technical_grade": None, "technical_score_0_to_100": None,
        "records": records,
    }
    if raw_records is not None:
        artifact["source_id"] = source_id
        inputs = {"frames": artifact["frames_sha256"], "events": artifact["events_sha256"],
                  "primary_timeline": artifact["primary_timeline_sha256"]}
        selected_events = [event for event in events
                           if event.get("event_code") in {"FS01", "FS02", "FS09"}]
        options = dict(video_id=video_id, source_id=source_id,
                       video_sha256=video_sha256, input_sha256=inputs)
        if cpu_pool is not None and cpu_pool.workers > 1:
            tasks = contextual_window_tasks(raw_records, primary_timeline or [], selected_events,
                                            options, cancellation_callback=check)
            results = cpu_pool.ordered_map(_contextual_measurement_safely, tasks)
        else:
            results = (_contextual_measurement_safely(raw_records, primary_timeline or [], event,
                       **options, cancellation_callback=check) for event in selected_events)
        completed = 0
        for index, contextual in enumerate(results):
            check()
            if index >= len(records):
                raise RuntimeError("Contextual result count exceeded event count")
            records[index]["contextual_measurements"] = [contextual]
            completed += 1
        if completed != len(records):
            raise RuntimeError("Contextual result count did not match event count")
    path = output_dir / ARTIFACT_NAME
    temporary = output_dir / (ARTIFACT_NAME + ".tmp")
    try:
        _write_json(temporary, artifact, cancellation_callback=check)
        check()
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    check()
    try:
        crossing_artifact = write_foot_crossing_candidates(
            output_dir, sequence, events, video_id=video_id, video_sha256=video_sha256,
            frames_sha256=frames_sha256, frames_path=frames_path,
            primary_timeline_path=primary_timeline_path, primary_timeline=primary_timeline,
            cancellation_callback=check)
    except Exception as error:
        if error is callback_error or _candidate_failure_must_propagate(error):
            raise
        # This boundary belongs only to the optional sidecar. Legacy compute,
        # serialization and writes above retain their original failure behavior.
        crossing_artifact = {
            "status": "unavailable", "reason_code": "optional_candidate_generation_failed",
            "error_type": type(error).__name__[:80],
            "path": None, "sha256": None, "version": FOOT_CROSSING_VERSION,
            "artifact_version": CROSSING_ARTIFACT_VERSION,
            "candidate_count": None, "stored_candidate_count": None, "scoring_allowed": False,
            "existing_candidate_file_is_not_this_attempt": True,
        }
    check()
    return {"path": ARTIFACT_NAME, "sha256": file_sha(path, cancellation_callback=check), "event_count": len(records),
            "indicator_window_count": sum(len(r["indicators"]) for r in records),
            "foot_crossing_candidates": crossing_artifact}


def _contextual_measurement_safely(raw_records, primary_timeline, event, *, video_id,
                                  source_id, video_sha256, input_sha256,
                                  cancellation_callback=None):
    """Preserve the optional measurement boundary in serial and child execution."""
    callback_error = None

    def check():
        nonlocal callback_error
        try:
            _check(cancellation_callback)
        except Exception as error:
            callback_error = error
            raise

    try:
        return compute_contextual_hip_measurement(raw_records, primary_timeline, event,
            video_id=video_id, source_id=source_id, video_sha256=video_sha256,
            input_sha256=input_sha256, cancellation_callback=check)
    except Exception as error:
        if error is callback_error or _candidate_failure_must_propagate(error):
            raise
        contextual = _contextual_hip_result(event, video_id=video_id, source_id=source_id,
            video_sha256=video_sha256, input_sha256=input_sha256, cancellation_callback=check)
        contextual["reason"] = "contextual_measurement_failed"
        window = canonical_contextual_window(event)
        if window is not None:
            contextual.update(source_window=window,
                measurement_window_video_ms=dict(contextual["parent_window_ms"]),
                source_frame_bounds={"start_source_frame": window["source_frames"][0],
                                     "end_source_frame": window["source_frames"][-1]})
        return contextual


def contextual_window_tasks(raw_records, primary_timeline, events, options, *,
                            cancellation_callback=None):
    """Transfer full event windows, preserving source order and duplicate rows.

    The measurement itself already discards rows outside this exact window.
    Index once in the parent rather than scan and copy the entire video per
    child. Nothing inside the selected window is reduced or resampled.
    """
    raw_index, timeline_index = {}, {}
    for index, row in enumerate(raw_records):
        if index % 64 == 0:
            _check(cancellation_callback)
        if (isinstance(row, dict) and isinstance(row.get("frame"), dict)
                and type(row["frame"].get("index")) is int):
            raw_index.setdefault(row["frame"]["index"], []).append((index, row))
    for index, row in enumerate(primary_timeline):
        if index % 64 == 0:
            _check(cancellation_callback)
        if isinstance(row, dict) and type(row.get("source_frame_index")) is int:
            timeline_index.setdefault(row["source_frame_index"], []).append((index, row))
    for event in events:
        _check(cancellation_callback)
        try:
            window = canonical_contextual_window(event)
        except Exception as error:
            if _candidate_failure_must_propagate(error):
                raise
            # Let the unchanged measurement boundary classify malformed input.
            window = None
        if window is None:
            rows, timeline = raw_records, primary_timeline
        else:
            wanted = set(window["source_frames"])
            rows = [row for _, row in sorted(
                (item for frame in wanted for item in raw_index.get(frame, ())), key=lambda item: item[0])]
            timeline = [row for _, row in sorted(
                (item for frame in wanted for item in timeline_index.get(frame, ())), key=lambda item: item[0])]
        yield ((rows, timeline, event), options)
