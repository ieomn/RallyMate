"""Source-bound FULL-TEST validation through the actual local SAM service.

Inventory and submissions are resumable. Existing inference is reused only when
its complete source SHA, full frame interval and native SAM archive match. Scores
are recomputed with the current working-tree scorer, never copied from old reports.
No videos or model assets are modified, and no service is restarted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def inventory(directory):
    import cv2
    rows = []
    for path in sorted(directory.glob("*.mp4")):
        cap = cv2.VideoCapture(str(path))
        try:
            if not cap.isOpened():
                raise ValueError(f"Video cannot be opened: {path.name}")
            count, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
            if count <= 0 or fps <= 0:
                raise ValueError(f"Missing source frame metadata: {path.name}")
            rows.append({"name": path.name, "source": str(path.resolve()), "sha256": sha256(path),
                         "frames": count, "fps": fps, "duration_s": count / fps,
                         "width": int(cap.get(3)), "height": int(cap.get(4))})
        finally:
            cap.release()
    if not rows:
        raise ValueError("FULL-TEST contains no MP4 videos")
    return rows


def database_jobs(database):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("""
            select id, status, original_filename, output_dir, completed_at,
              json_extract(summary_json, '$.processing.processed_frames') as processed_frames,
              json_extract(summary_json, '$.processing.source_start_frame') as source_start_frame,
              json_extract(summary_json, '$.processing.source_end_frame_exclusive') as source_end_frame_exclusive,
              json_extract(summary_json, '$.processing.frame_stride') as frame_stride,
              json_extract(summary_json, '$.minimum_scoring_loop.provenance.video_sha256') as video_sha256,
              json_extract(summary_json, '$.models.pose_backend.backend') as pose_backend
            from jobs order by completed_at desc
        """)]


def matching_run(video, jobs):
    for job in jobs:
        if (job["status"] == "succeeded" and str(job["video_sha256"]).lower() == video["sha256"]
                and job["processed_frames"] == video["frames"] and job["source_start_frame"] == 0
                and job["source_end_frame_exclusive"] == video["frames"] and job["frame_stride"] == 1
                and job["pose_backend"] == "sam3d"
                and (Path(job["output_dir"]) / "sam3d_frames.jsonl").is_file()):
            return job
    return None


def request(base, path, method="GET", payload=None, headers=None):
    headers = {"Origin": base, **(headers or {})}
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(base + path, data=payload, method=method, headers=headers), timeout=120) as response:
        return json.load(response)


def submit(video, out, base):
    receipt_path = out / "submissions" / (Path(video["name"]).stem + ".json")
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["sha256"] != video["sha256"]:
            raise ValueError("Existing submission is bound to different video bytes")
        if receipt.get("job_submitted"):
            return receipt
        upload_id = receipt["job_id"]
    else:
        upload_id = str(uuid.uuid4())
        receipt = {**video, "job_id": upload_id, "job_submitted": False, "created_utc": now(),
                   "base": base, "full_video": True, "frame_stride": 1}
        write_json(receipt_path, receipt)
    session = request(base, "/v1/uploads", "POST", {"upload_id": upload_id, "filename": video["name"],
        "size": Path(video["source"]).stat().st_size,
        "options": {"court_mode": "disabled", "max_players": 2, "write_annotated_video": True, "start_ms": 0}})
    def chunk(index):
        with Path(video["source"]).open("rb") as source:
            source.seek(index * session["chunk_size"])
            data = source.read(session["chunk_size"])
        return request(base, f"/v1/uploads/{upload_id}/chunks/{index}", "PUT", data,
                       {"Content-Type": "application/octet-stream", "X-Chunk-SHA256": hashlib.sha256(data).hexdigest(),
                        "X-Upload-Acknowledgement": "chunk-v1"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(chunk, range(session["chunk_count"])))
    job = request(base, f"/v1/uploads/{upload_id}/complete", "POST")
    if job["id"] != upload_id:
        raise ValueError("Upload completion returned a different job ID")
    receipt.update(job_submitted=True, submitted_utc=now())
    write_json(receipt_path, receipt)
    print(json.dumps({"submitted": video["name"], "job_id": upload_id}), flush=True)
    return receipt


def validate_native(video, job):
    run = Path(job["output_dir"])
    frames, statuses, subjects, sha_counts = set(), Counter(), set(), Counter()
    with (run / "sam3d_frames.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            index = row["frame_index"]
            frames.add(index)
            statuses[str(row.get("status"))] += 1
            sha_counts[str(row.get("video_sha256", "")).lower()] += 1
            if row.get("status") == "ok":
                subjects.add((index, row.get("person_track_id")))
    if frames != set(range(video["frames"])):
        raise ValueError("Native SAM archive does not cover every original frame")
    if set(sha_counts) != {video["sha256"]}:
        raise ValueError("Native SAM archive video SHA differs from FULL-TEST source")
    source_count = 0
    pose_frames = 0
    with (run / "frames.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["frame"]["index"] != source_count:
                raise ValueError("Projected observations are not complete source-frame order")
            pose_frames += bool(row.get("poses"))
            source_count += 1
    if source_count != video["frames"]:
        raise ValueError("Projected observations do not cover the full video")
    result = {"full_frame_coverage": True, "native_frame_count": len(frames), "native_status_counts": dict(statuses),
            "successful_native_person_frames": len(subjects), "observed_pose_frames": pose_frames,
            "pose_frame_fraction": pose_frames / source_count,
            "artifact_sha256": {name: sha256(run / name) for name in
                                ("primary-player.jsonl", "sam3d_frames.jsonl", "frames.jsonl")}}
    sidecar = run / "tennis-subject-selection.json"
    if sidecar.is_file():
        result["artifact_sha256"][sidecar.name] = sha256(sidecar)
        selection = json.loads(sidecar.read_text(encoding="utf-8"))
        result["identity_replay"] = {key: selection.get(key) for key in ("appearance_revision", "selection_authority", "diagnostics")}
        selected_frames = sum(row.get("source_track_id") is not None and row.get("pose_present") is True
                              for row in selection.get("timeline", []))
        result["selected_native_frame_count"] = selected_frames
        result["selected_native_frame_fraction"] = selected_frames / source_count
    return result


def evaluate(video, job, out):
    from rallymate_service.tennis_assessment import build_tennis_assessment
    run = Path(job["output_dir"])
    evidence = validate_native(video, job)
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    result = build_tennis_assessment(summary, run, job_id=job["id"])
    write_json(out / "assessments" / (Path(video["name"]).stem + ".json"), result)
    score = result.get("overall_score")
    measured_metrics = Counter()
    measured_joint_angles = Counter()
    missing_joint_reasons = Counter()
    phase_count, measured_anchor_count = 0, 0
    for episode in result.get("episodes", []):
        for key, metric in episode.get("metrics", {}).items():
            if all(isinstance(metric.get(field), (float, int)) and math.isfinite(metric[field])
                   for field in ("value", "score")):
                measured_metrics[key] += 1
        for key, metric in episode.get("joint_kinematics", {}).get("metrics", {}).items():
            if metric.get("status") == "measured":
                measured_joint_angles[key] += 1
            else:
                missing_joint_reasons[str(metric.get("reason_code", metric.get("reason", "unobserved")))] += 1
        for phase in episode.get("joint_kinematics", {}).get("phases", []):
            phase_count += 1
            observed = {frame for metric in phase.get("metrics", {}).values()
                        if metric.get("status") == "measured" for frame in metric.get("source_frames", [])}
            measured_anchor_count += phase.get("source_frame") in observed
    valid = (isinstance(score, (float, int)) and not isinstance(score, bool) and math.isfinite(score)
             and 0 <= score <= 100 and bool(measured_metrics) and result.get("observation_count", 0) > 0)
    episodes = result.get("episodes", [])
    levels = result.get("level_assessments", [])
    clips = result.get("training_session", {}).get("training", {}).get("active_clips", [])
    checks = {
        "source_binding": result.get("source_run_id") == job["id"] and result.get("source_video_sha256") == video["sha256"],
        "episode_accounting": bool(episodes) and result.get("observation_count") == len(episodes),
        "joint_measurements": bool(episodes) and all(any(metric.get("status") == "measured"
            for metric in episode.get("joint_kinematics", {}).get("metrics", {}).values()) for episode in episodes),
        "phase_anchors_measured": phase_count > 0 and measured_anchor_count == phase_count,
        "five_level_assessments": len(levels) == 5 and {level.get("target_level") for level in levels} == set(range(1, 6))
            and all(isinstance(level.get("overall_score"), (float, int)) and math.isfinite(level["overall_score"]) for level in levels),
        "training_clips": bool(clips),
    }
    receipt_path = out / "submissions" / (Path(video["name"]).stem + ".json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
    model = summary.get("models", {}).get("pose_backend", {})
    return {**video, "job_id": job["id"], "run_dir": str(run), "inference_completed_at": job["completed_at"],
            "inference_reused": receipt.get("job_id") != job["id"],
            "model": {key: model.get(key) for key in ("backend", "model_name", "model_sha256", "native_keypoint_format")},
            "evaluated_at": now(), "evidence": evidence, "assessment_version": result.get("version"),
            "score": score, "status": result.get("status"), "valid_numeric_score": valid,
            "required_feature_checks": checks, "required_features_valid": all(checks.values()),
            "measured_metric_episode_counts": dict(measured_metrics),
            "measured_joint_angle_episode_counts": dict(measured_joint_angles),
            "missing_joint_reason_counts": dict(missing_joint_reasons),
            "phase_count": phase_count, "measured_phase_anchor_count": measured_anchor_count,
            "observation_count": result.get("observation_count"),
            "training_clip_count": len(clips),
            "candidate_accounting": result.get("candidate_accounting"),
            "quality_reason_counts": result.get("quality_reason_counts"),
            "level_assessment_count": len(result.get("level_assessments", [])),
            "reason_zh": result.get("reason_zh"), "diagnostics": result.get("diagnostics")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "FULL-TEST")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/scoring-refactor-20261108")
    parser.add_argument("--database", type=Path, default=ROOT / "service_data/jobs.sqlite3")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--submit-missing", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--only", help="Exact filename or stem")
    args = parser.parse_args()
    videos = inventory(args.input)
    write_json(args.output / "video-inventory.json", videos)
    if args.only:
        videos = [v for v in videos if args.only in (v["name"], Path(v["name"]).stem)]
        if not videos:
            raise ValueError("Requested video is not in FULL-TEST")
    jobs = database_jobs(args.database)
    selected, missing = [], []
    for video in videos:
        match = matching_run(video, jobs)
        if match:
            selected.append((video, match))
        else:
            missing.append(video)
    write_json(args.output / "selected-runs.json", [{**v, **{"job": j}} for v, j in selected])
    print(json.dumps({"videos": len(videos), "full_source_bound_sam_runs": len(selected),
                      "missing": [v["name"] for v in missing]}), flush=True)
    if args.submit_missing:
        health = request(args.base, "/health/ready")
        if health.get("pose_backend") != "sam3d" or not health.get("inference_ready"):
            raise RuntimeError("Local SAM inference service is not ready")
        for video in sorted(missing, key=lambda v: v["frames"]):
            submit(video, args.output, args.base)
    if args.evaluate:
        results = []
        scorer_sources = sorted(set((ROOT / "src/rallymate_scoring").glob("*.py"))
                                | set((ROOT / "src/rallymate_scoring/data").glob("*.json"))
                                | set((ROOT / "src/rallymate_features").glob("*.py"))
                                | set((ROOT / "src/rallymate_tracking").glob("*.py"))
                                | set((ROOT / "src/rallymate_service").glob("tennis*.py"))
                                | {ROOT / "src/rallymate_events/gs_phase_segmentation.py",
                                   ROOT / "src/rallymate_service/tennis_assessment.py",
                                   ROOT / "src/rallymate_service/specialist_motion_assessment.py",
                                   ROOT / "src/rallymate_service/training_session_analysis.py",
                                   ROOT / "src/rallymate_service/level_assessment.py"})
        source_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in scorer_sources}
        write_json(args.output / "scorer-source-sha256.json", source_hashes)
        for video, job in sorted(selected, key=lambda p: p[0]["frames"]):
            started = time.perf_counter()
            try:
                result = evaluate(video, job, args.output)
            except Exception as error:
                result = {**video, "job_id": job["id"], "valid_numeric_score": False,
                          "error": f"{type(error).__name__}: {error}"}
            result["evaluation_seconds"] = round(time.perf_counter() - started, 3)
            results.append(result)
            write_json(args.output / "validation-results.json", {"evaluated_at": now(), "results": results,
                       "missing": [v["name"] for v in missing],
                       "all_passed": not missing and all(r["valid_numeric_score"] and r.get("required_features_valid") for r in results)})
            print(json.dumps({k: result.get(k) for k in ("name", "score", "status", "error", "evaluation_seconds")}), flush=True)
        source_stable = source_hashes == {str(path.relative_to(ROOT)): sha256(path) for path in scorer_sources}
        passed = not missing and bool(results) and all(r["valid_numeric_score"] and r.get("required_features_valid") for r in results) and source_stable
        write_json(args.output / "validation-results.json", {"evaluated_at": now(), "results": results,
                   "missing": [v["name"] for v in missing], "source_code_stable_during_evaluation": source_stable,
                   "scorer_source_fingerprint": hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest(),
                   "all_passed": passed})
        return 0 if passed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
