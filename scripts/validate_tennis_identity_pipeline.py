"""Reproduce real inference and subject selection in isolated output directories.

Example (run with the project's YOLO Python and PYTHONPATH=src):
  python scripts/validate_tennis_identity_pipeline.py --request input-request.json \
    --output-root new-validation-directory --run-id identity-check \
    --sam-python runtime/sam3d/.venv311/Scripts/python.exe \
    --sam-runtime runtime/sam3d --service-db service_data/jobs.sqlite3

Each --request is an ordinary pipeline request containing explicit source/model
paths and clip bounds. The production decoder, detector, camera guard, target
lock, SAM inference, native serialization and stroke recognizer run unchanged.
The only test hook stops after recognition, before FS scoring. Mesh export and
annotated previews are disabled. No service jobs are submitted or overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from unittest.mock import patch


class InferenceValidationComplete(BaseException):
    def __init__(self, result: dict):
        self.result = result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--request", type=Path, action="append", required=True)
    result.add_argument("--output-root", type=Path, required=True)
    result.add_argument("--run-id", required=True, help="Prefix for isolated validation job IDs")
    result.add_argument("--sam-python", type=Path, required=True)
    result.add_argument("--sam-runtime", type=Path, required=True)
    result.add_argument("--service-db", type=Path, help="Read-only queue guard; abort this validation if queued/running jobs appear")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        raise ValueError("run-id must contain only letters, digits, underscore or hyphen")
    requests = [path.resolve(strict=True) for path in args.request]
    if len({path.stem for path in requests}) != len(requests):
        raise ValueError("request file stems must be unique within a validation run")
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
        code_before = code_hashes()
        started = time.perf_counter()
        last_report = -100

        def progress(value):
            nonlocal last_report
            count = value.get("processed_frames", 0)
            if count >= last_report + 100:
                print(json.dumps({"job_id": request.job_id, "phase": value.get("phase"), "frames": count}), flush=True)
                last_report = count

        with patch.object(pipeline, "recognize_strokes_from_artifacts", stop_after_recognition):
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
        code_after = code_hashes()
        read_rows = lambda name: [json.loads(line) for line in (run / name).open(encoding="utf-8")]
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
            "code_hashes_at_start": code_before, "code_hashes_at_end": code_after,
            "code_changed_during_execution": code_before != code_after,
            "recognizer_version": recognized.get("detector_version"),
            "artifact_sha256": {name: sha256_file(run / name) for name in
                                ("frames.jsonl", "sam3d_frames.jsonl", "primary-player.jsonl", "action-recognition.json")},
            "validation_scope": "real_production_detection_decode_camera_subject_lock_SAM_and_stroke_candidates; scoring_and_mesh_preview_not_run",
        }
        (run / "validation-summary.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.append(row)
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
