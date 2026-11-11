import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.plan_gpu_capacity import build_plan, fifo_burst, load_cases, workers_for_wait_slo


def probe_fixture():
    frames = [[{"frame_index": index, "pts": index * 3000,
                "time_base_num": 1, "time_base_den": 90000}] for index in range(4)]
    return {"schema_version": "sam3d-isolated-pipeline-probe-v2",
        "status": "completed_comparison_not_scoring_acceptance", "frames_requested": 4,
        "start_frame": 0, "max_players": 2, "trial_order": [1, 8, 8, 1],
        "trials": [{"batch_frames": batch, "caller_wall_seconds": wall,
                    "processing": {"processed_frames": 4, "frame_stride": 1}}
                   for batch, wall in ((1, 4), (8, 2), (8, 4), (1, 6))],
        "source_binding": {"video_sha256": "a" * 64, "frames": frames},
        "video": "source.mp4", "scoring_accuracy_validated": False,
        "experiment_configuration": {"forward_graph_budget_mib": 2560},
        "scope": "real_pipeline_no_video_render", "timing_scope": "pipeline_excludes_load_warmup"}


class GPUCapacityPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "probe.json"

    def load(self, value=None, **kwargs):
        self.path.write_text(json.dumps(probe_fixture() if value is None else value), encoding="utf-8")
        return load_cases(self.path, **kwargs)

    def test_uses_measured_caller_wall_and_keeps_batch_accuracy_boundary(self):
        default, candidate = self.load()
        self.assertEqual(default["samples"]["median_seconds"], 5)
        self.assertEqual(candidate["samples"]["median_seconds"], 3)
        self.assertEqual(default["measured_pipeline_frames_per_second"], .8)
        self.assertEqual(candidate["measured_seconds_per_frame"], .75)
        self.assertEqual(default["batch_policy"], "production_default_batch_size")
        self.assertEqual(candidate["batch_policy"], "candidate_requires_accuracy_and_evidence_gate")
        self.assertFalse(candidate["deployment_configuration_authorized"])
        self.assertEqual(default["provenance"]["probe_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(default["provenance"]["experiment_configuration"]["forward_graph_budget_mib"], 2560)

    def test_input_pts_rate_is_distinct_from_explicit_nominal_rate(self):
        derived = self.load()[0]["provenance"]
        self.assertAlmostEqual(derived["source_fps"], 30)
        supplied = self.load(source_fps=30.0038)[0]["provenance"]
        self.assertEqual(supplied["source_fps"], 30.0038)
        self.assertAlmostEqual(supplied["source"]["observed_pts_interval_mean_fps"], 30)
        self.assertNotEqual(derived["source_fps_basis"], supplied["source_fps_basis"])

    def test_skipped_empty_frames_are_not_counted_as_people_or_targets(self):
        value = probe_fixture()
        groups = value["source_binding"]["frames"]
        groups[0][0]["status"] = "skipped"
        groups[1].append({**groups[1][0], "status": "failed"})
        groups[2] = []
        source = self.load(value)[0]["provenance"]["source"]
        self.assertEqual(source["observed_subject_count_histogram"], {0: 2, 1: 2})
        self.assertEqual(source["native_record_histogram"], {0: 1, 1: 2, 2: 1})
        self.assertEqual(source["observed_target_count"], 2)
        self.assertEqual(source["skipped_records"], 1)
        self.assertEqual(source["failed_or_error_records"], 1)
        self.assertEqual(source["native_record_status_counts"]["ok"], 2)

    def test_rejects_incomplete_or_skipped_trials_and_nonfinite_timing(self):
        for mutation in (lambda row: row.update(status="running"),
                         lambda row: row["trials"].pop(),
                         lambda row: row["trials"][0]["processing"].update(processed_frames=3),
                         lambda row: row["trials"][0]["processing"].update(frame_stride=2),
                         lambda row: row["trials"][0].update(caller_wall_seconds=float("nan")),
                         lambda row: row["trials"][0].update(caller_wall_seconds=-1)):
            value = probe_fixture()
            mutation(value)
            with self.assertRaises(ValueError):
                self.load(value)

    def test_offset_window_requires_explicit_bias_acknowledgement(self):
        value = probe_fixture()
        value["start_frame"] = 300
        with self.assertRaisesRegex(ValueError, "prefix decoding"):
            self.load(value)
        result = self.load(value, allow_window_extrapolation=True)
        self.assertTrue(any("prefix decoding" in warning for warning in result[0]["linear_extrapolation_warnings"]))

    def test_fifo_exact_burst_and_no_false_wait_when_cards_exceed_users(self):
        result = fifo_burst(5, 10, 2)
        self.assertEqual(result["queue_wait_seconds"], {"min": 0, "p50": 10, "p95": 20, "max": 20})
        self.assertEqual(result["completion_seconds"], {"min": 10, "p50": 20, "p95": 30, "max": 30})
        self.assertEqual(result["waves"], 3)
        self.assertEqual(fifo_burst(5, 10, 8)["queue_wait_seconds"]["max"], 0)

    def test_wait_slo_solves_minimum_workers_and_keeps_service_time(self):
        self.assertEqual(workers_for_wait_slo(20, 600, 1200), 7)
        self.assertEqual(workers_for_wait_slo(20, 600, 0), 20)
        self.assertEqual(workers_for_wait_slo(20, 600, 11400), 1)
        self.assertEqual(fifo_burst(20, 600, 20)["completion_seconds"]["max"], 600)
        with self.assertRaises(ValueError):
            workers_for_wait_slo(20, 600, -1)

    def test_all_requested_scenarios_preserve_frame_rate_and_duration_scaling(self):
        result = build_plan(self.load())
        self.assertEqual(len(result["scenarios"]), 64)
        self.assertEqual(len(result["wait_slo_requirements"]), 16)
        default = result["measured_cases"][0]["case_id"]
        scenarios = {(row["video_minutes"], row["video_fps"]): row for row in result["scenarios"]
                     if row["case_id"] == default and row["submitters"] == 20 and row["gpu_workers"] == 1}
        baseline = scenarios[(5, 30)]
        self.assertEqual(baseline["target_frames"], 9000)
        self.assertEqual(scenarios[(5, 60)]["estimated_single_job_service_seconds"], baseline["estimated_single_job_service_seconds"] * 2)
        self.assertEqual(scenarios[(10, 60)]["estimated_single_job_service_seconds"], baseline["estimated_single_job_service_seconds"] * 4)
        self.assertEqual(result["status"], "offline_simulation_only")
        self.assertTrue(all("not_multi_gpu_measurement" in row["classification"] for row in result["scenarios"]))

    def test_distinct_input_probes_remain_distinct_cases(self):
        first = self.load()
        second_probe = copy.deepcopy(probe_fixture())
        second_probe["max_players"] = 4
        second = self.load(second_probe)
        result = build_plan(first + second)
        self.assertEqual(len({row["case_id"] for row in result["measured_cases"]}), 4)
        self.assertEqual(len(result["scenarios"]), 128)


if __name__ == "__main__":
    unittest.main()
