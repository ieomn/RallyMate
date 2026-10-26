"""Recompute a source-bound subject/stroke regression without GPU or live writes.

The round directory contains manifest.json, source-inventory.json and optional
action-contexts/<job_id>.json. Native poses and old candidates are read from the
frozen previous round. This deliberately does not relabel native poses when the
new selector chooses another track. Private videos remain external local inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rallymate_tracking.primary_player import select_primary_player_timeline
from rallymate_service.tennis_assessment import (
    VERSION, _summary_episodes, analyze_tennis_assessment,
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def code_hashes():
    paths = [*sorted((ROOT / "src/rallymate_service").glob("tennis*.py")),
             ROOT / "src/rallymate_tracking/primary_player.py",
             ROOT / "src/rallymate_tracking/subject_lock.py", Path(__file__).resolve()]
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in paths}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-root", type=Path, required=True,
                        help="Directory containing this round's manifest, inventory and contexts.")
    parser.add_argument("--frozen-root", type=Path, required=True,
                        help="Previous round directory containing frozen-runs and after-final.")
    parser.add_argument("--source-root", type=Path, default=ROOT,
                        help="Base for source-inventory relative video paths (default repository).")
    parser.add_argument("--runs-root", type=Path, default=ROOT / "service_data/runs",
                        help="Read-only original detector frames, grouped by job ID.")
    parser.add_argument("--context-dir", type=Path,
                        help="Optional override; defaults to round-root/action-contexts.")
    parser.add_argument("--output-name", required=True,
                        help="New child directory name in round-root; existing outputs are refused.")
    parser.add_argument("--reuse-timeline-dir", type=Path,
                        help="Optional saved selector results; this run then validates scoring only.")
    args = parser.parse_args()
    if Path(args.output_name).name != args.output_name or args.output_name in {".", ".."}:
        parser.error("--output-name must be a single new directory name")
    selector_parent = None
    selector_parent_sha = None
    selector_parent_rows = {}
    if args.reuse_timeline_dir:
        parent_path = args.reuse_timeline_dir / "evaluation.json"
        selector_parent = read(parent_path)
        if selector_parent.get("selector_recomputed") is not True or selector_parent.get("code_changed_during_execution") is not False:
            raise ValueError("Reused timelines require a completed parent with fresh, unchanged selector code")
        current_hashes = code_hashes()
        for dependency in ("src/rallymate_tracking/primary_player.py", "src/rallymate_tracking/subject_lock.py"):
            expected = selector_parent.get("code_hashes_at_start", {}).get(dependency)
            if not expected or expected != current_hashes[dependency] or expected != selector_parent.get("code_hashes_at_end", {}).get(dependency):
                raise ValueError(f"Parent selector code differs from current code: {dependency}")
        if selector_parent.get("error_count") != 0:
            raise ValueError("Reused selector parent contains errors")
        selector_parent_sha = sha256(parent_path)
        selector_parent_rows = {r["sample_id"]: r for r in selector_parent["samples"]}
    output = args.round_root / args.output_name
    output.mkdir(parents=False, exist_ok=False)
    contexts = args.context_dir or args.round_root / "action-contexts"
    manifest_path = args.round_root / "manifest.json"
    inventory_path = args.round_root / "source-inventory.json"
    manifest = read(manifest_path)
    inventory = {r["sample_id"]: r for r in read(inventory_path)["sources"]}
    before_path = args.frozen_root / "after-final/evaluation.json"
    before = {r["sample_id"]: r for r in read(before_path)["samples"]}
    started_at, start_hashes = utc_now(), code_hashes()
    counts = []
    for row in manifest["samples"]:
        name, job = row["sample_id"], row["job_id"]
        item = {"sample_id": name, "job_id": job, "source_group": row["source_group"],
                "evaluation_label": row["evaluation_label"], "split": row["split"],
                "source_video_sha256": row["source_video_sha256"].lower(),
                "old_score": before[name]["score"], "old_scored_actions": before[name]["episodes"],
                "started_at_utc": utc_now()}
        started = time.monotonic()
        try:
            frozen = args.frozen_root / "frozen-runs" / job
            summary_path = frozen / "summary.json"
            native_path = frozen / "sam3d_frames.jsonl"
            source = args.source_root / inventory[name]["source_relative_path"]
            frames_path = args.runs_root / job / "frames.jsonl"
            frozen_hashes = {filename: sha256(frozen / filename)
                             for filename in ("summary.json", "sam3d_frames.jsonl")}
            for filename, digest in frozen_hashes.items():
                expected = row.get("frozen_artifact_sha256", {}).get(filename)
                if expected and digest != expected.lower():
                    raise ValueError(f"Frozen input SHA differs from manifest: {filename}")
            actual = sha256(source)
            if actual != item["source_video_sha256"]:
                raise ValueError("Submitted source SHA differs from the regression manifest")
            frames, records = read_rows(frames_path), read_rows(native_path)
            if args.reuse_timeline_dir:
                timeline_path = args.reuse_timeline_dir / f"{name}-primary-player.jsonl"
                parent_row = selector_parent_rows.get(name, {})
                timeline_sha = sha256(timeline_path)
                if (parent_row.get("job_id") != job
                        or parent_row.get("source_video_sha256") != actual
                        or parent_row.get("output_timeline_sha256") != timeline_sha
                        or parent_row.get("input_sha256", {}).get("frames.jsonl") != sha256(frames_path)):
                    raise ValueError("Parent timeline, source or detector frames binding differs from current inputs")
                timeline = read_rows(timeline_path)
                item["reused_timeline_sha256"] = timeline_sha
            else:
                timeline = select_primary_player_timeline(frames, source_video_path=source)["timeline"]
            context_path = contexts / f"{job}.json"
            context = read(context_path) if context_path.exists() else None
            result = analyze_tennis_assessment(
                timeline, records, video_sha256=actual, source_run_id=job,
                motion_episodes=_summary_episodes(read(summary_path)),
                source_frames=frames, action_context=context,
            )
            write(output / f"{name}-assessment.json", result)
            timeline_path = output / f"{name}-primary-player.jsonl"
            timeline_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in timeline),
                                     encoding="utf-8")
            selected = [r for r in timeline if r.get("selection_status") == "selected"]
            selected_keys = {(r["source_frame_index"], r.get("source_track_id")) for r in selected}
            native_keys = {(r["frame_index"], r.get("person_track_id")) for r in records if r.get("status") == "ok"}
            item.update(
                status=result["status"], score=result.get("overall_score"), episodes=len(result["episodes"]),
                candidate_accounting=result["candidate_accounting"], episode_validity=result.get("episode_validity", []),
                subject_scope=result.get("subject_scope"), pattern_coverage=result.get("pattern_coverage"),
                quality_reason_counts=result.get("quality_reason_counts"), selected_frames=len(selected),
                selected_frames_with_saved_native=len(selected_keys & native_keys),
                selected_frames_missing_saved_native=len(selected_keys - native_keys), old_native_records=len(records),
                selected_tracks=dict(Counter(str(r.get("source_track_id")) for r in selected)),
                selector_reason_counts=dict(Counter(str(r.get("subject_identity", {}).get("reason_code")) for r in timeline)),
                context_actions=len(context["actions"]) if context else 0,
                input_sha256={"video": actual, "frames.jsonl": sha256(frames_path),
                              "sam3d_frames.jsonl": frozen_hashes["sam3d_frames.jsonl"],
                              "summary.json": frozen_hashes["summary.json"],
                              "action_context": sha256(context_path) if context else None},
                output_timeline_sha256=sha256(timeline_path),
            )
        except Exception as exc:
            item.update(status="error", error=repr(exc), traceback=traceback.format_exc())
        item["elapsed_seconds"] = round(time.monotonic() - started, 3)
        item["completed_at_utc"] = utc_now()
        counts.append(item)
        write(output / "evaluation.partial.json", {"samples": counts})
        print(json.dumps({key: item.get(key) for key in (
            "sample_id", "status", "score", "episodes", "selected_frames_missing_saved_native", "error"
        )}, ensure_ascii=False), flush=True)
    end_hashes = code_hashes()
    result = {"schema_version": "tennis-round3-offline-regression-v1", "assessment_version": VERSION,
              "not_independent_holdout": True, "gpu_requested": False, "frozen_native_only": True,
              "selector_recomputed": args.reuse_timeline_dir is None, "python_version": platform.python_version(),
              "selector_parent_evaluation_sha256": selector_parent_sha,
              "selector_parent_code_verified": selector_parent is not None,
              "started_at_utc": started_at, "completed_at_utc": utc_now(),
              "manifest_sha256": sha256(manifest_path), "inventory_sha256": sha256(inventory_path),
              "before_evaluation_sha256": sha256(before_path), "code_hashes_at_start": start_hashes,
              "code_hashes_at_end": end_hashes, "code_changed_during_execution": start_hashes != end_hashes,
              "sample_count": len(counts), "error_count": sum(r["status"] == "error" for r in counts), "samples": counts}
    write(output / "evaluation.json", result)
    print(json.dumps({"completed": args.output_name, "samples": len(counts), "errors": result["error_count"],
                      "code_changed_during_execution": result["code_changed_during_execution"]}), flush=True)


if __name__ == "__main__":
    main()
