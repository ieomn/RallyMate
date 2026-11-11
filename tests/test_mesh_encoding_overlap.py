from fractions import Fraction
import hashlib
from pathlib import Path
import tempfile
from threading import Event, get_ident
import unittest

import numpy as np

from rallymate_vision.mesh_prepare import MeshEncodingQueue, encode_overlap_enabled
from rallymate_vision.owned_subprocess import ProcessTreeCleanupError
from rallymate_vision.sam3d_mesh import _open_video_outputs


def pixels(index):
    return (np.full((8, 10, 3), index, np.uint8), np.full((8, 10, 3), 255-index, np.uint8))


class MeshEncodingOverlapTests(unittest.TestCase):
    def test_default_enabled_and_invalid_value_rejected(self):
        self.assertTrue(encode_overlap_enabled({}))
        self.assertFalse(encode_overlap_enabled({'RALLYMATE_SAM3D_MESH_ENCODE_OVERLAP': '0'}))
        with self.assertRaises(ValueError):
            encode_overlap_enabled({'RALLYMATE_SAM3D_MESH_ENCODE_OVERLAP': 'true'})

    def test_active_encode_overlaps_producer_but_fifo_and_pixel_bound_are_retained(self):
        entered, release = Event(), Event()
        observed, owner = [], get_ident()
        def encode(images, pts):
            self.assertNotEqual(get_ident(), owner)
            self.assertTrue(all(not image.flags.writeable for image in images))
            if pts == 0:
                entered.set()
                self.assertTrue(release.wait(2))
            observed.append((pts, int(images[0][0, 0, 0]), int(images[1][0, 0, 0])))
        queue = MeshEncodingQueue(encode, enabled=True)
        try:
            queue.submit(pixels(0), 0)
            self.assertTrue(entered.wait(1))
            # Returns while the previous codec work is still active.
            queue.submit(pixels(1), 3000)
            self.assertEqual(queue._pending_frames, 2)
            self.assertEqual(observed, [])
            release.set()
            for i in range(2, 15):
                queue.submit(pixels(i), 3000*i)
            queue.finish()
        finally:
            release.set()
            queue.close()
        self.assertEqual(observed, [(3000*i, i, 255-i) for i in range(15)])
        receipt = queue.receipt
        self.assertEqual(receipt['encoded_frames'], 15)
        self.assertEqual(receipt['peak_pending_frames'], 2)
        self.assertLessEqual(receipt['peak_pending_pixel_bytes'], 2*8*10*3*2)

    def test_oversize_pixels_run_alone_without_resize_or_drop(self):
        calls = []
        queue = MeshEncodingQueue(lambda images, pts: calls.append((images[0].shape, pts)),
            enabled=True, max_pending_bytes=1)
        for i in range(8):
            queue.submit(pixels(i), i)
        queue.finish()
        self.assertEqual(calls, [((8, 10, 3), i) for i in range(8)])
        self.assertEqual(queue.receipt['peak_pending_frames'], 1)
        self.assertEqual(queue.receipt['oversize_pairs_run_alone'], 8)

    def test_codec_failure_is_observed_before_publication_and_thread_joins(self):
        def encode(images, pts):
            raise OSError('codec write failed')
        queue = MeshEncodingQueue(encode, enabled=True)
        queue.submit(pixels(0), 0)
        with self.assertRaisesRegex(OSError, 'codec write failed'):
            queue.finish()
        queue.close()
        self.assertFalse(queue._thread.is_alive())
        self.assertEqual(queue.receipt['encoded_frames'], 0)

    def test_cancelled_wait_requires_join_before_codec_resources_can_close(self):
        entered, release = Event(), Event()
        def encode(images, pts):
            entered.set()
            release.wait(2)
        queue = MeshEncodingQueue(encode, enabled=True, join_timeout_seconds=.01)
        queue.submit(pixels(0), 0)
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(ProcessTreeCleanupError):
                queue.close()
            with self.assertRaisesRegex(RuntimeError, 'Join'):
                _ = queue.receipt
        finally:
            release.set()
            queue._thread.join(1)
            queue.close()
        self.assertTrue(queue.receipt['joined'])

    def test_both_real_encoded_files_and_decoded_pts_pixels_match_serial(self):
        import av
        pts_values = [0, 3000, 7500, 10500, 19500, 24000]
        base = Fraction(1, 90000)
        rng = np.random.default_rng(735)
        frames = [(rng.integers(0, 256, (48, 64, 3), dtype=np.uint8),
                   rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)) for _ in pts_values]
        with tempfile.TemporaryDirectory() as directory:
            artifacts = []
            for enabled in (False, True):
                paths = [Path(directory)/f'{enabled}-{view}.mp4' for view in ('front', 'side')]
                outputs, _ = _open_video_outputs(av, paths, width=64, height=48, rate=30,
                    time_base=base, preference='libx264', thread_count=2)
                def encode(images, pts):
                    for image, (container, stream) in zip(images, outputs):
                        frame = av.VideoFrame.from_ndarray(image, format='bgr24')
                        frame.pts, frame.time_base = pts, base
                        for packet in stream.encode(frame):
                            container.mux(packet)
                queue = MeshEncodingQueue(encode, enabled=enabled)
                try:
                    for images, pts in zip(frames, pts_values):
                        queue.submit(images, pts)
                    queue.finish()
                    for container, stream in outputs:
                        for packet in stream.encode():
                            container.mux(packet)
                finally:
                    queue.close()
                    for container, _ in outputs:
                        container.close()
                for path in paths:
                    with av.open(str(path)) as container:
                        actual = [(frame.pts*frame.time_base,
                                   hashlib.sha256(frame.to_ndarray(format='bgr24')).hexdigest())
                                  for frame in container.decode(video=0)]
                    self.assertEqual([pts for pts, _ in actual], [pts*base for pts in pts_values])
                    artifacts.append((path.read_bytes(), actual))
            self.assertEqual(artifacts[:2], artifacts[2:])


if __name__ == '__main__':
    unittest.main()
