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

    def test_720p_missing_ball_uses_native_size_and_stricter_confidence(self):
        model = self.perception([result([[100,100,200,300]], [0], [.9]),
                                 result([[500,250,510,260]], [32], [.45])])
        rows = model.detect(np.zeros((720,1280,3),dtype=np.uint8))
        self.assertEqual({row['class_name'] for row in rows}, {'player', 'ball'})
        options = model.detect_model.predict.call_args_list[1].kwargs
        self.assertEqual(options['imgsz'], 1280)
        self.assertEqual(options['conf'], .35)
        self.assertEqual(options['classes'], [32])

    def test_720p_confident_ball_skips_extra_candidates(self):
        model = self.perception([result([[100,100,110,110]], [32], [.5])])
        rows = model.detect(np.zeros((720,1280,3),dtype=np.uint8))
        self.assertEqual(len(rows), 1)
        self.assertEqual(model.detect_model.predict.call_count, 1)

    def test_720p_weak_ball_can_be_refined_but_never_forced_to_one_identity(self):
        model = self.perception([result([[100,100,110,110]], [32], [.3]),
                                 result([[100,100,110,110],[400,400,411,411]], [32,32], [.6,.4])])
        rows = model.detect(np.zeros((720,1280,3),dtype=np.uint8))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['track_id'] is None for row in rows))
        self.assertEqual(rows[0]['confidence'], .6)

    def test_native_refinement_keeps_configuration_disable_and_source_size_cap(self):
        cases = [((720,1280,3),None,960,None), ((720,1366,3),1536,960,1344),
                 ((1280,720,3),1536,960,1280), ((540,1280,3),1536,960,None),
                 ((720,1280,3),1536,1280,None)]
        for shape, configured, base_size, expected in cases:
            with self.subTest(shape=shape,configured=configured,base_size=base_size):
                model=self.perception([result([],[],[])] * (2 if expected else 1))
                model.ball_refinement_imgsz=configured
                model.detect_imgsz=base_size
                model.detect(np.zeros(shape,dtype=np.uint8))
                self.assertEqual(model.detect_model.predict.call_count,2 if expected else 1)
                if expected:
                    self.assertEqual(model.detect_model.predict.call_args_list[1].kwargs['imgsz'],expected)

    def test_native_refinement_preserves_higher_requested_confidence(self):
        model=self.perception([result([],[],[]),result([],[],[])])
        model.detect_confidence=.6
        model.detect(np.zeros((720,1280,3),dtype=np.uint8))
        self.assertEqual(model.detect_model.predict.call_args_list[1].kwargs['conf'],.6)

    def test_native_refinement_rejects_low_confidence_extra_even_if_backend_returns_it(self):
        model=self.perception([result([],[],[]),
                               result([[100,100,110,110],[200,200,210,210]], [32,32], [.349,.35])])
        rows=model.detect(np.zeros((720,1280,3),dtype=np.uint8))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['confidence'],.35)

    def test_hd_keeps_extra_pass_even_with_confident_base_ball(self):
        model=self.perception([result([[100,100,110,110]],[32],[.8]),result([],[],[])])
        model.detect(np.zeros((1080,1920,3),dtype=np.uint8))
        options=model.detect_model.predict.call_args_list[1].kwargs
        self.assertEqual(options['imgsz'],1536)
        self.assertEqual(options['conf'],.25)

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
