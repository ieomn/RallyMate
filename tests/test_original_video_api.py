from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service import api
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.original_video import OriginalVideoUnavailable, original_video_descriptor, verify_original_video
from tests.test_report_delivery_api import poll
from tests.test_report_evidence_transport import native_fixture


JOB = "66883aeb-e0c3-4065-80fd-f67aa108b208"
SOURCE = "e4204ec3-7d84-4a90-a961-9dad01b43c2e"
BODY = b"test original footage bytes: 0123456789"


class OriginalVideoApiTests(unittest.TestCase):
    @staticmethod
    def timed_job(settings, database, *, origin=12000, start=8000):
        job = database.get_job(JOB)
        source_id = job["summary"]["minimum_scoring_loop"]["provenance"]["pipeline_job_id"]
        video_sha = hashlib.sha256(BODY).hexdigest()
        first_index = start // 40
        rows = [{"job_id": source_id, "frame": {"index": first_index + i, "processed_index": i,
                 "timestamp_ms": start + i * 40, "source_timestamp_ms": origin + start + i * 40,
                 "timestamp_source": "decoder_pts", "video_sha256": video_sha,
                 "pts": (origin + start) // 40 + i, "time_base_num": 1, "time_base_den": 25}}
                for i in range(2)]
        frames = Path(job["output_dir"]) / "frames.jsonl"
        frames.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        job["summary"]["processing"] = {"processed_frames": 2, "source_start_frame": first_index,
            "source_timing": {"method": "decoder_presentation_timestamps", "timing_verified": True,
                              "fallback_timestamp_frames": 0, "decoder_timestamp_frames": first_index + 2,
                              "source_origin_ms": origin}}
        job["summary"]["minimum_scoring_loop"]["provenance"]["frames_sha256"] = hashlib.sha256(frames.read_bytes()).hexdigest()
        return job, frames, rows

    def setup_job(self, root: Path, *, recovered=False):
        settings = ServiceSettings(data_root=root / "data", database_path=root / "data/jobs.sqlite3",
                                   detect_model=root / "detect.pt", pose_model=root / "pose.pt",
                                   api_key="original-video-fixture-key")
        settings.ensure_directories()
        source_id = SOURCE if recovered else JOB
        video = settings.uploads_dir / f"{source_id}.mp4"
        video.write_bytes(BODY)
        output = settings.runs_dir / JOB
        output.mkdir()
        old_path = f"/old/cloud/service_data/uploads/{source_id}.mp4"
        summary = {"job_id": JOB, "status": "completed",
                   "input": {"video_path": old_path, "upstream_metadata": {"upload_bytes": len(BODY)}},
                   "minimum_scoring_loop": {"video_id": source_id, "provenance": {
                       "pipeline_job_id": source_id, "video_path": old_path,
                       "video_sha256": hashlib.sha256(BODY).hexdigest().upper()}},
                   "action_recognition": {"detector_version": api.DETECTOR_VERSION,
                       "motion_analysis": {"analysis_version": api.ANALYSIS_VERSION}}}
        if recovered:
            summary.update(source_failed_job_id=source_id, recovery={
                "source_failed_job_id": source_id, "recovery_job_id": JOB,
                "reused_inference": True, "inference_identity_preserved": True,
                "mode": "cpu_finalization", "status": "completed",
                "video_sha256": hashlib.sha256(BODY).hexdigest()})
        (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        database = JobDatabase(settings.database_path)
        database.initialize()
        database.create_job(JOB, "private name.mp4", video, settings.requests_dir / f"{JOB}.json", output)
        database.claim_next("original-video-test", settings.lease_seconds, settings.max_attempts)
        database.mark_succeeded(JOB, summary)
        return settings, database, summary, video

    def test_authenticated_range_and_report_link_preserve_migrated_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, database, summary, video = self.setup_job(Path(directory), recovered=True)
            summary_before = (settings.runs_dir / JOB / "summary.json").read_bytes()
            url = f"/v1/jobs/{JOB}/original-video"
            headers = {"Authorization": f"Bearer {settings.api_key}"}
            with TestClient(api.create_app(settings, database)) as client:
                self.assertEqual(client.get(url).status_code, 401)
                response = client.get(url, headers={**headers, "Range": "bytes=4-12"})
                self.assertEqual(response.status_code, 206, response.text)
                self.assertEqual(response.content, BODY[4:13])
                self.assertEqual(response.headers["content-range"], f"bytes 4-12/{len(BODY)}")
                self.assertIn("no-store", response.headers["cache-control"])
                self.assertIn("inline", response.headers["content-disposition"])
                self.assertEqual(client.get(url, headers={**headers, "Range": "bytes=900-"}).status_code, 416)
                self.assertEqual(client.get(url, headers={**headers, "Range": "bytes=-5"}).content, BODY[-5:])
                result = client.get(f"/v1/jobs/{JOB}", headers=headers).json()
                self.assertEqual(result["original_video"], {"url": url, "source_job_id": SOURCE,
                    "sha256": hashlib.sha256(BODY).hexdigest(), "bytes": len(BODY)})
                self.assertNotIn(str(video), json.dumps(result["original_video"]))
                self.assertEqual(result["summary"], summary)
            self.assertEqual(database.get_job(JOB)["summary"], summary)
            self.assertEqual((settings.runs_dir / JOB / "summary.json").read_bytes(), summary_before)

    def test_http_unsatisfiable_range_has_bytes_unit_and_preserves_if_range(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, database, _, _ = self.setup_job(Path(directory), recovered=True)
            url = f"/v1/jobs/{JOB}/original-video"
            with TestClient(api.create_app(settings, database)) as client:
                client.headers["Authorization"] = f"Bearer {settings.api_key}"
                full = client.get(url)
                self.assertEqual(full.status_code, 200)
                self.assertEqual(full.content, BODY)
                for validator in (None, full.headers["etag"], full.headers["last-modified"]):
                    headers = {"Range": "bytes=4-12"}
                    if validator is not None:
                        headers["If-Range"] = validator
                    partial = client.get(url, headers=headers)
                    self.assertEqual(partial.status_code, 206)
                    self.assertEqual(partial.content, BODY[4:13])
                    self.assertEqual(partial.headers["content-range"], f"bytes 4-12/{len(BODY)}")
                mismatch = client.get(url, headers={"Range": "bytes=4-12", "If-Range": '"not-current"'})
                self.assertEqual(mismatch.status_code, 200)
                self.assertEqual(mismatch.content, BODY)
                self.assertNotIn("content-range", mismatch.headers)
                unsatisfiable = client.get(url, headers={"Range": f"bytes={len(BODY)}-"})
                self.assertEqual(unsatisfiable.status_code, 416)
                self.assertEqual(unsatisfiable.content, b"")
                self.assertEqual(unsatisfiable.headers["content-range"], f"bytes */{len(BODY)}")

    def test_local_normal_job_and_portable_historical_names(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, database, _, video = self.setup_job(Path(directory))
            job = database.get_job(JOB)
            self.assertEqual(verify_original_video(job, settings).path, video.resolve())
            self.assertEqual(original_video_descriptor(job, replace(settings, public_base_url="https://cloud.example"))["url"],
                             f"/v1/jobs/{JOB}/original-video")
            for historical in (str(video), f"D:\\prior machine\\uploads\\{JOB}.mp4"):
                job["summary"]["input"]["video_path"] = historical
                job["summary"]["minimum_scoring_loop"]["provenance"]["video_path"] = historical
                self.assertEqual(verify_original_video(job, settings).source_job_id, JOB)

    def test_original_pose_binding_uses_decoder_origin_including_recovery_and_later_analysis_start(self):
        for recovered in (False, True):
            with self.subTest(recovered=recovered), tempfile.TemporaryDirectory() as directory:
                settings, database, _, _ = self.setup_job(Path(directory), recovered=recovered)
                job, frames, _ = self.timed_job(settings, database)
                before = frames.read_bytes()
                result = original_video_descriptor(job, settings)
                binding = result["pose_time_binding"]
                self.assertEqual(binding["job_id"], JOB)
                self.assertEqual(binding["source_job_id"], SOURCE if recovered else JOB)
                self.assertEqual(binding["source_pts_origin_ms"], 12000)
                self.assertEqual(binding["media_time_origin_ms"], 0)
                self.assertEqual(binding["anchor"]["timestamp_ms"], 8000)
                self.assertEqual(binding["anchor"]["pts"], 500)
                self.assertEqual(binding["anchor"]["source_timestamp_ms"], 20000)
                self.assertEqual(binding["video_sha256"], result["sha256"])
                self.assertNotIn(str(frames), json.dumps(binding))
                with database.connect() as connection:
                    connection.execute("UPDATE jobs SET summary_json = ? WHERE id = ?", (json.dumps(job["summary"]), JOB))
                    connection.commit()
                with TestClient(api.create_app(settings, database)) as client:
                    response = client.get(f"/v1/jobs/{JOB}", headers={"Authorization": f"Bearer {settings.api_key}"})
                    self.assertEqual(response.json()["original_video"]["pose_time_binding"], binding)
                self.assertEqual(frames.read_bytes(), before)

    def test_missing_or_conflicting_pose_clock_evidence_preserves_video_without_authorizing_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, database, _, _ = self.setup_job(Path(directory))
            for mutate in (
                lambda job: job["summary"]["processing"].pop("source_timing"),
                lambda job: job["summary"]["processing"]["source_timing"].update(timing_verified=False),
                lambda job: job["summary"]["processing"]["source_timing"].update(fallback_timestamp_frames=1),
                lambda job: job["summary"]["processing"]["source_timing"].update(method="average_fps"),
                lambda job: job["summary"]["processing"]["source_timing"].update(source_origin_ms=0),
                lambda job: job["summary"]["processing"].update(source_start_frame=0),
                lambda job: job["summary"]["minimum_scoring_loop"]["provenance"].update(frames_sha256="f" * 64),
            ):
                job, _, _ = self.timed_job(settings, database)
                mutate(job)
                result = original_video_descriptor(job, settings)
                self.assertIsNotNone(result)
                self.assertNotIn("pose_time_binding", result)
            for change in ({"video_sha256": "b" * 64}, {"pts": 499}, {"timestamp_ms": 0},
                           {"time_base_den": 24}, {"timestamp_source": "fps_fallback"}, {"processed_index": 1}):
                job, frames, rows = self.timed_job(settings, database)
                rows[0]["frame"].update(change)
                frames.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                job["summary"]["minimum_scoring_loop"]["provenance"]["frames_sha256"] = hashlib.sha256(frames.read_bytes()).hexdigest()
                self.assertNotIn("pose_time_binding", original_video_descriptor(job, settings))
            job, frames, rows = self.timed_job(settings, database)
            before = frames.stat()
            raw = frames.read_bytes().replace(b'"pts": 500', b'"pts": 499')
            frames.write_bytes(raw)
            os.utime(frames, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertNotIn("pose_time_binding", original_video_descriptor(job, settings))

    def test_conflicting_identity_missing_evidence_and_unsafe_paths_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, database, _, video = self.setup_job(root, recovered=True)
            good = database.get_job(JOB)
            mutations = [
                lambda j: j.update(status="running"),
                lambda j: j.update(cancel_requested=True),
                lambda j: j["summary"].update(job_id=SOURCE),
                lambda j: j["summary"].pop("recovery"),
                lambda j: j["summary"]["recovery"].update(source_failed_job_id=JOB),
                lambda j: j["summary"]["recovery"].update(video_sha256="0" * 64),
                lambda j: j["summary"]["recovery"].update(inference_identity_preserved=False),
                lambda j: j["summary"]["input"].update(video_path=f"/old/uploads/{JOB}.mp4"),
                lambda j: j["summary"]["minimum_scoring_loop"]["provenance"].pop("video_sha256"),
                lambda j: j["summary"]["input"]["upstream_metadata"].update(upload_bytes=len(BODY) + 1),
            ]
            for mutation in mutations:
                job = copy.deepcopy(good)
                mutation(job)
                with self.assertRaises(OriginalVideoUnavailable):
                    verify_original_video(job, settings)
            outside = root / video.name
            outside.write_bytes(BODY)
            job = copy.deepcopy(good); job["video_path"] = str(outside)
            with self.assertRaises(OriginalVideoUnavailable):
                verify_original_video(job, settings)
            nested = settings.uploads_dir / "nested" / video.name
            nested.parent.mkdir(); nested.write_bytes(BODY)
            job["video_path"] = str(nested)
            with self.assertRaises(OriginalVideoUnavailable):
                verify_original_video(job, settings)
            with self.assertRaises(OriginalVideoUnavailable):
                verify_original_video(good, replace(settings, max_upload_bytes=len(BODY) - 1))

    def test_same_size_tamper_restored_mtime_never_serves_original_or_cached_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, database, _, video = self.setup_job(Path(directory), recovered=True)
            supplied = native_fixture(); supplied["job_id"] = JOB
            headers = {"Authorization": f"Bearer {settings.api_key}"}
            with patch.object(api, "build_user_demo_result", side_effect=lambda *a, **kw: copy.deepcopy(supplied)), TestClient(api.create_app(settings, database)) as client:
                client.headers.update(headers)
                url = f"/v1/jobs/{JOB}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
                ready = poll(client, url)
                self.assertEqual(ready.status_code, 200, ready.text[:200])
                self.assertEqual(ready.json()["original_video"]["source_job_id"], SOURCE)
                before = video.stat()
                video.write_bytes(b"x" * len(BODY))
                os.utime(video, ns=(before.st_atime_ns, before.st_mtime_ns))
                self.assertEqual(client.get(f"/v1/jobs/{JOB}/original-video").status_code, 409)
                self.assertIsNone(client.get(f"/v1/jobs/{JOB}").json()["original_video"])
                ready = poll(client, url)
                self.assertEqual(ready.status_code, 200)
                self.assertIsNone(ready.json()["original_video"])

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings, database, _, video = self.setup_job(root)
            outside = root / video.name; outside.write_bytes(BODY)
            video.unlink()
            try:
                video.symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation unavailable on this host")
            with self.assertRaises(OriginalVideoUnavailable):
                verify_original_video(database.get_job(JOB), settings)


if __name__ == "__main__":
    unittest.main()
