"""Add source-aligned measurements to completed local jobs without re-inference.

Only source-aligned-measurements.json and the separate audit ledger are written.
Original scores/events/frames/reports are hashed before and after every job.
This checks measurement implementation, not coach accuracy or technical grades.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_scoring.source_assessment import ARTIFACT_NAME, file_sha, write_source_aligned_assessment
from rallymate_service.source_assessment import load_source_aligned_assessment


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path):
    def invalid(value):
        raise ValueError(f"non-finite JSON value: {value}")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for attempt in range(40):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(.05)


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def read_job(database: Path, job_id: str) -> dict:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("job not found")
        result = dict(row)
        result["summary"] = json.loads(result.pop("summary_json"))
        return result


def code_fingerprint() -> dict:
    paths = ["src/rallymate_features/source_alignment.py", "src/rallymate_events/associations.py",
             "src/rallymate_features/coordinates.py", "src/rallymate_features/schemas.py",
             "src/rallymate_scoring/source_assessment.py", "src/rallymate_service/source_assessment.py",
             "src/rallymate_scoring/data/technical_review_rules.json", "scripts/backfill_source_assessments.py"]
    hashes = {name: file_sha(ROOT / name) for name in paths}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "files": hashes, "python_version": sys.version.split()[0]}


def core_hashes(output: Path) -> dict:
    return {path.name: file_sha(path) for path in sorted(output.iterdir()) if path.is_file()
            and path.name not in {ARTIFACT_NAME, ARTIFACT_NAME + ".tmp"}}


def validate_baseline(entry: dict | None, job: dict, before: dict) -> tuple[str, str]:
    summary, output = job["summary"], Path(job["output_dir"])
    if job["status"] != "succeeded":
        raise ValueError("job must be completed before backfill")
    loop = summary["minimum_scoring_loop"]
    provenance = loop["provenance"]
    source_sha = file_sha(Path(job["video_path"]))
    if source_sha != provenance["video_sha256"].lower():
        raise ValueError("source bytes do not match original scoring provenance")
    if entry and source_sha != entry["source_sha256"].lower():
        raise ValueError("source bytes do not match batch manifest")
    frames_sha = before["frames.jsonl"]
    if frames_sha != provenance["frames_sha256"].lower():
        raise ValueError("frames changed since original scoring pipeline")
    for name, digest in loop.get("artifact_sha256", {}).items():
        filename = {"events_jsonl": "events.jsonl", "features_jsonl": "features.jsonl",
                    "indicator_features_jsonl": "indicator-features.jsonl", "scores_jsonl": "scores.jsonl",
                    "event_feature_errors_json": "event-feature-errors.json"}.get(name, name)
        if filename in before and before[filename] != digest.lower():
            raise ValueError(f"original scoring artifact changed: {name}")
    if entry:
        for name, metadata in entry.get("artifacts", {}).items():
            filename = "summary.json" if name == "summary" else name
            if filename in before and metadata.get("sha256") != before[filename]:
                raise ValueError(f"original batch artifact changed: {filename}")
    if not all((output / name).is_file() for name in ["frames.jsonl", "primary-player.jsonl", "events.jsonl", "summary.json", "scores.jsonl"]):
        raise ValueError("required original artifact missing")
    return source_sha, frames_sha


def summarize_artifact(artifact: dict) -> dict:
    indicators = defaultdict(lambda: {"windows": 0, "measured_windows": 0, "unavailable_windows": 0,
                                      "measurement_status_counts": Counter(), "reason_counts": Counter(),
                                      "coverage_pass_count": 0, "measurement_samples": []})
    phases, repaired = {}, set()
    formal_grades = 0
    for record in artifact["records"]:
        formal_grades += int(record.get("grade") is not None)
        for item in record["indicators"]:
            target = indicators[item["indicator_id"]]
            target["windows"] += 1
            measured = any(row["status"] == "measured" for row in item["source_measurements"])
            target["measured_windows" if measured else "unavailable_windows"] += 1
            target["coverage_pass_count"] += int(item["source_rule_gate"]["pose_coverage"].get("passes") is True)
            for measurement in item["source_measurements"]:
                target["measurement_status_counts"][measurement["status"]] += 1
                if measurement.get("reason"):
                    target["reason_counts"][measurement["reason"]] += 1
                sample = {"event_id": record["event_id"], "feature_name": measurement["feature_name"],
                          "value": measurement["value"], "unit": measurement["unit"],
                          "status": measurement["status"], "reason": measurement.get("reason"),
                          "window_ms": measurement.get("window_ms")}
                # Keep up to two measured and two unavailable examples per
                # feature, so a large video cannot erase the early failures.
                matching = sum(row["feature_name"] == sample["feature_name"] and row["status"] == sample["status"]
                               for row in target["measurement_samples"])
                if matching < 2:
                    target["measurement_samples"].append(sample)
                phase = measurement.get("evidence", {}).get("source_phase")
                if phase:
                    phases[record["event_id"]] = phase
                    if phase.get("status") == "observed_candidate" and phase.get("source_preload_ms") != phase.get("original_preload_ms"):
                        repaired.add(record["event_id"])
    if formal_grades or artifact.get("technical_grade") is not None or artifact.get("technical_score_0_to_100") is not None:
        raise ValueError("source-aligned measurements must never create a technical grade or score")
    return {"event_count": len(artifact["records"]), "indicators": dict(indicators),
            "phase_status_counts": dict(Counter(phase["status"] for phase in phases.values())),
            "phase_contract_versions": sorted({phase.get("phase_contract_version", "unspecified") for phase in phases.values()}),
            "phase_reason_counts": dict(Counter(phase["repair_reason"] for phase in phases.values())),
            "phase_repaired_event_count": len(repaired),
            "phase_repaired_examples": [{"event_id": key, **phases[key]} for key in sorted(repaired)[:8]],
            "technical_grade": None, "technical_score_0_to_100": None}


def process_job(entry: dict | None, job: dict) -> dict:
    started = time.perf_counter()
    output = Path(job["output_dir"])
    before = core_hashes(output)
    source_sha, frames_sha = validate_baseline(entry, job, before)
    loop = job["summary"]["minimum_scoring_loop"]
    try:
        records = load_jsonl(output / "frames.jsonl")
        timeline = load_jsonl(output / "primary-player.jsonl")
        events = load_jsonl(output / "events.jsonl")
        sequence = pose_sequence_from_records(records, timeline)
        read_seconds = time.perf_counter() - started
        calculation_started = time.perf_counter()
        created = write_source_aligned_assessment(output, sequence, events, video_id=loop["video_id"],
                    video_sha256=source_sha, frames_sha256=frames_sha, primary_timeline=timeline)
        calculation_seconds = time.perf_counter() - calculation_started
        artifact = load_json(output / ARTIFACT_NAME)
        statistics = summarize_artifact(artifact)
        public = load_source_aligned_assessment(output, video_id=loop["video_id"],
                    duration_ms=job["summary"]["input"]["video"]["duration_ms"],
                    video_sha256=source_sha, frames_sha256=frames_sha)
        # An empty event set is legitimately unavailable. Distinguish that
        # complete, source-bound projection from the loader's fail-closed
        # response for a corrupt artifact, which has no artifact version.
        if (public.get("measurement_artifact_version") != artifact["version"]
                or public.get("source_binding") != artifact["source_binding"]
                or any(item["window_count"] != statistics["indicators"].get(item["indicator_id"], {}).get("windows", 0)
                       for item in public["indicators"])):
            raise ValueError("new measurement artifact failed the public projection validation")
        result = {"job_id": job["id"], "state": "verified", "completed_at": now(), "source_sha256": source_sha,
                  "frames_sha256": frames_sha, "frame_count": len(records), "input_event_count": len(events),
                  "artifact": {**created, "path": relative(output / ARTIFACT_NAME)},
                  "core_sha256_before": before, "read_and_sequence_seconds": round(read_seconds, 3),
                  "measurement_seconds": round(calculation_seconds, 3), "statistics": statistics,
                  "public_projection": {"status": public["status"], "indicators": [
                      {key: item[key] for key in ("indicator_id", "window_count", "measured_window_count",
                                                 "unavailable_window_count", "returned_window_count", "is_truncated")}
                      for item in public["indicators"]]}}
    finally:
        after = core_hashes(output)
        if before != after:
            changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
            raise ValueError(f"original core artifacts changed during backfill: {changed}")
        if file_sha(Path(job["video_path"])) != source_sha:
            raise ValueError("source video changed during backfill")
    result["core_sha256_after"] = after
    result["original_core_artifacts_unchanged"] = True
    result["total_seconds"] = round(time.perf_counter() - started, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / ".codex_tmp/scoring-word-audit-20261004/batch-manifest.json")
    parser.add_argument("--job-id", action="append", default=[])
    parser.add_argument("--database", type=Path, default=ROOT / "service_data/jobs.sqlite3")
    parser.add_argument("--audit-output", type=Path, default=ROOT / ".codex_tmp/scoring-word-audit-20261004/source-alignment-audit.json")
    args = parser.parse_args()
    manifest = load_json(args.manifest) if args.manifest.exists() else {"entries": []}
    by_job = {entry["job_id"]: entry for entry in manifest["entries"] if entry.get("job_id")}
    jobs = list(dict.fromkeys(args.job_id)) or list(by_job)
    if not jobs:
        parser.error("provide an existing batch manifest or at least one --job-id")
    fingerprint = code_fingerprint()
    prior = load_json(args.audit_output) if args.audit_output.exists() else {}
    rows = {row["job_id"]: row for row in prior.get("entries", [])}
    audit = {"schema_version": "source-alignment-backfill-audit-v1", "started_at": now(),
             "scope": "existing_pose_artifacts_only_no_gpu_no_coach_grade_validation", "code": fingerprint,
             "source_manifest_sha256": file_sha(args.manifest) if args.manifest.exists() else None,
             "technical_validity": "not_established", "entries": list(rows.values())}
    failures = 0
    for job_id in jobs:
        job = read_job(args.database, job_id)
        prior_row = rows.get(job_id)
        if (prior.get("code", {}).get("sha256") == fingerprint["sha256"] and prior_row
                and prior_row.get("state") == "verified" and
                (Path(job["output_dir"]) / ARTIFACT_NAME).is_file() and
                file_sha(Path(job["output_dir"]) / ARTIFACT_NAME) == prior_row["artifact"]["sha256"] and
                core_hashes(Path(job["output_dir"])) == prior_row["core_sha256_after"]):
            print(f"{job_id}: already verified; unchanged inputs and implementation", flush=True)
            continue
        rows[job_id] = {"job_id": job_id, "state": "running", "started_at": now()}
        audit["entries"] = list(rows.values())
        save_json(args.audit_output, audit)
        try:
            rows[job_id] = process_job(by_job.get(job_id), job)
            print(f"{job_id}: verified {rows[job_id]['frame_count']} frames, {rows[job_id]['total_seconds']}s, "
                  f"repaired phase candidates {rows[job_id]['statistics']['phase_repaired_event_count']}", flush=True)
        except Exception as exc:
            rows[job_id] = {"job_id": job_id, "state": "failed", "completed_at": now(),
                            "error_type": type(exc).__name__, "error": str(exc)[:1000]}
            failures += 1
            print(f"{job_id}: failed {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        audit["entries"] = list(rows.values())
        audit["updated_at"] = now()
        audit["counts"] = dict(Counter(row["state"] for row in rows.values()))
        save_json(args.audit_output, audit)
    audit["completed_at"] = now()
    audit["counts"] = dict(Counter(row["state"] for row in rows.values()))
    save_json(args.audit_output, audit)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
