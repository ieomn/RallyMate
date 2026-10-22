"""Map freshly calculated manual FS03/FS04 windows into catalogue instances.

The sequence entry point reuses one context and never reads files or saved
grades. The owner of a run supplies its already parsed, source-bound sequence;
this function does not certify external artifacts or register a detector.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json

from rallymate_features.schemas import PoseSequence
from rallymate_features.source_alignment import prepare_source_aligned_context
from rallymate_scoring.card_evaluation import GRADE_POINTS, summarize_card_scores
from rallymate_scoring.card_rule_catalog import load_card_rules
from rallymate_scoring.footwork_cycle_card_evidence import (
    VERSION as MEASUREMENT_VERSION, CORE_JOINTS, SUPPORTED_CODES, compute_footwork_cycle_card_evidence,
)
from rallymate_scoring.temporal_card_evidence import _finite

VERSION = "footwork-cycle-execution-v1.0.0"
MAX_WINDOWS = 2000


def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _instance(rule, raw, event):
    result = {"version": VERSION, "measurement_version": MEASUREMENT_VERSION,
        "indicator_id": rule["indicator_id"], "rule_version": rule["rule_version"],
        "video_id": event["video_id"], "event_id": event["event_id"], "person_track_id": event["person_track_id"],
        "event_code": event["event_code"], "status": "review_required", "original_grade": None,
        "proxy_grade": None, "score": None, "source_grade_supported": False, "conditions": [],
        "measurements": deepcopy(raw["measurements"]), "evidence": deepcopy(raw["evidence"]),
        "quality": deepcopy(raw["evidence"]["coverage"]), "reason_codes": list(raw["reason_codes"]),
        "threshold_basis": "engineering_v1_uncalibrated", "limitations": [rule["evaluation"]["limitation"],
            "manual_window_is_nonexpert_candidate_not_contact_force_or_step_family_ground_truth"],
        "source": {key: rule["source"][key] for key in ("document_id", "document_sha256", "table", "rubric_sha256")}}
    if raw["status"] != "measured_visual_proxy" and not result["reason_codes"]:
        result["reason_codes"].append(raw.get("reason") or "cycle_evidence_incomplete")
    expected = set(CORE_JOINTS) | set(rule["required_joint_names"])
    expected.update(name for condition in rule["evaluation"]["conditions"] for name in condition["required_joint_names"])
    if set(result["quality"]["required_joint_names"]) != expected:
        result["reason_codes"].append("cycle_common_required_joint_contract_mismatch")
    evaluation = rule["evaluation"]
    if evaluation.get("grade_policy") != "condition_completion_v1" or evaluation.get("grade_mapping") != GRADE_POINTS:
        result["reason_codes"].append("cycle_grade_policy_mismatch")
    if evaluation.get("kind") != "visual_proxy":
        result["reason_codes"].append("required_external_observable_not_recovered_from_pose")
    # Ignore both saved pass bits and the core's pass bits. Only fresh raw
    # measurements, the known core status, and current declared math enter.
    for condition in evaluation["conditions"]:
        metric, operator = condition["metric"], condition["operator"]
        value = raw["measurements"].get(metric)
        threshold = condition.get("bounds", condition.get("value"))
        passed = None
        if not result["reason_codes"] and _finite(value):
            passed = (threshold[0] <= value <= threshold[1] if operator == "between" else
                      value >= threshold if operator == "gte" else value <= threshold if operator == "lte" else
                      value == threshold if operator == "eq" else None)
        result["conditions"].append({"key": condition["key"], "metric": metric, "operator": operator,
            "threshold": threshold, "value": value, "unit": condition["unit"], "passed": passed})
    if result["reason_codes"] or not result["conditions"] or any(c["passed"] is None for c in result["conditions"]):
        if not result["reason_codes"]:
            result["reason_codes"].append("required_cycle_measurement_missing")
        result["reason_codes"] = list(dict.fromkeys(result["reason_codes"]))
        return result
    fraction = sum(c["passed"] for c in result["conditions"]) / len(result["conditions"])
    grade = "A" if fraction == 1 else "B" if fraction >= .75 else "C" if fraction >= .5 else "D" if fraction > 0 else "E"
    result.update(status="proxy_scored", proxy_grade=grade, score=GRADE_POINTS[grade],
        condition_completion_fraction=fraction,
        semantics="engineering_visual_condition_completion_not_original_technical_grade")
    return result


def compute_footwork_cycle_execution(sequence: PoseSequence, events: Sequence[Mapping], primary_timeline: Sequence[Mapping],
        bindings: Sequence[Mapping], *, source_run_id: str) -> dict:
    """Recompute explicitly bound candidate windows using a single pose context.

    Caller authorization to select manual windows is not evidence of expert
    technique labeling. All window times and identities are checked by the
    core against source frames. Missing windows remain missing, never E/zero.
    """
    if not isinstance(source_run_id, str) or not source_run_id.strip():
        raise ValueError("cycle_source_run_id_required")
    if sequence.coordinate_metadata.get("source_run_id", source_run_id) != source_run_id:
        raise ValueError("cycle_sequence_source_run_mismatch")
    if not isinstance(events, (list, tuple)) or not isinstance(bindings, (list, tuple)) or len(bindings) > MAX_WINDOWS:
        raise ValueError("cycle_events_or_bindings_invalid")
    unique = {}
    for event in events:
        if not isinstance(event, Mapping) or event.get("video_id") != source_run_id:
            raise ValueError("cycle_event_source_run_mismatch")
        identifier = event.get("event_id")
        if (not isinstance(identifier, str) or not identifier or type(event.get("person_track_id")) is not int
                or event["person_track_id"] != sequence.primary_player_id):
            raise ValueError("cycle_event_identity_invalid")
        if identifier in unique and unique[identifier] != event:
            raise ValueError("cycle_duplicate_event_conflict")
        unique[identifier] = event
    selected = [event for event in unique.values() if event.get("event_code") in SUPPORTED_CODES]
    if len(selected) > MAX_WINDOWS:
        raise ValueError("cycle_window_limit_exceeded")
    if not isinstance(primary_timeline, (list, tuple)) or any(not isinstance(row, Mapping) or
            any(key in row and row[key] != source_run_id for key in ("video_id", "job_id")) for row in primary_timeline):
        raise ValueError("cycle_primary_timeline_run_mismatch")
    by_binding = {}
    selected_ids = {event["event_id"] for event in selected}
    for binding in bindings:
        if (not isinstance(binding, Mapping) or binding.get("event_id") not in selected_ids
                or binding.get("video_id") != source_run_id):
            raise ValueError("cycle_binding_unknown_event_or_run")
        identifier = binding["event_id"]
        if identifier in by_binding and by_binding[identifier] != binding:
            raise ValueError("cycle_duplicate_binding_conflict")
        by_binding[identifier] = binding
    catalogue = load_card_rules()
    rules = [rule for rule in catalogue["cards"] if rule["event_code"] in SUPPORTED_CODES]
    by_rule = {rule["indicator_id"]: rule for rule in rules}
    by_card = defaultdict(list)
    context = prepare_source_aligned_context(sequence, list(unique.values()), primary_timeline=primary_timeline)
    for event in selected:
        raw = compute_footwork_cycle_card_evidence(sequence, event, events=list(unique.values()), context=context,
            cycle_binding=by_binding.get(event["event_id"]))
        for card in raw["cards"]:
            by_card[card["indicator_id"]].append(_instance(by_rule[card["indicator_id"]], card, event))
    rows, instances = [], []
    for rule in rules:
        actual = by_card[rule["indicator_id"]]
        status = "proxy_scored" if any(row["status"] == "proxy_scored" for row in actual) else "review_required" if actual else "not_observed"
        rows.append({"indicator_id": rule["indicator_id"], "event_code": rule["event_code"],
            "stage_code": rule["stage_code"], "rule_version": rule["rule_version"], "rule_kind": rule["evaluation"]["kind"],
            "name_zh": rule["effective"]["name_zh"], "runtime_engine": VERSION if actual else None,
            "run_status": status, "reason_codes": sorted({reason for row in actual for reason in row["reason_codes"]}) if actual else ["cycle_family_not_manually_annotated"],
            "instance_count": len(actual), "instance_status_counts": dict(Counter(row["status"] for row in actual)),
            "original_grade": None, "source_grade_supported": False, "instances": actual})
        instances.extend(actual)
    observed = {event["event_code"] for event in selected}
    return {"version": VERSION, "rule_version": catalogue["version"], "source_run_id": source_run_id,
        "input": {"source": "source_bound_sequence_and_manual_cycle_windows", "source_frame_count": len(sequence.source_frames),
            "event_count": len(unique), "candidate_event_count": len(selected), "supplied_window_binding_count": len(by_binding),
            "manual_window_count": sum(binding.get("method") == "manual_anchor" for binding in by_binding.values()),
            "catalogue_sha256": _canonical(catalogue), "catalogue_digest_kind": "canonical_json_sorted_utf8_v1",
            "artifact_files_verified": False, "automatic_cycle_detector_registered": False},
        "cards": rows, "summary": summarize_card_scores(instances, [rule["indicator_id"] for rule in rules]),
        "observed_type_summary": summarize_card_scores(instances, [rule["indicator_id"] for rule in rules if rule["event_code"] in observed]),
        "coverage": {"source_fs_card_count": 50, "cycle_card_count": len(rules), "computed_card_instance_count": len(instances),
            "run_card_status_counts": dict(Counter(row["run_status"] for row in rows)),
            "instance_status_counts": dict(Counter(row["status"] for row in instances)), "original_auto_graded_card_count": 0},
        "overall_score": None, "missing_policy": "exclude_from_grade_keep_in_applicable_card_coverage",
        "limitations": ["manual_cycle_windows_are_nonexpert_estimates_not_step_family_ground_truth",
            "continuous_image_motion_does_not_establish_contact_force_target_arrival_or_original_grade",
            "no_automatic_cycle_detector_registered", "record_level_entry_does_not_verify_artifact_files"]}
