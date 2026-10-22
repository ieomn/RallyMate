"""Execute remaining FS cards from individually reviewed source windows.

These optional manual estimates do not assert technique, gaze, contact or force.
Existing temporal and FS03/FS04 calculators own their cards. Four remaining
ankle/cycle rules deliberately stay unavailable without a complete cycle;
this adapter never fabricates that binding from a whole event or two endpoints.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

from rallymate_scoring.card_evaluation import evaluate_card_window, summarize_card_scores
from rallymate_scoring.card_kinematics import finite, validate_window
from rallymate_scoring.card_rule_catalog import load_card_rules
from rallymate_scoring.footwork_cycle_card_evidence import _quality_evidence
from rallymate_scoring.temporal_card_evidence import CARD_CONDITIONS
from rallymate_scoring.step_motion_evidence import (
    SUPPORTED_CARDS as STEP_MOTION_CARDS, VERSION as STEP_MOTION_VERSION,
    validate_step_motion_observation,
)
from .gs_card_execution import (
    _canonical, _decode_video_timeline, _digest, _integer, _keys, _prepare_samples,
    _read_json, _records, _sha, _text,
)

VERSION = "fs-card-windows-v1.0.0"
ANNOTATION_VERSION = "fs-card-window-annotations-v1.0.0"
SEMANTICS = "manual_card_window_not_expert_technique_contact_or_force_truth"
SOURCE_FILES = ("frames.jsonl", "primary-player.jsonl", "events.jsonl", "summary.json")
MAX_WINDOWS = 2000


def supported_rules():
    return {row["indicator_id"]: row for row in load_card_rules()["cards"]
        if row["event_code"].startswith("FS") and row["event_code"] not in {"FS03", "FS04"}
        and row["indicator_id"] not in CARD_CONDITIONS}


def validate_fs_card_annotations(document):
    _keys(document, ("version", "source_run_id", "video_sha256", "input_artifact_sha256", "windows"))
    if document["version"] != ANNOTATION_VERSION or not _text(document["source_run_id"]):
        raise ValueError("fs_window_annotation_version_or_run_invalid")
    _digest(document["video_sha256"])
    _keys(document["input_artifact_sha256"], SOURCE_FILES)
    for value in document["input_artifact_sha256"].values():
        _digest(value)
    if not isinstance(document["windows"], list) or len(document["windows"]) > MAX_WINDOWS:
        raise ValueError("fs_window_annotation_inventory_invalid")
    rules, seen, accepted = supported_rules(), set(), []
    for row in document["windows"]:
        required_keys = ("annotation_id", "indicator_id", "person_track_id", "start_source_frame", "end_source_frame",
            "start_ms", "end_ms", "binding")
        _keys(row, required_keys, ("step_motion_observation",))
        if not _text(row["annotation_id"]) or row["annotation_id"] in seen:
            raise ValueError("fs_window_duplicate_or_invalid_annotation_id")
        seen.add(row["annotation_id"])
        if not _text(row["indicator_id"]) or row["indicator_id"] not in rules:
            raise ValueError("fs_window_card_not_owned_by_adapter")
        if (not all(_integer(row[key]) for key in ("person_track_id", "start_source_frame", "end_source_frame"))
                or row["start_source_frame"] >= row["end_source_frame"]
                or not finite(row["start_ms"]) or not finite(row["end_ms"])
                or not 0 <= row["start_ms"] < row["end_ms"]):
            raise ValueError("fs_window_annotation_identity_or_bounds_invalid")
        binding = row["binding"]
        _keys(binding, ("method", "version", "source", "uncertainty_ms", "semantics"))
        if (binding["method"] != "manual_anchor" or binding["semantics"] != SEMANTICS
                or not _text(binding["version"]) or not _text(binding["source"])
                or not finite(binding["uncertainty_ms"]) or binding["uncertainty_ms"] < 0):
            raise ValueError("fs_window_manual_binding_invalid")
        if "step_motion_observation" in row:
            if row["indicator_id"] not in STEP_MOTION_CARDS:
                raise ValueError("step_motion_card_not_supported")
            validate_step_motion_observation(row["step_motion_observation"],
                first_frame=row["start_source_frame"], last_frame=row["end_source_frame"])
        for previous in accepted:
            if (row["person_track_id"] == previous["person_track_id"]
                    and row["indicator_id"].split("-")[0] != previous["indicator_id"].split("-")[0]
                    and max(row["start_source_frame"], previous["start_source_frame"])
                    < min(row["end_source_frame"], previous["end_source_frame"])):
                raise ValueError("fs_window_overlapping_technique_labels")
            if (row["person_track_id"] == previous["person_track_id"]
                    and row["indicator_id"] == previous["indicator_id"]
                    and max(row["start_source_frame"], previous["start_source_frame"])
                    <= min(row["end_source_frame"], previous["end_source_frame"])):
                raise ValueError("fs_window_duplicate_card_source_interval")
            if ({row["indicator_id"], previous["indicator_id"]} == {"FS08-M02", "FS08-M03"}
                    and max(row["start_source_frame"], previous["start_source_frame"])
                    <= min(row["end_source_frame"], previous["end_source_frame"])):
                # Two metrics of one crossing step must not silently choose
                # different subjects, endpoints, or observed moving feet.
                if (any(row[key] != previous[key] for key in ("person_track_id", "start_source_frame", "end_source_frame", "start_ms", "end_ms"))
                        or (row.get("step_motion_observation") or {}).get("moving_foot")
                        != (previous.get("step_motion_observation") or {}).get("moving_foot")):
                    raise ValueError("step_motion_shared_cycle_binding_conflict")
        accepted.append(row)
    return deepcopy(document)


def _quality_for_window(events, start, end, person, required, source_run_id, frame_times):
    """Union original event warnings; manual inputs cannot clear any warning."""
    seen, evidence, reasons, covered = {}, [], set(), set()
    for event in events:
        if (not isinstance(event, Mapping) or not _text(event.get("event_id"))
                or event.get("video_id") != source_run_id or not _integer(event.get("person_track_id"))
                or not finite(event.get("start_ms")) or not finite(event.get("end_ms"))
                or event["start_ms"] > event["end_ms"]):
            raise ValueError("fs_window_source_event_invalid")
        key = (event.get("video_id"), event.get("person_track_id"), event["event_id"])
        if key in seen:
            if seen[key] != event:
                raise ValueError("fs_window_conflicting_source_event")
            continue
        seen[key] = event
        if event["start_ms"] > end or event["end_ms"] < start:
            continue
        diagnostics = event.get("track_diagnostics")
        # Event person IDs are virtual primary IDs in the detector pipeline;
        # annotations bind the raw selected track. Equal integers are not proof
        # that those two identity namespaces name the same observed player.
        tracks = diagnostics.get("source_track_ids") if isinstance(diagnostics, Mapping) else None
        valid_tracks = isinstance(tracks, list) and len(tracks) == 1 and _integer(tracks[0])
        belongs = valid_tracks and tracks == [person]
        if not valid_tracks:
            reasons.add("fs_window_original_quality_identity_unverified")
        if belongs and (diagnostics.get("primary_identity_ambiguous") is not False
                or type(diagnostics.get("source_track_switch_candidate_count")) is not int
                or diagnostics["source_track_switch_candidate_count"] != 0):
            reasons.add("fs_window_original_quality_identity_unverified")
        flags = event.get("quality_flags")
        global_identity_warning = isinstance(flags, list) and any(isinstance(flag, str) and
            ("identity_ambiguous" in flag or "id_switch" in flag or "track_switch" in flag) for flag in flags)
        if isinstance(diagnostics, Mapping) and (diagnostics.get("primary_identity_ambiguous") is True
                or any(finite(diagnostics.get(key)) and diagnostics[key] > 0
                    for key in ("source_track_switch_candidate_count", "confirmed_id_switch_count"))):
            global_identity_warning = True
        if global_identity_warning:
            reasons.add("fs_window_original_identity_requires_review")
        raw_binding = {"source_event_id": event["event_id"],
            "source_start_ms": event["start_ms"], "source_end_ms": event["end_ms"],
            "source_track_ids": deepcopy(tracks),
            "source_track_diagnostics": deepcopy(diagnostics),
            "primary_identity_ambiguous": deepcopy(diagnostics.get("primary_identity_ambiguous")) if isinstance(diagnostics, Mapping) else None,
            "source_track_switch_candidate_count": deepcopy(diagnostics.get("source_track_switch_candidate_count")) if isinstance(diagnostics, Mapping) else None,
            "confirmed_id_switch_count": deepcopy(diagnostics.get("confirmed_id_switch_count")) if isinstance(diagnostics, Mapping) else None,
            "global_identity_warning": global_identity_warning}
        if not belongs:
            evidence.append({**raw_binding, "applies_to_selected_person": False,
                "raw_input_quality_flags": deepcopy(flags)})
            continue
        if not isinstance(flags, list) or not isinstance(diagnostics, Mapping):
            reasons.add("fs_window_original_quality_unverified")
        quality, reason = _quality_evidence(event, required)
        if reason:
            reasons.add("fs_window_original_quality_requires_review")
        if isinstance(flags, list) and all(isinstance(flag, str) for flag in flags) and isinstance(diagnostics, Mapping):
            covered.update(index for index, time in enumerate(frame_times) if event["start_ms"] <= time <= event["end_ms"])
        evidence.append({**raw_binding, "applies_to_selected_person": True, **quality})
    if len(covered) != len(frame_times):
        reasons.add("fs_window_original_quality_coverage_incomplete")
    return evidence, sorted(reasons)


def compute_fs_card_windows(records, primary, source_events, decoder_timeline, annotation_document, *, source_job_id):
    """Raw-record replay. Only the artifact entry point may claim media checks."""
    document = validate_fs_card_annotations(annotation_document)
    if source_job_id != document["source_run_id"]:
        raise ValueError("fs_window_source_run_binding_mismatch")
    if any(not isinstance(row, Mapping) or any(key in row and row[key] != source_job_id
        for key in ("job_id", "video_id")) for row in primary):
        raise ValueError("fs_window_primary_run_binding_mismatch")
    video_sha = _digest(document["video_sha256"])
    by_track, derived = _prepare_samples(records, primary, decoder_timeline, video_sha256=video_sha, job_id=source_job_id)
    decoded = {row["frame_index"]: row for row in decoder_timeline}
    rules, by_card, windows = supported_rules(), defaultdict(list), []
    for annotation in document["windows"]:
        first, last = annotation["start_source_frame"], annotation["end_source_frame"]
        if first not in decoded or last not in decoded:
            raise ValueError("fs_window_source_boundary_missing")
        indices = list(range(first, last + 1))
        if any(index not in decoded for index in indices):
            raise ValueError("fs_window_decoder_inventory_incomplete")
        for index, edge in ((first, "start_ms"), (last, "end_ms")):
            row = decoded[index]
            exact = Fraction(row["pts"] * row["time_base_num"] * 1000, row["time_base_den"])
            if abs(Fraction(str(annotation[edge])) - exact) > Fraction(51, 100):
                raise ValueError("fs_window_annotation_frame_time_mismatch")
        selected = set(indices)
        timeline = [row for row in derived if row["frame_index"] in selected]
        if [row["frame_index"] for row in timeline] != indices:
            raise ValueError("fs_window_original_frame_inventory_incomplete")
        samples = [sample for group in by_track.values() for sample in group if sample.frame_index in selected]
        rule = rules[annotation["indicator_id"]]
        sample = samples[0] if samples else None
        window = {"start_ms": timeline[0]["timestamp_ms"], "end_ms": timeline[-1]["timestamp_ms"],
            "person_track_id": annotation["person_track_id"], "selection_epoch": sample.selection_epoch if sample else None,
            "camera_reference_epoch": sample.camera_reference_epoch if sample else None,
            "camera_compensated": sample.camera_compensated if sample else False,
            "video_sha256": video_sha, "video_id": source_job_id, "event_id": annotation["annotation_id"],
            "event_code": rule["event_code"], "stage_code": rule["indicator_id"],
            "stage_binding": deepcopy(annotation["binding"])}
        if "step_motion_observation" in annotation:
            window["step_motion_observation"] = deepcopy(annotation["step_motion_observation"])
        reasons = []
        if any(row["selected_person_track_id"] != annotation["person_track_id"] for row in timeline):
            reasons.append("fs_window_primary_person_binding_unverified")
        if len({row["selection_epoch"] for row in timeline}) != 1 or len({row["camera_reference_epoch"] for row in timeline}) != 1:
            reasons.append("fs_window_identity_or_camera_epoch_changed")
        validation, _ = validate_window(samples, window, timeline)
        reasons.extend(validation)
        quality, quality_reasons = _quality_for_window(source_events, window["start_ms"], window["end_ms"],
            annotation["person_track_id"], rule["required_joint_names"], source_job_id,
            [row["timestamp_ms"] for row in timeline])
        result = evaluate_card_window(rule, samples, window, timeline)
        if reasons or quality_reasons:
            result.update(status="unavailable" if reasons or result["status"] == "unavailable" else "review_required", score=None, proxy_grade=None,
                reason_codes=sorted(set(result["reason_codes"] + reasons + quality_reasons)))
            for condition in result["conditions"]:
                condition["passed"] = None
            result.pop("condition_completion_fraction", None)
        source_annotation = {"annotation_id": annotation["annotation_id"], "indicator_id": rule["indicator_id"],
            "document_version": ANNOTATION_VERSION, "document_sha256": _canonical(document),
            "digest_kind": "canonical_json_sorted_utf8_v1", "binding": deepcopy(annotation["binding"]),
            "start_source_frame": first, "end_source_frame": last,
            "requested_window_ms": {key: annotation[key] for key in ("start_ms", "end_ms")},
            "video_sha256": video_sha, "person_track_id": annotation["person_track_id"]}
        if "step_motion_observation" in annotation:
            source_annotation["step_motion_observation"] = deepcopy(annotation["step_motion_observation"])
        result.update(version=VERSION, event_code=rule["event_code"], original_grade=None, source_grade_supported=False,
            source={key: rule["source"][key] for key in ("document_id", "document_sha256", "table", "rubric_sha256")},
            source_window_annotation=source_annotation,
            source_window_evidence={"source_frames": indices, "source_pts": [deepcopy(decoded[index]) for index in indices],
                "source_frame_bindings": deepcopy(timeline),
                "original_event_quality": quality, "quality_observations_present": bool(quality),
                "interpolation_used": False, "step_cycle_bound": False,
                "source_behavior_or_contact_verified": False})
        if "step_motion_observation" in annotation:
            step_evidence = next((condition["evidence"].get("step_motion_evidence") for condition in result["conditions"]
                if condition["evidence"].get("step_motion_evidence") is not None), None)
            result["source_window_evidence"].update(step_motion_version=STEP_MOTION_VERSION,
                step_motion=deepcopy(step_evidence),
                step_cycle_bound=bool(step_evidence and step_evidence["complete_visual_motion_cycle"]))
        by_card[rule["indicator_id"]].append(result)
        windows.append({**deepcopy(source_annotation), "window": window, "source_frames": indices})
    cards, instances = [], []
    for identifier, rule in rules.items():
        actual = by_card[identifier]
        status = ("proxy_scored" if any(row["status"] == "proxy_scored" for row in actual) else
            "review_required" if any(row["status"] == "review_required" for row in actual) else
            "unavailable" if actual else "not_executed")
        cards.append({"indicator_id": identifier, "event_code": rule["event_code"], "rule_version": rule["rule_version"],
            "rule_kind": rule["evaluation"]["kind"], "name_zh": rule["effective"]["name_zh"],
            "runtime_engine": VERSION if actual else None, "run_status": status,
            "reason_codes": sorted({reason for row in actual for reason in row["reason_codes"]}) if actual else ["fs_card_manual_window_missing"],
            "original_grade": None, "source_grade_supported": False, "instance_count": len(actual),
            "instance_status_counts": dict(Counter(row["status"] for row in actual)), "instances": actual})
        instances.extend(actual)
    catalogue = load_card_rules()
    return {"version": VERSION, "rule_version": catalogue["version"], "source_run_id": source_job_id,
        "cards": cards, "fs_card_windows": windows, "summary": summarize_card_scores(instances, list(rules)),
        "coverage": {"adapter_card_count": len(rules), "annotated_window_count": len(windows),
            "computed_card_instance_count": len(instances), "executed_card_count": sum(bool(row["instances"]) for row in cards),
            "instance_status_counts": dict(Counter(row["status"] for row in instances)), "original_auto_graded_card_count": 0},
        "input": {"video_sha256": video_sha, "source_frame_count": len(records), "decoder_frame_count": len(decoder_timeline),
            "input_artifact_sha256": deepcopy(document["input_artifact_sha256"]),
            "annotation_document_sha256": _canonical(document), "annotation_version": ANNOTATION_VERSION,
            "catalogue_sha256": _canonical(catalogue), "catalogue_digest_kind": "canonical_json_sorted_utf8_v1",
            "annotation_digest_kind": "canonical_json_sorted_utf8_v1", "artifact_digest_kind": "raw_file_sha256",
            "artifact_files_verified": False, "decoder_verified_from_original_video": False, "service_eligible": False,
            "saved_grades_trusted": False}, "overall_score": None,
        "limitations": ["individual_manual_card_windows_are_estimates_not_technical_truth",
            "no_automatic_fs_phase_or_technique_locator_added", "complete_step_cycle_never_inferred_from_manual_window",
            "manual_observable_cards_remain_ungraded", "engineering_proxy_thresholds_uncalibrated"]}


def build_fs_card_window_report(run_dir, annotation_document, *, video_path=None):
    document = validate_fs_card_annotations(annotation_document)
    directory = Path(run_dir).resolve()
    if not directory.is_dir() or directory.name != document["source_run_id"]:
        raise ValueError("fs_window_source_directory_binding_mismatch")
    expected = {name: _digest(value) for name, value in document["input_artifact_sha256"].items()}
    if any(not (directory / name).is_file() or _sha(directory / name) != expected[name] for name in SOURCE_FILES):
        raise ValueError("fs_window_source_artifact_digest_mismatch")
    summary = _read_json(directory / "summary.json")
    if summary.get("job_id") != document["source_run_id"]:
        raise ValueError("fs_window_summary_job_mismatch")
    original = Path(summary.get("input", {}).get("video_path", ""))
    source = Path(video_path).resolve() if video_path is not None else original.resolve()
    if not source.is_file() or _sha(source) != _digest(document["video_sha256"]):
        raise ValueError("fs_window_source_video_digest_mismatch")
    provenance = summary.get("minimum_scoring_loop", {}).get("provenance", {})
    if (_digest(provenance.get("video_sha256")) != _digest(document["video_sha256"])
            or _digest(provenance.get("frames_sha256")) != expected["frames.jsonl"]):
        raise ValueError("fs_window_summary_provenance_mismatch")
    decoded = _decode_video_timeline(source, _digest(document["video_sha256"]))
    report = compute_fs_card_windows(_records(directory / "frames.jsonl"),
        _records(directory / "primary-player.jsonl", 64 * 1024 * 1024),
        _records(directory / "events.jsonl", 64 * 1024 * 1024), decoded, document, source_job_id=summary["job_id"])
    if any(_sha(directory / name) != expected[name] for name in SOURCE_FILES) or _sha(source) != _digest(document["video_sha256"]):
        raise ValueError("fs_window_source_changed_during_execution")
    report["input"].update(artifact_files_verified=True, decoder_verified_from_original_video=True, service_eligible=True)
    return report
