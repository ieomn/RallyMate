from __future__ import annotations

import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch

from fastapi.testclient import TestClient
from rallymate_service import api, segmented_report as segments
from rallymate_service.report_overview import build_motion_detail, motion_navigation
from rallymate_service.training_evaluation import build_training_evaluation
from tests.test_segmented_report import fixture, prepared_get


class ReportOverviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app, self.settings, self.db, self.job, self.event, self.rows = fixture(Path(temporary.name))
        self.store = segments.SegmentedReportStore()
        self.original = {"source_job_id": self.job["id"], "sha256": "a" * 64,
            "url": f"/v1/jobs/{self.job['id']}/original-video"}

    def test_overview_keeps_legacy_aggregates_and_navigation_without_full_report(self):
        with patch.object(api, "original_video_descriptor", return_value=self.original), \
             patch.object(api, "build_user_demo_result", side_effect=AssertionError("no monolith")), \
             TestClient(self.app) as client:
            response = prepared_get(client, f"/v1/jobs/{self.job['id']}/report-overview")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        result = payload["result"]
        self.assertEqual(payload["source"], segments.source_identity(self.job))
        self.assertEqual(result["training_evaluation"], build_training_evaluation(self.rows))
        self.assertEqual(result["actions"][0]["detected_segments"], 1)
        self.assertEqual(len(payload["assessment"]["techniques"]), 24)
        episode = result["footwork_review"]["episodes"][0]
        self.assertEqual(episode["report_action"]["indicator_count"], 5)
        self.assertEqual(episode["detail_status"], "on_demand")
        self.assertFalse(result["report_delivery"]["is_full_evidence_export"])
        self.assertLess(len(response.content), segments.MAX_OVERVIEW_BYTES)
        self.assertNotIn('"source_window"', response.text)

    def test_scalar_projection_does_not_hide_conflicting_duplicate_records(self):
        first = self.rows[1]
        changed = {**first, "future_field": "changed"}
        compact = [segments._aggregate_observation(item, item) for item in (first, changed)]
        self.assertEqual(build_training_evaluation(compact)["input_validation"]["conflicting_record_count"], 2)

    def test_overview_checks_nested_proof_and_has_bounded_working_set(self):
        with patch.object(segments, "_validate_nested_proofs", side_effect=segments.SegmentedReportError):
            with self.assertRaises(segments.SegmentedReportError):
                self.store.overview(self.job, self.original, lambda: None)
        with patch.object(segments, "MAX_OVERVIEW_WORKING_BYTES", 1):
            with self.assertRaises(segments.SegmentedReportError):
                self.store.overview(self.job, self.original, lambda: None)

    def test_motion_revision_binds_navigation_and_complete_selected_episode(self):
        episode = {"episode_id": "motion-1", "family": "baseline", "method": "rule_based",
            "person_track_id": 99, "start_ms": 300, "peak_ms": 350, "end_ms": 400,
            "contact_confirmed": False, "classification": {"status": "unclassified", "label_zh": "挥拍"},
            "phases": [], "metrics": {"wrist_speed": 1.2}, "future_full_evidence": {"samples": [1, 2, 3]}}
        motion = {"schema_version": "1.1.0", "contact_confirmed": False,
            "families": {"baseline": {"status": "available", "episodes": [episode]}}}
        self.job["summary"]["action_recognition"] = {"motion_analysis": motion}
        source = segments.source_identity(self.job)
        detail = build_motion_detail(self.job, source, "motion-1")
        self.assertEqual(detail["episode"]["future_full_evidence"], episode["future_full_evidence"])
        self.assertEqual(detail["process_evaluation"]["detail_scope"]["episode_id"], "motion-1")
        navigation = motion_navigation(motion)["families"]["baseline"]["episodes"][0]
        self.assertEqual(navigation["metrics"], {})
        self.assertEqual(navigation["detail_status"], "on_demand")
        self.assertNotIn("future_full_evidence", navigation)
        episode["metrics"]["wrist_speed"] = 2
        self.assertNotEqual(segments.source_identity(self.job)["evidence_revision"], source["evidence_revision"])
        with self.assertRaises(segments.SegmentedReportError):
            build_motion_detail(self.job, source, "missing")

    def test_heavy_read_capacity_does_not_block_summary(self):
        entered, release = Event(), Event()
        builds = []
        original = segments.SegmentedReportStore.actions
        def blocked(store, *args, **kwargs):
            builds.append(1)
            entered.set()
            release.wait(timeout=5)
            return original(store, *args, **kwargs)
        with patch.object(api, "original_video_descriptor", return_value=self.original), \
             patch.object(segments.SegmentedReportStore, "actions", blocked), \
             TestClient(self.app) as client, ThreadPoolExecutor(max_workers=2) as executor:
            base = f"/v1/jobs/{self.job['id']}"
            deferred = base + "/report-actions?report_delivery=deferred-v1"
            futures = [executor.submit(client.get, deferred) for _ in range(2)]
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(client.get(base + "/report-summary").status_code, 200)
                self.assertEqual(client.get(deferred).status_code, 202)
                self.assertEqual(builds, [1])
            finally:
                release.set()
            self.assertEqual([future.result().status_code for future in futures], [202, 202])
            self.assertEqual(prepared_get(client, base + "/report-actions").status_code, 200)


if __name__ == "__main__":
    unittest.main()
