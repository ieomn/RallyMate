import unittest

from rallymate_vision.progress import POSTPROCESS_STAGES, postprocess_progress


class PipelineProgressTests(unittest.TestCase):
    def test_all_postprocessing_stages_remain_visible_and_below_complete(self):
        previous = 85
        for phase in POSTPROCESS_STAGES:
            start = postprocess_progress(phase, processed_frames=11516, total_frames=11516)
            end = postprocess_progress(phase, processed_frames=11516, total_frames=11516, completed=212, total=212)
            self.assertGreaterEqual(start["percent"], previous)
            self.assertLessEqual(start["percent"], end["percent"])
            self.assertLess(end["percent"], 100)
            self.assertEqual(end["completed_items"], 212)
            self.assertEqual(end["processed_frames"], 11516)
            previous = end["percent"]

    def test_partial_measurement_progress_reports_actual_work(self):
        state = postprocess_progress("extracting_features", processed_frames=60, total_frames=60, completed=5, total=10)
        self.assertEqual(state["percent"], 94)
        self.assertEqual(state["message"], "正在计算动作测量（5/10）")

    def test_empty_work_never_implies_complete(self):
        state = postprocess_progress("detecting_events", processed_frames=3, total_frames=3, completed=0, total=0)
        self.assertEqual(state["percent"], 90)
        self.assertNotIn("total_items", state)
