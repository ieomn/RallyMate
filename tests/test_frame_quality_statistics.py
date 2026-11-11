"""Keep published quality values and warning boundaries when reducing scans."""
import unittest

import cv2
import numpy as np

from rallymate_vision.quality import analyze_frame


def original_quality(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    contrast = float(gray.std())
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    warnings = []
    if brightness < 45:
        warnings.append('too_dark')
    elif brightness > 220:
        warnings.append('overexposed')
    if contrast < 25:
        warnings.append('low_contrast')
    if blur < 45:
        warnings.append('blurry')
    return dict(brightness=round(brightness, 3), contrast=round(contrast, 3),
                blur_score=round(blur, 3), status='warning' if warnings else 'ok', warnings=warnings)


class FrameQualityStatisticsTests(unittest.TestCase):
    def assert_original_quality(self, frame):
        self.assertEqual(analyze_frame(frame), original_quality(frame))

    def test_brightness_boundaries_and_constant_images(self):
        for value in (0, 44, 45, 46, 219, 220, 221, 255):
            for shape in ((1, 1, 3), (2, 7, 3), (448, 960, 3)):
                with self.subTest(value=value, shape=shape):
                    self.assert_original_quality(np.full(shape, value, dtype=np.uint8))

    def test_half_decimal_rounding_and_contrast_boundary(self):
        frame = np.full((40, 50, 3), 100, dtype=np.uint8)
        for increment in range(1, 30, 2):
            frame[0, 0] = 100 + increment
            with self.subTest(increment=increment):
                self.assert_original_quality(frame)
        contrast_boundary = np.zeros((40, 50, 3), dtype=np.uint8)
        contrast_boundary[:, ::2] = 50
        self.assert_original_quality(contrast_boundary)

    def test_seeded_color_noise_gradients_and_noncontiguous_views(self):
        rng = np.random.default_rng(20261109)
        for shape in ((2, 3, 3), (31, 33, 3), (448, 960, 3), (720, 1280, 3)):
            frame = rng.integers(0, 256, size=shape, dtype=np.uint8)
            for view in (frame, frame[:, ::2], frame[::-1]):
                with self.subTest(shape=shape, strides=view.strides):
                    self.assert_original_quality(view)
        gradient = np.repeat(np.arange(256, dtype=np.uint8)[None, :, None], 3, axis=2)
        self.assert_original_quality(np.repeat(gradient, 64, axis=0))

    def test_non_video_uint16_inputs_keep_original_reductions(self):
        rng = np.random.default_rng(70)
        self.assert_original_quality(rng.integers(0, 65536, (71, 123, 3), dtype=np.uint16))


if __name__ == '__main__':
    unittest.main()
