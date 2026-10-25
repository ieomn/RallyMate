"""Explicit offline publication of independent candidates, never a scoring job.

The administrator supplies a separately reviewed expected-receipt JSON containing
schema_version=foot-crossing-publication-expected-v1, job_id, algorithm_version,
artifact_version, video_id, video_sha256, source_job_id, job_summary_sha256,
source_summary_sha256, inputs (frames/events/primary byte counts and SHA256),
code (relative source name, bytes, SHA256), and previous_index_sha256 (or null).
No expected values are learned from an existing publication or a browser request.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
INPUT_NAMES = {"frames": "frames.jsonl", "events": "events.jsonl", "primary": "primary-player.jsonl"}
PRIMARY_TIMELINE_ADAPTER_VERSION = "native-sam-primary-epochs-v1.0.0"
REQUIRED_CODE = {
    "src/rallymate_features/foot_crossing.py",
    "src/rallymate_scoring/source_assessment.py",
    "src/rallymate_features/coordinates.py",
    "src/rallymate_features/native_primary.py",
    "src/rallymate_features/schemas.py",
    "src/rallymate_features/validity.py",
    "src/rallymate_scoring/pose_observations.py",
    "src/rallymate_vision/pose/metadata.py",
    "src/rallymate_scoring/data/technical_review_rules.json",
    "src/rallymate_service/foot_crossing_candidates.py",
    "scripts/publish_foot_crossing_candidates.py",
}


class PublicationError(ValueError):
    """Safe, fixed reason codes; never includes a database row or local payload."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PublicationError(reason)


def _contracts():
    from rallymate_service import foot_crossing_candidates
    return foot_crossing_candidates


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest(value) -> str:
    _require(isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None, "invalid_expected_digest")
    return value.lower()


def _plain_path(path: Path, root: Path, *, file: bool = True) -> Path:
    path = path.absolute()
    _require(path.is_relative_to(root) and path != root, "path_outside_authorized_root")
    for part in (path, *path.parents):
        _require(not part.is_symlink(), "symlink_not_allowed")
        if part == root:
            break
    _require(path.resolve() == path, "noncanonical_path")
    if file:
        _require(path.is_file(), "required_file_missing")
    return path


def _read_json(path: Path, limit: int):
    _require(path.stat().st_size <= limit, "json_limit_exceeded")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    _require(len(raw) <= limit, "json_limit_exceeded")
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=no_duplicates,
                      parse_constant=lambda _: (_ for _ in ()).throw(PublicationError("nonfinite_json")))


def _file_snapshot(path: Path, contract) -> dict:
    before = contract.file_stat(path)
    digest = _sha(path)
    _require(before == contract.file_stat(path), "file_changed_while_hashing")
    return {"bytes": before["size"], "sha256": digest, "stat": before}


def _db_snapshot(path: Path, contract) -> dict:
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(path) + suffix)
        result[suffix] = _file_snapshot(candidate, contract) if candidate.is_file() else None
    return result


def _read_job(database: Path, job_id: str) -> dict:
    # Even a read-only SQLite WAL connection may update shared-memory reader
    # bookkeeping. Only the independent byte copy is ever opened by SQLite.
    # Keep the real WAL: immutable=1 on the live database would silently ignore it.
    contract = _contracts()
    before = _db_snapshot(database, contract)
    # A rollback journal may contain the only committed-state recovery data.
    # Do not interpret the main file alone or recover the operator's database.
    _require(before["-journal"] is None, "rollback_journal_present")
    snapshot_root = _plain_path(database.parent / "derived-foot-crossing", database.parent, file=False)
    snapshot_root.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".db-read-", dir=snapshot_root) as temporary:
        snapshot = Path(temporary) / database.name
        for suffix in ("", "-wal"):
            source = Path(str(database) + suffix)
            if before[suffix] is not None:
                shutil.copyfile(source, Path(str(snapshot) + suffix))
                _require(_sha(Path(str(snapshot) + suffix)) == before[suffix]["sha256"], "database_changed_during_snapshot")
        _require(_db_snapshot(database, contract) == before, "database_changed_during_snapshot")
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT id,status,output_dir,summary_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
        _require(_db_snapshot(database, contract) == before, "database_changed_during_snapshot")
    _require(row is not None, "job_missing")
    job = dict(row)
    _require(job["status"] == "succeeded", "job_not_succeeded")
    job["summary"] = json.loads(job.pop("summary_json"))
    _require(isinstance(job["summary"], dict), "invalid_job_summary")
    return job


