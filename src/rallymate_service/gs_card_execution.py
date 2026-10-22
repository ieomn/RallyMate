"""Execute GS card proxies from explicit source-stage annotations and raw frames.

The artifact entry point verifies the original video, decoder PTS, frame file,
selection file and run summary. It never accepts saved conditions or grades.
Manual annotations identify estimated source stages; no ten-stage detector is
claimed. The record-level function supports deterministic internal replay and
does not claim to have checked files or decoded a video.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import subprocess

from rallymate_features.coordinates import validated_camera_transform
from rallymate_scoring.card_evaluation import evaluate_card_window, summarize_card_scores
from rallymate_scoring.card_kinematics import _point as _valid_card_point, finite, validate_window
from rallymate_scoring.card_rule_catalog import load_card_rules
from rallymate_scoring.stroke_candidates import _sample, _stabilize_partial_torso_windows
from rallymate_scoring.gs_preload_evidence import (
    ANNOTATION_VERSION as PRELOAD_ANNOTATION_VERSION, ANNOTATION_SEMANTICS as PRELOAD_SEMANTICS,
    SUPPORTED_CARDS as PRELOAD_CARDS, AUXILIARY_REFERENCES as PRELOAD_REFERENCES, compute_gs_preload_evidence,
)

VERSION = "gs-card-execution-v1.0.0"
ANNOTATION_VERSION = "gs-stage-annotations-v1.0.0"
RACKET_SIDE_VERSION = "manual-racket-side-observation-v1.0.0"
RACKET_SIDE_SEMANTICS = "visually_observed_racket_holding_wrist_not_dominant_hand"
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_FRAME_BYTES = 2 * 1024 * 1024 * 1024
MAX_FRAME_RECORDS = 150000
MAX_ANNOTATIONS = 2000
SOURCE_FILES = ("frames.jsonl", "primary-player.jsonl", "summary.json")
# Automatic source-stage classifiers require a separately implemented, reviewed
# registration. A caller cannot register a detector by supplying its name.
REGISTERED_STAGE_DETECTORS = frozenset()


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError("invalid_source_sha256")
    return value.lower()


def _integer(value):
    return type(value) is int and value >= 0


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 512


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("nonfinite_json_value:" + value)


def _read_json(path, limit=16 * 1024 * 1024):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("json_source_missing_or_too_large")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)


def _records(path, maximum_bytes=MAX_FRAME_BYTES):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > maximum_bytes:
        raise ValueError("record_source_missing_or_too_large")
    result, consumed = [], 0
    with path.open("rb") as stream:
        while line := stream.readline(MAX_RECORD_BYTES + 1):
            consumed += len(line)
            if len(line) > MAX_RECORD_BYTES or consumed > maximum_bytes or len(result) >= MAX_FRAME_RECORDS:
                raise ValueError("record_source_limit_exceeded")
            if not line.strip():
                continue
            row = json.loads(line, object_pairs_hook=_unique, parse_constant=_nonfinite)
            if not isinstance(row, dict):
                raise ValueError("source_record_not_object")
            result.append(row)
    return result


def _keys(value, required, optional=()):
    if not isinstance(value, Mapping) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError("annotation_schema_fields_invalid")


def _validate_racket_side_observation(observation, action, video_sha256):
    _keys(observation, ("version", "method", "action_id", "video_sha256", "person_track_id",
        "measurement_wrist_side", "observed_racket_hands", "source", "source_description",
        "uncertainty_ms", "semantics", "source_frames"))
    if (observation["version"] != RACKET_SIDE_VERSION or observation["method"] != "manual_visual_observation"
            or observation["semantics"] != RACKET_SIDE_SEMANTICS):
        raise ValueError("racket_side_observation_version_or_semantics_invalid")
    if (observation["action_id"] != action["action_id"]
            or not _integer(observation["person_track_id"])
            or observation["person_track_id"] != action["person_track_id"]
            or _digest(observation["video_sha256"]) != _digest(video_sha256)):
        raise ValueError("racket_side_action_person_or_video_mismatch")
    side, hands = observation["measurement_wrist_side"], observation["observed_racket_hands"]
    if side not in ("left", "right") or hands not in ("left", "right", "both"):
        raise ValueError("racket_side_value_invalid")
    if (action["technique_code"] == "GS02" and hands != "both") or (hands != "both" and hands != side):
        raise ValueError("racket_side_hands_or_technique_mismatch")
    if (any(not _text(observation[key]) or not observation[key].strip() for key in ("source", "source_description"))
            or not finite(observation["uncertainty_ms"]) or observation["uncertainty_ms"] < 0):
        raise ValueError("racket_side_review_description_invalid")
    frames = observation["source_frames"]
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_ANNOTATIONS:
        raise ValueError("racket_side_source_frames_invalid")
    previous = -1
    for frame in frames:
        _keys(frame, ("frame_index", "pts", "time_base_num", "time_base_den"))
        if (not _integer(frame["frame_index"]) or frame["frame_index"] <= previous
                or not _integer(frame["pts"]) or not _integer(frame["time_base_num"]) or frame["time_base_num"] == 0
                or not _integer(frame["time_base_den"]) or frame["time_base_den"] == 0):
            raise ValueError("racket_side_source_frame_identity_invalid")
        previous = frame["frame_index"]


def validate_stage_annotations(document):
    """Validate author-supplied identities and stages, never technical truth."""
    _keys(document, ("version", "source_run_id", "video_sha256", "input_artifact_sha256", "actions", "stages"))
    if document["version"] != ANNOTATION_VERSION or not _text(document["source_run_id"]):
        raise ValueError("annotation_version_or_run_id_invalid")
    _digest(document["video_sha256"])
    _keys(document["input_artifact_sha256"], SOURCE_FILES)
    for digest in document["input_artifact_sha256"].values():
        _digest(digest)
    if (not isinstance(document["actions"], list) or not isinstance(document["stages"], list)
            or len(document["actions"]) > MAX_ANNOTATIONS or len(document["stages"]) > MAX_ANNOTATIONS):
        raise ValueError("annotation_count_invalid")
    stages = {row["stage_code"]: row for row in load_card_rules()["stages"] if row["stage_code"].startswith("GS")}
    techniques = {row["event_code"] for row in stages.values()}
    actions = {}
    for action in document["actions"]:
        _keys(action, ("action_id", "technique_code", "person_track_id", "start_ms", "end_ms"), ("racket_side_observation",))
        if (not _text(action["action_id"]) or action["action_id"] in actions or not _text(action["technique_code"]) or action["technique_code"] not in techniques
                or not _integer(action["person_track_id"]) or not finite(action["start_ms"]) or not finite(action["end_ms"])
                or not 0 <= action["start_ms"] < action["end_ms"]):
            raise ValueError("action_identity_or_window_invalid")
        if "racket_side_observation" in action:
            _validate_racket_side_observation(action["racket_side_observation"], action, document["video_sha256"])
        actions[action["action_id"]] = action
    ids, accepted = set(), []
    for annotation in document["stages"]:
        _keys(annotation, ("annotation_id", "action_id", "technique_code", "stage_code", "person_track_id", "start_ms", "end_ms", "binding"),
              ("local_preload_observation",))
        identifier = annotation["annotation_id"]
        if not _text(identifier) or identifier in ids:
            raise ValueError("duplicate_or_invalid_annotation_id")
        ids.add(identifier)
        if not _text(annotation["action_id"]) or not _text(annotation["stage_code"]):
            raise ValueError("unknown_action_or_source_stage")
        action = actions.get(annotation["action_id"])
        stage = stages.get(annotation["stage_code"])
        if action is None or stage is None:
            raise ValueError("unknown_action_or_source_stage")
        if (not _integer(annotation["person_track_id"]) or annotation["technique_code"] != action["technique_code"] or stage["event_code"] != action["technique_code"]
                or annotation["person_track_id"] != action["person_track_id"]):
            raise ValueError("annotation_technique_stage_or_person_mismatch")
        start, end = annotation["start_ms"], annotation["end_ms"]
        if not finite(start) or not finite(end) or not action["start_ms"] <= start < end <= action["end_ms"]:
            raise ValueError("annotation_stage_outside_action_window")
        binding = annotation["binding"]
        _keys(binding, ("method", "version", "source", "uncertainty_ms", "semantics"))
        if (not _text(binding["version"]) or not _text(binding["source"])
                or not finite(binding["uncertainty_ms"]) or binding["uncertainty_ms"] < 0):
            raise ValueError("annotation_binding_invalid")
        if binding["method"] == "manual_anchor":
            if binding["semantics"] != "reviewer_estimated_source_stage_not_expert_ground_truth":
                raise ValueError("manual_stage_semantics_invalid")
        elif binding["method"] == "versioned_detector":
            if (binding["version"] not in REGISTERED_STAGE_DETECTORS
                    or binding["semantics"] != "registered_local_visual_proxy_not_full_source_stage"):
                raise ValueError("automatic_stage_detector_unregistered")
        else:
            raise ValueError("stage_binding_method_invalid")
        if "local_preload_observation" in annotation:
            _validate_local_preload_observation(annotation["local_preload_observation"], annotation, document)
        for previous in accepted:
            if annotation["action_id"] == previous["action_id"] and annotation["stage_code"] == previous["stage_code"]:
                raise ValueError("duplicate_action_stage_annotation")
            if annotation["person_track_id"] != previous["person_track_id"]:
                continue
            overlap = min(end, previous["end_ms"]) - max(start, previous["start_ms"])
            if overlap > 0 and (annotation["stage_code"] == previous["stage_code"]
                    or annotation["technique_code"] != previous["technique_code"]
                    or annotation["action_id"] != previous["action_id"]):
                raise ValueError("overlapping_stage_annotation_conflict")
            if (start, end) == (previous["start_ms"], previous["end_ms"]):
                raise ValueError("identical_window_reused_for_distinct_source_stages")
            if annotation["action_id"] == previous["action_id"]:
                earlier, later = sorted((previous, annotation), key=lambda row: int(row["stage_code"].rsplit("M", 1)[1]))
                if earlier["start_ms"] >= later["start_ms"] or earlier["end_ms"] >= later["end_ms"]:
                    raise ValueError("source_stage_order_or_containment_conflict")
        accepted.append(annotation)
    return deepcopy(document)


def _validate_local_preload_observation(observation, stage, document):
    _keys(observation, ("version", "method", "annotation_id", "action_id", "indicator_id", "video_sha256",
        "person_track_id", "start_frame", "end_frame", "source", "source_description", "uncertainty_ms", "semantics"))
    if (observation["version"] != PRELOAD_ANNOTATION_VERSION or observation["method"] != "manual_anchor"
            or observation["semantics"] != PRELOAD_SEMANTICS):
        raise ValueError("local_preload_version_or_semantics_invalid")
    identifier = observation["indicator_id"]
    if (not _text(identifier) or identifier not in PRELOAD_CARDS
            or identifier.rsplit("-", 1)[0] != stage["stage_code"]):
        raise ValueError("local_preload_card_or_stage_mismatch")
    if (observation["annotation_id"] != stage["annotation_id"] or observation["action_id"] != stage["action_id"]
            or not _integer(observation["person_track_id"]) or observation["person_track_id"] != stage["person_track_id"]
            or _digest(observation["video_sha256"]) != _digest(document["video_sha256"])):
        raise ValueError("local_preload_action_person_or_video_mismatch")
    if (any(not _text(observation[key]) or not observation[key].strip() for key in ("source", "source_description"))
            or not finite(observation["uncertainty_ms"]) or observation["uncertainty_ms"] < 0):
        raise ValueError("local_preload_review_description_invalid")
    times = []
    for key in ("start_frame", "end_frame"):
        row = observation[key]
        _keys(row, ("frame_index", "pts", "time_base_num", "time_base_den"))
        if (not _integer(row["frame_index"]) or not _integer(row["pts"])
                or not _integer(row["time_base_num"]) or not row["time_base_num"]
                or not _integer(row["time_base_den"]) or not row["time_base_den"]):
            raise ValueError("local_preload_source_frame_invalid")
        times.append(Fraction(row["pts"] * row["time_base_num"] * 1000, row["time_base_den"]))
    if (observation["start_frame"]["frame_index"] >= observation["end_frame"]["frame_index"]
            or not stage["start_ms"] <= times[0] < times[1] <= stage["end_ms"]):
        raise ValueError("local_preload_window_outside_source_stage")


def _local_preload_evidence(observation, annotation, document, decoder_timeline, timeline, by_track, stage_reasons):
    for key in ("start_frame", "end_frame"):
        row = observation[key]
        if row["frame_index"] >= len(decoder_timeline) or any(
                decoder_timeline[row["frame_index"]][field] != value for field, value in row.items()):
            raise ValueError("local_preload_source_frame_or_pts_mismatch")
    start, end = (observation[key]["frame_index"] for key in ("start_frame", "end_frame"))
    expected = decoder_timeline[start:end + 1]
    observed = {row["frame_index"]: row for row in timeline if start <= row["frame_index"] <= end}
    bound = [dict(observed[row["frame_index"]]) if row["frame_index"] in observed else {
        **dict(row), "timestamp_ms": float(Fraction(row["pts"] * row["time_base_num"] * 1000, row["time_base_den"])),
        "selected_person_track_id": None, "selection_epoch": None, "camera_reference_epoch": None} for row in expected]
    samples = [sample for sample in by_track.get(annotation["person_track_id"], []) if start <= sample.frame_index <= end]
    sample = samples[0] if samples else None
    window = {"start_ms": bound[0]["timestamp_ms"], "end_ms": bound[-1]["timestamp_ms"],
        "person_track_id": annotation["person_track_id"], "selection_epoch": sample.selection_epoch if sample else None,
        "camera_reference_epoch": sample.camera_reference_epoch if sample else None,
        "camera_compensated": sample.camera_compensated if sample else False,
        "video_sha256": _digest(document["video_sha256"]), "video_id": document["source_run_id"],
        "event_id": annotation["annotation_id"], "event_code": annotation["technique_code"], "stage_code": annotation["stage_code"]}
    result = compute_gs_preload_evidence(samples, window, bound)
    if stage_reasons:
        result["status"] = "unavailable"
        result["reason_codes"] = sorted(set(result["reason_codes"] + ["parent_gs_stage_unavailable"] + list(stage_reasons)))
        for row in result["auxiliary_observations"]:
            row["value"] = None
        result["joint_descent_fraction"] = None
    result["source_annotation"] = {"observation": deepcopy(observation), "document_version": document["version"],
        "document_sha256": _canonical(document), "digest_kind": "canonical_json_sorted_utf8_v1",
        "stage_code": annotation["stage_code"], "stage_window_ms": {"start_ms": annotation["start_ms"], "end_ms": annotation["end_ms"]},
        "expert_adjudicated": False, "file_verification": "parent_report_input_flags"}
    return result


def _decode_video_timeline(path, expected_digest):
    import imageio_ffmpeg
    if _sha(path) != expected_digest:
        raise ValueError("source_video_digest_mismatch")
    try:
        completed = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-i", str(path),
            "-map", "0:v:0", "-vf", "showinfo", "-frames:v", str(MAX_FRAME_RECORDS + 1), "-an", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("source_video_decode_timeout") from exc
    if completed.returncode:
        raise ValueError("source_video_decode_failed")
    timebase = re.search(r"config in time_base:\s*(\d+)/(\d+)", completed.stderr)
    if not timebase:
        raise ValueError("decoder_timebase_missing")
    numerator, denominator = map(int, timebase.groups())
    if min(numerator, denominator) <= 0:
        raise ValueError("decoder_timebase_invalid")
    rows = [{"frame_index": int(index), "pts": int(pts), "time_base_num": numerator,
             "time_base_den": denominator, "video_sha256": expected_digest}
            for index, pts in re.findall(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:", completed.stderr)]
    if not rows or len(rows) > MAX_FRAME_RECORDS or any(row["frame_index"] != index for index, row in enumerate(rows)):
        raise ValueError("decoder_frame_inventory_invalid")
    return rows


def _prepare_samples(frame_records, primary_timeline, decoder_timeline, *, video_sha256, job_id):
    if (not isinstance(frame_records, Sequence) or not frame_records or len(frame_records) > MAX_FRAME_RECORDS
            or not isinstance(primary_timeline, Sequence) or not isinstance(decoder_timeline, Sequence)
            or len(primary_timeline) > MAX_FRAME_RECORDS or len(decoder_timeline) > MAX_FRAME_RECORDS):
        raise ValueError("source_observation_inventory_invalid")
    decoded = {}
    previous_pts = None
    for index, row in enumerate(decoder_timeline):
        if (not isinstance(row, Mapping) or row.get("frame_index") != index
                or _digest(row.get("video_sha256")) != video_sha256
                or not _integer(row.get("pts")) or type(row.get("time_base_num")) is not int or row["time_base_num"] <= 0
                or type(row.get("time_base_den")) is not int or row["time_base_den"] <= 0):
            raise ValueError("decoder_source_binding_invalid")
        time = Fraction(row["pts"] * row["time_base_num"] * 1000, row["time_base_den"])
        if previous_pts is not None and time <= previous_pts:
            raise ValueError("decoder_pts_nonmonotonic")
        decoded[index] = {**row, "actual_ms": float(time)}
        previous_pts = time
    selected = {}
    for row in primary_timeline:
        if not isinstance(row, Mapping):
            raise ValueError("primary_record_not_object")
        if any(key in row and row[key] != job_id for key in ("job_id", "video_id")):
            raise ValueError("primary_source_run_binding_mismatch")
        index = row.get("processed_index")
        if not _integer(index) or index in selected:
            raise ValueError("duplicate_or_invalid_primary_frame")
        selected[index] = row
    by_track, all_tracks, timeline = defaultdict(list), defaultdict(list), []
    epoch, previous_selected, previous_camera_epoch = 0, None, None
    previous_frame, previous_time = -1, -1
    frame_processed = set()
    for record in frame_records:
        if not isinstance(record, Mapping) or record.get("job_id") != job_id:
            raise ValueError("source_frame_job_binding_mismatch")
        if not isinstance(record.get("frame"), Mapping):
            raise ValueError("source_frame_not_object")
        frame, camera = dict(record.get("frame", {})), record.get("camera_motion")
        index, processed = frame.get("index"), frame.get("processed_index")
        if (not _integer(index) or index not in decoded or index <= previous_frame
                or not _integer(processed) or processed in frame_processed or not finite(frame.get("timestamp_ms"))
                or frame["timestamp_ms"] <= previous_time or frame.get("timestamp_source") != "decoder_pts"):
            raise ValueError("source_frame_identity_or_time_invalid")
        if frame.get("video_sha256") is not None and _digest(frame["video_sha256"]) != video_sha256:
            raise ValueError("source_frame_video_binding_mismatch")
        source = decoded[index]
        if abs(source["actual_ms"] - frame["timestamp_ms"]) > .51:
            raise ValueError("source_frame_decoder_pts_mismatch")
        for key in ("pts", "time_base_num", "time_base_den"):
            if key in frame and frame[key] != source[key]:
                raise ValueError("source_frame_pts_identity_mismatch")
        frame_processed.add(processed)
        previous_frame, previous_time = index, frame["timestamp_ms"]
        selection = selected.get(processed)
        if selection is not None and (selection.get("source_frame_index") != index or selection.get("timestamp_ms") != frame["timestamp_ms"]):
            raise ValueError("primary_frame_time_binding_mismatch")
        current = (selection.get("source_track_id") if selection is not None
                   and selection.get("selection_status") == "selected" and selection.get("identity_ambiguous") is False else None)
        if current is not None and not _integer(current):
            raise ValueError("selected_person_identity_invalid")
        if isinstance(camera, Mapping):
            camera_epoch, status = camera.get("reference_epoch"), camera.get("status", "unavailable")
            transform = validated_camera_transform(camera)
            if (("compensation_valid" in camera or "matrix_to_reference" in camera)
                    and (transform is None or not _integer(camera_epoch) or status not in {"reference", "stationary", "moving"})):
                status = "unavailable"
            elif status == "moving" and transform is not None:
                status = "compensated"
            if status not in {"reference", "stationary", "compensated", "moving", "unavailable"}:
                status = "unavailable"
            frame.update(camera_motion_status=status, camera_motion=camera)
            if previous_camera_epoch is not None and camera_epoch != previous_camera_epoch:
                epoch += 1
            previous_camera_epoch = camera_epoch
        else:
            frame["camera_motion_status"] = "unavailable"
        if current != previous_selected:
            epoch += 1
        previous_selected = current
        if frame["camera_motion_status"] in {"moving", "unavailable"}:
            epoch += 1
        timeline.append({key: value for key, value in source.items() if key != "actual_ms"} | {
            "timestamp_ms": frame["timestamp_ms"], "selected_person_track_id": current, "selection_epoch": epoch,
            "camera_reference_epoch": camera.get("reference_epoch") if isinstance(camera, Mapping) else None})
        poses = record.get("poses", [])
        if (not isinstance(poses, list) or any(not isinstance(pose, Mapping) or not _integer(pose.get("person_track_id")) for pose in poses)
                or len({pose.get("person_track_id") for pose in poses}) != len(poses)):
            raise ValueError("duplicate_or_invalid_pose_identity")
        for pose in poses:
            sample = _sample(pose, frame)
            if sample is None:
                continue
            sample.selection_epoch = epoch
            if not sample.source_binding:
                sample.source_binding = {"video_sha256": video_sha256, "frame_index": sample.frame_index,
                    "person_track_id": sample.track, "selection_epoch": epoch,
                    "camera_reference_epoch": sample.camera_reference_epoch,
                    "pts": source["pts"], "time_base_num": source["time_base_num"], "time_base_den": source["time_base_den"],
                    "timestamp_ms": source["actual_ms"], "binding_source": "verified_decoder_and_run_artifact_snapshot"}
            all_tracks[sample.track].append(sample)
            if sample.track == current:
                by_track[sample.track].append(sample)
    if set(selected) - frame_processed:
        raise ValueError("primary_frame_not_in_source_snapshot")
    _stabilize_partial_torso_windows(all_tracks)
    return by_track, timeline


def _unavailable(rule, window, reasons):
    return {"version": VERSION, "indicator_id": rule["indicator_id"], "rule_version": rule["rule_version"],
        "video_id": window["video_id"], "event_id": window["event_id"], "person_track_id": window["person_track_id"],
        "window": window, "status": "unavailable", "original_grade": None, "proxy_grade": None, "score": None,
        "source_grade_supported": False, "reason_codes": sorted(set(reasons)), "conditions": []}


def _bind_racket_side_observations(document, timeline, by_track):
    """Verify manual references, without inferring grip or dominant hand."""
    source_by_index = {row["frame_index"]: row for row in timeline}
    sample_by_identity = {(sample.track, sample.frame_index): sample for group in by_track.values() for sample in group}
    bindings = {}
    for action in document["actions"]:
        observation = action.get("racket_side_observation")
        if observation is None:
            continue
        verified = []
        for frame in observation["source_frames"]:
            source = source_by_index.get(frame["frame_index"])
            if source is None or any(source[key] != frame[key] for key in ("pts", "time_base_num", "time_base_den")):
                raise ValueError("racket_side_source_frame_or_pts_mismatch")
            actual_ms = Fraction(source["pts"] * source["time_base_num"] * 1000, source["time_base_den"])
            if not action["start_ms"] <= actual_ms <= action["end_ms"]:
                raise ValueError("racket_side_source_frame_outside_action")
            if source["selected_person_track_id"] != action["person_track_id"]:
                raise ValueError("racket_side_selected_person_mismatch")
            sample = sample_by_identity.get((action["person_track_id"], frame["frame_index"]))
            hands = ("left", "right") if observation["observed_racket_hands"] == "both" else (observation["measurement_wrist_side"],)
            if sample is None or any(_valid_card_point(sample, hand + "_wrist") is None for hand in hands):
                raise ValueError("racket_side_source_wrist_observation_missing")
            verified.append({**deepcopy(frame), "video_sha256": _digest(document["video_sha256"]),
                "person_track_id": action["person_track_id"], "timestamp_ms": float(actual_ms),
                "selection_epoch": source["selection_epoch"], "camera_reference_epoch": source["camera_reference_epoch"]})
        bindings[action["action_id"]] = {**deepcopy(observation), "technique_code": action["technique_code"],
            "action_window_ms": {"start_ms": action["start_ms"], "end_ms": action["end_ms"]},
            "document_version": document["version"],
            "document_sha256": _canonical(document), "digest_kind": "canonical_json_sorted_utf8_v1",
            "verification": "source_frames_matched_to_decoder_and_selected_person",
            "evidence_scope": "sampled_frames_only", "expert_adjudicated": False,
            "dominant_hand_established": False, "continuous_racket_hold_established": False,
            "source_frame_bindings": verified}
    return bindings


def compute_gs_card_execution(frame_records, primary_timeline, decoder_timeline, annotation_document, *, source_job_id):
    """Internal deterministic record replay; artifact entry point verifies files.

    This API takes source observations, never caller-supplied computed metrics,
    pass flags or grades. ``input.artifact_files_verified`` remains false here.
    Service routes should call ``build_gs_card_execution_report`` instead.
    """
    document = validate_stage_annotations(annotation_document)
    if not _text(source_job_id):
        raise ValueError("source_job_id_missing")
    catalogue = load_card_rules()
    rules = [rule for rule in catalogue["cards"] if rule["event_code"].startswith("GS")]
    by_track, timeline = _prepare_samples(frame_records, primary_timeline, decoder_timeline,
        video_sha256=_digest(document["video_sha256"]), job_id=source_job_id)
    racket_bindings = _bind_racket_side_observations(document, timeline, by_track)
    by_card, window_records = defaultdict(list), []
    for annotation in document["stages"]:
        expected = [row for row in decoder_timeline if annotation["start_ms"] <=
                    Fraction(row["pts"] * row["time_base_num"] * 1000, row["time_base_den"]) <= annotation["end_ms"]]
        expected_indices = [row["frame_index"] for row in expected]
        expected_set = set(expected_indices)
        bound = [row for row in timeline if row["frame_index"] in expected_set]
        samples = [sample for group in by_track.values() for sample in group
                   if sample.frame_index in expected_set]
        sample = samples[0] if samples else None
        window = {"start_ms": bound[0]["timestamp_ms"] if bound else annotation["start_ms"],
            "end_ms": bound[-1]["timestamp_ms"] if bound else annotation["end_ms"],
            "person_track_id": annotation["person_track_id"], "selection_epoch": sample.selection_epoch if sample else None,
            "camera_reference_epoch": sample.camera_reference_epoch if sample else None,
            "camera_compensated": sample.camera_compensated if sample else False,
            "video_sha256": _digest(document["video_sha256"]), "video_id": document["source_run_id"],
            "event_code": annotation["technique_code"], "stage_code": annotation["stage_code"],
            "stage_id": annotation["stage_code"], "event_id": annotation["annotation_id"],
            "stage_binding": deepcopy(annotation["binding"])}
        racket_binding = deepcopy(racket_bindings.get(annotation["action_id"]))
        if racket_binding is not None:
            # A track number alone does not establish identity across selection
            # or camera resets. Never overwrite the stage's own binding.
            applicable = bool(bound) and [row["frame_index"] for row in bound] == expected_indices and all(
                row["selected_person_track_id"] == annotation["person_track_id"]
                and row["selection_epoch"] == window["selection_epoch"]
                and row["camera_reference_epoch"] == window["camera_reference_epoch"] for row in bound) and all(
                row["selection_epoch"] == window["selection_epoch"]
                and row["camera_reference_epoch"] == window["camera_reference_epoch"]
                for row in racket_binding["source_frame_bindings"])
            racket_binding["stage_continuity_verified"] = applicable
            window["racket_side_binding"] = racket_binding
            if applicable:
                window["racket_side"] = racket_binding["measurement_wrist_side"]
        reasons = []
        if [row["frame_index"] for row in bound] != expected_indices:
            reasons.append("source_decoder_window_inventory_incomplete")
        if not samples or len(bound) < 3:
            reasons.append("stage_source_observations_missing")
        elif any(row["selected_person_track_id"] != annotation["person_track_id"] for row in bound):
            reasons.append("stage_primary_person_binding_unverified")
        elif (len({row["selection_epoch"] for row in bound}) != 1
                or len({row["camera_reference_epoch"] for row in bound}) != 1):
            reasons.append("stage_identity_or_camera_epoch_changed")
        if annotation["start_ms"] < timeline[0]["timestamp_ms"] - .51 or annotation["end_ms"] > timeline[-1]["timestamp_ms"] + .51:
            reasons.append("stage_window_truncated_by_source")
        if not reasons:
            reasons, _ = validate_window(samples, window, bound)
        preload = annotation.get("local_preload_observation")
        if preload is not None:
            evaluation = next(rule["evaluation"] for rule in rules if rule["indicator_id"] == preload["indicator_id"])
            if (evaluation.get("kind") != "manual_review" or evaluation.get("conditions") != []
                    or evaluation.get("required_engine") != "temporal_preload_subwindow_required"
                    or [(row.get("metric"), row.get("unit"), row.get("value")) for row in evaluation.get("auxiliary_observations", [])]
                        != list(PRELOAD_REFERENCES)
                    or any(row.get("operator") != "gte" or row.get("required") is not True
                           for row in evaluation.get("auxiliary_observations", []))):
                raise ValueError("local_preload_auxiliary_contract_mismatch")
        auxiliary = (_local_preload_evidence(preload, annotation, document, decoder_timeline, timeline, by_track, reasons)
                     if preload is not None else None)
        instance_ids = []
        for rule in rules:
            if rule["stage_code"] != annotation["stage_code"]:
                continue
            result = (_unavailable(rule, window, reasons) if reasons else evaluate_card_window(rule, samples, window, bound))
            result.update(version=VERSION, event_code=rule["event_code"], action_id=annotation["action_id"],
                source={key: rule["source"][key] for key in ("document_id", "document_sha256", "table", "rubric_sha256")},
                source_annotation={"annotation_id": annotation["annotation_id"], "document_sha256": _canonical(document),
                    "digest_kind": "canonical_json_sorted_utf8_v1", "binding": deepcopy(annotation["binding"])},
                original_grade=None, source_grade_supported=False)
            if racket_binding is not None:
                result["source_annotation"]["racket_side_observation"] = deepcopy(racket_binding)
            if auxiliary is not None and rule["indicator_id"] == preload["indicator_id"]:
                result["auxiliary_evidence"] = auxiliary
            by_card[rule["indicator_id"]].append(result)
            instance_ids.append(rule["indicator_id"])
        window_records.append({"annotation_id": annotation["annotation_id"], "action_id": annotation["action_id"],
            "stage_code": annotation["stage_code"], "requested_window_ms": {"start_ms": annotation["start_ms"], "end_ms": annotation["end_ms"]},
            "window": window, "source_frames": expected_indices, "observed_source_frame_count": len(bound),
            "source_pts": [{key: row[key] for key in ("frame_index", "pts", "time_base_num", "time_base_den")} for row in expected],
            "reason_codes": reasons, "indicator_ids": instance_ids})
    observed_techniques = {action["technique_code"] for action in document["actions"]}
    rows, instances = [], []
    for rule in rules:
        actual = by_card.get(rule["indicator_id"], [])
        if actual:
            run_status = "proxy_scored" if any(row["status"] == "proxy_scored" for row in actual) else (
                "review_required" if any(row["status"] == "review_required" for row in actual) else "unavailable")
            reasons = sorted({reason for row in actual for reason in row.get("reason_codes", [])})
        elif rule["event_code"] in observed_techniques:
            run_status, reasons = "not_executed", ["gs_stage_annotation_missing"]
        else:
            run_status, reasons = "not_observed", ["gs_technique_not_annotated_in_source_run"]
        rows.append({"indicator_id": rule["indicator_id"], "event_code": rule["event_code"],
            "stage_code": rule["stage_code"], "rule_version": rule["rule_version"],
            "rule_kind": rule["evaluation"]["kind"], "name_zh": rule["effective"]["name_zh"],
            "runtime_engine": VERSION if actual else None, "run_status": run_status, "reason_codes": reasons,
            "original_grade": None, "source_grade_supported": False, "instance_count": len(actual),
            "instance_status_counts": dict(Counter(row["status"] for row in actual)), "instances": actual})
        instances.extend(actual)
    return {"version": VERSION, "rule_version": catalogue["version"], "source_run_id": document["source_run_id"],
        "input": {"source": "frame_observations_primary_selection_decoder_pts_and_explicit_stage_annotations",
            "source_frame_count": len(frame_records), "decoder_frame_count": len(decoder_timeline),
            "annotation_count": len(document["stages"]), "action_count": len(document["actions"]),
            "video_sha256": _digest(document["video_sha256"]), "input_artifact_sha256": deepcopy(document["input_artifact_sha256"]),
            "annotation_document_sha256": _canonical(document), "catalogue_sha256": _canonical(catalogue),
            "annotation_digest_kind": "canonical_json_sorted_utf8_v1",
            "catalogue_digest_kind": "canonical_json_sorted_utf8_v1", "artifact_digest_kind": "raw_file_sha256",
            "saved_grades_trusted": False,
            "artifact_files_verified": False, "decoder_verified_from_original_video": False, "service_eligible": False},
        "cards": rows, "stage_windows": window_records,
        "summary": summarize_card_scores(instances, [rule["indicator_id"] for rule in rules]),
        "observed_type_summary": summarize_card_scores(instances, [rule["indicator_id"] for rule in rules if rule["event_code"] in observed_techniques]),
        "coverage": {"source_gs_card_count": len(rules), "annotated_stage_count": len(document["stages"]),
            "executed_card_count": len(by_card), "computed_card_instance_count": len(instances),
            "run_card_status_counts": dict(Counter(row["run_status"] for row in rows)),
            "instance_status_counts": dict(Counter(row["status"] for row in instances)),
            "original_auto_graded_card_count": 0, "registered_automatic_source_stage_detectors": len(REGISTERED_STAGE_DETECTORS)},
        "overall_score": None, "missing_policy": "exclude_from_grade_keep_in_applicable_card_coverage",
        "limitations": ["manual_stage_annotations_are_estimates_not_expert_ground_truth",
            "source_stage_technical_behavior_not_established_by_2d_proxy",
            "no_automatic_ten_stage_gs_locator_registered",
            "missing_stage_never_inferred_from_three_generic_swing_phases",
            "monocular_geometry_does_not_establish_gaze_force_racket_contact_or_visibility"]}


def _build_artifact_report(run_dir, annotation_document, *, video_path, service_identity):
    document = validate_stage_annotations(annotation_document)
    directory = Path(run_dir).resolve()
    if not directory.is_dir() or directory.name != document["source_run_id"]:
        raise ValueError("source_run_directory_binding_mismatch")
    expected = {name: _digest(value) for name, value in document["input_artifact_sha256"].items()}
    for name in SOURCE_FILES:
        if not (directory / name).is_file() or _sha(directory / name) != expected[name]:
            raise ValueError("source_artifact_digest_mismatch:" + name)
    summary = _read_json(directory / "summary.json")
    if service_identity and summary.get("job_id") != document["source_run_id"]:
        raise ValueError("source_summary_job_binding_mismatch")
    original_path = Path(summary.get("input", {}).get("video_path", ""))
    source = Path(video_path).resolve() if video_path is not None else original_path.resolve()
    if not source.is_file() or _sha(source) != _digest(document["video_sha256"]):
        raise ValueError("source_video_digest_mismatch")
    provenance = summary.get("minimum_scoring_loop", {}).get("provenance", {})
    if (_digest(provenance.get("video_sha256")) != _digest(document["video_sha256"])
            or _digest(provenance.get("frames_sha256")) != expected["frames.jsonl"]):
        raise ValueError("source_summary_provenance_mismatch")
    decoded = _decode_video_timeline(source, _digest(document["video_sha256"]))
    frame_records = _records(directory / "frames.jsonl")
    primary = _records(directory / "primary-player.jsonl", 64 * 1024 * 1024)
    report = compute_gs_card_execution(frame_records, primary, decoded, document, source_job_id=summary.get("job_id"))
    if any(_sha(directory / name) != expected[name] for name in SOURCE_FILES) or _sha(source) != _digest(document["video_sha256"]):
        raise ValueError("source_changed_during_card_execution")
    report["input"].update(artifact_files_verified=True, decoder_verified_from_original_video=True,
        artifact_digest_kind="raw_file_sha256", annotation_version=ANNOTATION_VERSION,
        catalogue_digest_kind="canonical_json_sorted_utf8_v1", service_eligible=service_identity,
        source_job_id=summary["job_id"])
    return report


def build_gs_card_execution_report(run_dir, annotation_document, *, video_path=None):
    """Production: directory, summary, annotations and all frames share one job.

    The service chooses ``run_dir`` and ``video_path``; the annotation document
    cannot override a path. Source files and saved reports are never modified.
    """
    return _build_artifact_report(run_dir, annotation_document, video_path=video_path, service_identity=True)


def build_gs_research_replay_report(run_dir, annotation_document, *, video_path=None):
    """Legacy research aliases only; output is explicitly ineligible for service.

    Original MIT research directories use a short case id while their unchanged
    source frames and summary carry the original ``card-`` job id. This replay
    preserves both identifiers, without relaxing the production entry point.
    """
    return _build_artifact_report(run_dir, annotation_document, video_path=video_path, service_identity=False)
