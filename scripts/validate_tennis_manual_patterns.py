"""Validate reviewed motion windows without modifying automatic episodes.

This CPU-only experiment reads native poses and original source observations.
Manual timing remains manual evidence, never automatic segmentation success.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()


def code_hashes():
    files = [Path(__file__), *sorted((ROOT / "src/rallymate_service").glob("tennis*.py")),
             ROOT / "src/rallymate_vision/sam3d_evaluation.py"]
    return {relative(path): digest(path) for path in files}


def extract_window(segments, annotation):
    """Require one source-continuous segment; never extend or stitch the window."""
    start, end = annotation["start_ms"], annotation["end_ms"]
    expected = (annotation["person_track_id"], annotation["native_selection_epoch"], annotation["camera_reference_epoch"])
    choices = []
    for segment in segments:
        if not segment or (segment[0]["track"], *segment[0]["reference"]) != expected:
            continue
        if segment[0]["subject_id"] != annotation["subject_id"]:
            continue
        selected = [row for row in segment if start - 1e-6 <= row["time"] <= end + 1e-6]
        if len(selected) < 7:
            continue
        times = np.asarray([row["time"] for row in selected])
        resolution = float(np.median(np.diff(times)))
        # Manual endpoints can fall between observed frames. Permit at most one
        # measured interval of quantization, never a missing action phase.
        if times[0] - start > resolution + 1e-6 or end - times[-1] > resolution + 1e-6:
            continue
        prep_end = int(np.searchsorted(times, annotation["prep_end_ms"], side="right"))
        peak = int(np.argmin(np.abs(times - annotation["peak_ms"])))
        if not 2 <= prep_end <= peak - 2 or peak + 2 >= len(selected):
            continue
        if abs(times[peak] - annotation["peak_ms"]) > resolution / 2 + 1e-6:
            continue
        choices.append((selected, prep_end, peak, resolution))
    if len(choices) != 1:
        return None, "manual_window_not_covered_by_one_bound_subject_segment" if not choices else "manual_window_has_ambiguous_source_segments"
    if any(annotation.get("racket_hand") in (*row.get("wrist_discontinuities", ()), *row.get("wrist_unavailable", ())) for row in choices[0][0]):
        return None, "active_wrist_discontinuity_in_motion"
    return choices[0], None


def validate_annotation(window):
    if (window.get("window_source") != "manual-reviewed-window" or window.get("status") != "reviewed"
            or window.get("method") not in {"human_visual_review", "agent_visual_review"}
            or not isinstance(window.get("reviewer"), str) or not window["reviewer"] or not window.get("evidence")
            or not isinstance(window.get("job_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", window["job_id"])
            or not isinstance(window.get("sample_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", window["sample_id"])
            or not isinstance(window.get("video_sha256"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", window["video_sha256"])
            or not isinstance(window.get("subject_id"), str) or not window["subject_id"]
            or any(type(window.get(key)) is not int for key in ("person_track_id", "native_selection_epoch", "camera_reference_epoch"))
            or window.get("racket_hand") not in {"left", "right"}):
        raise ValueError("manual_review_source_or_identity_invalid")
    values = [window.get(key) for key in ("start_ms", "prep_end_ms", "peak_ms", "end_ms")]
    if not all(type(v) in (int, float) and np.isfinite(v) for v in values) or not 0 <= values[0] < values[1] < values[2] < values[3]:
        raise ValueError("manual_review_phase_times_invalid")
    for field in ('artifact_run_id', 'artifact_job_id'):
        if field in window and (not isinstance(window[field], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', window[field])):
            raise ValueError('manual_artifact_identity_invalid')


def validate_return_evidence(window, frames, sha):
    """Bind visual return context without claiming an automatic ball track.

    Semantic interpretation remains the named visual reviewer's observation.
    The script independently checks its actual source frames, clock, identity,
    camera reference, pixel bounds and event order. A technique string is not
    sufficient to invoke a return-specific profile in this manual experiment.
    """
    if not window['technique'].startswith('return_'):
        return {'status':'not_applicable'}
    def missing(reason):
        return {'status':'not_measured', 'reason':reason, 'automatic_return_context':False}
    proof = window.get('return_evidence')
    if (not isinstance(proof, dict) or proof.get('version') != 'manual-return-context-v1'
            or str(proof.get('source_video_sha256','')).lower() != sha
            or proof.get('receiver_subject_id') != window['subject_id']
            or proof.get('receiver_source_track_id') != window['person_track_id']
            or not isinstance(proof.get('server_subject_description'),str) or not proof['server_subject_description']
            or not isinstance(proof.get('relationship_zh'),str) or not proof['relationship_zh']):
        return missing('manual_return_context_source_or_subject_unverified')
    serves, balls = proof.get('opponent_serve_frames'), proof.get('incoming_ball_frames')
    if (not isinstance(serves,list) or len(serves)<2 or not isinstance(balls,list) or len(balls)<3
            or {r.get('stage') for r in serves if isinstance(r,dict)} != {'preparation','overhead_swing'}):
        return missing('manual_opponent_serve_or_incoming_ball_observations_missing')
    by_index = {}
    for row in frames:
        by_index.setdefault(row.get('frame',{}).get('index'),[]).append(row)
    bound = {'opponent_serve_frames':[], 'incoming_ball_frames':[]}
    for name, annotations in (('opponent_serve_frames',serves),('incoming_ball_frames',balls)):
        previous = -1.
        for row in annotations:
            if not isinstance(row,dict) or type(row.get('source_frame_index')) is not int or type(row.get('timestamp_ms')) not in (int,float):
                return missing('manual_return_source_frame_invalid')
            matches = by_index.get(row['source_frame_index'],[])
            if len(matches)!=1:
                return missing('manual_return_source_frame_missing_or_duplicate')
            source=matches[0]; frame=source['frame']; time=row['timestamp_ms']
            if (not np.isfinite(time) or time<=previous or str(frame.get('video_sha256','')).lower()!=sha
                    or abs(frame.get('timestamp_ms',-1e9)-time)>1.01 or frame.get('timestamp_source')!='decoder_pts'
                    or type(frame.get('pts')) is not int or type(frame.get('time_base_num')) is not int
                    or type(frame.get('time_base_den')) is not int or frame['time_base_num']<=0 or frame['time_base_den']<=0
                    or source.get('camera_motion',{}).get('reference_epoch')!=window['camera_reference_epoch']):
                return missing('manual_return_source_clock_or_camera_mismatch')
            width,height=frame.get('width'),frame.get('height')
            coordinates=row.get('server_bbox_px' if name=='opponent_serve_frames' else 'point_px')
            if (type(width) not in (int,float) or type(height) not in (int,float) or min(width,height)<=0
                    or not isinstance(coordinates,list) or len(coordinates)!=(4 if name=='opponent_serve_frames' else 2)
                    or not all(type(v) in (int,float) and np.isfinite(v) for v in coordinates)
                    or any(not 0<=v<=limit for v,limit in zip(coordinates,[width,height]*2))
                    or len(coordinates)==4 and (coordinates[0]>=coordinates[2] or coordinates[1]>=coordinates[3])):
                return missing('manual_return_pixel_evidence_invalid')
            previous=time
            bound[name].append({**row,'source_pts':frame['pts'],'time_base_num':frame['time_base_num'],'time_base_den':frame['time_base_den']})
    if (serves[0]['stage']!='preparation' or serves[-1]['stage']!='overhead_swing'
            or serves[-1]['timestamp_ms']>=balls[-1]['timestamp_ms']
            or not window['peak_ms']-350<=balls[-1]['timestamp_ms']<=window['peak_ms']+100):
        return missing('manual_return_event_order_unverified')
    return {'status':'review_bound', 'reason':None, 'automatic_return_context':False,
            'method':window['method'], 'reviewer':window['reviewer'], 'source_video_sha256':sha,
            'receiver_subject_id':window['subject_id'], 'receiver_source_track_id':window['person_track_id'],
            'server_subject_description':proof['server_subject_description'], 'relationship_zh':proof['relationship_zh'],**bound}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", type=Path, default=ROOT / ".codex_tmp/tennis-score-round3-20261027/manual-pattern-windows.json")
    parser.add_argument("--timeline-root", type=Path, default=ROOT / ".codex_tmp/tennis-score-round3-20261027/offline-cpu-pass3")
    parser.add_argument("--timeline-layout", choices=('sample_files','run_directory'), default='sample_files')
    parser.add_argument("--summary-artifact", choices=('summary.json','validation-summary.json'), default='summary.json')
    parser.add_argument("--native-root", type=Path, default=ROOT / ".codex_tmp/tennis-score-round2-20261026/frozen-runs")
    parser.add_argument("--frames-root", type=Path, default=ROOT / "service_data/runs")
    parser.add_argument("--inventory", type=Path, default=ROOT / ".codex_tmp/tennis-score-round3-20261027/source-inventory.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".codex_tmp/tennis-score-round3-20261027/manual-pattern-results.json")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refuse to overwrite existing evidence: {args.output}")
    start_code = code_hashes()
    from rallymate_service.tennis_assessment import _segments, _joint_scoped_window
    from rallymate_service.tennis_source_motion import source_motion_by_frame, window_source_motion, bind_reviewed_directions
    from rallymate_service.tennis_motion_patterns import analyze_motion_pattern, TECHNIQUES

    document = read(args.windows)
    if document.get("version") != "manual-reviewed-window-v1":
        raise ValueError("unsupported_manual_window_version")
    inventory = {row["sample_id"]: row for row in read(args.inventory)["sources"]}
    input_paths = {args.windows.resolve(), args.inventory.resolve()}
    input_hashes = {relative(path): digest(path) for path in input_paths}
    output = []
    for window in document["windows"]:
        validate_annotation(window)
        if window.get("technique") not in TECHNIQUES and window.get("technique") != 'unknown':
            raise ValueError("manual_technique_unrecognized")
        sample, job, sha = window["sample_id"], window["job_id"], window["video_sha256"].lower()
        source = inventory[sample]
        if source["job_id"] != job or source["source_sha256"].lower() != sha:
            raise ValueError("manual_source_inventory_mismatch")
        video = ROOT / source["source_relative_path"]
        if digest(video) != sha:
            raise ValueError("manual_source_video_sha_mismatch")
        artifact_run = window.get('artifact_run_id',job)
        paths = {"native": args.native_root / artifact_run / "sam3d_frames.jsonl",
                 "timeline": args.timeline_root / artifact_run / 'primary-player.jsonl' if args.timeline_layout=='run_directory' else args.timeline_root / f"{sample}-primary-player.jsonl",
                 "frames": args.frames_root / artifact_run / "frames.jsonl", "summary": args.native_root / artifact_run / args.summary_artifact}
        before = {relative(path): digest(path) for path in paths.values()}
        input_paths.update(path.resolve() for path in paths.values())
        input_hashes.update(before)
        timeline, native, frames = rows(paths["timeline"]), rows(paths["native"]), rows(paths["frames"])
        summary = read(paths["summary"])
        summary_sha = summary.get('source_sha256') if args.summary_artifact=='validation-summary.json' else summary['minimum_scoring_loop']['provenance']['video_sha256']
        if summary.get("job_id") != window.get('artifact_job_id',artifact_run) or str(summary_sha).lower() != sha:
            raise ValueError("manual_summary_source_mismatch")
        if args.summary_artifact=='validation-summary.json' and (summary.get('original_job_id')!=job
                or any(str(summary.get('artifact_sha256',{}).get(name,'')).lower()!=digest(paths[key]) for key,name in
                       (('native','sam3d_frames.jsonl'),('frames','frames.jsonl'),('timeline','primary-player.jsonl')))):
            raise ValueError('manual_validation_artifact_hash_mismatch')
        segments, segment_reasons = _segments(timeline, native, sha)
        selected, reason = extract_window(segments, window)
        return_context = validate_return_evidence(window,frames,sha)
        result = {"window_id": window["window_id"], "sample_id": sample, "job_id": job, "artifact_run_id":artifact_run,
                  "artifact_job_id":summary['job_id'], "source_video_sha256": sha,
                  "source_video_sha256_verified": True, "source_video_relative_path": relative(video),
                  "window_source": "manual-reviewed-window", "included_in_automatic_assessment": False,
                  "automatic_segmentation_success": False, "classification_method": window["method"], "reviewer": window["reviewer"],
                  "requested_window": {key: window[key] for key in ("start_ms", "prep_end_ms", "peak_ms", "end_ms")},
                  "technique": window["technique"], "technique_review_status":"unverified" if window['technique']=='unknown' else 'reviewed',
                  "racket_hand": window["racket_hand"], "subject_id": window["subject_id"], "return_context_review":return_context,
                  "input_artifact_sha256": before, "source_segment_reasons": dict(segment_reasons),
                  "limitations": ["manual_phase_boundaries_are_visual_estimates", "not_automatic_segmentation_success", "not_expert_technique_ground_truth", "exact_ball_contact_unverified"]}
        if selected is None:
            result.update(status="unavailable", reason=reason)
        else:
            selected_rows, prep_end, peak, resolution = selected
            observations = source_motion_by_frame(native, frames, sha)
            camera_rows, camera_context = window_source_motion(selected_rows, observations, sha)
            selected_rows, camera_rows, missing_wrists = _joint_scoped_window(selected_rows, camera_rows)
            if missing_wrists:
                camera_context["unavailable_wrist_observations"] = missing_wrists
            direction = bind_reviewed_directions(window, observations, camera_context)
            camera_context.update(direction["directions"])
            camera_context["recovery_end_verified"] = window.get("recovery_end_verified") is True
            pattern = analyze_motion_pattern(selected_rows, prep_end=prep_end, peak=peak, side=window["racket_hand"],
                technique=window["technique"], classification_verified=window['technique'] in TECHNIQUES and return_context['status']!='not_measured', source_observations=camera_rows, source_context=camera_context)
            result.update(status="measured" if pattern["metrics"] else "unavailable", reason=None,
                actual_window={"start_ms": selected_rows[0]["time"], "last_preparation_observation_ms": selected_rows[prep_end - 1]["time"],
                               "peak_ms": selected_rows[peak]["time"], "end_ms": selected_rows[-1]["time"], "timing_resolution_ms": resolution},
                source_frame_indices=[row["frame"] for row in selected_rows], source_track_id=selected_rows[0]["track"],
                source_selection_epoch=selected_rows[0]["reference"][0], camera_reference_epoch=selected_rows[0]["reference"][1],
                fixed_camera_observations=len(camera_rows), pattern=pattern,
                unavailable_wrist_observations=missing_wrists,
                direction_context={key: value for key, value in camera_context.items() if "direction" in key},
                direction_binding=direction["evidence"],
                fixed_camera_reference_verified=camera_context.get("camera_reference_verified") is True)
        if before != {relative(path): digest(path) for path in paths.values()}:
            raise RuntimeError("manual_validation_input_changed_during_read")
        output.append(result)
    final_hashes = {relative(path): digest(path) for path in input_paths}
    if input_hashes != final_hashes:
        raise RuntimeError("manual_validation_input_changed_during_execution")
    end_code = code_hashes()
    payload = {"schema_version": "tennis-manual-pattern-validation-v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
               "gpu_requested": False, "automatic_assessments_modified": False, "manual_windows_count": len(output),
               "code_sha256_at_start": start_code, "code_sha256_at_end": end_code, "code_changed_during_execution": start_code != end_code,
               "input_sha256": input_hashes, "input_unchanged": True, "results": output}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"output": relative(args.output), "code_changed_during_execution": start_code != end_code,
                      "results": [{"window_id": r["window_id"], "status": r["status"], "reason": r["reason"],
                                   "measured_metrics": sorted(r.get("pattern", {}).get("metrics", {}))} for r in output]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
