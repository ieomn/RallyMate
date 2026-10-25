"""Recompute source-bound card executions in an archived Git runtime, then inventory them.

The replay command requires original media and immutable run artifacts. The build
command checks an already produced numeric bundle and its Git-source closure;
it does not claim to rerun videos. Synthetic/record-only reports are inadmissible.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.util
import inspect
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tarfile

VERSION = "card-execution-matrix-v2.0.0"
BUNDLE_VERSION = "card-execution-replay-bundle-v2.0.0"
INPUT_VERSION = "card-execution-inputs-v2.0.0"
CATALOGUE = "src/rallymate_scoring/data/card_rules_v1.json"
RAW = "raw_file_sha256"
CANONICAL = "canonical_json_sorted_utf8_v1"
LF = "utf8_lf_sha256"
ROUTES = {"temporal", "gs_preparation", "gs_acceleration", "footwork_cycle"}
STATUSES = {"proxy_scored", "review_required", "unavailable"}
RUNNER_DEPENDENCIES = ("scripts/build_card_execution_matrix.py", "scripts/replay_gs_acceleration_validation.py")


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
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


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
    return resolved


def load_runtime(checkout):
    require(not any(name.startswith("rallymate") for name in sys.modules), "runtime_already_imported")
    sys.path.insert(0, str(checkout / "src"))
    spec = importlib.util.spec_from_file_location("_card_matrix_previous_validation", checkout / "scripts/build_card_execution_matrix.py")
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


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
                "src/rallymate_service/footwork_cycle_artifacts.py", "src/rallymate_scoring/footwork_cycle_card_evidence.py"}
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
    from rallymate_service.gs_card_execution import compute_gs_card_execution, validate_stage_annotations
    side_field_present = all("racket_side" in inspect.getsource(function)
                             for function in (compute_gs_card_execution, validate_stage_annotations))
    cycle_metrics = {"hip_displacement_torso", "trunk_tilt_deg.range", "hip_speed_drop_torso_s",
        "hip_inside_ankle_support_fraction.mean", "ankle_max_displacement_torso", "ankle_order_exchange_count",
        "ankle_width_contraction_torso", "ankle_width_expansion_torso", "left_knee_flexion_deg.range", "right_knee_flexion_deg.range"}
    output = {}
    for rule in catalogue["cards"]:
        identifier, evaluation = rule["indicator_id"], rule["evaluation"]
        route = "gs_manual_stage_adapter" if rule["event_code"].startswith("GS") else (
            "temporal_event_adapter" if identifier in CARD_CONDITIONS else
            "manual_cycle_adapter" if rule["event_code"] in SUPPORTED_CODES else None)
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
        # At this frozen baseline the GS adapter builds no racket_side field.
        # The registered wrist function therefore has no trusted side input.
        if route == "gs_manual_stage_adapter" and any(c["metric"] == "wrist_speed_increase_torso_s" for c in conditions):
            blockers.append("trusted_racket_side_binding_requires_contract_review" if side_field_present
                            else "trusted_racket_side_input_not_wired_at_baseline")
        if evaluation.get("required_engine"):
            blockers.append(evaluation["required_engine"])
        output[identifier] = {"adapter": route, "conditions": conditions,
            "proxy_rule_registered": evaluation["kind"] == "visual_proxy" and bool(conditions) and all(c["registration"] for c in conditions),
            "dependency_binding_gaps": blockers}
    return output


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
        identity = (row["event_id"], row["person_track_id"], identifier)
        require(identity not in seen, "duplicate_report_instance")
        seen.add(identity)


def compact_report(report, entry, rules, validator):
    require(report["source_run_id"] == entry["source_run_id"], "report_source_run_mismatch")
    rows = [row for card in report["cards"] for row in card["instances"]]
    validate_rows(rows, rules, entry, validator)
    require(report.get("overall_score") is None, "original_overall_grade_unsupported")
    require(report["summary"] == validator.summarize_card_scores(rows, [card["indicator_id"] for card in report["cards"]]), "report_summary_mismatch")
    require(report["coverage"]["computed_card_instance_count"] == len(rows), "report_coverage_count_mismatch")
    require(len({card["indicator_id"] for card in report["cards"]}) == len(report["cards"]), "duplicate_report_card")
    for card in report["cards"]:
        require(card["indicator_id"] in rules and card["instance_count"] == len(card["instances"])
            and card["instance_status_counts"] == dict(Counter(row["status"] for row in card["instances"])), "report_card_summary_mismatch")
    if entry["route"] != "temporal":
        require(all(report["input"].get(key) is True for key in
            ("artifact_files_verified", "decoder_verified_from_original_video", "service_eligible")), "record_only_or_research_report_rejected")
    result = []
    for card_index, card in enumerate(report["cards"]):
        for index, row in enumerate(card["instances"]):
            copied = {key: deepcopy(row[key]) for key in ("indicator_id", "rule_version", "video_id", "event_id", "person_track_id", "status",
                "original_grade", "proxy_grade", "score", "source_grade_supported", "conditions", "quality", "reason_codes") if key in row}
            for key in ("measurement_version", "condition_completion_fraction", "window", "source_annotation", "source", "event_code"):
                if key in row:
                    copied[key] = deepcopy(row[key])
            evidence = row.get("evidence", {})
            copied["evidence_binding"] = {key: deepcopy(evidence[key]) for key in
                ("requested_window_ms", "time_basis", "source_frames", "timestamps_ms", "source_timestamps_ms", "step_window_binding") if key in evidence}
            copied["full_instance_sha256"] = canonical(row)
            copied["full_evidence_sha256"] = canonical(evidence)
            copied["report_pointer"] = f"/cards/{card_index}/instances/{index}"
            result.append(copied)
    return {"id": entry["id"], "route": entry["route"], "source_run_id": entry["source_run_id"],
        "video_sha256": entry["video_sha256"], "artifact_verified": True, "original_video_decoder_verified": True,
        "execution_origin": "fresh_clean_runtime_from_original_run_observations", "instances": result,
        "status_counts": dict(Counter(row["status"] for row in result)), "instance_count": len(result)}


def validate_bundle(bundle, catalogue, validator):
    require(bundle.get("version") == BUNDLE_VERSION, "unknown_bundle_version")
    require(bundle.get("catalogue_digest_kind") == CANONICAL and bundle.get("catalogue_sha256") == canonical(catalogue), "catalogue_digest_mismatch")
    rules = {row["indicator_id"]: row for row in catalogue["cards"]}
    require(len(rules) == len(catalogue["cards"]) == 298, "catalogue_inventory_mismatch")
    require(set(bundle.get("registry", {})) == set(rules), "registry_inventory_mismatch")
    groups, all_rows, seen, sources = set(), [], set(), {row["video_sha256"] for row in bundle["video_inventory"]}
    require(len(sources) == len(bundle["video_inventory"]), "duplicate_inventory_video")
    for group in bundle.get("executions", []):
        require(group["id"] not in groups and group["route"] in ROUTES, "duplicate_or_unknown_execution_group")
        groups.add(group["id"])
        require(group["video_sha256"] in sources, "execution_video_not_in_inventory")
        require(group.get("execution_origin") == "fresh_clean_runtime_from_original_run_observations"
            and group.get("artifact_verified") is True and group.get("original_video_decoder_verified") is True,
            "synthetic_or_unverified_execution_rejected")
        rows = group["instances"]
        validate_rows(rows, rules, group, validator)
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
            elif group["route"] == "footwork_cycle":
                annotation, binding = row.get("source_annotation", {}), row.get("evidence_binding", {}).get("step_window_binding", {})
                require(rule["event_code"] in {"FS03", "FS04"} and annotation.get("event_id") == row["event_id"]
                    and annotation.get("document_sha256") == group["annotation_canonical_sha256"]
                    and binding.get("video_sha256") == group["video_sha256"] and binding.get("video_id") == group["source_run_id"]
                    and binding.get("method") == "manual_anchor", "cycle_annotation_binding_mismatch")
            else:
                require(row["indicator_id"] in group["allowed_card_ids"], "unwired_temporal_execution")
            identity = (group["video_sha256"], row["event_id"], row["person_track_id"], row["indicator_id"])
            require(identity not in seen, "duplicate_cross_report_instance")
            seen.add(identity)
            digest(row["full_instance_sha256"]); digest(row["full_evidence_sha256"])
            all_rows.append((group, row))
    require(groups and {group["route"] for group in bundle["executions"]} == ROUTES, "required_execution_routes_missing")
    expected = {"instances": len(all_rows), "status_counts": dict(Counter(row["status"] for _, row in all_rows)),
        "by_route": {route: sum(group["instance_count"] for group in bundle["executions"] if group["route"] == route) for route in sorted(ROUTES)}}
    require(bundle.get("summary") == expected, "bundle_summary_mismatch")
    return all_rows


def build_matrix(bundle, catalogue, validator):
    actual = validate_bundle(bundle, catalogue, validator)
    by_card = defaultdict(list)
    for group, row in actual:
        by_card[row["indicator_id"]].append({"execution_id": group["id"], "event_id": row["event_id"],
            "person_track_id": row["person_track_id"], "status": row["status"], "proxy_grade": row["proxy_grade"],
            "score": row["score"], "instance_sha256": row["full_instance_sha256"], "report_pointer": row["report_pointer"]})
    cards = []
    for rule in catalogue["cards"]:
        identifier, evaluation = rule["indicator_id"], rule["evaluation"]
        registry, instances = bundle["registry"][identifier], by_card[identifier]
        statuses = dict(Counter(row["status"] for row in instances))
        cards.append({"indicator_id": identifier, "event_code": rule["event_code"], "stage_code": rule["stage_code"],
            "rule_sha256": canonical(rule), "source_rubric_sha256": rule["source"]["rubric_sha256"],
            "revision_ids": rule["revision_ids"], "rule_kind": evaluation["kind"], "original_automatic_grade_supported": False,
            **registry, "manual_external_observation_required": evaluation["kind"] == "manual_review",
            "real_stage_evidence_available": bool(instances), "real_executed": bool(instances),
            "real_proxy_scored": bool(statuses.get("proxy_scored")), "instance_count": len(instances),
            "status_counts": statuses, "instances": instances})
    video_instances = Counter(group["video_sha256"] for group, _ in actual)
    inventory = [dict(row, card_instance_count=video_instances[row["video_sha256"]],
        scope="card_execution_and_recognition" if video_instances[row["video_sha256"]] else "recognition_only_no_card_execution") for row in bundle["video_inventory"]]
    return {"version": VERSION, "source_commit": bundle["runtime"]["commit"], "catalogue_sha256": canonical(catalogue),
        "catalogue_digest_kind": CANONICAL, "replay_bundle_sha256": canonical(bundle), "replay_bundle_digest_kind": CANONICAL,
        "semantics": {"original_grade_verifier": "not_implemented", "proxy_rule_is_not_runtime_execution": True,
            "adapter_presence_is_not_true_stage_or_success": True, "manual_anchor_is_not_expert_truth": True,
            "recognition_only_videos_are_not_card_samples": True, "synthetic_instances_counted": 0,
            "unified_api_reports_not_added_to_avoid_duplicate_fs_instances": True},
        "counts": {"cards": len(cards), "original_automatically_graded_cards": 0,
            "specified_visual_proxy_rules": sum(row["rule_kind"] == "visual_proxy" for row in cards),
            "registered_proxy_rule_cards": sum(bool(row["proxy_rule_registered"]) for row in cards),
            "adapter_present_cards": sum(row["adapter"] is not None for row in cards),
            "dependency_binding_gap_cards": sum(bool(row["dependency_binding_gaps"]) for row in cards),
            "manual_external_observation_cards": sum(row["manual_external_observation_required"] for row in cards),
            "real_executed_cards": sum(row["real_executed"] for row in cards),
            "real_proxy_scored_cards": sum(row["real_proxy_scored"] for row in cards),
            "missing_real_stage_evidence_cards": sum(not row["real_stage_evidence_available"] for row in cards),
            "distinct_videos_in_inventory": len(inventory), "videos_with_card_instances": sum(row["card_instance_count"] > 0 for row in inventory),
            **deepcopy(bundle["summary"])},
        "video_inventory": inventory,
        "execution_artifacts": [{key: deepcopy(group[key]) for key in ("id", "route", "source_run_id", "video_sha256", "run_dir",
            "input_artifact_sha256", "full_report_path", "full_report_sha256", "instance_count", "status_counts")} for group in bundle["executions"]],
        "runtime_closure_sha256": canonical(bundle["runtime"]), "cards": cards}


def validate_input_bindings(bundle, manifest):
    """Match each execution to the original declared run, rather than its label."""
    expected = {entry["id"]: entry for entry in manifest["executions"]}
    require(len(expected) == len(manifest["executions"])
        and set(expected) == {group["id"] for group in bundle["executions"]}, "declared_execution_inventory_mismatch")
    for group in bundle["executions"]:
        entry = expected[group["id"]]
        require(all(group[key] == entry[key] for key in ("route", "source_run_id", "run_dir", "local_video", "video_sha256")),
                "declared_source_run_video_binding_mismatch")
        if entry.get("input_artifact_sha256"):
            require(group["input_artifact_sha256"] == entry["input_artifact_sha256"], "declared_source_artifact_binding_mismatch")
        if entry.get("annotation_path") or entry.get("annotation_manifest"):
            require(group["annotation_source_path"] == entry.get("annotation_path", entry.get("annotation_manifest")),
                    "declared_source_annotation_binding_mismatch")


def verify_bundle_artifacts(repository, bundle, catalogue, validator):
    """Verify serialized claims against exact full reports and immutable inputs.

    This intentionally requires local originals. Numeric unit tests use the
    separate validate_bundle API and make no fresh decoder-validation claim.
    """
    require(bundle["producer"]["normalized_sha256"] == lf(Path(__file__).read_bytes()), "producer_changed_since_replay")
    verify_text_artifact(local(repository, bundle["input_manifest_path"]), bundle["input_manifest_sha256"],
        bundle["input_manifest_normalized_sha256"], "input_manifest_changed")
    manifest = read_json(local(repository, bundle["input_manifest_path"]))
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
    for group in bundle["executions"]:
        report_path = local(repository, group["full_report_path"])
        require(sha(report_path) == group["full_report_sha256"], "full_report_digest_mismatch")
        report = read_json(report_path)
        rebuilt = compact_report(report, group, rules, validator)
        require(all(group[key] == value for key, value in rebuilt.items()), "compact_evidence_differs_from_full_report")
        require(sha(local(repository, group["local_video"])) == group["video_sha256"], "source_video_changed")
        directory = local(repository, group["run_dir"])
        require(all(sha(directory / name) == value for name, value in group["input_artifact_sha256"].items()), "source_artifact_changed")
        if group.get("annotation_source_path"):
            verify_text_artifact(local(repository, group["annotation_source_path"]), group["annotation_source_raw_sha256"],
                group["annotation_source_normalized_sha256"], "source_annotation_changed")
            require(report["input"]["annotation_document_sha256"] == group["annotation_canonical_sha256"], "annotation_canonical_digest_mismatch")
    # Also retain byte checks for inventory-only videos, which are deliberately
    # excluded from card counts but still have source provenance.
    inventory_sources(repository, manifest["inventory_manifests"])


def replay(repository, commit, manifest_path, output_directory):
    require(not output_directory.exists(), "replay_output_must_be_new")
    require(output_directory.is_relative_to(repository), "replay_output_outside_repository")
    manifest = read_json(manifest_path)
    require(manifest.get("version") == INPUT_VERSION, "unknown_input_manifest_version")
    checkout = output_directory / "checkout"
    commit = archive_runtime(repository, commit, checkout)
    validator = load_runtime(checkout)
    from rallymate_service.card_execution import build_card_execution_report
    from rallymate_service.gs_card_execution import build_gs_card_execution_report, _decode_video_timeline, _prepare_samples, _records
    from rallymate_service.footwork_cycle_artifacts import build_footwork_cycle_artifact_report
    from rallymate_scoring.temporal_card_evidence import CARD_CONDITIONS
    spec = importlib.util.spec_from_file_location("_gs_acceleration_replay", checkout / "scripts/replay_gs_acceleration_validation.py")
    acceleration = importlib.util.module_from_spec(spec); spec.loader.exec_module(acceleration)
    catalogue = read_json(checkout / CATALOGUE)
    rules = {rule["indicator_id"]: rule for rule in catalogue["cards"]}
    inventory = inventory_sources(repository, manifest["inventory_manifests"])
    executions = []
    for entry in manifest["executions"]:
        require(entry["route"] in ROUTES, "unknown_execution_route")
        require(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", entry["id"]) is not None, "invalid_execution_id")
        directory, video = local(repository, entry["run_dir"]), local(repository, entry["local_video"])
        require(directory.name == entry["source_run_id"] and sha(video) == entry["video_sha256"], "input_source_binding_mismatch")
        names = ["frames.jsonl", "primary-player.jsonl", "summary.json"] + (["events.jsonl"] if entry["route"] in {"temporal", "footwork_cycle"} else [])
        artifacts = {name: sha(directory / name) for name in names}
        if entry.get("input_artifact_sha256"):
            require(artifacts == entry["input_artifact_sha256"], "frozen_source_artifact_changed")
        annotation = None
        if entry["route"] == "temporal":
            decoded = _decode_video_timeline(video, entry["video_sha256"])
            _prepare_samples(_records(directory / "frames.jsonl"), _records(directory / "primary-player.jsonl"), decoded,
                video_sha256=entry["video_sha256"], job_id=entry["source_run_id"])
            report = build_card_execution_report(directory)
        else:
            if entry["route"] == "gs_acceleration":
                frozen = read_json(local(repository, entry["annotation_manifest"]))
                case = next(case for case in frozen["cases"] if case["case_id"] == entry["annotation_case_id"])
                acceleration.verify_review_artifacts(case, repository)
                annotation = acceleration.build_annotations(case, frozen["annotation_version"])
            else:
                annotation = read_json(local(repository, entry["annotation_path"]))
            if entry["route"] == "footwork_cycle":
                report = build_footwork_cycle_artifact_report(directory, annotation, video_path=video)
            else:
                report = build_gs_card_execution_report(directory, annotation, video_path=video)
                if entry["route"] == "gs_acceleration":
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
        if annotation:
            annotation_source = entry.get("annotation_path", entry.get("annotation_manifest"))
            compact.update(annotation_source_path=annotation_source, annotation_source_raw_sha256=sha(local(repository, annotation_source)),
                annotation_source_normalized_sha256=lf(local(repository, annotation_source).read_bytes()))
        executions.append(compact)
        print(json.dumps({"execution_id": entry["id"], "instances": compact["instance_count"], "statuses": compact["status_counts"]}), flush=True)
    runtime = source_closure(repository, checkout, commit, RUNNER_DEPENDENCIES)
    rows = [row for execution in executions for row in execution["instances"]]
    bundle = {"version": BUNDLE_VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "producer": {"path": "scripts/build_card_execution_matrix_v2.py", "raw_sha256": sha(Path(__file__)), "normalized_sha256": lf(Path(__file__).read_bytes())},
        "input_manifest_path": relative(repository, manifest_path), "input_manifest_sha256": sha(manifest_path),
        "input_manifest_normalized_sha256": lf(manifest_path.read_bytes()), "text_manifest_normalization": LF,
        "inventory_manifest_sha256": {name: sha(local(repository, name)) for name in manifest["inventory_manifests"]},
        "inventory_manifest_normalized_sha256": {name: lf(local(repository, name).read_bytes()) for name in manifest["inventory_manifests"]},
        "catalogue_sha256": canonical(catalogue), "catalogue_digest_kind": CANONICAL, "runtime": runtime,
        "registry": registry_snapshot(catalogue), "video_inventory": inventory, "executions": executions,
        "summary": {"instances": len(rows), "status_counts": dict(Counter(row["status"] for row in rows)),
            "by_route": {route: sum(group["instance_count"] for group in executions if group["route"] == route) for route in sorted(ROUTES)}}}
    validate_bundle(bundle, catalogue, validator)
    verify_bundle_artifacts(repository, bundle, catalogue, validator)
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
        validator = load_runtime(args.runtime_checkout.resolve())
        catalogue = read_json(args.runtime_checkout / CATALOGUE)
        require(registry_snapshot(catalogue) == bundle["registry"], "runtime_registry_changed")
        # Recheck the runtime actually imported by this process, not only its
        # historical declared digests. No media recomputation is claimed here.
        source_closure(args.repository.resolve(), args.runtime_checkout.resolve(), bundle["runtime"]["commit"], RUNNER_DEPENDENCIES)
        verify_bundle_artifacts(args.repository.resolve(), bundle, catalogue, validator)
        write_json(args.output, build_matrix(bundle, catalogue, validator), compact=True)


if __name__ == "__main__":
    main()
