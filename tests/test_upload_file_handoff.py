from __future__ import annotations

import hashlib
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

import cv2
import numpy as np
from fastapi import HTTPException, UploadFile, Request, Response
from fastapi.testclient import TestClient

from rallymate_service.api import _owned_upload_source, _rename_submission_file, _sync_submission_directory, create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase, QueueCapacityError
from rallymate_service.uploads import UploadStore
from rallymate_vision.quality import probe_video


class OwnedUploadPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "uploads"
        self.upload_id = str(uuid.uuid4())
        self.source = self.root / ".chunks" / self.upload_id / "assembled.video"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"verified bytes")

    def validate(self, path=None, size=None, limit=100):
        return _owned_upload_source(path or self.source, self.root, self.upload_id,
                                    len(b"verified bytes") if size is None else size, limit)

    def test_only_current_owned_regular_file_is_accepted(self):
        source, metadata = self.validate()
        self.assertEqual(source, self.source.resolve())
        self.assertEqual(metadata.st_size, len(b"verified bytes"))
        other = Path(self.temporary.name) / "user-original.mp4"
        other.write_bytes(b"verified bytes")
        for candidate in (other, self.source.relative_to(self.root), self.source.parent):
            with self.subTest(candidate=candidate), self.assertRaises(HTTPException) as failed:
                self.validate(candidate)
            self.assertEqual(failed.exception.status_code, 409)
        self.assertEqual(other.read_bytes(), b"verified bytes")
        self.assertEqual(self.source.read_bytes(), b"verified bytes")

    def test_length_limit_and_empty_are_rechecked(self):
        for size, limit, code in ((True, 100, 422), (5, 100, 422), (14, 5, 413)):
            with self.subTest(size=size, limit=limit), self.assertRaises(HTTPException) as failed:
                self.validate(size=size, limit=limit)
            self.assertEqual(failed.exception.status_code, code)
        self.source.write_bytes(b"")
        with self.assertRaises(HTTPException) as failed:
            self.validate(size=0)
        self.assertEqual(failed.exception.status_code, 422)

    def test_hardlinked_source_is_not_exclusively_owned(self):
        linked = Path(self.temporary.name) / "outside-original.mp4"
        try:
            linked.hardlink_to(self.source)
        except OSError as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        with self.assertRaises(HTTPException) as failed:
            self.validate()
        self.assertEqual(failed.exception.status_code, 409)
        self.assertEqual(linked.read_bytes(), b"verified bytes")

    def test_symlink_source_cannot_move_external_file(self):
        other = Path(self.temporary.name) / "outside.mp4"
        other.write_bytes(b"verified bytes")
        self.source.unlink()
        try:
            self.source.symlink_to(other)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(HTTPException) as failed:
            self.validate()
        self.assertEqual(failed.exception.status_code, 409)
        self.assertEqual(other.read_bytes(), b"verified bytes")

    def test_cross_filesystem_handoff_is_refused_without_copying(self):
        original = Path.stat
        root = self.root.resolve()
        def different_destination(path, *args, **kwargs):
            metadata = original(path, *args, **kwargs)
            if path == root:
                return SimpleNamespace(st_dev=metadata.st_dev + 1)
            return metadata
        with patch.object(Path, "stat", different_destination), self.assertRaises(HTTPException) as failed:
            self.validate()
        self.assertEqual(failed.exception.status_code, 409)
        self.assertEqual(self.source.read_bytes(), b"verified bytes")

    def test_publication_is_atomic_and_never_replaces_an_existing_name(self):
        destination = self.root / "already-owned.mp4"
        destination.write_bytes(b"original durable input")
        with self.assertRaises(FileExistsError):
            _rename_submission_file(self.source, destination)
        self.assertEqual(destination.read_bytes(), b"original durable input")
        self.assertEqual(self.source.read_bytes(), b"verified bytes")
        destination.unlink()
        source_identity = self.source.stat().st_ino
        _rename_submission_file(self.source, destination)
        self.assertFalse(self.source.exists())
        self.assertEqual(destination.stat().st_ino, source_identity)


class UploadFileHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        detect, pose = root / "detect.pt", root / "pose.pt"
        detect.touch()
        pose.touch()
        self.settings = ServiceSettings(
            data_root=root / "service", database_path=root / "service/jobs.sqlite3",
            detect_model=detect, pose_model=pose, api_key="test-key",
            max_upload_bytes=4 * 1024 * 1024, max_video_duration_seconds=10,
        )
        self.settings.ensure_directories()
        self.database = JobDatabase(self.settings.database_path)
        source = root / "camera.mp4"
        writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
        self.assertTrue(writer.isOpened())
        for value in (0, 60, 120):
            writer.write(np.full((120, 160, 3), value, dtype=np.uint8))
        writer.release()
        self.content = source.read_bytes()
        self.upload_id = str(uuid.uuid4())
        self.store = UploadStore(self.settings.uploads_dir / ".chunks", self.settings.max_upload_bytes)
        self.url = f"/v1/uploads/{self.upload_id}/complete"
        self.client = TestClient(create_app(self.settings, self.database),
            headers={"Authorization": "Bearer test-key"}, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def prepare(self, content=None, options=None):
        content = self.content if content is None else content
        self.store.create(self.upload_id, "phone.mp4", len(content), options or {})
        size = self.store.status(self.upload_id)["chunk_size"]
        for index, start in enumerate(range(0, len(content), size)):
            part = content[start:start + size]
            self.store.put(self.upload_id, index, part, hashlib.sha256(part).hexdigest())

    def test_complete_preserves_file_identity_without_second_stream_copy(self):
        self.prepare()
        seen = []

        def inspect_source(path):
            self.assertEqual(path.name, "assembled.video")
            seen.append(path.stat())
            return probe_video(path)

        with patch("rallymate_service.api.probe_video", side_effect=inspect_source), \
                patch.object(UploadFile, "read", side_effect=AssertionError("no stream copy for owned input")):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        job = self.database.get_job(self.upload_id)
        destination = Path(job["video_path"])
        self.assertEqual(destination.read_bytes(), self.content)
        self.assertEqual((destination.stat().st_dev, destination.stat().st_ino),
                         (seen[0].st_dev, seen[0].st_ino))
        request = json.loads(Path(job["request_path"]).read_text("utf-8"))
        self.assertEqual(request["upstream_metadata"]["upload_bytes"], len(self.content))
        self.assertEqual(request["upstream_metadata"]["original_filename"], "phone.mp4")
        self.assertFalse((self.store.directory(self.upload_id) / "assembled.video").exists())
        with patch("rallymate_service.api.probe_video", side_effect=AssertionError("retry must reuse durable job")):
            self.assertEqual(self.client.post(self.url).json()["id"], self.upload_id)
        self.assertEqual(len(self.database.list_jobs()), 1)

    def test_failed_queue_insertion_preserves_parts_and_can_retry(self):
        self.prepare()
        with patch.object(self.database, "create_job", side_effect=RuntimeError("queue unavailable")):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 500)
        self.assertIsNone(self.database.get_job(self.upload_id))
        self.assertFalse((self.settings.uploads_dir / f"{self.upload_id}.mp4").exists())
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], len(self.content))
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(Path(self.database.get_job(self.upload_id)["video_path"]).read_bytes(), self.content)

    def test_admission_429_preserves_verified_upload_and_can_retry(self):
        self.prepare()
        with patch.object(self.database, "create_job", side_effect=QueueCapacityError(32)):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.headers["Retry-After"], "30")
        self.assertEqual(response.json()["detail"]["code"], "analysis_queue_full")
        self.assertIsNone(self.database.get_job(self.upload_id))
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], len(self.content))
        self.assertEqual(self.client.post(self.url).status_code, 202)

    def test_maintenance_preserves_existing_uploads_and_allows_read_then_resume(self):
        self.prepare()
        settings = replace(self.settings, accept_new_jobs=False)
        with TestClient(create_app(settings, self.database), headers={"Authorization": "Bearer test-key"}) as client:
            health = client.get("/health/live")
            self.assertFalse(health.json()["admission"]["accept_new_jobs"])
            for response in (client.post("/v1/uploads", json={}),
                             client.post("/v1/jobs", files={"video": ("phone.mp4", self.content, "video/mp4")}),
                             client.post(self.url)):
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.headers["Retry-After"], "30")
                self.assertEqual(response.json()["detail"]["code"], "submissions_paused")
            self.assertEqual(client.get(f"/v1/uploads/{self.upload_id}").status_code, 200)
            self.assertEqual(self.store.status(self.upload_id)["received_bytes"], len(self.content))
            self.assertIsNone(self.database.get_job(self.upload_id))
        self.assertEqual(self.client.post(self.url).status_code, 202)

    def test_lost_response_after_queue_commit_keeps_durable_input(self):
        self.prepare()
        original = self.database.create_job

        def commit_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("response lost after commit")

        with patch.object(self.database, "create_job", side_effect=commit_then_fail):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 500)
        destination = Path(self.database.get_job(self.upload_id)["video_path"])
        self.assertEqual(destination.read_bytes(), self.content)
        self.assertEqual(self.client.post(self.url).status_code, 202)
        self.assertEqual(len(self.database.list_jobs()), 1)
        self.assertEqual(destination.read_bytes(), self.content)

    def test_database_read_failure_keeps_uncertain_handoff_for_retry(self):
        self.prepare()
        original_get = self.database.get_job
        queue_attempted = False
        def fail_queue(*args, **kwargs):
            nonlocal queue_attempted
            queue_attempted = True
            raise RuntimeError("database connection lost")
        def unavailable_after_attempt(job_id):
            if queue_attempted:
                raise RuntimeError("cannot determine commit state")
            return original_get(job_id)
        with patch.object(self.database, "create_job", side_effect=fail_queue), \
                patch.object(self.database, "get_job", side_effect=unavailable_after_attempt):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 500)
        destination = self.settings.uploads_dir / f"{self.upload_id}.mp4"
        self.assertEqual(destination.read_bytes(), self.content)
        self.assertIsNone(original_get(self.upload_id))
        original_identity = destination.stat().st_ino
        request_path = self.settings.requests_dir / f"{self.upload_id}.json"
        original_request = request_path.read_bytes()
        # Recovery verifies matching bytes and preserves both published files.
        self.assertEqual(self.client.post(self.url).status_code, 202)
        self.assertEqual(len(self.database.list_jobs()), 1)
        self.assertEqual(destination.read_bytes(), self.content)
        self.assertEqual(destination.stat().st_ino, original_identity)
        self.assertEqual(request_path.read_bytes(), original_request)

    def test_conflicting_orphan_video_is_not_overwritten(self):
        self.prepare()
        destination = self.settings.uploads_dir / f"{self.upload_id}.mp4"
        other_content = bytes([self.content[0] ^ 1]) + self.content[1:]
        destination.write_bytes(other_content)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(destination.read_bytes(), other_content)
        self.assertIsNone(self.database.get_job(self.upload_id))
        self.assertFalse((self.settings.requests_dir / f"{self.upload_id}.json").exists())
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], len(self.content))

    def test_conflicting_orphan_request_is_not_overwritten(self):
        self.prepare()
        request_path = self.settings.requests_dir / f"{self.upload_id}.json"
        request_path.write_bytes(b'{"existing": "unrelated request"}')
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(request_path.read_bytes(), b'{"existing": "unrelated request"}')
        self.assertIsNone(self.database.get_job(self.upload_id))
        self.assertFalse((self.settings.uploads_dir / f"{self.upload_id}.mp4").exists())

    def test_queue_created_during_probe_keeps_its_existing_files(self):
        self.prepare()
        destination = self.settings.uploads_dir / f"{self.upload_id}.mp4"
        request_path = self.settings.requests_dir / f"{self.upload_id}.json"
        def enqueue_elsewhere(path):
            metadata = probe_video(path)
            destination.write_bytes(b"already queued input")
            request_path.write_bytes(b"already queued request")
            self.database.create_job(self.upload_id, "phone.mp4", destination, request_path,
                                     self.settings.runs_dir / self.upload_id)
            return metadata
        with patch("rallymate_service.api.probe_video", side_effect=enqueue_elsewhere):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(destination.read_bytes(), b"already queued input")
        self.assertEqual(request_path.read_bytes(), b"already queued request")

    def test_maintenance_during_probe_fences_enqueue_and_preserves_uploaded_bytes(self):
        self.prepare()
        app = create_app(self.settings, self.database,
            executor_status=lambda: {"status": "ready", "available": True, "heartbeat_at": None},
            executor_shutdown=lambda: {"status": "stopped"})
        stop = next(route.endpoint for route in app.routes
                    if getattr(route, 'path', '') == '/internal/maintenance/stop-executor')

        def drain_during_probe(path):
            metadata = probe_video(path)
            stop(Request({'type': 'http', 'client': ('127.0.0.1', 50000)}), Response(), None)
            return metadata

        with TestClient(app, headers={"Authorization": "Bearer test-key"}) as client:
            with patch('rallymate_service.api.probe_video', side_effect=drain_during_probe):
                response = client.post(self.url)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIsNone(self.database.get_job(self.upload_id))
        directory = self.store.directory(self.upload_id)
        parts = sorted(directory.glob('*.part'), key=lambda path: int(path.stem))
        self.assertEqual(b''.join(part.read_bytes() for part in parts), self.content)
        self.assertEqual(self.store.status(self.upload_id)['received_bytes'], len(self.content))
        self.assertEqual(self.client.post(self.url).status_code, 202)

    def test_directory_names_are_synced_before_queue_commit(self):
        self.prepare()
        synced = []
        original_create = self.database.create_job
        def sync(directory):
            _sync_submission_directory(directory)
            synced.append(directory.resolve())
        def create(*args, **kwargs):
            for directory in (self.settings.requests_dir, self.settings.uploads_dir,
                              self.store.directory(self.upload_id)):
                self.assertIn(directory.resolve(), synced)
            return original_create(*args, **kwargs)
        with patch("rallymate_service.api._sync_submission_directory", side_effect=sync), \
                patch.object(self.database, "create_job", side_effect=create):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)

    def test_publish_failure_remains_retryable_without_queued_job(self):
        self.prepare()
        with patch("rallymate_service.api._rename_submission_file", side_effect=OSError("storage unavailable")):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 500)
        self.assertIsNone(self.database.get_job(self.upload_id))
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], len(self.content))
        self.assertFalse((self.settings.uploads_dir / f"{self.upload_id}.mp4").exists())
        self.assertEqual(self.client.post(self.url).status_code, 202)

    def test_invalid_video_and_duration_limit_still_reject_before_handoff(self):
        for content, duration_limit in ((b"not a video", 10), (self.content, 0)):
            with self.subTest(duration_limit=duration_limit):
                self.upload_id = str(uuid.uuid4())
                self.url = f"/v1/uploads/{self.upload_id}/complete"
                self.prepare(content)
                if duration_limit == 0:
                    original = probe_video
                    def too_long(path):
                        result = original(path)
                        result.duration_ms = 10001
                        return result
                    with patch("rallymate_service.api.probe_video", side_effect=too_long):
                        response = self.client.post(self.url)
                    expected = 413
                else:
                    response = self.client.post(self.url)
                    expected = 422
                self.assertEqual(response.status_code, expected, response.text)
                self.assertIsNone(self.database.get_job(self.upload_id))
                self.assertFalse((self.settings.uploads_dir / f"{self.upload_id}.mp4").exists())
                self.assertEqual(self.store.status(self.upload_id)["received_bytes"], len(content))

    def test_file_mutation_during_probe_is_rejected_and_parts_survive(self):
        self.prepare()
        def modify(path):
            metadata = probe_video(path)
            initial = path.stat()
            changed = bytearray(path.read_bytes())
            changed[-1] ^= 1
            path.write_bytes(changed)
            os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns + 2_000_000_000))
            return metadata
        with patch("rallymate_service.api.probe_video", side_effect=modify):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIsNone(self.database.get_job(self.upload_id))
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], len(self.content))
        self.assertEqual(self.client.post(self.url).status_code, 202)

    def test_authorization_and_original_multipart_path_are_preserved(self):
        self.prepare()
        response = self.client.post(self.url, headers={"Authorization": "Bearer wrong-key"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.database.list_jobs(), [])
        with patch("rallymate_service.api._owned_upload_source", side_effect=AssertionError("multipart stays streamed")):
            response = self.client.post("/v1/jobs", files={"video": ("direct.mp4", self.content, "video/mp4")})
        self.assertEqual(response.status_code, 202, response.text)
        job = self.database.get_job(response.json()["id"])
        self.assertEqual(Path(job["video_path"]).read_bytes(), self.content)


if __name__ == "__main__":
    unittest.main()
