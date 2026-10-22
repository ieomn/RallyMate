"""Recompute card evidence from frame observations, never saved grades.

The catalogue contains all 298 source cards. Five temporal card calculators run
by default. Service-controlled annotation files optionally connect exact
manual GS stages and FS03/FS04 windows through source-verified adapters.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any

from rallymate_events.associations import event_identity
from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.schemas import PoseSequence
from rallymate_features.source_alignment import prepare_source_aligned_context, clear_source_aligned_feature_cache
from rallymate_scoring.card_evaluation import GRADE_POINTS, summarize_card_scores
from rallymate_scoring.card_kinematics import finite
from rallymate_scoring.card_rule_catalog import load_card_rules
from rallymate_scoring.temporal_card_evidence import (
    VERSION as TEMPORAL_VERSION, CARD_CONDITIONS, CARD_REQUIRED_JOINTS,
    compute_temporal_card_evidence,
)

VERSION = "card-execution-v1.0.0"
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_FRAME_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_EVENT_FILE_BYTES = 64 * 1024 * 1024
MAX_FRAME_RECORDS = 150000
MAX_EVENT_RECORDS = 5000
_REPORT_LOCK = RLock()


def _reject_constant(value):
    raise ValueError("non_finite_json_number:" + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_object_key")
        result[key] = value
    return result


def _read_records(path: Path, max_bytes: int, max_records: int) -> list[dict[str, Any]]:
    if path.stat().st_size > max_bytes:
        raise ValueError("card_input_file_limit_exceeded")
    output, consumed = [], 0
    with path.open("rb") as stream:
        while True:
            line = stream.readline(MAX_RECORD_BYTES + 1)
            if not line:
                break
            consumed += len(line)
            if len(line) > MAX_RECORD_BYTES or consumed > max_bytes:
                raise ValueError("card_input_record_or_file_limit_exceeded")
            if not line.strip():
                continue
            if len(output) >= max_records:
                raise ValueError("card_input_record_count_exceeded")
            row = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if not isinstance(row, dict):
                raise ValueError("card_input_record_not_object")
            output.append(row)
    return output


def _catalogue_reason(rule: Mapping, raw: Mapping) -> str | None:
    evaluation = rule.get("evaluation", {})
    conditions = evaluation.get("conditions", [])
    actual = CARD_CONDITIONS[rule["indicator_id"]]
    if (evaluation.get("kind") != "visual_proxy"
            or evaluation.get("engine_version") != TEMPORAL_VERSION
            or evaluation.get("grade_policy") != "condition_completion_v1"
            or evaluation.get("grade_mapping") != GRADE_POINTS):
        return "catalogue_temporal_engine_or_grade_policy_mismatch"
    if (not isinstance(conditions, list) or len(conditions) != len(actual)
            or any(not isinstance(c, Mapping) for c in conditions)):
        return "catalogue_temporal_conditions_mismatch"
    declared = {}
    for condition in conditions:
        metric = condition.get("metric")
        if not isinstance(metric, str) or metric in declared or condition.get("required") is not True:
            return "catalogue_temporal_conditions_mismatch"
        declared[metric] = condition
    for metric, operator, threshold, unit in actual:
        condition = declared.get(metric, {})
        configured = condition.get("bounds") if operator == "between" else condition.get("value")
        threshold_matches = (isinstance(configured, list) and len(configured) == 2
            and all(finite(v) for v in configured) and tuple(configured) == threshold) if operator == "between" else (
                finite(configured) and configured == threshold)
        if (condition.get("operator") != operator or condition.get("unit") != unit
                or not threshold_matches):
            return "catalogue_temporal_conditions_mismatch"
    required = set(rule.get("required_joint_names", []))
    required.update(joint for c in conditions for joint in c.get("required_joint_names", []))
    if required != set(CARD_REQUIRED_JOINTS[rule["indicator_id"]]):
        return "catalogue_temporal_joint_contract_mismatch"
    return None


def _score_temporal(rule: Mapping, raw: Mapping, event: Mapping) -> dict:
    result = {
        "version": VERSION, "indicator_id": raw["indicator_id"], "rule_version": rule["rule_version"],
        "measurement_version": raw["rule_version"], "video_id": event.get("video_id"),
        "event_id": event.get("event_id"), "person_track_id": event.get("person_track_id"),
        "event_code": event.get("event_code"), "status": "review_required",
        "original_grade": None, "proxy_grade": None, "score": None,
        "source_grade_supported": False, "reason_codes": [], "conditions": [],
        "measurements": raw["measurements"], "evidence": raw["evidence"],
        "quality": raw["evidence"].get("coverage"), "threshold_basis": raw["threshold_basis"],
        "limitations": raw["limitations"], "source": {key: rule["source"][key]
            for key in ("document_id", "document_sha256", "table", "rubric_sha256")},
    }
    mismatch = _catalogue_reason(rule, raw)
    if mismatch:
        result["reason_codes"].append(mismatch)
    if raw["status"] != "measured_visual_proxy":
        result["reason_codes"].append(raw.get("reason") or "temporal_evidence_incomplete")
    # Compare the freshly recomputed values again. Neither serialized pass bits
    # nor a score stored in the event input can authorize a score.
    for metric, operator, threshold, unit in CARD_CONDITIONS[raw["indicator_id"]]:
        value = raw["measurements"].get(metric)
        passed = None
        if not result["reason_codes"] and finite(value):
            passed = (threshold[0] <= value <= threshold[1] if operator == "between" else
                      value >= threshold if operator == "gte" else value <= threshold)
        result["conditions"].append({"key": metric, "metric": metric, "value": value,
            "operator": operator, "threshold": threshold, "unit": unit, "passed": passed})
    if result["reason_codes"] or any(c["passed"] is None for c in result["conditions"]):
        if not result["reason_codes"]:
            result["reason_codes"].append("required_temporal_value_missing")
        return result
    fraction = sum(c["passed"] for c in result["conditions"]) / len(result["conditions"])
    grade = "A" if fraction == 1 else "B" if fraction >= .75 else "C" if fraction >= .5 else "D" if fraction > 0 else "E"
    result.update(status="proxy_scored", proxy_grade=grade, score=GRADE_POINTS[grade],
                  condition_completion_fraction=fraction,
                  semantics="engineering_visual_condition_completion_not_original_technical_grade")
    return result


def build_card_execution_from_sequence(sequence: PoseSequence, events: Sequence[Mapping[str, Any]],
                                        primary_timeline: Sequence[Mapping[str, Any]], *,
                                        source_run_id: str | None = None) -> dict:
    """Batch one run using a trusted run ID or sequence metadata binding."""
    bound_run = sequence.coordinate_metadata.get("source_run_id")
    if source_run_id is not None and (not isinstance(source_run_id, str) or not source_run_id.strip()):
        raise ValueError("card_source_run_id_invalid")
    if bound_run is not None and (not isinstance(bound_run, str) or not bound_run.strip()):
        raise ValueError("card_sequence_run_binding_invalid")
    if source_run_id is not None and bound_run is not None and source_run_id != bound_run:
        raise ValueError("card_sequence_run_binding_mismatch")
    source_run_id = source_run_id or bound_run
    if events and source_run_id is None:
        raise ValueError("card_source_run_binding_required")
    for event in events:
        if not isinstance(event, Mapping) or event.get("video_id") != source_run_id:
            raise ValueError("card_event_video_run_binding_mismatch")
    for row in primary_timeline:
        if (not isinstance(row, Mapping) or any(key in row and row[key] != source_run_id
                for key in ("video_id", "job_id"))):
            raise ValueError("card_timeline_video_run_binding_mismatch")
    catalogue = load_card_rules()
    rules = {row["indicator_id"]: row for row in catalogue["cards"]}
    context = prepare_source_aligned_context(sequence, events, primary_timeline=primary_timeline)
    computed = []
    seen = set()
    for index, event in enumerate(events):
        if not isinstance(event, Mapping) or event.get("event_code") not in {"FS01", "FS09"}:
            continue
        identity = event_identity(event) or ("invalid_event", index)
        if identity in seen:
            continue
        seen.add(identity)
        result = compute_temporal_card_evidence(sequence, event, events=events,
                                               primary_timeline=primary_timeline, context=context)
        computed.extend(_score_temporal(rules[row["indicator_id"]], row, event) for row in result["cards"])
    by_card = defaultdict(list)
    for result in computed:
        by_card[result["indicator_id"]].append(result)
    observed_codes = {row.get("event_code") for row in events if isinstance(row, Mapping)}
    cards = []
    for identifier, rule in rules.items():
        instances = by_card[identifier]
        if instances:
            run_status = "proxy_scored" if any(row["status"] == "proxy_scored" for row in instances) else "review_required"
            reasons = []
        elif rule["event_code"] not in observed_codes:
            run_status, reasons = "not_observed", ["event_type_not_observed_in_run"]
        elif rule["evaluation"]["kind"] == "manual_review":
            run_status, reasons = "review_required", ["required_observable_needs_manual_review"]
        else:
            run_status, reasons = "not_executed", ["runtime_stage_or_metric_binding_missing"]
        cards.append({"indicator_id": identifier, "event_code": rule["event_code"],
            "rule_version": rule["rule_version"], "rule_kind": rule["evaluation"]["kind"],
            "name_zh": rule["effective"].get("name_zh", rule["source"]["fields"]["指标名称"]["text"]),
            "runtime_engine": TEMPORAL_VERSION if identifier in CARD_CONDITIONS else None,
            "run_status": run_status, "reason_codes": reasons,
            "original_grade": None, "source_grade_supported": False,
            "instance_count": len(instances), "instance_status_counts": dict(Counter(row["status"] for row in instances)),
            "instances": instances})
    all_ids = list(rules)
    observed_ids = [identifier for identifier, rule in rules.items() if rule["event_code"] in observed_codes]
    summary = summarize_card_scores(computed, all_ids)
    observed_summary = summarize_card_scores(computed, observed_ids)
    return {"version": VERSION, "rule_version": catalogue["version"], "source_run_id": source_run_id,
        "input": {"source_frame_count": len(sequence.source_frames), "event_record_count": len(events),
                  "observed_event_codes": sorted(code for code in observed_codes if isinstance(code, str)),
                  "source": "original_frame_observations_events_and_primary_selection",
                  "saved_grades_trusted": False},
        "cards": cards, "summary": summary, "observed_type_summary": observed_summary,
        "coverage": {"source_card_count": len(cards), "fs_card_count": sum(c["event_code"].startswith("FS") for c in cards),
                     "gs_card_count": sum(c["event_code"].startswith("GS") for c in cards),
                     "wired_temporal_card_count": len(CARD_CONDITIONS),
                     "computed_card_instance_count": len(computed),
                     "run_card_status_counts": dict(Counter(c["run_status"] for c in cards)),
                     "original_auto_graded_card_count": 0,
                     "semantics": "catalogue_coverage_is_not_extractor_or_validation_coverage"},
        "overall_score": None, "missing_policy": "exclude_from_score_keep_in_coverage_no_missing_zero",
        "limitations": ["Only five temporal calculators are wired by this adapter.",
                        "Automatic event candidates are not independently verified action labels.",
                        "Proxy thresholds are uncalibrated engineering rules, not Word-grade thresholds."]}


def build_card_execution_from_records(records: list[dict[str, Any]], events: Sequence[Mapping[str, Any]],
                                       primary_timeline: list[dict[str, Any]], *,
                                       source_run_id: str | None = None) -> dict:
    record_run_ids = set()
    for record in records:
        job_id = record.get("job_id") if isinstance(record, Mapping) else None
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("card_frame_run_binding_required")
        record_run_ids.add(job_id)
    if len(record_run_ids) > 1 or (source_run_id is not None and record_run_ids and record_run_ids != {source_run_id}):
        raise ValueError("card_frame_run_binding_mismatch")
    bound_run = next(iter(record_run_ids), source_run_id)
    sequence = pose_sequence_from_records(records, primary_timeline)
    if bound_run is not None:
        sequence.coordinate_metadata["source_run_id"] = bound_run
    try:
        return build_card_execution_from_sequence(sequence, events, primary_timeline, source_run_id=source_run_id)
    finally:
        clear_source_aligned_feature_cache(sequence)


def _input_signature(directory: Path) -> tuple:
    base = tuple((name, (stat := (directory / name).stat()).st_size, stat.st_mtime_ns)
                 for name in ("events.jsonl", "primary-player.jsonl", "frames.jsonl"))
    annotation_names = ("gs-stage-annotations.json", "footwork-cycle-annotations.json")
    if not any((directory / name).exists() for name in annotation_names):
        return base + tuple((name, "absent") for name in annotation_names)
    # Optional manual inputs are content-bound, including the actual video. A
    # same-size edit that preserves mtime must invalidate a completed report.
    from .gs_card_execution import _read_json
    extra = []
    for name in (*annotation_names, "summary.json", "frames.jsonl", "primary-player.jsonl", "events.jsonl"):
        path = directory / name
        if not path.is_file():
            extra.append((name, "missing_or_not_file"))
            continue
        try:
            with path.open("rb") as stream:
                extra.append((name, hashlib.file_digest(stream, "sha256").hexdigest()))
        except OSError:
            extra.append((name, "unreadable"))
    try:
        summary = _read_json(directory / "summary.json")
        source = Path(summary["input"]["video_path"]).resolve()
        if source.is_file():
            with source.open("rb") as stream:
                extra.append(("gs_source_video", str(source), hashlib.file_digest(stream, "sha256").hexdigest()))
        else:
            extra.append(("gs_source_video", "missing"))
    except (OSError, ValueError, TypeError, KeyError):
        extra.append(("gs_source_video", "source_summary_unavailable"))
    return base + tuple(extra)


def _apply_optional_gs_annotations(report: dict, directory: Path) -> None:
    """Recompute optional GS inputs; never merge externally supplied scores."""
    annotation_path = directory / "gs-stage-annotations.json"
    if not annotation_path.exists():
        return
    from .gs_card_execution import VERSION as GS_VERSION, _read_json, build_gs_card_execution_report
    try:
        annotations = _read_json(annotation_path)
        gs = build_gs_card_execution_report(directory, annotations)
        if (gs["source_run_id"] != report["source_run_id"] or gs["rule_version"] != report["rule_version"]
                or gs["input"].get("service_eligible") is not True
                or gs["input"].get("artifact_files_verified") is not True
                or gs["input"].get("decoder_verified_from_original_video") is not True):
            raise ValueError("gs_production_report_binding_mismatch")
        replacements = {row["indicator_id"]: row for row in gs["cards"]}
        expected = {row["indicator_id"] for row in report["cards"] if row["event_code"].startswith("GS")}
        if set(replacements) != expected or len(gs["cards"]) != len(expected):
            raise ValueError("gs_production_card_inventory_mismatch")
        report["cards"] = [replacements[row["indicator_id"]] if row["indicator_id"] in replacements else row for row in report["cards"]]
        observed = set(report["input"]["observed_event_codes"])
        observed.update(action["technique_code"] for action in annotations["actions"])
        report["input"]["observed_event_codes"] = sorted(observed)
        report["input"]["gs_execution"] = {"status": "executed", "version": GS_VERSION, **gs["input"]}
        report["gs_stage_windows"] = gs["stage_windows"]
        report["coverage"].update(gs_annotated_stage_count=gs["coverage"]["annotated_stage_count"],
                                  gs_executed_card_count=gs["coverage"]["executed_card_count"])
        report["limitations"][0] = "Five temporal FS calculators and explicitly annotated GS stages are connected; no automatic ten-stage GS locator is registered."
        report["limitations"].extend(gs["limitations"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # GS errors cannot suppress a valid FS result or turn an unverifiable
        # annotation into a grade. Avoid exposing paths from OS/JSON errors.
        reason = str(exc)
        if not reason or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_:" for character in reason):
            reason = "gs_annotation_or_source_validation_failed"
        for row in report["cards"]:
            if row["event_code"].startswith("GS"):
                row.update(run_status="unavailable", reason_codes=[reason], runtime_engine=None,
                    instance_count=0, instance_status_counts={}, instances=[], original_grade=None, source_grade_supported=False)
        report["input"]["gs_execution"] = {"status": "unavailable", "version": GS_VERSION, "reason_codes": [reason]}
        report.pop("gs_stage_windows", None)
        report["coverage"].update(gs_annotated_stage_count=0, gs_executed_card_count=0)
    all_instances = [instance for row in report["cards"] for instance in row["instances"]]
    all_ids = [row["indicator_id"] for row in report["cards"]]
    observed_ids = [row["indicator_id"] for row in report["cards"] if row["event_code"] in report["input"]["observed_event_codes"]]
    report["summary"] = summarize_card_scores(all_instances, all_ids)
    report["observed_type_summary"] = summarize_card_scores(all_instances, observed_ids)
    report["coverage"]["computed_card_instance_count"] = len(all_instances)
    report["coverage"]["run_card_status_counts"] = dict(Counter(row["run_status"] for row in report["cards"]))


def _apply_optional_cycle_annotations(report: dict, directory: Path) -> None:
    annotation_path = directory / "footwork-cycle-annotations.json"
    if not annotation_path.exists():
        return
    from .footwork_cycle_artifacts import _read_json, build_footwork_cycle_artifact_report
    from .footwork_cycle_execution import VERSION as CYCLE_VERSION
    try:
        annotations = _read_json(annotation_path)
        cycle = build_footwork_cycle_artifact_report(directory, annotations)
        if (cycle["source_run_id"] != report["source_run_id"] or cycle["rule_version"] != report["rule_version"]
                or cycle["input"].get("service_eligible") is not True
                or cycle["input"].get("artifact_files_verified") is not True
                or cycle["input"].get("decoder_verified_from_original_video") is not True):
            raise ValueError("cycle_production_report_binding_mismatch")
        replacements = {row["indicator_id"]: row for row in cycle["cards"]}
        expected = {row["indicator_id"] for row in report["cards"] if row["event_code"] in {"FS03", "FS04"}}
        if set(replacements) != expected or len(cycle["cards"]) != len(expected):
            raise ValueError("cycle_production_card_inventory_mismatch")
        report["cards"] = [replacements.get(row["indicator_id"], row) for row in report["cards"]]
        observed = set(report["input"]["observed_event_codes"])
        observed.update(window["event_code"] for window in annotations["windows"])
        report["input"]["observed_event_codes"] = sorted(observed)
        report["input"]["footwork_cycle_execution"] = {"status": "executed", "version": CYCLE_VERSION, **cycle["input"]}
        report["cycle_windows"] = cycle["cycle_windows"]
        report["coverage"].update(cycle_annotated_window_count=len(cycle["cycle_windows"]),
            cycle_executed_card_count=sum(bool(row["instances"]) for row in cycle["cards"]))
        report["limitations"].extend(cycle["limitations"])
        report["limitations"].append("Explicit manual FS03/FS04 candidate windows are connected; no automatic step-family or ground-contact verifier is registered.")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        reason = str(exc)
        if not reason or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_:" for character in reason):
            reason = "cycle_annotation_or_source_validation_failed"
        for row in report["cards"]:
            if row["event_code"] in {"FS03", "FS04"}:
                row.update(run_status="unavailable", reason_codes=[reason], runtime_engine=None,
                    instance_count=0, instance_status_counts={}, instances=[], original_grade=None, source_grade_supported=False)
        report["input"]["footwork_cycle_execution"] = {"status": "unavailable", "version": CYCLE_VERSION, "reason_codes": [reason]}
        report.pop("cycle_windows", None)
        report["coverage"].update(cycle_annotated_window_count=0, cycle_executed_card_count=0)
    all_instances = [instance for row in report["cards"] for instance in row["instances"]]
    all_ids = [row["indicator_id"] for row in report["cards"]]
    observed_ids = [row["indicator_id"] for row in report["cards"] if row["event_code"] in report["input"]["observed_event_codes"]]
    report["summary"] = summarize_card_scores(all_instances, all_ids)
    report["observed_type_summary"] = summarize_card_scores(all_instances, observed_ids)
    report["coverage"]["computed_card_instance_count"] = len(all_instances)
    report["coverage"]["run_card_status_counts"] = dict(Counter(row["run_status"] for row in report["cards"]))


@lru_cache(maxsize=2)
def _cached_report(directory_name: str, signature: tuple, rule_digest: str,
                   engine_version: str) -> dict:
    directory = Path(directory_name)
    events = _read_records(directory / "events.jsonl", MAX_EVENT_FILE_BYTES, MAX_EVENT_RECORDS)
    timeline = _read_records(directory / "primary-player.jsonl", MAX_FRAME_FILE_BYTES, MAX_FRAME_RECORDS)
    records = _read_records(directory / "frames.jsonl", MAX_FRAME_FILE_BYTES, MAX_FRAME_RECORDS)
    # Preserve the existing strict FS source contract. A broken FS source run
    # still fails here; a successful GS report must not conceal that failure.
    report = build_card_execution_from_records(records, events, timeline, source_run_id=directory.name)
    _apply_optional_gs_annotations(report, directory)
    _apply_optional_cycle_annotations(report, directory)
    if _input_signature(directory) != signature:
        raise ValueError("card_input_changed_during_computation")
    report["input"]["catalogue_sha256"] = rule_digest
    report["input"]["catalogue_digest_kind"] = "canonical_json_sorted_utf8_v1"
    return report


def clear_card_execution_cache() -> None:
    with _REPORT_LOCK:
        _cached_report.cache_clear()


def build_card_execution_report(run_directory: Path | str) -> dict:
    """Cache two finished reports; no raw frames/sequence live in this cache.

    File size/mtime, catalogue contents and engine versions bind a cache entry.
    When manual annotations exist, their contents, source files and video are
    also hashed into the key. Missing annotations retain the original FS path.
    The lock prevents concurrent requests from parsing the same large run twice.
    Returned copies keep callers from mutating a cached report.
    """
    directory = Path(run_directory).resolve()
    with _REPORT_LOCK:
        signature = _input_signature(directory)
        rule_digest = hashlib.sha256(json.dumps(load_card_rules(), sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        engine_version = VERSION + ":" + TEMPORAL_VERSION
        if (directory / "gs-stage-annotations.json").exists():
            from .gs_card_execution import VERSION as GS_VERSION
            engine_version += ":" + GS_VERSION
        if (directory / "footwork-cycle-annotations.json").exists():
            from .footwork_cycle_artifacts import VERSION as ARTIFACT_VERSION
            from .footwork_cycle_execution import VERSION as CYCLE_VERSION
            from rallymate_scoring.footwork_cycle_card_evidence import VERSION as CYCLE_MEASUREMENT_VERSION
            engine_version += ":" + ARTIFACT_VERSION + ":" + CYCLE_VERSION + ":" + CYCLE_MEASUREMENT_VERSION
        return deepcopy(_cached_report(str(directory), signature, rule_digest, engine_version))
