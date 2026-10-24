"""Recompute formal card executions in a committed archived runtime.

The replay command requires original media and immutable run artifacts. The build
command checks an already produced numeric bundle and its Git-source closure;
it does not claim to rerun videos. Synthetic/record-only reports are inadmissible.
The original nine execution groups and the added frozen MIT negative are kept
separate. This runner never promotes a previously serialized proxy/review row.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import uuid
import statistics
from types import SimpleNamespace

VERSION = "card-execution-matrix-v4.0.0"
BUNDLE_VERSION = "card-execution-replay-bundle-v4.0.0"
INPUT_VERSION = "card-execution-inputs-v4.0.0"
CATALOGUE = "src/rallymate_scoring/data/card_rules_v1.json"
RAW = "raw_file_sha256"
CANONICAL = "canonical_json_sorted_utf8_v1"
LF = "utf8_lf_sha256"
REQUIRED_ROUTES = {"temporal", "gs_preparation", "gs_acceleration", "footwork_cycle", "fs_card_windows"}
ROUTES = REQUIRED_ROUTES | {"gs_additional"}
STEP_MOTION_CARDS = {"FS02-M04", "FS07-M02", "FS08-M02", "FS08-M03"}
PRELOAD_CARDS = {"GS01-M01-03", "GS02-M01-04", "GS03-M01-03", "GS04-M01-03", "GS05-M01-03"}
STATUSES = {"scored", "unavailable"}
SCORING_POLICY = "automatic-formal-card-scoring-v1.0.0"
SCORE_SEMANTICS = "application_condition_completion_score"
GRADE_POINTS = {"A": 100, "B": 80, "C": 60, "D": 40, "E": 20}
SUMMARY_VERSION = "card-evaluation-v1.0.0"
BASELINE_COHORT = "v3_comparable_nine_groups"
ADDED_COHORT = "mit_fs07_frozen_development_negative"
RUNNER_DEPENDENCIES = ("scripts/build_card_execution_matrix_v4.py", "scripts/replay_gs_acceleration_validation.py")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def read_json(path):
    def unique(pairs):
        output = {}
        for key, value in pairs:
            require(key not in output, "duplicate_json_key")
            output[key] = value
        return output
    def reject(value):
        raise ValueError("nonfinite_json:" + value)
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=reject)


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def lf(data):
    return hashlib.sha256(data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def verify_text_artifact(path, raw_digest, normalized_digest, reason):
    # Retain the historical raw digest; only explicit LF-equivalence permits
    # another checkout's line endings. Semantic changes still fail.
    digest(raw_digest); digest(normalized_digest)
    require(lf(Path(path).read_bytes()) == normalized_digest, reason)
    return {"raw_matches_replay": sha(path) == raw_digest, "normalized_matches_replay": True}


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "invalid_sha256")
    return value


def relative(root, path):
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def local(root, name):
    require(isinstance(name, str) and name and not re.match(r"[A-Za-z]:|/|\\", name), "nonrelative_artifact_path")
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()), "artifact_outside_repository")
    return path


def write_json(path, value, *, compact=False):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
        separators=(",", ":") if compact else None, indent=None if compact else 2) + "\n", encoding="utf-8")


def git_blob(repository, commit, path):
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=repository)


def archive_runtime(repository, commit, checkout):
    resolved = subprocess.check_output(["git", "rev-parse", "--verify", commit + "^{commit}"], cwd=repository, text=True).strip()
    require(not checkout.exists(), "runtime_checkout_must_be_new")
    checkout.mkdir(parents=True)
    archive = subprocess.check_output(["git", "archive", resolved], cwd=repository)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(checkout, filter="data")
    verify_import_surface(repository, checkout, resolved)
    return resolved


def verify_import_surface(repository, checkout, commit):
    """Authenticate all possible source imports independently of a saved closure."""
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit, "src", *RUNNER_DEPENDENCIES],
                                    cwd=repository, text=True).splitlines()
    actual = {relative(checkout, path) for path in (checkout / "src").rglob("*")
              if path.is_file() and "__pycache__" not in path.parts}
    require(actual == {name for name in names if name.startswith("src/")}, "runtime_import_surface_inventory_mismatch")
    for name in names:
        path = local(checkout, name)
        require(path.is_file() and lf(path.read_bytes()) == lf(git_blob(repository, commit, name)),
                "archived_runtime_source_mismatch:" + name)


def load_runtime(checkout):
    require(not any(name.startswith("rallymate") for name in sys.modules), "runtime_already_imported")
    # Source hashes do not authenticate a stale .pyc. Use a fresh, never-written
    # cache namespace so verification always precedes loading the source bytes.
    cache = checkout / (".matrix-no-bytecode-" + uuid.uuid4().hex)
    require(not cache.exists(), "runtime_bytecode_namespace_exists")
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(cache)
    sys.path.insert(0, str(checkout / "src"))
    from rallymate_scoring.card_scoring_policy import SCORING_POLICY_VERSION, FORMAL_SCORE_SEMANTICS
    from rallymate_scoring.card_scoring_policy import GRADE_POINTS as runtime_points
    from rallymate_scoring.card_evaluation import VERSION as evaluation_version
    require(SCORING_POLICY_VERSION == SCORING_POLICY and FORMAL_SCORE_SEMANTICS == SCORE_SEMANTICS
            and runtime_points == GRADE_POINTS and evaluation_version == SUMMARY_VERSION,
            "unsupported_runtime_scoring_policy")
    return SimpleNamespace(validate_instance=validate_instance, summarize_card_scores=formal_summary)


def source_closure(repository, checkout, commit, extra_paths=()):
    paths = {Path(module.__file__).resolve() for name, module in list(sys.modules.items())
             if name.startswith("rallymate") and getattr(module, "__file__", None)}
    # All packaged JSON is included conservatively; this is not a claim that
    # every resource was read by every individual calculator.
    paths.update((checkout / "src").rglob("*.json"))
    paths.update(checkout / path for path in extra_paths)
    output = {}
    for path in sorted(paths):
        require(path.is_relative_to(checkout.resolve()), "runtime_import_escaped_clean_checkout")
        name = relative(checkout, path)
        raw, committed = path.read_bytes(), git_blob(repository, commit, name)
        require(lf(raw) == lf(committed), "runtime_source_differs_from_commit:" + name)
        output[name] = {"raw_sha256": hashlib.sha256(raw).hexdigest(), "utf8_lf_sha256": lf(raw),
                       "git_blob_sha256": hashlib.sha256(committed).hexdigest()}
    return {"commit": commit, "raw_digest_kind": RAW, "normalized_digest_kind": LF,
            "scope": "all_imported_rallymate_modules_all_packaged_json_and_named_runner_dependencies", "files": output}


def verify_closure(repository, closure):
    require(closure.get("raw_digest_kind") == RAW and closure.get("normalized_digest_kind") == LF,
            "unknown_source_digest_algorithm")
    files = closure.get("files", {})
    required = {CATALOGUE, "src/rallymate_service/card_execution.py", "src/rallymate_service/gs_card_execution.py",
                "src/rallymate_service/footwork_cycle_artifacts.py", "src/rallymate_scoring/footwork_cycle_card_evidence.py",
                "src/rallymate_service/fs_card_windows.py", "src/rallymate_scoring/gs_preload_evidence.py",
                "src/rallymate_scoring/step_motion_evidence.py", "src/rallymate_scoring/card_scoring_policy.py",
                "src/rallymate_scoring/card_evaluation.py", "src/rallymate_scoring/card_kinematics.py"}
    required.update(RUNNER_DEPENDENCIES)
    require(required.issubset(files), "incomplete_runtime_source_closure")
    for path, declared in files.items():
        local(repository, path)
        blob = git_blob(repository, closure["commit"], path)
        require(digest(declared["git_blob_sha256"]) == hashlib.sha256(blob).hexdigest()
                and digest(declared["utf8_lf_sha256"]) == lf(blob), "source_closure_digest_mismatch:" + path)
        digest(declared["raw_sha256"])


def verify_runtime_checkout(checkout, closure):
    """Check the files that will execute before importing any checkout code."""
    for name, declared in closure["files"].items():
        path = local(checkout, name)
        require(path.is_file() and lf(path.read_bytes()) == digest(declared["utf8_lf_sha256"]),
                "runtime_checkout_source_digest_mismatch:" + name)


def registry_snapshot(catalogue):
    from rallymate_scoring.card_kinematics import SPECS, DYNAMIC_SPECS
    from rallymate_scoring.temporal_card_evidence import CARD_CONDITIONS
    from rallymate_scoring.footwork_cycle_card_evidence import SUPPORTED_CODES
    from rallymate_service.gs_card_execution import RACKET_SIDE_VERSION
    from rallymate_service.fs_card_windows import supported_rules
    from rallymate_scoring.gs_preload_evidence import SUPPORTED_CARDS as runtime_preload_cards, VERSION as PRELOAD_VERSION
    require(set(runtime_preload_cards) == PRELOAD_CARDS, "preload_contract_inventory_mismatch")
    manual_fs = supported_rules()
    # These are explicit contract declarations, not behavior or video evidence.
    # Actual execution and successful source binding are counted only from rows.
    step_spec = importlib.util.find_spec("rallymate_scoring.step_motion_evidence")
    step_version = None
    if step_spec is not None:
        from rallymate_scoring import step_motion_evidence
        require(set(step_motion_evidence.SUPPORTED_CARDS) == STEP_MOTION_CARDS, "step_motion_contract_inventory_mismatch")
        step_version = step_motion_evidence.VERSION
    cycle_metrics = {"hip_displacement_torso", "trunk_tilt_deg.range", "hip_speed_drop_torso_s",
        "hip_inside_ankle_support_fraction.mean", "ankle_max_displacement_torso", "ankle_order_exchange_count",
        "ankle_width_contraction_torso", "ankle_width_expansion_torso", "left_knee_flexion_deg.range", "right_knee_flexion_deg.range"}
    output = {}
    for rule in catalogue["cards"]:
        identifier, evaluation = rule["indicator_id"], rule["evaluation"]
        route = "gs_manual_stage_adapter" if rule["event_code"].startswith("GS") else (
            "temporal_event_adapter" if identifier in CARD_CONDITIONS else
            "manual_cycle_adapter" if rule["event_code"] in SUPPORTED_CODES else
            "manual_card_window_adapter" if identifier in manual_fs else None)
        conditions = []
        for condition in evaluation["conditions"]:
            metric, unit = condition["metric"], condition["unit"]
            name, _, reducer = metric.rpartition(".")
            registered = None
            if identifier in CARD_CONDITIONS:
                spec = (metric, condition["operator"], condition.get("bounds", condition.get("value")), unit)
                expected = [(m, o, list(v) if isinstance(v, tuple) else v, u) for m, o, v, u in CARD_CONDITIONS[identifier]]
                registered = "temporal_card_condition" if spec in expected else None
            elif rule["event_code"] in SUPPORTED_CODES and metric in cycle_metrics:
                registered = "continuous_cycle_measurement"
            elif metric in DYNAMIC_SPECS and DYNAMIC_SPECS[metric][1] == unit:
                registered = "dynamic_scalar"
            elif name in SPECS and SPECS[name][1] == unit and reducer in {"mean", "median", "range", "delta", "std", "min", "max"}:
                registered = "geometry_reducer"
            conditions.append({"metric": metric, "registration": registered})
        blockers = []
        contracts = {}
        if any(c["metric"] == "wrist_speed_increase_torso_s" for c in conditions):
            contracts["manual_racket_side_observation"] = RACKET_SIDE_VERSION
        if identifier in PRELOAD_CARDS:
            contracts["manual_ungraded_local_preload_auxiliary"] = PRELOAD_VERSION
        elif evaluation.get("required_engine"):
            blockers.append(evaluation["required_engine"])
        if identifier in STEP_MOTION_CARDS:
            if step_version is None:
                blockers.append("complete_step_motion_cycle_input_not_wired_at_baseline")
            else:
                contracts["manual_source_step_motion_observation"] = step_version
        output[identifier] = {"adapter": route, "conditions": conditions,
            "proxy_rule_registered": evaluation["kind"] == "visual_proxy" and bool(conditions) and all(c["registration"] for c in conditions),
            "dependency_binding_gaps": blockers, "declared_dependency_contracts": contracts,
            "contract_declaration_is_not_behavior_validation": True}
    return output


def merge_racket_side(annotation, side_document, side_case, base_manifest):
    """Add a sampled-side observation without adding or extending an action."""
    require(side_document.get("base_stage_manifest_digest_kind") == CANONICAL
        and side_document.get("base_stage_manifest_sha256") == canonical(base_manifest), "racket_side_base_manifest_mismatch")
    observed = side_case["annotations"]
    for key in ("version", "source_run_id", "video_sha256", "input_artifact_sha256"):
        require(observed[key] == annotation[key], "racket_side_document_source_mismatch")
    actions = {row["action_id"]: row for row in annotation["actions"]}
    stages = {row["annotation_id"]: row for row in annotation["stages"]}
    require(len(actions) == len(annotation["actions"]) and len(stages) == len(annotation["stages"]), "duplicate_base_gs_annotation")
    require(len({a["action_id"] for a in observed["actions"]}) == len(observed["actions"]), "duplicate_racket_side_action")
    require(len({s["annotation_id"] for s in observed["stages"]}) == len(observed["stages"]), "duplicate_racket_side_stage")
    output = deepcopy(annotation)
    replacements = {}
    for row in observed["actions"]:
        require("racket_side_observation" in row, "racket_side_observation_missing")
        action = {key: value for key, value in row.items() if key != "racket_side_observation"}
        require(actions.get(row["action_id"]) == action, "racket_side_action_window_changed")
        replacements[row["action_id"]] = deepcopy(row)
    for stage in observed["stages"]:
        require(stages.get(stage["annotation_id"]) == stage, "racket_side_stage_window_changed")
    require({s["action_id"] for s in observed["stages"]} == set(replacements), "racket_side_action_stage_inventory_mismatch")
    output["actions"] = [replacements.get(row["action_id"], row) for row in output["actions"]]
    require(len(output["actions"]) == len(annotation["actions"]) and output["stages"] == annotation["stages"], "racket_side_added_instances")
    return output


def source_annotation(repository, entry, acceleration, *, verify_media=True):
    """Reconstruct the exact document from frozen sources, never saved grades."""
    if entry["route"] == "temporal":
        return None, []
    sources = []
    if entry["route"] == "gs_acceleration":
        name = entry["annotation_manifest"]
        frozen = read_json(local(repository, name))
        cases = [c for c in frozen["cases"] if c["case_id"] == entry["annotation_case_id"]]
        require(len(cases) == 1, "gs_acceleration_annotation_case_missing_or_duplicate")
        if verify_media:
            acceleration.verify_review_artifacts(cases[0], repository)
        annotation = acceleration.build_annotations(cases[0], frozen["annotation_version"])
        sources.append(name)
        if entry.get("racket_side_manifest"):
            side_name = entry["racket_side_manifest"]
            side = read_json(local(repository, side_name))
            selected = [c for c in side["cases"] if c["case_id"] == entry["racket_side_case_id"]]
            require(len(selected) == 1, "racket_side_annotation_case_missing_or_duplicate")
            case = selected[0]
            require(all(case[key] == entry[key] for key in ("source_run_id", "run_dir", "local_video", "video_sha256")), "racket_side_source_case_mismatch")
            if verify_media:
                require(sha(local(repository, case["source_frame_image"])) == case["source_frame_image_sha256"], "racket_side_original_image_changed")
            annotation = merge_racket_side(annotation, side, case, frozen)
            sources.append(side_name)
    elif entry.get("annotation_fixture"):
        name = entry["annotation_fixture"]
        frozen = read_json(local(repository, name))
        require(entry["route"] == "fs_card_windows" and frozen.get("version") in {"fs-card-real-usta-manual-v1", "fs-step-real-mit-development-v1.0.0"},
                "unknown_real_annotation_fixture")
        verify_fs_fixture(frozen, repository, verify_media=verify_media)
        annotation = deepcopy(frozen["annotation_document"])
        sources.append(name)
    else:
        name = entry["annotation_path"]
        annotation = read_json(local(repository, name))
        sources.append(name)
    require(annotation["source_run_id"] == entry["source_run_id"] and annotation["video_sha256"] == entry["video_sha256"],
            "annotation_declared_source_mismatch")
    return annotation, sources


def inventory_sources(repository, manifests, *, verify_media=True):
    by_hash = {}
    for name in manifests:
        manifest = read_json(local(repository, name))
        for case in manifest["cases"]:
            if not case.get("local_video"):
                continue
            video_hash = digest(case["video_sha256"].lower())
            media = local(repository, case["local_video"])
            if verify_media:
                require(media.is_file() and sha(media) == video_hash, "inventory_media_digest_mismatch")
            row = by_hash.setdefault(video_hash, {"video_sha256": video_hash, "case_ids": [], "source_urls": [], "manifest_paths": []})
            row["case_ids"].append(case["id"])
            if case.get("source_url") and case["source_url"] not in row["source_urls"]:
                row["source_urls"].append(case["source_url"])
            if name not in row["manifest_paths"]:
                row["manifest_paths"].append(name)
    return sorted(by_hash.values(), key=lambda row: row["video_sha256"])


def validate_rows(rows, rules, entry, validator):
    require(rows, "empty_execution_result")
    seen = set()
    for row in rows:
        identifier = row.get("indicator_id")
        require(identifier in rules, "unknown_card")
        require(row.get("video_id") == entry["source_run_id"], "instance_source_run_mismatch")
        require(row.get("status") in STATUSES, "unknown_instance_status")
        validator.validate_instance(row, rules[identifier], video_sha256=entry["video_sha256"], video_id=entry["source_run_id"])
        validate_condition_measurements(row, rules[identifier])
        identity = (row["event_id"], row["person_track_id"], identifier)
        require(identity not in seen, "duplicate_report_instance")
        seen.add(identity)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def nonnegative_int(value):
    return type(value) is int and value >= 0


def video_digest(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None, "invalid_source_video_digest")
    return value.lower()


def validate_instance(row, rule, *, video_sha256, video_id):
    """Check formal arithmetic independently; never promote serialized results."""
    require(row.get("indicator_id") == rule["indicator_id"] and row.get("rule_version") == rule["rule_version"],
            "instance_rule_binding_mismatch")
    require(row.get("scoring_policy_version") == SCORING_POLICY and row.get("status") in STATUSES,
            "instance_scoring_policy_mismatch")
    require(row.get("original_grade") is None and row.get("source_grade_supported") is False,
            "unsupported_original_grade_claim")
    require(row.get("video_id") == video_id and nonnegative_int(row.get("person_track_id"))
            and isinstance(row.get("event_id"), str) and bool(row["event_id"]), "instance_source_run_binding_mismatch")
    conditions = row.get("conditions")
    require(isinstance(conditions, list) and all(isinstance(c, dict) for c in conditions), "instance_conditions_missing")
    specs = {c["metric"]: c for c in rule["evaluation"]["conditions"]}
    require(all(c.get("metric") in specs for c in conditions), "unknown_condition_metric")
    require(isinstance(row.get("reason_codes"), list) and all(isinstance(r, str) and r for r in row["reason_codes"]),
            "invalid_reason_codes")
    validate_condition_measurements(row, rule)
    if row["status"] == "unavailable":
        require(all(row.get(k) is None for k in ("grade", "proxy_grade", "score", "condition_completion_fraction"))
                and all(c.get("passed") is None for c in conditions), "unscored_instance_has_grade")
        return
    require(row.get("semantics") == SCORE_SEMANTICS and not row["reason_codes"], "formal_score_semantics_or_reason_mismatch")
    require(conditions and {c["metric"] for c in conditions} == set(specs), "condition_inventory_mismatch")
    require(all(finite(c.get("value")) and type(c.get("passed")) is bool for c in conditions), "scored_condition_missing")
    fraction = sum(c["passed"] for c in conditions) / len(conditions)
    grade = "A" if fraction == 1 else "B" if fraction >= .75 else "C" if fraction >= .5 else "D" if fraction > 0 else "E"
    require(row.get("grade") == row.get("proxy_grade") == grade and finite(row.get("score"))
            and row["score"] == GRADE_POINTS[grade] and finite(row.get("condition_completion_fraction"))
            and row["condition_completion_fraction"] == fraction, "formal_grade_math_mismatch")
    quality = row.get("quality") or {}
    valid, total = quality.get("valid_frame_count"), quality.get("total_frame_count")
    require(nonnegative_int(valid) and nonnegative_int(total) and total >= 3 and valid <= total
            and valid * 10 >= total * 7 and set(rule["required_joint_names"]).issubset(quality.get("required_joint_names", []))
            and quality.get("visibility_verified") is False
            and quality.get("geometry_gate_passed", quality.get("passes")) is True, "scored_quality_gate_mismatch")


def formal_summary(rows, applicable_ids):
    """Independent equal-card aggregation, after averaging events within cards."""
    require(len(applicable_ids) == len(set(applicable_ids)), "duplicate_applicable_card")
    seen, values = set(), defaultdict(list)
    for row in rows:
        key = (row["indicator_id"], row["video_id"], row["event_id"], row["person_track_id"])
        require(key not in seen and row["indicator_id"] in applicable_ids, "duplicate_or_outside_summary_instance")
        require(row.get("scoring_policy_version") == SCORING_POLICY and row["status"] in STATUSES,
                "summary_nonformal_instance")
        seen.add(key)
        if row["status"] == "scored":
            require(row.get("semantics") == SCORE_SEMANTICS and row.get("grade") in GRADE_POINTS
                    and finite(row.get("score")) and row["score"] == GRADE_POINTS[row["grade"]], "summary_invalid_score")
            values[row["indicator_id"]].append(row["score"])
    per_card = {key: statistics.fmean(v) for key, v in sorted(values.items())}
    empty = {"score": None, "scored_cards": 0, "applicable_cards": len(applicable_ids),
             "coverage": 0.0 if applicable_ids else None, "scored_event_instances": 0, "per_card": {}}
    formal = {**empty, "score": statistics.fmean(per_card.values()) if per_card else None,
              "scored_cards": len(per_card), "coverage": len(per_card) / len(applicable_ids) if applicable_ids else None,
              "scored_event_instances": sum(map(len, values.values())), "per_card": per_card}
    return {"version": SUMMARY_VERSION, "grade_points": dict(GRADE_POINTS),
        "channels": {**{key: deepcopy(empty) for key in ("original", "visual_proxy", "manual")}, "formal": formal},
        "status_counts": dict(Counter(row["status"] for row in rows)), "conflicting_event_instances": 0,
        "overall_score": formal["score"], "missing_policy": "exclude_from_score_keep_in_applicable_coverage",
        "weight_policy": "equal_card_weight_after_event_mean", "uncertainty": "engineering_proxy_thresholds_uncalibrated_no_accuracy_claim",
        "scoring_policy_version": SCORING_POLICY, "formal_scored_cards": len(per_card),
        "formal_scored_instances": formal["scored_event_instances"]}


def exact_time(source):
    require(all(type(source.get(k)) is int for k in ("pts", "time_base_num", "time_base_den"))
            and source["time_base_num"] > 0 and source["time_base_den"] > 0, "source_pts_invalid")
    return Fraction(source["pts"] * source["time_base_num"] * 1000, source["time_base_den"])


def validate_source_ledger(row, group):
    """Check original frame denominators, clocks, primary and model namespaces.

    GS/FS sample adapters expose their derived primary continuity ledger.
    Temporal/cycle engines have separate source contracts; never invent that
    sample-adapter counter for those routes. Failed attempts retain failures.
    """
    route, scored = group["route"], row["status"] == "scored"
    if route.startswith("gs_") or route == "fs_card_windows":
        proof = row.get("source_stage_evidence" if route.startswith("gs_") else "source_window_evidence", {})
        frames, bindings, pts = proof.get("source_frames", []), proof.get("source_frame_bindings", []), proof.get("source_pts", [])
        require(frames and frames == list(range(frames[0], frames[-1] + 1))
                and all(nonnegative_int(f) for f in frames), "source_original_frame_denominator_missing")
        require([b.get("frame_index") for b in bindings] == frames and [p.get("frame_index") for p in pts] == frames
                and proof.get("interpolation_used") is False, "source_frame_ledger_inventory_mismatch")
        window = row["window"]
        require(nonnegative_int(window.get("derived_selection_epoch"))
                and all(nonnegative_int(b.get("derived_selection_epoch")) for b in bindings), "derived_ledger_missing")
        times = []
        model_epochs = set()
        for bound, source in zip(bindings, pts):
            require(bound.get("video_sha256") == group["video_sha256"]
                    and all(bound.get(k) == source.get(k) for k in ("pts", "time_base_num", "time_base_den")), "source_pts_or_video_mismatch")
            timestamp = exact_time(source); times.append(timestamp)
            require(finite(bound.get("timestamp_ms")) and abs(Fraction(str(bound["timestamp_ms"])) - timestamp) <= Fraction(51, 100),
                    "source_timestamp_pts_mismatch")
            if scored:
                require(bound.get("selected_person_track_id") == row["person_track_id"]
                        and all(nonnegative_int(bound.get(k)) and bound[k] == window.get(k)
                                for k in ("selection_epoch", "camera_reference_epoch", "derived_selection_epoch")), "source_selection_epoch_mismatch")
                require(bound.get("rejected_model_person_track_id") != row["person_track_id"], "rejected_model_cannot_score")
            if "source_model_selection_epoch" in bound or "model_epoch_semantics" in bound:
                require(nonnegative_int(bound.get("source_model_selection_epoch"))
                        and bound.get("model_epoch_semantics") == "detector_track_epoch_primary_selection_applied_downstream",
                        "source_model_epoch_marker_invalid")
                model_epochs.add(bound["source_model_selection_epoch"])
        require(all(a < b for a, b in zip(times, times[1:])), "source_clock_not_increasing")
        if scored:
            require(len(model_epochs) <= 1, "source_model_epoch_changed")
            require(all(Fraction(str(window["start_ms"])) - Fraction(51, 100) <= t <= Fraction(str(window["end_ms"])) + Fraction(51, 100)
                        for t in times), "source_frame_outside_window")
        # FS step evidence carries the original raw model binding alongside the
        # primary namespace. Caller markers never replace that original proof.
        geometry = proof.get("step_motion", {}).get("source_geometry_series", [])
        by_frame = {b["frame_index"]: b for b in bindings}
        for sample in geometry:
            if sample.get("observation_semantics") != "monocular_model_estimate":
                continue
            original = sample.get("source_binding", {})
            source = by_frame.get(sample.get("frame_index"), {})
            require(sample.get("pose_source") == "sam3d" and video_digest(original.get("video_sha256")) == group["video_sha256"]
                    and original.get("person_track_id") == source.get("selected_person_track_id")
                    and all(original.get(k) == source.get(k) for k in ("frame_index", "pts", "time_base_num", "time_base_den", "camera_reference_epoch")),
                    "step_model_source_binding_mismatch")
            require(nonnegative_int(original.get("selection_epoch"))
                    and original["selection_epoch"] == sample.get("source_model_selection_epoch") == source.get("source_model_selection_epoch")
                    and sample.get("selection_epoch") == source.get("selection_epoch"), "step_model_namespace_mismatch")
    elif route == "temporal":
        evidence = row.get("evidence", row.get("evidence_binding", {}))
        blocks = [evidence] + [v for v in evidence.values() if isinstance(v, dict) and "source_frame_bindings" in v]
        for block in blocks:
            frames, bindings = block.get("source_frames", []), block.get("source_frame_bindings", [])
            if not frames:
                require(not scored or block is not evidence, "scored_temporal_window_missing")
                continue
            if not scored and not bindings:
                continue  # Failed phase/geometry location did not assert a bound window.
            require(block.get("source_frame_binding_version") == "temporal-source-frame-bindings-v1.0.0"
                    and [b.get("frame_index") for b in bindings] == frames, "temporal_source_ledger_inventory_mismatch")
            if scored:
                raw_ids = block.get("source_track_ids")
                require(isinstance(raw_ids, list) and len(raw_ids) == 1 and nonnegative_int(raw_ids[0]), "temporal_raw_subject_missing")
                require(frames == list(range(frames[0], frames[-1] + 1)), "temporal_source_frame_gap")
                exact_times, video_times, primary_epochs = [], [], []
                for binding in bindings:
                    require(binding.get("status") == "verified_decoder_binding" and binding.get("video_sha256") == group["video_sha256"],
                            "temporal_decoder_binding_missing")
                    timestamp = exact_time(binding)
                    exact_times.append(timestamp)
                    require(finite(binding.get("source_timestamp_ms"))
                            and abs(Fraction(str(binding["source_timestamp_ms"])) - timestamp) <= Fraction(51, 100), "temporal_source_pts_mismatch")
                    require(finite(binding.get("video_timestamp_ms")), "temporal_video_clock_missing")
                    video_times.append(Fraction(str(binding["video_timestamp_ms"])))
                    selection = binding.get("primary_selection") or {}
                    require(selection.get("selection_status") == "selected" and selection.get("source_track_id") == raw_ids[0]
                            and nonnegative_int(selection.get("primary_player_id"))
                            and selection["primary_player_id"] == binding.get("primary_player_id") == row["person_track_id"],
                            "temporal_original_selection_mismatch")
                    require(all(nonnegative_int(binding.get(k)) for k in ("selection_epoch", "camera_reference_epoch", "person_track_id"))
                            and binding["person_track_id"] == raw_ids[0], "temporal_source_identity_epoch_invalid")
                    if "selection_epoch" in selection:
                        require(nonnegative_int(selection["selection_epoch"]), "temporal_primary_epoch_invalid")
                        primary_epochs.append(selection["selection_epoch"])
                    model = binding.get("model_geometry_source")
                    if model is not None:
                        original = model.get("source_binding", {})
                        require(model.get("pose_source") == "sam3d" and original.get("person_track_id") == raw_ids[0]
                                and video_digest(original.get("video_sha256")) == binding.get("video_sha256")
                                and all(type(original.get(k)) is int and original.get(k) == binding.get(k) for k in
                                    ("frame_index", "processed_index", "pts", "time_base_num", "time_base_den", "selection_epoch", "camera_reference_epoch")),
                                "temporal_model_source_binding_mismatch")
                require(all(0 < b - a <= 160 for a, b in zip(exact_times, exact_times[1:]))
                        and all(a < b for a, b in zip(video_times, video_times[1:])), "temporal_source_clock_not_increasing")
                offsets = [a - b for a, b in zip(exact_times, video_times)]
                require(max(offsets) - min(offsets) <= Fraction(102, 100), "temporal_source_video_clock_offset_changed")
                require(all(len({b[k] for b in bindings}) == 1 for k in ("selection_epoch", "camera_reference_epoch"))
                        and len(set(primary_epochs)) <= 1, "temporal_source_epoch_changed")
                if block.get("timestamps_ms") is not None:
                    require(block["timestamps_ms"] == [b["video_timestamp_ms"] for b in bindings], "temporal_video_timestamp_inventory_mismatch")
    elif route == "footwork_cycle":
        evidence = row.get("evidence", row.get("evidence_binding", {}))
        parent = evidence.get("step_window_binding", {})
        first, last = parent.get("start_source_frame"), parent.get("end_source_frame")
        require(nonnegative_int(first) and nonnegative_int(last) and first < last, "cycle_original_window_missing")
        frames = list(range(first, last + 1))
        bindings = evidence.get("source_frame_bindings", [])
        require(evidence.get("source_frames") == frames and [b.get("frame_index") for b in bindings] == frames,
                "cycle_source_ledger_inventory_mismatch")
        times = []
        for binding in bindings:
            if binding.get("status") != "verified_decoder_binding":
                require(not scored, "cycle_decoder_binding_missing")
                continue
            require(binding.get("video_sha256") == group["video_sha256"], "cycle_video_binding_mismatch")
            timestamp = exact_time(binding); times.append(timestamp)
            require(finite(binding.get("source_timestamp_ms"))
                    and abs(Fraction(str(binding["source_timestamp_ms"])) - timestamp) <= Fraction(51, 100), "cycle_source_pts_mismatch")
            if scored:
                require(binding.get("primary_player_id") == row["person_track_id"], "cycle_primary_binding_mismatch")
        require(all(a < b for a, b in zip(times, times[1:])), "cycle_source_clock_not_increasing")
        if scored:
            require(all(len({b.get(k) for b in bindings}) == 1 for k in
                        ("person_track_id", "selection_epoch", "camera_reference_epoch")), "cycle_epoch_or_subject_changed")


def verify_fs_fixture(frozen, repository, *, verify_media):
    if frozen["version"] != "fs-step-real-mit-development-v1.0.0":
        return
    visual, annotation = frozen["visual_observation"], frozen["annotation_document"]
    require(frozen.get("dataset_role") == "previously_observed_development_not_independent_validation"
            and visual.get("visual_review_only_before_this_freeze") is True
            and visual.get("new_step_motion_numeric_output_read_or_executed_before_freeze") is False,
            "mit_step_freeze_not_independent_of_numeric_selection")
    for name, value in (("visual-freeze.json", visual), ("fs-card-window-annotations.json", annotation)):
        lock = frozen["freeze_lock"]["files"][name]
        require(canonical(value) == lock["canonical_sha256"], "mit_step_visual_annotation_changed")
    window, = annotation["windows"]
    require(window["indicator_id"] == "FS07-M02" and [window["start_source_frame"], window["end_source_frame"]] == [29, 38]
            and window["step_motion_observation"]["moving_foot"] == "left", "mit_step_frozen_window_changed")
    if verify_media:
        for frame in visual["source_frames"]:
            require(sha(local(repository, frame["original_png"])) == frame["original_png_sha256"], "mit_step_original_image_changed")
        require(sha(local(repository, visual["source_extraction_ledger"])) == visual["source_extraction_ledger_sha256"],
                "mit_step_extraction_ledger_changed")


def validate_input_manifest(repository, manifest, *, verify_sources=True):
    require(manifest.get("version") == INPUT_VERSION and manifest.get("scoring_policy_version") == SCORING_POLICY,
            "unknown_input_manifest_or_policy_version")
    baseline = manifest["baseline_manifest"]
    verify_text_artifact(local(repository, baseline["path"]), baseline["raw_sha256"], baseline["utf8_lf_sha256"], "baseline_manifest_changed")
    original = read_json(local(repository, baseline["path"]))
    entries = manifest["executions"]
    require(len(entries) == 10 and len({e["id"] for e in entries}) == 10, "execution_inventory_not_ten")
    before = {e["id"]: e for e in entries if e.get("cohort") == BASELINE_COHORT}
    require(len(before) == 9 and set(before) == {e["id"] for e in original["executions"]}, "nine_group_comparison_scope_changed")
    for entry in original["executions"]:
        require(all(before[entry["id"]].get(k) == v for k, v in entry.items()), "baseline_execution_binding_changed")
    added = [e for e in entries if e.get("cohort") == ADDED_COHORT]
    require(len(added) == 1 and added[0]["route"] == "fs_card_windows"
            and added[0]["annotation_fixture"] == "tests/fixtures/fs-step-real-mit-development-v1.json",
            "added_mit_step_scope_changed")
    require(added[0]["video_sha256"] in {e["video_sha256"] for e in before.values()}, "added_step_is_not_existing_video")
    for entry in entries:
        names = {"frames.jsonl", "primary-player.jsonl", "summary.json"}
        if entry["route"] in {"temporal", "footwork_cycle", "fs_card_windows"}: names.add("events.jsonl")
        require(set(entry.get("input_artifact_sha256", {})) == names, "frozen_raw_input_inventory_missing")
        for value in entry["input_artifact_sha256"].values(): digest(value)
        paths = [entry[k] for k in ("annotation_path", "annotation_manifest", "annotation_fixture", "racket_side_manifest") if entry.get(k)]
        require(set(paths) == set(entry.get("annotation_source_digests", {})), "annotation_digest_inventory_missing")
        for name in paths:
            declared = entry["annotation_source_digests"][name]
            digest(declared["raw_sha256"]); digest(declared["utf8_lf_sha256"])
            if verify_sources:
                verify_text_artifact(local(repository, name), declared["raw_sha256"], declared["utf8_lf_sha256"], "frozen_annotation_changed")


def validate_condition_measurements(row, rule):
    """Check measured conditions even when a quality gate withholds the grade."""
    specs = {condition["metric"]: condition for condition in rule["evaluation"]["conditions"]}
    conditions = row["conditions"]
    require(len({condition["metric"] for condition in conditions}) == len(conditions), "duplicate_condition_metric")
    for condition in conditions:
        spec = specs[condition["metric"]]
        threshold = spec.get("bounds", spec.get("value"))
        actual_threshold = condition.get("threshold")
        threshold_matches = (isinstance(actual_threshold, list) and len(actual_threshold) == len(threshold)
            and all(finite(value) and value == expected for value, expected in zip(actual_threshold, threshold))) \
            if isinstance(threshold, list) else finite(actual_threshold) and actual_threshold == threshold
        require(condition.get("operator") == spec["operator"] and condition.get("unit") == spec["unit"]
                and threshold_matches, "condition_contract_mismatch")
        value = condition.get("value")
        if value is None:
            require(condition.get("passed") is None, "missing_condition_cannot_pass_or_fail")
            continue
        require(type(value) in (float, int) and math.isfinite(value), "condition_value_nonfinite_or_non_numeric")
        if condition.get("passed") is None:
            require(row["status"] != "scored" and row.get("reason_codes"), "condition_outcome_missing_without_block")
            continue
        operator = spec["operator"]
        passed = {"gte": lambda: value >= threshold, "lte": lambda: value <= threshold,
                  "eq": lambda: value == threshold, "between": lambda: threshold[0] <= value <= threshold[1]}[operator]()
        require(condition.get("passed") is passed, "condition_pass_flag_mismatch")


def source_interval_identity(group, row):
    """Deduplicate the same physical card window, even if its label was changed."""
    evidence = row.get("source_window_evidence", {})
    frames = evidence.get("source_frames") or row.get("evidence_binding", {}).get("source_frames")
    bounds = (row.get("window") or row.get("evidence_binding", {}).get("requested_window_ms") or
              row.get("source_window_annotation", {}).get("requested_window_ms") or {})
    start, end = bounds.get("start_ms"), bounds.get("end_ms")
    if frames:
        require(all(isinstance(x, int) and not isinstance(x, bool) for x in frames)
                and frames == sorted(set(frames)), "invalid_source_interval_frames")
        # Actual source-frame boundaries survive harmless rounding changes in
        # manual millisecond labels; missing interior geometry adds no sample.
        interval = ("frames", frames[0], frames[-1])
    elif isinstance(start, (int, float)) and isinstance(end, (int, float)):
        require(not isinstance(start, bool) and not isinstance(end, bool) and
                math.isfinite(start) and math.isfinite(end) and start <= end, "invalid_source_interval")
        interval = ("ms", start, end)
    else:
        require(group["route"] == "temporal" and row["status"] != "scored", "source_interval_evidence_missing")
        # The adapter keeps failed phase-location attempts as ungraded rows.
        # No physical subwindow is asserted or invented for those attempts.
        interval = ("unlocated_event_attempt", row["event_id"])
    return group["video_sha256"], row["person_track_id"], row["indicator_id"], interval


def validate_auxiliary(row, rule, group):
    auxiliary = row.get("auxiliary_evidence")
    if auxiliary is None:
        return
    require(row.get("indicator_id") in PRELOAD_CARDS and rule["evaluation"]["kind"] == "manual_review" and row["status"] != "scored"
        and auxiliary.get("status") in {"measured_auxiliary", "unavailable"}
        and all(auxiliary.get(key) is None for key in ("original_grade", "proxy_grade", "score"))
        and auxiliary.get("source_grade_supported") is False and auxiliary.get("split_step_confirmed") is False,
        "manual_auxiliary_cannot_authorize_grade")
    require(all(observation.get("passed") is None and observation.get("scoring_eligible") is False
        for observation in auxiliary.get("auxiliary_observations", [])), "auxiliary_observation_claims_grade")
    annotation = auxiliary.get("source_annotation", {})
    require(annotation.get("document_sha256") == group["annotation_canonical_sha256"],
            "auxiliary_annotation_binding_mismatch")


def validate_racket_binding(row, rule, group):
    binding = row.get("window", {}).get("racket_side_binding")
    if binding is None:
        return
    require(binding == row.get("source_annotation", {}).get("racket_side_observation")
        and binding.get("document_sha256") == group["annotation_canonical_sha256"]
        and binding.get("digest_kind") == CANONICAL and binding.get("evidence_scope") == "sampled_frames_only"
        and binding.get("expert_adjudicated") is False and binding.get("dominant_hand_established") is False
        and binding.get("continuous_racket_hold_established") is False,
        "racket_side_source_proof_mismatch")
    frames, sources = binding.get("source_frames", []), binding.get("source_frame_bindings", [])
    require(frames and len(frames) == len(sources), "racket_side_source_inventory_missing")
    for observed, source in zip(frames, sources):
        require(all(observed.get(key) == source.get(key) for key in ("frame_index", "pts", "time_base_num", "time_base_den"))
            and source.get("person_track_id") == row["person_track_id"]
            and source.get("video_sha256") == group["video_sha256"], "racket_side_source_frame_binding_mismatch")
        require(all(type(source.get(key)) is int for key in ("frame_index", "pts", "time_base_num", "time_base_den"))
                and source["time_base_num"] > 0 and source["time_base_den"] > 0, "racket_side_source_pts_invalid")
        exact = Fraction(source["pts"] * source["time_base_num"] * 1000, source["time_base_den"])
        bounds = binding.get("action_window_ms", {})
        require(bounds.get("start_ms", math.inf) <= exact <= bounds.get("end_ms", -math.inf)
            and abs(float(exact) - source.get("timestamp_ms", math.inf)) <= 1e-6, "racket_side_action_time_mismatch")
    require([x["frame_index"] for x in frames] == sorted({x["frame_index"] for x in frames}), "racket_side_frames_not_unique_ordered")
    if binding.get("stage_continuity_verified") is True:
        window = row["window"]
        require(window.get("racket_side") == binding.get("measurement_wrist_side") and all(
            source.get("selection_epoch") == window.get("selection_epoch") and source.get("camera_reference_epoch") == window.get("camera_reference_epoch")
            for source in sources), "racket_side_stage_epoch_mismatch")


def validate_fs_window(row, rule, group):
    annotation, evidence = row.get("source_window_annotation", {}), row.get("source_window_evidence", {})
    require(annotation.get("annotation_id") == row["event_id"] and annotation.get("indicator_id") == row["indicator_id"]
        and annotation.get("person_track_id") == row["person_track_id"]
        and annotation.get("video_sha256") == group["video_sha256"]
        and annotation.get("document_sha256") == group["annotation_canonical_sha256"]
        and annotation.get("digest_kind") == CANONICAL, "fs_window_annotation_binding_mismatch")
    first, last = annotation.get("start_source_frame"), annotation.get("end_source_frame")
    require(isinstance(first, int) and not isinstance(first, bool) and isinstance(last, int) and not isinstance(last, bool)
            and 0 <= first <= last, "fs_window_original_frame_range_invalid")
    frames = list(range(first, last + 1))
    require(evidence.get("source_frames") == frames and evidence.get("interpolation_used") is False
        and evidence.get("source_behavior_or_contact_verified") is False, "fs_window_original_frame_inventory_mismatch")
    bindings, pts = evidence.get("source_frame_bindings", []), evidence.get("source_pts", [])
    require([x.get("frame_index") for x in bindings] == frames and [x.get("frame_index") for x in pts] == frames,
            "fs_window_pts_inventory_mismatch")
    for bound, source in zip(bindings, pts):
        require(all(bound.get(key) == source.get(key) for key in ("pts", "time_base_num", "time_base_den"))
            and bound.get("video_sha256") == group["video_sha256"], "fs_window_original_pts_binding_mismatch")
        require(all(isinstance(source.get(key), int) and not isinstance(source[key], bool)
                    for key in ("pts", "time_base_num", "time_base_den"))
                and source["time_base_num"] > 0 and source["time_base_den"] > 0, "fs_window_invalid_pts")
    requested = annotation["requested_window_ms"]
    for source, key in ((pts[0], "start_ms"), (pts[-1], "end_ms")):
        timestamp = Fraction(source["pts"] * source["time_base_num"] * 1000, source["time_base_den"])
        require(abs(Fraction(str(requested[key])) - timestamp) <= Fraction(51, 100), "fs_window_endpoint_pts_mismatch")
    if evidence.get("step_cycle_bound"):
        require(row["indicator_id"] in STEP_MOTION_CARDS and annotation.get("step_motion_observation")
                and evidence.get("step_motion", {}).get("complete_visual_motion_cycle") is True,
                "fs_cycle_claim_without_computed_cycle")


def compact_report(report, entry, rules, validator):
    require(report["source_run_id"] == entry["source_run_id"], "report_source_run_mismatch")
    rows = [row for card in report["cards"] for row in card["instances"]]
    validate_rows(rows, rules, entry, validator)
    require(report.get("scoring_policy_version") == SCORING_POLICY
            and report.get("score_semantics") == SCORE_SEMANTICS, "report_scoring_policy_mismatch")
    require(report.get("overall_score") == report["summary"]["overall_score"], "report_overall_score_mismatch")
    require(report["summary"] == formal_summary(rows, [card["indicator_id"] for card in report["cards"]]), "report_summary_mismatch")
    require(report["coverage"]["computed_card_instance_count"] == len(rows), "report_coverage_count_mismatch")
    require(len({card["indicator_id"] for card in report["cards"]}) == len(report["cards"]), "duplicate_report_card")
    for card in report["cards"]:
        require(card["indicator_id"] in rules and card["instance_count"] == len(card["instances"])
            and card["instance_status_counts"] == dict(Counter(row["status"] for row in card["instances"])), "report_card_summary_mismatch")
    if entry["route"] != "temporal":
        require(all(report["input"].get(key) is True for key in
            ("artifact_files_verified", "decoder_verified_from_original_video", "service_eligible")), "record_only_or_research_report_rejected")
    result = []
    stage_windows = {window["annotation_id"]: window for window in report.get("stage_windows", [])}
    require(len(stage_windows) == len(report.get("stage_windows", [])), "duplicate_report_stage_window")
    for card_index, card in enumerate(report["cards"]):
        for index, row in enumerate(card["instances"]):
            copied = {key: deepcopy(row[key]) for key in ("indicator_id", "rule_version", "video_id", "event_id", "person_track_id", "status",
                "original_grade", "grade", "proxy_grade", "score", "source_grade_supported", "conditions", "quality", "reason_codes",
                "scoring_policy_version", "semantics", "measurement_provenance", "advisory_flags", "advisory_reason_codes") if key in row}
            for key in ("measurement_version", "condition_completion_fraction", "window", "source_annotation", "source", "event_code",
                        "source_window_annotation", "source_window_evidence", "source_stage_evidence", "auxiliary_evidence"):
                if key in row:
                    copied[key] = deepcopy(row[key])
            evidence = row.get("evidence", {})
            # Keep the temporal/cycle engine's native proof. GS/FS use their
            # separate complete stage/card ledgers; these namespaces differ.
            if evidence:
                copied["evidence"] = deepcopy(evidence)
            copied["evidence_binding"] = {key: deepcopy(evidence[key]) for key in
                ("requested_window_ms", "time_basis", "source_frames", "timestamps_ms", "source_timestamps_ms", "step_window_binding",
                 "source_frame_binding_version", "source_frame_bindings", "source_track_ids", "temporal_successor_binding") if key in evidence}
            if entry["route"].startswith("gs_"):
                stage = stage_windows.get(row["event_id"])
                require(stage is not None and stage["window"] == row["window"], "gs_report_stage_window_mismatch")
                copied["evidence_binding"].update(source_frames=deepcopy(stage["source_frames"]),
                    source_pts=deepcopy(stage["source_pts"]))
            copied["full_instance_sha256"] = canonical(row)
            copied["full_evidence_sha256"] = canonical(evidence)
            copied["report_pointer"] = f"/cards/{card_index}/instances/{index}"
            result.append(copied)
    return {"id": entry["id"], "route": entry["route"], "cohort": entry["cohort"], "source_run_id": entry["source_run_id"],
        "video_sha256": entry["video_sha256"], "artifact_verified": True, "original_video_decoder_verified": True,
        "execution_origin": "fresh_clean_runtime_from_original_run_observations", "instances": result,
        "scoring_policy_version": SCORING_POLICY, "summary": deepcopy(report["summary"]),
        "overall_score": report["overall_score"], "applicable_card_ids": [card["indicator_id"] for card in report["cards"]],
        "status_counts": dict(Counter(row["status"] for row in result)), "instance_count": len(result)}


def validate_bundle(bundle, catalogue, validator):
    require(bundle.get("version") == BUNDLE_VERSION, "unknown_bundle_version")
    require(bundle.get("scoring_policy_version") == SCORING_POLICY, "bundle_scoring_policy_mismatch")
    require(bundle.get("catalogue_digest_kind") == CANONICAL and bundle.get("catalogue_sha256") == canonical(catalogue), "catalogue_digest_mismatch")
    rules = {row["indicator_id"]: row for row in catalogue["cards"]}
    require(len(rules) == len(catalogue["cards"]) == 298, "catalogue_inventory_mismatch")
    require(set(bundle.get("registry", {})) == set(rules), "registry_inventory_mismatch")
    groups, all_rows, seen, source_intervals = set(), [], set(), set()
    sources = {row["video_sha256"] for row in bundle["video_inventory"]}
    require(len(sources) == len(bundle["video_inventory"]), "duplicate_inventory_video")
    for group in bundle.get("executions", []):
        require(group["id"] not in groups and group["route"] in ROUTES, "duplicate_or_unknown_execution_group")
        groups.add(group["id"])
        require(group["video_sha256"] in sources, "execution_video_not_in_inventory")
        require(group.get("execution_origin") == "fresh_clean_runtime_from_original_run_observations"
            and group.get("artifact_verified") is True and group.get("original_video_decoder_verified") is True,
            "synthetic_or_unverified_execution_rejected")
        require(group.get("cohort") in {BASELINE_COHORT, ADDED_COHORT}, "unknown_execution_cohort")
        rows = group["instances"]
        validate_rows(rows, rules, group, validator)
        require(group.get("scoring_policy_version") == SCORING_POLICY
                and group.get("summary") == formal_summary(rows, group.get("applicable_card_ids", []))
                and group.get("overall_score") == group["summary"]["overall_score"], "execution_formal_summary_mismatch")
        require(group["instance_count"] == len(rows) and group["status_counts"] == dict(Counter(row["status"] for row in rows)), "execution_summary_mismatch")
        require(group["source_run_id"] == Path(group["run_dir"]).name, "source_run_directory_mismatch")
        require(set(group["input_artifact_sha256"]) >= {"frames.jsonl", "primary-player.jsonl", "summary.json"}, "source_artifact_inventory_missing")
        for row in rows:
            rule = rules[row["indicator_id"]]
            if group["route"].startswith("gs_"):
                window, annotation = row.get("window", {}), row.get("source_annotation", {})
                require(window.get("video_sha256", "").lower() == group["video_sha256"]
                    and window.get("stage_id", window.get("stage_code")) == rule["stage_code"]
                    and window.get("event_id") == row["event_id"] and window.get("person_track_id") == row["person_track_id"], "gs_stage_source_binding_mismatch")
                require(annotation.get("annotation_id") == row["event_id"] and annotation.get("document_sha256") == group["annotation_canonical_sha256"], "gs_annotation_binding_mismatch")
                source_evidence = row.get("evidence_binding", {})
                frames, pts = source_evidence.get("source_frames", []), source_evidence.get("source_pts", [])
                require([source.get("frame_index") for source in pts] == frames, "gs_original_pts_inventory_mismatch")
                for source in pts:
                    require(all(type(source.get(key)) is int for key in ("frame_index", "pts", "time_base_num", "time_base_den"))
                        and source["time_base_num"] > 0 and source["time_base_den"] > 0, "gs_original_pts_invalid")
                    exact = Fraction(source["pts"] * source["time_base_num"] * 1000, source["time_base_den"])
                    require(Fraction(str(window["start_ms"])) - Fraction(51, 100) <= exact <=
                            Fraction(str(window["end_ms"])) + Fraction(51, 100), "gs_original_pts_outside_stage")
                validate_auxiliary(row, rule, group)
                validate_racket_binding(row, rule, group)
            elif group["route"] == "footwork_cycle":
                annotation, binding = row.get("source_annotation", {}), row.get("evidence_binding", {}).get("step_window_binding", {})
                require(rule["event_code"] in {"FS03", "FS04"} and annotation.get("event_id") == row["event_id"]
                    and annotation.get("document_sha256") == group["annotation_canonical_sha256"]
                    and binding.get("video_sha256") == group["video_sha256"] and binding.get("video_id") == group["source_run_id"]
                    and binding.get("method") == "manual_anchor", "cycle_annotation_binding_mismatch")
            elif group["route"] == "fs_card_windows":
                validate_fs_window(row, rule, group)
            else:
                require(row["indicator_id"] in group["allowed_card_ids"], "unwired_temporal_execution")
            validate_source_ledger(row, group)
            identity = (group["video_sha256"], row["event_id"], row["person_track_id"], row["indicator_id"])
            require(identity not in seen, "duplicate_cross_report_instance")
            seen.add(identity)
            interval_identity = source_interval_identity(group, row)
            require(interval_identity not in source_intervals, "duplicate_source_card_window")
            source_intervals.add(interval_identity)
            digest(row["full_instance_sha256"]); digest(row["full_evidence_sha256"])
            all_rows.append((group, row))
    require(groups and REQUIRED_ROUTES.issubset({group["route"] for group in bundle["executions"]}), "required_execution_routes_missing")
    expected = {"instances": len(all_rows), "status_counts": dict(Counter(row["status"] for _, row in all_rows)),
        "by_route": {route: sum(group["instance_count"] for group in bundle["executions"] if group["route"] == route)
                     for route in sorted({group["route"] for group in bundle["executions"]})}}
    require(bundle.get("summary") == expected, "bundle_summary_mismatch")
    require(bundle.get("formal_summary") == formal_summary([row for _, row in all_rows], list(rules)),
            "bundle_formal_summary_mismatch")
    return all_rows


def cohort_summary(executions):
    result = {}
    for cohort in (BASELINE_COHORT, ADDED_COHORT):
        groups = [g for g in executions if g["cohort"] == cohort]
        rows = [r for g in groups for r in g["instances"]]
        result[cohort] = {"execution_ids": [g["id"] for g in groups], "execution_groups": len(groups),
            "distinct_videos": len({g["video_sha256"] for g in groups}), "instances": len(rows),
            "status_counts": dict(Counter(r["status"] for r in rows)),
            "executed_card_ids": sorted({r["indicator_id"] for r in rows}),
            "scored_card_ids": sorted({r["indicator_id"] for r in rows if r["status"] == "scored"})}
    baseline = {r["indicator_id"] for g in executions if g["cohort"] == BASELINE_COHORT for r in g["instances"]}
    baseline_videos = {g["video_sha256"] for g in executions if g["cohort"] == BASELINE_COHORT}
    result[ADDED_COHORT]["new_executed_card_ids"] = sorted(set(result[ADDED_COHORT]["executed_card_ids"]) - baseline)
    result[ADDED_COHORT]["new_video_count"] = len({g["video_sha256"] for g in executions if g["cohort"] == ADDED_COHORT} - baseline_videos)
    return result


def build_matrix(bundle, catalogue, validator):
    actual = validate_bundle(bundle, catalogue, validator)
    by_card = defaultdict(list)
    for group, row in actual:
        racket = row.get("window", {}).get("racket_side_binding", {})
        auxiliary = row.get("auxiliary_evidence")
        step = row.get("source_window_evidence", {})
        by_card[row["indicator_id"]].append({"execution_id": group["id"], "event_id": row["event_id"],
            "person_track_id": row["person_track_id"], "status": row["status"], "grade": row["grade"],
            "scoring_policy_version": row["scoring_policy_version"], "cohort": group["cohort"], "proxy_grade": row["proxy_grade"],
            "score": row["score"], "instance_sha256": row["full_instance_sha256"], "report_pointer": row["report_pointer"],
            "reason_codes": deepcopy(row.get("reason_codes", [])),
            "racket_side_observation_present": bool(racket),
            "racket_side_stage_continuity_verified": racket.get("stage_continuity_verified") is True,
            "local_preload_auxiliary_status": auxiliary.get("status") if auxiliary else None,
            "source_step_motion_observation_present": bool(row.get("source_window_annotation", {}).get("step_motion_observation")),
            "measurement_window_located": source_interval_identity(group, row)[-1][0] != "unlocated_event_attempt",
            "complete_visual_step_cycle_observed": step.get("step_cycle_bound") is True})
    cards = []
    for rule in catalogue["cards"]:
        identifier, evaluation = rule["indicator_id"], rule["evaluation"]
        registry, instances = bundle["registry"][identifier], by_card[identifier]
        statuses = dict(Counter(row["status"] for row in instances))
        cards.append({"indicator_id": identifier, "event_code": rule["event_code"], "stage_code": rule["stage_code"],
            "rule_sha256": canonical(rule), "source_rubric_sha256": rule["source"]["rubric_sha256"],
            "revision_ids": rule["revision_ids"], "rule_kind": evaluation["kind"], "original_automatic_grade_supported": False,
            **registry, "no_registered_scoring_conditions": not evaluation["conditions"],
            "real_stage_evidence_available": any(row["measurement_window_located"] for row in instances), "real_executed": bool(instances),
            "unlocated_measurement_window_attempt_count": sum(not row["measurement_window_located"] for row in instances),
            "real_formal_scored": bool(statuses.get("scored")), "instance_count": len(instances),
            "status_counts": statuses,
            "observed_failure_reason_counts": dict(sorted(Counter(reason for row in instances for reason in row["reason_codes"]).items())),
            "actual_racket_side_bound_instance_count": sum(row["racket_side_stage_continuity_verified"] for row in instances),
            "actual_preload_auxiliary_status_counts": dict(Counter(row["local_preload_auxiliary_status"] for row in instances
                if row["local_preload_auxiliary_status"] is not None)),
            "actual_complete_visual_step_cycle_instances": sum(row["complete_visual_step_cycle_observed"] for row in instances),
            "instances": instances})
    video_instances = Counter(group["video_sha256"] for group, _ in actual)
    inventory = [dict(row, card_instance_count=video_instances[row["video_sha256"]],
        scope="card_execution_and_recognition" if video_instances[row["video_sha256"]] else "recognition_only_no_card_execution") for row in bundle["video_inventory"]]
    return {"version": VERSION, "source_commit": bundle["runtime"]["commit"], "scoring_policy_version": SCORING_POLICY,
        "score_semantics": SCORE_SEMANTICS, "catalogue_sha256": canonical(catalogue),
        "catalogue_digest_kind": CANONICAL, "replay_bundle_sha256": canonical(bundle), "replay_bundle_digest_kind": CANONICAL,
        "semantics": {"original_grade_verifier": "not_implemented", "proxy_rule_is_not_runtime_execution": True,
            "adapter_presence_is_not_true_stage_or_success": True, "manual_anchor_is_not_expert_truth": True,
            "recognition_only_videos_are_not_card_samples": True, "synthetic_instances_counted": 0,
            "declared_dependency_contract_is_not_executed_behavior": True,
            "dependency_binding_gaps_scope": "missing_declared_runtime_input_contracts_not_original_rubric_verifiers",
            "racket_side_observation_merges_existing_action_not_additional_sample": True,
            "local_preload_auxiliary_does_not_authorize_proxy_or_original_grade": True,
            "unified_api_reports_not_added_to_avoid_duplicate_fs_instances": True},
        "counts": {"cards": len(cards), "original_automatically_graded_cards": 0,
            "specified_visual_proxy_rules": sum(row["rule_kind"] == "visual_proxy" for row in cards),
            "registered_proxy_rule_cards": sum(bool(row["proxy_rule_registered"]) for row in cards),
            "adapter_present_cards": sum(row["adapter"] is not None for row in cards),
            "dependency_binding_gap_cards": sum(bool(row["dependency_binding_gaps"]) for row in cards),
            "no_registered_scoring_conditions_cards": sum(row["no_registered_scoring_conditions"] for row in cards),
            "manual_auxiliary_contract_cards": sum("manual_ungraded_local_preload_auxiliary" in row["declared_dependency_contracts"] for row in cards),
            "step_motion_contract_cards": sum("manual_source_step_motion_observation" in row["declared_dependency_contracts"] for row in cards),
            "racket_side_observation_instances": sum(row["racket_side_observation_present"] for card in cards for row in card["instances"]),
            "racket_side_stage_bound_instances": sum(row["actual_racket_side_bound_instance_count"] for row in cards),
            "local_preload_auxiliary_status_counts": dict(Counter(row["local_preload_auxiliary_status"] for card in cards for row in card["instances"]
                if row["local_preload_auxiliary_status"] is not None)),
            "source_step_motion_observation_instances": sum(row["source_step_motion_observation_present"] for card in cards for row in card["instances"]),
            "complete_visual_step_cycle_instances": sum(row["actual_complete_visual_step_cycle_instances"] for row in cards),
            "real_executed_cards": sum(row["real_executed"] for row in cards),
            "real_formal_scored_cards": sum(row["real_formal_scored"] for row in cards),
            "missing_real_stage_evidence_cards": sum(not row["real_stage_evidence_available"] for row in cards),
            "unlocated_measurement_window_attempts": sum(row["unlocated_measurement_window_attempt_count"] for row in cards),
            "distinct_videos_in_inventory": len(inventory), "videos_with_card_instances": sum(row["card_instance_count"] > 0 for row in inventory),
            **deepcopy(bundle["summary"])},
        "video_inventory": inventory, "cohorts": cohort_summary(bundle["executions"]),
        "formal_summary": deepcopy(bundle["formal_summary"]), "overall_score": bundle["formal_summary"]["overall_score"],
        "execution_artifacts": [{key: deepcopy(group[key]) for key in ("id", "route", "cohort", "source_run_id", "video_sha256", "run_dir",
            "input_artifact_sha256", "full_report_path", "full_report_sha256", "instance_count", "status_counts")} for group in bundle["executions"]],
        "runtime_closure_sha256": canonical(bundle["runtime"]), "cards": cards}


def validate_input_bindings(bundle, manifest):
    """Match each execution to the original declared run, rather than its label."""
    expected = {entry["id"]: entry for entry in manifest["executions"]}
    require(len(expected) == len(manifest["executions"])
        and set(expected) == {group["id"] for group in bundle["executions"]}, "declared_execution_inventory_mismatch")
    for group in bundle["executions"]:
        entry = expected[group["id"]]
        require(all(group[key] == entry[key] for key in ("route", "cohort", "source_run_id", "run_dir", "local_video", "video_sha256")),
                "declared_source_run_video_binding_mismatch")
        if entry.get("input_artifact_sha256"):
            require(group["input_artifact_sha256"] == entry["input_artifact_sha256"], "declared_source_artifact_binding_mismatch")
        sources = [entry[key] for key in ("annotation_path", "annotation_manifest", "annotation_fixture", "racket_side_manifest") if entry.get(key)]
        require([row["path"] for row in group.get("annotation_sources", [])] == sources,
                "declared_source_annotation_binding_mismatch")


def load_acceleration(checkout):
    spec = importlib.util.spec_from_file_location("_gs_acceleration_replay", checkout / "scripts/replay_gs_acceleration_validation.py")
    acceleration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceleration)
    return acceleration


def verify_bundle_artifacts(repository, bundle, catalogue, validator, *, runtime_checkout=None):
    """Verify serialized claims against exact full reports and immutable inputs.

    This intentionally requires local originals. Numeric unit tests use the
    separate validate_bundle API and make no fresh decoder-validation claim.
    """
    require(bundle["producer"]["normalized_sha256"] == lf(Path(__file__).read_bytes()), "producer_changed_since_replay")
    verify_text_artifact(local(repository, bundle["input_manifest_path"]), bundle["input_manifest_sha256"],
        bundle["input_manifest_normalized_sha256"], "input_manifest_changed")
    manifest = read_json(local(repository, bundle["input_manifest_path"]))
    validate_input_manifest(repository, manifest)
    validate_input_bindings(bundle, manifest)
    require(set(bundle["inventory_manifest_sha256"]) == set(manifest["inventory_manifests"])
        == set(bundle["inventory_manifest_normalized_sha256"]), "inventory_manifest_set_mismatch")
    for name, expected in bundle["inventory_manifest_sha256"].items():
        verify_text_artifact(local(repository, name), expected, bundle["inventory_manifest_normalized_sha256"][name], "inventory_manifest_changed")
    # Reconstruct source identities from the bound manifests, rather than
    # trusting a self-consistent extra/missing/renamed inventory row.
    require(bundle["video_inventory"] == inventory_sources(repository, manifest["inventory_manifests"], verify_media=False),
            "video_inventory_differs_from_source_manifests")
    rules = {row["indicator_id"]: row for row in catalogue["cards"]}
    acceleration = None
    entries = {entry["id"]: entry for entry in manifest["executions"]}
    for group in bundle["executions"]:
        report_path = local(repository, group["full_report_path"])
        require(sha(report_path) == group["full_report_sha256"], "full_report_digest_mismatch")
        report = read_json(report_path)
        rebuilt = compact_report(report, group, rules, validator)
        require(all(group[key] == value for key, value in rebuilt.items()), "compact_evidence_differs_from_full_report")
        require(sha(local(repository, group["local_video"])) == group["video_sha256"], "source_video_changed")
        directory = local(repository, group["run_dir"])
        require(all(sha(directory / name) == value for name, value in group["input_artifact_sha256"].items()), "source_artifact_changed")
        if group.get("annotation_sources"):
            for source in group["annotation_sources"]:
                verify_text_artifact(local(repository, source["path"]), source["raw_sha256"], source["utf8_lf_sha256"], "source_annotation_changed")
            if group["route"] == "gs_acceleration" and acceleration is None:
                require(runtime_checkout is not None, "verified_runtime_checkout_required_for_annotation_reconstruction")
                verify_runtime_checkout(runtime_checkout, bundle["runtime"])
                acceleration = load_acceleration(runtime_checkout)
            annotation, sources = source_annotation(repository, entries[group["id"]], acceleration)
            require(sources == [row["path"] for row in group["annotation_sources"]]
                and canonical(annotation) == report["input"]["annotation_document_sha256"] == group["annotation_canonical_sha256"],
                "annotation_canonical_digest_mismatch")
    # Also retain byte checks for inventory-only videos, which are deliberately
    # excluded from card counts but still have source provenance.
    inventory_sources(repository, manifest["inventory_manifests"])


def replay(repository, commit, manifest_path, output_directory):
    require(not output_directory.exists(), "replay_output_must_be_new")
    require(output_directory.is_relative_to(repository), "replay_output_outside_repository")
    manifest = read_json(manifest_path)
    validate_input_manifest(repository, manifest)
    checkout = output_directory / "checkout"
    commit = archive_runtime(repository, commit, checkout)
    require(lf(Path(__file__).read_bytes()) == lf(git_blob(repository, commit, "scripts/build_card_execution_matrix_v4.py")),
            "producer_differs_from_runtime_commit")
    validator = load_runtime(checkout)
    from rallymate_service.card_execution import build_card_execution_report, clear_card_execution_cache
    from rallymate_service.gs_card_execution import build_gs_card_execution_report, _decode_video_timeline, _prepare_samples, _records
    from rallymate_service.footwork_cycle_artifacts import build_footwork_cycle_artifact_report
    from rallymate_service.fs_card_windows import build_fs_card_window_report
    from rallymate_scoring.temporal_card_evidence import CARD_CONDITIONS
    clear_card_execution_cache()
    acceleration = load_acceleration(checkout)
    catalogue = read_json(checkout / CATALOGUE)
    rules = {rule["indicator_id"]: rule for rule in catalogue["cards"]}
    inventory = inventory_sources(repository, manifest["inventory_manifests"])
    executions = []
    for entry in manifest["executions"]:
        require(entry["route"] in ROUTES, "unknown_execution_route")
        require(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", entry["id"]) is not None, "invalid_execution_id")
        directory, video = local(repository, entry["run_dir"]), local(repository, entry["local_video"])
        require(directory.name == entry["source_run_id"] and sha(video) == entry["video_sha256"], "input_source_binding_mismatch")
        names = ["frames.jsonl", "primary-player.jsonl", "summary.json"] + (["events.jsonl"] if entry["route"] in {"temporal", "footwork_cycle", "fs_card_windows"} else [])
        artifacts = {name: sha(directory / name) for name in names}
        if entry.get("input_artifact_sha256"):
            require(artifacts == entry["input_artifact_sha256"], "frozen_source_artifact_changed")
        annotation, annotation_sources = source_annotation(repository, entry, acceleration)
        if entry["route"] == "temporal":
            decoded = _decode_video_timeline(video, entry["video_sha256"])
            _prepare_samples(_records(directory / "frames.jsonl"), _records(directory / "primary-player.jsonl"), decoded,
                video_sha256=entry["video_sha256"], job_id=entry["source_run_id"])
            report = build_card_execution_report(directory)
        else:
            if entry["route"] == "footwork_cycle":
                report = build_footwork_cycle_artifact_report(directory, annotation, video_path=video)
            elif entry["route"] == "fs_card_windows":
                report = build_fs_card_window_report(directory, annotation, video_path=video)
            else:
                report = build_gs_card_execution_report(directory, annotation, video_path=video)
                if entry["route"] == "gs_acceleration":
                    frozen = read_json(local(repository, entry["annotation_manifest"]))
                    case = next(case for case in frozen["cases"] if case["case_id"] == entry["annotation_case_id"])
                    acceleration.assert_source_windows(case, report)
        require(all(sha(directory / name) == value for name, value in artifacts.items()), "input_changed_during_replay")
        # Validate the actual public JSON representation (e.g. tuple bounds
        # become arrays), rather than Python-only serialization differences.
        report = json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))
        target = output_directory / (entry["id"] + ".json")
        write_json(target, report, compact=True)
        compact = compact_report(report, entry, rules, validator)
        compact.update(run_dir=entry["run_dir"], local_video=entry["local_video"], input_artifact_sha256=artifacts,
            full_report_path=relative(repository, target), full_report_sha256=sha(target),
            allowed_card_ids=sorted(CARD_CONDITIONS) if entry["route"] == "temporal" else [],
            annotation_canonical_sha256=canonical(annotation) if annotation else None)
        compact["annotation_sources"] = [{"path": name, "raw_sha256": sha(local(repository, name)),
            "utf8_lf_sha256": lf(local(repository, name).read_bytes())} for name in annotation_sources]
        executions.append(compact)
        print(json.dumps({"execution_id": entry["id"], "instances": compact["instance_count"], "statuses": compact["status_counts"]}), flush=True)
    registry = registry_snapshot(catalogue)
    runtime = source_closure(repository, checkout, commit, RUNNER_DEPENDENCIES)
    rows = [row for execution in executions for row in execution["instances"]]
    bundle = {"version": BUNDLE_VERSION, "scoring_policy_version": SCORING_POLICY, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "producer": {"path": "scripts/build_card_execution_matrix_v4.py", "raw_sha256": sha(Path(__file__)), "normalized_sha256": lf(Path(__file__).read_bytes())},
        "input_manifest_path": relative(repository, manifest_path), "input_manifest_sha256": sha(manifest_path),
        "input_manifest_normalized_sha256": lf(manifest_path.read_bytes()), "text_manifest_normalization": LF,
        "inventory_manifest_sha256": {name: sha(local(repository, name)) for name in manifest["inventory_manifests"]},
        "inventory_manifest_normalized_sha256": {name: lf(local(repository, name).read_bytes()) for name in manifest["inventory_manifests"]},
        "catalogue_sha256": canonical(catalogue), "catalogue_digest_kind": CANONICAL, "runtime": runtime,
        "registry": registry, "video_inventory": inventory, "executions": executions,
        "formal_summary": formal_summary(rows, list(rules)),
        "summary": {"instances": len(rows), "status_counts": dict(Counter(row["status"] for row in rows)),
            "by_route": {route: sum(group["instance_count"] for group in executions if group["route"] == route)
                         for route in sorted({group["route"] for group in executions})}}}
    validate_bundle(bundle, catalogue, validator)
    verify_bundle_artifacts(repository, bundle, catalogue, validator, runtime_checkout=checkout)
    write_json(output_directory / "results.json", bundle, compact=True)
    write_json(output_directory / "matrix.json", build_matrix(bundle, catalogue, validator), compact=True)
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("replay")
    run.add_argument("--repository", type=Path, required=True)
    run.add_argument("--source-commit", required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--output-directory", type=Path, required=True)
    build = sub.add_parser("build")
    build.add_argument("--repository", type=Path, required=True)
    build.add_argument("--results", type=Path, required=True)
    build.add_argument("--runtime-checkout", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "replay":
        replay(args.repository.resolve(), args.source_commit, args.manifest.resolve(), args.output_directory.resolve())
    else:
        bundle = read_json(args.results)
        verify_closure(args.repository.resolve(), bundle["runtime"])
        verify_runtime_checkout(args.runtime_checkout.resolve(), bundle["runtime"])
        verify_import_surface(args.repository.resolve(), args.runtime_checkout.resolve(), bundle["runtime"]["commit"])
        validator = load_runtime(args.runtime_checkout.resolve())
        catalogue = read_json(args.runtime_checkout / CATALOGUE)
        require(registry_snapshot(catalogue) == bundle["registry"], "runtime_registry_changed")
        # Recheck the runtime actually imported by this process, not only its
        # historical declared digests. No media recomputation is claimed here.
        source_closure(args.repository.resolve(), args.runtime_checkout.resolve(), bundle["runtime"]["commit"], RUNNER_DEPENDENCIES)
        verify_bundle_artifacts(args.repository.resolve(), bundle, catalogue, validator, runtime_checkout=args.runtime_checkout.resolve())
        write_json(args.output, build_matrix(bundle, catalogue, validator), compact=True)


if __name__ == "__main__":
    main()
