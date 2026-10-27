"""Reproduce real inference and subject selection in isolated output directories.

Example (run with the project's YOLO Python and PYTHONPATH=src):
  python scripts/validate_tennis_identity_pipeline.py --request input-request.json \
    --output-root new-validation-directory --run-id identity-check \
    --sam-python runtime/sam3d/.venv311/Scripts/python.exe \
    --sam-runtime runtime/sam3d --service-db service_data/jobs.sqlite3

Each --request is an ordinary pipeline request containing explicit source/model
paths and clip bounds. The production decoder, detector, camera guard, target
lock, SAM inference, native serialization and stroke recognizer run unchanged.
An optional source-bound reviewed subject manifest replaces only target choice;
those runs are explicitly manual-selection experiments, never automatic wins.
The other test hook stops after recognition, before FS scoring. Mesh export and
annotated previews are disabled. No service jobs are submitted or overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from unittest.mock import patch


class InferenceValidationComplete(BaseException):
    def __init__(self, result: dict):
        self.result = result


class ReviewedSubjectSelection:
    """Exact replay of independently reviewed detections, without pose edits."""

    def __init__(self, document: dict, source_sha: str):
        if (document.get("version") != "reviewed-inference-subject-v1"
                or str(document.get("source_video_sha256", "")).lower() != source_sha.lower()
                or document.get("method") not in {"agent_visual_review", "human_visual_review"}
                or not isinstance(document.get("reviewer"), str) or not document["reviewer"]
                or document.get("automatic_subject_selection") is not False
                or not isinstance(document.get("frames"), list) or not document["frames"]):
            raise ValueError("reviewed_subject_manifest_invalid")
        self.document = document
        self.by_time = {}
        last_index = -1
        last_time = -math.inf
        for row in document["frames"]:
            time_ms = row.get("timestamp_ms")
            box = row.get("bbox_px")
            missing = row.get("source_track_id") is None and box is None and row.get("reason_code") == "reviewed_target_not_detected"
            if (type(time_ms) not in (int, float) or not math.isfinite(time_ms) or time_ms <= last_time
                    or type(row.get("source_frame_index")) is not int or row["source_frame_index"] <= last_index
                    or any(type(row.get(k)) is not int or row[k] < 0 for k in ("camera_reference_epoch", "pts"))
                    or not missing and (type(row.get("source_track_id")) is not int or row["source_track_id"] < 0)
                    or any(type(row.get(k)) is not int or row[k] <= 0 for k in
                           ("time_base_num", "time_base_den", "width", "height"))
                    or not missing and (not isinstance(box, list) or len(box) != 4
                        or not all(type(v) in (int, float) and math.isfinite(v) for v in box)
                        or not 0 <= box[0] < box[2] <= row["width"]
                        or not 0 <= box[1] < box[3] <= row["height"])):
                raise ValueError("reviewed_subject_frame_invalid")
            self.by_time[time_ms] = row
            last_index, last_time = row["source_frame_index"], time_ms
        self.seen = set()

    def update(self, detections, *, timestamp_ms, width, height, camera_epoch, **_kwargs):
        row = self.by_time.get(timestamp_ms)
        if (row is None or timestamp_ms in self.seen or width != row["width"] or height != row["height"]
                or camera_epoch != row["camera_reference_epoch"]):
            raise ValueError("reviewed_subject_clock_or_camera_mismatch")
        if row["source_track_id"] is None:
            self.seen.add(timestamp_ms)
            return {"version": "subject-lock-v1", "subject_id": None, "source_track_id": None,
                    "status": "unknown", "reason_code": "reviewed_target_not_detected", "evidence": [],
                    "semantics": "manual_subject_selection_not_automatic_identity_success"}
        matches = [d for d in detections if d.get("class_name") == "player" and type(d.get("track_id")) is int
                   and d.get("track_id") == row["source_track_id"]]
        if (len(matches) != 1 or len(matches[0].get("bbox_px", [])) != 4
                or not all(type(value) in (int, float) and math.isfinite(value) for value in matches[0]["bbox_px"])
                or any(abs(a-b) > .001 for a, b in zip(matches[0]["bbox_px"], row["bbox_px"]))):
            raise ValueError("reviewed_subject_detection_changed")
        self.seen.add(timestamp_ms)
        return {"version": "subject-lock-v1", "subject_id": "subject-1", "source_track_id": row["source_track_id"],
                "status": "locked", "reason_code": None,
                "evidence": ["source_bound_independent_subject_review"],
                "semantics": "manual_subject_selection_not_automatic_identity_success"}

    def validate_artifacts(self, frames):
        if len(frames) != len(self.by_time) or self.seen != set(self.by_time):
            raise ValueError("reviewed_subject_frame_coverage_mismatch")
        for observed in frames:
            frame = observed["frame"]
            row = self.by_time.get(frame.get("timestamp_ms"))
            if (row is None or frame.get("index") != row["source_frame_index"]
                    or str(frame.get("video_sha256", "")).lower() != self.document["source_video_sha256"].lower()
                    or any(frame.get(key) != row[key] for key in ("pts", "time_base_num", "time_base_den"))):
                raise ValueError("reviewed_subject_source_binding_mismatch")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--request", type=Path, action="append", required=True)
    result.add_argument("--output-root", type=Path, required=True)
    result.add_argument("--run-id", required=True, help="Prefix for isolated validation job IDs")
    result.add_argument("--sam-python", type=Path, required=True)
    result.add_argument("--sam-runtime", type=Path, required=True)
    result.add_argument("--service-db", type=Path, help="Read-only queue guard; abort this validation if queued/running jobs appear")
    result.add_argument("--reviewed-subject-manifest", type=Path,
                        help="One request only; independently reviewed per-frame target detections, never automatic selection success")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        raise ValueError("run-id must contain only letters, digits, underscore or hyphen")
    requests = [path.resolve(strict=True) for path in args.request]
    if len({path.stem for path in requests}) != len(requests):
        raise ValueError("request file stems must be unique within a validation run")
    if args.reviewed_subject_manifest and len(requests) != 1:
        raise ValueError("reviewed_subject_requires_one_request")
    args.sam_python = args.sam_python.resolve(strict=True)
    args.sam_runtime = args.sam_runtime.resolve(strict=True)
    output = args.output_root.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite validation output: {output}")
    os.environ["RALLYMATE_SAM3D_PYTHON"] = str(args.sam_python)
    os.environ["RALLYMATE_SAM3D_RUNTIME"] = str(args.sam_runtime)
    os.environ["RALLYMATE_SAM3D_EXPORT_MESH"] = "0"
    from rallymate_vision.contracts import load_request
    from rallymate_vision import pipeline
    from rallymate_vision.pose.metadata import sha256_file

    def check_service_idle():
        if args.service_db is None:
            return
        database = args.service_db.resolve(strict=True)
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            active = connection.execute("select id from jobs where status in (?,?)", ("running", "queued")).fetchall()
        if active:
            raise RuntimeError("Service GPU queue is active; isolated validation stopped")

    check_service_idle()
    output.mkdir(parents=True, exist_ok=False)
    original_recognize = pipeline.recognize_strokes_from_artifacts

    def stop_after_recognition(frames, timeline):
        result = original_recognize(frames, timeline)
        Path(frames).with_name("action-recognition.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        raise InferenceValidationComplete(result)

    manifest = []
    selector_paths = [Path(__file__).resolve().parents[1] / "src/rallymate_tracking" / name
                      for name in ("subject_lock.py", "primary_player.py")]
    selector_hashes = {path.name: sha256_file(path) for path in selector_paths}
    source_root = Path(__file__).resolve().parents[1] / "src"

    def code_hashes():
        return {path.relative_to(source_root).as_posix(): sha256_file(path)
                for path in sorted(source_root.rglob("*.py"))}

    for original_path in requests:
        check_service_idle()
        config = json.loads(original_path.read_text(encoding="utf-8"))
        original_request = load_request(original_path)
        # Resolve through the real contract before moving the request file so
        # relative source/model paths retain their original meaning.
        config["source"]["video_path"] = str(original_request.video_path)
        for field in ("detect", "pose", "pose_config"):
            value = getattr(original_request.models, field, None)
            if value is not None:
                config["models"][field] = str(value)
        if original_request.scoring.feasibility_registry is not None:
            config.setdefault("scoring", {})["feasibility_registry"] = str(original_request.scoring.feasibility_registry)
        config.setdefault("scoring", {})["calibration_assets"] = [str(p) for p in original_request.scoring.calibration_assets]
        if original_request.scoring.reference_context is not None:
            config["scoring"]["reference_context"] = str(original_request.scoring.reference_context)
        original_job_id = config["job_id"]
        config["job_id"] = f"{args.run_id}-{original_path.stem}"
        run = output / original_path.stem
        config["output"]["directory"] = str(run)
        config.setdefault("processing", {})["write_annotated_video"] = False
        config.pop("upstream_metadata", None)
        request_path = output / f"{original_path.stem}-request.json"
        request_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        request = load_request(request_path)
        source_sha_before = sha256_file(request.video_path)
        runner_sha_before = sha256_file(Path(__file__))
        reviewed = None
        reviewed_sha = None
        if args.reviewed_subject_manifest:
            reviewed_sha = sha256_file(args.reviewed_subject_manifest)
            reviewed = ReviewedSubjectSelection(json.loads(args.reviewed_subject_manifest.read_text(encoding="utf-8")), source_sha_before)
        from rallymate_tracking import subject_lock
        selection_scope = (patch.object(subject_lock.SubjectLock, "update", lambda _self, *a, **kw: reviewed.update(*a, **kw))
                           if reviewed is not None else nullcontext())
        code_before = code_hashes()
        started = time.perf_counter()
        last_report = -100

        def progress(value):
            nonlocal last_report
            count = value.get("processed_frames", 0)
            if count >= last_report + 100:
                print(json.dumps({"job_id": request.job_id, "phase": value.get("phase"), "frames": count}), flush=True)
                last_report = count

        with selection_scope, patch.object(pipeline, "recognize_strokes_from_artifacts", stop_after_recognition):
            try:
                pipeline.run_pipeline(request, progress_callback=progress, cancellation_callback=check_service_idle)
            except InferenceValidationComplete as complete:
                recognized = complete.result
            else:
                raise RuntimeError("Production pipeline did not reach the expected validation boundary")
        if selector_hashes != {path.name: sha256_file(path) for path in selector_paths}:
            raise RuntimeError("Selector source changed during GPU validation")
        if source_sha_before != sha256_file(request.video_path):
            raise RuntimeError("Source video changed during GPU validation")
        if runner_sha_before != sha256_file(Path(__file__)):
            raise RuntimeError("Validation runner changed during inference")
        code_after = code_hashes()
        read_rows = lambda name: [json.loads(line) for line in (run / name).open(encoding="utf-8")]
        if reviewed is not None:
            reviewed.validate_artifacts(read_rows("frames.jsonl"))
            if reviewed_sha != sha256_file(args.reviewed_subject_manifest):
                raise RuntimeError("Subject review changed during inference")
        timeline = read_rows("primary-player.jsonl")
        native = read_rows("sam3d_frames.jsonl")
        successful = [r for r in native if r.get("status") == "ok"]
        selected = [r for r in timeline if r["selection_status"] == "selected"]
        row = {
            "job_id": request.job_id, "original_job_id": original_job_id, "run_dir": str(run),
            "request_path": str(request_path), "original_request_sha256": sha256_file(original_path),
            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "source_sha256": sha256_file(request.video_path), "frame_count": len(timeline),
            "selected_frames": len(selected), "native_ok": len(successful),
            "native_track_counts": dict(Counter(r["person_track_id"] for r in successful)),
            "candidate_count": recognized.get("candidate_count"), "candidates_by_family": recognized.get("by_family"),
            "native_inference_seconds": round(sum(r.get("inference_ms") or 0 for r in native) / 1000, 3),
            "selector_sha256": selector_hashes,
            "runner_sha256": runner_sha_before,
            "runner_unchanged": True,
            "automatic_subject_selection": reviewed is None,
            "reviewed_subject_manifest_sha256": reviewed_sha,
            "code_hashes_at_start": code_before, "code_hashes_at_end": code_after,
            "code_changed_during_execution": code_before != code_after,
            "recognizer_version": recognized.get("detector_version"),
            "artifact_sha256": {name: sha256_file(run / name) for name in
                                ("frames.jsonl", "sam3d_frames.jsonl", "primary-player.jsonl", "action-recognition.json")},
            "validation_scope": ("real_production_detection_decode_camera_SAM_and_stroke_candidates_with_source_bound_manual_subject_selection"
                                 if reviewed else "real_production_detection_decode_camera_subject_lock_SAM_and_stroke_candidates")
                                + "; scoring_and_mesh_preview_not_run",
        }
        (run / "validation-summary.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.append(row)
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
