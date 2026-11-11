from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from rallymate_scoring.artifact_evidence import EvidencePoolWriter
from rallymate_service import api, segmented_report as segments
from rallymate_service.report_evidence_transport import unpack_report_evidence
from tests.test_model_measurement_contract import model_window_fixture
from tests import test_trajectory_api


def prepared_get(client, url):
    url += ("&" if "?" in url else "?") + "report_delivery=deferred-v1"
    deadline = time.monotonic() + 8
    while True:
        response = client.get(url)
        if response.status_code != 202 or time.monotonic() >= deadline:
            return response
        time.sleep(.02)


def fixture(root):
    app, settings, db = test_trajectory_api.TrajectoryApiTests()._app(root)
    job_id = test_trajectory_api.TrajectoryApiTests()._succeeded_job(settings, db, running=True)
    path = settings.runs_dir / job_id
    proof = model_window_fixture()
    # Preserve logical primary identity separately from the native track.
    event = {**copy.deepcopy(proof), "event_id": "event-1", "video_id": job_id,
        "event_code": "FS01", "person_track_id": 99, "start_ms": 300, "end_ms": 400,
        "provenance": {"source_id": job_id}}
    rows = [{**copy.deepcopy(proof), "event_id": "event-1", "event_code": "FS01",
        "person_track_id": 99, "video_id": job_id, "indicator_id": f"FS01-M0{i}",
        "feature_status": "measured", "scoring_status": "calibration_required",
        "features": [{"name": "value", "value": i}],
        "provenance": {"pipeline_job_id": job_id, "video_sha256": "a" * 64}} for i in range(1, 6)]
    with EvidencePoolWriter(path, video_id=job_id, source_id=job_id, video_sha256="a" * 64) as writer:
        writer.write_jsonl(path / "events.jsonl", [event])
        writer.write_jsonl(path / "indicator-features.jsonl", rows)
    frames = []
    for i, binding in enumerate(proof["source_window"]["source_bindings"]):
        frames.append({"job_id": job_id, "frame": {"index": binding["frame_index"],
            "processed_index": binding["processed_index"], "timestamp_ms": proof["source_window"]["video_timestamps_ms"][i],
            "source_timestamp_ms": binding["timestamp_ms"], "pts": binding["pts"],
            "time_base_num": binding["time_base_num"], "time_base_den": binding["time_base_den"],
            "video_sha256": "a" * 64}, "poses": [{"person_track_id": 1, "source_binding": copy.deepcopy(binding)}]})
    (path / "frames.jsonl").write_text("".join(json.dumps(row) + "\n" for row in frames), encoding="utf-8")
    summary = {"job_id": job_id, "status": "completed", "minimum_scoring_loop": {
        "video_id": job_id, "provenance": {"pipeline_job_id": job_id, "video_sha256": "a" * 64,
            "frames_sha256": hashlib.sha256((path / "frames.jsonl").read_bytes()).hexdigest()},
        "artifact_sha256": {name.replace("-", "_").replace(".", "_"):
            hashlib.sha256((path / name).read_bytes()).hexdigest() for name in ("events.jsonl", "indicator-features.jsonl")},
        "evidence_storage": writer.manifest,
        "event_counts": {"FS01": 1}, "score_status_counts": {"calibration_required": 5},
        "result_state": {"measurement_status": "measured", "scoring_status": "calibration_required", "grade": None}},
        "input": {"video": {"duration_ms": 400}}, "processing": {"processed_frames": 4}}
    db.mark_succeeded(job_id, summary)
    return app, settings, db, db.get_job(job_id), event, rows


class SegmentedReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app, self.settings, self.db, self.job, self.event, self.rows = fixture(Path(temporary.name))
        self.store = segments.SegmentedReportStore()
        self.check = lambda: None

    def test_summary_does_not_build_or_read_complete_report(self):
        original = {"source_job_id": self.job["id"], "sha256": "a" * 64,
                    "url": f"/v1/jobs/{self.job['id']}/original-video"}
        with patch.object(api, "original_video_descriptor", return_value=original), \
             patch.object(api, "build_user_demo_result", side_effect=AssertionError("full report must stay lazy")), \
             patch.object(segments.SegmentedReportStore, "index", side_effect=AssertionError("no index on summary")), \
             TestClient(self.app) as client:
            response = client.get(f"/v1/jobs/{self.job['id']}/report-summary")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["delivery"], {"summary": "complete", "actions": "not_loaded", "evidence": "on_demand", "mesh": "independent"})
        self.assertEqual(result["scoring"]["status"], "calibration_required")
        self.assertLess(len(response.content), segments.MAX_SUMMARY_BYTES)

    def test_full_proof_roundtrip_pagination_and_native_identity(self):
        actions = self.store.actions(self.job, 0, 20, self.check)
        self.assertEqual(actions["actions"][0]["person_track_id"], 99)
        self.assertEqual(actions["actions"][0]["source_track_id"], 1)
        page1 = unpack_report_evidence(self.store.detail(self.job, "event-1", 0, 4, self.check))
        page2 = unpack_report_evidence(self.store.detail(self.job, "event-1", 4, 4, self.check))
        self.assertEqual(page1["status"], "partial")
        self.assertEqual(page1["pagination"]["next_offset"], 4)
        self.assertEqual(page2["status"], "complete")
        self.assertIsNone(page2["pagination"]["next_offset"])
        self.assertEqual(page1["event"], self.event)
        self.assertEqual(page1["records"] + page2["records"], self.rows)

    def test_changed_content_with_restored_size_and_mtime_invalidates_index(self):
        self.store.actions(self.job, 0, 20, self.check)
        path = Path(self.job["output_dir"]) / "indicator-features.jsonl"
        original = path.stat()
        content = path.read_bytes().replace(b'"value":1', b'"value":9', 1)
        path.write_bytes(content)
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaises(segments.SegmentedReportError):
            self.store.detail(self.job, "event-1", 0, 4, self.check)

    def test_frames_are_independent_pts_and_native_subject_oracles(self):
        path = Path(self.job["output_dir"]) / "frames.jsonl"
        original = path.read_text("utf-8")
        for old, new in [('"pts": 75300', '"pts": 75301'), ('"person_track_id": 1', '"person_track_id": 2'),
                         ('"selection_epoch": 2', '"selection_epoch": 3')]:
            with self.subTest(change=new):
                path.write_text(original.replace(old, new, 1), encoding="utf-8")
                # Even updating the declared artifact digest cannot repair a
                # source-window clock or native-track mismatch.
                self.job["summary"]["minimum_scoring_loop"]["provenance"]["frames_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                with self.assertRaises(segments.SegmentedReportError):
                    self.store.detail(self.job, "event-1", 0, 4, self.check)

    def test_cancelled_reads_do_not_publish_partial_index_and_can_retry(self):
        calls = []
        def cancel():
            calls.append(1)
            if len(calls) == 4:
                raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.store.actions(self.job, 0, 20, cancel)
        self.assertFalse(self.store._indexes)
        self.assertEqual(self.store.actions(self.job, 0, 20, self.check)["pagination"]["total"], 1)

    def test_foreign_event_binding_and_unknown_nested_proof_fail_closed(self):
        with self.assertRaises(segments.SegmentedReportError):
            segments._bound_row({**self.rows[0], "event_id": "other"}, segments.source_identity(self.job), self.event)
        bad = copy.deepcopy(self.rows[0])
        bad["unknown_future_field"] = model_window_fixture()
        bad["unknown_future_field"]["source_window"]["source_bindings"][0]["pts"] += 1
        with self.assertRaises(segments.SegmentedReportError):
            segments._validate_nested_proofs(bad, segments.source_identity(self.job), self.event["source_window"], self.check)

    def test_api_200_limits_and_cancelled_cache_hits(self):
        with TestClient(self.app) as client:
            base = f"/v1/jobs/{self.job['id']}"
            with patch.object(self.db, "get_job", side_effect=AssertionError("poll must not load summary")):
                status = client.get(base + "/status")
            self.assertEqual(status.status_code, 200)
            self.assertNotIn("summary", status.json())
            self.assertEqual(status.json()["id"], self.job["id"])
            self.assertEqual(client.get(base + "/report-actions?limit=0").status_code, 422)
            self.assertEqual(client.get(base + "/report-actions?limit=51").status_code, 422)
            self.assertEqual(prepared_get(client, base + "/report-actions").status_code, 200)
            result = prepared_get(client, base + "/report-actions/event-1?limit=4")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(len(unpack_report_evidence(result.json())["records"]), 4)
            self.assertEqual(prepared_get(client, base + "/report-actions/missing").status_code, 404)
            with self.db.connect() as connection:
                connection.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (self.job["id"],))
                connection.commit()
            self.assertEqual(client.get(base + "/report-actions").status_code, 409)

    def test_bounded_index_rejects_without_truncating(self):
        with patch.object(segments, "MAX_INDEX_BYTES", 1):
            with self.assertRaises(segments.SegmentedReportError):
                self.store.actions(self.job, 0, 20, self.check)
        self.assertFalse(self.store._indexes)

    def test_same_status_summary_change_during_delivery_discards_bytes(self):
        original = segments.SegmentedReportStore.actions
        def changed(store, *args):
            result = original(store, *args)
            with self.db.connect() as connection:
                connection.execute("UPDATE jobs SET summary_json=summary_json || ' ' WHERE id=?", (self.job["id"],))
                connection.commit()
            return result
        with patch.object(segments.SegmentedReportStore, "actions", changed), TestClient(self.app) as client:
            response = client.get(f"/v1/jobs/{self.job['id']}/report-actions?report_delivery=deferred-v1")
            self.assertEqual(response.status_code, 202)
            cache = self.app.state.segmented_delivery.cache
            deadline = time.monotonic() + 5
            while cache.stats()["pending"] and time.monotonic() < deadline:
                time.sleep(.02)
            results = list(cache._completed.values())
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].error.code, "report_source_changed")
            self.assertIsNone(results[0].body)

    def test_evidence_revision_changes_even_when_video_and_frames_are_unchanged(self):
        first = segments.source_identity(self.job)
        changed = copy.deepcopy(self.job)
        changed["summary"]["minimum_scoring_loop"]["artifact_sha256"]["events_jsonl"] = "b" * 64
        second = segments.source_identity(changed)
        self.assertEqual(first["video_sha256"], second["video_sha256"])
        self.assertEqual(first["frames_sha256"], second["frames_sha256"])
        self.assertNotEqual(first["evidence_revision"], second["evidence_revision"])


if __name__ == "__main__":
    unittest.main()
