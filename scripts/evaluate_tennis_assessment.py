"""Recompute a locked source-group manifest without GPU inference or identity input.

Example (run once for each revision, in separate processes):
  python scripts/evaluate_tennis_assessment.py --manifest MANIFEST --output OUT
  python scripts/evaluate_tennis_assessment.py --manifest MANIFEST --output OLD --revision 794ea15

Private videos/native poses stay local. The report contains measurements, hashes,
rejection reasons, source grouping and timings, not private source pixels.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
ENGINE_FILES = ("tennis_assessment.py", "tennis_profiles.py", "tennis_coaching.py", "swing_kinematics.py")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_manifest(manifest):
    """Split by source/person group before scoring; repeats never count as new people."""
    ids, groups, hashes, labels, distribution_hashes = set(), {}, {}, {}, set()
    for row in manifest["samples"]:
        identifier = row["sample_id"]
        if identifier in ids:
            raise ValueError(f"duplicate sample_id: {identifier}")
        ids.add(identifier)
        group, split = row["source_group"], row["split"]
        if split not in {"development", "regression", "holdout", "robustness"}:
            raise ValueError(f"unknown split: {split}")
        if split != "robustness":
            if group in groups and groups[group] != split:
                raise ValueError(f"source group leaks across splits: {group}")
            groups[group] = split
        sha = row.get("source_video_sha256")
        sha = sha.lower() if isinstance(sha, str) else sha
        if sha:
            previous = hashes.setdefault(sha, group)
            if previous != group:
                raise ValueError(f"identical source hash assigned different groups: {identifier}")
            if split != "robustness" and row.get("include_in_distribution", True):
                if sha in distribution_hashes:
                    raise ValueError(f"duplicate source counted in distribution: {identifier}")
                distribution_hashes.add(sha)
        if row.get("evaluation_label") not in {"ordinary", "professional", "unknown"}:
            raise ValueError(f"unrecognized evaluation-only label: {identifier}")
        previous_label = labels.setdefault(group, row["evaluation_label"])
        if previous_label != row["evaluation_label"]:
            raise ValueError(f"source group assigned inconsistent labels: {group}")


def install_engine(revision, temporary):
    sys.path.insert(0, str(ROOT / "src"))
    directory = ROOT / "src/rallymate_service"
    if revision:
        directory = Path(temporary) / "rallymate_service"
        directory.mkdir()
        for name in ENGINE_FILES:
            content = subprocess.check_output(["git", "show", f"{revision}:src/rallymate_service/{name}"], cwd=ROOT)
            (directory / name).write_bytes(content)
        (directory / "__init__.py").write_text(
            f"__path__.append({str(ROOT / 'src/rallymate_service')!r})\n", encoding="utf-8")
        sys.path.insert(0, temporary)
    engine = importlib.import_module("rallymate_service.tennis_assessment")
    return engine, {name: digest(directory / name) for name in ENGINE_FILES}


def projection(result):
    return {key: result.get(key) for key in ("status", "overall_score", "dimensions", "episodes", "observation_count")}


def transform(records, variant):
    rows = copy.deepcopy(records)
    fixed = {}
    for row in rows:
        points = row.get("keypoints_3d", {})
        if variant == "wrong_source":
            row["video_sha256"] = "0" * 64
            continue
        if variant == "static":
            key = (row.get("person_track_id"), row.get("selection_epoch"), row.get("camera_reference_epoch"))
            fixed.setdefault(key, copy.deepcopy(points))
            row["keypoints_3d"] = copy.deepcopy(fixed[key])
            continue
        t = row.get("timestamp_ms", 0) / 1000
        a, b, c = .3 * math.sin(t), .45 * math.cos(t * .7), .4 * math.sin(t * .3)
        for name, point in points.items():
            if not isinstance(point, (tuple, list)) or len(point) != 3:
                continue
            x, y, z = point
            if variant == "moving_rigid_and_scale":
                y, z = math.cos(a) * y - math.sin(a) * z, math.sin(a) * y + math.cos(a) * z
                x, z = math.cos(b) * x + math.sin(b) * z, -math.sin(b) * x + math.cos(b) * z
                x, y = math.cos(c) * x - math.sin(c) * y, math.sin(c) * x + math.cos(c) * y
                points[name] = [2.4 * x + math.sin(t), 2.4 * y - 3 + t * .2, 2.4 * z + 7]
    return rows


def measure_sample(engine, row, repeats, variants, run_root=None):
    item = dict(row)
    job = row.get("job_id")
    directory = (Path(run_root) if run_root else ROOT / "service_data/runs") / str(job)
    paths = [directory / name for name in ("summary.json", "primary-player.jsonl", "sam3d_frames.jsonl")]
    if not job or any(not p.is_file() for p in paths):
        return {**item, "evaluation_status": "unavailable", "rejection_reason": row.get("rejection_reason", "native_pose_artifacts_unavailable")}, None
    signatures_before = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    summary = read_json(paths[0])
    sha = summary.get("minimum_scoring_loop", {}).get("provenance", {}).get("video_sha256")
    if row.get("source_video_sha256") and str(row["source_video_sha256"]).lower() != str(sha).lower():
        raise ValueError(f"manifest SHA disagrees with source: {row['sample_id']}")
    timeline = [json.loads(line) for line in paths[1].read_text(encoding="utf-8").splitlines() if line.strip()]
    keys = {(r.get("source_frame_index"), r.get("source_track_id")) for r in timeline}
    with paths[2].open(encoding="utf-8") as stream:
        records = [r for line in stream if line.strip() for r in [json.loads(line)]
                   if (r.get("frame_index"), r.get("person_track_id")) in keys]
    episodes = engine._summary_episodes(summary)
    call = lambda poses: engine.analyze_tennis_assessment(timeline, poses, video_sha256=sha, source_run_id=job, motion_episodes=episodes)
    values, times = [], []
    for _ in range(repeats):
        started = time.perf_counter()
        values.append(call(records))
        times.append(time.perf_counter() - started)
    result = values[0]
    # Force a genuinely uncached function call (OS file cache is not flushed).
    engine._cached.cache_clear()
    started = time.perf_counter()
    built = engine.build_tennis_assessment(summary, directory, job_id=job)
    first_builder = time.perf_counter() - started
    started = time.perf_counter()
    cached = engine.build_tennis_assessment(summary, directory, job_id=job)
    cached_builder = time.perf_counter() - started
    checks = {"repeat_exact": all(v == result for v in values), "public_builder_matches": projection(built) == projection(result),
              "cached_builder_matches": built == cached, "input_files_unchanged": signatures_before == [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]}
    variant_results = {}
    if variants:
        for variant in ("moving_rigid_and_scale", "static", "wrong_source"):
            altered = call(transform(records, variant))
            passed = (projection(altered) == projection(result) if variant == "moving_rigid_and_scale"
                      else altered.get("overall_score") is None)
            variant_results[variant] = {"passed": passed, "score": altered.get("overall_score"), "episodes": altered.get("observation_count")}
    metrics = [m for d in result["dimensions"] for m in d["metrics"] if m["score"] is not None]
    source_video = summary.get("input", {}).get("video", {})
    item.update(evaluation_status="evaluated", source_video_sha256=sha, summary_sha256=digest(paths[0]),
                artifact_sha256={p.name: digest(p) for p in paths},
                decoder_time_bases=sorted({(r.get("time_base_num"), r.get("time_base_den")) for r in records}, key=str),
                first_selected_decoder_pts=records[0].get("pts") if records else None,
                last_selected_decoder_pts=records[-1].get("pts") if records else None,
                selected_native_records=len(records), video=source_video,
                status=result["status"], score=result["overall_score"], episodes=result["observation_count"],
                dimension_scores={d["id"]: d["score"] for d in result["dimensions"]},
                metric_scores={m["id"]: m["score"] for m in metrics}, metric_values={m["id"]: m["value"] for m in metrics},
                metric_observation_counts={m["id"]: m["observation_count"] for m in metrics},
                episode_quality_reason_counts=dict(Counter(reason for e in result["episodes"] for reason in e.get("quality_reason_codes", []))),
                metrics_at_least_99=sum(m["score"] >= 99 for m in metrics), measured_metrics=len(metrics),
                families=dict(Counter(e["family"] for e in result["episodes"])), arms=dict(Counter(e["active_arm"] for e in result["episodes"])),
                quality_reason_counts=result.get("quality_reason_counts"), reason=result.get("reason_zh"),
                candidate_accounting=result.get("candidate_accounting"),
                timings={"pure_cpu_seconds": times, "pure_cpu_median_seconds": statistics.median(times),
                         "uncached_builder_seconds": first_builder, "cached_builder_seconds": cached_builder},
                checks=checks, variants=variant_results, strengths=result.get("strengths"), improvements=result.get("improvements"))
    return item, result


def distributions(rows):
    groups = defaultdict(list)
    for row in rows:
        if (row.get("score") is not None and row.get("include_in_distribution", True)
                and row.get("evaluation_validity", "valid") == "valid" and row["split"] != "robustness"):
            groups[(row["split"], row["source_group"], row["evaluation_label"])].append(row["score"])
    means = [{"split": split, "source_group": group, "label": label, "score": statistics.mean(values), "videos": len(values)}
             for (split, group, label), values in groups.items()]
    result = {"unit": "equal_weight_source_group_mean_not_action_or_clip", "groups": means}
    for split in ("all", "holdout"):
        selected = [r for r in means if split == "all" or r["split"] == split]
        scores = [r["score"] for r in selected]
        pro = [r["score"] for r in selected if r["label"] == "professional"]
        ordinary = [r["score"] for r in selected if r["label"] == "ordinary"]
        pairs = [float(p > o) + .5 * float(p == o) for p in pro for o in ordinary]
        eligible = [r for r in rows if (split == "all" or r["split"] == split)
                    and (r.get("include_in_distribution", True) or r.get("evaluation_validity", "valid") != "valid")
                    and r["split"] != "robustness"]
        all_groups = {r["source_group"] for r in eligible}
        result[split] = {"attempted_groups": len(all_groups), "unscored_or_invalid_groups": len(all_groups) - len(scores),
                         "unscored_groups": len(all_groups) - len(scores),
                         "groups": len(scores), "minimum": min(scores) if scores else None, "maximum": max(scores) if scores else None,
                         "mean": statistics.mean(scores) if scores else None, "at_least_90": sum(v >= 90 for v in scores),
                         "professional_groups": len(pro), "ordinary_groups": len(ordinary),
                         "professional_mean": statistics.mean(pro) if pro else None, "ordinary_mean": statistics.mean(ordinary) if ordinary else None,
                         "professional_above_ordinary_pair_fraction": statistics.mean(pairs) if pairs else None, "pairs": len(pairs)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--variants", action="store_true")
    parser.add_argument("--run-root", type=Path, help="Optional immutable snapshot of run directories, each named by job ID")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    manifest = read_json(args.manifest)
    validate_manifest(manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rallymate-rubric-eval-") as temporary:
        engine, hashes = install_engine(args.revision, temporary)
        report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "revision": args.revision or "working_tree",
                  "manifest_sha256": digest(args.manifest), "engine_sha256": hashes, "rubric_version": engine.RUBRIC_VERSION,
                  "inputs_frozen_snapshot": args.run_root is not None,
                  "labels_passed_to_scoring": False, "gpu_inference": False,
                  "timing_scope": "saved native poses; pure CPU analysis and file-loading builder measured separately; OS caches not flushed",
                  "samples": []}
        for row in manifest["samples"]:
            item, assessment = measure_sample(engine, row, args.repeats, args.variants, args.run_root)
            report["samples"].append(item)
            if assessment:
                (args.output / f"{row['sample_id']}-assessment.json").write_text(json.dumps(assessment, ensure_ascii=False, indent=2), encoding="utf-8")
            report["distributions"] = distributions(report["samples"])
            (args.output / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"sample_id": item["sample_id"], "score": item.get("score"), "episodes": item.get("episodes"), "evaluation_status": item["evaluation_status"], "checks": item.get("checks"), "variants": item.get("variants")}), flush=True)


if __name__ == "__main__":
    main()