def _history_snapshot(output: Path, contract) -> dict:
    """Include scores and every other historical artifact, not only inputs."""
    result = {}
    for path in sorted(output.rglob("*")):
        _plain_path(path, output, file=False)
        if path.is_file():
            result[path.relative_to(output).as_posix()] = _file_snapshot(path, contract)
        else:
            _require(path.is_dir(), "nonregular_historical_artifact")
    return result


def _code_snapshot(expected: list, contract) -> list:
    _require(isinstance(expected, list) and 10 <= len(expected) <= 32, "invalid_expected_code")
    names = [row.get("name") for row in expected if isinstance(row, dict)]
    _require(len(names) == len(expected) and len(set(names)) == len(names)
             and REQUIRED_CODE.issubset(names), "incomplete_expected_code")
    result = []
    for row in sorted(expected, key=lambda item: item["name"]):
        name = row["name"]
        _require(isinstance(name, str) and "\\" not in name and ".." not in Path(name).parts
                 and (name.startswith("src/") or name == "scripts/publish_foot_crossing_candidates.py"), "invalid_code_name")
        path = _plain_path(ROOT / name, ROOT)
        actual = _file_snapshot(path, contract)
        _require(actual["sha256"] == _digest(row.get("sha256")) and actual["bytes"] == row.get("bytes"), "expected_code_mismatch")
        result.append({"name": name, "bytes": actual["bytes"], "sha256": actual["sha256"]})
    return result


def _adapt_primary_timeline(records: list, timeline: list, *, video_sha256: str) -> tuple[list, dict]:
    """Compatibility entry point for the shared, unchanged native epoch join."""
    from rallymate_features.native_primary import (
        NativePrimaryTimelineError,
        adapt_native_primary_timeline,
    )

    try:
        return adapt_native_primary_timeline(records, timeline, video_sha256=video_sha256)
    except NativePrimaryTimelineError as error:
        raise PublicationError(str(error)) from error


def _produce(staging: Path, job: dict, inputs: dict, contract) -> dict:
    """Only the independent candidate writer is called; no loop/worker/model."""
    from rallymate_features.coordinates import pose_sequence_from_records
    from rallymate_scoring.source_assessment import write_foot_crossing_candidates

    def rows(name):
        with (staging / name).open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    records, timeline, events = (rows(INPUT_NAMES[key]) for key in ("frames", "primary", "events"))
    loop = job["summary"]["minimum_scoring_loop"]
    video_id = loop["video_id"]
    _require(all(event.get("video_id") == video_id for event in events), "event_video_identity_mismatch")
    timeline, adaptation = _adapt_primary_timeline(records, timeline, video_sha256=loop["provenance"]["video_sha256"])
    sequence = pose_sequence_from_records(records, timeline)
    write_foot_crossing_candidates(staging, sequence, events, video_id=video_id,
                                  video_sha256=loop["provenance"]["video_sha256"],
                                  frames_sha256=inputs["frames"]["sha256"],
                                  frames_path=staging / INPUT_NAMES["frames"],
                                  primary_timeline_path=staging / INPUT_NAMES["primary"],
                                  primary_timeline=timeline)
    time_binding = sequence.coordinate_metadata["source_time_binding"]
    return {**adaptation, "bound_frame_count": time_binding["bound_frame_count"],
            "unavailable_frame_reasons": dict(sorted(Counter(time_binding["unavailable_source_frames"].values()).items()))}


