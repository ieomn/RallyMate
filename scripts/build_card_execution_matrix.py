"""Build a 298-card evidence matrix from explicit, verified replay artifacts.

This is a proxy execution/evidence inventory. Complete original-card grades
have no registered verifier in this implementation; registered scalar functions
and proxy results cannot establish complete source-rubric coverage.
No report, absent source files, stale rule/code digests, or inconsistent summary
claims fail the build. Unit fixtures are not added to real-video evidence counts.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rallymate_scoring.card_evaluation import GRADE_POINTS, summarize_card_scores
from rallymate_scoring.card_kinematics import SPECS, DYNAMIC_SPECS

VERSION = "card-execution-matrix-v1.0.0"
RAW = "raw_file_sha256"
CANONICAL = "canonical_json_sorted_utf8_v1"
NORMALIZED = "utf8_lf_source_v1"
ALLOWED_STATUS = {"proxy_scored", "review_required", "unavailable"}
RULE_PATH = Path("src/rallymate_scoring/data/card_rules_v1.json")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key:" + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite_json_value:" + value)


def read_json(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("missing_or_oversized_evidence_file:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def normalized_digest(path):
    text = Path(path).read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("invalid_sha256")
    return value.lower()


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _count(value):
    return type(value) is int and value >= 0


def _same_number(a, b):
    return _finite(a) and _finite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-9)


def _verify_equal(actual, expected, context):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise ValueError("summary_keys_mismatch:" + context)
        for key in expected:
            _verify_equal(actual[key], expected[key], context + "/" + key)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError("summary_list_mismatch:" + context)
        for index, (left, right) in enumerate(zip(actual, expected)):
            _verify_equal(left, right, context + f"/{index}")
    elif _finite(expected):
        if not _same_number(actual, expected):
            raise ValueError("summary_numeric_mismatch:" + context)
    elif type(actual) is not type(expected) or actual != expected:
        raise ValueError("summary_value_mismatch:" + context)


def _safe_repo_path(root, value):
    if not isinstance(value, str) or not value:
        raise ValueError("missing_artifact_path")
    candidate = (root / value).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("artifact_path_outside_repository")
    return candidate


def _relative(root, path):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("output_provenance_path_outside_repository")
    return path.relative_to(root.resolve()).as_posix()


def verify_catalogue_digest(value, kind, catalogue_path, catalogue, normalized=None):
    if kind == RAW:
        expected = sha256(catalogue_path)
    elif kind == CANONICAL:
        expected = canonical_digest(catalogue)
    else:
        raise ValueError("unknown_catalogue_digest_kind")
    tested = _digest(value)
    if tested != expected:
        if kind != RAW or normalized is None or _digest(normalized) != normalized_digest(catalogue_path):
            raise ValueError("catalogue_digest_mismatch")
    return {"kind": kind, "tested_sha256": tested, "current_sha256": expected,
            "raw_matches_tested": tested == expected,
            "normalized_equivalence_used": tested != expected}


def verify_module_digests(root, declared, normalized, *, prefix=""):
    if not isinstance(declared, dict) or not declared:
        raise ValueError("runtime_module_digests_missing")
    normalized = normalized or {}
    if not isinstance(normalized, dict) or set(normalized) - set(declared):
        raise ValueError("unknown_normalized_module_digest")
    result = {}
    for name, expected in declared.items():
        path = _safe_repo_path(root, prefix + name)
        if not path.is_file() or path.suffix not in {".py", ".json"}:
            raise ValueError("unsupported_runtime_module_path")
        raw_actual, raw_expected = sha256(path), _digest(expected)
        normalized_actual = normalized_digest(path)
        normalized_expected = _digest(normalized[name]) if name in normalized else None
        if normalized_expected is not None and normalized_expected != normalized_actual:
            raise ValueError("normalized_runtime_module_mismatch:" + name)
        if raw_actual != raw_expected and normalized_expected is None:
            raise ValueError("runtime_module_digest_mismatch:" + name)
        # JSON formatting changes cannot stand in for a different rule object.
        # Rule content is checked separately with its own declared algorithm.
        result[_relative(root, path)] = {"tested_raw_sha256": raw_expected,
            "current_raw_sha256": raw_actual, "current_raw_matches_tested": raw_actual == raw_expected,
            "tested_normalized_sha256": normalized_expected,
            "current_normalized_sha256": normalized_actual, "normalization": NORMALIZED}
    return result


def validate_instance(row, rule, *, video_sha256, video_id, event=None, stage=None):
    """Validate serialized execution claims mathematically, not source accuracy."""
    if not isinstance(row, dict) or row.get("indicator_id") != rule["indicator_id"]:
        raise ValueError("unknown_or_mismatched_card")
    if row.get("rule_version") != rule["rule_version"]:
        raise ValueError("instance_rule_version_mismatch")
    if row.get("source_grade_supported") is not False or row.get("original_grade") is not None:
        raise ValueError("unsupported_original_grade_claim")
    if row.get("status") not in ALLOWED_STATUS:
        raise ValueError("unknown_instance_status")
    if row.get("video_id") != video_id or not _count(row.get("person_track_id")):
        raise ValueError("instance_source_run_binding_mismatch")
    event_id = row.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("instance_event_id_missing")
    if event is not None and (event.get("event_id") != event_id
            or event.get("person_track_id") != row["person_track_id"]
            or event.get("event_code") != rule["event_code"]):
        raise ValueError("instance_event_binding_mismatch")
    window = row.get("window", {})
    if stage is not None:
        if window != stage["window"] or row["event_id"] != stage["annotation_id"]:
            raise ValueError("stage_instance_window_mismatch")
        if window.get("person_track_id") != row["person_track_id"]:
            raise ValueError("stage_instance_person_binding_mismatch")
        if _digest(window.get("video_sha256")) != _digest(video_sha256):
            raise ValueError("instance_video_digest_mismatch")
        if window.get("stage_id") != rule["stage_code"]:
            raise ValueError("instance_stage_binding_mismatch")
    declared_conditions = rule["evaluation"]["conditions"]
    conditions = row.get("conditions")
    if not isinstance(conditions, list):
        raise ValueError("instance_conditions_missing")
    if any(condition.get("metric") not in {spec["metric"] for spec in declared_conditions} for condition in conditions):
        raise ValueError("unknown_condition_metric")
    if row["status"] != "proxy_scored":
        if row.get("proxy_grade") is not None or row.get("score") is not None:
            raise ValueError("unscored_instance_has_grade")
        return
    if event is not None:
        bounds = row.get("evidence", {}).get("requested_window_ms", {})
        start, end = bounds.get("start_ms"), bounds.get("end_ms")
        if not _finite(start) or not _finite(end) or not event["start_ms"] <= start < end <= event["end_ms"]:
            raise ValueError("temporal_card_subwindow_binding_mismatch")
    if rule["evaluation"]["kind"] != "visual_proxy" or not conditions:
        raise ValueError("non_proxy_rule_claims_score")
    by_metric = {condition.get("metric"): condition for condition in conditions}
    if len(by_metric) != len(conditions) or set(by_metric) != {c["metric"] for c in declared_conditions}:
        raise ValueError("condition_inventory_mismatch")
    outcomes = []
    for spec in declared_conditions:
        actual = by_metric[spec["metric"]]
        threshold = spec.get("bounds", spec.get("value"))
        if (actual.get("operator") != spec["operator"] or actual.get("unit") != spec["unit"]
                or actual.get("threshold") != threshold or not _finite(actual.get("value"))):
            raise ValueError("condition_contract_mismatch")
        value, operator = actual["value"], spec["operator"]
        passed = {"gte": lambda: value >= threshold, "lte": lambda: value <= threshold,
                  "eq": lambda: value == threshold, "between": lambda: threshold[0] <= value <= threshold[1]}[operator]()
        if actual.get("passed") is not passed:
            raise ValueError("condition_pass_flag_mismatch")
        outcomes.append(passed)
    fraction = sum(outcomes) / len(outcomes)
    grade = "A" if fraction == 1 else "B" if fraction >= .75 else "C" if fraction >= .5 else "D" if fraction > 0 else "E"
    if (row.get("proxy_grade") != grade or not _same_number(row.get("score"), GRADE_POINTS[grade])
            or not _same_number(row.get("condition_completion_fraction"), fraction)):
        raise ValueError("proxy_grade_math_mismatch")
    quality = row.get("quality")
    if not isinstance(quality, dict):
        raise ValueError("scored_quality_missing")
    valid, total = quality.get("valid_frame_count"), quality.get("total_frame_count")
    required = set(rule["required_joint_names"])
    if (not _count(valid) or not _count(total) or total < 3 or valid > total or valid * 10 < total * 7
            or not required.issubset(quality.get("required_joint_names", []))
            or quality.get("visibility_verified") is not False
            or quality.get("geometry_gate_passed", quality.get("passes")) is not True):
        raise ValueError("scored_quality_gate_mismatch")
    if row.get("reason_codes"):
        raise ValueError("scored_instance_has_unresolved_reason")


def _add_instance(output, seen, row, rule, video_sha, case_id, route, pointer, *, stage=None, event=None):
    validate_instance(row, rule, video_sha256=video_sha, video_id=case_id, stage=stage, event=event)
    identity = (video_sha.lower(), row["event_id"], row["person_track_id"], row["indicator_id"])
    if identity in seen:
        raise ValueError("duplicate_evidence_instance")
    seen.add(identity)
    bounds = row.get("window") or row.get("evidence", {}).get("requested_window_ms") or {}
    output.append({"indicator_id": row["indicator_id"], "case_id": case_id, "video_sha256": video_sha.lower(),
        "event_id": row["event_id"], "person_track_id": row["person_track_id"], "route": route,
        "status": row["status"], "proxy_grade": row.get("proxy_grade"), "score": row.get("score"),
        "start_ms": bounds.get("start_ms"), "end_ms": bounds.get("end_ms"),
        "reason_codes": row.get("reason_codes", []), "result_pointer": pointer,
        "conditions": [{key: condition.get(key) for key in
                        ("metric", "value", "operator", "threshold", "unit", "passed")}
                       for condition in row.get("conditions", [])],
        "quality": deepcopy(row.get("quality")),
        "instance_digest_kind": CANONICAL, "instance_sha256": canonical_digest(row)})


def temporal_contract(root):
    # Inspection of literal registration is not a claim that the runtime ran.
    tree = ast.parse((root / "src/rallymate_scoring/temporal_card_evidence.py").read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"VERSION", "CARD_CONDITIONS"}:
                    values[target.id] = ast.literal_eval(node.value)
    if set(values) != {"VERSION", "CARD_CONDITIONS"} or len(values["CARD_CONDITIONS"]) != 5:
        raise ValueError("temporal_contract_registration_missing")
    return values["VERSION"], values["CARD_CONDITIONS"]


def validate_recognition_counts(metrics, annotations):
    """Verify counts, without treating estimated annotations as true contacts."""
    if not isinstance(metrics, dict):
        raise ValueError("recognition_summary_missing")
    matches, missed, false = metrics["matches"], metrics["missed_annotation_ids"], metrics["false_candidates"]
    annotation_ids = [row["id"] for row in annotations]
    matched_ids = [row["annotation_id"] for row in matches]
    # The phase-anchor report serializes unmatched candidates without an id;
    # their exact bounds still distinguish them. This is not card-score evidence.
    candidate_ids = [row.get("candidate_id") or (row.get("start_ms"), row.get("peak_ms"), row.get("end_ms"))
                     for row in matches + false]
    if (len(set(annotation_ids)) != len(annotation_ids) or len(set(matched_ids + missed)) != len(matched_ids + missed)
            or set(matched_ids + missed) != set(annotation_ids) or len(set(candidate_ids)) != len(candidate_ids)):
        raise ValueError("duplicate_or_unbound_recognition_instance")
    expected = {"annotation_count": len(annotations), "candidate_count": len(candidate_ids),
                "matched_count": len(matches), "missed_count": len(missed), "false_candidate_count": len(false),
                "recall": len(matches) / len(annotations) if annotations else None,
                "precision": len(matches) / len(candidate_ids) if candidate_ids else None,
                "unknown_classification_count": sum(row["classification_status"] == "unknown" for row in matches),
                "incompatible_wrist_side_count": sum(row["wrist_side_status"] == "incompatible" for row in matches)}
    for key, value in expected.items():
        _verify_equal(metrics.get(key), value, "recognition/" + key)
    by_type = {}
    for action in sorted({row["action_type"] for row in annotations}):
        n = sum(row["action_type"] == action for row in annotations)
        matched = sum(row["action_type"] == action for row in matches)
        by_type[action] = {"annotated": n, "matched": matched, "missed": n - matched, "recall": matched / n}
    _verify_equal(metrics["by_action_type"], by_type, "recognition/by_action_type")


def verify_source_artifacts(root, case, declared, *, include_events=False):
    required = {"frames.jsonl", "primary-player.jsonl", "summary.json"}
    if include_events:
        required.add("events.jsonl")
    if not isinstance(declared, dict) or set(declared) != required:
        raise ValueError("source_artifact_inventory_mismatch")
    directory = _safe_repo_path(root, case["run_dir"])
    if directory.name != case["id"]:
        raise ValueError("source_run_directory_binding_mismatch")
    source = _safe_repo_path(root, case["local_video"])
    expected_video = _digest(case["video_sha256"])
    if not source.is_file() or sha256(source) != expected_video:
        raise ValueError("source_video_artifact_mismatch")
    artifacts = {}
    for name, expected in declared.items():
        path = directory / name
        if not path.is_file() or sha256(path) != _digest(expected):
            raise ValueError("source_artifact_digest_mismatch:" + name)
        artifacts[name] = {"path": _relative(root, path), "sha256": _digest(expected), "kind": RAW}
    summary = read_json(directory / "summary.json")
    source_name = str(summary.get("input", {}).get("video_path", "")).replace("\\", "/")
    if not source_name.endswith("/" + _relative(root, source)):
        raise ValueError("source_summary_video_binding_mismatch")
    job_id = summary.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("source_job_id_missing")
    frames = {}
    hash_bound_frames = 0
    previous_index, previous_timestamp = -1, -math.inf
    with (directory / "frames.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            frame = row["frame"]
            index, timestamp = frame["index"], frame["timestamp_ms"]
            if row.get("job_id") != job_id:
                raise ValueError("source_frame_run_binding_mismatch")
            frame_digest = frame.get("video_sha256")
            if frame_digest is not None:
                if _digest(frame_digest) != expected_video:
                    raise ValueError("source_frame_video_binding_mismatch")
                hash_bound_frames += 1
            if (not _count(index) or not _finite(timestamp) or index <= previous_index
                    or timestamp <= previous_timestamp):
                raise ValueError("source_frame_identity_invalid")
            pts, numerator, denominator = (frame.get(key) for key in ("pts", "time_base_num", "time_base_den"))
            exact_pts_ms = None
            if all(type(value) is int for value in (pts, numerator, denominator)) and numerator > 0 and denominator > 0:
                exact_pts_ms = float(Fraction(pts * numerator * 1000, denominator))
            # Legacy source_timestamp_ms may be rounded; retain the rational
            # decoder PTS rather than reconstructing time from nominal FPS.
            frames[index] = {"timestamp_ms": timestamp, "source_timestamp_ms": exact_pts_ms}
            previous_index, previous_timestamp = index, timestamp
    if not frames:
        raise ValueError("source_frames_empty")
    events = {}
    if include_events:
        with (directory / "events.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
                key = (row["event_id"], row["person_track_id"])
                if key in events or row.get("provenance", {}).get("source_id") != case["id"]:
                    raise ValueError("source_event_run_binding_mismatch")
                events[key] = row
    return {"case_id": case["id"], "video_sha256": expected_video, "source_video_path": _relative(root, source),
            "run_dir": _relative(root, directory), "input_artifacts": artifacts,
            "source_frame_count": len(frames), "frame_embedded_video_digest_count": hash_bound_frames,
            "video_binding": "hashed_source_video_and_hashed_run_summary_path_with_job_id"}, frames, events


def validate_video_payload(report, manifest, rules):
    """Pure claim validation; filesystem/code provenance is checked separately."""
    if report.get("schema_version") != "card-video-validation-v1":
        raise ValueError("unknown_video_report_version")
    cases = {case["id"]: case for case in manifest["cases"] if "run_dir" in case and "local_video" in case}
    if len(cases) != sum("run_dir" in case and "local_video" in case for case in manifest["cases"]):
        raise ValueError("duplicate_manifest_case")
    reported = report.get("cases", [])
    if (not reported or len({case.get("case_id") for case in reported}) != len(reported)
            or {case.get("case_id") for case in reported} != set(cases)):
        raise ValueError("video_case_inventory_mismatch")
    output, seen = [], set()
    for case_index, case_report in enumerate(reported):
        case_id = case_report["case_id"]
        case = cases[case_id]
        if (case_report.get("status") != "replayed_real_observations"
                or _digest(case_report.get("source_video_sha256")) != _digest(case["video_sha256"])
                or case_report.get("split") != case["split"]):
            raise ValueError("video_case_source_binding_mismatch")
        annotations = case.get("annotations", [])
        for key in ("primary_window_metrics", "with_review_fallback_metrics", "estimated_phase_anchor_metrics"):
            if annotations:
                validate_recognition_counts(case_report.get(key), annotations)
            elif case_report.get(key) is not None:
                raise ValueError("unannotated_case_has_accuracy_claim")
        if annotations:
            _verify_equal(case_report["primary_candidate_count"], case_report["primary_window_metrics"]["candidate_count"], "primary_candidates")
            _verify_equal(case_report["primary_candidate_count"] + case_report["fallback_review_candidate_count"],
                          case_report["with_review_fallback_metrics"]["candidate_count"], "fallback_candidates")
        windows = {window["id"]: window for window in case.get("stage_windows", [])}
        stages = case_report.get("stage_measurements", [])
        if len(windows) != len(case.get("stage_windows", [])) or len(stages) != len(windows):
            raise ValueError("stage_window_inventory_mismatch")
        used = set()
        for stage_index, stage in enumerate(stages):
            identifier = stage.get("annotation_id")
            if identifier not in windows or identifier in used:
                raise ValueError("duplicate_or_unknown_stage_window")
            used.add(identifier)
            declared, window = windows[identifier], stage["window"]
            start, end = window.get("start_ms"), window.get("end_ms")
            binding = window.get("stage_binding", {})
            if (not _finite(start) or not _finite(end) or not declared["start_ms"] <= start < end <= declared["end_ms"]
                    or window.get("stage_id") != declared["stage_id"] or window.get("video_id") != case_id
                    or window.get("event_id") != identifier or binding.get("method") != "manual_anchor"
                    or binding.get("source") != case["annotation_origin"] or binding.get("version") != case["annotation_version"]):
                raise ValueError("stage_window_source_binding_mismatch")
            expected_ids = {identifier for identifier, rule in rules.items() if rule["stage_code"] == declared["stage_id"]}
            rows = stage.get("card_evaluations", [])
            if not expected_ids or len(rows) != len(expected_ids) or {row.get("indicator_id") for row in rows} != expected_ids:
                raise ValueError("unknown_or_missing_stage_card")
            for row_index, row in enumerate(rows):
                _add_instance(output, seen, row, rules[row["indicator_id"]], case["video_sha256"], case_id,
                    "explicit_estimated_gs_stage_replay", f"/cases/{case_index}/stage_measurements/{stage_index}/card_evaluations/{row_index}", stage=stage)
    return output, cases


def validate_temporal_payload(report, rules, events, case, temporal_specs):
    if report.get("version") != "card-execution-v1.0.0" or report.get("source_run_id") != case["id"]:
        raise ValueError("temporal_source_run_binding_mismatch")
    if report.get("rule_version") != next(iter(rules.values()))["rule_version"]:
        raise ValueError("temporal_rule_version_mismatch")
    cards = report.get("cards", [])
    if len(cards) != len(rules) or {card.get("indicator_id") for card in cards} != set(rules):
        raise ValueError("unknown_or_duplicate_temporal_card")
    observed = sorted({event["event_code"] for event in events.values()})
    _verify_equal(report["input"]["observed_event_codes"], observed, "observed_event_codes")
    _verify_equal(report["input"]["event_record_count"], len(events), "event_record_count")
    if report["input"].get("saved_grades_trusted") is not False or report.get("overall_score") is not None:
        raise ValueError("temporal_original_grade_or_saved_grade_claim")
    output, seen, raw_instances, statuses = [], set(), [], Counter()
    for card_index, card in enumerate(cards):
        rule = rules[card["indicator_id"]]
        if (card.get("event_code") != rule["event_code"] or card.get("rule_kind") != rule["evaluation"]["kind"]
                or card.get("original_grade") is not None or card.get("source_grade_supported") is not False):
            raise ValueError("temporal_card_contract_mismatch")
        instances = card.get("instances", [])
        _verify_equal(card.get("instance_count"), len(instances), "temporal/instance_count")
        _verify_equal(card.get("instance_status_counts"), dict(Counter(row["status"] for row in instances)), "temporal/instance_status_counts")
        expected_events = {key for key, event in events.items() if event["event_code"] == rule["event_code"]}
        if rule["indicator_id"] not in temporal_specs:
            if instances or card.get("runtime_engine") is not None:
                raise ValueError("unwired_temporal_card_claim")
        else:
            if card.get("runtime_engine") != rule["evaluation"].get("engine_version"):
                raise ValueError("temporal_engine_version_mismatch")
            if ({(row.get("event_id"), row.get("person_track_id")) for row in instances} != expected_events
                    or len(instances) != len(expected_events)):
                raise ValueError("temporal_event_instance_inventory_mismatch")
        expected_status = ("proxy_scored" if any(row["status"] == "proxy_scored" for row in instances) else "review_required") if instances else (
            "not_observed" if rule["event_code"] not in observed else
            "review_required" if rule["evaluation"]["kind"] == "manual_review" else "not_executed")
        if card.get("run_status") != expected_status:
            raise ValueError("temporal_card_run_status_mismatch")
        statuses[expected_status] += 1
        for row_index, row in enumerate(instances):
            if row.get("measurement_version") != rule["evaluation"].get("engine_version"):
                raise ValueError("temporal_instance_engine_version_mismatch")
            event = events.get((row.get("event_id"), row.get("person_track_id")))
            if event is None:
                raise ValueError("temporal_source_event_missing")
            _add_instance(output, seen, row, rule, case["video_sha256"], case["id"], "temporal_service",
                          f"/cards/{card_index}/instances/{row_index}", event=event)
            raw_instances.append(row)
    _verify_equal(report["summary"], summarize_card_scores(raw_instances, list(rules)), "temporal/summary")
    applicable = [key for key, rule in rules.items() if rule["event_code"] in observed]
    _verify_equal(report["observed_type_summary"], summarize_card_scores(raw_instances, applicable), "temporal/observed_summary")
    counts = {"source_card_count": len(rules), "fs_card_count": sum(key.startswith("FS") for key in rules),
              "gs_card_count": sum(key.startswith("GS") for key in rules), "wired_temporal_card_count": len(temporal_specs),
              "computed_card_instance_count": len(raw_instances), "run_card_status_counts": dict(statuses),
              "original_auto_graded_card_count": 0}
    for key, expected in counts.items():
        _verify_equal(report["coverage"].get(key), expected, "temporal/coverage/" + key)
    return output


def metric_registration(condition, identifier, temporal_specs):
    metric = condition["metric"]
    if identifier in temporal_specs:
        expected = [(name, operator, list(threshold) if isinstance(threshold, tuple) else threshold, unit)
                    for name, operator, threshold, unit in temporal_specs[identifier]]
        spec = (metric, condition["operator"], condition.get("bounds", condition.get("value")), condition["unit"])
        return "temporal_registered_condition" if spec in expected else "unregistered_condition"
    if metric in DYNAMIC_SPECS and DYNAMIC_SPECS[metric][1] == condition["unit"]:
        return "registered_dynamic_scalar"
    name, separator, reducer = metric.rpartition(".")
    if name in SPECS and separator and reducer in {"mean", "median", "range", "delta", "std", "min", "max"}:
        if SPECS[name][1] == condition["unit"]:
            return "registered_geometry_reducer"
    return "unregistered_condition"


def validate_temporal_frames(report, frames):
    for card in report["cards"]:
        for instance in card["instances"]:
            evidence = instance.get("evidence", {})
            source_indices = evidence.get("source_frames", [])
            timestamps = evidence.get("timestamps_ms", [])
            if not source_indices:
                if instance["status"] == "proxy_scored":
                    raise ValueError("scored_source_frames_missing")
                continue
            if (len(set(source_indices)) != len(source_indices) or len(timestamps) != len(source_indices)
                    or any(index not in frames for index in source_indices)
                    or any(b != a + 1 for a, b in zip(source_indices, source_indices[1:]))):
                raise ValueError("temporal_source_frame_window_mismatch")
            for index, timestamp in zip(source_indices, timestamps):
                if not _same_number(frames[index]["timestamp_ms"], timestamp):
                    raise ValueError("temporal_source_frame_time_mismatch")
            if instance["status"] == "proxy_scored":
                bounds = evidence["requested_window_ms"]
                expected_indices = [index for index, frame in frames.items()
                                    if bounds["start_ms"] <= frame["timestamp_ms"] <= bounds["end_ms"]]
                if source_indices != expected_indices:
                    raise ValueError("temporal_source_denominator_inventory_mismatch")
                actual_times = evidence.get("source_timestamps_ms", [])
                if len(actual_times) != len(source_indices) or evidence.get("time_basis") != "decoder_pts":
                    raise ValueError("temporal_pts_missing")
                for index, timestamp in zip(source_indices, actual_times):
                    if not _same_number(frames[index]["source_timestamp_ms"], timestamp):
                        raise ValueError("temporal_pts_mismatch")
                if instance["quality"]["total_frame_count"] != len(source_indices):
                    raise ValueError("temporal_denominator_mismatch")


def build_matrix(*, root, video_results, case_manifest, temporal_report, temporal_evidence):
    root = Path(root).resolve()
    paths = {key: Path(value).resolve() for key, value in {
        "video_results": video_results, "case_manifest": case_manifest,
        "temporal_report": temporal_report, "temporal_evidence": temporal_evidence}.items()}
    for path in paths.values():
        _relative(root, path)
    catalogue = read_json(root / RULE_PATH)
    rules = {rule["indicator_id"]: rule for rule in catalogue["cards"]}
    if len(rules) != 298 or len(catalogue["cards"]) != 298:
        raise ValueError("catalogue_card_inventory_mismatch")
    temporal_version, temporal_specs = temporal_contract(root)
    video, manifest, temporal, sidecar = (read_json(paths[key]) for key in
                                        ("video_results", "case_manifest", "temporal_report", "temporal_evidence"))
    if _digest(video.get("case_manifest_sha256")) != sha256(paths["case_manifest"]):
        if _digest(video.get("case_manifest_normalized_sha256")) != normalized_digest(paths["case_manifest"]):
            raise ValueError("case_manifest_digest_mismatch")
    if video.get("module_digest_kind") != RAW or video.get("normalized_digest_kind") != "utf8_lf_sha256":
        raise ValueError("unknown_video_digest_algorithm")
    video_digest = verify_catalogue_digest(video["module_sha256"]["data/card_rules_v1.json"],
        video.get("catalogue_digest_kind"), root / RULE_PATH, catalogue,
        video.get("module_normalized_sha256", {}).get("data/card_rules_v1.json"))
    required_video = {"stroke_candidates.py", "stroke_review_fallback.py", "temporal_recognition.py",
                      "card_rule_catalog.py", "card_kinematics.py", "card_evaluation.py", "data/card_rules_v1.json"}
    if not required_video.issubset(video["module_sha256"]):
        raise ValueError("video_runtime_dependency_missing")
    video_modules = verify_module_digests(root, video["module_sha256"], video.get("module_normalized_sha256"), prefix="src/rallymate_scoring/")
    producer = "scripts/replay_card_video_validation.py"
    producer_hashes = verify_module_digests(root, {producer: video["replay_script_sha256"]},
        {producer: video["replay_script_normalized_sha256"]} if video.get("replay_script_normalized_sha256") else {})
    video_modules.update(producer_hashes)
    video_instances, cases = validate_video_payload(video, manifest, rules)
    verified_cases, source_frames = {}, {}
    for case in video["cases"]:
        identifier = case["case_id"]
        verified, frames, _ = verify_source_artifacts(root, cases[identifier], case["input_artifact_sha256"])
        _verify_equal(case["diagnostics"]["frame_count"], len(frames), "video/decoded_frame_count")
        for stage in case.get("stage_measurements", []):
            window = stage["window"]
            in_window = [frame for frame in frames.values() if window["start_ms"] <= frame["timestamp_ms"] <= window["end_ms"]]
            if not in_window or in_window[0]["timestamp_ms"] != window["start_ms"] or in_window[-1]["timestamp_ms"] != window["end_ms"]:
                raise ValueError("video_stage_decoder_binding_mismatch")
            for row in stage["card_evaluations"]:
                if row["status"] == "proxy_scored" and row["quality"]["total_frame_count"] != len(in_window):
                    raise ValueError("video_stage_denominator_mismatch")
        verified_cases[identifier], source_frames[identifier] = verified, frames
    if _digest(sidecar.get("report_sha256")) != sha256(paths["temporal_report"]):
        raise ValueError("temporal_report_digest_mismatch")
    if sidecar.get("hash_algorithm") != "sha256_raw_bytes_v1" or sidecar.get("module_normalized_hash_algorithm") != "sha256_utf8_lf_normalized_v1":
        raise ValueError("unknown_temporal_digest_algorithm")
    run_id = temporal.get("source_run_id")
    if (run_id not in cases or sidecar.get("source_run_id") != run_id
            or _digest(sidecar.get("source_video_sha256")) != _digest(cases[run_id]["video_sha256"])):
        raise ValueError("temporal_source_run_binding_mismatch")
    temporal_digest = verify_catalogue_digest(temporal["input"]["catalogue_sha256"],
        temporal["input"].get("catalogue_digest_kind"), root / RULE_PATH, catalogue)
    verify_catalogue_digest(sidecar["catalogue_sha256"], sidecar.get("catalogue_digest_kind"), root / RULE_PATH, catalogue)
    required_temporal_modules = {"src/rallymate_service/card_execution.py", "src/rallymate_scoring/temporal_card_evidence.py",
        "src/rallymate_scoring/card_evaluation.py", "src/rallymate_scoring/quality_policy.py", "src/rallymate_scoring/card_rule_catalog.py",
        "src/rallymate_features/schemas.py", "src/rallymate_features/source_alignment.py", "src/rallymate_scoring/data/card_rules_v1.json"}
    if not required_temporal_modules.issubset(sidecar["module_sha256"]):
        raise ValueError("temporal_runtime_dependency_missing")
    temporal_modules = verify_module_digests(root, sidecar["module_sha256"], sidecar.get("module_normalized_sha256"))
    temporal_source, frames, events = verify_source_artifacts(root, cases[run_id], sidecar["input_artifact_sha256"], include_events=True)
    _verify_equal(temporal["input"]["source_frame_count"], len(frames), "temporal/source_frame_count")
    temporal_instances = validate_temporal_payload(temporal, rules, events, cases[run_id], temporal_specs)
    validate_temporal_frames(temporal, frames)
    for key in ("summary", "coverage"):
        if key in sidecar:
            _verify_equal(sidecar[key], temporal[key], "sidecar/" + key)
    if "per_card_instance_status_counts" in sidecar:
        expected = {card["indicator_id"]: card["instance_status_counts"] for card in temporal["cards"] if card["instances"]}
        _verify_equal(sidecar["per_card_instance_status_counts"], expected, "sidecar/per_card_instance_status_counts")
    instances = video_instances + temporal_instances
    identities = {(row["video_sha256"], row["event_id"], row["person_track_id"], row["indicator_id"]) for row in instances}
    if len(identities) != len(instances):
        raise ValueError("duplicate_cross_report_instance")
    by_card = defaultdict(list)
    for row in instances:
        by_card[row["indicator_id"]].append(row)
    rows = []
    for identifier, rule in rules.items():
        evaluation = rule["evaluation"]
        conditions = [{"metric": spec["metric"], "operator": spec["operator"], "threshold": spec.get("bounds", spec.get("value")),
            "unit": spec["unit"], "required_joint_names": spec["required_joint_names"],
            "registration": metric_registration(spec, identifier, temporal_specs)} for spec in evaluation["conditions"]]
        actual = by_card.get(identifier, [])
        statuses = dict(Counter(row["status"] for row in actual))
        has_proxy = statuses.get("proxy_scored", 0) > 0
        calculator_registered = bool(conditions) and all(row["registration"] != "unregistered_condition" for row in conditions)
        stage_evidence = bool(actual)
        if has_proxy:
            classification = "proxy_executed_on_real_observations"
        elif evaluation["kind"] == "manual_review":
            classification = "manual_review_required"
        elif stage_evidence:
            classification = "executed_without_proxy_grade"
        elif calculator_registered:
            classification = "calculator_registered_missing_stage_evidence"
        else:
            classification = "not_observed_unregistered_calculator"
        source = rule["source"]
        rows.append({"indicator_id": identifier, "event_code": rule["event_code"], "stage_code": rule["stage_code"],
            "rule_version": rule["rule_version"], "rule_sha256": canonical_digest(rule), "rule_digest_kind": CANONICAL,
            "source": {key: source[key] for key in ("document_id", "document_sha256", "table", "rubric_sha256")},
            "definition_anchors": source["fields"]["技术定义"]["locators"],
            "source_anchor_count": sum(len(value.get("locators", [])) for value in source["fields"].values()),
            "revision_ids": rule["revision_ids"], "original_automatic_grade_supported": False,
            "evaluation_kind": evaluation["kind"], "evaluation_family": evaluation["family"],
            "source_behavior_coverage": evaluation.get("source_behavior_coverage", "manual_review_required"),
            "grade_policy": evaluation.get("grade_policy"), "conditions": conditions,
            "required_engine": evaluation.get("required_engine", evaluation.get("engine")),
            "required_joint_names": rule["required_joint_names"], "dependencies": rule["dependencies"],
            "stage_binding": {"required": True, "subwindow_required": rule["stage_window"]["subwindow_required"],
                "origin": rule["stage_window"]["origin"], "real_evidence_available": stage_evidence,
                "automatic_landmark_validation_claimed": False},
            "calculator_registered": calculator_registered,
            "per_card_unit_execution_claimed": False,
            "temporal_service_wired": identifier in temporal_specs,
            "generic_stage_replay_observed": any(row["route"] == "explicit_estimated_gs_stage_replay" for row in actual),
            "classification": classification, "observation_status": "execution_recorded_in_bound_window" if actual else "not_observed",
            "instance_status_counts": statuses, "instance_count": len(actual),
            "real_video_count": len({row["video_sha256"] for row in actual}), "instances": actual})
    artifacts = {key: {"path": _relative(root, value), "sha256": sha256(value), "digest_kind": RAW} for key, value in paths.items()}
    return {"version": VERSION, "rule_version": catalogue["version"],
        "builder": {"path": "scripts/build_card_execution_matrix.py",
                    "raw_sha256": sha256(root / "scripts/build_card_execution_matrix.py"),
                    "normalized_sha256": normalized_digest(root / "scripts/build_card_execution_matrix.py"),
                    "normalization": NORMALIZED},
        "semantics": {"original_grade_verifier": None,
            "original_grade_verifier_scope": "complete_source_rubric_verifier_not_implemented",
            "matrix_scope": "verified_artifact_claims_and_registered_functions_not_accuracy_or_full_runtime_coverage",
            "geometry_registration_scope": "callable_scalar_inventory_not_per_card_execution_test",
            "phase_scope": "reviewer_estimated_gs_stages_and_detector_estimated_fs_subwindows_not_expert_ground_truth",
            "unit_fixture_instances_in_real_video_counts": 0,
            "missing_policy": "no_grade_no_zero_keep_in_coverage_denominator"},
        "catalogue": {"path": RULE_PATH.as_posix(), "raw_sha256": sha256(root / RULE_PATH),
            "canonical_sha256": canonical_digest(catalogue), "canonical_digest_kind": CANONICAL,
            "normalized_sha256": normalized_digest(root / RULE_PATH), "normalization": NORMALIZED},
        "evidence_artifacts": artifacts,
        "reproduction_arguments": {"--video-results": _relative(root, paths["video_results"]),
            "--case-manifest": _relative(root, paths["case_manifest"]),
            "--temporal-report": _relative(root, paths["temporal_report"]),
            "--temporal-evidence": _relative(root, paths["temporal_evidence"]),
            "--output": "docs/card-implementation-matrix-v1.json"},
        "execution_provenance": {"video_replay": {"catalogue_digest": video_digest, "modules": video_modules,
            "cases": list(verified_cases.values())}, "temporal_service": {"catalogue_digest": temporal_digest,
            "engine_version": temporal_version, "modules": temporal_modules, "case": temporal_source}},
        "counts": {"source_cards": len(rows), "original_automatically_graded_cards": 0,
            "specified_rule_kinds": dict(Counter(row["evaluation_kind"] for row in rows)),
            "classifications": dict(Counter(row["classification"] for row in rows)),
            "registered_calculator_cards": sum(row["calculator_registered"] for row in rows),
            "temporal_service_wired_cards": len(temporal_specs),
            "real_video_source_count": len({case["video_sha256"] for case in verified_cases.values()}),
            "real_video_sources_with_card_instances": len({row["video_sha256"] for row in instances}),
            "real_card_instance_count": len(instances), "real_instance_status_counts": dict(Counter(row["status"] for row in instances)),
            "real_proxy_scored_card_count": sum(row["instance_status_counts"].get("proxy_scored", 0) > 0 for row in rows),
            "real_executed_card_count": len(by_card), "not_observed_card_count": len(rows) - len(by_card),
            "stage_evidence_missing_card_count": sum(not row["stage_binding"]["real_evidence_available"] for row in rows)},
        "cards": rows}


def validate_matrix_invariants(matrix):
    """Recount the generated matrix; this cannot replace source-artifact checks."""
    rows = matrix["cards"]
    if len(rows) != 298 or len({row["indicator_id"] for row in rows}) != 298:
        raise ValueError("matrix_card_inventory_mismatch")
    instances = [instance for row in rows for instance in row["instances"]]
    identities = {(row["video_sha256"], row["event_id"], row["person_track_id"], row["indicator_id"]) for row in instances}
    if len(identities) != len(instances):
        raise ValueError("duplicate_matrix_instance")
    for row in rows:
        if any(instance["indicator_id"] != row["indicator_id"] for instance in row["instances"]):
            raise ValueError("matrix_instance_card_binding_mismatch")
        _verify_equal(row["instance_count"], len(row["instances"]), "matrix/card/instance_count")
        _verify_equal(row["instance_status_counts"], dict(Counter(instance["status"] for instance in row["instances"])), "matrix/card/status_counts")
    expected = {"source_cards": len(rows), "original_automatically_graded_cards": 0,
        "specified_rule_kinds": dict(Counter(row["evaluation_kind"] for row in rows)),
        "classifications": dict(Counter(row["classification"] for row in rows)),
        "registered_calculator_cards": sum(row["calculator_registered"] for row in rows),
        "temporal_service_wired_cards": sum(row["temporal_service_wired"] for row in rows),
        "real_video_source_count": len({case["video_sha256"] for case in matrix["execution_provenance"]["video_replay"]["cases"]}),
        "real_video_sources_with_card_instances": len({row["video_sha256"] for row in instances}),
        "real_card_instance_count": len(instances), "real_instance_status_counts": dict(Counter(row["status"] for row in instances)),
        "real_proxy_scored_card_count": sum(any(instance["status"] == "proxy_scored" for instance in row["instances"]) for row in rows),
        "real_executed_card_count": sum(bool(row["instances"]) for row in rows),
        "not_observed_card_count": sum(not row["instances"] for row in rows),
        "stage_evidence_missing_card_count": sum(not row["stage_binding"]["real_evidence_available"] for row in rows)}
    _verify_equal(matrix["counts"], expected, "matrix/counts")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-results", required=True, type=Path)
    parser.add_argument("--case-manifest", required=True, type=Path)
    parser.add_argument("--temporal-report", required=True, type=Path)
    parser.add_argument("--temporal-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_matrix(root=ROOT, video_results=args.video_results, case_manifest=args.case_manifest,
                              temporal_report=args.temporal_report, temporal_evidence=args.temporal_evidence)
        validate_matrix_invariants(result)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, "evidence_validation_failed: " + str(exc) + "\n")
    target = args.output.resolve()
    _relative(ROOT, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
