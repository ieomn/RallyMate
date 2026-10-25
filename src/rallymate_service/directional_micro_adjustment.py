"""Two-card application scoring from source-bound visible image references.

This optional adapter preserves the original FS05 rubric and adds a separately
versioned, uncalibrated application rule. It never imports saved pass/grade data.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

from rallymate_scoring.card_rule_catalog import load_card_rules
from rallymate_scoring.card_scoring_policy import apply_scoring_policy, apply_report_scoring_policy, GRADE_POINTS
from rallymate_scoring.direction_reference_observables import (
    VERSION as MEASUREMENT_VERSION, REQUIRED_JOINTS, compute_direction_reference_observables,
    validate_direction_reference,
)
from rallymate_scoring.step_motion_evidence import validate_step_motion_observation
from .fs_card_windows import (
    ANNOTATION_VERSION as BASE_ANNOTATION_VERSION, SOURCE_FILES, compute_fs_card_windows,
    validate_fs_card_annotations, _quality_for_window,
)
from .gs_card_execution import _canonical, _decode_video_timeline, _digest, _prepare_samples, _read_json, _records, _sha

VERSION = "directional-micro-adjustment-execution-v1.0.0"
ANNOTATION_VERSION = "directional-micro-adjustment-annotations-v1.0.0"
RULE_VERSION = "image-projected-small-adjustment-v1.0.0"
SUPPORTED_CARDS = {"FS05-M03": "longitudinal_projection", "FS05-M04": "transverse_projection"}
CONDITIONS = (
    {"key": "approach", "metric": "hip_axis_target_error_reduction_torso", "operator": "gte", "threshold": .03, "unit": "torso"},
    {"key": "small_step", "metric": "both_ankles_max_path_length_torso", "operator": "between", "threshold": [.03, .5], "unit": "torso"},
    {"key": "axis_alignment", "metric": "hip_axis_alignment_fraction", "operator": "gte", "threshold": .7, "unit": "fraction"},
)


def validate_micro_adjustment_annotations(document):
    if not isinstance(document, dict) or document.get("version") != ANNOTATION_VERSION or not isinstance(document.get("windows"), list):
        raise ValueError("micro_adjustment_annotation_version_invalid")
    base = deepcopy(document)
    base["version"] = BASE_ANNOTATION_VERSION
    for row in base["windows"]:
        if not isinstance(row, dict) or row.get("indicator_id") not in SUPPORTED_CARDS:
            raise ValueError("micro_adjustment_card_not_supported")
        reference = row.pop("direction_reference", None)
        observation = row.pop("step_motion_observation", None)
        # Missing observations remain explicit unscored instances; malformed
        # supplied observations fail the document before any calculation.
        if reference is not None:
            validate_direction_reference(reference)
        if observation is not None:
            validate_step_motion_observation(observation, first_frame=row.get("start_source_frame", 0), last_frame=row.get("end_source_frame", 0))
    validate_fs_card_annotations(base)
    return deepcopy(document), base


def compute_directional_micro_adjustment(records, primary, source_events, decoder_timeline, annotation_document, *, source_job_id):
    """Callable raw-record service entry; no media verification is claimed here."""
    document, base = validate_micro_adjustment_annotations(annotation_document)
    report = compute_fs_card_windows(records, primary, source_events, decoder_timeline, base, source_job_id=source_job_id)
    samples_by_track, _ = _prepare_samples(records, primary, decoder_timeline,
        video_sha256=_digest(document["video_sha256"]), job_id=source_job_id)
    records_by_frame = {record["frame"]["index"]: record for record in records}
    annotations = {row["annotation_id"]: row for row in document["windows"]}
    cards = [row for row in report["cards"] if row["indicator_id"] in SUPPORTED_CARDS]
    for card in cards:
        for result in card["instances"]:
            annotation = annotations[result["event_id"]]
            window = result["window"]
            window["step_motion_observation"] = deepcopy(annotation.get("step_motion_observation"))
            timeline = result["source_window_evidence"]["source_frame_bindings"]
            frames = {row["frame_index"] for row in timeline}
            samples = [sample for group in samples_by_track.values() for sample in group if sample.frame_index in frames]
            reference = annotation.get("direction_reference")
            anchor = reference.get("source_frame", {}).get("frame_index") if isinstance(reference, dict) else None
            measurements = compute_direction_reference_observables(samples, window, timeline, reference, records_by_frame.get(anchor), source_records=records)
            # Reapply source quality with this rule's actual six-joint union.
            quality, quality_reasons = _quality_for_window(source_events, window["start_ms"], window["end_ms"],
                window["person_track_id"], REQUIRED_JOINTS, source_job_id, [row["timestamp_ms"] for row in timeline])
            reasons = [reason for reason in result["reason_codes"] if reason != "required_observable_not_recoverable_from_pose"
                and not reason.startswith("fs_window_original_")]
            # The new consumer verified both the original source representation
            # and its local full-torso copy, including the original decoder time.
            if measurements["geometry_gate_passed"]:
                reasons = [reason for reason in reasons if reason != "source_time_or_normalization_unverified"]
            reasons.extend(measurements["reason_codes"] + quality_reasons)
            if reference is None or reference.get("axis_role") != SUPPORTED_CARDS[card["indicator_id"]]:
                reasons.append("micro_adjustment_card_axis_missing_or_mismatched")
            if reference is None or reference.get("target_kind") != "visible_ball_center":
                reasons.append("micro_adjustment_visible_target_missing")
            values = measurements["measurements"]
            conditions = []
            for spec in CONDITIONS:
                value = values.get(spec["metric"])
                passed = None if value is None else (spec["threshold"][0] <= value <= spec["threshold"][1]
                    if spec["operator"] == "between" else value <= spec["threshold"] if spec["operator"] == "lte" else value >= spec["threshold"])
                conditions.append({**deepcopy(spec), "value": value, "passed": passed,
                    "evidence": {"measurement_version": MEASUREMENT_VERSION, "source_frames": measurements["source_frames"]}})
            if any(condition["value"] is None for condition in conditions):
                reasons.append("micro_adjustment_required_value_missing")
            result.update(version=VERSION, measurement_version=MEASUREMENT_VERSION,
                application_rule_version=RULE_VERSION, conditions=conditions, measurements=values,
                reason_codes=sorted(set(reasons)),
                quality={"total_frame_count": measurements["total_frame_count"], "valid_frame_count": measurements.get("valid_frame_count", 0),
                    "required_joint_names": list(REQUIRED_JOINTS), "visibility_verified": False,
                    "geometry_gate_passed": measurements["geometry_gate_passed"],
                    "source_quality_gate_passed": not quality_reasons},
                threshold_basis="application_thresholds_uncalibrated_not_source_or_literature_standard",
                limitations=["image_axis_not_metric_court_direction", "visible_ball_position_not_predicted_impact_truth",
                    "distance_reasonableness_not_established", "visual_motion_not_foot_contact_or_technical_step",
                    "original_qualitative_grade_not_established"])
            result["source_window_evidence"].update(original_event_quality=quality,
                directional_micro_adjustment=measurements, step_cycle_bound=measurements["status"] == "measured_directional_motion")
            result["advisory_flags"] = sorted({flag for entry in quality for flag in entry.get("advisory_flags", [])}
                | set(result.get("advisory_flags", [])))
            result["source_window_annotation"].update(document_version=ANNOTATION_VERSION,
                document_sha256=_canonical(document), direction_reference=deepcopy(reference),
                step_motion_observation=deepcopy(annotation.get("step_motion_observation")))
            if not reasons:
                fraction = sum(condition["passed"] for condition in conditions)/len(conditions)
                grade = "A" if fraction == 1 else "B" if fraction >= .75 else "C" if fraction >= .5 else "D" if fraction > 0 else "E"
                # The source-window template was already finalized unavailable;
                # these freshly computed application conditions replace it.
                result.pop("scoring_policy_version", None)
                result.update(status="proxy_scored", proxy_grade=grade, score=GRADE_POINTS[grade])
            apply_scoring_policy(result)
        card["runtime_engine"] = VERSION if card["instances"] else None
        card["application_rule_version"] = RULE_VERSION
        card["reason_codes"] = sorted({reason for instance in card["instances"] for reason in instance["reason_codes"]}) if card["instances"] else ["micro_adjustment_source_window_missing"]
    instances = [instance for card in cards for instance in card["instances"]]
    report.update(version=VERSION, cards=cards,
        coverage={"adapter_card_count": 2, "annotated_window_count": len(annotations), "computed_card_instance_count": len(instances),
            "executed_card_count": sum(bool(card["instances"]) for card in cards),
            "instance_status_counts": dict(Counter(row["status"] for row in instances)), "original_auto_graded_card_count": 0})
    report["fs_card_windows"] = [{**deepcopy(instance["source_window_annotation"]), "window": deepcopy(instance["window"]),
        "source_frames": list(instance["source_window_evidence"]["source_frames"])} for instance in instances]
    report["input"].update(annotation_document_sha256=_canonical(document), annotation_version=ANNOTATION_VERSION,
        application_rule_version=RULE_VERSION, catalogue_sha256=_canonical(load_card_rules()))
    report["limitations"] = ["manual_image_references_not_metric_court_or_target_truth", "application_thresholds_uncalibrated", "original_qualitative_grade_not_established"]
    return apply_report_scoring_policy(report)


def build_directional_micro_adjustment_report(run_dir, annotation_document, *, video_path=None):
    """Original-media service entry with unchanged artifact/decoder checks."""
    document, _ = validate_micro_adjustment_annotations(annotation_document)
    directory = Path(run_dir).resolve()
    if not directory.is_dir() or directory.name != document["source_run_id"]:
        raise ValueError("micro_adjustment_source_directory_mismatch")
    expected = {name: _digest(value) for name, value in document["input_artifact_sha256"].items()}
    if any(not (directory/name).is_file() or _sha(directory/name) != expected[name] for name in SOURCE_FILES):
        raise ValueError("micro_adjustment_source_artifact_digest_mismatch")
    summary = _read_json(directory/"summary.json")
    if summary.get("job_id") != document["source_run_id"]:
        raise ValueError("micro_adjustment_summary_job_mismatch")
    original = Path(summary.get("input", {}).get("video_path", ""))
    source = Path(video_path).resolve() if video_path is not None else original.resolve()
    digest = _digest(document["video_sha256"])
    if not source.is_file() or _sha(source) != digest:
        raise ValueError("micro_adjustment_source_video_digest_mismatch")
    provenance = summary.get("minimum_scoring_loop", {}).get("provenance", {})
    if _digest(provenance.get("video_sha256")) != digest or _digest(provenance.get("frames_sha256")) != expected["frames.jsonl"]:
        raise ValueError("micro_adjustment_summary_provenance_mismatch")
    report = compute_directional_micro_adjustment(_records(directory/"frames.jsonl"),
        _records(directory/"primary-player.jsonl", 64*1024*1024), _records(directory/"events.jsonl", 64*1024*1024),
        _decode_video_timeline(source, digest), document, source_job_id=summary["job_id"])
    if any(_sha(directory/name) != expected[name] for name in SOURCE_FILES) or _sha(source) != digest:
        raise ValueError("micro_adjustment_source_changed_during_execution")
    report["input"].update(artifact_files_verified=True, decoder_verified_from_original_video=True, service_eligible=True)
    return report
