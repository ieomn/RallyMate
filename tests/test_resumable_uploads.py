from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rallymate_service.api import create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.uploads import CHUNK_BYTES, SESSION_TTL_SECONDS, UploadStore


class ResumableUploadTests(unittest.TestCase):
    def test_out_of_order_resume_corruption_and_exactly_once_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = UploadStore(Path(temporary), 20 * CHUNK_BYTES)
            upload_id = str(uuid.uuid4())
            data = b"a" * CHUNK_BYTES + b"last chunk"
            store.create(upload_id, "mobile.mov", len(data), {})
            chunks = [data[:CHUNK_BYTES], data[CHUNK_BYTES:]]
            store.put(upload_id, 1, chunks[1], hashlib.sha256(chunks[1]).hexdigest())
            # New process/store instance must see persisted progress.
            store = UploadStore(Path(temporary), 20 * CHUNK_BYTES)
            self.assertEqual(store.status(upload_id)["received_chunks"], [1])
            with self.assertRaises(HTTPException):
                store.complete(upload_id, lambda *_: None, lambda _: None)
            with self.assertRaises(HTTPException):
                store.put(upload_id, 0, chunks[0], "0" * 64)
            store.put(upload_id, 0, chunks[0], hashlib.sha256(chunks[0]).hexdigest())
            # Both repeated and concurrent identical requests are safe.
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda _: store.put(upload_id, 0, chunks[0], hashlib.sha256(chunks[0]).hexdigest()), range(2)))
            self.assertTrue(all(r["received_bytes"] == len(data) for r in results))
            jobs = {}
            calls = []
            def enqueue(path, manifest):
                calls.append(1)
                self.assertEqual(path.read_bytes(), data)
                jobs[upload_id] = {"id": upload_id}
                return jobs[upload_id]
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda _: store.complete(upload_id, enqueue, jobs.get), range(2)))
            self.assertEqual(len(calls), 1)
            self.assertEqual(results, [{"id": upload_id}] * 2)
            self.assertFalse(list((Path(temporary) / upload_id).glob("*.part")))

    def test_disk_corruption_is_removed_from_resume_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = UploadStore(Path(temporary), CHUNK_BYTES)
            upload_id = str(uuid.uuid4())
            store.create(upload_id, "a.mp4", 3, {})
            store.put(upload_id, 0, b"abc", hashlib.sha256(b"abc").hexdigest())
            (store.directory(upload_id) / "0.part").write_bytes(b"bad")
            with self.assertRaises(HTTPException):
                store.complete(upload_id, lambda *_: self.fail("must not enqueue"), lambda _: None)
            self.assertEqual(store.status(upload_id)["received_chunks"], [])

    def test_expiry_and_identity_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = UploadStore(Path(temporary), CHUNK_BYTES)
            upload_id = str(uuid.uuid4())
            store.create(upload_id, "a.mp4", 3, {})
            with self.assertRaises(HTTPException):
                store.create(upload_id, "other.mp4", 3, {})
            with self.assertRaises(HTTPException):
                store.status("../other")
            store.put(upload_id, 0, b"abc", hashlib.sha256(b"abc").hexdigest())
            directory = store.directory(upload_id)
            import json
            manifest = store.read(directory)
            manifest["updated_at"] = time.time() - SESSION_TTL_SECONDS - 1
            (directory / "manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(store.cleanup(), 1)
            self.assertFalse((directory / "0.part").exists())
            with self.assertRaises(HTTPException) as expired:
                store.status(upload_id)
            self.assertEqual(expired.exception.status_code, 410)

    def test_http_upload_persists_and_queues_once_without_inference_on_parts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            detect = root / "detect.pt"; detect.touch()
            pose = root / "pose.pt"; pose.touch()
            settings = ServiceSettings(data_root=root / "data", database_path=root / "jobs.sqlite3", detect_model=detect, pose_model=pose, api_key="test-key")
            settings.ensure_directories()
            database = JobDatabase(settings.database_path)
            video = root / "test.mp4"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
            for _ in range(5):
                writer.write(np.zeros((120, 160, 3), dtype=np.uint8))
            writer.release()
            content = video.read_bytes()
            upload_id = str(uuid.uuid4())
            payload = {"upload_id": upload_id, "filename": "phone.mp4", "size": len(content), "options": {"court_mode": "disabled", "write_annotated_video": True}}
            with TestClient(create_app(settings, database)) as client:
                self.assertEqual(client.post("/v1/uploads", json=payload).status_code, 401)
                client.headers["Authorization"] = "Bearer test-key"
                created = client.post("/v1/uploads", json=payload)
                self.assertEqual(created.status_code, 201, created.text)
                url = f"/v1/uploads/{upload_id}"
                self.assertEqual(client.post(url + "/complete").status_code, 409)
                part = client.put(url + "/chunks/0", content=content, headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()})
                self.assertEqual(part.status_code, 200, part.text)
                self.assertEqual(database.list_jobs(), [])
            # Restart API before finalizing.
            with TestClient(create_app(settings, database), headers={"Authorization": "Bearer test-key"}) as client:
                self.assertEqual(client.get(url).json()["received_bytes"], len(content))
                submitted = client.post(url + "/complete")
                self.assertEqual(submitted.status_code, 202, submitted.text)
                self.assertEqual(submitted.json()["id"], upload_id)
                self.assertEqual(client.post(url + "/complete").json()["id"], upload_id)
                self.assertEqual(len(database.list_jobs()), 1)
                self.assertEqual(Path(database.get_job(upload_id)["video_path"]).read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
