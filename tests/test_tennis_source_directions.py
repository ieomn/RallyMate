from copy import deepcopy
import unittest

import numpy as np

from rallymate_service.tennis_source_motion import bind_reviewed_directions, source_motion_by_frame
from tests.test_tennis_assessment import inputs
from tests.test_tennis_subject_scoring import source_frames


SHA = "a" * 64


def fixture():
    _, native = inputs()
    frames = source_frames(native)
    frames[3]["camera_motion"]["matrix_to_reference"] = [[0, -2, 300], [2, 0, -200]]
    observations = source_motion_by_frame(native, frames, SHA)
    row = next(r for r in observations.values() if r["frame"] == 3)
    context = {"coordinate_system": "fixed_camera_image_plane", "camera_reference_verified": True,
               "source_binding_verified": True, "video_sha256": SHA, "track": row["track"],
               "selection_epoch": row["selection_epoch"], "camera_reference_epoch": row["camera_reference_epoch"]}
    review = {"status": "reviewed", "method": "agent_visual_review", "reviewer": "test", "evidence": {"frames": [3]},
              "camera_reference_epoch": row["camera_reference_epoch"], "direction_reference_timestamp_ms": row["time"],
              "direction_basis": {"basis": "court_net_and_upright_reference", "coordinate_system": "image_pixels_x_right_y_down",
                                  "camera_reference_epoch": row["camera_reference_epoch"], "frames": [{"timestamp_ms": row["time"], "source_frame_index": 3}]},
              "forward_direction_2d": [1, 0], "forward_direction_2d_verified": True,
              "up_direction_2d": [0, -1], "up_direction_2d_verified": True}
    return review, observations, context


class SourceDirectionTests(unittest.TestCase):
    def test_vectors_are_rotated_and_normalized_without_translation(self):
        review, observations, context = fixture()
        result = bind_reviewed_directions(review, observations, context)
        self.assertEqual(result["directions"]["forward_direction_2d"], [0., 1.])
        self.assertEqual(result["directions"]["up_direction_2d"], [1., 0.])
        self.assertEqual(result["evidence"]["source_frame_index"], 3)
        self.assertIsNotNone(result["evidence"]["source_pts"])

    def test_true_flag_or_matching_epoch_alone_is_insufficient(self):
        for field in ("direction_reference_timestamp_ms", "direction_basis", "evidence"):
            review, observations, context = fixture()
            del review[field]
            self.assertFalse(bind_reviewed_directions(review, observations, context)["directions"])

    def test_wrong_time_frame_video_subject_and_epoch_do_not_bind(self):
        mutations = [lambda r, o, c: r.update(direction_reference_timestamp_ms=12345),
                     lambda r, o, c: r["direction_basis"]["frames"][0].update(source_frame_index=99),
                     lambda r, o, c: r.update(video_sha256="b" * 64),
                     lambda r, o, c: c.update(track=99), lambda r, o, c: c.update(selection_epoch=99),
                     lambda r, o, c: c.update(camera_reference_epoch=99),
                     lambda r, o, c: r["direction_basis"].update(coordinate_system="body_relative")]
        for mutate in mutations:
            review, observations, context = fixture()
            mutate(review, observations, context)
            self.assertFalse(bind_reviewed_directions(review, observations, context)["directions"])

    def test_missing_or_ambiguous_source_observation_is_not_replaced_with_nearest(self):
        review, observations, context = fixture()
        rows = list(observations.values())
        target = next(r for r in rows if r["frame"] == 3)
        for changed in ([r for r in rows if r["frame"] != 3], rows + [deepcopy(target)]):
            self.assertFalse(bind_reviewed_directions(review, changed, context)["directions"])

    def test_invalid_transforms_are_missing_not_identity(self):
        for matrix in (None, [[0, 0], [0, 0]], [[-1, 0], [0, 1]], [[1, .3], [0, 1]], [[np.nan, 0], [0, 1]]):
            review, observations, context = fixture()
            row = next(r for r in observations.values() if r["frame"] == 3)
            row["image_to_reference_linear"] = matrix
            self.assertFalse(bind_reviewed_directions(review, observations, context)["directions"])

    def test_one_missing_direction_does_not_discard_the_other_and_inputs_are_unchanged(self):
        review, observations, context = fixture()
        review["forward_direction_2d"] = [0, 0]
        saved = deepcopy((review, observations, context))
        result = bind_reviewed_directions(review, observations, context)
        self.assertNotIn("forward_direction_2d", result["directions"])
        self.assertIn("up_direction_2d", result["directions"])
        self.assertEqual((review, observations, context), saved)


if __name__ == "__main__":
    unittest.main()
