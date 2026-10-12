from __future__ import annotations

import io
import http.client
import json
import tempfile
import sqlite3
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from rallymate_service.database import JobDatabase
from rallymate_service.motion_replay_api import (
    MotionReplayInputError, build_motion_replay_router, enqueue_job_replay, public_motion_replay,
)
from rallymate_service.motion_replay_provider import (
    LEGACY_PROMPT_V1, PROMPT, RECONSTRUCTION_PROMPT, MiniMaxVideoProvider, ProviderError, _download_url, build_generation_payload, request_prompt,
)
from rallymate_service.motion_replay_store import (
    APPEARANCE_REFERENCE, APPEARANCE_REFERENCE_VERSION, APPEARANCE_STYLE, MEDIA_FILES, SOURCE_REFERENCE_FILES,
    LeaseLost, MotionReplaySettings, MotionReplayStore, ReplayConflict, sha256_path,
)
from rallymate_service.motion_replay_worker import _validate_output, process_motion_replay, run_motion_replay_worker
from rallymate_service.motion_replay_source import source_video_path


MP4 = b"\x00\x00\x00\x18ftypisom" + b"test-only-media"


def fake_renderer(frames, output, **kwargs):
    from rallymate_vision.motion_replay import FULL_BODY_RENDERER_VERSION, MOTION_REPLAY_VERSION
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {key: filename for key, filename in MEDIA_FILES.items() if key != "generated_video" and key not in SOURCE_REFERENCE_FILES}
    manifest = {"schema_version": "1.0.0", "version": MOTION_REPLAY_VERSION, "artifacts": artifacts,
                "source": {"frames_path": str(frames), "frames_sha256": "hash", "start_ms": kwargs["start_ms"]},
                "quality": {"source_accuracy_verified": False},
                "render": {"is_3d_reconstruction": False, "style": kwargs.get("appearance_style"),
                           "renderer_version": FULL_BODY_RENDERER_VERSION}}
    for filename in artifacts.values():
        (output / filename).write_bytes(json.dumps(manifest).encode() if filename.endswith(".json") else MP4)
    manifest["artifact_checksums"] = {filename: sha256_path(output / filename)
                                      for filename in artifacts.values() if filename != "manifest.json"}
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def fake_source_renderer(source, trajectory, output, **kwargs):
    from rallymate_vision.motion_reconstruction_reference import REFERENCE_VERSION
    output.mkdir(parents=True, exist_ok=True)
    (output / "reference.mp4").write_bytes(MP4 + b"original-rgb-crop")
    start = json.loads(trajectory.read_text(encoding="utf-8"))["source"]["start_ms"]
    manifest = {"version": REFERENCE_VERSION, "result_kind": "original_rgb_person_reference",
                "source": {"sha256": sha256_path(source), "trajectory_sha256": sha256_path(trajectory),
                           "start_ms": start, "duration_ms": 6000, "person_track_id": 2},
                "crop": {"mode": "fixed_entire_window", "requested_horizontal_margin_px": kwargs["horizontal_margin_px"],
                         "requested_vertical_margin_px": kwargs["vertical_margin_px"], "width": 320, "height": 288},
                "video": {"audio_included": False, "geometric_resize": False, "visual_interpolation": False,
                          "overlays": False, "fps": 24, "frame_count": 144, "duration_ms": 6000, "width": 320, "height": 288},
                "frame_map": [{"source_frame_index": index} for index in range(144)],
                "target_locator": {"strategy": "first_frame_crop_bbox_v1", "source_frame_index": 0,
                                   "person_track_id": 2, "coordinate_space": "normalized_reference_crop_0_1",
                                   "bbox_xyxy": [.2, .2, .6, .8]},
                "artifact_checksums": {"reference.mp4": sha256_path(output / "reference.mp4")}}
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class FakeProvider:
    def __init__(self):
        self.submissions = 0
        self.submit_kwargs = []
        self.submitted_references = []
        self.queries = []
        self.submit_error = None
        self.query_error = None
        self.download_error = None
        self.result_status = "succeeded"

    def submit(self, reference, **kwargs):
        self.submissions += 1
        self.submit_kwargs.append(kwargs)
        self.submitted_references.append(reference)
        if self.submit_error:
            raise self.submit_error
        return "remote-123"

    def query(self, task_id):
        self.queries.append(task_id)
        if self.query_error:
            raise self.query_error
        return {"status": self.result_status, "download_url": "https://cdn.example.com/output.mp4"}

    def download(self, url, target):
        if self.download_error:
            raise self.download_error
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(MP4)


class MotionReplayServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = MotionReplaySettings(self.root, enabled=True, poll_seconds=.01)
        self.store = MotionReplayStore(self.settings.database_path)
        self.store.initialize()
        self.jobs = JobDatabase(self.root / "jobs.sqlite3")
        self.jobs.initialize()
        self.source = self.root / "original-analysis"
        self.source.mkdir()
        (self.source / "frames.jsonl").write_text("{}\n", encoding="utf-8")
        (self.source / "primary-player.jsonl").write_text("{}\n", encoding="utf-8")
        self.jobs.create_job("job-1", "input.mp4", self.root / "input.mp4", self.root / "request.json", self.source)
        self.jobs.claim_next("vision", 60, 2)
        self.jobs.mark_succeeded("job-1", {"unchanged": True})
        self.provider = FakeProvider()

    def enqueue(self, **kwargs):
        return enqueue_job_replay(jobs=self.jobs, store=self.store, settings=self.settings,
                                  job_id="job-1", payload={"start_ms": 0}, **kwargs)

    def enqueue_source(self, **kwargs):
        return enqueue_job_replay(jobs=self.jobs, store=self.store, settings=self.settings,
                                  job_id="job-1", payload={"start_ms": 0, "mode": "source_reconstruction"}, **kwargs)

    def tick(self):
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET next_attempt_at=0")
            connection.commit()
        return process_motion_replay(self.store, self.settings, provider_factory=lambda: self.provider,
                                     renderer=fake_renderer,
                                     source_renderer=fake_source_renderer,
                                     output_validator=lambda *_: {"duration_ms": 6583, "duration_difference_ms": 583,
                                                                   "timing_adjustment": "none", "fidelity_verified": False})

    def test_atomic_content_deduplication_and_idempotency_conflict(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            rows = list(executor.map(lambda _: self.enqueue(idempotency_key="one"), range(8)))
        self.assertEqual(len({row["id"] for row in rows}), 1)
        self.assertEqual(len(self.store.list_for_job("job-1")), 1)
        with self.assertRaises(ReplayConflict):
            enqueue_job_replay(jobs=self.jobs, store=self.store, settings=self.settings, job_id="job-1",
                               payload={"start_ms": 1000}, idempotency_key="one")

    def test_new_generation_snapshots_surface_image_and_passes_verified_first_frame(self):
        row = self.enqueue()
        request = row["request"]
        self.assertEqual(request["appearance_reference"], APPEARANCE_REFERENCE)
        self.assertEqual(request["appearance_reference_version"], APPEARANCE_REFERENCE_VERSION)
        self.assertEqual(request["appearance_style"], APPEARANCE_STYLE)
        self.assertEqual(request["prompt_strategy"], "structured")
        self.tick()
        stored = self.store.get(row["id"])
        self.assertEqual(stored["generation_status"], "provider_processing")
        self.assertEqual(self.provider.submit_kwargs[0]["prompt"], request["prompt"])
        self.assertEqual(self.provider.submit_kwargs[0]["reference_image"],
                         self.settings.output_root / row["id"] / stored["artifacts"]["first_frame"])
        self.assertEqual(public_motion_replay(stored, self.settings)["prompt_strategy"], "structured")

    def test_source_reconstruction_requires_provenance_and_submits_rgb_instead_of_pose_video(self):
        with self.assertRaises(MotionReplayInputError) as error:
            self.enqueue_source()
        self.assertEqual(error.exception.status_code, 409)
        source = self._original_video({"request": {"frames_sha256": sha256_path(self.source / "frames.jsonl")}})
        row = self.enqueue_source()
        self.assertEqual(row["request"]["prompt"], RECONSTRUCTION_PROMPT)
        self.assertEqual(row["request"]["motion_reference_mode"], "source_video")
        self.assertEqual(row["request"]["original_video_sha256"], sha256_path(source))
        self.tick()
        stored = self.store.get(row["id"])
        self.assertEqual(stored["generation_status"], "provider_processing")
        root = self.settings.output_root / row["id"]
        self.assertEqual(self.provider.submitted_references, [root / stored["artifacts"]["motion_source_video"]])
        self.assertNotEqual(self.provider.submitted_references[0], root / stored["artifacts"]["reference_video"])
        self.assertEqual(self.provider.submitted_references[0].read_bytes(), MP4 + b"original-rgb-crop")
        self.assertEqual(self.provider.submit_kwargs[0]["reference_image"], root / stored["artifacts"]["first_frame"])
        prompt = self.provider.submit_kwargs[0]["prompt"]
        self.assertIn("[TRACKED SUBJECT LOCATION]", prompt)
        self.assertIn("0.200000, 0.200000, 0.600000, 0.800000", prompt)
        self.assertEqual(stored["render_manifest"]["motion_reference"]["submission_prompt_snapshot"], prompt)
        public = public_motion_replay(stored, self.settings)
        self.assertEqual(public["generation_mode"], "source_reconstruction")
        self.assertTrue(public["media"]["motion_source_video"].endswith("/motion-source.mp4"))
        self.assertFalse(public["semantics"]["anatomical_correction_verified"])
        self.assertFalse(public["semantics"]["used_for_scoring"])

    def test_default_mode_stays_deduplicated_and_does_not_reuse_source_mode_idempotency(self):
        original = self.enqueue(idempotency_key="same-intent")
        explicit = enqueue_job_replay(jobs=self.jobs, store=self.store, settings=self.settings, job_id="job-1",
                                      payload={"start_ms": 0, "mode": "trajectory_proxy"})
        self.assertEqual(original["id"], explicit["id"])
        self.assertNotIn("motion_reference_mode", original["request"])
        with self.assertRaises(ReplayConflict):
            self.enqueue_source(idempotency_key="same-intent")
        legacy = dict(original["request"]); legacy.pop("client_request")
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET request_json=? WHERE id=?", (json.dumps(legacy), original["id"]))
            connection.execute("UPDATE motion_replay_idempotency SET client_request_json=NULL")
            connection.commit()
        with self.assertRaises(ReplayConflict):
            self.enqueue_source(idempotency_key="same-intent")

    def test_changed_source_video_prevents_generation_and_unsupported_fps_has_specific_message(self):
        source = self._original_video({"request": {"frames_sha256": sha256_path(self.source / "frames.jsonl")}})
        row = self.enqueue_source()
        source.write_bytes(b"changed video")
        self.tick()
        self.assertEqual(self.store.get(row["id"])["error_code"], "source_evidence_changed")
        self.assertEqual(self.provider.submissions, 0)
        self._original_video(row)
        fresh = self.enqueue_source(idempotency_key="new-fps-test")
        # Reuse of content intentionally resolves to the previous failed task;
        # a separate source hash is needed to model another upload in this unit test.
        request = {**fresh["request"], "prompt_version": "fps-test-variant"}
        another = self.store.create("job-1", request)
        from rallymate_vision.motion_reconstruction_reference import ReconstructionReferenceError
        with patch("rallymate_service.motion_replay_worker._render_source", side_effect=ReconstructionReferenceError("reference_timebase_mismatch")):
            process_motion_replay(self.store, self.settings, provider_factory=lambda: self.provider, renderer=fake_renderer)
        failure = self.store.get(another["id"])
        self.assertEqual(failure["error_code"], "reference_timebase_mismatch")
        self.assertIn("连续 24fps", failure["error_message"])
        self.assertEqual(self.provider.submissions, 0)

    def test_source_crop_tamper_fails_before_post_and_unknown_submission_is_not_repeated(self):
        self._original_video({"request": {"frames_sha256": sha256_path(self.source / "frames.jsonl")}})
        row = self.enqueue_source()
        def missing():
            raise ProviderError("credentials_unavailable", "未配置", retryable=True)
        process_motion_replay(self.store, self.settings, provider_factory=missing, renderer=fake_renderer,
                              source_renderer=fake_source_renderer)
        rendered = self.store.get(row["id"])
        source_crop = self.settings.output_root / row["id"] / rendered["artifacts"]["motion_source_video"]
        source_crop.write_bytes(MP4 + b"replaced")
        self.store.retry(row["id"])
        self.tick()
        self.assertEqual(self.store.get(row["id"])["error_code"], "reconstruction_reference_changed")
        self.assertEqual(self.provider.submissions, 0)
        another = self.store.create("job-1", {**row["request"], "prompt_version": "unknown-post-variant"})
        self.provider.submit_error = ProviderError("submission_outcome_unknown", "未确认", submission_unknown=True)
        self.tick()
        self.assertEqual(self.store.get(another["id"])["generation_status"], "submit_unknown")
        self.assertFalse(self.tick())
        self.assertEqual(self.provider.submissions, 1)

    def test_source_reference_binds_trajectory_to_original_white_render_checksum(self):
        self._original_video({"request": {"frames_sha256": sha256_path(self.source / "frames.jsonl")}})
        original = self.enqueue_source()
        for phase in ("before_export", "during_export", "before_submit"):
            with self.subTest(phase=phase):
                row = original if phase == "before_export" else self.store.create(
                    "job-1", {**original["request"], "prompt_version": "trajectory-check-" + phase})
                def render(frames, output, **kwargs):
                    manifest = fake_renderer(frames, output, **kwargs)
                    if phase == "before_export":
                        with (output / "trajectory.json").open("a", encoding="utf-8") as stream:
                            stream.write("\n")
                    return manifest
                def render_source(source, trajectory, output, **kwargs):
                    if phase == "during_export":
                        # Exporter could hash this changed file consistently, but
                        # it no longer matches the frozen white render evidence.
                        with trajectory.open("a", encoding="utf-8") as stream:
                            stream.write("\n")
                    return fake_source_renderer(source, trajectory, output, **kwargs)
                def provider():
                    if phase == "before_submit":
                        frozen = self.store.get(row["id"])
                        path = self.settings.output_root / row["id"] / frozen["artifacts"]["trajectory"]
                        with path.open("a", encoding="utf-8") as stream:
                            stream.write("\n")
                    return self.provider
                with patch("rallymate_service.motion_replay_worker._render_source", wraps=render_source) as exporter:
                    process_motion_replay(self.store, self.settings, provider_factory=provider, renderer=render)
                failure = self.store.get(row["id"])
                self.assertEqual(failure["error_code"], "reconstruction_trajectory_changed")
                self.assertEqual(exporter.call_count, 0 if phase == "before_export" else 1)
                self.assertEqual(self.provider.submissions, 0)

    def test_source_reference_geometry_is_checked_before_paid_submit(self):
        self._original_video({"request": {"frames_sha256": sha256_path(self.source / "frames.jsonl")}})
        original = self.enqueue_source()
        for index, dimensions in enumerate(((1280, 270), (256, 1000), (5762, 3000), (320, None))):
            with self.subTest(dimensions=dimensions):
                row = original if index == 0 else self.store.create(
                    "job-1", {**original["request"], "prompt_version": f"geometry-check-{index}"})
                def render_source(*args, **kwargs):
                    manifest = fake_source_renderer(*args, **kwargs)
                    manifest["video"].update(width=dimensions[0], height=dimensions[1])
                    manifest["crop"].update(width=dimensions[0], height=dimensions[1])
                    return manifest
                process_motion_replay(self.store, self.settings, provider_factory=lambda: self.provider,
                                      renderer=fake_renderer, source_renderer=render_source)
                self.assertEqual(self.store.get(row["id"])["error_code"], "reference_geometry_unsupported")
                self.assertEqual(self.provider.submissions, 0)

    def test_cached_source_reference_rejects_trajectory_path_outside_task_root(self):
        self._original_video({"request": {"frames_sha256": sha256_path(self.source / "frames.jsonl")}})
        row = self.enqueue_source()
        def missing():
            raise ProviderError("credentials_unavailable", "未配置", retryable=True)
        process_motion_replay(self.store, self.settings, provider_factory=missing, renderer=fake_renderer,
                              source_renderer=fake_source_renderer)
        rendered = self.store.get(row["id"])
        trajectory = self.settings.output_root / row["id"] / rendered["artifacts"]["trajectory"]
        outside = self.root / "trajectory.json"
        outside.write_bytes(trajectory.read_bytes())
        rendered["artifacts"]["trajectory"] = str(outside)
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET artifacts_json=? WHERE id=?",
                               (json.dumps(rendered["artifacts"]), row["id"]))
            connection.commit()
        self.store.retry(row["id"])
        self.tick()
        self.assertEqual(self.store.get(row["id"])["error_code"], "reconstruction_trajectory_changed")
        self.assertEqual(self.provider.submissions, 0)

    def test_source_mode_media_exposes_actual_input_and_redacts_private_prompt_snapshot(self):
        self._original_video({"request": {"frames_sha256": sha256_path(self.source / "frames.jsonl")}})
        def authorize(authorization: str | None = Header(None)):
            if authorization != "Bearer test":
                raise HTTPException(401)
        app = FastAPI()
        app.include_router(build_motion_replay_router(jobs=self.jobs, store=self.store,
                                                      settings=self.settings, authorize=authorize))
        headers = {"Authorization": "Bearer test", "Idempotency-Key": "source-mode"}
        with TestClient(app) as client:
            created = client.post("/v1/jobs/job-1/motion-replays", json={"start_ms": 0, "mode": "source_reconstruction"}, headers=headers)
            self.assertEqual(created.status_code, 202, created.text)
            self.assertIsNone(created.json()["media"]["motion_source_video"])
            self.tick()
            task = client.get(created.json()["links"]["self"], headers=headers).json()
            video = task["media"]["motion_source_video"]
            manifest_url = task["media"]["motion_source_manifest"]
            self.assertEqual(client.get(video).status_code, 401)
            self.assertEqual(client.get(manifest_url).status_code, 401)
            self.assertEqual(client.get(video, headers=headers).content, MP4 + b"original-rgb-crop")
            source_manifest = client.get(manifest_url, headers=headers)
            self.assertEqual(source_manifest.status_code, 200)
            self.assertEqual(source_manifest.json()["target_locator"]["bbox_xyxy"], [.2, .2, .6, .8])
            self.assertNotIn("submission_prompt_snapshot", source_manifest.text)
            self.assertNotIn("[TRACKED SUBJECT LOCATION]", source_manifest.text)
            self.assertNotIn(str(self.root), source_manifest.text)

    def test_legacy_generation_keeps_original_prompt_without_adding_image(self):
        row = self.enqueue()
        legacy_request = dict(row["request"])
        for key in ("appearance_reference", "appearance_reference_version", "appearance_style", "surface_renderer_version", "prompt_strategy"):
            legacy_request.pop(key)
        legacy_request["prompt"] = "Original saved legacy instructions."
        legacy_request["prompt_version"] = "white-mannequin-motion-v2"
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET request_json=? WHERE id=?", (json.dumps(legacy_request), row["id"]))
            connection.commit()
        with patch("rallymate_service.motion_replay_worker._render", wraps=fake_renderer) as renderer:
            process_motion_replay(self.store, self.settings, provider_factory=lambda: self.provider)
        self.assertEqual(renderer.call_args.kwargs["appearance_style"], "legacy_capsule")
        self.assertEqual(self.provider.submit_kwargs[0]["prompt"], legacy_request["prompt"])
        self.assertNotIn("reference_image", self.provider.submit_kwargs[0])
        stored = self.store.get(row["id"])
        self.assertEqual(stored["request"], legacy_request)
        self.assertEqual(public_motion_replay(stored, self.settings)["prompt_strategy"], "legacy")

    def test_changed_appearance_reference_fails_before_any_provider_submission(self):
        row = self.enqueue()
        def unavailable_provider():
            raise ProviderError("credentials_unavailable", "暂不可用", retryable=True)
        process_motion_replay(self.store, self.settings, provider_factory=unavailable_provider, renderer=fake_renderer)
        rendered = self.store.get(row["id"])
        image = self.settings.output_root / row["id"] / rendered["artifacts"]["first_frame"]
        image.write_bytes(b"replaced first frame")
        self.store.retry(row["id"])
        self.tick()
        stored = self.store.get(row["id"])
        self.assertEqual(stored["generation_status"], "failed")
        self.assertEqual(stored["error_code"], "appearance_reference_changed")
        self.assertEqual(self.provider.submissions, 0)

    def test_reference_strategy_and_prompt_strategy_are_part_of_content_identity(self):
        current = self.enqueue()
        legacy = dict(current["request"])
        for key in ("appearance_reference", "appearance_reference_version", "appearance_style", "surface_renderer_version"):
            legacy.pop(key)
        legacy_row = self.store.create("job-1", legacy)
        enhanced = {**current["request"], "prompt_strategy": "context_ir", "prompt": "Reviewed enhanced prompt."}
        enhanced_row = self.store.create("job-1", enhanced)
        self.assertEqual(len({current["id"], legacy_row["id"], enhanced_row["id"]}), 3)
        self.assertEqual(self.store.create("job-1", enhanced)["id"], enhanced_row["id"])
        self.assertEqual(public_motion_replay(enhanced_row, self.settings)["prompt_strategy"], "context_ir")

    def test_surface_renderer_version_invalidates_cache_and_never_submits_mismatched_render(self):
        current = self.enqueue()
        with patch("rallymate_vision.motion_replay.FULL_BODY_RENDERER_VERSION", "full-body-surface-next"):
            upgraded = self.enqueue()
            self.tick()  # Original queued snapshot must not silently use the new surface.
        self.assertNotEqual(upgraded["id"], current["id"])
        stored = self.store.get(current["id"])
        self.assertEqual(stored["generation_status"], "failed")
        self.assertEqual(stored["error_code"], "renderer_snapshot_mismatch")
        self.assertFalse(stored["artifacts"])
        self.assertEqual(self.provider.submissions, 0)

    def test_same_idempotency_key_survives_prompt_deployment_and_missing_source(self):
        row = self.enqueue(idempotency_key="deployment")
        old_request = dict(row["request"])
        old_request.pop("prompt")
        old_request.pop("client_request")
        old_request["prompt_version"] = "white-mannequin-motion-v1"
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET request_json=?,fingerprint='legacy-v1' WHERE id=?",
                               (json.dumps(old_request), row["id"]))
            connection.execute("UPDATE motion_replay_idempotency SET fingerprint='legacy-v1',client_request_json=NULL")
            connection.commit()
        (self.source / "frames.jsonl").unlink()
        original = self.enqueue(idempotency_key="deployment")
        self.assertEqual(original["id"], row["id"])
        self.assertEqual(original["request"]["prompt_version"], "white-mannequin-motion-v1")
        with self.assertRaises(ReplayConflict):
            enqueue_job_replay(jobs=self.jobs, store=self.store, settings=self.settings, job_id="job-1",
                               payload={"start_ms": 1000}, idempotency_key="deployment")

    def test_renderer_upgrade_creates_new_reference_without_duplicate_idempotent_submission(self):
        original = self.enqueue(idempotency_key="before-renderer-upgrade")
        with patch("rallymate_vision.motion_replay.MOTION_REPLAY_VERSION", "display-continuity-next"):
            repeated = self.enqueue(idempotency_key="before-renderer-upgrade")
            upgraded = self.enqueue(idempotency_key="after-renderer-upgrade")
            duplicate = self.enqueue()
        self.assertEqual(repeated["id"], original["id"])
        self.assertNotEqual(upgraded["id"], original["id"])
        self.assertEqual(duplicate["id"], upgraded["id"])
        self.assertEqual(upgraded["request"]["renderer_version"], "display-continuity-next")
        self.assertEqual(self.store.get(original["id"])["request"], original["request"])

    def test_deduplicated_window_keeps_each_idempotency_keys_distinct_client_request(self):
        explicit = self.enqueue(idempotency_key="explicit")
        with patch("rallymate_vision.motion_replay.select_motion_replay_window", return_value={"start_ms": 0}):
            automatic = enqueue_job_replay(jobs=self.jobs, store=self.store, settings=self.settings,
                                            job_id="job-1", idempotency_key="automatic")
        self.assertEqual(explicit["id"], automatic["id"])
        with patch("rallymate_vision.motion_replay.select_motion_replay_window", side_effect=AssertionError("must not reselect")):
            repeated = enqueue_job_replay(jobs=self.jobs, store=self.store, settings=self.settings,
                                           job_id="job-1", idempotency_key="automatic")
        self.assertEqual(repeated["id"], explicit["id"])
        with self.assertRaises(ReplayConflict):
            self.enqueue(idempotency_key="automatic")

    def test_client_intent_is_atomically_checked_even_when_window_content_deduplicates(self):
        row = self.enqueue(idempotency_key="racing-intents")
        request = dict(row["request"])
        request["client_request"] = {"duration_ms": 6000, "selection": "automatic"}
        with self.assertRaises(ReplayConflict):
            self.store.create("job-1", request, idempotency_key="racing-intents")

    def test_restart_resumes_remote_task_and_completion_does_not_approve_fidelity(self):
        row = self.enqueue()
        source_before = (self.source / "frames.jsonl").read_bytes()
        self.tick()
        first = self.store.get(row["id"])
        self.assertEqual(first["generation_status"], "provider_processing")
        self.assertEqual(first["provider_task_id"], "remote-123")
        public = public_motion_replay(first, self.settings)
        self.assertIsNotNone(public["media"]["reference_video"])
        self.assertIsNone(public["media"]["generated_video"])
        self.store = MotionReplayStore(self.settings.database_path)
        self.tick()
        self.tick()
        completed = self.store.get(row["id"])
        self.assertEqual(completed["generation_status"], "completed")
        self.assertEqual(completed["quality_status"], "pending_review")
        self.assertEqual(completed["generated_metadata"]["duration_difference_ms"], 583)
        self.assertEqual(self.provider.submissions, 1)
        self.assertEqual(self.provider.queries, ["remote-123"])
        self.assertEqual((self.source / "frames.jsonl").read_bytes(), source_before)
        self.assertEqual(self.jobs.get_job("job-1")["summary"], {"unchanged": True})

    def test_ambiguous_post_is_never_automatically_or_manually_retried(self):
        row = self.enqueue()
        self.provider.submit_error = ProviderError("submission_outcome_unknown", "提交未确认", submission_unknown=True)
        self.tick()
        self.assertEqual(self.store.get(row["id"])["generation_status"], "submit_unknown")
        with self.assertRaises(ReplayConflict):
            self.store.retry(row["id"])
        self.assertFalse(self.tick())
        self.assertEqual(self.provider.submissions, 1)

    def test_crash_after_submission_marker_becomes_unknown_and_fences_old_worker(self):
        row = self.enqueue()
        claim = self.store.claim_next("old", 60)
        self.store.transition(row["id"], claim["lease_token"], "submitting")
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET lease_expires_at=0")
            connection.commit()
        self.assertIsNone(self.store.claim_next("new", 60))
        self.assertEqual(self.store.get(row["id"])["generation_status"], "submit_unknown")
        with self.assertRaises(LeaseLost):
            self.store.transition(row["id"], claim["lease_token"], "completed")

    def test_expired_render_lease_recovers_and_old_worker_cannot_publish(self):
        row = self.enqueue()
        old = self.store.claim_next("old", 60)
        self.store.transition(row["id"], old["lease_token"], "rendering")
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET lease_expires_at=0")
            connection.commit()
        new = self.store.claim_next("new", 60)
        self.assertEqual(new["attempts"], 2)
        with self.assertRaises(LeaseLost):
            self.store.transition(row["id"], old["lease_token"], "queued", artifacts={"generated_video": "old.mp4"})
        self.assertFalse(self.store.renew(row["id"], old["lease_token"], 60))

    def test_rate_limit_retry_budget_is_finite(self):
        row = self.enqueue()
        self.provider.submit_error = ProviderError("provider_rate_limited", "繁忙", retryable=True, automatic_retry=True)
        for _ in range(3):
            self.tick()
        failed = self.store.get(row["id"])
        self.assertEqual(failed["generation_status"], "failed")
        self.assertEqual(failed["attempts"], 3)
        self.assertEqual(self.provider.submissions, 3)
        self.assertFalse(self.store.can_retry(failed))
        self.assertFalse(self.tick())

    def test_missing_credentials_retains_deterministic_reference_and_can_retry(self):
        row = self.enqueue()
        def missing():
            raise ProviderError("credentials_unavailable", "未配置凭据", retryable=True)
        process_motion_replay(self.store, self.settings, provider_factory=missing, renderer=fake_renderer)
        failed = self.store.get(row["id"])
        self.assertEqual(failed["generation_status"], "failed")
        self.assertTrue(self.store.can_retry(failed))
        self.assertIsNotNone(public_motion_replay(failed, self.settings)["media"]["reference_video"])
        self.store.retry(row["id"])
        self.tick()
        self.assertEqual(self.provider.submissions, 1)

    def test_provider_failure_needs_explicit_retry_and_preserves_task_history(self):
        row = self.enqueue()
        self.tick()
        self.provider.result_status = "failed"
        self.tick()
        self.assertFalse(self.tick())
        self.assertEqual(self.provider.submissions, 1)
        self.store.retry(row["id"])
        self.tick()
        self.assertEqual(self.provider.submissions, 2)
        self.assertEqual(self.store.get(row["id"])["provider_history"], ["remote-123", "remote-123"])

    def test_download_failure_refreshes_same_remote_task_without_resubmission(self):
        row = self.enqueue()
        self.tick()
        self.tick()
        self.provider.download_error = ProviderError("output_download_failed", "下载失败", retryable=True, automatic_retry=True)
        self.tick()
        self.assertEqual(self.store.get(row["id"])["generation_status"], "provider_processing")
        self.provider.download_error = None
        self.tick()
        self.tick()
        self.assertEqual(self.provider.submissions, 1)
        self.assertEqual(self.provider.queries, ["remote-123", "remote-123"])

    def test_intermittent_query_failures_reset_without_consuming_generation_budget(self):
        row = self.enqueue()
        self.tick()
        self.provider.result_status = "running"
        for _ in range(6):
            self.provider.query_error = ProviderError("provider_network_error", "查询失败", retryable=True, automatic_retry=True)
            self.tick()
            current = self.store.get(row["id"])
            self.assertEqual((current["attempts"], current["query_failures"]), (1, 1))
            self.provider.query_error = None
            self.tick()
            current = self.store.get(row["id"])
            self.assertEqual((current["generation_status"], current["query_failures"]), ("provider_processing", 0))
        self.provider.result_status = "succeeded"
        self.tick()
        self.tick()
        completed = self.store.get(row["id"])
        self.assertEqual(completed["generation_status"], "completed")
        self.assertEqual((completed["attempts"], self.provider.submissions), (1, 1))

    def test_consecutive_query_failures_stop_but_manual_resume_cannot_add_paid_budget(self):
        row = self.enqueue()
        # Reach the paid-generation limit through explicit failed-task retries.
        for attempt in range(1, 4):
            self.tick()
            self.assertEqual(self.store.get(row["id"])["attempts"], attempt)
            if attempt < 3:
                self.provider.result_status = "failed"
                self.tick()
                self.store.retry(row["id"])
        self.provider.query_error = ProviderError("provider_network_error", "查询失败", retryable=True, automatic_retry=True)
        for _ in range(3):
            self.tick()
        failed = self.store.get(row["id"])
        self.assertEqual((failed["generation_status"], failed["query_failures"]), ("failed", 3))
        self.assertEqual((failed["attempts"], self.provider.submissions), (3, 3))
        self.assertFalse(self.tick())
        self.assertTrue(self.store.can_retry(failed))  # Resume the same task, even at the paid limit.
        resumed = self.store.retry(row["id"])
        self.assertEqual((resumed["attempts"], resumed["query_failures"]), (3, 0))
        self.provider.query_error = None
        self.provider.result_status = "failed"
        self.tick()
        self.assertFalse(self.store.can_retry(self.store.get(row["id"])))
        with self.assertRaises(ReplayConflict):
            self.store.retry(row["id"])
        self.assertEqual(self.provider.submissions, 3)

    def test_repeated_download_failures_are_not_reset_by_successful_url_refresh(self):
        row = self.enqueue()
        self.tick()
        self.tick()
        self.provider.download_error = ProviderError("output_download_failed", "下载失败", retryable=True, automatic_retry=True)
        for count in range(1, 4):
            self.tick()
            current = self.store.get(row["id"])
            self.assertEqual((current["attempts"], current["download_failures"]), (1, count))
            if count < 3:
                self.tick()  # Successful query refreshes URL, not the download failure budget.
                self.assertEqual(self.store.get(row["id"])["download_failures"], count)
        self.assertEqual(self.store.get(row["id"])["generation_status"], "failed")
        self.assertFalse(self.tick())
        self.assertEqual(self.provider.submissions, 1)
        self.store.retry(row["id"])
        self.provider.download_error = None
        self.tick()
        self.tick()
        completed = self.store.get(row["id"])
        self.assertEqual((completed["generation_status"], completed["download_failures"], completed["attempts"]),
                         ("completed", 0, 1))

    def test_download_stage_obeys_pending_deadline(self):
        row = self.enqueue()
        self.tick()
        self.tick()
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET poll_deadline_at=0")
            connection.commit()
        with patch.object(self.provider, "download", side_effect=AssertionError("deadline must prevent downloading")):
            self.tick()
        failed = self.store.get(row["id"])
        self.assertEqual(failed["error_code"], "provider_pending_timeout")
        self.assertEqual((failed["attempts"], self.provider.submissions), (1, 1))

    def test_remote_lease_recovery_has_separate_bounded_budget(self):
        row = self.enqueue()
        self.tick()
        for count in range(1, 4):
            with self.store.connect() as connection:
                connection.execute("UPDATE motion_replays SET next_attempt_at=0")
                connection.commit()
            self.store.claim_next("old", 60)
            with self.store.connect() as connection:
                connection.execute("UPDATE motion_replays SET lease_expires_at=0")
                connection.commit()
            recovered = self.store.claim_next("new", 60)
            current = self.store.get(row["id"])
            self.assertEqual((current["attempts"], current["query_failures"]), (1, count))
            if count < 3:
                self.assertIsNotNone(recovered)
                self.store.transition(row["id"], recovered["lease_token"], "provider_processing", release=True)
            else:
                self.assertIsNone(recovered)
                self.assertEqual(current["generation_status"], "failed")
        self.assertEqual(self.provider.submissions, 1)

    def test_remote_failure_counter_migration_preserves_existing_records(self):
        row = self.enqueue()
        self.tick()
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET attempts=3")
            connection.execute("ALTER TABLE motion_replays DROP COLUMN query_failures")
            connection.execute("ALTER TABLE motion_replays DROP COLUMN download_failures")
            connection.commit()
        self.store.initialize()
        current = self.store.get(row["id"])
        self.assertEqual((current["query_failures"], current["download_failures"]), (0, 0))
        self.assertEqual((current["attempts"], current["generation_status"], current["provider_task_id"]),
                         (3, "provider_processing", "remote-123"))

    def test_changed_evidence_fails_before_provider_submission(self):
        row = self.enqueue()
        (self.source / "frames.jsonl").write_text("changed", encoding="utf-8")
        self.tick()
        self.assertEqual(self.store.get(row["id"])["error_code"], "source_evidence_changed")
        self.assertEqual(self.provider.submissions, 0)

    def test_pending_timeout_manual_retry_queries_existing_task(self):
        row = self.enqueue()
        self.tick()
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET poll_deadline_at=0")
            connection.commit()
        self.tick()
        self.assertEqual(self.store.get(row["id"])["error_code"], "provider_pending_timeout")
        self.store.retry(row["id"])
        self.tick()
        self.assertEqual(self.provider.submissions, 1)
        self.assertEqual(self.provider.queries, ["remote-123"])

    def test_api_auth_read_only_and_whitelisted_media(self):
        def authorize(authorization: str | None = Header(None)):
            if authorization != "Bearer test":
                raise HTTPException(401)
        app = FastAPI()
        app.include_router(build_motion_replay_router(jobs=self.jobs, store=self.store,
                                                     settings=self.settings, authorize=authorize))
        headers = {"Authorization": "Bearer test", "Idempotency-Key": "api-one"}
        with TestClient(app) as client:
            self.assertEqual(client.get("/v1/jobs/job-1/motion-replays").status_code, 401)
            created = client.post("/v1/jobs/job-1/motion-replays", json={"start_ms": 0}, headers=headers)
            self.assertEqual(created.status_code, 202, created.text)
            payload = created.json()
            replay_id = payload["id"]
            snapshot = self.store.get(replay_id)
            self.assertNotIn(str(self.root), created.text)
            self.assertNotIn("provider_task_id", payload)
            self.assertEqual(client.get(payload["links"]["self"], headers=headers).json(), payload)
            self.assertEqual(len(client.get("/v1/jobs/job-1/motion-replays", headers=headers).json()["items"]), 1)
            self.assertEqual(self.store.get(replay_id), snapshot)
            self.assertEqual(client.get(f"/v1/motion-replays/{replay_id}/media/generated.mp4", headers=headers).status_code, 404)
            self.tick()
            current = client.get(payload["links"]["self"], headers=headers).json()
            self.assertEqual(client.get(current["media"]["reference_video"], headers=headers).content, MP4)
            manifest = client.get(current["media"]["manifest"], headers=headers)
            self.assertNotIn(str(self.root), manifest.text)
            self.assertNotIn("frames_path", manifest.json()["source"])
            self.assertEqual(client.get(f"/v1/motion-replays/{replay_id}/media/request.json", headers=headers).status_code, 404)
            # A corrupted DB path cannot use the media endpoint to read outside its task.
            outside = self.root / "first.png"
            outside.write_bytes(b"private")
            with self.store.connect() as connection:
                connection.execute("UPDATE motion_replays SET artifacts_json=? WHERE id=?",
                                   (json.dumps({"first_frame": str(outside)}), replay_id))
                connection.commit()
            self.assertEqual(client.get(f"/v1/motion-replays/{replay_id}/media/first.png", headers=headers).status_code, 404)
            for body in ({"start_ms": True}, {"start_ms": -1}, {"start_ms": 0, "duration_ms": 10000},
                         {"start_ms": 0, "episode_id": "x"}, {"prompt": "override"}, {"mode": []}, {"mode": "wrong"}):
                self.assertEqual(client.post("/v1/jobs/job-1/motion-replays", json=body, headers=headers).status_code, 422)
            self.assertEqual(client.post("/v1/jobs/job-1/motion-replays", content=b"x" * 4097, headers=headers).status_code, 413)

    def _original_video(self, row):
        from rallymate_service.motion_replay_store import sha256_path
        uploads = self.root / "uploads"
        uploads.mkdir(exist_ok=True)
        source = uploads / "job-1.mp4"
        source.write_bytes(MP4)
        summary = {"minimum_scoring_loop": {"provenance": {
            "video_path": str(source), "video_sha256": sha256_path(source),
            "frames_sha256": row["request"]["frames_sha256"]}}}
        with self.jobs.connect() as connection:
            connection.execute("UPDATE jobs SET video_path=?,summary_json=? WHERE id='job-1'",
                               (str(source), json.dumps(summary)))
            connection.commit()
        return source

    def test_original_media_is_authenticated_cached_source_window_and_does_not_mutate_replay(self):
        row = enqueue_job_replay(jobs=self.jobs, store=self.store, settings=self.settings, job_id="job-1",
                                 payload={"start_ms": 5400})
        source = self._original_video(row)
        def authorize(authorization: str | None = Header(None)):
            if authorization != "Bearer test":
                raise HTTPException(401)
        app = FastAPI()
        app.include_router(build_motion_replay_router(jobs=self.jobs, store=self.store,
                                                      settings=self.settings, authorize=authorize))
        headers = {"Authorization": "Bearer test"}
        def export(original, target, start_ms, duration_ms):
            self.assertEqual((original, start_ms, duration_ms), (source, 5400, 6000))
            target.write_bytes(MP4)
            return {"duration_ms": 6000}
        with TestClient(app) as client, patch("rallymate_service.motion_replay_source._export_clip", side_effect=export) as exporter:
            payload = client.get(f"/v1/motion-replays/{row['id']}", headers=headers).json()
            url = payload["media"]["original_video"]
            self.assertEqual(payload["semantics"]["original_playback_start_ms"], 0)
            exporter.assert_not_called()
            self.assertEqual(client.get(url).status_code, 401)
            exporter.assert_not_called()
            first = client.get(url, headers=headers)
            self.assertEqual((first.status_code, first.content), (200, MP4))
            self.assertEqual(first.headers["content-type"], "video/mp4")
            ranged = client.get(url, headers={**headers, "Range": "bytes=0-6"})
            self.assertEqual((ranged.status_code, ranged.content), (206, MP4[:7]))
            self.assertEqual(exporter.call_count, 1)
            self.assertEqual(self.store.get(row["id"]), row)
            self.assertEqual(self.provider.submissions, 0)
            source.write_bytes(b"changed-original")
            denied = client.get(url, headers=headers)
            self.assertEqual(denied.status_code, 409)
            self.assertNotIn(str(self.root), denied.text)
            self.assertEqual(exporter.call_count, 1)

    def test_original_export_rejects_changed_source_and_never_substitutes_annotated_video(self):
        from rallymate_service.motion_replay_source import SourceVideoError
        row = self.enqueue()
        source = self._original_video(row)
        (self.source / "annotated.mp4").write_bytes(MP4)
        def changed_export(original, target, *_):
            target.write_bytes(MP4)
            original.write_bytes(b"changed-during-export")
            return {"duration_ms": 6000}
        with patch("rallymate_service.motion_replay_source._export_clip", side_effect=changed_export):
            with self.assertRaises(SourceVideoError):
                source_video_path(row, self.jobs.get_job("job-1"), self.settings)
        self.assertFalse((self.settings.output_root / row["id"] / "source-window" / "source.mp4").exists())
        source.unlink()
        with patch("rallymate_service.motion_replay_source._export_clip") as exporter:
            with self.assertRaises(SourceVideoError):
                source_video_path(row, self.jobs.get_job("job-1"), self.settings)
            exporter.assert_not_called()

    def test_original_export_rejects_path_escape_and_wrong_analysis_provenance(self):
        from rallymate_service.motion_replay_source import SourceVideoError
        row = self.enqueue()
        source = self._original_video(row)
        job = self.jobs.get_job("job-1")
        outside = self.root / "job-1.mp4"
        outside.write_bytes(MP4)
        job["video_path"] = str(outside)
        with self.assertRaises(SourceVideoError):
            source_video_path(row, job, self.settings)
        job["video_path"] = str(source)
        job["summary"]["minimum_scoring_loop"]["provenance"]["frames_sha256"] = "0" * 64
        with self.assertRaises(SourceVideoError):
            source_video_path(row, job, self.settings)

    def test_actual_container_timing_is_recorded_without_retiming(self):
        class Capture:
            def __init__(self, count):
                self.remaining = count
            def isOpened(self):
                return True
            def read(self):
                self.remaining -= 1
                return (True, SimpleNamespace(shape=(768, 1368, 3))) if self.remaining >= 0 else (False, None)
            def release(self):
                pass
        with patch("rallymate_vision.quality.probe_video", return_value=SimpleNamespace(
                width=1368, height=768, fps=24.0, frame_count=158, duration_ms=6583)), \
                patch("cv2.VideoCapture", return_value=Capture(158)):
            metadata = _validate_output(self.root / "generated.mp4", 6)
        self.assertEqual(metadata["duration_difference_ms"], 583)
        self.assertEqual(metadata["timing_adjustment"], "none")
        self.assertFalse(metadata["fidelity_verified"])
        self.assertEqual(metadata["decoded_frame_count"], 158)
        with patch("rallymate_vision.quality.probe_video", return_value=SimpleNamespace(
                width=1368, height=768, fps=24.0, frame_count=158, duration_ms=6583)), \
                patch("cv2.VideoCapture", return_value=Capture(79)):
            with self.assertRaises(ProviderError) as context:
                _validate_output(self.root / "truncated.mp4", 6)
        self.assertEqual(context.exception.code, "output_decode_incomplete")
        self.assertTrue(context.exception.automatic_retry)

    def test_worker_survives_transient_queue_error(self):
        class Stop:
            stopped = False
            waits = []
            def is_set(self):
                return self.stopped
            def wait(self, timeout):
                self.waits.append(timeout)
        stop = Stop()
        calls = []
        def tick(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("private database path")
            stop.stopped = True
            return True
        with patch("rallymate_service.motion_replay_worker.process_motion_replay", side_effect=tick):
            run_motion_replay_worker(self.store, self.settings, stop_event=stop)
        self.assertEqual(len(calls), 2)
        self.assertEqual(stop.waits, [1.0])


class FakeResponse(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())
        self.headers = {}


class ProviderTests(unittest.TestCase):
    def test_prompt_snapshots_preserve_legacy_version_and_reject_unknown_fallbacks(self):
        self.assertEqual(request_prompt({"prompt_version": "white-mannequin-motion-v1"}), LEGACY_PROMPT_V1)
        self.assertEqual(request_prompt({"prompt_version": "white-mannequin-motion-v2", "prompt": PROMPT}), PROMPT)
        self.assertNotIn("tennis", PROMPT)
        self.assertNotIn("swing", PROMPT)
        self.assertIn("tennis", LEGACY_PROMPT_V1)
        for request in ({"prompt_version": "unknown"}, {"prompt_version": "white-mannequin-motion-v2"},
                        {"prompt_version": "white-mannequin-motion-v1", "prompt": None}):
            with self.assertRaises(ProviderError):
                request_prompt(request)

    def test_proxy_dns_exception_is_opt_in_official_host_and_benchmark_only(self):
        official = "https://video-product.cdn.minimax.io/output.mp4"
        for address in ("198.18.1.10", "2001:2::109"):
            with patch("rallymate_service.motion_replay_provider.socket.getaddrinfo",
                       return_value=[(2, 1, 6, "", (address, 443))]):
                with self.assertRaises(ProviderError):
                    _download_url(official)
                self.assertEqual(_download_url(official, allow_proxy_dns=True), official)
                with self.assertRaises(ProviderError):
                    _download_url("https://unrelated.example.com/output.mp4", allow_proxy_dns=True)
        for address in ("127.0.0.1", "10.0.0.1", "192.168.0.1", "::1", "fc00::1"):
            with patch("rallymate_service.motion_replay_provider.socket.getaddrinfo",
                       return_value=[(2, 1, 6, "", (address, 443))]):
                with self.assertRaises(ProviderError):
                    _download_url(official, allow_proxy_dns=True)

    def test_download_truncation_and_incomplete_read_are_retryable_and_remove_partial_files(self):
        class Response(io.BytesIO):
            def __init__(self, incomplete=False):
                super().__init__(MP4)
                self.headers = {"Content-Length": str(len(MP4) * 2)}
                self.incomplete = incomplete
            def read(self, amount=-1):
                if self.incomplete:
                    raise http.client.IncompleteRead(b"partial", 50)
                return super().read(amount)
        class Opener:
            incomplete = False
            def open(self, request, timeout):
                self.request = request
                return Response(self.incomplete)
        opener = Opener()
        provider = MiniMaxVideoProvider(api_key="secret", opener=opener)
        with tempfile.TemporaryDirectory() as directory, patch("rallymate_service.motion_replay_provider.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            path = Path(directory) / "generated.mp4"
            for incomplete in (False, True):
                opener.incomplete = incomplete
                with self.assertRaises(ProviderError) as context:
                    provider.download("https://cdn.example.com/output.mp4", path)
                self.assertTrue(context.exception.automatic_retry)
                self.assertFalse(path.exists())
                self.assertIsNone(opener.request.get_header("Authorization"))

    def test_official_reference_payload_and_v2_query_contract(self):
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                return FakeResponse({"task_id": "123"} if request.method == "POST" else {
                    "task": {"id": "123", "status": "succeeded", "content": {"url": "https://cdn.example.com/out.mp4"}}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.mp4"
            path.write_bytes(MP4)
            provider = MiniMaxVideoProvider(api_key="never-public", opener=Opener())
            task_id = provider.submit(path)
            self.assertEqual(provider.query(task_id)["status"], "succeeded")
        payload = json.loads(calls[0].data)
        video = payload["content"][1]
        self.assertEqual(video["role"], "reference_video")
        self.assertTrue(video["video_url"]["url"].startswith("data:video/mp4;base64,"))
        self.assertEqual(payload["model"], "MiniMax-H3")
        self.assertEqual((payload["resolution"], payload["duration"]), ("768P", 6))
        self.assertEqual(calls[0].full_url, "https://api.minimax.io/v2/video_generation")
        self.assertEqual(calls[1].full_url, "https://api.minimax.io/v2/query/video_generation/123")

    def test_network_post_and_missing_id_fail_closed_without_exposing_secrets(self):
        class BrokenOpener:
            def open(self, request, timeout):
                raise urllib.error.URLError("secret-token local C:/private/path")
        provider = MiniMaxVideoProvider(api_key="secret-token", opener=BrokenOpener())
        with self.assertRaises(ProviderError) as context:
            provider._json_request("POST", "/v2/video_generation", {})
        self.assertTrue(context.exception.submission_unknown)
        self.assertNotIn("secret-token", str(context.exception))
        with self.assertRaises(ProviderError) as context:
            provider._json_request("GET", "/v2/query/video_generation/123")
        self.assertTrue(context.exception.automatic_retry)
        self.assertFalse(context.exception.submission_unknown)

    def test_known_rate_limit_and_server_post_failure_have_different_retry_semantics(self):
        class ErrorOpener:
            status = 429
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, self.status, "secret", {}, io.BytesIO(b"secret"))
        opener = ErrorOpener()
        provider = MiniMaxVideoProvider(api_key="secret", opener=opener)
        with self.assertRaises(ProviderError) as context:
            provider._json_request("POST", "/v2/video_generation", {})
        self.assertTrue(context.exception.automatic_retry)
        opener.status = 500
        with self.assertRaises(ProviderError) as context:
            provider._json_request("POST", "/v2/video_generation", {})
        self.assertTrue(context.exception.submission_unknown)

    def test_download_rejects_private_address_without_network_or_authorization(self):
        class Opener:
            def open(self, request, timeout):
                raise AssertionError("private host should not be contacted")
        provider = MiniMaxVideoProvider(api_key="secret", opener=Opener())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProviderError):
                provider.download("https://127.0.0.1/private", Path(directory) / "out.mp4")


if __name__ == "__main__":
    unittest.main()