def _write_new(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish(data_root: Path, database: Path, job_id: str, expected: dict, *, producer=None) -> dict:
    """Publish once under an exclusive job lock, then atomically change index.

    `producer` is an in-process test seam; the CLI cannot name/import plugins.
    An interrupted attempt may leave an unindexed immutable directory. It never
    changes historical artifacts and cannot expose an incomplete publication.
    """
    contract = _contracts()
    _require(str(uuid.UUID(job_id)) == job_id, "invalid_job_id")
    data_root = Path(data_root).absolute()
    _require(data_root.is_dir() and data_root.resolve() == data_root and not data_root.is_symlink(), "invalid_data_root")
    database = _plain_path(Path(database), data_root)
    _require(database.parent == data_root, "database_must_be_direct_child_of_data_root")
    _require(isinstance(expected, dict) and expected.get("schema_version") == "foot-crossing-publication-expected-v1"
             and expected.get("job_id") == job_id, "expected_receipt_identity_mismatch")
    _require(expected.get("algorithm_version") == contract.ALGORITHM_VERSION
             and expected.get("artifact_version") == contract.ARTIFACT_VERSION, "expected_version_mismatch")
    code = _code_snapshot(expected.get("code"), contract)
    db_before = _db_snapshot(database, contract)
    job = _read_job(database, job_id)
    output = _plain_path(data_root / "runs" / job_id, data_root, file=False)
    _require(Path(job["output_dir"]).resolve() == output and output.is_dir(), "job_output_directory_mismatch")
    summary_path = _plain_path(output / "summary.json", data_root)
    summary = _read_json(summary_path, contract.LIMITS["summary_bytes"])
    _require(_json_bytes(summary) == _json_bytes(job["summary"]), "database_and_saved_summary_mismatch")
    summary_before = _file_snapshot(summary_path, contract)
    _require(contract.canonical_sha(summary) == _digest(expected.get("job_summary_sha256"))
             and summary_before["sha256"] == _digest(expected.get("source_summary_sha256")), "expected_summary_mismatch")
    loop = summary.get("minimum_scoring_loop", {})
    provenance = loop.get("provenance", {})
    _require(loop.get("video_id") == expected.get("video_id") and isinstance(loop.get("video_id"), str), "expected_video_id_mismatch")
    video_sha = _digest(provenance.get("video_sha256"))
    _require(video_sha == _digest(expected.get("video_sha256")), "expected_video_hash_mismatch")
    source_ids = [summary.get("source_failed_job_id"),
                  (summary.get("recovery") or {}).get("source_failed_job_id"), provenance.get("pipeline_job_id")]
    source_ids = [value for value in source_ids if value is not None] or [job_id]
    _require(all(value == source_ids[0] for value in source_ids)
             and source_ids[0] == expected.get("source_job_id"), "expected_source_job_mismatch")
    inputs = {}
    for key, name in INPUT_NAMES.items():
        actual = _file_snapshot(_plain_path(output / name, data_root), contract)
        pin = expected.get("inputs", {}).get(key, {})
        _require(actual["bytes"] == pin.get("bytes") and actual["sha256"] == _digest(pin.get("sha256")), "expected_input_mismatch")
        inputs[key] = {"name": name, **actual}
    _require(inputs["frames"]["sha256"] == _digest(provenance.get("frames_sha256")), "historical_frames_hash_mismatch")
    _require(inputs["events"]["sha256"] == _digest(loop.get("artifact_sha256", {}).get("events_jsonl")), "historical_events_hash_mismatch")
    for key, name in INPUT_NAMES.items():
        recovery_pin = (summary.get("recovery") or {}).get("source_artifact_sha256", {}).get(name)
        if recovery_pin is not None:
            _require(inputs[key]["sha256"] == _digest(recovery_pin), "historical_recovery_hash_mismatch")
    history_before = _history_snapshot(output, contract)

    derived = _plain_path(data_root / "derived-foot-crossing", data_root, file=False)
    derived.mkdir(mode=0o700, exist_ok=True)
    destination = _plain_path(derived / job_id, data_root, file=False)
    destination.mkdir(mode=0o700, exist_ok=True)
    lock = destination / ".publish.lock"
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes({"job_id": job_id, "pid": os.getpid()}))
            stream.flush()
            os.fsync(stream.fileno())
        index = _plain_path(destination / "index.json", data_root, file=False)
        index_before = _sha(index) if index.is_file() else None
        _require("previous_index_sha256" in expected and index_before == expected["previous_index_sha256"], "existing_index_not_authorized")

        def guards():
            _require(_read_job(database, job_id) == job, "job_changed_during_publication")
            _require(_db_snapshot(database, contract) == db_before, "database_changed_during_publication")
            _require(_file_snapshot(summary_path, contract) == summary_before, "summary_changed_during_publication")
            for key, name in INPUT_NAMES.items():
                _require(_file_snapshot(output / name, contract) == {k: v for k, v in inputs[key].items() if k != "name"}, "input_changed_during_publication")
            _require(_history_snapshot(output, contract) == history_before, "historical_artifacts_changed_during_publication")
            _require(_code_snapshot(expected["code"], contract) == code, "code_changed_during_publication")
            _require((_sha(index) if index.is_file() else None) == index_before, "index_changed_during_publication")

        guards()
        with tempfile.TemporaryDirectory(prefix=".publication-", dir=destination) as temporary:
            staging = Path(temporary)
            for key, name in INPUT_NAMES.items():
                # Independent byte copies only: no hard links or writable aliases.
                shutil.copyfile(output / name, staging / name)
                _require(_sha(staging / name) == inputs[key]["sha256"], "input_copy_mismatch")
            input_adapter_receipt = (producer or _produce)(staging, job, inputs, contract)
            artifact_path = _plain_path(staging / contract.ARTIFACT_NAME, staging)
            artifact = _read_json(artifact_path, contract.LIMITS["artifact_bytes"])
            artifact_ref = _file_snapshot(artifact_path, contract)
            manifest = {
                "schema_version": contract.MANIFEST_VERSION, "display_job_id": job_id,
                "source_job_id": source_ids[0], "video_id": loop["video_id"], "video_sha256": video_sha,
                "job_summary_sha256": contract.canonical_sha(summary), "source_summary_sha256": summary_before["sha256"],
                "input_bundle_sha256": contract.input_bundle_sha(loop["video_id"], video_sha, inputs),
                "inputs": inputs, "producer": {"algorithm_version": contract.ALGORITHM_VERSION, "code": code},
                "source_binding": contract.source_binding(),
                "artifact": {"name": contract.ARTIFACT_NAME, "bytes": artifact_ref["bytes"], "sha256": artifact_ref["sha256"],
                             "artifact_version": contract.ARTIFACT_VERSION, "algorithm_version": contract.ALGORITHM_VERSION},
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "input_binding_mode": "verified_publication_snapshot",
            }
            contract.validate_publication(manifest, artifact, job)
            manifest_raw = _json_bytes(manifest)
            _require(len(manifest_raw) <= contract.LIMITS["manifest_bytes"], "manifest_limit_exceeded")
            manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
            publication = _plain_path(destination / manifest_sha, data_root, file=False)
            ready = staging / "ready"
            ready.mkdir(mode=0o700)
            _write_new(ready / "manifest.json", manifest_raw)
            _write_new(ready / contract.ARTIFACT_NAME, artifact_path.read_bytes())
            _sync_directory(ready)
            guards()
            if publication.exists():
                _require(publication.is_dir() and {p.name for p in publication.iterdir()} == {"manifest.json", contract.ARTIFACT_NAME}, "immutable_publication_conflict")
                _require(_sha(_plain_path(publication / "manifest.json", data_root)) == manifest_sha
                         and _sha(_plain_path(publication / contract.ARTIFACT_NAME, data_root)) == artifact_ref["sha256"], "immutable_publication_conflict")
            else:
                ready.rename(publication)
                _sync_directory(destination)
            # Recheck all original bytes after installing the immutable output,
            # before publishing the discoverable pointer.
            guards()
            index_payload = {"schema_version": contract.INDEX_VERSION, "job_id": job_id, "manifest_sha256": manifest_sha}
            temporary_index = destination / (".index-" + uuid.uuid4().hex + ".tmp")
            try:
                _write_new(temporary_index, _json_bytes(index_payload))
                os.replace(temporary_index, index)
                _sync_directory(destination)
            finally:
                temporary_index.unlink(missing_ok=True)
            receipt = {"status": "published", "job_id": job_id, "manifest_sha256": manifest_sha,
                    "artifact_sha256": artifact_ref["sha256"], "input_bundle_sha256": manifest["input_bundle_sha256"],
                    "historical_bytes_verified_unchanged_before_index": True,
                    "database_writes": 0, "scoring_runs": 0, "model_inference_runs": 0}
            if producer is None:
                receipt["input_adapter"] = input_adapter_receipt
            return receipt
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--expected-receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    # Explicit administrator invocation is the only production compute path.
    sys.path.insert(0, str(ROOT / "src"))
    try:
        expected = _read_json(args.expected_receipt, 65536)
        receipt = publish(args.data_root, args.database, args.job_id, expected)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, PublicationError) else "publication_failed"
        print(json.dumps({"status": "failed", "reason_code": reason, "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
