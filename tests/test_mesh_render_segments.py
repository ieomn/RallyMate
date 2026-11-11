from collections import defaultdict
from fractions import Fraction
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import av
import numpy as np

from rallymate_vision import sam3d_mesh as mesh
from rallymate_vision import mesh_render_segments as segments_module
from rallymate_vision.mesh_render_segments import (
    CHECKPOINT_NAME, RESULT_NAME, SEGMENTS_NAME, render_mesh_resumable,
)
from rallymate_vision.pose.metadata import sha256_file


class FakeRenderer:
    rendered = 0
    closed = 0
    created = 0

    def __init__(self, *args):
        type(self).created += 1
        self.renderer_sha256 = "a" * 64
        self.timing_seconds = defaultdict(float)

    def prepare_pair(self, image, outputs):
        return image, outputs

    def render_prepared_pair(self, pair):
        type(self).rendered += 1
        self.timing_seconds["front_gl_render_and_readback"] += .25
        image, outputs = pair
        front = image.copy()
        if outputs:
            front[4:10, 4:10] = [130, 100, 90]
        return front, np.full_like(image, 245)

    def close(self):
        type(self).closed += 1


class MeshSegmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "job"
        self.output.mkdir()
        self.runtime = self.root / "runtime"
        self.source = self.root / "source.mp4"
        with av.open(str(self.source), "w") as container:
            stream = container.add_stream("libx264", rate=30)
            stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
            stream.time_base = stream.codec_context.time_base = Fraction(1, 90000)
            stream.codec_context.max_b_frames = 0
            stream.codec_context.gop_size = 2
            stream.options = {"sc_threshold": "0"}
            for i, pts in enumerate([90000, 93000, 97000, 102000, 105000, 110000, 113000, 119000]):
                frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), i*15, np.uint8), format="bgr24")
                frame.pts, frame.time_base = pts, Fraction(1, 90000)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        self.sha = sha256_file(self.source)
        with av.open(str(self.source)) as container:
            self.pts = [Fraction(frame.pts)*frame.time_base for frame in container.decode(video=0)]
        native = {"pred_vertices": np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]], np.float32),
                  "pred_cam_t": np.array([0., 0., 3.], np.float32), "focal_length": np.array(100.)}
        rows = []
        for index in (1, 7):
            artifact = mesh.capture_mesh_output(native, np.array([[0,1,2]], np.int32), self.runtime)
            pts = self.pts[index]
            rows.extend(mesh.archive_mesh_records([{"status": "ok", "mesh_artifact": artifact,
                "video_sha256": self.sha, "frame_index": index, "pts": pts.numerator,
                "time_base_num": 1, "time_base_den": pts.denominator, "image_size": [64,48]}],
                self.output, self.runtime))
        (self.output / mesh.LEDGER_NAME).write_text("".join(json.dumps(row)+"\n" for row in rows))
        environment = patch.dict(os.environ, {"RALLYMATE_SAM3D_MESH_SEGMENT_FRAMES": "2",
            "RALLYMATE_SAM3D_MESH_ENCODER": "libx264", "RALLYMATE_SAM3D_MESH_PREPARE_WORKERS": "0"})
        environment.start(); self.addCleanup(environment.stop)
        renderer = patch.object(mesh, "OfficialMeshRenderer", FakeRenderer)
        renderer.start(); self.addCleanup(renderer.stop)
        FakeRenderer.rendered = FakeRenderer.closed = FakeRenderer.created = 0

    def render(self, **kwargs):
        selection = {"first_frame": 0, "last_frame": 7, "stride": 1, **kwargs}
        return render_mesh_resumable(self.source, self.output, self.runtime,
            source_sha256=self.sha, model={"backend": "sam3d"}, **selection)

    def pixels_and_pts(self, path):
        with av.open(str(path)) as container:
            values = [(Fraction(frame.pts)*frame.time_base, frame.to_ndarray(format="bgr24"))
                      for frame in container.decode(video=0)]
        return values

    def test_fair_slice_commits_one_segment_then_resume_preserves_pts_and_encoded_pixels(self):
        first = self.render(time_slice_seconds=.000001)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["completed_frames"], 2)
        self.assertEqual(FakeRenderer.rendered, 2)
        self.assertFalse((self.output / mesh.MANIFEST_NAME).exists())
        final = self.render()
        self.assertEqual(final["status"], "ready")
        self.assertEqual(final["mesh_segments"]["resumed_frames"], 2)
        self.assertEqual(final["frame_count"], 8)
        self.assertEqual(final["mesh_frame_count"], 2)
        self.assertEqual(final["subject_count"], 2)
        self.assertEqual(final["preview_source_frame"], 1)
        self.assertEqual(FakeRenderer.rendered, 8)
        self.assertEqual(FakeRenderer.closed, 2)
        for name in (mesh.VIDEO_NAME, mesh.SIDE_VIDEO_NAME):
            actual = self.pixels_and_pts(self.output / name)
            self.assertEqual([pts for pts, _ in actual], [pts-self.pts[0] for pts in self.pts])
            chunks = []
            for number in range(4):
                chunks.extend(image for _, image in self.pixels_and_pts(self.output / SEGMENTS_NAME / f"{number:08d}" / name))
            for (_, actual_image), expected_image in zip(actual, chunks):
                np.testing.assert_array_equal(actual_image, expected_image)
        self.assertEqual(json.loads((self.output / RESULT_NAME).read_text())["status"], "ready")

    def test_cancel_keeps_completed_segments_and_retries_only_uncommitted_work(self):
        def cancel():
            if (self.output / CHECKPOINT_NAME).exists():
                raise InterruptedError("new inference")
        with self.assertRaisesRegex(InterruptedError, "new inference"):
            self.render(check_cancelled=cancel)
        self.assertEqual(FakeRenderer.rendered, 2)
        result = self.render()
        self.assertEqual(result["mesh_segments"]["resumed_frames"], 2)
        self.assertEqual(FakeRenderer.rendered, 8)

    def test_tampered_completed_media_is_rejected_before_more_rendering(self):
        self.render(time_slice_seconds=.000001)
        path = self.output / SEGMENTS_NAME / "00000000" / mesh.VIDEO_NAME
        with path.open("ab") as handle:
            handle.write(b"changed")
        with self.assertRaisesRegex(ValueError, "media changed"):
            self.render()
        self.assertEqual(FakeRenderer.rendered, 2)

    def test_changed_encoder_or_ledger_does_not_reuse_checkpoint(self):
        self.render(time_slice_seconds=.000001)
        with patch.dict(os.environ, {"RALLYMATE_SAM3D_MESH_ENCODER_THREADS": "3"}):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                self.render()
        with (self.output / mesh.LEDGER_NAME).open("a") as handle:
            handle.write("\n")
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            self.render()
        self.assertEqual(FakeRenderer.rendered, 2)

    def test_invalid_time_slice_fails_before_work(self):
        for seconds in (0, -1, float("nan"), float("inf")):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, "positive finite"):
                self.render(time_slice_seconds=seconds)
        self.assertEqual(FakeRenderer.rendered, 0)

    def test_one_turn_decodes_source_once_and_hashes_source_twice_for_all_segments(self):
        source_hashes = []
        def digest(path):
            if Path(path).resolve() == self.source.resolve():
                source_hashes.append(1)
            return sha256_file(path)
        with patch.object(mesh, "sha256_file", side_effect=digest), \
                patch.object(segments_module, "sha256_file", side_effect=digest), \
                patch.object(mesh, "_verified_geometry_bytes", wraps=mesh._verified_geometry_bytes) as geometry:
            result = self.render()
        self.assertEqual(result["mesh_segments"]["turn_source_decoded_frames"], 8)
        self.assertEqual(result["mesh_segments"]["turn_seek_prefix_frames"], 0)
        self.assertEqual(len(source_hashes), 2)
        self.assertEqual(result["mesh_prepare"]["consumed_frames"], 8)
        self.assertEqual(result["mesh_encode"]["encoded_frames"], 8)
        self.assertEqual(FakeRenderer.created, 1)
        self.assertEqual(FakeRenderer.closed, 1)
        self.assertEqual(result["stage_seconds"]["front_gl_render_and_readback"], 2.)
        self.assertEqual(sum(call.args[2] == "topology" for call in geometry.call_args_list), 1)
        self.assertLess((self.output / mesh.MANIFEST_NAME).stat().st_size, 16000)

    def test_resume_seeks_to_last_checkpoint_keyframe_instead_of_redecoding_prefix(self):
        self.render(time_slice_seconds=.000001)
        second = self.render(time_slice_seconds=.000001)
        self.assertEqual(second["completed_frames"], 4)
        result = self.render()
        self.assertEqual(result["mesh_segments"]["turn_seek_prefix_frames"], 2)
        self.assertEqual(result["mesh_segments"]["turn_source_decoded_frames"], 6)
        self.assertEqual(FakeRenderer.rendered, 8)

    def test_stride_and_nonzero_first_frame_resume_preserve_absolute_time_origin(self):
        self.render(first_frame=1, stride=2, time_slice_seconds=.000001)
        result = self.render(first_frame=1, stride=2)
        actual = self.pixels_and_pts(self.output / mesh.VIDEO_NAME)
        self.assertEqual([pts for pts, _ in actual], [pts-self.pts[1] for pts in self.pts[1::2]])
        self.assertEqual(Fraction(*result["source_pts_origin"]), self.pts[1])
        self.assertEqual(Fraction(*result["source_stream_origin"]), self.pts[0])
        self.assertEqual(result["source_frame_start"], 1)
        self.assertEqual(result["source_frame_end"], 8)
        self.assertEqual(result["frame_count"], 4)
        self.assertEqual(FakeRenderer.rendered, 4)

    def test_cancel_during_join_retries_join_without_rendering_completed_frames(self):
        with patch.object(segments_module, "_remux_parts", side_effect=InterruptedError("inference")):
            with self.assertRaisesRegex(InterruptedError, "inference"):
                self.render()
        self.assertEqual(FakeRenderer.rendered, 8)
        self.assertFalse((self.output / mesh.MANIFEST_NAME).exists())
        result = self.render()
        self.assertEqual(FakeRenderer.rendered, 8)
        self.assertEqual(result["mesh_segments"]["resumed_frames"], 8)
        self.assertEqual(result["mesh_segments"]["turn_source_decoded_frames"], 0)

    def test_unjoined_preparation_keeps_borrowed_gl_resources_alive(self):
        session = segments_module.SourceFrames(self.source)
        renderer = session.get_renderer(64, 48, self.runtime, FakeRenderer)
        with patch.object(mesh, "_UNJOINED_PREPARATIONS", [(renderer, object(), object(), [])]):
            session.close()
        self.assertEqual(FakeRenderer.closed, 0)
        self.assertIs(session.renderer, renderer)
        session.close()
        self.assertEqual(FakeRenderer.closed, 1)


if __name__ == "__main__":
    unittest.main()
