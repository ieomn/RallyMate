from types import SimpleNamespace
import unittest

import numpy as np

from rallymate_vision.inference import Yolo26Perception


class Array:
    def __init__(self, values):
        self.values = values

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.values


class Detector:
    names = {0: "person", 32: "sports ball", 38: "tennis racket"}

    def __init__(self):
        self.calls = []
        self.drop_result = False

    def predict(self, source, **kwargs):
        frames = source if isinstance(source, list) else [source]
        self.calls.append((frames, kwargs))
        rows = []
        for frame in frames:
            marker = int(frame[0, 0, 0])
            if kwargs["classes"] == [32]:
                boxes, classes, confidence = [[300 + marker, 300, 310 + marker, 310]], [32], [.6]
            elif marker % 2:
                boxes, classes, confidence = [[100 + marker, 100, 110 + marker, 110]], [32], [.7]
            else:
                boxes, classes, confidence = [[100 + marker, 100, 200 + marker, 300]], [0], [.9]
            rows.append(SimpleNamespace(
                names=self.names,
                boxes=SimpleNamespace(xyxy=Array(boxes), cls=Array(classes), conf=Array(confidence)),
                speed={"preprocess": 1., "inference": 2., "postprocess": 3.},
            ))
        return rows[:-1] if self.drop_result else rows


def perception():
    model = Yolo26Perception.__new__(Yolo26Perception)
    model.detect_imgsz = 960
    model.detect_confidence = .15
    model.ball_refinement_imgsz = 1536
    model.device = "0"
    model.target_class_ids = [0, 32, 38]
    model.detect_model = Detector()
    return model


def frame(marker, shape=(720, 1280, 3)):
    image = np.zeros(shape, dtype=np.uint8)
    image[0, 0, 0] = marker
    return image


class DetectionBatchingTests(unittest.TestCase):
    def test_matches_single_frames_and_batches_only_frames_needing_refinement(self):
        frames = [frame(index) for index in range(4)]
        expected = [perception().detect(image) for image in frames]
        model = perception()
        self.assertEqual(model.detect_batch(frames), expected)
        self.assertEqual([len(call[0]) for call in model.detect_model.calls], [4, 2])
        self.assertEqual([int(image[0, 0, 0]) for image in model.detect_model.calls[1][0]], [0, 2])
        self.assertEqual(model.detect_model.calls[1][1]["imgsz"], 1280)
        self.assertEqual(model.detect_model.calls[1][1]["conf"], .35)
        self.assertEqual(len(model.last_detection_timings), 4)
        self.assertAlmostEqual(
            sum(row["total_wall_ms"] for row in model.last_detection_timings),
            model.last_detection_batch_timing["total_wall_ms"],
        )
        self.assertTrue(all(row["base_inference_ms"] == 2. for row in model.last_detection_timings))

    def test_mixed_dimensions_preserve_rectangular_padding_and_original_order(self):
        frames = [frame(0), frame(1, (1080, 1920, 3)), frame(2), frame(3, (480, 640, 3))]
        expected = [perception().detect(image) for image in frames]
        model = perception()
        self.assertEqual(model.detect_batch(frames), expected)
        self.assertEqual([len(call[0]) for call in model.detect_model.calls], [2, 1, 1, 2, 1])
        for images, _ in model.detect_model.calls:
            self.assertEqual(len({image.shape for image in images}), 1)
        self.assertEqual(model.last_detection_batch_timing["base_calls"], 3)
        self.assertEqual(model.last_detection_batch_timing["refinement_calls"], 2)

    def test_empty_batch_makes_no_model_call_and_clears_last_timing(self):
        model = perception()
        model.detect(frame(1))
        model.detect_model.calls.clear()
        self.assertEqual(model.detect_batch([]), [])
        self.assertEqual(model.detect_model.calls, [])
        self.assertEqual(model.last_detection_timings, [])
        self.assertEqual(model.last_detection_timing, {})

    def test_single_frame_keeps_model_input_and_detection_contract(self):
        model = perception()
        image = frame(1)
        result = model.detect_batch([image])
        self.assertEqual(result, [perception().detect(image)])
        self.assertEqual(len(model.detect_model.calls), 1)
        self.assertIs(model.detect_model.calls[0][0][0], image)
        self.assertEqual(model.last_detection_timing, model.last_detection_timings[0])

    def test_rejects_unbounded_or_invalid_inputs_before_any_gpu_call(self):
        for images in ([frame(1)] * 17, frame(1), [np.zeros((2, 2), dtype=np.uint8)],
                       [np.zeros((0, 2, 3), dtype=np.uint8)], [frame(1).astype(np.float32)]):
            with self.subTest(kind=type(images)):
                model = perception()
                with self.assertRaises(ValueError):
                    model.detect_batch(images)
                self.assertEqual(model.detect_model.calls, [])

    def test_missing_result_fails_instead_of_rebinding_later_frames(self):
        model = perception()
        model.detect_model.drop_result = True
        with self.assertRaisesRegex(ValueError, "exactly one result per source frame"):
            model.detect_batch([frame(1), frame(3)])


if __name__ == "__main__":
    unittest.main()
