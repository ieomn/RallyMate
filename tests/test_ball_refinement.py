from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import numpy as np
from rallymate_vision.inference import Yolo26Perception


class Array:
    def __init__(self, values): self.values = values
    def detach(self): return self
    def cpu(self): return self
    def tolist(self): return self.values


def result(boxes, classes, confidence):
    return SimpleNamespace(names={0: 'person', 32: 'sports ball', 38: 'tennis racket'},
                           boxes=SimpleNamespace(xyxy=Array(boxes), cls=Array(classes), conf=Array(confidence)))


class BallRefinementTests(unittest.TestCase):
    def perception(self, outputs):
        model = Yolo26Perception.__new__(Yolo26Perception)
        model.detect_imgsz = 960
        model.detect_confidence = .15
        model.ball_refinement_imgsz = 1536
        model.device = '0'
        model.target_class_ids = [0, 32, 38]
        model.detect_model = Mock(names={0:'person',32:'sports ball',38:'tennis racket'})
        model.detect_model.predict.side_effect = [[output] for output in outputs]
        return model

    def test_hd_missing_ball_is_recovered_without_changing_player_or_racket(self):
        model = self.perception([
            result([[100,100,200,300],[120,120,150,170]], [0,38], [.9,.8]),
            result([[500,250,512,262]], [32], [.45]),
        ])
        rows = model.detect(np.zeros((1080,1920,3),dtype=np.uint8))
        self.assertEqual({x['class_name'] for x in rows}, {'player','racket','ball'})
        self.assertEqual(next(x for x in rows if x['class_name']=='player')['bbox_px'], [100,100,200,300])
        self.assertEqual(model.detect_model.predict.call_args_list[1].kwargs['classes'], [32])
        self.assertEqual(model.detect_model.predict.call_args_list[1].kwargs['imgsz'], 1536)

    def test_multiscale_same_ball_is_counted_once_and_separate_balls_remain(self):
        model = self.perception([
            result([[100,100,114,114]], [32], [.3]),
            result([[101,101,113,113],[400,400,414,414]], [32,32], [.6,.5]),
        ])
        rows=model.detect(np.zeros((1080,1920,3),dtype=np.uint8))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['confidence'],.6)

    def test_low_resolution_and_already_high_resolution_use_one_pass(self):
        for shape, size in [((544,960,3),960), ((1080,1920,3),1536)]:
            with self.subTest(shape=shape,size=size):
                model=self.perception([result([],[],[])])
                model.detect_imgsz=size
                self.assertEqual(model.detect(np.zeros(shape,dtype=np.uint8)),[])
                self.assertEqual(model.detect_model.predict.call_count,1)

    def test_refinement_can_be_disabled_and_handles_empty_base_output(self):
        for enabled in (None,1536):
            with self.subTest(enabled=enabled):
                base=SimpleNamespace(boxes=None)
                outputs=[base] if enabled is None else [base,result([[100,100,110,110]],[32],[.5])]
                model=self.perception(outputs); model.ball_refinement_imgsz=enabled
                rows=model.detect(np.zeros((1920,1080,3),dtype=np.uint8))
                self.assertEqual(len(rows),0 if enabled is None else 1)

    def test_refinement_excludes_large_signs_but_keeps_motion_blurred_small_ball(self):
        model = self.perception([
            result([[800,100,860,155]], [32], [.7]),
            result([[100,100,158,155], [200,100,218,135], [300,100,310,180]], [32,32,32], [.6,.4,.5]),
        ])
        rows = model.detect(np.zeros((1080,1920,3),dtype=np.uint8))
        self.assertEqual([row['bbox_px'] for row in rows], [[800,100,860,155], [200,100,218,135]])

    def test_empty_refinement_preserves_adjacent_base_detections(self):
        model = self.perception([result([[100,100,114,114],[106,100,120,114]], [32,32], [.6,.5]), result([],[],[])])
        self.assertEqual(len(model.detect(np.zeros((1080,1920,3),dtype=np.uint8))), 2)

    def test_nested_boxes_with_different_scales_are_not_forced_into_one_ball(self):
        model = self.perception([result([[100,100,140,140]], [32], [.3]), result([[110,110,120,120]], [32], [.6])])
        self.assertEqual(len(model.detect(np.zeros((1080,1920,3),dtype=np.uint8))), 2)


if __name__=='__main__': unittest.main()
