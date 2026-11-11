"""Recompute a source-frozen reference/test comparison from saved native SAM data.

No inference, service calls, database writes, or historical score reuse occurs.
Without --manifest, the three documented sources are frozen into manifest.json.
Use --write-manifest PATH to inventory them without scoring, then --manifest PATH
to replay the same bytes. All input paths are relative to --source-root.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.validate_full_test_scoring import database_jobs, matching_run, now, sha256, validate_native, write_json

VERSION = "reference-scoring-validation-v1"
ARTIFACTS = ("summary.json", "frames.jsonl", "sam3d_frames.jsonl", "primary-player.jsonl",
             "action-context.json", "tennis-subject-selection.json")
REQUIRED_ARTIFACTS = frozenset(ARTIFACTS[:4])
DEFAULT_SAMPLES = (
    {"sample_id": "djokovic-usopen-2012", "split": "reference", "source_group": "2012-usopen-jc-demiace",
     "video": "service_data/uploads/0a6ba301-acd4-46a9-829f-7afc832a552c.mp4",
     "video_sha256": "80f91cb924c32026754031b24730b3888c33bd0078dbc3ec3a432f836a86be68",
     "run_id": "0a6ba301-acd4-46a9-829f-7afc832a552c", "frames": 469},
    {"sample_id": "c235227fffcd3290b60572d0c3f9cc85", "split": "test", "source_group": "full-test-c235-fbf52",
     "video": "FULL-TEST/c235227fffcd3290b60572d0c3f9cc85.mp4",
     "video_sha256": "41ae2b5c12a92e8883b159f0963741451d42a515bb82f11194dec1f4fb1c05d6",
     "run_id": "fd448c4a-7edb-4f6a-bced-329747c1af3f", "frames": 6523},
    {"sample_id": "c2485607c23646e706d9c4aae89e668e", "split": "test", "source_group": "full-test-c248",
     "video": "FULL-TEST/c2485607c23646e706d9c4aae89e668e.mp4",
     "video_sha256": "c384fb160dfbe3dc3d8a560c69aee69404bc885d337473cd0d171ea408a2845f",
     "run_id": "0fdee4d4-b680-40e6-808e-07795fc89815", "frames": 2170},
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def source_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Manifest paths must be relative to source-root")
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Manifest path escapes source-root")
    return path


def artifact_hashes(run):
    hashes = {}
    for name in ARTIFACTS:
        path = Path(run) / name
        if name in REQUIRED_ARTIFACTS and not path.is_file():
            raise ValueError(f"Required source artifact missing: {name}")
        hashes[name] = sha256(path) if path.is_file() else None
    return hashes


def code_fingerprint(root=ROOT):
    paths = sorted(p for p in (root / "src").rglob("*") if p.suffix in {".py", ".json"})
    paths += [root / "scripts/validate_reference_scoring.py", root / "scripts/validate_full_test_scoring.py"]
    files = {p.relative_to(root).as_posix(): sha256(p) for p in paths}
    return {"sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            "files": files, "python_version": sys.version.split()[0]}


def probe_video(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        count, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
        if not cap.isOpened() or count <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ValueError(f"Source video metadata unavailable: {path.name}")
        return {"frames": count, "fps": fps, "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), "duration_s": count / fps}
    finally:
        cap.release()


def validate_manifest(manifest):
    if manifest.get("version") != VERSION or not isinstance(manifest.get("samples"), list) or not manifest["samples"]:
        raise ValueError("Invalid reference comparison manifest")
    ids, groups, videos = set(), set(), set()
    for sample in manifest["samples"]:
        for field in ("sample_id", "run_id"):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", str(sample.get(field, ""))):
                raise ValueError(f"Invalid {field}")
        if sample["sample_id"] in ids:
            raise ValueError("Duplicate sample_id")
        ids.add(sample["sample_id"])
        if sample.get("split") not in {"reference", "holdout", "test"}:
            raise ValueError("Sample split must be reference, holdout or test")
        if sample.get("split") == "holdout" and sample.get("prior_results_seen") is not False:
            raise ValueError("Holdout requires explicit prior_results_seen=false")
        group = sample.get("source_group")
        if not isinstance(group, str) or not group or group in groups:
            raise ValueError("Repeated or missing source_group: duplicates are not independent samples")
        groups.add(group)
        digest = sample.get("video_sha256", "")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest in videos:
            raise ValueError("Invalid or duplicate video_sha256")
        videos.add(digest)
        if type(sample.get("frames")) is not int or sample["frames"] < 1:
            raise ValueError("Invalid source frame count")
        expected = sample.get("artifact_sha256", {})
        if set(expected) != set(ARTIFACTS):
            raise ValueError("Manifest must freeze every scoring input artifact")
        for name, value in expected.items():
            if value is None and name not in REQUIRED_ARTIFACTS:
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"Invalid artifact SHA: {name}")
    if not any(s["split"] == "reference" for s in manifest["samples"]):
        raise ValueError("Comparison requires a reference sample")
    return manifest


def make_manifest(source_root):
    samples = []
    for definition in DEFAULT_SAMPLES:
        sample = deepcopy(definition)
        video = source_path(source_root, sample["video"])
        if sha256(video) != sample["video_sha256"]:
            raise ValueError(f"Source video SHA differs: {sample['sample_id']}")
        metadata = probe_video(video)
        if metadata["frames"] != sample["frames"]:
            raise ValueError(f"Source frame count differs: {sample['sample_id']}")
        sample.update(metadata, prior_results_seen=True, run=f"service_data/runs/{sample['run_id']}")
        sample["artifact_sha256"] = artifact_hashes(source_path(source_root, sample["run"]))
        samples.append(sample)
    return validate_manifest({"version": VERSION, "created_at": now(), "samples": samples,
                              "scope": "saved_native_inference_current_scorer_recomputation",
                              "excluded_duplicate": "fbf52d9a52b66d28a5316df4ed7babc0"})


def verify_source(sample, source_root, jobs):
    video_path = source_path(source_root, sample["video"])
    run = source_path(source_root, sample["run"])
    if run.name != sample["run_id"]:
        raise ValueError("Run directory does not match pinned run_id")
    if sha256(video_path) != sample["video_sha256"]:
        raise ValueError("Source video SHA differs from manifest")
    metadata = probe_video(video_path)
    if any(metadata[k] != sample[k] for k in ("frames", "width", "height")):
        raise ValueError("Source frame count or dimensions differ from manifest")
    if not math.isclose(metadata["fps"], sample["fps"], rel_tol=1e-8):
        raise ValueError("Source fps differs from manifest")
    if artifact_hashes(run) != sample["artifact_sha256"]:
        raise ValueError("Scoring input artifact SHA differs from manifest")
    video = {"name": video_path.name, "source": str(video_path), "sha256": sample["video_sha256"], **metadata}
    # The database's old absolute output_dir may come from another machine.
    # The manifest pins the local archive; source SHA and complete run metadata
    # must still match the same database job.
    candidates = [{**j, "output_dir": str(run)} for j in jobs if j["id"] == sample["run_id"]]
    job = matching_run(video, candidates)
    if job is None:
        raise ValueError("Pinned database job is not a successful complete SAM run of this source")
    summary = read_json(run / "summary.json")
    digest = summary.get("minimum_scoring_loop", {}).get("provenance", {}).get("video_sha256", "")
    if digest.lower() != video["sha256"] or summary.get("job_id", job["id"]) != job["id"]:
        raise ValueError("Run summary source identity differs from manifest")
    evidence = validate_native(video, job)
    # The shared FULL-TEST check covers native SHA and complete frame sets.
    # Also bind every projected observation to this source and its dimensions.
    with (run / "frames.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            frame = json.loads(line)["frame"]
            if str(frame.get("video_sha256", "")).lower() != video["sha256"]:
                raise ValueError("Projected observation source SHA differs")
            if (frame.get("width"), frame.get("height")) != (video["width"], video["height"]):
                raise ValueError("Projected observation dimensions differ")
    return video, job, summary, evidence


def episode_scores(assessment):
    from rallymate_scoring.action_aggregation import episode_dimension_scores, measurement_pair
    keys = {d["id"]: [m["id"] for m in d["metrics"]] for d in assessment.get("dimensions", [])}
    rows = []
    for episode in assessment.get("episodes", []):
        scores = episode_dimension_scores(episode.get("metrics", {}), keys)
        measured = [v for v in scores.values() if v is not None]
        pairs = [pair for metric in episode.get("metrics", {}).values() if (pair := measurement_pair(metric)) is not None]
        pattern = episode.get("motion_pattern", {})
        rows.append({**{k: episode.get(k) for k in ("episode_id", "family", "start_ms", "peak_ms", "end_ms", "source_track_id")},
                     "technique": pattern.get("profile_id"), "classification_provenance": pattern.get("classification_provenance"),
                     "dimension_scores": scores,
                     "engineering_score": round(math.fsum(measured) / len(measured), 2) if measured else None,
                     "observed_dimension_count": len(measured), "measured_metric_count": len(pairs),
                     "metrics_at_100": sum(score == 100 for _, score in pairs),
                     "metric_saturation_fraction": sum(score == 100 for _, score in pairs) / len(pairs) if pairs else None})
    return rows


def recompute(summary, run, job_id):
    from rallymate_service import tennis_assessment
    tennis_assessment._cached.cache_clear()
    report = tennis_assessment.build_tennis_assessment(summary, run, job_id=job_id)
    cache = tennis_assessment._cached.cache_info()
    if cache.hits != 0 or cache.misses != 1:
        raise ValueError("Assessment was not freshly recomputed from native inputs")
    return report


def validate_assessment(report, sample):
    score = report.get("overall_score")
    if report.get("source_video_sha256") != sample["video_sha256"] or report.get("source_run_id") != sample["run_id"]:
        raise ValueError("Recomputed assessment source binding differs")
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError("Recomputed assessment has no finite engineering score")
    episodes = report.get("episodes", [])
    if not episodes or report.get("observation_count") != len(episodes):
        raise ValueError("Recomputed assessment has no complete episode accounting")
    if any(not e.get("metrics") for e in episodes):
        raise ValueError("Recomputed episode lacks measurements")


def run_validation(manifest, source_root, database, output):
    validate_manifest(manifest)
    output, source_root = Path(output).resolve(), Path(source_root).resolve()
    inputs = [source_path(source_root, s[k]) for s in manifest["samples"] for k in ("video", "run")]
    if any(output == p or output.is_relative_to(p) or p.is_relative_to(output) for p in inputs):
        raise ValueError("Output overlaps source evidence")
    before = code_fingerprint()
    write_json(output / "manifest.json", manifest)
    write_json(output / "scorer-source-sha256.json", before)
    ledger = {"version": VERSION, "started_at": now(), "all_passed": False, "results": [],
              "scorer_source_fingerprint": before["sha256"], "fresh_recomputation": True,
              "saturation_definition": "fraction of measured episode metric scores exactly 100 after public rounding",
              "score_semantics": "engineering rubric, independent of reference identity; not reference similarity"}
    write_json(output / "validation-results.json", ledger)
    try:
        jobs = database_jobs(Path(database))
        all_episodes = []
        for sample in manifest["samples"]:
            started = time.perf_counter()
            video, job, summary, evidence = verify_source(sample, source_root, jobs)
            report = recompute(summary, Path(job["output_dir"]), job["id"])
            validate_assessment(report, sample)
            rows = episode_scores(report)
            if any(row["engineering_score"] is None for row in rows):
                raise ValueError("Episode cannot be reconstructed from report dimension definitions")
            path = output / "assessments" / f"{sample['sample_id']}.json"
            write_json(path, report)
            all_episodes.extend({"sample_id": sample["sample_id"], "split": sample["split"], **row} for row in rows)
            measured = sum(r["measured_metric_count"] for r in rows)
            row = {"sample_id": sample["sample_id"], "split": sample["split"], "source_group": sample["source_group"],
                   "video_sha256": video["sha256"], "run_id": job["id"], "inference_reused": True,
                   "assessment_recomputed": True, "assessment_sha256": sha256(path), "assessment_path": path.relative_to(output).as_posix(),
                   "status": report.get("status"), "overall_score": report["overall_score"], "observation_count": len(rows),
                   "measured_metric_count": measured, "metric_saturation_fraction": sum(r["metrics_at_100"] for r in rows) / measured if measured else None,
                   "evidence": evidence, "model": summary.get("models", {}).get("pose_backend"),
                   "elapsed_seconds": round(time.perf_counter() - started, 3)}
            ledger["results"].append(row)
            write_json(output / "episode-scores.json", {"version": VERSION, "episodes": all_episodes})
            write_json(output / "validation-results.json", ledger)
            print(json.dumps({k: row[k] for k in ("sample_id", "overall_score", "observation_count", "elapsed_seconds")}), flush=True)
        # Recheck the complete input set after the last build, including earlier
        # sources and optional sidecar existence. No old score file is read.
        for sample in manifest["samples"]:
            if sha256(source_path(source_root, sample["video"])) != sample["video_sha256"] or artifact_hashes(source_path(source_root, sample["run"])) != sample["artifact_sha256"]:
                raise ValueError("Source inputs changed during evaluation")
        after = code_fingerprint()
        ledger["source_code_stable_during_evaluation"] = after == before
        ledger["ending_scorer_source_fingerprint"] = after["sha256"]
        if after != before:
            raise ValueError("Scorer code changed during evaluation")
        for row in ledger["results"]:
            if sha256(output / row["assessment_path"]) != row["assessment_sha256"]:
                raise ValueError("Recomputed assessment file changed during evaluation")
        ledger.update(all_passed=True, source_inputs_stable=True, assessment_files_stable=True)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        ledger["error"] = f"{type(error).__name__}: {error}"
    finally:
        ledger["finished_at"] = now()
        write_json(output / "validation-results.json", ledger)
    return ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--write-manifest", type=Path, help="Freeze the three documented inputs, then exit without scoring")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/djokovic-comparison-20261112/current")
    args = parser.parse_args(argv)
    try:
        manifest = read_json(args.manifest) if args.manifest else make_manifest(args.source_root)
        validate_manifest(manifest)
        if args.write_manifest:
            write_json(args.write_manifest, manifest)
            return 0
        ledger = run_validation(manifest, args.source_root, args.database or args.source_root / "service_data/jobs.sqlite3", args.output)
        if not ledger["all_passed"]:
            print(ledger.get("error", "Reference validation failed"), file=sys.stderr)
        return 0 if ledger["all_passed"] else 1
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
