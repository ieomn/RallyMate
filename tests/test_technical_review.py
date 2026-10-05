from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from rallymate_service.api import create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.technical_review import (
    TechnicalReviewError, TechnicalReviewStore, parse_review_input,
)


class TechnicalReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input.mp4"
        self.source.write_bytes(b"fixture source video identity; no GPU processing")
        self.output = self.root / "run"
        self.output.mkdir()
        self.primary = self.output / "primary-player.jsonl"
        self.primary.write_text("\n".join(json.dumps({"primary_player_id": 1, "timestamp_ms": t})
                                         for t in [0, 1000, 1960]), encoding="utf-8")
        self.events = self.output / "events.jsonl"
        self.events.write_text(json.dumps({"event_id": "fs01-real", "event_code": "FS01", "person_track_id": 1,
                                           "start_ms": 0, "end_ms": 1500}) + "\n", encoding="utf-8")
        self.job = {"id": str(uuid.uuid4()), "status": "succeeded", "video_path": str(self.source),
                    "output_dir": str(self.output), "summary": {
                        "input": {"video": {"duration_ms": 2000, "fps": 25}},
                        "processing": {"source_start_frame": 0, "source_end_frame_exclusive": 50}}}
        self.store = TechnicalReviewStore(self.root / "reviews.sqlite3")
        self.store.initialize()
        self.initial = self.store.get(self.job)

    def submission(self, **changes):
        result = {"mutation_id": str(uuid.uuid4()), "expected_revision": 0,
                  "source_sha256": self.initial["source_sha256"],
                  "source_reference_version": self.initial["source_reference_version"],
                  "source_reference_sha256": self.initial["source_reference_sha256"],
                  "artifact_context_sha256": self.initial["artifact_context_sha256"],
                  "indicator_id": "FS01-M02", "player_id": 1, "event_id": "fs01-real",
                  "event_source": "system_event", "start_ms": 100, "end_ms": 1000,
                  "reviewer_id": "coach-a", "reviewer_name": "甲教练", "reviewer_role": "coach",
                  "observability": "observable", "status": "graded", "grade": "B",
                  "reason_zh": "该时段可见屈膝与髋部下降；结合原文人工判断。", "next_step_zh": "下一次练习继续观察动作连续性。"}
        result.update(changes)
        return result

    def save(self, payload):
        return self.store.save(self.job, parse_review_input(json.dumps(payload, ensure_ascii=False).encode()))

    def rejects(self, payload, code):
        with self.assertRaises(TechnicalReviewError) as raised:
            self.save(payload)
        self.assertEqual(raised.exception.code, code)

    def test_catalog_and_initial_get_are_source_bound_without_fabricated_reviews(self):
        self.assertEqual(len(self.initial["rules"]), 298)
        self.assertEqual(len(self.initial["visual_rules"]), 246)
        self.assertEqual(sum(not item["manual_grading_allowed"] for item in self.initial["rules"]), 100)
        self.assertEqual(self.initial["entries"], [])
        self.assertEqual(self.initial["revision"], 0)
        self.assertEqual(self.initial["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertNotIn(str(self.root), json.dumps(self.initial))

    def test_save_restart_revision_history_and_exact_retry(self):
        payload = self.submission()
        saved = self.save(payload)
        entry = saved["entries"][0]
        self.assertEqual((saved["revision"], entry["entry_revision"], entry["grade"]), (1, 1, "B"))
        self.assertIsNone(entry["formal_grade"])
        self.assertIsNone(entry["formal_score"])
        self.assertIsNone(entry["automatic_joint_gate_passed"])
        self.assertEqual(entry["observability_source"], "manual_visual_review")
        self.assertEqual(entry["identity_verification"], "self_reported_local")
        self.assertFalse(entry["calibration_eligible"])
        self.assertIn("independent_adjudication", entry["calibration_status"])
        self.assertEqual(entry["selected_grade_definition"], self.store.catalog().rules["FS01-M02"]["grades"]["B"])
        self.assertEqual(self.save(payload)["revision"], 1)
        updated = self.save(self.submission(expected_revision=1, review_id=entry["review_id"], grade="A", reason_zh="再次逐帧检查后修订，原记录保留。"))
        self.assertEqual(updated["entries"][0]["entry_revision"], 2)
        self.store = TechnicalReviewStore(self.root / "reviews.sqlite3")
        exported = self.store.get(self.job, export=True)
        self.assertEqual([record["grade"] for record in exported["history"]], ["B", "A"])
        self.assertEqual(exported["history"][1]["previous_record_sha256"], exported["history"][0]["record_sha256"])
        self.assertEqual(len(exported["coach_label_candidates"]), 1)
        candidate = exported["coach_label_candidates"][0]
        self.assertEqual(candidate["coach_label"]["grade"], "A")
        self.assertFalse(candidate["accepted_manual_event"])
        self.assertFalse(candidate["human_truth_ready"])

    def test_two_reviewers_remain_independent_without_majority_resolution(self):
        self.save(self.submission())
        result = self.save(self.submission(expected_revision=1, reviewer_id="coach-b", reviewer_name="乙教练", grade="D"))
        self.assertEqual([entry["grade"] for entry in result["entries"]], ["B", "D"])
        self.assertIsNone(result["formal_grade"])
        self.rejects(self.submission(expected_revision=2), "review_already_exists")

    def test_concurrent_writes_have_one_winner_and_no_lost_history(self):
        def attempt(payload):
            try:
                return self.save(payload)["revision"]
            except TechnicalReviewError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [self.submission(), self.submission(reviewer_id="b")]))
        self.assertCountEqual(results, [1, "revision_conflict"])
        self.assertEqual(len(self.store.get(self.job, export=True)["history"]), 1)

    def test_mutation_id_is_idempotent_only_for_identical_content(self):
        payload = self.submission()
        self.save(payload)
        self.rejects({**payload, "grade": "A"}, "mutation_conflict")
        self.rejects(self.submission(reviewer_id="b"), "revision_conflict")

    def test_revisions_cannot_change_person_source_window_or_author(self):
        entry = self.save(self.submission())["entries"][0]
        for field, value in [("reviewer_id", "other"), ("reviewer_role", "reviewer"),
                             ("reviewer_name", "他人"), ("start_ms", 200), ("indicator_id", "FS01-M03")]:
            with self.subTest(field=field):
                self.rejects(self.submission(expected_revision=1, review_id=entry["review_id"], **{field: value}), "immutable_binding")

    def test_all_source_blockers_disallow_grade_but_keep_unassessable_reason(self):
        blocked = [rule for rule in self.initial["rules"] if not rule["manual_grading_allowed"]]
        for rule in blocked:
            with self.subTest(indicator=rule["indicator_id"]):
                self.rejects(self.submission(indicator_id=rule["indicator_id"], event_source="manual_interval", event_id=None), "source_rule_blocked")
        record = self.save(self.submission(indicator_id=blocked[0]["indicator_id"], event_source="manual_interval", event_id=None,
                            grade=None, status="unassessable", reason_zh="原文定义存在异常，待规则负责人核对。"))["entries"][0]
        self.assertTrue(record["source_blockers"])
        self.assertIsNone(record["grade"])
        self.assertEqual(self.store.get(self.job, export=True)["coach_label_candidates"], [])

    def test_no_unevaluable_or_partial_view_becomes_an_e_grade(self):
        self.rejects(self.submission(observability="partial", grade="E"), "grade_without_observation")
        self.rejects(self.submission(observability="unobservable", grade="E"), "grade_without_observation")
        self.rejects(self.submission(status="unassessable", grade="E"), "unassessable_has_grade")
        self.rejects(self.submission(grade=None), "grade_without_observation")
        result = self.save(self.submission(observability="unobservable", status="unassessable", grade=None,
                                          reason_zh="脚部完全出画，不能判断该行为。"))
        self.assertIsNone(result["entries"][0]["grade"])

    def test_interval_player_and_event_bindings_are_enforced(self):
        cases = [({"start_ms": 1000, "end_ms": 1000}, "invalid_interval"),
                 ({"end_ms": 2001}, "invalid_interval"), ({"player_id": 2}, "unknown_player"),
                 ({"event_id": "invented"}, "event_binding_mismatch"),
                 ({"indicator_id": "FS02-M02"}, "event_binding_mismatch"),
                 ({"end_ms": 1600}, "event_binding_mismatch"),
                 ({"event_source": "manual_interval"}, "manual_interval_has_event"),
                 ({"indicator_id": "FAKE"}, "unknown_indicator")]
        for changes, code in cases:
            with self.subTest(changes=changes):
                self.rejects(self.submission(**changes), code)
        result = self.save(self.submission(event_source="manual_interval", event_id=None, indicator_id="GS01-M01-01"))
        self.assertEqual(result["entries"][0]["event_source"], "manual_interval")
        candidate = self.store.get(self.job, export=True)["coach_label_candidates"][0]
        self.assertTrue(candidate["coach_label"]["event_id"].startswith("manual-interval-"))

    def test_source_and_reference_identity_are_not_trusted_from_client(self):
        self.rejects(self.submission(source_sha256="0" * 64), "source_changed")
        self.rejects(self.submission(source_reference_sha256="0" * 64), "source_reference_changed")
        self.rejects(self.submission(source_reference_version="invented"), "source_reference_changed")
        self.save(self.submission())
        self.source.write_bytes(b"different source bytes with same job ID")
        refreshed = self.store.get(self.job, export=True)
        self.assertFalse(refreshed["entries"][0]["source_binding_current"])
        self.assertEqual(refreshed["coach_label_candidates"], [])

    def test_strict_input_rejects_unknown_fields_coercion_duplicates_and_nonfinite(self):
        for changes in [{"fake": 1}, {"start_ms": True}, {"start_ms": "100"}, {"player_id": False},
                        {"grade": 100}, {"reason_zh": " "}, {"reviewer_id": "\n"}, {"expected_revision": -1}]:
            with self.subTest(changes=changes):
                self.rejects(self.submission(**changes), "invalid_review")
        for body in [b'{"grade":"A","grade":"E"}', b'{"start_ms":NaN}', b'[]', b'\xff']:
            with self.subTest(body=body), self.assertRaises(TechnicalReviewError):
                parse_review_input(body)
        with self.assertRaises(TechnicalReviewError) as raised:
            parse_review_input(b" " * 32769)
        self.assertEqual(raised.exception.status, 413)

    def test_compatible_artifact_change_after_get_requires_explicit_reload(self):
        self.save(self.submission())
        # Identity/observability metadata changes without changing event IDs or
        # the set of allowable windows must invalidate the old browser view.
        self.primary.write_text(self.primary.read_text(encoding="utf-8").replace('"timestamp_ms": 1000', '"identity_ambiguous": true, "timestamp_ms": 1000'), encoding="utf-8")
        self.rejects(self.submission(expected_revision=1, reviewer_id="b"), "artifact_context_changed")
        current = self.store.get(self.job, export=True)
        self.assertFalse(current["entries"][0]["source_binding_current"])
        self.assertEqual(current["coach_label_candidates"], [])
        self.assertNotEqual(current["artifact_context_sha256"], self.initial["artifact_context_sha256"])

    def test_visual_rules_record_observation_without_invented_grade_or_deduction(self):
        visual = next(item for item in self.initial["visual_rules"] if item["optional_in_source"])
        payload = self.submission(target_kind="visual_rule", indicator_id=None, visual_rule_id=visual["visual_rule_id"],
                                  event_source="manual_interval", event_id=None, grade=None, status="not_observed")
        self.rejects({**payload, "grade": "E"}, "invalid_visual_review")
        self.rejects({**payload, "status": "graded"}, "invalid_visual_review")
        self.rejects({**payload, "observability": "partial"}, "visual_without_observation")
        record = self.save(payload)["entries"][0]
        self.assertTrue(record["optional_in_source"])
        self.assertEqual(record["source_text"], visual["source_text"])
        self.assertIsNone(record["formal_grade"])
        self.assertIsNone(record["formal_score"])
        self.assertIsNone(record["grade"])
        self.assertEqual(self.store.get(self.job, export=True)["coach_label_candidates"], [])

    def test_missing_malformed_or_duplicate_artifacts_fail_closed(self):
        self.events.write_text('{"event_id":"duplicate","event_id":"other"}', encoding="utf-8")
        with self.assertRaises(TechnicalReviewError):
            self.store.get(self.job)
        self.events.unlink()
        with self.assertRaises(TechnicalReviewError):
            self.store.get(self.job)

    def test_unfinished_jobs_and_incomplete_video_metadata_fail_closed(self):
        for changed in [{**self.job, "status": "running"}, {**self.job, "summary": {}}]:
            with self.assertRaises(TechnicalReviewError):
                self.store.get(changed)

    def test_history_cannot_be_updated_or_deleted(self):
        self.save(self.submission())
        with sqlite3.connect(str(self.store.path)) as conn:
            for query in ["DELETE FROM technical_review_revisions", "UPDATE technical_review_revisions SET record_json='{}'"]:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(query)

    def test_http_endpoints_auth_validation_and_export_use_same_persistent_contract(self):
        detect, pose = self.root / "detect.pt", self.root / "pose.pt"
        detect.write_bytes(b"fixture")
        pose.write_bytes(b"fixture")
        settings = ServiceSettings(data_root=self.root / "service", database_path=self.root / "jobs.sqlite3",
                                   detect_model=detect, pose_model=pose, api_key="unit-test-secret-not-a-real-key")
        database = JobDatabase(settings.database_path)
        database.initialize()
        database.create_job(self.job["id"], "test.mp4", self.source, self.root / "request.json", self.output)
        database.claim_next("test-fixture", lease_seconds=60, max_attempts=1)
        database.mark_succeeded(self.job["id"], self.job["summary"])
        app = create_app(settings, database)
        route = f"/v1/jobs/{self.job['id']}/technical-review"
        headers = {"Authorization": "Bearer unit-test-secret-not-a-real-key"}
        with TestClient(app) as client:
            self.assertEqual(client.get(route).status_code, 401)
            initial = client.get(route, headers=headers)
            self.assertEqual(initial.status_code, 200, initial.text[:500])
            self.assertEqual(initial.headers["cache-control"], "no-store")
            self.assertEqual(initial.json()["entries"], [])
            payload = self.submission()
            saved = client.post(route, headers=headers, json=payload)
            self.assertEqual(saved.status_code, 200, saved.text[:500])
            self.assertEqual(client.post(route, headers=headers, json=payload).json()["revision"], 1)
            invalid = client.post(route, headers=headers, json={**payload, "start_ms": True})
            self.assertEqual(invalid.status_code, 422)
            self.assertNotIn(settings.api_key, invalid.text)
            exported = client.get(route + "/export", headers=headers)
            self.assertEqual(exported.status_code, 200)
            self.assertIn("attachment", exported.headers["content-disposition"])
            self.assertEqual(len(exported.json()["history"]), 1)
            self.assertEqual(client.get("/v1/jobs/absent/technical-review", headers=headers).status_code, 404)


if __name__ == "__main__":
    unittest.main()
