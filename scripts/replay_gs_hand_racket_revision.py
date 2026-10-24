"""Replay the fixed MIT M09 windows against the hand/racket observability revision.

This is a new producer. Only annotation construction and source validation are
reused from the historical producer; its replay entry point is never called.
No pose inference, window search, or threshold selection is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

VERSION = "gs-hand-racket-revision-replay-v1.0.0"
RUNTIME_COMMIT = "64c097d9c8ae0bf7fb247b00648204b75519b26b"
ARCHIVED_CATALOGUE_COMMIT = "add42671ad00b3ef3d0026c2f797d38656317435"
REVISION = "gs01-m09-hand-racket-observability-v1.0.0"
CHANGED = {"GS01-M09-04", "GS01-M09-05"}
RAW_FILES = ("frames.jsonl", "primary-player.jsonl", "events.jsonl", "summary.json")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalized_sha256(data):
    return hashlib.sha256(data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return sha256(path)


def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def instances(report):
    return [row for card in report["cards"] for row in card["instances"]]


def _keyed(rows):
    result = {(row["indicator_id"], row["event_id"]): row for row in rows}
    _require(len(result) == len(rows), "duplicate_card_event_instance")
    return result


def compare_revision(report, historical):
    """Check complete observations, not merely the expected count or new status."""
    current, old = _keyed(instances(report)), _keyed(historical["instances"])
    _require(len(current) == 20 and set(current) == set(old), "revision_instance_inventory_changed")
    _require(Counter(row["status"] for row in current.values()) == {"scored": 3, "unavailable": 17}, "revision_status_counts_changed")
    comparisons = []
    for key, row in current.items():
        previous = old[key]
        _require(row["window"] == previous["window"] and row["source_stage_evidence"] == previous["source_stage_evidence"], "revision_source_window_changed")
        if key[0] not in CHANGED:
            _require(row == previous, "unaffected_card_instance_changed")
            comparisons.append({"indicator_id": key[0], "event_id": key[1], "unchanged_entire_instance": True})
            continue
        _require(row.get("scoring_revision_id") == REVISION, "revision_id_missing")
        _require(row["status"] == "unavailable" and row["conditions"] == []
            and all(row.get(field) is None for field in ("grade", "proxy_grade", "score", "original_grade")), "auxiliary_measurement_must_not_score")
        auxiliary = row["auxiliary_observations"]
        _require(auxiliary["scoring_eligible"] is False, "auxiliary_scoring_authorization_invalid")
        measurement = auxiliary["measurements"]["wrist_separation_torso"]
        _require(measurement == previous["conditions"][0]["evidence"]
            and measurement["statistics"].get("delta") == previous["conditions"][0]["value"], "historical_wrist_measurement_changed")
        comparisons.append({"indicator_id": key[0], "event_id": key[1], "old_status": previous["status"],
            "old_score": previous["score"], "new_status": row["status"], "new_score": row["score"],
            "auxiliary_entire_measurement_matches_historical_condition_evidence": True})
    return comparisons


def runtime_sources(runtime_root, repository_root, runtime_commit):
    """Reject dirty/mixed imports; retain exact bytes as well as cross-OS hashes."""
    paths = {runtime_root / "src/rallymate_scoring/data/card_rules_v1.json"}
    for name, module in tuple(sys.modules.items()):
        if name.startswith("rallymate_") or name.startswith("scripts.replay_gs"):
            file = getattr(module, "__file__", None)
            if file and Path(file).resolve() != Path(__file__).resolve():
                paths.add(Path(file).resolve())
    result = {}
    for path in sorted(paths):
        _require(path.is_relative_to(runtime_root), "runtime_import_outside_selected_source_root")
        relative = path.relative_to(runtime_root).as_posix()
        committed = subprocess.check_output(["git", "show", runtime_commit + ":" + relative], cwd=repository_root)
        actual = path.read_bytes()
        _require(normalized_sha256(actual) == normalized_sha256(committed), "runtime_source_differs_from_commit:" + relative)
        result[relative] = {"raw_sha256": sha256(path), "utf8_lf_sha256": normalized_sha256(actual)}
    return result


def replay(runtime_root, artifact_root, repository_root, output_directory, runtime_commit=RUNTIME_COMMIT):
    runtime_root, artifact_root, repository_root, output_directory = [Path(p).resolve()
        for p in (runtime_root, artifact_root, repository_root, output_directory)]
    _require(not output_directory.exists(), "preserve_existing_replay_output")
    sys.path[:0] = [str(runtime_root / "src"), str(runtime_root)]
    from scripts.replay_gs_follow_through_validation import build_annotations, verify_artifacts, assert_source_windows
    from rallymate_service.gs_card_execution import build_gs_card_execution_report
    import rallymate_service.card_execution as execution
    import rallymate_scoring.card_rule_catalog as catalogue

    runtime_sources(runtime_root, repository_root, runtime_commit)
    manifest_path = runtime_root / "tests/fixtures/gs-follow-through-windows-20261024.json"
    historical_path = runtime_root / "tests/fixtures/gs-follow-through-results-20261024.json"
    manifest, historical = _read(manifest_path), _read(historical_path)
    document = build_annotations(manifest)
    review_hashes = verify_artifacts(manifest, artifact_root)  # Actual decoder PTS and original PNG SHA.
    case = manifest["case"]
    run, video = artifact_root / case["run_dir"], artifact_root / case["local_video"]
    before = {name: sha256(run / name) for name in RAW_FILES}
    _require(all(before[name] == digest for name, digest in case["input_artifact_sha256"].items()), "raw_input_digest_changed")
    _require(sha256(video) == case["video_sha256"], "original_video_digest_changed")
    report = build_gs_card_execution_report(run, document, video_path=video)
    assert_source_windows(manifest, report)
    comparisons = compare_revision(report, historical)

    output_directory.mkdir(parents=True)
    _write(output_directory / "full-gs-report.json", report)
    _write(output_directory / "gs-stage-annotations.json", document)
    copied_run = output_directory / "runs" / case["source_run_id"]
    copied_run.mkdir(parents=True)
    for name in RAW_FILES:
        shutil.copyfile(run / name, copied_run / name)
    copied_summary = _read(copied_run / "summary.json")
    migration = {"summary_video_path_changed": copied_summary["input"]["video_path"] != str(video),
        "source_summary_sha256": before["summary.json"], "same_video_sha256": case["video_sha256"],
        "original_summary_unmodified": True}
    if migration["summary_video_path_changed"]:
        copied_summary["input"]["video_path"] = str(video)
        _write(copied_run / "summary.json", copied_summary)
    migration["copied_summary_sha256"] = sha256(copied_run / "summary.json")
    copied_document = deepcopy(document)
    copied_document["input_artifact_sha256"]["summary.json"] = migration["copied_summary_sha256"]
    migration["source_annotations_sha256"] = sha256(output_directory / "gs-stage-annotations.json")
    migration["copied_annotations_sha256"] = _write(output_directory / "unified-gs-stage-annotations.json", copied_document)
    migration["source_annotations_canonical_sha256"] = _canonical(document)
    migration["copied_annotations_canonical_sha256"] = _canonical(copied_document)
    shutil.copyfile(output_directory / "unified-gs-stage-annotations.json", copied_run / "gs-stage-annotations.json")
    execution.clear_card_execution_cache()
    unified = execution.build_card_execution_report(copied_run)
    _require(len(unified["cards"]) == 298, "unified_catalogue_inventory_changed")
    _require(report["input"]["annotation_document_sha256"] == _canonical(document)
        and unified["input"].get("gs_execution", {}).get("annotation_document_sha256") == _canonical(copied_document),
        "direct_or_unified_annotation_digest_mismatch")
    expected_unified = deepcopy(instances(report))
    for row in expected_unified:
        _require(row["source_annotation"]["document_sha256"] == _canonical(document), "direct_instance_annotation_digest_mismatch")
        # Relocation changes this one provenance hash, never the stage, evidence,
        # quality, conditions, or result. The original report remains untouched.
        row["source_annotation"]["document_sha256"] = _canonical(copied_document)
    _require(_keyed([row for row in instances(unified) if row["indicator_id"].startswith("GS")])
        == _keyed(expected_unified), "unified_gs_differs_from_direct_adapter")
    _write(output_directory / "unified-current-catalogue.json", unified)

    # Recompute using the archived catalogue solely to check catalogue-key cache
    # invalidation. This diagnostic does not belong in current result counts.
    old_catalogue = json.loads(subprocess.check_output(["git", "show", ARCHIVED_CATALOGUE_COMMIT
        + ":src/rallymate_scoring/data/card_rules_v1.json"], cwd=repository_root))
    execution.clear_card_execution_cache()
    with patch.object(catalogue, "_catalog", return_value=deepcopy(old_catalogue)):
        old_unified = execution.build_card_execution_report(copied_run)
        first_info = execution._cached_report.cache_info()._asdict()
        repeated = execution.build_card_execution_report(copied_run)
        repeat_info = execution._cached_report.cache_info()._asdict()
        _require(repeated == old_unified and repeat_info["hits"] == first_info["hits"] + 1, "unchanged_input_cache_hit_missing")
    restored = execution.build_card_execution_report(copied_run)
    restored_info = execution._cached_report.cache_info()._asdict()
    _require(restored_info["misses"] == repeat_info["misses"] + 1, "catalogue_change_did_not_invalidate_cache")
    _require(restored == unified and restored["input"]["catalogue_sha256"] != old_unified["input"]["catalogue_sha256"], "current_catalogue_result_not_restored")
    cache = {"scope": "in_memory_archived_catalogue_diagnostic_only_not_current_result",
        "archived_catalogue_commit": ARCHIVED_CATALOGUE_COMMIT,
        "old_report_catalogue_digest": old_unified["input"]["catalogue_sha256"],
        "new_report_catalogue_digest": unified["input"]["catalogue_sha256"],
        "old_cache": first_info, "after_unchanged_repeated_call": repeat_info,
        "after_restored_current_catalogue": restored_info, "current_report_exactly_restored": True}
    _write(output_directory / "cache-diagnostic-archived-catalogue.json", old_unified)
    _write(output_directory / "catalogue-cache-invalidation.json", cache)
    _require({name: sha256(run / name) for name in RAW_FILES} == before
        and sha256(video) == case["video_sha256"], "original_source_changed_during_replay")
    _require(all(sha256(artifact_root / name) == digest for name, digest in review_hashes.items()), "review_evidence_changed_during_replay")
    sources = runtime_sources(runtime_root, repository_root, runtime_commit)
    compact = {"version": VERSION, "runtime_commit": runtime_commit, "source_run_id": case["source_run_id"],
        "video_sha256": case["video_sha256"], "source_input_sha256": before,
        "window_manifest_sha256": sha256(manifest_path), "historical_result_sha256": sha256(historical_path),
        "stage_windows": report["stage_windows"], "instances": instances(report), "comparisons": comparisons,
        "summary": {"instances": 20, "scored": 3, "unavailable": 17, "changed_card_instances": 8,
            "unchanged_card_instances": 12, "source_grade_supported": False, "source_pose_inference_rerun": False},
        "unified_summary": {"card_count": 298, "coverage": unified["coverage"], "summary": unified["summary"], "overall_score": unified["overall_score"]},
        "catalogue_cache_invalidation": cache, "source_path_migration": migration,
        "runtime_source_digests": sources, "review_source_sha256": review_hashes,
        "runner_raw_sha256": sha256(__file__), "runner_utf8_lf_sha256": normalized_sha256(Path(__file__).read_bytes()),
        "output_sha256": {name: sha256(output_directory / name) for name in ("full-gs-report.json", "gs-stage-annotations.json", "unified-gs-stage-annotations.json", "unified-current-catalogue.json")},
        "limitations": ["existing_development_windows_not_new_video_or_heldout", "auxiliary_wrist_geometry_not_hand_racket_contact_or_control", "no_window_expansion_no_threshold_retuning_no_model_inference"]}
    _write(output_directory / "revision-results.json", compact)
    _write(output_directory / "source-closure-receipt.json", {"runtime_commit": runtime_commit,
        "runtime_source_digests": sources, "input_sha256": before, "review_source_sha256": review_hashes,
        "video_sha256": case["video_sha256"], "source_path_migration": migration,
        "runner_sha256": sha256(__file__), "output_sha256": {p.name: sha256(p) for p in output_directory.glob("*.json")},
        "all_assertions_passed": True, "original_inputs_unmodified": True, "new_inference": False})
    return compact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True, help="Git repository containing the immutable source commits")
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--runtime-commit", default=RUNTIME_COMMIT)
    args = parser.parse_args()
    result = replay(args.runtime_root, args.artifact_root, args.repository_root, args.output_directory, args.runtime_commit)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
