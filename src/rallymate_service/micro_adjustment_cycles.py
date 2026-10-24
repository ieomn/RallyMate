"""Independent FS05-M02 application score from complete visual motion episodes.

This consumes an explicitly bound development window, not a verified FS05 phase.
Every ankle motion run is retained. Incomplete runs are observations, not missing
geometry; overlapping left/right motion intervals count as one adjustment episode.
No target, saved pass flag, actual foot contact or tennis family is inferred.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
from pathlib import Path
import statistics

from rallymate_scoring.card_kinematics import (
    _integer, _middle, _reference_point, _source_times, validate_window,
)
from rallymate_scoring.card_scoring_policy import apply_scoring_policy, apply_report_scoring_policy, GRADE_POINTS
from rallymate_scoring.direction_reference_observables import REQUIRED_JOINTS, _local_full_torso
from rallymate_scoring.step_motion_evidence import (
    LOW_SPEED_TORSO_S, MIN_PHASE_FRAMES, MIN_PHASE_MS, _phases, _source_geometry,
)
from .fs_card_windows import (
    ANNOTATION_VERSION as BASE_ANNOTATION_VERSION, SOURCE_FILES, compute_fs_card_windows,
    validate_fs_card_annotations, _quality_for_window,
)
from .gs_card_execution import _canonical, _decode_video_timeline, _digest, _prepare_samples, _read_json, _records, _sha

VERSION = "micro-adjustment-cycles-execution-v1.0.0"
ANNOTATION_VERSION = "micro-adjustment-cycle-annotations-v1.0.0"
RULE_VERSION = "complete-visual-adjustment-episodes-v1.0.0"
CARD = "FS05-M02"
EPISODE_POLICY = "positive_overlap_half_open_motion_intervals_transitive_union_v1"
CONDITIONS = (
    {"key": "continuous_adjustments", "metric": "complete_visual_adjustment_episode_count",
     "operator": "gte", "threshold": 2, "unit": "count"},
    {"key": "small_paths", "metric": "both_ankles_max_path_length_torso",
     "operator": "between", "threshold": [.03, .5], "unit": "torso"},
)


def validate_micro_adjustment_cycle_annotations(document):
    if not isinstance(document, dict) or document.get("version") != ANNOTATION_VERSION:
        raise ValueError("micro_adjustment_cycle_annotation_version_invalid")
    base = deepcopy(document)
    base["version"] = BASE_ANNOTATION_VERSION
    validate_fs_card_annotations(base)
    if any(row["indicator_id"] != CARD or "step_motion_observation" in row for row in base["windows"]):
        raise ValueError("micro_adjustment_cycle_card_or_supplied_motion_invalid")
    return deepcopy(document), base


def _episodes(cycles):
    """Union all motion runs, including incomplete runs; never double count feet.

    Intervals are [first_motion_frame, last_motion_frame), representing adjacent
    frame pairs. A shared endpoint alone is not overlap. An incomplete run may
    connect overlapping runs, but cannot itself make an episode complete.
    """
    episodes = []
    for cycle in sorted(cycles, key=lambda c: (c["motion_start_source_frame"], c["motion_end_source_frame"], c["cycle_id"])):
        start, end = cycle["motion_start_source_frame"], cycle["motion_end_source_frame"]
        if not episodes or start >= episodes[-1]["motion_end_source_frame"]:
            episodes.append({"episode_id": f"episode-{len(episodes)+1}",
                "motion_start_source_frame": start, "motion_end_source_frame": end,
                "cycle_ids": [], "complete_cycle_ids": [], "incomplete_cycle_ids": []})
        episode = episodes[-1]
        episode["motion_end_source_frame"] = max(episode["motion_end_source_frame"], end)
        episode["cycle_ids"].append(cycle["cycle_id"])
        episode["complete_cycle_ids" if cycle["complete_visual_motion_cycle"] else "incomplete_cycle_ids"].append(cycle["cycle_id"])
    for episode in episodes:
        episode["complete_visual_adjustment_episode"] = bool(episode["complete_cycle_ids"])
    return episodes


def measure_micro_adjustment_cycles(samples, window, timeline, source_records):
    """Close original source geometry first, then measure both feet without gaps."""
    canonical, normalization, reasons = _local_full_torso(samples, window, timeline, source_records)
    validation, aligned = validate_window(canonical, window, timeline)
    reasons.extend(validation)
    rows = list(timeline)
    if len(aligned) != len(rows):
        aligned = [None] * len(rows)
    evidence = {"version": RULE_VERSION, "status": "unavailable", "reason_codes": [],
        "required_joint_names": list(REQUIRED_JOINTS), "total_frame_count": len(rows),
        "source_frames": [row["frame_index"] for row in rows], "source_frame_bindings": deepcopy(rows),
        "source_geometry_series": [_source_geometry(sample, row["frame_index"]) for sample, row in zip(aligned, rows)],
        "local_normalization": normalization, "reference_series": [], "feet": {}, "cycles": [], "episodes": [],
        "measurements": {}, "geometry_gate_passed": False, "interpolation_used": False,
        "denominator": "all_decoder_samples_in_bound_window", "source_grade_supported": False,
        "contact_verified": False, "technical_step_or_FS05_phase_verified": False,
        "episode_policy": EPISODE_POLICY, "low_speed_threshold_torso_s": LOW_SPEED_TORSO_S,
        "minimum_phase_duration_ms": MIN_PHASE_MS, "minimum_phase_frame_count": MIN_PHASE_FRAMES}
    if (not _integer(window.get("derived_selection_epoch")) or not _integer(window.get("camera_reference_epoch"))
            or window.get("camera_compensated") is not True
            or any(row.get("selected_person_track_id") != window.get("person_track_id")
                or row.get("selection_epoch") != window.get("selection_epoch")
                or row.get("derived_selection_epoch") != window.get("derived_selection_epoch")
                or row.get("camera_reference_epoch") != window.get("camera_reference_epoch")
                or row.get("video_sha256") != window.get("video_sha256") for row in rows)):
        reasons.append("micro_adjustment_complete_source_ledger_required")
    points = [{joint: _reference_point(sample, joint) for joint in REQUIRED_JOINTS}
              if sample is not None else {} for sample in aligned]
    common = [row["frame_index"] for row, point in zip(rows, points) if all(point.get(j) is not None for j in REQUIRED_JOINTS)]
    evidence.update(valid_source_frames=common, valid_frame_count=len(common))
    if len(rows) < 7 or len(common) != len(rows):
        reasons.append("micro_adjustment_common_geometry_not_continuous")
    if reasons:
        evidence["reason_codes"] = sorted(set(reasons))
        return evidence
    scales = [math.dist(_middle(*(p[j] for j in REQUIRED_JOINTS[:2])),
                        _middle(*(p[j] for j in REQUIRED_JOINTS[2:4]))) for p in points]
    scale = statistics.median(scales)
    if not math.isfinite(scale) or scale <= 0:
        evidence["reason_codes"] = ["micro_adjustment_fixed_torso_scale_unavailable"]
        return evidence
    exact, indices = _source_times(rows), evidence["source_frames"]
    cycles = []
    for side in ("left", "right"):
        path = [p[side+"_ankle"] for p in points]
        # The whole-window single-cycle result is diagnostic only. Multiple or
        # censored runs must not make otherwise complete source data unavailable.
        whole = _phases(path, exact, scale, indices, max(3, math.ceil(len(rows)*.2)))
        evidence["feet"][side] = whole
        for ordinal, phase in enumerate(whole["phases"]):
            if not phase["moving"]:
                continue
            first = whole["phases"][ordinal-1]["first_interval"] if ordinal else phase["first_interval"]
            last = (whole["phases"][ordinal+1]["last_interval"]+1
                    if ordinal+1 < len(whole["phases"]) else phase["last_interval"]+1)
            local = _phases(path[first:last+1], exact[first:last+1], scale, indices[first:last+1],
                max(3, math.ceil((last-first+1)*.2)))
            cycle_reasons = list(local["reason_codes"])
            if ordinal == 0:
                cycle_reasons.append("preceding_low_motion_censored_at_window_start")
            if ordinal == len(whole["phases"])-1:
                cycle_reasons.append("following_low_motion_censored_at_window_end")
            cycles.append({"cycle_id": f"{side}-{ordinal}", "moving_ankle": side,
                "motion_start_source_frame": phase["source_frames"][0],
                "motion_end_source_frame": phase["source_frames"][-1],
                "source_frames": indices[first:last+1], "local_motion_evidence": local,
                "complete_visual_motion_cycle": not cycle_reasons,
                "reason_codes": sorted(set(cycle_reasons))})
    episodes = _episodes(cycles)
    complete = sum(episode["complete_visual_adjustment_episode"] for episode in episodes)
    duration_s = float(exact[-1]-exact[0])/1000
    evidence.update(status="measured_visual_adjustment_episodes", geometry_gate_passed=True,
        fixed_torso_scale=scale, source_torso_scales=scales, duration_s=duration_s,
        reference_series=[{"frame_index": row["frame_index"], "points": {j: list(p[j]) for j in REQUIRED_JOINTS}}
                          for row, p in zip(rows, points)], cycles=cycles, episodes=episodes,
        measurements={"complete_visual_adjustment_episode_count": complete,
            "both_ankles_max_path_length_torso": max(foot["path_length_torso"] for foot in evidence["feet"].values())},
        auxiliary={"complete_visual_ankle_cycle_count": sum(c["complete_visual_motion_cycle"] for c in cycles),
            "incomplete_ankle_motion_run_count": sum(not c["complete_visual_motion_cycle"] for c in cycles),
            "all_motion_episode_count": len(episodes), "observed_complete_episode_frequency_hz": complete/duration_s,
            "frequency_semantics": "complete_observed_episodes_per_bound_window_second_not_actual_contact_cadence"})
    return evidence


def compute_micro_adjustment_cycles(records, primary, source_events, decoder_timeline, annotation_document, *, source_job_id):
    """Raw-record replay only; artifact and original-video checks are separate."""
    document, base = validate_micro_adjustment_cycle_annotations(annotation_document)
    report = compute_fs_card_windows(records, primary, source_events, decoder_timeline, base, source_job_id=source_job_id)
    groups, _ = _prepare_samples(records, primary, decoder_timeline,
        video_sha256=_digest(document["video_sha256"]), job_id=source_job_id)
    cards = [card for card in report["cards"] if card["indicator_id"] == CARD]
    for card in cards:
        for result in card["instances"]:
            window = result["window"]
            timeline = result["source_window_evidence"]["source_frame_bindings"]
            frames = {r["frame_index"] for r in timeline}
            samples = [s for group in groups.values() for s in group if s.frame_index in frames]
            evidence = measure_micro_adjustment_cycles(samples, window, timeline, records)
            quality, quality_reasons = _quality_for_window(source_events, window["start_ms"], window["end_ms"],
                window["person_track_id"], REQUIRED_JOINTS, source_job_id, [r["timestamp_ms"] for r in timeline])
            reasons = [r for r in result["reason_codes"] if r != "required_observable_not_recoverable_from_pose"
                       and not r.startswith("fs_window_original_")]
            if evidence["geometry_gate_passed"]:
                reasons = [r for r in reasons if r != "source_time_or_normalization_unverified"]
            reasons = sorted(set(reasons + evidence["reason_codes"] + quality_reasons))
            conditions = []
            for spec in CONDITIONS:
                value = evidence["measurements"].get(spec["metric"])
                passed = None if value is None else (spec["threshold"][0] <= value <= spec["threshold"][1]
                    if spec["operator"] == "between" else value >= spec["threshold"])
                conditions.append({**deepcopy(spec), "value": value, "passed": passed,
                    "evidence": {"measurement_version": RULE_VERSION, "source_frames": evidence["source_frames"]}})
            if any(c["value"] is None for c in conditions):
                reasons = sorted(set(reasons + ["micro_adjustment_required_value_missing"]))
            result.update(version=VERSION, measurement_version=RULE_VERSION, application_rule_version=RULE_VERSION,
                conditions=conditions, measurements=deepcopy(evidence["measurements"]), reason_codes=reasons,
                threshold_basis="application_thresholds_uncalibrated_not_source_or_literature_standard",
                quality={"total_frame_count": len(timeline), "valid_frame_count": evidence["valid_frame_count"],
                    "required_joint_names": list(REQUIRED_JOINTS), "visibility_verified": False,
                    "geometry_gate_passed": evidence["geometry_gate_passed"], "source_quality_gate_passed": not quality_reasons})
            result["source_window_evidence"].update(original_event_quality=quality, micro_adjustment_cycles=evidence)
            result["source_window_annotation"].update(document_version=ANNOTATION_VERSION, document_sha256=_canonical(document))
            result["advisory_flags"] = sorted(set(result.get("advisory_flags", []))
                | {flag for q in quality for flag in q.get("advisory_flags", [])})
            if not reasons:
                fraction = sum(c["passed"] for c in conditions)/len(conditions)
                grade = "A" if fraction == 1 else "C" if fraction == .5 else "E"
                result.update(status="scored", grade=grade, proxy_grade=grade, score=GRADE_POINTS[grade])
            apply_scoring_policy(result)
        card.update(runtime_engine=VERSION if card["instances"] else None, application_rule_version=RULE_VERSION,
            reason_codes=sorted({r for i in card["instances"] for r in i["reason_codes"]}) if card["instances"] else ["micro_adjustment_source_window_missing"])
    instances = [i for card in cards for i in card["instances"]]
    report.update(version=VERSION, cards=cards,
        coverage={"adapter_card_count": 1, "annotated_window_count": len(document["windows"]),
            "executed_card_count": sum(bool(c["instances"]) for c in cards), "computed_card_instance_count": len(instances),
            "instance_status_counts": dict(Counter(i["status"] for i in instances)), "original_auto_graded_card_count": 0},
        limitations=["fixed_observed_development_window_not_verified_original_FS05_stage", "visual_episode_not_contact_or_tennis_step",
            "frequency_is_auxiliary_not_an_independent_condition", "application_thresholds_uncalibrated", "independent_one_card_not_unified_api_or_frontend"])
    report["fs_card_windows"] = [{**deepcopy(i["source_window_annotation"]), "window": deepcopy(i["window"]),
        "source_frames": list(i["source_window_evidence"]["source_frames"])} for i in instances]
    report["input"].update(annotation_document_sha256=_canonical(document), annotation_version=ANNOTATION_VERSION,
        application_rule_version=RULE_VERSION)
    return apply_report_scoring_policy(report)


def build_micro_adjustment_cycle_report(run_dir, annotation_document, *, video_path=None):
    """Read immutable original artifacts and decode the actual source video."""
    document, _ = validate_micro_adjustment_cycle_annotations(annotation_document)
    directory = Path(run_dir).resolve()
    if not directory.is_dir() or directory.name != document["source_run_id"]:
        raise ValueError("micro_adjustment_cycle_source_directory_mismatch")
    expected = {name: _digest(value) for name, value in document["input_artifact_sha256"].items()}
    if any(not (directory/name).is_file() or _sha(directory/name) != expected[name] for name in SOURCE_FILES):
        raise ValueError("micro_adjustment_cycle_source_artifact_digest_mismatch")
    summary = _read_json(directory/"summary.json")
    if summary.get("job_id") != document["source_run_id"]:
        raise ValueError("micro_adjustment_cycle_summary_job_mismatch")
    source = Path(video_path).resolve() if video_path is not None else Path(summary.get("input", {}).get("video_path", "")).resolve()
    digest = _digest(document["video_sha256"])
    if not source.is_file() or _sha(source) != digest:
        raise ValueError("micro_adjustment_cycle_source_video_digest_mismatch")
    provenance = summary.get("minimum_scoring_loop", {}).get("provenance", {})
    if _digest(provenance.get("video_sha256")) != digest or _digest(provenance.get("frames_sha256")) != expected["frames.jsonl"]:
        raise ValueError("micro_adjustment_cycle_summary_provenance_mismatch")
    report = compute_micro_adjustment_cycles(_records(directory/"frames.jsonl"),
        _records(directory/"primary-player.jsonl", 64*1024*1024), _records(directory/"events.jsonl", 64*1024*1024),
        _decode_video_timeline(source, digest), document, source_job_id=summary["job_id"])
    if any(_sha(directory/name) != expected[name] for name in SOURCE_FILES) or _sha(source) != digest:
        raise ValueError("micro_adjustment_cycle_source_changed_during_execution")
    report["input"].update(artifact_files_verified=True, decoder_verified_from_original_video=True, service_eligible=True)
    return report
