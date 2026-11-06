"""HTTP review persistence uses real candidate generation and source binding."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from rallymate_service import api
from rallymate_service.database import JobDatabase
from tests import test_original_video_api as original_video_fixture
from tests.test_shot_statistics import bounce_frames, frame

BODY, JOB = original_video_fixture.BODY, original_video_fixture.JOB


class ShotStatisticsApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings, self.database, self.summary, self.video = original_video_fixture.OriginalVideoApiTests.setup_job(self, self.root)
        self.digest = hashlib.sha256(BODY).hexdigest()
        self.frames_path = self.settings.runs_dir / JOB / "frames.jsonl"
        self.url = f"/v1/jobs/{JOB}/shot-statistics"
        self.headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        self.write_frames()

    def write_frames(self, *, racket=False, calibrated=True):
        rows = bounce_frames(racket=racket, calibrated=calibrated)
        rows += [frame(i, (120, 100), calibrated=calibrated) for i in range(5, 21)]
        for row in rows:
            row["frame"]["video_sha256"] = self.digest
        self.frames_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def client(self):
        return TestClient(api.create_app(self.settings, self.database))

    def submission(self, event, *, revision=0, mutation=None, digest=None):
        return {"mutation_id": mutation or str(uuid4()), "expected_revision": revision,
                "source_video_sha256": digest or self.digest, "event": event}

    @staticmethod
    def event(candidate, **changes):
        result = {key: candidate[key] for key in ("id", "kind", "status", "timestamp_ms", "frame_index",
                                                 "position_image", "position_court") if key in candidate}
        return {**result, **changes}

    @staticmethod
    def manual(identifier="manual-hit", *, kind="hit", timestamp=500, **extra):
        return {"id": identifier, "kind": kind, "status": "confirmed", "timestamp_ms": timestamp, **extra}

    def post(self, client, event, **options):
        return client.post(self.url, headers=self.headers, json=self.submission(event, **options))

    def test_authentication_completed_job_and_source_file_integrity(self):
        with self.client() as client:
            self.assertEqual(client.get(self.url).status_code, 401)
            self.assertEqual(client.post(self.url, json=self.submission(self.manual())).status_code, 401)
            response = client.get(self.url, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertEqual(response.json()["source"]["video_sha256"], self.digest)
            self.assertEqual(client.get(self.url.replace(JOB, str(uuid4())), headers=self.headers).status_code, 404)
            for status in ("queued", "running", "failed", "cancelled"):
                with sqlite3.connect(self.settings.database_path) as connection:
                    connection.execute("UPDATE jobs SET status=? WHERE id=?", (status, JOB))
                self.assertEqual(client.get(self.url, headers=self.headers).status_code, 409, status)
                self.assertEqual(self.post(client, self.manual()).status_code, 409, status)
            with sqlite3.connect(self.settings.database_path) as connection:
                connection.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (JOB,))
            self.video.write_bytes(BODY + b"changed")
            self.assertEqual(client.get(self.url, headers=self.headers).status_code, 409)

    def test_confirm_and_withdraw_candidate_updates_count_and_landing_only_after_review(self):
        before = self.frames_path.read_bytes()
        with self.client() as client:
            result = client.get(self.url, headers=self.headers).json()
            candidate = result["events"][0]
            self.assertEqual(candidate["status"], "candidate")
            self.assertEqual(result["summary"]["confirmed_bounce_count"], 0)
            self.assertIsNone(candidate["position_court"])
            response = self.post(client, self.event(candidate, status="confirmed"))
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["revision"], 1)
            self.assertEqual(result["summary"]["confirmed_bounce_count"], 1)
            self.assertEqual(result["summary"]["mapped_bounce_count"], 1)
            confirmed = result["events"][0]
            self.assertEqual(confirmed["position_court"]["source"], "calibrated_ground_projection")
            # Echoing the displayed automatic location must keep its provenance.
            response = self.post(client, self.event(confirmed, status="candidate"), revision=1)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["revision"], 2)
            self.assertEqual(result["summary"]["confirmed_bounce_count"], 0)
            self.assertEqual(result["summary"]["mapped_bounce_count"], 0)
            self.assertIsNone(result["events"][0]["position_court"])
        self.assertEqual(self.frames_path.read_bytes(), before)
        self.assertEqual(self.database.get_job(JOB)["summary"], self.summary)

    def test_hit_confirmation_dismissal_and_manual_court_positions_inside_and_outside(self):
        self.write_frames(racket=True, calibrated=False)
        with self.client() as client:
            candidate = client.get(self.url, headers=self.headers).json()["events"][0]
            self.assertEqual(candidate["kind"], "hit")
            response = self.post(client, self.event(candidate, status="confirmed"))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["summary"]["confirmed_hit_count"], 1)
            response = self.post(client, self.event(candidate, status="dismissed"), revision=1)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["summary"]["confirmed_hit_count"], 0)
            self.assertEqual(response.json()["summary"]["dismissed_count"], 1)
            events = [self.manual("manual-hit", timestamp=350),
                      self.manual("manual-in", kind="bounce", timestamp=500,
                                  position_court={"x": .3, "y": .6, "source": "manual"}),
                      self.manual("manual-out", kind="bounce", timestamp=750,
                                  position_court={"x": 1.1, "y": -.1, "source": "manual"})]
            for revision, event in enumerate(events, start=2):
                response = self.post(client, event, revision=revision)
                self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["summary"]["confirmed_hit_count"], 1)
            self.assertEqual(result["summary"]["confirmed_bounce_count"], 2)
            self.assertEqual(result["summary"]["mapped_bounce_count"], 2)
            outside = next(event for event in result["events"] if event["id"] == "manual-out")
            self.assertEqual(outside["position_court"], {"x": 1.1, "y": -.1, "source": "manual"})
            self.assertIsNone(outside["frame_index"])

    def test_duplicate_submission_is_idempotent_and_changed_payload_is_rejected(self):
        mutation = str(uuid4())
        event = self.manual()
        with self.client() as client:
            first = self.post(client, event, mutation=mutation)
            self.assertEqual(first.status_code, 200, first.text)
            repeated = self.post(client, event, mutation=mutation)
            self.assertEqual(repeated.status_code, 200, repeated.text)
            self.assertEqual(repeated.json(), first.json())
            changed = self.post(client, {**event, "note": "different content"}, mutation=mutation)
            self.assertEqual(changed.status_code, 409, changed.text)
            # A new mutation with an old revision cannot overwrite saved work.
            self.assertEqual(self.post(client, self.manual("manual-other", timestamp=800)).status_code, 409)
            current = client.get(self.url, headers=self.headers).json()
            self.assertEqual(current["revision"], 1)
            self.assertEqual(current["summary"]["confirmed_hit_count"], 1)

    def test_old_idempotent_request_does_not_revalidate_or_restore_withdrawn_event(self):
        mutation = str(uuid4())
        original = self.manual("manual-a", timestamp=500)
        with self.client() as client:
            self.assertEqual(self.post(client, original, mutation=mutation).status_code, 200)
            self.assertEqual(self.post(client, {**original, "status": "dismissed"}, revision=1).status_code, 200)
            current = self.post(client, self.manual("manual-b", timestamp=500), revision=2)
            self.assertEqual(current.status_code, 200, current.text)
            repeated = self.post(client, original, mutation=mutation)
            self.assertEqual(repeated.status_code, 200, repeated.text)
            self.assertEqual(repeated.json(), current.json())
            self.assertEqual(repeated.json()["revision"], 3)
            self.assertEqual(repeated.json()["summary"]["confirmed_hit_count"], 1)

    def test_concurrent_apps_with_same_revision_commit_only_one_and_restart_reads_saved_data(self):
        barrier = Barrier(2)
        with self.client() as first, self.client() as second:
            def save(client, identifier, timestamp):
                barrier.wait(timeout=5)
                return self.post(client, self.manual(identifier, timestamp=timestamp))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(save, first, "manual-a", 400),
                           pool.submit(save, second, "manual-b", 800)]
                responses = [future.result(timeout=10) for future in futures]
            self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        # New app and JobDatabase instances share only the durable SQLite state.
        with TestClient(api.create_app(self.settings, JobDatabase(self.settings.database_path))) as fresh:
            result = fresh.get(self.url, headers=self.headers).json()
            self.assertEqual(result["revision"], 1)
            self.assertEqual(result["summary"]["confirmed_hit_count"], 1)
            self.assertEqual(sum(e["id"].startswith("manual-") for e in result["events"]), 1)

    def test_saved_confirmation_survives_candidate_changes_without_reusing_old_projection(self):
        with self.client() as client:
            candidate = client.get(self.url, headers=self.headers).json()["events"][0]
            saved = self.post(client, self.event(candidate, status="confirmed"))
            self.assertEqual(saved.status_code, 200, saved.text)
            rows = [json.loads(line) for line in self.frames_path.read_text().splitlines()]
            for row in rows:
                row["detections"] = []
            self.frames_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            response = client.get(self.url, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            historical = next(e for e in response.json()["events"] if e["id"] == candidate["id"])
            self.assertIn("historical_candidate_source_changed", historical["reason_codes"])
            self.assertEqual(historical["status"], "confirmed")
            self.assertIsNone(historical["position_court"])
            self.assertEqual(historical["timestamp_ms"], candidate["timestamp_ms"])
            response = self.post(client, self.event(historical, status="candidate"), revision=1)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["summary"]["confirmed_bounce_count"], 0)
            forged = self.event(historical, id="shot-" + "f" * 20, status="confirmed")
            self.assertEqual(self.post(client, forged, revision=2).status_code, 422)

    def test_video_digest_mismatch_cannot_save_or_reuse_reviews_on_changed_source(self):
        with self.client() as client:
            response = self.post(client, self.manual(), digest="b" * 64)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(client.get(self.url, headers=self.headers).json()["revision"], 0)
            self.assertEqual(self.post(client, self.manual()).status_code, 200)
            rows = [json.loads(line) for line in self.frames_path.read_text().splitlines()]
            for row in rows:
                row["frame"]["video_sha256"] = "b" * 64
            self.frames_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            self.assertEqual(client.get(self.url, headers=self.headers).status_code, 409)

    def test_automatic_timestamp_kind_frame_and_image_position_cannot_be_rewritten(self):
        with self.client() as client:
            candidate = client.get(self.url, headers=self.headers).json()["events"][0]
            event = self.event(candidate, status="confirmed")
            for change in ({"timestamp_ms": candidate["timestamp_ms"] + 1}, {"kind": "hit"},
                           {"frame_index": 19}, {"position_image": {"x": .8, "y": .8}},
                           {"position_court": {"x": .2, "y": .2, "source": "calibrated_ground_projection"}}):
                with self.subTest(change=change):
                    response = self.post(client, {**event, **change})
                    self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(client.get(self.url, headers=self.headers).json()["revision"], 0)

    def test_request_size_strict_json_manual_bounds_and_annotation_limit(self):
        with self.client() as client:
            response = self.post(client, self.manual(note="x" * 9000))
            self.assertEqual(response.status_code, 413, response.text)
            duplicate_json = json.dumps(self.submission(self.manual())).replace('"expected_revision": 0',
                '"expected_revision": 0, "expected_revision": 1')
            response = client.post(self.url, headers={**self.headers, "Content-Type": "application/json"}, content=duplicate_json)
            self.assertEqual(response.status_code, 422, response.text)
            for change in ({"timestamp_ms": 1001}, {"frame_index": 4},
                           {"position_court": {"x": .5, "y": .5}}, {"extra": "forbidden"}):
                response = self.post(client, {**self.manual(), **change})
                self.assertEqual(response.status_code, 422, response.text)
            for position in ({"x": -1., "y": .5}, {"x": .5, "y": 2.}):
                response = self.post(client, self.manual(kind="bounce", position_court=position))
                self.assertEqual(response.status_code, 422, response.text)
            with patch("rallymate_service.shot_review_store.MAX_REVIEWS", 1):
                self.assertEqual(self.post(client, self.manual()).status_code, 200)
                response = self.post(client, self.manual("manual-second", timestamp=800), revision=1)
                self.assertEqual(response.status_code, 409, response.text)
                # Existing annotation can still be withdrawn at the capacity limit.
                response = self.post(client, self.manual(status="dismissed"), revision=1)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["summary"]["confirmed_hit_count"], 0)


if __name__ == "__main__":
    unittest.main()
