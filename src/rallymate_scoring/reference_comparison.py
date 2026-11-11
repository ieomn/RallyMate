"""Compare source-bound phase geometry with an explicitly selected reference.

The score describes agreement with that reference under an engineering policy.
It neither changes the action-quality score nor estimates playing ability, force,
ball contact, or coaching correctness. Existing reports preserve original PTS in
milliseconds; this consumer validates their internal bindings, not raw video.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import re


VERSION = "action-reference-comparison-v1.0.0"
POLICY_VERSION = "phase-geometry-reference-policy-v1.0.0"
SCORE_SEMANTICS = "engineering_reference_motion_similarity_not_skill_or_action_quality"
PHASE_WEIGHTS = {"M01": .03, "M02": .03, "M03": .03,
                 "M04": .4 / 3, "M05": .4 / 3, "M06": .4 / 3,
                 "M07": .25, "M08": .20, "M09": .03, "M10": .03}
GROUP_WEIGHTS = {"lower_body": .30, "trunk": .30, "upper_body": .40}
METRIC_GROUPS = {
    "lower_body": ("left_knee_flexion_deg", "right_knee_flexion_deg", "stance_width_shoulders"),
    "trunk": ("shoulder_hip_separation_deg",),
    "upper_body": ("left_elbow_flexion_deg", "right_elbow_flexion_deg",
                   "left_shoulder_elevation_deg", "right_shoulder_elevation_deg"),
}
METRIC_WEIGHTS = {metric: GROUP_WEIGHTS[group] / len(metrics)
                  for group, metrics in METRIC_GROUPS.items() for metric in metrics}
REQUIRED_PHASES = ("M04", "M05", "M06", "M07", "M08")
MINIMUM_COVERAGE = .80
MINIMUM_JOINT_COVERAGE = .70
ANGLE_TOLERANCE_DEG = 5.
ANGLE_SCALE_DEG = 45.
STANCE_TOLERANCE_SHOULDERS = .10
STANCE_SCALE_SHOULDERS = 1.
SUPPORTED_PROFILES = frozenset({"baseline_forehand", "baseline_two_hand_backhand", "baseline_one_hand_backhand",
    "backhand_slice", "forehand_slice", "serve", "return_forehand", "return_two_hand_backhand",
    "return_one_hand_backhand", "return_backhand_slice", "return_forehand_slice"})
CLASSIFICATION_METHODS = frozenset({"source_bound_rule_inferred", "source_bound_serve_phases",
                                    "human_visual_review", "agent_visual_review"})
KINEMATICS_VERSION = "body-joint-kinematics-v1"


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _integer(value):
    return type(value) is int and value >= 0


def _identifier(value):
    return isinstance(value, str) and bool(value.strip())


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None


def _digest(value):
    try:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("report_not_finite_json") from error
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _frames(value, code):
    _require(isinstance(value, list) and all(_integer(frame) for frame in value), code)
    _require(all(b > a for a, b in zip(value, value[1:])), code)
    return value


def _unit(metric):
    return "shoulder_width" if metric == "stance_width_shoulders" else "deg"


def policy_snapshot():
    """Return a detached, fully disclosed policy snapshot for this calculation."""
    return {"version": POLICY_VERSION, "origin": "explicit_engineering_policy_not_source_document_numeric_thresholds",
        "coach_calibrated": False, "phase_weights": dict(PHASE_WEIGHTS), "group_weights": dict(GROUP_WEIGHTS),
        "metric_groups": {key: list(value) for key, value in METRIC_GROUPS.items()},
        "metric_weights": dict(METRIC_WEIGHTS), "minimum_coverage": MINIMUM_COVERAGE,
        "required_measured_phases": list(REQUIRED_PHASES), "minimum_joint_coverage": MINIMUM_JOINT_COVERAGE,
        "angle_tolerance_deg": ANGLE_TOLERANCE_DEG, "angle_scale_deg": ANGLE_SCALE_DEG,
        "stance_tolerance_shoulders": STANCE_TOLERANCE_SHOULDERS, "stance_scale_shoulders": STANCE_SCALE_SHOULDERS,
        "statistic": "phase_median", "distance": "clamp((abs(candidate-reference)-tolerance)/scale,0,1)",
        "aggregation": "100_minus_available_weight_normalized_sum_of_weighted_distances",
        "weight_semantics": "phase_weight_times_group_weight_divided_by_group_metric_count",
        "missing_policy": "exclude_from_distance_keep_in_total_weight_coverage",
        "angle_difference": "absolute_reported_angle_difference_no_mirror_or_time_warp",
        "phase_semantics": "overlapping_kinematic_observations_not_physical_contact_or_force",
        "source_clock_validation": "report_preserved_original_decoder_pts_internal_frame_time_consistency",
        "report_sha256_semantics": "sha256_canonical_utf8_json_sorted_keys_no_whitespace",
        "deduction_semantics": "weighted_deduction_uses_available_weight_normalization_raw_weighted_deduction_preserves_policy_mass",
        "evaluation_acceptance": "independent_video_sources_and_both_classifications_visually_reviewed_no_automatic_quality_pass"}


def _metric(value, metric, *, bounds=None, parent_frames=None, denominator=None):
    if value is None:
        return None, []
    _require(isinstance(value, Mapping), "metric_not_object")
    _require(value.get("unit") == _unit(metric), "metric_unit_mismatch")
    _require(value.get("status") in {"measured", "not_measured"}, "metric_status_invalid")
    frames = _frames(value.get("source_frames"), "metric_frames_invalid")
    _require(_integer(value.get("observation_count")) and value["observation_count"] == len(frames), "metric_count_mismatch")
    coverage = value.get("coverage")
    _require(_number(coverage) and 0 <= coverage <= 1, "metric_coverage_invalid")
    if denominator is not None:
        _require(denominator > 0 and len(frames) <= denominator
                 and abs(coverage - len(frames) / denominator) <= 1e-6, "metric_coverage_count_mismatch")
    if bounds is not None:
        _require(all(bounds[0] <= frame <= bounds[1] for frame in frames), "metric_frames_outside_phase")
    if parent_frames is not None:
        _require(set(frames) <= set(parent_frames), "metric_frames_outside_episode_measurement")
    for field in ("median", "min", "max", "p10", "p90", "range", "angular_speed_p90_deg_s"):
        if field in value:
            _require(_number(value[field]), "metric_statistic_nonfinite")
    if value["status"] == "not_measured":
        return None, frames
    _require(bool(frames) and coverage >= MINIMUM_JOINT_COVERAGE and _number(value.get("median")), "measured_metric_evidence_invalid")
    statistics = [value.get(field) for field in ("min", "p10", "median", "p90", "max", "range")]
    _require(all(_number(statistic) for statistic in statistics), "measured_metric_statistics_missing")
    low, p10, median, p90, high, extent = statistics
    _require(low <= p10 <= median <= p90 <= high and extent >= 0
             and abs(extent - (high - low)) <= 1e-4, "metric_statistics_inconsistent")
    _require("angular_speed_p90_deg_s" not in value or value["angular_speed_p90_deg_s"] >= 0,
             "metric_angular_speed_invalid")
    return float(value["median"]), frames


def _source(report, episode_id):
    _require(isinstance(report, Mapping), "report_not_object")
    digest = _digest(report)
    _require(_sha(report.get("source_video_sha256")), "source_video_sha256_invalid")
    _require(_identifier(report.get("source_run_id")), "source_run_id_missing")
    _require(report.get("source_timestamp_semantics") == "original_decoder_pts", "source_clock_semantics_invalid")
    _require(_identifier(episode_id) and isinstance(report.get("episodes"), list), "episode_request_invalid")
    episode_ids = [row.get("episode_id") if isinstance(row, Mapping) else None for row in report["episodes"]]
    _require(all(_identifier(identifier) for identifier in episode_ids) and len(set(episode_ids)) == len(episode_ids), "episode_identities_invalid")
    selected = [row for row in report["episodes"] if row["episode_id"] == episode_id]
    _require(len(selected) == 1, "episode_not_found")
    episode = selected[0]
    start, peak, end = (episode.get(key) for key in ("start_ms", "peak_ms", "end_ms"))
    _require(all(_number(value) for value in (start, peak, end)) and 0 <= start < peak < end, "episode_timeline_invalid")
    fields = ("person_track_id", "source_track_id", "source_selection_epoch", "camera_reference_epoch")
    _require(all(_integer(episode.get(field)) for field in fields), "episode_source_identity_invalid")
    _require(episode["person_track_id"] == episode["source_track_id"], "episode_track_binding_mismatch")
    _require(_integer(episode.get("frame_count")) and episode["frame_count"] >= 3, "episode_frame_count_invalid")
    scope = report.get("subject_scope")
    _require(isinstance(scope, Mapping) and scope.get("cross_subject_aggregation") is False, "subject_scope_invalid")
    subject = episode.get("subject_id")
    _require(_identifier(subject) and subject == scope.get("selected_subject_id"), "episode_subject_mismatch")
    _require(isinstance(scope.get("subjects"), list), "subject_inventory_missing")
    subjects = [row for row in scope["subjects"] if isinstance(row, Mapping) and row.get("subject_id") == subject]
    _require(len(subjects) == 1, "subject_identity_ambiguous")
    selected_subject = subjects[0]
    tracks = selected_subject.get("source_track_ids")
    _require(selected_subject.get("included_in_report") is True and isinstance(tracks, list)
             and all(_integer(track) for track in tracks) and episode["source_track_id"] in tracks, "subject_track_binding_mismatch")
    _require(_number(selected_subject.get("start_ms")) and _number(selected_subject.get("end_ms"))
             and selected_subject["start_ms"] <= start < end <= selected_subject["end_ms"], "episode_outside_subject_timeline")
    arm, roles = episode.get("active_arm"), episode.get("hand_roles")
    _require(arm in {"left", "right"} and isinstance(roles, Mapping)
             and roles.get("measurement_hand") == arm, "measurement_hand_binding_invalid")
    pattern = episode.get("motion_pattern")
    _require(isinstance(pattern, Mapping), "classification_missing")
    profile, provenance = pattern.get("profile_id"), pattern.get("classification_provenance")
    _require(isinstance(provenance, Mapping), "classification_provenance_missing")
    verified = profile in SUPPORTED_PROFILES and pattern.get("classification_status") == "verified"
    if verified:
        method = provenance.get("method")
        _require(method in CLASSIFICATION_METHODS, "classification_provenance_invalid")
        _require(method not in {"human_visual_review", "agent_visual_review"} or _identifier(provenance.get("reviewer")), "classification_reviewer_missing")
        _require(method != "source_bound_serve_phases" or profile == "serve", "classification_method_profile_mismatch")
        family = "serve" if profile == "serve" else "return" if profile.startswith("return_") else "baseline"
        _require(episode.get("family") == family, "classification_family_mismatch")
    kin = episode.get("joint_kinematics")
    _require(isinstance(kin, Mapping) and kin.get("version") == KINEMATICS_VERSION, "kinematics_version_unsupported")
    _require(kin.get("coordinate_system") == "body_relative_native_3d"
             and kin.get("measurement_semantics") == "monocular_model_estimate", "kinematics_semantics_mismatch")
    _require(_number(kin.get("minimum_joint_coverage")) and MINIMUM_JOINT_COVERAGE <= kin["minimum_joint_coverage"] <= 1,
             "kinematics_coverage_contract_invalid")
    whole = kin.get("metrics")
    _require(isinstance(whole, Mapping), "episode_joint_metrics_missing")
    parent_frames = {metric: _metric(whole.get(metric), metric, denominator=episode["frame_count"])[1] for metric in METRIC_WEIGHTS}
    frame_union = sorted({frame for frames in parent_frames.values() for frame in frames})
    _require(bool(frame_union) and len(frame_union) == episode["frame_count"], "episode_joint_frame_binding_invalid")
    observed_frames = set(frame_union)
    clock = {}
    def bind(frame, time):
        _require(_integer(frame) and frame in observed_frames and _number(time)
                 and start <= time <= end, "phase_source_binding_invalid")
        _require(frame not in clock or clock[frame] == time, "source_frame_clock_conflict")
        clock[frame] = time
    bind(frame_union[0], start)
    bind(frame_union[-1], end)
    raw_phases = kin.get("phases")
    _require(isinstance(raw_phases, list), "phases_missing")
    phases = {}
    for phase in raw_phases:
        _require(isinstance(phase, Mapping), "phase_not_object")
        identifier = phase.get("phase_id")
        _require(identifier in PHASE_WEIGHTS and identifier not in phases, "phase_identity_invalid_or_duplicate")
        times = [phase.get(key) for key in ("start_ms", "anchor_ms", "end_ms")]
        frames = [phase.get(key) for key in ("source_start_frame", "source_frame", "source_end_frame")]
        _require(all(_number(time) for time in times) and start <= times[0] <= times[1] <= times[2] <= end,
                 "phase_timeline_invalid")
        _require(all(_integer(frame) for frame in frames) and frames[0] <= frames[1] <= frames[2], "phase_frame_order_invalid")
        for frame, time in zip(frames, times):
            bind(frame, time)
        _require(identifier != "M08" or times[1] == peak, "primary_phase_peak_mismatch")
        _require(phase.get("status") == "kinematic_candidate"
                 and phase.get("physical_event_verified") is False
                 and phase.get("window_semantics") == "overlapping_kinematic_observation_not_strict_state_transition", "phase_semantics_invalid")
        metrics = phase.get("metrics")
        _require(isinstance(metrics, Mapping), "phase_metrics_missing")
        measured = {}
        denominator = sum(frames[0] <= frame <= frames[2] for frame in frame_union)
        for metric in METRIC_WEIGHTS:
            value, source_frames = _metric(metrics.get(metric), metric, bounds=(frames[0], frames[2]),
                                           parent_frames=parent_frames[metric], denominator=denominator)
            measured[metric] = {"value": value, "source_frames": list(source_frames)}
        phases[identifier] = {"phase_id": identifier, "start_ms": times[0], "anchor_ms": times[1], "end_ms": times[2],
                              "source_start_frame": frames[0], "source_frame": frames[1], "source_end_frame": frames[2],
                              "measurements": measured}
    ordered = sorted(clock.items())
    _require(all(b[1] > a[1] for a, b in zip(ordered, ordered[1:])), "source_clock_non_increasing")
    evidence = {"report_sha256": digest, "source_video_sha256": report["source_video_sha256"].lower(),
        "source_run_id": report["source_run_id"], "episode_id": episode_id, "subject_id": subject,
        **{field: episode[field] for field in fields}, "active_arm": arm,
        "start_ms": start, "peak_ms": peak, "end_ms": end, "source_frames": frame_union,
        "classification_provenance": deepcopy(dict(provenance)), "profile_id": profile,
        "classification_status": pattern.get("classification_status"),
        "source_timestamp_semantics": report["source_timestamp_semantics"],
        "source_clock_evidence": [{"frame_index": frame, "timestamp_ms": time} for frame, time in ordered],
        "phases": [{**{key: phase[key] for key in ("phase_id", "start_ms", "anchor_ms", "end_ms",
                    "source_start_frame", "source_frame", "source_end_frame")},
                    "physical_event_verified": False,
                    "window_semantics": "overlapping_kinematic_observation_not_strict_state_transition"}
                   for phase in phases.values()],
        "source_binding_verification": "report_internal_consistency_not_independent_decoder_replay"}
    return evidence, phases, verified


def compare_action_to_reference(candidate_report, candidate_episode_id, reference_report, reference_episode_id):
    """Compare one same-technique, same-hand action; never mutate either report.

    Malformed geometry, units or source bindings raise ``ValueError(code)``.
    Legitimate incompatible classifications/hands or insufficient coverage return
    an unavailable result. Self comparisons retain the arithmetic diagnostic but
    are explicitly ineligible as independent evaluation evidence.
    """
    candidate, first, candidate_verified = _source(candidate_report, candidate_episode_id)
    reference, second, reference_verified = _source(reference_report, reference_episode_id)
    same_source = candidate["source_video_sha256"] == reference["source_video_sha256"]
    overlap = same_source and (bool(set(candidate["source_frames"]) & set(reference["source_frames"]))
        or max(candidate["start_ms"], reference["start_ms"]) <= min(candidate["end_ms"], reference["end_ms"]))
    scope = "self_reference" if overlap else "same_source_holdout" if same_source else "independent_video_source"
    snapshot = policy_snapshot()
    output = {"version": VERSION, "status": "unavailable", "reference_similarity_score": None,
        "coverage": 0., "comparison_scope": scope, "is_independent_comparison": not same_source,
        "evaluation_eligible": False, "score_semantics": SCORE_SEMANTICS,
        "coverage_semantics": "weighted_paired_phase_joint_measurements_not_physical_event_verification",
        "candidate": candidate, "reference": reference, "phases": [], "reason_codes": [],
        "policy": snapshot, "policy_sha256": _digest(snapshot), "original_scores_modified": False}
    if not candidate_verified or not reference_verified:
        output["reason_codes"] = ["technique_classification_unverified_or_unsupported"]
        return output
    if candidate["profile_id"] != reference["profile_id"]:
        output["reason_codes"] = ["technique_profile_mismatch"]
        return output
    if candidate["active_arm"] != reference["active_arm"]:
        output["reason_codes"] = ["measurement_hand_mismatch"]
        return output
    weighted_distances, available_weights = [], []
    available_phases = set()
    for identifier, phase_weight in PHASE_WEIGHTS.items():
        a, b = first.get(identifier), second.get(identifier)
        items, phase_distances, phase_available = [], [], []
        for metric, metric_weight in METRIC_WEIGHTS.items():
            left = a["measurements"][metric] if a else {"value": None, "source_frames": []}
            right = b["measurements"][metric] if b else {"value": None, "source_frames": []}
            x, y = left["value"], right["value"]
            tolerance = STANCE_TOLERANCE_SHOULDERS if _unit(metric) == "shoulder_width" else ANGLE_TOLERANCE_DEG
            scale = STANCE_SCALE_SHOULDERS if _unit(metric) == "shoulder_width" else ANGLE_SCALE_DEG
            measured = x is not None and y is not None
            delta = x - y if measured else None
            _require(not measured or _number(delta), "metric_distance_nonfinite")
            distance = min(1., max(0., (abs(delta) - tolerance) / scale)) if measured else None
            weight = phase_weight * metric_weight
            deduction = 100 * weight * distance if measured else None
            items.append({"metric_id": metric, "unit": _unit(metric), "candidate_value": x, "reference_value": y,
                "delta": delta, "normalized_distance": distance, "weight": weight, "raw_weighted_deduction": deduction,
                "tolerance": tolerance, "scale": scale, "status": "compared" if measured else "not_measured",
                "candidate_source_frames": left["source_frames"], "reference_source_frames": right["source_frames"]})
            if measured:
                phase_available.append(metric_weight)
                phase_distances.append(metric_weight * distance)
                available_weights.append(weight)
                weighted_distances.append(weight * distance)
        coverage = math.fsum(phase_available)
        if coverage:
            available_phases.add(identifier)
        output["phases"].append({"phase_id": identifier, "weight": phase_weight,
            "score": round(100 * (1 - math.fsum(phase_distances) / coverage), 6) if coverage else None,
            "coverage": round(coverage, 8), "metrics": items,
            "candidate_window": {key: a[key] for key in ("start_ms", "anchor_ms", "end_ms", "source_start_frame", "source_frame", "source_end_frame")} if a else None,
            "reference_window": {key: b[key] for key in ("start_ms", "anchor_ms", "end_ms", "source_start_frame", "source_frame", "source_end_frame")} if b else None})
    available = math.fsum(available_weights)
    output["coverage"] = round(available, 8)
    for phase in output["phases"]:
        for metric in phase["metrics"]:
            compared = metric["status"] == "compared" and available > 0
            metric["normalized_weight"] = metric["weight"] / available if compared else None
            metric["weighted_deduction"] = metric["raw_weighted_deduction"] / available if compared else None
    output["missing_required_phases"] = sorted(set(REQUIRED_PHASES) - available_phases)
    if available + 1e-12 < MINIMUM_COVERAGE:
        output["reason_codes"].append("insufficient_comparison_coverage")
    if output["missing_required_phases"]:
        output["reason_codes"].append("required_comparison_phase_unavailable")
    if not output["reason_codes"]:
        output["status"] = "scored"
        output["reference_similarity_score"] = round(100 * (1 - math.fsum(weighted_distances) / available), 6)
        independently_reviewed = all(source["classification_provenance"].get("method") in {"human_visual_review", "agent_visual_review"}
                                     for source in (candidate, reference))
        output["evaluation_eligible"] = not same_source and independently_reviewed
        if not independently_reviewed:
            output["reason_codes"].append("classification_not_independently_reviewed")
    if same_source:
        output["reason_codes"].append(scope)
    return output
