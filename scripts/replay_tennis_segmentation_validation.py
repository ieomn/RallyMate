"""Compare automatic segmentation/scoring on immutable real-video observations.

Run this script in separate processes with --source-code-root pointing to an
archived release's src directory or the current src directory. Reviewed context
is opt-in and never participates in detection. Sources, observations, selected
timelines, code and optional reviews are hashed before and after the replay.
This CPU replay is not new video inference or a blind accuracy benchmark.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def code_hashes(source):
    return {path.relative_to(source).as_posix(): digest(path) for path in sorted(source.rglob("*.py"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-code-root", type=Path, default=ROOT / "src")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-reviewed-context", action="store_true")
    args = parser.parse_args()
    source = args.source_code_root.resolve(strict=True)
    manifest_path = args.manifest.resolve(strict=True)
    manifest = read(manifest_path)
    if manifest.get("version") != "tennis-segmentation-replay-inputs-v1":
        raise ValueError("Unsupported manifest version")
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite replay: {args.output}")
    names = [item["sample_id"] for item in manifest["samples"]]
    if len(set(names)) != len(names) or any(Path(n).name != n or n in {".", ".."} for n in names):
        raise ValueError("Sample IDs must be distinct safe path names")
    sys.path.insert(0, str(source))
    from rallymate_scoring.stroke_candidates import detect_stroke_candidates, DETECTOR_VERSION
    from rallymate_service.tennis_assessment import analyze_tennis_assessment, _summary_episodes, VERSION
    # Never accidentally compare the current module twice because of PYTHONPATH.
    import rallymate_service.tennis_assessment as module
    if not Path(module.__file__).resolve().is_relative_to(source):
        raise RuntimeError("Imported assessment is outside the requested source tree")
    start_code = code_hashes(source)
    manifest_sha = digest(manifest_path)
    args.output.mkdir(parents=True, exist_ok=False)
    result = {"version": "tennis-segmentation-replay-v1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "semantics": "automatic_candidates_and_scores_on_existing_real_observations_not_fresh_GPU_or_blind_holdout",
              "reviewed_context_enabled": args.with_reviewed_context,
              "manifest_sha256": manifest_sha, "runner_sha256": digest(Path(__file__)),
              "detector_version": DETECTOR_VERSION, "assessment_version": VERSION,
              "code_hashes_at_start": start_code, "samples": []}
    for item in manifest["samples"]:
        paths = {key: ROOT / value for key, value in item["paths"].items()}
        input_sha = {key: digest(path) for key, path in paths.items()}
        if input_sha != item["input_sha256"]:
            raise ValueError(f"Input artifacts changed: {item['sample_id']}")
        video_sha = item["source_video_sha256"].lower()
        if input_sha["video"] != video_sha:
            raise ValueError("Source video SHA mismatch")
        frames, timeline, native = (rows(paths[key]) for key in ("frames", "timeline", "native"))
        started = time.perf_counter()
        recognition = detect_stroke_candidates(frames, primary_timeline=timeline)
        episodes = _summary_episodes({"action_recognition": recognition})
        review = read(paths["context"]) if args.with_reviewed_context and "context" in paths else None
        assessment = analyze_tennis_assessment(timeline, native, video_sha256=video_sha,
            source_run_id=item["job_id"], motion_episodes=episodes, source_frames=frames, action_context=review)
        name = item["sample_id"]
        write(args.output / f"{name}-recognition.json", recognition)
        write(args.output / f"{name}-assessment.json", assessment)
        scored = assessment.get("episodes", [])
        metrics = Counter(key for episode in scored for key in episode.get("metrics", {}) if key.startswith("pattern_"))
        row = {"sample_id": name, "job_id": item["job_id"], "source_video_sha256": video_sha,
               "observation_origin": item["observation_origin"], "input_sha256": input_sha,
               "candidate_count": recognition.get("candidate_count"), "phase_episode_count": len(episodes),
               "scored_count": len(scored), "score": assessment.get("overall_score"),
               "candidate_accounting": assessment.get("candidate_accounting"),
               "pattern_metric_counts": dict(metrics), "coverage": assessment.get("pattern_coverage"),
               "elapsed_seconds": round(time.perf_counter() - started, 3),
               "recognition_sha256": digest(args.output / f"{name}-recognition.json"),
               "assessment_sha256": digest(args.output / f"{name}-assessment.json"),
               "inputs_unchanged": input_sha == {key: digest(path) for key, path in paths.items()}}
        if not row["inputs_unchanged"]:
            raise RuntimeError("Replay input changed while being read")
        result["samples"].append(row)
        print(json.dumps({"sample": name, "candidates": row["candidate_count"], "scored": len(scored)}, ensure_ascii=False), flush=True)
    result["code_hashes_at_end"] = code_hashes(source)
    result["code_changed_during_execution"] = start_code != result["code_hashes_at_end"]
    if result["code_changed_during_execution"] or manifest_sha != digest(manifest_path):
        raise RuntimeError("Replay code or manifest changed during execution; evidence is not final")
    result["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    write(args.output / "evaluation.json", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
