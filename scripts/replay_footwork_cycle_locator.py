"""Replay automatic ankle motion localization against original video files.

Outputs are separate artifacts, never manual annotations, event replacements,
or card scores. The full source run is scanned; no score-driven window search.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.source_alignment import clear_source_aligned_feature_cache
from rallymate_scoring.footwork_cycle_locator import locate_footwork_cycles
from rallymate_service.footwork_cycle_artifacts import SOURCE_FILES, _bind_verified_decoder_snapshot
from rallymate_service.gs_card_execution import _decode_video_timeline, _digest, _prepare_samples, _read_json, _records, _sha

VERSION = "footwork-cycle-locator-replay-v1.0.0"


def replay(run_dir: Path, video_path: Path, output: Path):
    run_dir, video_path, output = run_dir.resolve(), video_path.resolve(), output.resolve()
    if output.exists():
        raise ValueError("output_must_be_new")
    artifacts = {name: _sha(run_dir / name) for name in SOURCE_FILES}
    summary = _read_json(run_dir / "summary.json")
    run_id = summary.get("job_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("source_summary_job_missing")
    provenance = summary.get("minimum_scoring_loop", {}).get("provenance", {})
    video_sha = _digest(provenance.get("video_sha256"))
    if provenance.get("frames_sha256", "").lower() != artifacts["frames.jsonl"]:
        raise ValueError("summary_frames_digest_mismatch")
    if _sha(video_path) != video_sha:
        raise ValueError("source_video_digest_mismatch")
    # Decode the actual file again. Saved timeline files are not certification.
    decoded = _decode_video_timeline(video_path, video_sha)
    records = _records(run_dir / "frames.jsonl")
    primary = _records(run_dir / "primary-player.jsonl", 64 * 1024 * 1024)
    events = _records(run_dir / "events.jsonl", 64 * 1024 * 1024)
    if any(any(key in row and row[key] != run_id for key in ("video_id", "job_id")) for row in primary):
        raise ValueError("primary_source_run_mismatch")
    _, derived = _prepare_samples(records, primary, decoded, video_sha256=video_sha, job_id=run_id)
    prepared, selected = _bind_verified_decoder_snapshot(records, primary, derived)
    sequence = pose_sequence_from_records(prepared, selected)
    sequence.coordinate_metadata["source_run_id"] = run_id
    try:
        report = locate_footwork_cycles(sequence, primary_timeline=selected, source_run_id=run_id, events=events)
    finally:
        clear_source_aligned_feature_cache(sequence)
    after_artifacts = {name: _sha(run_dir / name) for name in SOURCE_FILES}
    after_video_sha = _sha(video_path)
    if after_artifacts != artifacts or after_video_sha != video_sha:
        raise ValueError("source_changed_during_replay")
    modules = {}
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name.startswith("rallymate_") and path:
            path = Path(path).resolve()
            if not path.is_relative_to(ROOT / "src"):
                raise ValueError("runtime_module_outside_repository")
            modules[path.relative_to(ROOT).as_posix()] = path
    modules[Path(__file__).resolve().relative_to(ROOT).as_posix()] = Path(__file__).resolve()
    report.update(file_artifacts_verified=True, decoder_verified_from_original_video=True,
        source_frame_verification_ledger=derived,
        input={"source_run_directory": str(run_dir), "video_path": str(video_path),
            "source_video_sha256": video_sha, "input_artifact_sha256": artifacts,
            "input_artifact_sha256_after": after_artifacts, "source_video_sha256_after": after_video_sha,
            "runtime_module_sha256": {name: _sha(path) for name, path in sorted(modules.items())},
            "runtime_module_normalized_sha256": {name: hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()
                                                 for name, path in sorted(modules.items())},
            "module_digest_kinds": ["raw_file_sha256", "utf8_lf_normalized_sha256"],
            "repository_head_at_replay": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "runtime_identity": "actual_imported_module_hashes;repository_HEAD_is_context_not_a_claim_of_clean_source",
            "source_epoch_derivation": "preserve_valid_original_model_epoch_else_existing_selector_camera_derivation",
            "replay_version": VERSION})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.run_dir, args.video, args.output)
    print(json.dumps({"summary": result["summary"], "source_frame_count": result["source_frame_count"],
                      "excluded_frame_count": result["excluded_frame_count"], "segments": len(result["segments"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
