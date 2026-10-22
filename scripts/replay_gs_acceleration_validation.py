"""Replay independently inspected M07 windows using the committed GS adapter.

The fixture contains nonexpert visible boundary estimates, not contact, force,
predicted-ball-trajectory or technical-grade truth. Short stages stay short.
No model inference, thresholds, source records or production files are changed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from rallymate_service.gs_card_execution import (
    ANNOTATION_VERSION, build_gs_card_execution_report, validate_stage_annotations,
)

VERSION = "gs-acceleration-validation-v1.0.0"
WINDOW_VERSION = "gs-acceleration-windows-v1.0.0"
SEMANTICS = "reviewer_estimated_source_stage_not_expert_ground_truth"


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalized_sha256(content):
    return hashlib.sha256(content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def source_ms(frame):
    return Fraction(frame["pts"] * frame["time_base_num"] * 1000, frame["time_base_den"])


def build_annotations(case, annotation_version):
    """Use exact reviewed source frames, never legacy generic swing intervals."""
    document = {"version": ANNOTATION_VERSION, "source_run_id": case["source_run_id"],
        "video_sha256": case["video_sha256"], "input_artifact_sha256": deepcopy(case["input_artifact_sha256"]),
        "actions": [], "stages": []}
    for review in case["reviews"]:
        context = review["action_context"]
        document["actions"].append({"action_id": review["action_id"], "technique_code": case["technique_code"],
            "person_track_id": review["person_track_id"], "start_ms": context["start_ms"], "end_ms": context["end_ms"]})
        if review["decision"] == "phase_unresolved":
            if review.get("end_boundary") is not None:
                raise ValueError("unresolved_boundary_must_not_be_executable")
            continue
        if review["decision"] != "manual_source_stage_candidate" or review["stage_semantics"] != SEMANTICS:
            raise ValueError("invalid_review_decision_or_semantics")
        start, end = (review[key]["estimate"] for key in ("start_boundary", "end_boundary"))
        if (review["source_frames"] != list(range(start["frame_index"], end["frame_index"] + 1))
                or any(row["video_sha256"] != case["video_sha256"] or source_ms(row) != row["timestamp_ms"]
                       for row in (start, end))):
            raise ValueError("review_source_frame_or_pts_binding_mismatch")
        for key in ("start_boundary", "end_boundary"):
            boundary = review[key]
            lower, upper = boundary["bracket"]
            if (boundary["bracket_source_frames"] != [lower["frame_index"], upper["frame_index"]]
                    or not source_ms(lower) <= source_ms(boundary["estimate"]) <= source_ms(upper)
                    or max(abs(source_ms(boundary["estimate"]) - source_ms(edge)) for edge in (lower, upper)) > review["uncertainty_ms"]):
                raise ValueError("review_uncertainty_does_not_cover_boundary_bracket")
        document["stages"].append({"annotation_id": review["annotation_id"], "action_id": review["action_id"],
            "technique_code": case["technique_code"], "stage_code": case["stage_code"],
            "person_track_id": review["person_track_id"], "start_ms": float(source_ms(start)), "end_ms": float(source_ms(end)),
            "binding": {"method": "manual_anchor", "version": annotation_version,
                "source": "gs-acceleration-windows-20261022.json#" + review["annotation_id"],
                "uncertainty_ms": review["uncertainty_ms"], "semantics": SEMANTICS}})
    return validate_stage_annotations(document)


def assert_source_windows(case, report):
    actual = {row["annotation_id"]: row for row in report["stage_windows"]}
    accepted = [row for row in case["reviews"] if row["decision"] == "manual_source_stage_candidate"]
    if set(actual) != {row["annotation_id"] for row in accepted}:
        raise ValueError("stage_annotation_inventory_changed")
    for review in accepted:
        window = actual[review["annotation_id"]]
        if window["source_frames"] != review["source_frames"]:
            raise ValueError("stage_frame_window_widened_or_shrunk")
        for edge, source in ((window["source_pts"][0], review["start_boundary"]["estimate"]),
                             (window["source_pts"][-1], review["end_boundary"]["estimate"])):
            if any(edge[key] != source[key] for key in ("frame_index", "pts", "time_base_num", "time_base_den")):
                raise ValueError("stage_exact_decoder_pts_changed")


def compact_case(case, report):
    assert_source_windows(case, report)
    instances = [instance for row in report["cards"] for instance in row["instances"]]
    return {"case_id": case["case_id"], "source_run_id": report["source_run_id"],
        "stage_code": case["stage_code"], "video_sha256": case["video_sha256"],
        "input": report["input"], "coverage": report["coverage"],
        "status_counts": dict(sorted(Counter(row["status"] for row in instances).items())),
        "reason_counts": dict(sorted(Counter(reason for row in instances for reason in row.get("reason_codes", [])).items())),
        "executed_card_ids": sorted({row["indicator_id"] for row in instances}),
        "stage_windows": report["stage_windows"], "instances": instances,
        "unresolved_candidates": [deepcopy(row) for row in case["reviews"] if row["decision"] == "phase_unresolved"],
        "summary": report["summary"], "overall_score": report["overall_score"]}


def runtime_provenance(repository, commit):
    """Verify every imported rallymate module against the requested Git commit."""
    paths = {Path(module.__file__).resolve() for name, module in list(sys.modules.items())
             if name.startswith("rallymate") and getattr(module, "__file__", None)}
    import rallymate_service.gs_card_execution as adapter
    runtime_root = Path(adapter.__file__).resolve().parents[2]
    paths.add(runtime_root / "src/rallymate_scoring/data/card_rules_v1.json")
    output = {}
    for path in sorted(paths):
        if not path.is_relative_to(runtime_root / "src"):
            raise ValueError("runtime_module_outside_selected_checkout")
        relative = path.relative_to(runtime_root).as_posix()
        content = path.read_bytes()
        committed = subprocess.check_output(["git", "show", commit + ":" + relative], cwd=repository)
        if normalized_sha256(content) != normalized_sha256(committed):
            raise ValueError("runtime_differs_from_committed_source:" + relative)
        output[relative] = {"raw_sha256": hashlib.sha256(content).hexdigest(),
            "utf8_lf_normalized_sha256": normalized_sha256(content),
            "committed_blob_raw_sha256": hashlib.sha256(committed).hexdigest()}
    return {"base_commit": commit, "all_imported_source_matches_commit": True,
        "normalized_digest_kind": "utf8_sig_removed_crlf_and_cr_to_lf_sha256", "modules": output}


def verify_review_artifacts(case, artifact_root):
    if sha256(artifact_root / case["local_video"]) != case["video_sha256"]:
        raise ValueError("review_video_digest_changed")
    for review in case["reviews"]:
        inspected = review["inspection"]
        if sha256(artifact_root / inspected["sheet"]) != inspected["sha256"]:
            raise ValueError("review_contact_sheet_changed")
        frames = []
        if review["decision"] == "phase_unresolved":
            frames.append(review["visible_start_candidate"])
        else:
            for key in ("start_boundary", "end_boundary"):
                frames.extend([review[key]["estimate"], *review[key]["bracket"]])
        for frame in frames:
            if sha256(artifact_root / frame["frame_image_path"]) != frame["frame_image_sha256"]:
                raise ValueError("review_source_frame_image_changed")


def replay(manifest_path, artifact_root, output_directory):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["version"] != WINDOW_VERSION or manifest["annotation_frozen_before_scoring"] is not True:
        raise ValueError("review_manifest_not_frozen")
    commit = manifest["runtime_base_commit"]
    runtime_provenance(artifact_root, commit)
    output_directory.mkdir(parents=True, exist_ok=True)
    cases, reports = [], {}
    for case in manifest["cases"]:
        verify_review_artifacts(case, artifact_root)
        annotation = build_annotations(case, manifest["annotation_version"])
        report = build_gs_card_execution_report(artifact_root / case["run_dir"], annotation,
            video_path=artifact_root / case["local_video"])
        compact = compact_case(case, report)
        cases.append(compact)
        report_path = output_directory / (case["case_id"] + ".report.json")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        reports[report_path.name] = sha256(report_path)
        print(json.dumps({"case_id": case["case_id"], "status_counts": compact["status_counts"]}), flush=True)
    instances = [row for case in cases for row in case["instances"]]
    result = {"version": VERSION, "window_manifest_sha256": sha256(manifest_path),
        "runtime": runtime_provenance(artifact_root, commit),
        "runner_sha256": sha256(Path(__file__)),
        "runner_digest_kind": "raw_file_sha256", "full_report_sha256": reports,
        "policy": deepcopy(manifest["policy"]), "cases": cases,
        "summary": {"reviewed_action_count": sum(len(case["reviews"]) for case in manifest["cases"]),
            "manual_stage_window_count": sum(len(case["stage_windows"]) for case in cases),
            "phase_unresolved_count": sum(len(case["unresolved_candidates"]) for case in cases),
            "new_stage_card_ids": sorted({row["indicator_id"] for row in instances}),
            "card_instance_count": len(instances),
            "instance_status_counts": dict(sorted(Counter(row["status"] for row in instances).items())),
            "reason_counts": dict(sorted(Counter(reason for row in instances for reason in row.get("reason_codes", [])).items())),
            "original_graded_instance_count": sum(row["original_grade"] is not None for row in instances),
            "overall_score": None}}
    result_path = output_directory / "gs-acceleration-results-20261022.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False), flush=True)
    return result_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    replay(args.manifest.resolve(), args.artifact_root.resolve(), args.output_directory.resolve())


if __name__ == "__main__":
    main()
