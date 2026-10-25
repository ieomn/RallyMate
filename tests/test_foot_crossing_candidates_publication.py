"""Offline publication boundaries, using only private two-frame fixtures."""
from __future__ import annotations

from contextlib import closing
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.source_assessment import write_foot_crossing_candidates
from rallymate_service import foot_crossing_candidates as reader
from test_foot_crossing_candidates import crossing_fixture

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("foot_crossing_publication_under_test", ROOT / "scripts/publish_foot_crossing_candidates.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
JOB = "11111111-1111-4111-8111-111111111111"
SOURCE = "22222222-2222-4222-8222-222222222222"
VIDEO = "33333333-3333-4333-8333-333333333333"


class FootCrossingPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "runs" / JOB
        self.output.mkdir(parents=True)
        self.database = self.root / "jobs.sqlite3"
        self.sequence, self.timeline = crossing_fixture()
        # The producer below deliberately injects the two-frame PoseSequence;
        # these small input files test byte binding, not the coordinate loader.
        (self.output / "frames.jsonl").write_text('{"fixture":0}\n{"fixture":1}\n', encoding="utf-8")
        (self.output / "primary-player.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.timeline), encoding="utf-8")
        (self.output / "events.jsonl").write_bytes(b"")
        (self.output / "source-aligned-measurements.json").write_bytes(b'{"historical":true}\n')
        (self.output / "scores.jsonl").write_bytes(b'{"historical_score":null}\n')
        self.summary = {
            "job_id": JOB, "source_failed_job_id": SOURCE,
            "minimum_scoring_loop": {"video_id": VIDEO,
                "provenance": {"pipeline_job_id": SOURCE, "video_sha256": "a" * 64,
                               "frames_sha256": publisher._sha(self.output / "frames.jsonl")},
                "artifact_sha256": {"events_jsonl": publisher._sha(self.output / "events.jsonl")}},
        }
        self._save_summary()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT,output_dir TEXT,summary_json TEXT)")
            connection.execute("INSERT INTO jobs VALUES(?,?,?,?)", (JOB, "succeeded", str(self.output), json.dumps(self.summary)))
            connection.commit()
        self.expected = {
            "schema_version": "foot-crossing-publication-expected-v1", "job_id": JOB,
            "algorithm_version": reader.ALGORITHM_VERSION, "artifact_version": reader.ARTIFACT_VERSION,
            "video_id": VIDEO, "video_sha256": "a" * 64, "source_job_id": SOURCE,
            "job_summary_sha256": reader.canonical_sha(self.summary),
            "source_summary_sha256": publisher._sha(self.output / "summary.json"),
            "previous_index_sha256": None,
            "inputs": {key: {"bytes": (self.output / name).stat().st_size, "sha256": publisher._sha(self.output / name)}
                       for key, name in publisher.INPUT_NAMES.items()},
            "code": [{"name": name, "bytes": (ROOT / name).stat().st_size, "sha256": publisher._sha(ROOT / name)}
                     for name in sorted(publisher.REQUIRED_CODE)],
        }

    def _save_summary(self):
        (self.output / "summary.json").write_bytes(publisher._json_bytes(self.summary))

    @property
    def index(self):
        return self.root / "derived-foot-crossing" / JOB / "index.json"

    def produce(self, staging, job, inputs, contract):
        for key, name in publisher.INPUT_NAMES.items():
            self.assertFalse((staging / name).samefile(self.output / name))
            self.assertEqual((staging / name).read_bytes(), (self.output / name).read_bytes())
        write_foot_crossing_candidates(staging, self.sequence, [], video_id=VIDEO,
                                      video_sha256="a" * 64, frames_sha256=inputs["frames"]["sha256"],
                                      primary_timeline=self.timeline)

    def publish(self, **kwargs):
        return publisher.publish(self.root, self.database, JOB, self.expected, producer=kwargs.get("producer", self.produce))

    def history(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in (self.database, *sorted(self.output.iterdir()))}

    def test_publishes_actual_two_frame_writer_and_reader_without_changing_history(self):
        before = self.history()
        result = self.publish()
        self.assertEqual(result["status"], "published")
        self.assertEqual(self.history(), before)
        index = json.loads(self.index.read_bytes())
        directory = self.index.parent / index["manifest_sha256"]
        self.assertEqual({path.name for path in directory.iterdir()}, {"manifest.json", reader.ARTIFACT_NAME})
        self.assertEqual(publisher._sha(directory / "manifest.json"), result["manifest_sha256"])
        response = reader.load_foot_crossing_candidates(self.root, publisher._read_job(self.database, JOB))
        self.assertEqual((response["status"], response["counts"]["candidates"]), ("available", 1))
        self.assertEqual(response["identity"]["source_job_id"], SOURCE)
        self.assertEqual(response["identity"]["video_id"], VIDEO)
        self.assertFalse(response["scoring_allowed"])
        self.assertFalse((self.output / reader.ARTIFACT_NAME).exists())

    def test_database_connection_is_read_only_and_executes_only_select(self):
        real_connect = sqlite3.connect
        calls, statements = [], []
        def traced(database_uri, *args, **kwargs):
            calls.append((database_uri, kwargs))
            connection = real_connect(database_uri, *args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        before = publisher._sha(self.database)
        with patch.object(publisher.sqlite3, "connect", side_effect=traced):
            self.publish()
        self.assertTrue(calls)
        self.assertTrue(all(uri.endswith("?mode=ro") and options["uri"] is True for uri, options in calls))
        self.assertTrue(all(".db-read-" in uri for uri, _ in calls))
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT ") for statement in statements))
        self.assertEqual(publisher._sha(self.database), before)

    def test_live_wal_summary_is_read_from_copy_without_touching_original_shm(self):
        connection = sqlite3.connect(self.database)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        self.summary["wal_only_fixture_value"] = True
        self._save_summary()
        connection.execute("UPDATE jobs SET summary_json=? WHERE id=?", (json.dumps(self.summary), JOB))
        connection.commit()
        self.assertTrue(Path(str(self.database) + "-wal").is_file())
        self.assertTrue(Path(str(self.database) + "-shm").is_file())
        self.expected["job_summary_sha256"] = reader.canonical_sha(self.summary)
        self.expected["source_summary_sha256"] = publisher._sha(self.output / "summary.json")
        before = publisher._db_snapshot(self.database, reader)
        self.assertEqual(self.publish()["status"], "published")
        self.assertEqual(publisher._db_snapshot(self.database, reader), before)

    def test_rollback_journal_is_rejected_before_any_sqlite_connection(self):
        journal = Path(str(self.database) + "-journal")
        journal.write_bytes(b"synthetic pending rollback journal")
        before = publisher._db_snapshot(self.database, reader)
        with patch.object(publisher.sqlite3, "connect", side_effect=AssertionError("SQLite must not open")):
            with self.assertRaisesRegex(publisher.PublicationError, "rollback_journal_present"):
                self.publish()
        self.assertEqual(publisher._db_snapshot(self.database, reader), before)
        self.assertFalse(self.index.exists())

    def test_plain_successful_job_uses_its_own_native_source_identity(self):
        self.summary.pop("source_failed_job_id")
        self.summary["minimum_scoring_loop"]["provenance"].pop("pipeline_job_id")
        self._save_summary()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE jobs SET summary_json=? WHERE id=?", (json.dumps(self.summary), JOB))
            connection.commit()
        self.expected["source_job_id"] = JOB
        self.expected["job_summary_sha256"] = reader.canonical_sha(self.summary)
        self.expected["source_summary_sha256"] = publisher._sha(self.output / "summary.json")
        self.publish()
        response = reader.load_foot_crossing_candidates(self.root, publisher._read_job(self.database, JOB))
        self.assertEqual(response["identity"]["source_job_id"], JOB)

    def test_expected_input_or_code_mismatch_stops_before_computation(self):
        for category in ("inputs", "code"):
            original = copy.deepcopy(self.expected)
            if category == "inputs":
                self.expected["inputs"]["frames"]["sha256"] = "0" * 64
            else:
                self.expected["code"][0]["sha256"] = "0" * 64
            with self.subTest(category=category), patch.object(publisher, "_produce", side_effect=AssertionError("compute called")):
                with self.assertRaisesRegex(publisher.PublicationError, "expected_.*_mismatch"):
                    publisher.publish(self.root, self.database, JOB, self.expected)
            self.assertFalse(self.index.exists())
            self.expected = original

    def test_failed_job_cannot_publish(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE jobs SET status='failed'")
            connection.commit()
        with self.assertRaisesRegex(publisher.PublicationError, "job_not_succeeded"):
            self.publish()
        self.assertFalse(self.index.exists())

    def test_summary_database_disagreement_rejected_without_repair(self):
        (self.output / "summary.json").write_bytes(b'{"wrong":true}')
        before = self.history()
        with self.assertRaisesRegex(publisher.PublicationError, "database_and_saved_summary_mismatch"):
            self.publish()
        self.assertEqual(self.history(), before)
        self.assertFalse(self.index.exists())

    def test_original_input_drift_during_compute_cannot_register_index(self):
        def drift(*args):
            self.produce(*args)
            (self.output / "frames.jsonl").write_bytes(b'{"changed_by_fixture":true}\n')
        with self.assertRaisesRegex(publisher.PublicationError, "input_changed_during_publication"):
            self.publish(producer=drift)
        self.assertFalse(self.index.exists())

    def test_candidate_formal_claim_is_rejected(self):
        def bad(*args):
            self.produce(*args)
            path = args[0] / reader.ARTIFACT_NAME
            artifact = json.loads(path.read_bytes())
            artifact["scoring_allowed"] = True
            path.write_bytes(publisher._json_bytes(artifact))
        with self.assertRaisesRegex(reader.CandidateArtifactError, "formal_claim_rejected"):
            self.publish(producer=bad)
        self.assertFalse(self.index.exists())

    def test_producer_overreach_into_historical_scores_cannot_register_index(self):
        def drift(*args):
            self.produce(*args)
            (self.output / "scores.jsonl").write_bytes(b'{"fixture_external_change":true}\n')
        with self.assertRaisesRegex(publisher.PublicationError, "historical_artifacts_changed_during_publication"):
            self.publish(producer=drift)
        self.assertFalse(self.index.exists())

    def test_previous_index_requires_explicit_hash_authorization(self):
        self.publish()
        original = self.index.read_bytes()
        with self.assertRaisesRegex(publisher.PublicationError, "existing_index_not_authorized"):
            self.publish()
        self.assertEqual(self.index.read_bytes(), original)

    def test_failed_atomic_index_replace_preserves_previous_publication(self):
        self.publish()
        previous = self.index.read_bytes()
        self.expected["previous_index_sha256"] = hashlib.sha256(previous).hexdigest()
        real_replace = publisher.os.replace
        def fail_index(source, destination):
            if Path(destination) == self.index:
                raise OSError("fixture index failure")
            return real_replace(source, destination)
        with patch.object(publisher.os, "replace", side_effect=fail_index):
            with self.assertRaisesRegex(OSError, "fixture index failure"):
                self.publish()
        self.assertEqual(self.index.read_bytes(), previous)
        self.assertFalse(any(path.name.startswith(".index-") for path in self.index.parent.iterdir()))
        self.assertEqual(reader.load_foot_crossing_candidates(self.root, publisher._read_job(self.database, JOB))["status"], "available")

    def test_existing_publication_lock_prevents_concurrent_compute(self):
        self.index.parent.mkdir(parents=True)
        lock = self.index.parent / ".publish.lock"
        lock.write_bytes(b"other owner")
        with self.assertRaises(FileExistsError):
            self.publish()
        self.assertEqual(lock.read_bytes(), b"other owner")
        self.assertFalse(self.index.exists())


if __name__ == "__main__":
    unittest.main()
