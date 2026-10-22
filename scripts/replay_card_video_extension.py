"""Replay separately catalogued slice/footwork evidence without tuning production.

Media remains local. Visible motion windows are not certified source-card stages;
the optional kinematics output deliberately contains no card evaluation or grade.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from replay_card_video_validation import (match_action_windows, normalized_sha256,
                                         replay_case, selected_samples, sha256)


def window_metrics(candidates, annotations):
    result = match_action_windows(candidates, annotations)
    result["correct_action_classification_count"] = sum(
        m["classification_status"] == "correct" for m in result["matches"])
    result["incorrect_action_classification_count"] = sum(
        m["classification_status"] == "incorrect" for m in result["matches"])
    by_id = {a["id"]: a for a in annotations}
    result["anchor_within_declared_uncertainty_count"] = sum(
        abs(m["anchor_error_ms"]) <= by_id[m["annotation_id"]]["anchor_uncertainty_ms"]
        for m in result["matches"])
    return result


def replay_extension_case(case):
    from rallymate_scoring.card_kinematics import measure_card_window
    from rallymate_scoring.stroke_candidates import recognize_strokes_from_artifacts

    result = replay_case(case)
    if result["status"] != "replayed_real_observations":
        return result
    run = ROOT / case["run_dir"]
    run_summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    result["fresh_inference_evidence"] = {
        "runtime": run_summary.get("runtime"), "processing": run_summary.get("processing"),
        "detection_policy": run_summary.get("models", {}).get("detection_policy"),
        "weight_sha256": {name: sha256(ROOT / "models" / name) for name in ("yolo26n.pt", "yolo26n-pose.pt")},
        "weight_digest_note": "Local weight file digest captured during replay; pose digest must equal run-declared model digest"}
    if result["pose_model_sha256"].lower() != result["fresh_inference_evidence"]["weight_sha256"]["yolo26n-pose.pt"]:
        raise ValueError("Pose weight changed since inference")
    recognition = recognize_strokes_from_artifacts(run / "frames.jsonl", run / "primary-player.jsonl")
    primary, fallback = recognition["candidates"], recognition.get("review_candidates", [])
    episode_keys = ("episode_id", "start_ms", "peak_ms", "end_ms", "classification", "racket_hand",
                    "racket_hand_candidate", "wrist_side", "family")
    episodes = [{**{k: e[k] for k in episode_keys if k in e},
                 "candidate_id": e.get("episode_id"), "route": "estimated_phase"}
                for family in recognition["motion_analysis"]["families"].values() for e in family["episodes"]]
    result["primary_candidates"] = primary
    result["review_candidates"] = fallback
    result["estimated_phase_candidates"] = episodes
    result["source_interval_ms"] = case["source_interval_ms"]
    result["declared_family"] = case["declared_family"]
    result["tuning_performed"] = False
    result["original_card_grade"] = None
    result["original_stage_established_count"] = 0
    result["reviewer_stage_observability"] = case.get("stage_observability", [])
    result["candidate_crosses_manual_shot_cut"] = [c["candidate_id"] for c in primary + fallback
        if any(c["start_ms"] < t < c["end_ms"] for t in case.get("manual_shot_boundaries_ms", []))]
    if case["annotations"]:
        result["primary_window_metrics"] = window_metrics(primary, case["annotations"])
        result["with_review_fallback_metrics"] = window_metrics(primary + fallback, case["annotations"])
        result["estimated_phase_anchor_metrics"] = window_metrics(episodes, case["annotations"])
        result["estimated_phase_with_review_metrics"] = window_metrics(episodes + fallback, case["annotations"])
        result["boundary_sensitivity"] = {}
        for direction in (-1, 1):
            refs = [{**a, "start_ms": a["start_ms"] - direction*a["boundary_uncertainty_ms"],
                     "end_ms": a["end_ms"] + direction*a["boundary_uncertainty_ms"]}
                    for a in case["annotations"]]
            if any(a["start_ms"] >= a["end_ms"] for a in refs):
                raise ValueError("Annotation uncertainty collapses a window")
            result["boundary_sensitivity"]["contracted" if direction < 0 else "expanded"] = {
                "primary": window_metrics(primary, refs), "with_review": window_metrics(primary + fallback, refs),
                "estimated_phase": window_metrics(episodes, refs),
                "estimated_phase_with_review": window_metrics(episodes + fallback, refs)}
    frames = [json.loads(line) for line in (run / "frames.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    primary_summary = json.loads((run / "primary-player-summary.json").read_text(encoding="utf-8"))["diagnostics"]
    result["primary_observation_diagnostics"] = {k: primary_summary.get(k) for k in (
        "source_track_ids", "track_coverage_fraction", "pose_coverage_fraction", "keypoint_valid_fraction",
        "source_track_switch_candidate_count", "primary_identity_ambiguous", "quality_flags")}
    tracks, timeline = selected_samples(run)
    if len(timeline) != len(frames) or any(b["frame_index"] != a["frame_index"] + 1 for a,b in zip(timeline,timeline[1:])):
        raise ValueError("Full original frame denominator is not contiguous")
    result["decoded_clock_verification"] = {"status": "verified_against_original_media_pts",
        "processed_original_frame_count": len(timeline), "first": timeline[0], "last": timeline[-1],
        "source_frames_contiguous": True, "no_fps_synthesized_pts": True}
    result["camera_reference_epoch_count"] = len({f.get("camera_motion", {}).get("reference_epoch") for f in frames})
    result["camera_status_counts"] = dict(Counter(f.get("camera_motion", {}).get("status") for f in frames))
    result["visual_window_measurements"] = []
    for annotation in case.get("visual_phase_windows", []):
        rows = [r for r in timeline if annotation["start_ms"] <= r["timestamp_ms"] <= annotation["end_ms"]]
        samples = [s for group in tracks.values() for s in group if annotation["start_ms"] <= s.time <= annotation["end_ms"]]
        if not rows or not samples:
            result["visual_window_measurements"].append({"annotation": annotation, "status": "unavailable_no_observations"})
            continue
        first = samples[0]
        window = dict(start_ms=rows[0]["timestamp_ms"], end_ms=rows[-1]["timestamp_ms"],
                      person_track_id=first.track, selection_epoch=first.selection_epoch,
                      camera_reference_epoch=first.camera_reference_epoch, camera_compensated=first.camera_compensated,
                      video_sha256=case["video_sha256"], racket_side="right")
        result["visual_window_measurements"].append({"annotation": annotation,
            "measurement": measure_card_window(samples, window, rows), "original_grade": None,
            "card_evaluation_status": "not_requested_source_card_phase_boundary_unestablished"})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/fixtures/card-video-cases-extension-20261022.json")
    parser.add_argument("--output", type=Path, default=ROOT / "tests/fixtures/card-video-validation-results-extension-20261022.json")
    parser.add_argument("--footwork-review", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    modules = ["stroke_candidates.py", "stroke_review_fallback.py", "temporal_recognition.py",
               "card_kinematics.py", "card_rule_catalog.py", "data/card_rules_v1.json"]
    result = dict(schema_version="card-video-validation-extension-v1", created_at_utc=datetime.now(timezone.utc).isoformat(),
        case_manifest_sha256=sha256(args.manifest), case_manifest_normalized_sha256=normalized_sha256(args.manifest),
        replay_script_sha256=sha256(Path(__file__)), replay_script_normalized_sha256=normalized_sha256(Path(__file__)),
        shared_replay_script_sha256=sha256(ROOT / "scripts/replay_card_video_validation.py"),
        catalogue_digest_kind="raw_file_sha256", module_digest_kind="raw_file_sha256", normalized_digest_kind="utf8_lf_sha256",
        module_sha256={name: sha256(ROOT / "src/rallymate_scoring" / name) for name in modules},
        module_normalized_sha256={name: normalized_sha256(ROOT / "src/rallymate_scoring" / name) for name in modules},
        annotation_policy=manifest["annotation_policy"],
        matching_policy=dict(method="maximum_one_to_one", minimum_reference_window_overlap=.2,
                             scope="visible_motion_windows_not_contact_or_original_card_phases",
                             sensitivity="contract_and_expand_each_reference_by_declared_nonexpert_boundary_uncertainty"),
        cases=[replay_extension_case(c) for c in manifest["cases"]])
    if args.footwork_review:
        review = json.loads(args.footwork_review.read_text(encoding="utf-8"))
        target = next((c for c in result["cases"] if c["case_id"] == review.get("case_id")), None)
        if (target is None or target["split"] != "held_out_validation"
                or review.get("video_sha256") != target["source_video_sha256"]
                or review.get("frames_sha256") != target["input_artifact_sha256"]["frames.jsonl"]):
            raise ValueError("Independent review is not bound to the held-out video and frame artifact")
        result["independent_footwork_review"] = review
        result["independent_footwork_review_sha256"] = sha256(args.footwork_review)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    for case in result["cases"]:
        print(json.dumps({k: case.get(k) for k in ("case_id", "primary_candidate_count", "fallback_review_candidate_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
