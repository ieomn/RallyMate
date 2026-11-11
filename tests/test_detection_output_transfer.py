from types import SimpleNamespace
import unittest

import numpy as np

from rallymate_vision.inference import Yolo26Perception


class Tensor:
    def __init__(self, values, transfers):
        self.value, self.transfers = np.asarray(values, np.float32), transfers
        self.shape, self.ndim = self.value.shape, self.value.ndim
    def detach(self): return self
    def cpu(self):
        self.transfers.append(1)
        return self
    def tolist(self): return self.value.tolist()


class DetectionOutputTransferTests(unittest.TestCase):
    def test_boxes_transfer_preserves_coordinates_classes_confidence_and_track_layout(self):
        values = np.asarray([[-3.3214, 8.1234, 991.331, 452.1, .1512345, 0],
                             [30, 41, 50, 60, .6512345, 32],
                             [70, 80, 90, 100, .4012345, 38]], np.float32)
        names = {0: 'person', 32: 'sports ball', 38: 'tennis racket'}
        old_transfers = []
        old = SimpleNamespace(boxes=SimpleNamespace(
            xyxy=Tensor(values[:, :4], old_transfers),
            conf=Tensor(values[:, -2], old_transfers),
            cls=Tensor(values[:, -1], old_transfers)), names=names)
        expected = Yolo26Perception._decode_detections(old, 960, 448)
        self.assertEqual(len(old_transfers), 3)
        for data in (values, np.insert(values, 4, [101, 102, 103], axis=1)):
            transfers = []
            actual = SimpleNamespace(boxes=SimpleNamespace(data=Tensor(data, transfers)), names=names)
            self.assertEqual(Yolo26Perception._decode_detections(actual, 960, 448), expected)
            self.assertEqual(len(transfers), 1)
        self.assertEqual(expected[0]['bbox_px'], [0.0, 8.123, 960.0, 448.0])

    def test_empty_result_still_keeps_one_cpu_completion_boundary(self):
        transfers = []
        result = SimpleNamespace(boxes=SimpleNamespace(data=Tensor(np.empty((0, 6)), transfers)), names={})
        self.assertEqual(Yolo26Perception._decode_detections(result, 960, 448), [])
        self.assertEqual(len(transfers), 1)



if __name__ == '__main__':
    unittest.main()
