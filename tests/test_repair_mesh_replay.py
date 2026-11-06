"""Repair provenance/publication tests; fake renderer deliberately avoids GPU."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import repair_mesh_replay as repair
from rallymate_vision.mesh_render_task import TASK_NAME
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_mesh import LEDGER_NAME, MANIFEST_NAME


class RepairMeshReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / "runs" / "job-1"
        self.run.mkdir(parents=True)
        self.staging = self.root / "staging"
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"original-source-video")
        geometry = self.run / "sam3d-mesh-data"
        geometry.mkdir()
        (geometry / "faces.npy").write_bytes(b"original-shared-topology")
        rows = []
        for frame, track in ((0, 1), (0, 3), (1, 1)):
            name = f"{frame}-{track}.mesh.npz"
            (geometry / name).write_bytes(f"original-geometry-{frame}-{track}".encode())
            rows.append({"frame_index": frame, "person_track_id": track, "selection_epoch": 0,
                "video_sha256": sha256_file(self.source), "mesh_artifact": {
                    "path": f"sam3d-mesh-data/{name}", "sha256": sha256_file(geometry / name),
                    "faces_path": "sam3d-mesh-data/faces.npy", "faces_sha256": sha256_file(geometry / "faces.npy")}})
        (self.run / LEDGER_NAME).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        for name in ("frames.jsonl", "sam3d_frames.jsonl", "scores.jsonl"):
            (self.run / name).write_text(f"original evidence {name}", encoding="utf-8")
        for name in repair.PUBLISHED:
            (self.run / name).write_bytes(f"original {name}".encode())
        task = {"job_id": self.run.name, "ledger_sha256": sha256_file(self.run / LEDGER_NAME),
            "render": {"source": str(self.source), "output_dir": str(self.run),
                "runtime_dir": str(self.root / "runtime"), "first_frame": 0, "last_frame": 1,
                "stride": 1, "source_sha256": sha256_file(self.source), "model": {"backend": "sam3d"}}}
        (self.run / TASK_NAME).write_text(json.dumps(task), encoding="utf-8")
        self.decisions = self.root / "decisions.json"
        self.selection = {"version": "sam3d-replay-selection-v1",
            "source_video_sha256": sha256_file(self.source),
            "inputs": {name: sha256_file(self.run / name) for name in repair.INPUTS},
            "keep": [{key: row[key] for key in ("frame_index", "person_track_id", "selection_epoch")}
                     for row in rows if row["person_track_id"] == 1]}
        self.write_selection()
        self.before = {path.relative_to(self.run): path.read_bytes() for path in self.run.rglob("*") if path.is_file()}

    def write_selection(self):
        self.decisions.write_text(json.dumps(self.selection), encoding="utf-8")

    def prepare(self):
        return repair.prepare(self.run, self.decisions, self.staging)

    def fake_renderer(self, **arguments):
        arguments["check_cancelled"]()
        output = arguments["output_dir"]
        for name in repair.MEDIA:
            (output / name).write_bytes(f"repaired {name}".encode())
        manifest = {"status": "ready", "timing_preserved": True,
            "source_video_sha256": arguments["source_sha256"],
            "ledger": LEDGER_NAME, "ledger_sha256": sha256_file(output / LEDGER_NAME),
            "files": {name: {"sha256": sha256_file(output / name), "bytes": (output / name).stat().st_size}
                      for name in repair.MEDIA}}
        (output / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def render(self):
        self.prepare()
        return repair.render(self.staging, renderer=self.fake_renderer,
            check_idle=lambda: None, verify_pts=lambda *_: None)

    def assert_originals(self):
        for name, content in self.before.items():
            self.assertEqual((self.run / name).read_bytes(), content, str(name))

    def test_prepare_filters_exact_epoch_keys_and_leaves_evidence_and_media_untouched(self):
        plan = self.prepare()
        self.assertEqual(plan["original_subject_records"], 3)
        self.assertEqual(plan["selected_subject_records"], 2)
        rows = [json.loads(line) for line in (self.staging / LEDGER_NAME).read_text().splitlines()]
        self.assertEqual([(row["frame_index"], row["person_track_id"]) for row in rows], [(0, 1), (1, 1)])
        self.assertFalse((self.staging / "sam3d-mesh-data/0-3.mesh.npz").exists())
        self.assert_originals()

    def test_stale_or_ambiguous_selection_fails_before_staging(self):
        self.selection["keep"][0]["selection_epoch"] = 1
        self.write_selection()
        with self.assertRaisesRegex(ValueError, "uniquely"):
            self.prepare()
        self.assertFalse(self.staging.exists())
        self.selection["keep"][0]["selection_epoch"] = 0
        self.selection["keep"].append(dict(self.selection["keep"][0]))
        self.write_selection()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.prepare()
        self.assert_originals()

    def test_changed_source_and_nested_staging_are_refused(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            repair.prepare(self.run, self.decisions, self.run / "staging")
        self.selection["inputs"]["frames.jsonl"] = "a" * 64
        self.write_selection()
        with self.assertRaisesRegex(ValueError, "input changed"):
            self.prepare()
        self.assertFalse(self.staging.exists())
        self.assert_originals()

    def test_render_records_verification_without_publishing(self):
        self.prepare()
        calls = []
        receipt = repair.render(self.staging, renderer=self.fake_renderer,
            check_idle=lambda: None, verify_pts=lambda staging, args: calls.append((staging, args)))
        self.assertEqual(len(calls), 1)
        self.assertTrue(receipt["exact_source_pts_verified"])
        self.assertFalse(receipt["inference_rerun"])
        self.assert_originals()

    def test_failed_pts_validation_never_creates_publish_receipt(self):
        self.prepare()
        def invalid(*_):
            raise ValueError("wrong PTS")
        with self.assertRaisesRegex(ValueError, "wrong PTS"):
            repair.render(self.staging, renderer=self.fake_renderer, check_idle=lambda: None, verify_pts=invalid)
        self.assertFalse((self.staging / repair.RENDER_RECEIPT).exists())
        self.assert_originals()

    def test_real_video_pts_check_catches_dropped_or_shifted_frames(self):
        import av
        import numpy as np

        def video(path, timestamps):
            with av.open(str(path), "w") as container:
                stream = container.add_stream("libx264", rate=30)
                stream.width = stream.height = 16
                stream.pix_fmt = "yuv420p"
                stream.time_base = stream.codec_context.time_base = Fraction(1, 30)
                stream.codec_context.max_b_frames = 0
                stream.codec_context.thread_count = 1
                for timestamp in timestamps:
                    frame = av.VideoFrame.from_ndarray(np.zeros((16, 16, 3), np.uint8), format="bgr24")
                    frame.pts, frame.time_base = timestamp, Fraction(1, 30)
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)

        self.staging.mkdir()
        video(self.source, [10, 11, 14, 18])
        for name in (repair.VIDEO_NAME, repair.SIDE_VIDEO_NAME):
            video(self.staging / name, [0, 7])
        arguments = {"source": str(self.source), "first_frame": 1, "last_frame": 3, "stride": 2}
        repair._verify_pts(self.staging, arguments)
        video(self.staging / repair.SIDE_VIDEO_NAME, [0, 6])
        with self.assertRaisesRegex(ValueError, "PTS differ"):
            repair._verify_pts(self.staging, arguments)

    def test_publish_retains_actual_filtered_ledger_hash_and_backups_original(self):
        self.render()
        result = repair.publish(self.staging, check_idle=lambda: None)
        self.assertTrue(result["published"])
        backup = Path(result["backup"])
        for name in repair.PUBLISHED:
            self.assertEqual((backup / name).read_bytes(), self.before[Path(name)])
        manifest = json.loads((self.run / MANIFEST_NAME).read_text())
        self.assertNotEqual(manifest["ledger"], LEDGER_NAME)
        self.assertEqual(manifest["ledger_sha256"], sha256_file(self.run / manifest["ledger"]))
        self.assertEqual(manifest["replay_repair"]["source_ledger_sha256"], sha256_file(self.run / LEDGER_NAME))
        for name, content in self.before.items():
            if str(name) not in repair.PUBLISHED:
                self.assertEqual((self.run / name).read_bytes(), content, str(name))

    def test_changed_staged_media_or_original_media_cannot_publish(self):
        self.render()
        (self.staging / repair.VIDEO_NAME).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "input changed"):
            repair.publish(self.staging, check_idle=lambda: None)
        self.assertFalse((self.run / "mesh-replay-backups").exists())
        self.assert_originals()

    def test_publication_failure_rolls_back_all_four_deliverables(self):
        self.render()
        original = repair.atomic_json
        def fail_target(path, value):
            if path == self.run / MANIFEST_NAME:
                raise OSError("fixture publication failure")
            return original(path, value)
        with patch.object(repair, "atomic_json", fail_target), self.assertRaisesRegex(OSError, "publication failure"):
            repair.publish(self.staging, check_idle=lambda: None)
        self.assert_originals()

    def test_busy_check_prevents_render_and_publish(self):
        self.prepare()
        def busy():
            raise RuntimeError("busy")
        with self.assertRaisesRegex(RuntimeError, "busy"):
            repair.render(self.staging, renderer=self.fake_renderer, check_idle=busy)
        self.assert_originals()


if __name__ == "__main__":
    unittest.main()
