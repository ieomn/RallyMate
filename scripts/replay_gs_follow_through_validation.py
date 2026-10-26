"""Replay four frozen MIT forehand M09 candidates without extending their frames.

The new parent contexts are explicit source-boundary revisions. They do not
rewrite historical preparation/M07 annotations or establish racket control.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from rallymate_service.gs_card_execution import (
    ANNOTATION_VERSION, _decode_video_timeline, build_gs_card_execution_report,
    validate_stage_annotations,
)
from scripts.replay_gs_acceleration_validation import (
    normalized_sha256, runtime_provenance, sha256, source_ms,
)

VERSION = "gs-follow-through-validation-v1.0.0"
WINDOW_VERSION = "gs-follow-through-windows-v1.0.0"
FROZEN_SHA = "8e109efaaf878bdeb15a9909b2d3bad536cd7055af9f6b89ee163daf24c62c79"
FIXED_WINDOWS = ((40, 45), (103, 107), (162, 166), (224, 229))
SEMANTICS = "reviewer_estimated_source_stage_not_expert_ground_truth"


def validate_manifest(manifest):
    if (manifest.get("version") != WINDOW_VERSION or manifest.get("annotation_frozen_before_scoring") is not True
            or manifest.get("frozen_boundary_source", {}).get("raw_sha256") != FROZEN_SHA):
        raise ValueError("follow_through_frozen_manifest_invalid")
    case, frozen = manifest["case"], manifest["frozen_boundary_source"]["document"]
    reviews = frozen["cases"]
    if len(reviews) != 4 or frozen["video_sha256"] != case["video_sha256"] or frozen["source_run_id"] != case["source_run_id"]:
        raise ValueError("follow_through_source_inventory_mismatch")
    for review, (first, last) in zip(reviews, FIXED_WINDOWS):
        if (review["source_frames"] != list(range(first, last+1)) or review["source_frame_count"] != last-first+1
                or review["start_estimate"]["frame_index"] != first or review["end_estimate"]["frame_index"] != last
                or review["candidate_stage_code"] != "GS01-M09" or review["technique_code"] != "GS01"
                or review["contact_verified"] is not False or review["racket_control_verified"] is not False):
            raise ValueError("follow_through_frozen_frames_or_semantics_changed")
        for kind in ("start", "end"):
            estimate, bracket = review[kind+"_estimate"], review[kind+"_bracket"]
            if (len(bracket) != 2 or not source_ms(bracket[0]) <= source_ms(estimate) <= source_ms(bracket[1])
                    or any(row["video_sha256"] != case["video_sha256"] or source_ms(row) != row["timestamp_ms"]
                           for row in [estimate, *bracket])):
                raise ValueError("follow_through_boundary_pts_or_bracket_invalid")
        old, revised = review["old_parent"], review["proposed_parent_revision"]
        if (old["action_id"] != review["action_id"] or revised["revision_kind"] != "new_source_full_action_boundary_revision"
                or revised["start_ms"] != old["start_ms"] or revised["end_ms"] != source_ms(review["end_bracket"][-1])
                or not old["end_ms"] < source_ms(review["end_estimate"]) <= revised["end_ms"]):
            raise ValueError("follow_through_explicit_parent_revision_required")
    return manifest


def build_annotations(manifest):
    validate_manifest(manifest)
    case = manifest["case"]
    document = {"version": ANNOTATION_VERSION, "source_run_id": case["source_run_id"],
        "video_sha256": case["video_sha256"], "input_artifact_sha256": deepcopy(case["input_artifact_sha256"]),
        "actions": [], "stages": []}
    for review in manifest["frozen_boundary_source"]["document"]["cases"]:
        action_id = review["action_id"] + "-m09-source-revision-v1"
        revised = review["proposed_parent_revision"]
        document["actions"].append({"action_id": action_id, "technique_code": "GS01",
            "person_track_id": review["old_parent"]["person_track_id"],
            "start_ms": revised["start_ms"], "end_ms": revised["end_ms"]})
        uncertainty = max(abs(source_ms(review[kind+"_estimate"])-source_ms(edge))
            for kind in ("start", "end") for edge in review[kind+"_bracket"])
        document["stages"].append({"annotation_id": review["action_id"] + "-m09-visible-follow-through-v1",
            "action_id": action_id, "technique_code": "GS01", "stage_code": "GS01-M09",
            "person_track_id": review["old_parent"]["person_track_id"],
            "start_ms": float(source_ms(review["start_estimate"])), "end_ms": float(source_ms(review["end_estimate"])),
            "binding": {"method": "manual_anchor", "version": manifest["annotation_version"],
                "source": "gs-follow-through-windows-20261024.json#" + review["action_id"]
                    + "; nonexpert visible separation to hand/handle convergence; contact and racket control unverified",
                "uncertainty_ms": float(uncertainty), "semantics": SEMANTICS}})
    return validate_stage_annotations(document)


def assert_source_windows(manifest, report):
    expected = build_annotations(manifest)
    windows = {row["annotation_id"]: row for row in report["stage_windows"]}
    if set(windows) != {row["annotation_id"] for row in expected["stages"]}:
        raise ValueError("follow_through_stage_inventory_changed")
    for review, stage in zip(manifest["frozen_boundary_source"]["document"]["cases"], expected["stages"]):
        row = windows[stage["annotation_id"]]
        if (row["source_frames"] != review["source_frames"] or row["action_id"] != stage["action_id"]
                or row["requested_window_ms"] != {key: stage[key] for key in ("start_ms", "end_ms")}):
            raise ValueError("follow_through_stage_widened_shrunk_or_rebound")
        for original, actual in ((review["start_estimate"], row["source_pts"][0]), (review["end_estimate"], row["source_pts"][-1])):
            if any(original[key] != actual[key] for key in ("frame_index", "pts", "time_base_num", "time_base_den")):
                raise ValueError("follow_through_exact_pts_changed")


def _artifact(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact_path_must_be_relative")
    return root/path


def verify_artifacts(manifest, artifact_root):
    validate_manifest(manifest)
    frozen = manifest["frozen_boundary_source"]
    file = _artifact(artifact_root, frozen["artifact_path"])
    if sha256(file) != FROZEN_SHA or json.loads(file.read_text(encoding="utf-8")) != frozen["document"]:
        raise ValueError("original_frozen_boundary_document_changed")
    artifacts = {frozen["artifact_path"]: sha256(file)}
    for name, spec in manifest["historical_inputs"].items():
        path = _artifact(artifact_root, name)
        if normalized_sha256(path.read_bytes()) != spec["utf8_lf_sha256"]:
            raise ValueError("historical_preparation_or_m07_changed")
        artifacts[name] = sha256(path)
    case = manifest["case"]
    video = _artifact(artifact_root, case["local_video"])
    if sha256(video) != case["video_sha256"]:
        raise ValueError("follow_through_original_video_changed")
    decoded = {row["frame_index"]: row for row in _decode_video_timeline(video, case["video_sha256"])}
    for review in frozen["document"]["cases"]:
        images = [(review["existing_sheet_path"], review["existing_sheet_sha256"])]
        for kind in ("start", "end"):
            for frame in [review[kind+"_estimate"], *review[kind+"_bracket"]]:
                row = decoded.get(frame["frame_index"], {})
                if any(frame[key] != row.get(key) for key in ("pts", "time_base_num", "time_base_den", "video_sha256")):
                    raise ValueError("original_boundary_not_in_fresh_decoder_ledger")
                images.append((frame["existing_png_path"], frame["existing_png_sha256"]))
        for name, digest in images:
            if sha256(_artifact(artifact_root, name)) != digest:
                raise ValueError("follow_through_original_review_image_changed")
            artifacts[name] = digest
    return artifacts


def compact_report(manifest, report):
    assert_source_windows(manifest, report)
    instances = [instance for card in report["cards"] for instance in card["instances"]]
    if len(instances) != 20 or {row["indicator_id"] for row in instances} != {f"GS01-M09-0{i}" for i in range(1, 6)}:
        raise ValueError("follow_through_card_inventory_changed")
    diagnostics = [{"event_id": row["event_id"], "indicator_id": row["indicator_id"],
        "status": row["status"], "grade": row.get("grade"), "score": row["score"], "reason_codes": row["reason_codes"],
        "conditions": [{"metric": c["metric"], "value": c["value"], "passed": c["passed"],
            "source_frame_count": c.get("evidence", {}).get("total_frame_count"),
            "valid_frame_count": c.get("evidence", {}).get("valid_frame_count"),
            "metric_reason_codes": c.get("evidence", {}).get("reason_codes", []), "reason": c.get("reason")}
            for c in row["conditions"]]} for row in instances]
    return {"source_run_id": report["source_run_id"], "video_sha256": manifest["case"]["video_sha256"],
        "input": report["input"], "coverage": report["coverage"], "stage_windows": report["stage_windows"],
        "instances": instances, "condition_diagnostics": diagnostics,
        "summary": {"manual_stage_window_count": 4, "card_instance_count": len(instances),
            "unique_card_count": 5, "status_counts": dict(sorted(Counter(row["status"] for row in instances).items())),
            "original_graded_instance_count": sum(row["original_grade"] is not None for row in instances),
            "independent_behavior_count_claimed": False,
            "original_contact_or_control_verified": False},
        "raw_adapter_summary": report["summary"], "raw_adapter_overall_score": report["overall_score"],
        "summary_limitation": "Raw adapter arithmetic repeats the same wrist-approach predicate for M09-04 and M09-05; it is not independent evidence of racket control."}


def replay(manifest_path, artifact_root, output_directory):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime_provenance(artifact_root, manifest["runtime_base_commit"])
    before = verify_artifacts(manifest, artifact_root)
    case, document = manifest["case"], build_annotations(manifest)
    report = build_gs_card_execution_report(_artifact(artifact_root, case["run_dir"]), document,
        video_path=_artifact(artifact_root, case["local_video"]))
    compact = compact_report(manifest, report)
    if any(sha256(_artifact(artifact_root, name)) != digest for name, digest in before.items()):
        raise ValueError("review_source_changed_during_replay")
    output_directory.mkdir(parents=True, exist_ok=True)
    report_file, annotation_file = output_directory/"full-gs-report.json", output_directory/"gs-stage-annotations.json"
    for path, value in ((report_file, report), (annotation_file, document)):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    result = {"version": VERSION, "window_manifest_sha256": sha256(manifest_path),
        "window_manifest_utf8_lf_sha256": normalized_sha256(manifest_path.read_bytes()),
        "digest_normalization": "utf8_sig_removed_crlf_and_cr_to_lf_sha256", "frozen_boundary_sha256": FROZEN_SHA,
        "runtime": runtime_provenance(artifact_root, manifest["runtime_base_commit"]),
        "runner_raw_sha256": sha256(Path(__file__)), "runner_utf8_lf_sha256": normalized_sha256(Path(__file__).read_bytes()),
        "review_artifact_sha256": before, "full_report_sha256": sha256(report_file),
        "annotation_sha256": sha256(annotation_file), "policy": deepcopy(manifest["policy"]), **compact}
    result_file = output_directory/"gs-follow-through-results-20261024.json"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False), flush=True)
    return result_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    replay(args.manifest.resolve(), args.artifact_root.resolve(), args.output_directory.resolve())


if __name__ == "__main__":
    main()
