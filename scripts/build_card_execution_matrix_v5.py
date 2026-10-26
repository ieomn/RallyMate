"""Current-rule coverage, preserving media/export identities and historical audit.

No model inference. Replay requires an exact committed runtime; bootstrap tests
may import this candidate but cannot claim a committed media replay. Version 4
remains an immutable helper and historical artifact, never a score input.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from fractions import Fraction
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location("_matrix_v4_support", Path(__file__).with_name("build_card_execution_matrix_v4.py"))
v4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4)
require, read_json, canonical, sha, lf, local, relative, write_json = (
    v4.require, v4.read_json, v4.canonical, v4.sha, v4.lf, v4.local, v4.relative, v4.write_json)
VERSION = "card-execution-matrix-v5.0.0"
BUNDLE_VERSION = "card-execution-replay-bundle-v5.0.0"
INPUT_VERSION = "card-execution-inputs-v5.0.0"
APP_ROUTES = {"directional_micro", "micro_cycles"}
ROUTES = v4.ROUTES | APP_ROUTES | {"gs_follow_through"}
RETIRED = {"GS01-M09-04", "GS01-M09-05"}
RUNNERS = (*v4.RUNNER_DEPENDENCIES, "scripts/build_card_execution_matrix_v5.py", "scripts/replay_gs_follow_through_validation.py")
SCORE_SCOPE = "per_video_pose_export_primary_continuity_only_no_cross_source_total"
ADDED_COHORT = "v5_added_current_application_and_follow_through"


def application_contracts():
    from rallymate_service import directional_micro_adjustment as direction, micro_adjustment_cycles as cycles
    from rallymate_scoring.direction_reference_observables import REQUIRED_JOINTS
    return {identifier: {"runtime_engine": module.VERSION, "application_rule_version": module.RULE_VERSION,
        "conditions": json_safe(module.CONDITIONS), "required_joint_names": list(REQUIRED_JOINTS),
        "measurement_version": getattr(module, "MEASUREMENT_VERSION", module.RULE_VERSION),
        "route": route, "measurement_key": key}
        for module, identifiers, route, key in ((direction, direction.SUPPORTED_CARDS, "directional_micro", "directional_micro_adjustment"),
              (cycles, [cycles.CARD], "micro_cycles", "micro_adjustment_cycles")) for identifier in identifiers}


def effective_rule(rule, contract):
    """Validation projection only; never rewrite the source catalogue kind."""
    projected = deepcopy(rule)
    projected["required_joint_names"] = contract["required_joint_names"]
    projected["evaluation"]["conditions"] = [{"metric": c["metric"], "operator": c["operator"], "unit": c["unit"],
        **({"bounds": c["threshold"]} if isinstance(c["threshold"], list) else {"value": c["threshold"]})} for c in contract["conditions"]]
    return projected


def validate_instance(row, rule, *, video_sha256, video_id):
    contract = application_contracts().get(rule["indicator_id"])
    if contract:
        require(row.get("version") == contract["runtime_engine"] and row.get("application_rule_version") == contract["application_rule_version"],
                "application_runtime_contract_mismatch")
        projected = effective_rule(rule, contract)
        require([c.get("key") for c in row.get("conditions", [])] == [c["key"] for c in contract["conditions"]], "application_condition_keys_mismatch")
        v4.validate_instance(row, projected, video_sha256=video_sha256, video_id=video_id)
        proof = row.get("source_window_evidence", {}).get(contract["measurement_key"], {})
        require(row.get("measurement_version") == proof.get("version") == contract["measurement_version"], "application_measurement_version_mismatch")
        require(proof.get("source_grade_supported") is False and proof.get("contact_verified") is False
            and proof.get("interpolation_used") is False, "application_observation_semantics_missing")
        require(proof.get("source_frames") == row["source_window_evidence"]["source_frames"]
            and proof.get("source_frame_bindings") == row["source_window_evidence"]["source_frame_bindings"], "application_source_copy_mismatch")
        values = proof.get("measurements", {})
        require(all(c.get("value") == values.get(c["metric"]) for c in row["conditions"]), "application_measurement_copy_mismatch")
        if row["status"] == "scored":
            require(proof.get("geometry_gate_passed") is True and proof.get("valid_frame_count") == proof.get("total_frame_count")
                == len(proof["source_frames"]), "application_complete_geometry_required")
        return
    v4.validate_instance(row, rule, video_sha256=video_sha256, video_id=video_id)
    if rule["indicator_id"] in RETIRED:
        require(row["status"] == "unavailable" and row["conditions"] == []
            and row.get("scoring_revision_id") == rule["evaluation"]["scoring_revision_id"]
            and row.get("missing_required_observables") == rule["evaluation"]["required_observables"], "retired_hand_racket_rule_cannot_score")
        auxiliary = row.get("auxiliary_observations")
        if auxiliary is not None:
            require(auxiliary.get("version") == "gs-hand-racket-auxiliary-v1.0.0" and auxiliary.get("scoring_eligible") is False
                and auxiliary.get("semantics") == "bilateral_wrist_geometry_only_not_hand_racket_approach_or_control"
                and set(auxiliary).isdisjoint({"grade", "score", "passed"}), "retired_auxiliary_cannot_authorize_score")


def pose_export_id(group):
    return canonical({"video_sha256": v4.video_digest(group["video_sha256"]),
        "frames_sha256": v4.digest(group["input_artifact_sha256"]["frames.jsonl"])})


def snapshot_id(group):
    return canonical({"video_sha256": group["video_sha256"], "input_artifact_sha256": group["input_artifact_sha256"]})


def instance_identity(group, row):
    # Track IDs are local to the observation export, never cross-run identity.
    return (pose_export_id(group), *v4.source_interval_identity(group, row)[1:])


def registry_snapshot(catalogue):
    result = v4.registry_snapshot(catalogue)
    apps = application_contracts()
    for identifier, entry in result.items():
        contract = apps.get(identifier)
        entry.update(application_rule_registered=bool(contract), application_contract=deepcopy(contract),
            scoring_rule_registered=entry["proxy_rule_registered"] or bool(contract),
            adapter_routes=[entry["adapter"]] + ([contract["route"]] if contract else []))
        if identifier in RETIRED:
            entry["declared_dependency_contracts"]["unscored_bilateral_wrist_auxiliary"] = "gs-hand-racket-auxiliary-v1.0.0"
    return result


def load_follow(checkout):
    sys.path.insert(0, str(checkout))
    spec = importlib.util.spec_from_file_location("_matrix_follow_replay", checkout / "scripts/replay_gs_follow_through_validation.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def annotation_for(repository, entry, acceleration, follow, *, verify_media=True):
    if entry["route"] == "gs_follow_through":
        name = entry["annotation_manifest"]
        manifest = read_json(local(repository, name))
        # This frozen source includes explicit parent revisions and exact PTS.
        annotation = follow.build_annotations(manifest)
        if verify_media:
            follow.verify_artifacts(manifest, repository)
        return annotation, [name]
    if entry["route"] in APP_ROUTES:
        name = entry["annotation_path"]
        annotation = read_json(local(repository, name))
        require(annotation["source_run_id"] == entry["source_run_id"] and annotation["video_sha256"] == entry["video_sha256"], "application_annotation_source_mismatch")
        require(annotation["input_artifact_sha256"] == entry["input_artifact_sha256"], "application_annotation_artifact_mismatch")
        return annotation, [name]
    return v4.source_annotation(repository, entry, acceleration, verify_media=verify_media)


def validate_input_manifest(repository, manifest, *, verify_sources=True):
    require(manifest.get("version") == INPUT_VERSION, "unknown_input_version")
    baseline = manifest["baseline_manifest"]
    v4.verify_text_artifact(local(repository, baseline["path"]), baseline["raw_sha256"], baseline["utf8_lf_sha256"], "v4_manifest_changed")
    old = read_json(local(repository, baseline["path"]))
    v4.validate_input_manifest(repository, old, verify_sources=verify_sources)
    entries = manifest["executions"]
    require(len({e["id"] for e in entries}) == len(entries), "duplicate_execution_id")
    require(entries[:len(old["executions"]) ] == old["executions"], "v4_source_groups_changed")
    added = entries[len(old["executions"]):]
    require({e["route"] for e in added} == APP_ROUTES | {"gs_follow_through"} and len(added) == 3, "added_execution_inventory_changed")
    require(manifest["inventory_manifests"] == old["inventory_manifests"], "media_inventory_scope_changed")
    history = manifest["retirement_history"]
    v4.verify_text_artifact(local(repository, history["path"]), history["raw_sha256"], history["utf8_lf_sha256"], "retirement_history_changed")
    for entry in added:
        require(entry["route"] in ROUTES and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", entry["id"]), "invalid_added_execution")
        require(entry["cohort"] == ADDED_COHORT, "invalid_added_cohort")
        required = {"frames.jsonl", "primary-player.jsonl", "summary.json"} | ({"events.jsonl"} if entry["route"] in APP_ROUTES else set())
        require(set(entry["input_artifact_sha256"]) == required, "added_source_artifact_inventory_mismatch")
        for value in entry["input_artifact_sha256"].values(): v4.digest(value)
        paths = [entry[key] for key in ("annotation_path", "annotation_manifest") if entry.get(key)]
        require(set(paths) == set(entry["annotation_source_digests"]), "added_annotation_digest_inventory")
        if verify_sources:
            for name in paths:
                digests = entry["annotation_source_digests"][name]
                v4.verify_text_artifact(local(repository, name), digests["raw_sha256"], digests["utf8_lf_sha256"], "added_annotation_changed")


def validate_windows(group):
    windows = group["report_windows"]
    if group["route"] not in APP_ROUTES:
        return
    by_id = {w["annotation_id"]: w for w in windows}
    require(len(by_id) == len(windows) == len(group["instances"]), "application_window_inventory_mismatch")
    for row in group["instances"]:
        saved = by_id.get(row["event_id"], {})
        annotation = row["source_window_annotation"]
        require(saved == {**annotation, "window": row["window"], "source_frames": row["source_window_evidence"]["source_frames"]}, "application_top_window_copy_mismatch")
        require(annotation["document_sha256"] == group["annotation_canonical_sha256"]
            == group["source_input"]["annotation_document_sha256"], "application_document_digest_mismatch")


def compact_report(report, entry, rules):
    # v4's compact step also verifies complete report arithmetic. Only its
    # validation rule projection receives application conditions.
    from rallymate_scoring.card_rule_catalog import load_card_rules
    require(report["input"].get("catalogue_sha256") == canonical(load_card_rules())
        and report["input"].get("catalogue_digest_kind") == v4.CANONICAL, "report_not_current_catalogue")
    if entry["route"] != "temporal":
        # The cycle artifact adapter's native contract names this source digest
        # explicitly. Other adapters must retain their own exact field; this is
        # not an alias fallback that can hide a missing/wrong native binding.
        video_key = "source_video_sha256" if entry["route"] == "footwork_cycle" else "video_sha256"
        require(report["input"].get(video_key) == entry["video_sha256"]
            and report["input"].get("input_artifact_sha256") == entry["input_artifact_sha256"], "report_source_artifact_binding_mismatch")
    projected = {key: effective_rule(rule, application_contracts()[key]) if key in application_contracts() else rule for key, rule in rules.items()}
    validator = type("Validator", (), {"validate_instance": staticmethod(validate_instance)})
    compact = v4.compact_report(report, entry, projected, validator)
    originals = [row for card in report["cards"] for row in card["instances"]]
    for row, original in zip(compact["instances"], originals):
        for key in ("version", "application_rule_version", "measurements", "scoring_revision_id", "missing_required_observables", "auxiliary_observations"):
            if key in original: row[key] = deepcopy(original[key])
    compact.update(source_input=deepcopy(report["input"]),
        report_windows=deepcopy(report.get("fs_card_windows", report.get("stage_windows", []))))
    return compact


def validate_bundle(bundle, catalogue):
    require(bundle.get("version") == BUNDLE_VERSION and bundle.get("catalogue_digest_kind") == v4.CANONICAL
        and bundle["catalogue_sha256"] == canonical(catalogue), "bundle_catalogue_or_version_mismatch")
    rules = {r["indicator_id"]: r for r in catalogue["cards"]}
    require(len(rules) == 298 and bundle["registry"] == registry_snapshot(catalogue), "registry_inventory_or_content_mismatch")
    require(bundle.get("scoring_policy_version") == v4.SCORING_POLICY, "bundle_scoring_policy_mismatch")
    require(bundle.get("overall_score") is None and "formal_summary" not in bundle
        and bundle.get("score_aggregation_scope") == SCORE_SCOPE, "cross_source_total_forbidden")
    groups, identities, actual = set(), set(), []
    inventory = {r["video_sha256"] for r in bundle["video_inventory"]}
    require(len(inventory) == len(bundle["video_inventory"]), "duplicate_inventory_video")
    for group in bundle["executions"]:
        require(group["id"] not in groups and group["route"] in ROUTES, "duplicate_or_unknown_execution")
        groups.add(group["id"])
        require(group["source_run_id"] == Path(group["run_dir"]).name and group["video_sha256"] in inventory, "execution_source_mismatch")
        require(group["cohort"] in {v4.BASELINE_COHORT, v4.ADDED_COHORT, ADDED_COHORT}, "unknown_cohort")
        require(group["scoring_policy_version"] == v4.SCORING_POLICY, "group_scoring_policy_mismatch")
        require(set(group["input_artifact_sha256"]) >= {"frames.jsonl", "primary-player.jsonl", "summary.json"}, "missing_input_artifact_inventory")
        require(group["artifact_verified"] is True and group["original_video_decoder_verified"] is True
            and group["execution_origin"] == "fresh_clean_runtime_from_original_run_observations", "not_original_media_execution")
        require(group["pose_export_id"] == pose_export_id(group) and group["source_snapshot_id"] == snapshot_id(group), "export_identity_mismatch")
        rows = group["instances"]
        require(rows and group["summary"] == v4.formal_summary(rows, group["applicable_card_ids"])
            and group["overall_score"] == group["summary"]["overall_score"], "group_summary_mismatch")
        require(group["instance_count"] == len(rows) and group["status_counts"] == dict(Counter(r["status"] for r in rows)), "group_instance_count_mismatch")
        validate_windows(group)
        for row in rows:
            require(row["indicator_id"] in rules, "unknown_card")
            rule = rules[row["indicator_id"]]
            validate_instance(row, rule, video_sha256=group["video_sha256"], video_id=group["source_run_id"])
            if group["route"] in APP_ROUTES:
                contract = application_contracts().get(row["indicator_id"])
                require(contract is not None and contract["route"] == group["route"], "wrong_application_route")
                # Old FS4's step-cycle whitelist is not an FS05 scoring rule.
                source_only = deepcopy(row); source_only["source_window_evidence"]["step_cycle_bound"] = False
                v4.validate_fs_window(source_only, rule, group)
                v4.validate_source_ledger(row, {**group, "route": "fs_card_windows"})
            elif group["route"].startswith("gs_"):
                window, annotation = row.get("window", {}), row.get("source_annotation", {})
                require(window.get("video_sha256", "").lower() == group["video_sha256"]
                    and window.get("stage_id", window.get("stage_code")) == rule["stage_code"]
                    and window.get("event_id") == row["event_id"] and window.get("person_track_id") == row["person_track_id"], "gs_stage_source_binding_mismatch")
                require(annotation.get("annotation_id") == row["event_id"]
                    and annotation.get("document_sha256") == group["annotation_canonical_sha256"], "gs_annotation_binding_mismatch")
                evidence = row["evidence_binding"]
                require([p.get("frame_index") for p in evidence["source_pts"]] == evidence["source_frames"], "gs_pts_inventory_mismatch")
                for source in evidence["source_pts"]:
                    require(Fraction(str(window["start_ms"]))-Fraction(51, 100) <= v4.exact_time(source)
                        <= Fraction(str(window["end_ms"]))+Fraction(51, 100), "gs_pts_outside_stage")
                v4.validate_auxiliary(row, rule, group); v4.validate_racket_binding(row, rule, group)
                v4.validate_source_ledger(row, group)
            elif group["route"] == "fs_card_windows":
                v4.validate_fs_window(row, rule, group); v4.validate_source_ledger(row, group)
            elif group["route"] == "footwork_cycle":
                annotation, binding = row["source_annotation"], row["evidence_binding"]["step_window_binding"]
                require(rule["event_code"] in {"FS03", "FS04"} and annotation.get("event_id") == row["event_id"]
                    and annotation.get("document_sha256") == group["annotation_canonical_sha256"]
                    and binding.get("video_sha256") == group["video_sha256"] and binding.get("video_id") == group["source_run_id"]
                    and binding.get("method") == "manual_anchor", "cycle_annotation_binding_mismatch")
                v4.validate_source_ledger(row, group)
            else:
                require(row["indicator_id"] in group["allowed_card_ids"], "unwired_temporal_card")
                v4.validate_source_ledger(row, group)
            key = instance_identity(group, row)
            require(key not in identities, "duplicate_observation_card_window")
            identities.add(key); actual.append((group, row))
            v4.digest(row["full_instance_sha256"])
            v4.digest(row["full_evidence_sha256"])
    require(v4.REQUIRED_ROUTES | APP_ROUTES | {"gs_follow_through"} <= {g["route"] for g in bundle["executions"]}, "required_routes_missing")
    expected = {"instances": len(actual), "status_counts": dict(Counter(r["status"] for _, r in actual)),
                "by_route": dict(Counter(g["route"] for g, _ in actual))}
    require(bundle["summary"] == expected, "bundle_count_mismatch")
    require(bundle["score_groups"] == score_groups(bundle["executions"]), "source_scoped_score_groups_mismatch")
    validate_retirement_audit(bundle["retirement_audit"], bundle["executions"])
    return actual


def build_matrix(bundle, catalogue):
    actual = validate_bundle(bundle, catalogue)
    by_card = defaultdict(list)
    for group, row in actual:
        by_card[row["indicator_id"]].append({"execution_id": group["id"], "source_run_id": group["source_run_id"],
            "video_sha256": group["video_sha256"], "pose_export_id": group["pose_export_id"], "source_snapshot_id": group["source_snapshot_id"],
            "event_id": row["event_id"], "person_track_id": row["person_track_id"], "status": row["status"], "grade": row["grade"], "score": row["score"],
            "application_rule_version": row.get("application_rule_version"), "scoring_revision_id": row.get("scoring_revision_id"),
            "missing_required_observables": row.get("missing_required_observables", []), "reason_codes": row["reason_codes"],
            "auxiliary_status": row.get("auxiliary_observations", {}).get("status"),
            "report_pointer": row["report_pointer"], "instance_sha256": row["full_instance_sha256"],
            "measurement_window_located": v4.source_interval_identity(group, row)[-1][0] != "unlocated_event_attempt"})
    cards = [{"indicator_id": r["indicator_id"], "event_code": r["event_code"], "stage_code": r["stage_code"],
        "rule_sha256": canonical(r), "source_rubric_sha256": r["source"]["rubric_sha256"], "rule_kind": r["evaluation"]["kind"],
        "source_grade_supported": False, **bundle["registry"][r["indicator_id"]],
        "real_executed": bool(by_card[r["indicator_id"]]),
        "real_formal_scored": any(i["status"] == "scored" for i in by_card[r["indicator_id"]]),
        "real_stage_evidence_available": any(i["measurement_window_located"] for i in by_card[r["indicator_id"]]),
        "instances": by_card[r["indicator_id"]]} for r in catalogue["cards"]]
    return {"version": VERSION, "source_commit": bundle["runtime"]["commit"], "catalogue_sha256": canonical(catalogue),
        "replay_bundle_sha256": canonical(bundle), "digest_kind": v4.CANONICAL, "cards": cards,
        "counts": {"cards": len(cards), "original_automatically_graded_cards": 0,
            "registered_catalogue_rules": sum(c["proxy_rule_registered"] for c in cards),
            "registered_application_rules": sum(c["application_rule_registered"] for c in cards),
            "real_executed_cards": sum(c["real_executed"] for c in cards), "real_formal_scored_cards": sum(c["real_formal_scored"] for c in cards),
            "missing_real_stage_evidence_cards": sum(not c["real_stage_evidence_available"] for c in cards),
            "distinct_videos_in_inventory": len(bundle["video_inventory"]),
            "videos_with_card_instances": len({g["video_sha256"] for g in bundle["executions"]}),
            "pose_exports_with_instances": len({g["pose_export_id"] for g in bundle["executions"]}), **bundle["summary"]},
        "overall_score": None, "score_aggregation_scope": bundle["score_aggregation_scope"],
        "score_groups": deepcopy(bundle["score_groups"]),
        "execution_artifacts": [{k: deepcopy(g[k]) for k in ("id", "route", "source_run_id", "video_sha256", "pose_export_id", "source_snapshot_id", "instance_count", "status_counts", "full_report_path", "full_report_sha256")} for g in bundle["executions"]],
        "video_inventory": bundle["video_inventory"], "retirement_audit": bundle["retirement_audit"],
        "semantics": {"coverage_uses_unique_card_ids": True, "raw_track_ids_are_export_local": True,
            "auxiliary_is_not_scoring": True, "synthetic_instances_counted": 0, "original_grade_verifier_implemented": False,
            "no_new_model_inference": True, "recognition_only_video_is_not_card_sample": True,
            "source_identity_verification_scope": "artifact_selected_track_epoch_and_pts_bindings_only",
            "visual_person_identity_truth_verified": False, "pose_accuracy_verified": False,
            "stable_selected_track_does_not_exclude_pose_person_misassociation": True}}


def continuity_scope(group, row):
    """Only a complete explicit primary namespace permits cross-window pooling.

    A temporal source selection epoch can name the model tracker. It is not
    interchangeable with the sample adapter's derived primary counter.
    """
    route = group["route"]
    if route.startswith("gs_") or route in APP_ROUTES | {"fs_card_windows"}:
        proof = row.get("source_stage_evidence" if route.startswith("gs_") else "source_window_evidence", {})
        fields = ("selected_person_track_id", "selection_epoch", "derived_selection_epoch", "camera_reference_epoch")
        namespace = "sample_adapter_primary_derived_camera"
    else:
        # Even equal raw model epochs do not establish a continuous selected
        # person across event windows. Keep these scores at the instance scope.
        proof, fields, namespace = {}, (), None
    frames, ledger = proof.get("source_frames", []), proof.get("source_frame_bindings", [])
    complete = (bool(frames) and [b.get("frame_index") for b in ledger] == frames and
        frames == list(range(frames[0], frames[-1]+1)) and all(
        all(v4.nonnegative_int(b.get(k)) for k in fields) and b.get("video_sha256") == group["video_sha256"]
        and b.get("selected_person_track_id") == row["person_track_id"] for b in ledger)
        and all(len({b[k] for b in ledger}) == 1 for k in fields))
    if complete:
        return {"kind": namespace, "primary_artifact_sha256": group["input_artifact_sha256"]["primary-player.jsonl"],
            **{k: ledger[0][k] for k in fields}}
    return {"kind": "instance_only_continuity_not_established", "execution_id": group["id"],
        "instance_identity_sha256": canonical(instance_identity(group, row))}


def score_groups(executions):
    """One score per video/export/local person; never average separate people."""
    grouped = defaultdict(list)
    origins, scopes = defaultdict(set), {}
    seen = set()
    for group in executions:
        for row in group["instances"]:
            identity = instance_identity(group, row)
            require(identity not in seen, "duplicate_observation_card_window")
            seen.add(identity)
            scope = continuity_scope(group, row)
            key = (group["video_sha256"], pose_export_id(group), row["person_track_id"], canonical(scope))
            scopes[key] = scope
            grouped[key].append(row); origins[key].add(group["id"])
    return [{"video_sha256": key[0], "pose_export_id": key[1], "person_track_id": key[2],
        "execution_ids": sorted(origins[key]), "continuity_scope": scopes[key],
        "identity_semantics": "export_local_selected_track_not_cross_video_person_identity",
        "applicable_scope": "observed_card_ids_only_full_catalogue_coverage_reported_separately",
        "summary": v4.formal_summary(rows, sorted({r["indicator_id"] for r in rows}))}
        for key, rows in sorted(grouped.items())]


def retirement_audit(history, source, executions):
    groups = [g for g in executions if g["route"] == "gs_follow_through"]
    require(len(groups) == 1, "follow_through_group_inventory_mismatch")
    group, = groups
    require(history["source_run_id"] == group["source_run_id"] and history["video_sha256"] == group["video_sha256"]
        and history["input"]["input_artifact_sha256"] == group["input_artifact_sha256"], "retirement_source_export_mismatch")
    current = {(r["event_id"], r["person_track_id"], r["indicator_id"]): r for r in group["instances"] if r["indicator_id"] in RETIRED}
    rows = []
    for index, old in enumerate(history["instances"]):
        if old["indicator_id"] not in RETIRED:
            continue
        key = (old["event_id"], old["person_track_id"], old["indicator_id"])
        new = current.pop(key, None)
        require(new is not None and old["window"] == new["window"], "retirement_original_window_changed")
        rows.append({"indicator_id": old["indicator_id"], "event_id": old["event_id"], "person_track_id": old["person_track_id"],
            "execution_id": group["id"], "pose_export_id": group["pose_export_id"],
            "historical_pointer": f"/instances/{index}", "historical_instance_sha256": canonical(old),
            "historical_status": old["status"], "historical_grade": old.get("grade"), "historical_score": old["score"],
            "historical_condition_evidence": deepcopy(old["conditions"]),
            "current_instance_sha256": new["full_instance_sha256"], "current_status": new["status"],
            "current_score": new["score"], "scoring_revision_id": new["scoring_revision_id"],
            "auxiliary_observations": deepcopy(new.get("auxiliary_observations")),
            "counted_as_additional_instance": False})
    require(not current and rows, "retirement_audit_inventory_mismatch")
    return {"source": deepcopy(source), "historical_runtime_commit": history["runtime"]["base_commit"],
        "semantics": "historical_scores_retained_for_audit_only_current_instances_replace_them",
        "rows": rows, "withdrawn_scored_instances": sum(r["historical_score"] is not None for r in rows)}


def validate_retirement_audit(audit, executions):
    current = {(g["id"], r["event_id"], r["person_track_id"], r["indicator_id"]): r
        for g in executions for r in g["instances"] if r["indicator_id"] in RETIRED}
    require(audit["semantics"] == "historical_scores_retained_for_audit_only_current_instances_replace_them", "retirement_semantics_changed")
    for row in audit["rows"]:
        key = (row["execution_id"], row["event_id"], row["person_track_id"], row["indicator_id"])
        result = current.pop(key, None)
        require(result is not None and result["status"] == row["current_status"] == "unavailable"
            and row["current_score"] is None and row["counted_as_additional_instance"] is False
            and row["current_instance_sha256"] == result["full_instance_sha256"]
            and row["auxiliary_observations"] == result.get("auxiliary_observations")
            and row["scoring_revision_id"] == result["scoring_revision_id"], "retirement_current_evidence_mismatch")
    require(not current and audit["withdrawn_scored_instances"] == sum(r["historical_score"] is not None for r in audit["rows"]), "retirement_count_mismatch")


def verify_closure(repository, closure):
    v4.verify_closure(repository, closure)
    require(set(RUNNERS) | {"src/rallymate_service/directional_micro_adjustment.py",
        "src/rallymate_service/micro_adjustment_cycles.py", "src/rallymate_scoring/direction_reference_observables.py"}
        <= set(closure["files"]), "incomplete_v5_runtime_source_closure")
    resources = {p for p in v4.subprocess.check_output(["git", "ls-tree", "-r", "--name-only", closure["commit"], "src"],
        cwd=repository, text=True).splitlines() if p.endswith(".json")}
    require(resources <= set(closure["files"]), "packaged_json_closure_incomplete")


def verify_producer(repository, checkout, commit):
    for name in RUNNERS:
        require(lf(local(checkout, name).read_bytes()) == lf(v4.git_blob(repository, commit, name)), "runner_not_committed:" + name)
    for module, name in ((sys.modules[__name__], "scripts/build_card_execution_matrix_v5.py"),
                         (v4, "scripts/build_card_execution_matrix_v4.py")):
        require(lf(Path(module.__file__).read_bytes()) == lf(v4.git_blob(repository, commit, name)), "executing_producer_not_committed:" + name)


def json_safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def recompute_application(entry, records, primary, events, decoded, annotation):
    from rallymate_service.directional_micro_adjustment import compute_directional_micro_adjustment
    from rallymate_service.micro_adjustment_cycles import compute_micro_adjustment_cycles
    calculator = compute_directional_micro_adjustment if entry["route"] == "directional_micro" else compute_micro_adjustment_cycles
    require(entry["route"] in APP_ROUTES, "not_application_route")
    return json_safe(calculator(records, primary, events, decoded, annotation, source_job_id=entry["source_run_id"]))


def verify_application_recomputation(report, recomputed):
    # Source-only invocation intentionally lacks artifact/decoder verification
    # flags. It independently rebuilds all numeric/source outputs from records.
    require(all(report[key] == recomputed[key] for key in ("cards", "fs_card_windows", "summary", "coverage", "overall_score")),
        "application_full_raw_recomputation_mismatch")


def verify_bundle_artifacts(repository, bundle, catalogue, *, runtime_checkout):
    require(bundle["producer"]["path"] == "scripts/build_card_execution_matrix_v5.py"
        and bundle["producer"]["normalized_sha256"] == lf(Path(__file__).read_bytes()), "producer_changed_since_replay")
    v4.verify_text_artifact(local(repository, bundle["input_manifest_path"]), bundle["input_manifest_sha256"],
        bundle["input_manifest_normalized_sha256"], "input_manifest_changed")
    manifest = read_json(local(repository, bundle["input_manifest_path"]))
    validate_input_manifest(repository, manifest)
    v4.validate_input_bindings(bundle, manifest)
    require(set(bundle["inventory_manifest_sha256"]) == set(manifest["inventory_manifests"])
        == set(bundle["inventory_manifest_normalized_sha256"]), "inventory_manifest_set_mismatch")
    for name, expected in bundle["inventory_manifest_sha256"].items():
        v4.verify_text_artifact(local(repository, name), expected, bundle["inventory_manifest_normalized_sha256"][name], "inventory_manifest_changed")
    require(bundle["video_inventory"] == v4.inventory_sources(repository, manifest["inventory_manifests"]), "inventory_source_mismatch")
    rules = {r["indicator_id"]: r for r in catalogue["cards"]}
    acceleration, follow = v4.load_acceleration(runtime_checkout), load_follow(runtime_checkout)
    entries = {e["id"]: e for e in manifest["executions"]}
    from rallymate_service.gs_card_execution import _records
    for group in bundle["executions"]:
        path = local(repository, group["full_report_path"])
        require(sha(path) == group["full_report_sha256"], "full_report_digest_mismatch")
        report = read_json(path)
        rebuilt = compact_report(report, group, rules)
        require(all(group[k] == value for k, value in rebuilt.items()), "compact_evidence_differs_from_full_report")
        require(sha(local(repository, group["local_video"])) == group["video_sha256"], "source_video_changed")
        directory = local(repository, group["run_dir"])
        require(all(sha(directory / name) == expected for name, expected in group["input_artifact_sha256"].items()), "source_artifact_changed")
        for source in group["annotation_sources"]:
            v4.verify_text_artifact(local(repository, source["path"]), source["raw_sha256"], source["utf8_lf_sha256"], "source_annotation_changed")
        annotation, paths = annotation_for(repository, entries[group["id"]], acceleration, follow, verify_media=False)
        require(paths == [s["path"] for s in group["annotation_sources"]]
            and (canonical(annotation) if annotation else None) == group["annotation_canonical_sha256"], "annotation_canonical_digest_mismatch")
        if annotation:
            require(canonical(annotation) == report["input"]["annotation_document_sha256"], "report_annotation_digest_mismatch")
        if group["route"] == "gs_follow_through":
            follow.assert_source_windows(read_json(local(repository, entries[group["id"]]["annotation_manifest"])), report)
        if group["route"] in APP_ROUTES:
            binding = group["decoder_timeline_artifact"]
            decoded_path = local(repository, binding["path"])
            require(sha(decoded_path) == binding["sha256"], "saved_decoder_timeline_changed")
            recomputed = recompute_application(group, _records(directory/"frames.jsonl"), _records(directory/"primary-player.jsonl"),
                _records(directory/"events.jsonl"), read_json(decoded_path), annotation)
            verify_application_recomputation(report, recomputed)
    require(bundle["retirement_audit"] == retirement_audit(read_json(local(repository, manifest["retirement_history"]["path"])),
        manifest["retirement_history"], bundle["executions"]), "retirement_history_evidence_changed")


def replay(repository, commit, manifest_path, output_directory):
    require(not output_directory.exists() and output_directory.is_relative_to(repository), "replay_output_must_be_new_inside_repository")
    manifest = read_json(manifest_path)
    validate_input_manifest(repository, manifest)
    checkout = output_directory / "checkout"
    commit = v4.archive_runtime(repository, commit, checkout)
    verify_producer(repository, checkout, commit)
    v4.load_runtime(checkout)
    from rallymate_service.card_execution import build_card_execution_report, clear_card_execution_cache
    from rallymate_service.gs_card_execution import build_gs_card_execution_report, _decode_video_timeline, _prepare_samples, _records
    from rallymate_service.footwork_cycle_artifacts import build_footwork_cycle_artifact_report
    from rallymate_service.fs_card_windows import build_fs_card_window_report
    from rallymate_service.directional_micro_adjustment import build_directional_micro_adjustment_report
    from rallymate_service.micro_adjustment_cycles import build_micro_adjustment_cycle_report
    from rallymate_scoring.temporal_card_evidence import CARD_CONDITIONS
    clear_card_execution_cache()
    acceleration, follow = v4.load_acceleration(checkout), load_follow(checkout)
    catalogue = read_json(checkout / v4.CATALOGUE)
    rules = {r["indicator_id"]: r for r in catalogue["cards"]}
    inventory = v4.inventory_sources(repository, manifest["inventory_manifests"])
    executions, decoder_cache = [], {}
    for entry in manifest["executions"]:
        directory, video = local(repository, entry["run_dir"]), local(repository, entry["local_video"])
        require(directory.name == entry["source_run_id"] and sha(video) == entry["video_sha256"], "input_source_binding_mismatch")
        names = ["frames.jsonl", "primary-player.jsonl", "summary.json"] + (["events.jsonl"] if entry["route"] in
            {"temporal", "footwork_cycle", "fs_card_windows"} | APP_ROUTES else [])
        artifacts = {name: sha(directory/name) for name in names}
        require(artifacts == entry["input_artifact_sha256"], "frozen_source_artifact_changed")
        annotation, paths = annotation_for(repository, entry, acceleration, follow)
        if entry["route"] == "temporal":
            decoded = _decode_video_timeline(video, entry["video_sha256"])
            _prepare_samples(_records(directory/"frames.jsonl"), _records(directory/"primary-player.jsonl"), decoded,
                video_sha256=entry["video_sha256"], job_id=entry["source_run_id"])
            report = build_card_execution_report(directory)
        else:
            producer = {"footwork_cycle": build_footwork_cycle_artifact_report, "fs_card_windows": build_fs_card_window_report,
                "directional_micro": build_directional_micro_adjustment_report,
                "micro_cycles": build_micro_adjustment_cycle_report}.get(entry["route"], build_gs_card_execution_report)
            report = producer(directory, annotation, video_path=video)
            if entry["route"] == "gs_acceleration":
                frozen = read_json(local(repository, entry["annotation_manifest"]))
                acceleration.assert_source_windows(next(c for c in frozen["cases"] if c["case_id"] == entry["annotation_case_id"]), report)
            if entry["route"] == "gs_follow_through":
                follow.assert_source_windows(read_json(local(repository, entry["annotation_manifest"])), report)
        require(all(sha(directory/name) == digest for name, digest in artifacts.items()), "input_changed_during_replay")
        report = json_safe(report)
        target = output_directory/(entry["id"]+".json")
        write_json(target, report, compact=True)
        compact = compact_report(report, entry, rules)
        compact.update(run_dir=entry["run_dir"], local_video=entry["local_video"], input_artifact_sha256=artifacts,
            full_report_path=relative(repository, target), full_report_sha256=sha(target),
            allowed_card_ids=sorted(CARD_CONDITIONS) if entry["route"] == "temporal" else [],
            annotation_canonical_sha256=canonical(annotation) if annotation else None)
        compact.update(pose_export_id=pose_export_id(compact), source_snapshot_id=snapshot_id(compact))
        compact["annotation_sources"] = [{"path": name, "raw_sha256": sha(local(repository, name)),
            "utf8_lf_sha256": lf(local(repository, name).read_bytes())} for name in paths]
        if entry["route"] in APP_ROUTES:
            digest = entry["video_sha256"]
            if digest not in decoder_cache:
                path = output_directory/(digest+".decoder-timeline.json")
                write_json(path, _decode_video_timeline(video, digest), compact=True)
                decoder_cache[digest] = {"path": relative(repository, path), "sha256": sha(path)}
            compact["decoder_timeline_artifact"] = decoder_cache[digest]
        executions.append(compact)
        print(json.dumps({"execution_id": entry["id"], "instances": compact["instance_count"], "statuses": compact["status_counts"]}), flush=True)
    registry = registry_snapshot(catalogue)
    runtime = v4.source_closure(repository, checkout, commit, RUNNERS)
    verify_closure(repository, runtime)
    rows = [r for g in executions for r in g["instances"]]
    bundle = {"version": BUNDLE_VERSION, "scoring_policy_version": v4.SCORING_POLICY, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "producer": {"path": "scripts/build_card_execution_matrix_v5.py", "raw_sha256": sha(Path(__file__)), "normalized_sha256": lf(Path(__file__).read_bytes())},
        "input_manifest_path": relative(repository, manifest_path), "input_manifest_sha256": sha(manifest_path),
        "input_manifest_normalized_sha256": lf(manifest_path.read_bytes()), "text_manifest_normalization": v4.LF,
        "inventory_manifest_sha256": {name: sha(local(repository, name)) for name in manifest["inventory_manifests"]},
        "inventory_manifest_normalized_sha256": {name: lf(local(repository, name).read_bytes()) for name in manifest["inventory_manifests"]},
        "catalogue_sha256": canonical(catalogue), "catalogue_digest_kind": v4.CANONICAL, "runtime": runtime,
        "registry": registry, "video_inventory": inventory, "executions": executions,
        "overall_score": None, "score_aggregation_scope": SCORE_SCOPE, "score_groups": score_groups(executions),
        "retirement_audit": retirement_audit(read_json(local(repository, manifest["retirement_history"]["path"])), manifest["retirement_history"], executions),
        "summary": {"instances": len(rows), "status_counts": dict(Counter(r["status"] for r in rows)),
            "by_route": dict(Counter(g["route"] for g in executions for _ in g["instances"]))}}
    validate_bundle(bundle, catalogue)
    verify_bundle_artifacts(repository, bundle, catalogue, runtime_checkout=checkout)
    bundle["runtime"] = v4.source_closure(repository, checkout, commit, RUNNERS)
    write_json(output_directory/"results.json", bundle, compact=True)
    write_json(output_directory/"matrix.json", build_matrix(bundle, catalogue), compact=True)
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
        repository, checkout = args.repository.resolve(), args.runtime_checkout.resolve()
        bundle = read_json(args.results)
        verify_closure(repository, bundle["runtime"])
        v4.verify_runtime_checkout(checkout, bundle["runtime"])
        v4.verify_import_surface(repository, checkout, bundle["runtime"]["commit"])
        verify_producer(repository, checkout, bundle["runtime"]["commit"])
        v4.load_runtime(checkout)
        catalogue = read_json(checkout/v4.CATALOGUE)
        verify_bundle_artifacts(repository, bundle, catalogue, runtime_checkout=checkout)
        actual_closure = v4.source_closure(repository, checkout, bundle["runtime"]["commit"], RUNNERS)
        require(all(name in bundle["runtime"]["files"] and hashes["utf8_lf_sha256"] ==
            bundle["runtime"]["files"][name]["utf8_lf_sha256"] for name, hashes in actual_closure["files"].items()),
            "executed_source_missing_from_saved_closure")
        write_json(args.output, build_matrix(bundle, catalogue), compact=True)


if __name__ == "__main__":
    main()
