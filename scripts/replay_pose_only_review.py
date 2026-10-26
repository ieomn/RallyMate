"""Replay the independent pose-only experiment against frozen source labels."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import replay_card_video_validation as shared
from replay_card_video_extension import window_metrics


def bound_existing(candidates, timeline):
    """Bind only candidates read from this verified run, with all-frame identity."""
    by_frame = {r["frame_index"]: r for r in timeline}
    output = []
    identity_keys = ("video_sha256", "person_track_id", "selection_epoch", "camera_reference_epoch")
    for candidate in candidates:
        endpoints = [by_frame.get(candidate.get(k)) for k in ("start_frame", "peak_frame", "end_frame")]
        if any(row is None for row in endpoints):
            continue
        first, peak, last = endpoints
        identity = tuple(peak[k] for k in identity_keys)
        if any(v is None for v in identity) or candidate.get("person_track_id") != peak["person_track_id"]:
            continue
        rows = [r for r in timeline if first["frame_index"] <= r["frame_index"] <= last["frame_index"]]
        if not rows or any(tuple(r[k] for k in identity_keys) != identity for r in rows):
            continue
        if any(abs(float(row["pts"]*row["time_base_num"]*1000/row["time_base_den"])-candidate[k]) > .51
               for row,k in zip(endpoints, ("start_ms", "peak_ms", "end_ms"))):
            continue
        output.append({**candidate, **dict(zip(identity_keys, identity)),
                       "source_context_binding": "verified_same_run_artifact_and_every_interval_frame_identity"})
    return output


def replay_case(case, experiment, unbounded=None):
    from rallymate_scoring.stroke_candidates import recognize_strokes_from_artifacts
    run = ROOT / case["run_dir"]
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    by_track, source = shared.selected_samples(run)
    if not source or any(row["video_sha256"] != case["video_sha256"] for row in source):
        raise ValueError("Case manifest and decoded source video disagree")
    samples = [s for group in by_track.values() for s in group]
    by_frame = {s.frame_index: s for s in samples}
    selections = {r["source_frame_index"]: r for r in shared._read_jsonl(run / "primary-player.jsonl")}
    frames = {r["frame"]["index"]: r for r in shared._read_jsonl(run / "frames.jsonl")}
    timeline = []
    for row in source:
        sample = by_frame.get(row["frame_index"])
        selection = selections[row["frame_index"]]
        timeline.append({**row,
            "person_track_id": selection.get("source_track_id") if selection.get("selection_status") == "selected" and not selection.get("identity_ambiguous") else None,
            "selection_epoch": sample.selection_epoch if sample is not None else None,
            "camera_reference_epoch": frames[row["frame_index"]].get("camera_motion", {}).get("reference_epoch"),
            "identity_semantics": "engineering_selected_subject_segment_not_human_identity_truth"})
    baseline = recognize_strokes_from_artifacts(run / "frames.jsonl", run / "primary-player.jsonl")
    primary, old_review = baseline["candidates"], baseline.get("review_candidates", [])
    existing = bound_existing(primary+old_review, timeline)
    started = time.perf_counter()
    report = experiment.build_pose_only_review_candidates(samples, timeline, existing)
    bounded_seconds = time.perf_counter()-started
    performance = None
    if unbounded is not None:
        started = time.perf_counter()
        reference = unbounded.build_pose_only_review_candidates(samples, timeline, existing)
        reference_seconds = time.perf_counter()-started
        if reference != report:
            raise ValueError("Bounded endpoint enumeration changed candidate semantics")
        performance = {"all_output_fields_identical":True, "bounded_seconds":bounded_seconds,
                       "unbounded_reference_seconds":reference_seconds,
                       "reference_change":"only starts=range(peak-1), ends=range(peak+2,len(segment)); all policies, evidence and calculations identical"}
    candidates = report["candidates"]
    result = {"case_id":case["id"], "split":case["split"], "video_sha256":case["video_sha256"],
        "input_artifact_sha256": {name:shared.sha256(run/name) for name in ("frames.jsonl", "summary.json", "primary-player.jsonl")},
        "primary_candidates":primary, "racket_review_candidates":old_review, "pose_only_report":report,
        "source_frame_count":len(source), "selected_valid_samples":len(samples),
        "pose_model_sha256":summary["models"]["pose_backend"]["model_sha256"],
        "source_clock":{"first":source[0], "last":source[-1], "actual_pts_verified":True, "fps_synthesis":False},
        "negative_window_results":[], "source_grade":None, "score":None}
    if performance is not None:
        result["performance_semantic_comparison"] = performance
    inference_proof = run / "inference-provenance.json"
    result["inference_provenance"] = json.loads(inference_proof.read_text(encoding="utf-8")) if inference_proof.exists() else None
    result["recorded_inference_configuration"] = {"runtime":summary.get("runtime"), "models":summary.get("models"),
                                                 "processing":summary.get("processing")}
    result["weight_file_sha256_at_replay"] = {name:shared.sha256(ROOT/"models"/name)
        for name in ("yolo26n.pt", "yolo26n-pose.pt")}
    if result["weight_file_sha256_at_replay"]["yolo26n-pose.pt"] != result["pose_model_sha256"].lower():
        raise ValueError("Current pose weight no longer agrees with the recorded inference model")
    result["weight_digest_limitation"] = "Pose digest is recorded by inference and cross-checked; detection digest is captured at replay, not a retroactive proof of the first inference weight."
    result["negative_annotation_scope"] = case.get("negative_annotation_scope", "listed_windows_only_not_entire_video_negative_truth")
    annotations = case.get("annotations", [])
    if annotations:
        for name, rows in (("primary_metrics",primary), ("previous_combined_metrics",primary+old_review),
                           ("pose_only_metrics",candidates), ("new_combined_metrics",primary+old_review+candidates)):
            result[name] = window_metrics(rows, annotations)
    for negative in case.get("negative_intervals", []):
        start, end = negative["start_ms"], negative["end_ms"]
        result["negative_window_results"].append({**negative,
            "new_candidate_peaks_inside":[c["candidate_id"] for c in candidates if start <= c["peak_ms"] <= end],
            "new_candidate_windows_overlap":[c["candidate_id"] for c in candidates if max(start,c["start_ms"]) < min(end,c["end_ms"])],
            "baseline_candidate_peaks_inside":[c["candidate_id"] for c in primary+old_review if start <= c["peak_ms"] <= end]})
    result["negative_reviewed_duration_ms"] = sum(n["end_ms"]-n["start_ms"] for n in case.get("negative_intervals", []))
    result["negative_new_peak_count"] = len({i for n in result["negative_window_results"] for i in n["new_candidate_peaks_inside"]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--runtime-root",type=Path,default=ROOT)
    parser.add_argument("--compare-unbounded",action="store_true")
    args = parser.parse_args()
    runtime = args.runtime_root.resolve()
    # Import original production modules only from the explicitly selected
    # baseline. The experiment itself is a separate standard-library module.
    sys.path.insert(0,str(runtime/"src"))
    experiment_path = ROOT / "src/rallymate_scoring/pose_only_swing_review.py"
    spec = importlib.util.spec_from_file_location("independent_pose_review_experiment",experiment_path)
    experiment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experiment)
    unbounded = None
    if args.compare_unbounded:
        import types
        code = experiment_path.read_text(encoding="utf-8")
        before = "starts = range(bisect_left(times, times[peak]-reach), peak-1)\n                ends = range(peak+2, bisect_right(times, times[peak]+reach))"
        after = "starts = range(peak-1)\n                ends = range(peak+2, len(segment))"
        if code.count(before) != 1:
            raise ValueError("Unbounded semantic reference patch no longer matches")
        unbounded = types.ModuleType("unbounded_pose_review_reference")
        exec(compile(code.replace(before,after),"<unbounded_endpoint_enumeration_reference>","exec"),unbounded.__dict__)
    original_decode, cache = shared.decode_source_timeline, {}
    def cached_decode(path,digest):
        if digest not in cache:
            cache[digest] = original_decode(path,digest)
        return cache[digest]
    shared.decode_source_timeline = cached_decode
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    results = []
    for case in manifest["cases"]:
        result = replay_case(case,experiment,unbounded)
        results.append(result)
        print(json.dumps({"case":case["id"], "new_candidates":len(result["pose_only_report"]["candidates"]),
                          "new_matched":result.get("pose_only_metrics",{}).get("matched_count"),
                          "negative_new_peaks":result["negative_new_peak_count"]}),flush=True)
    imported = {name:{"path":str(Path(m.__file__).resolve().relative_to(runtime)).replace("\\","/"),
                      "sha256":shared.sha256(Path(m.__file__))}
                for name,m in sys.modules.items() if name.startswith("rallymate_") and getattr(m,"__file__",None)}
    report = {"schema_version":"pose-only-review-experiment-v1", "created_at_utc":datetime.now(timezone.utc).isoformat(),
        "case_manifest_sha256":shared.sha256(args.manifest),"case_manifest_normalized_sha256":shared.normalized_sha256(args.manifest),
        "experiment_module_sha256":shared.sha256(experiment_path),"experiment_module_normalized_sha256":shared.normalized_sha256(experiment_path),
        "replay_script_sha256":shared.sha256(Path(__file__)),
        "replay_script_normalized_sha256":shared.normalized_sha256(Path(__file__)),
        "replay_helper_normalized_sha256":{name:shared.normalized_sha256(ROOT/"scripts"/name) for name in
            ("replay_card_video_validation.py", "replay_card_video_extension.py")},
        "baseline_imported_modules":imported,
        "policy":experiment.POLICY, "cases":results,
        "limitations":"Motion-window recall, negative-window candidates, hand compatibility and tennis/contact/grade truth are distinct. No scores or confirmed action labels."}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__ == "__main__":
    main()
