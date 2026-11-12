from __future__ import annotations

import copy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from scripts.benchmark_sam3d_pipeline import (IdleGuard, MAX_WARMUP_SOURCE_BYTES,
    build_parser, candidate_subject_shapes, main, parse_graph_sizes, pipeline_settings,
    prepare_source, run_pose_warmup, validate_output_root, warmup_group)
from scripts.sam3d_pipeline_comparison import compare_pipeline, native_snapshot, recursive_changes, timing_summary, validate_source_bindings, validate_trial_order
from rallymate_vision.pose.metadata import MHR70_NAMES, sha256_file


class PipelineBenchmarkHelperTests(unittest.TestCase):
    def test_runtime_cli_options_reach_recorded_worker_profile(self):
        required = ["--" + name + "=unused" for name in
            ("video", "native-jsonl", "runtime-dir", "sam-python", "idle-jobs-db", "output-root")]
        args = build_parser().parse_args(required + ["--reuse-cuda-cache=0", "--profile-stages=1",
                                                   "--async-mesh-capture=0"])
        with patch.dict("os.environ", {}, clear=True):
            profile = pipeline_settings(Path("runtime"), Path("python"), 0,
                reuse_cuda_cache=args.reuse_cuda_cache, profile_stages=args.profile_stages,
                async_mesh_capture=args.async_mesh_capture)
        self.assertEqual(profile["RALLYMATE_SAM3D_REUSE_CUDA_CACHE"], "0")
        self.assertEqual(profile["RALLYMATE_SAM3D_PROFILE_STAGES"], "1")
        self.assertEqual(profile["RALLYMATE_SAM3D_ASYNC_MESH_CAPTURE"], "0")

    def test_4k_warmup_shapes_match_frame_size_aware_pipeline_staging(self):
        groups = [[{"image_size": [3840, 2160], "status": "ok", "bbox_px": [1, 2, 30, 40]}]
                  for _ in range(8)]
        self.assertEqual(candidate_subject_shapes(groups, 8), [8])
        self.assertEqual(candidate_subject_shapes(groups, 8, 64 * 1024**2), [2])

    def test_batch_preprocess_is_explicit_off_even_when_environment_is_on(self):
        parser = build_parser()
        args = parser.parse_args(["--" + name + "=unused" for name in
            ("video", "native-jsonl", "runtime-dir", "sam-python", "idle-jobs-db", "output-root")])
        self.assertEqual(args.frames, 128)
        self.assertEqual(args.batch_frames, 4)
        self.assertEqual(args.batch_preprocess, 0)
        with patch.dict("os.environ", {"RALLYMATE_SAM3D_BATCH_PREPROCESS": "1",
                                      "RALLYMATE_SAM3D_CUDA_WAIT_MODE": "blocking_sync"}):
            settings = pipeline_settings(Path("runtime"), Path("python"), args.batch_preprocess)
        self.assertEqual(settings["RALLYMATE_SAM3D_BATCH_PREPROCESS"], "0")
        self.assertEqual(settings["RALLYMATE_SAM3D_BATCH_PERSONS"], "8")
        self.assertEqual(settings["RALLYMATE_SAM3D_BATCH_GRAPH_SIZES"], "8")
        self.assertEqual(settings["RALLYMATE_DETECT_BATCH_SIZE"], "1")
        self.assertEqual(settings["RALLYMATE_SAM3D_CUDA_WAIT_MODE"], "blocking_sync")
        with patch.dict("os.environ", {"RALLYMATE_SAM3D_CUDA_WAIT_MODE": "unknown"}):
            with self.assertRaisesRegex(ValueError, "wait mode"):
                pipeline_settings(Path("runtime"), Path("python"), 0)

    def test_parser_accepts_only_four_or_eight_candidate_frames(self):
        parser = build_parser()
        required = ["--" + name + "=unused" for name in
            ("video", "native-jsonl", "runtime-dir", "sam-python", "idle-jobs-db", "output-root")]
        self.assertEqual(parser.parse_args(required + ["--batch-frames=8"]).batch_frames, 8)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(required + ["--batch-frames=2"])

    def test_experiment_options_preserve_defaults_and_validate_graph_shapes(self):
        required = ["--" + name + "=unused" for name in
            ("video", "native-jsonl", "runtime-dir", "sam-python", "idle-jobs-db", "output-root")]
        defaults = build_parser().parse_args(required)
        self.assertEqual(defaults.graph_sizes, "8")
        self.assertIsNone(defaults.forward_graph_budget_mib)
        self.assertIsNone(defaults.head_graph_budget_mib)
        self.assertIsNone(defaults.archive_pending_frames)
        explicit = build_parser().parse_args(required + ["--graph-sizes=4,8",
            "--forward-graph-budget-mib=1024", "--head-graph-budget-mib=768", "--archive-pending-frames=8"])
        self.assertEqual(parse_graph_sizes(explicit.graph_sizes), (4, 8))
        self.assertEqual((explicit.forward_graph_budget_mib, explicit.head_graph_budget_mib,
                          explicit.archive_pending_frames), (1024, 768, 8))
        for valid in ("2", "4", "8", "2,8"):
            self.assertTrue(parse_graph_sizes(valid))
        for invalid in ("", "1", "9", "4,4", "2,4,8", "4.0", "４", "4,"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_graph_sizes(invalid)

    def test_experiment_budgets_inherit_environment_and_explicit_values_win(self):
        forward = "RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB"
        head = "RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB"
        archive = "RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES"
        with patch.dict("os.environ", {}, clear=True):
            defaults = pipeline_settings(Path("runtime"), Path("python"), 0)
        self.assertEqual((defaults[forward], defaults[head], defaults[archive]), ("512", "512", "2"))
        with patch.dict("os.environ", {forward: "1024", head: " 768 ", archive: "4"}, clear=True):
            inherited = pipeline_settings(Path("runtime"), Path("python"), 0)
            explicit = pipeline_settings(Path("runtime"), Path("python"), 0, graph_sizes=(4,),
                forward_graph_budget_mib=2048, head_graph_budget_mib=1024, archive_pending_frames=8)
        self.assertEqual((inherited[forward], inherited[head], inherited[archive]), ("1024", "768", "4"))
        self.assertEqual((explicit[forward], explicit[head], explicit[archive]), ("2048", "1024", "8"))
        self.assertEqual(explicit["RALLYMATE_SAM3D_BATCH_GRAPH_SIZES"], "4")
        for variable, invalid in ((forward, "127"), (head, "4097"), (head, "1e3"),
                                  (forward, ""), (head, "５１２"), (archive, "3")):
            with self.subTest(variable=variable, invalid=invalid), \
                    patch.dict("os.environ", {variable: invalid}, clear=True), self.assertRaises(ValueError):
                pipeline_settings(Path("runtime"), Path("python"), 0)
        with patch.dict("os.environ", {forward: "invalid", head: "invalid", archive: "invalid"}, clear=True):
            valid = pipeline_settings(Path("runtime"), Path("python"), 0,
                forward_graph_budget_mib=128, head_graph_budget_mib=4096, archive_pending_frames=2)
        self.assertEqual(valid[forward], "128")

    def test_comparison_requires_one_consistent_declared_abba_candidate(self):
        def probe(candidate, actual, declared=None):
            value = {"candidate_batch_frames": candidate,
                     "trials": [{"batch_frames": item} for item in actual]}
            if declared is not None:
                value["trial_order"] = declared
            return value
        for candidate in (4, 8):
            order = [1, candidate, candidate, 1]
            self.assertEqual(validate_trial_order(probe(candidate, order, order)), candidate)
        legacy = {"trials": [{"batch_frames": item} for item in (1, 4, 4, 1)]}
        self.assertEqual(validate_trial_order(legacy), 4)
        for invalid in (probe(4, [1, 8, 8, 1]), probe(8, [1, 8, 4, 1]),
                        probe(16, [1, 16, 16, 1]), probe(8, [1, 8, 8, 1], [1, 4, 4, 1]),
                        probe(8, [True, 8, 8, True])):
            with self.subTest(probe=invalid), self.assertRaises(ValueError):
                validate_trial_order(invalid)

    def test_idle_guard_refuses_active_jobs_and_does_not_change_database(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "jobs.sqlite3"
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE jobs(status TEXT)")
                conn.execute("INSERT INTO jobs VALUES('running')")
            conn.close()
            before = database.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "queued/running"):
                IdleGuard(database)(force=True)
            self.assertEqual(database.read_bytes(), before)
            with self.assertRaises(ValueError):
                validate_output_root(database.parent / "probe", database)
            missing = database.parent / "missing.sqlite3"
            with self.assertRaises(sqlite3.OperationalError):
                IdleGuard(missing)(force=True)
            self.assertFalse(missing.exists())

    def test_timing_uses_each_actual_frame_and_subject_count(self):
        trials = [{"trial": 1, "batch_frames": 1, "caller_wall_seconds": 10,
                   "processing": {"stage_seconds": {"pose_seconds": 5}}},
                  {"trial": 2, "batch_frames": 4, "caller_wall_seconds": 4,
                   "processing": {"stage_seconds": {"pose_seconds": 2}}}]
        counts = [{"trial": 1, "source_frames": 100, "successful_subjects": 150},
                  {"trial": 2, "source_frames": 90, "successful_subjects": 160}]
        summary = timing_summary(trials, counts)
        self.assertEqual(summary[0]["median_pipeline_frames_per_second"], 10)
        self.assertEqual(summary[0]["median_sam_subjects_per_second"], 30)
        self.assertEqual(summary[1]["median_pipeline_frames_per_second"], 22.5)
        self.assertEqual(summary[1]["median_sam_subjects_per_second"], 80)

    def test_prepare_checks_every_pts_but_keeps_only_four_images(self):
        selected = [[{"frame_index": index, "video_sha256": "a" * 64,
                     "pts": index, "time_base_num": 1, "time_base_den": 30,
                     "image_size": [10, 10], "bbox_px": [0, 0, 10, 10]}] for index in range(8)]
        class Decoder:
            binding = None
            index = 0
            released = False
            def read(self):
                self.binding = {"pts": self.index, "time_base_num": 1, "time_base_den": 30}
                self.index += 1
                return True, np.zeros((10, 10, 3), dtype=np.uint8)
            def release(self):
                self.released = True
        decoder = Decoder()
        with patch("scripts.benchmark_sam3d_pipeline.select_pipeline_records", return_value=selected), \
                patch("scripts.benchmark_sam3d_pipeline.sha256_file", return_value="a" * 64), \
                patch("scripts.benchmark_sam3d_pipeline.SourcePTSDecoder", return_value=decoder):
            source, images = prepare_source(Path("video"), Path("native"), 8, lambda **kwargs: None)
        self.assertEqual(len(source["frames"]), 8)
        self.assertEqual(len(images), 4)
        self.assertEqual(decoder.index, 8)
        self.assertTrue(decoder.released)
        selected[-1][0]["pts"] = 99
        with patch("scripts.benchmark_sam3d_pipeline.select_pipeline_records", return_value=selected), \
                patch("scripts.benchmark_sam3d_pipeline.sha256_file", return_value="a" * 64), \
                patch("scripts.benchmark_sam3d_pipeline.SourcePTSDecoder", return_value=Decoder()):
            with self.assertRaisesRegex(ValueError, "PTS"):
                prepare_source(Path("video"), Path("native"), 8, lambda **kwargs: None)

    def test_prepare_warms_eight_single_person_frames_and_honors_source_byte_limit(self):
        for width, height, retained, shapes, environment in (
                (10, 10, 8, [8], {}), (3840, 2160, 8, [8], {}),
                (3840, 2160, 2, [2], {"RALLYMATE_SAM3D_SOURCE_BUFFER_MIB": "64"})):
            with self.subTest(resolution=(width, height)):
                selected = [[{"frame_index": index, "video_sha256": "a" * 64,
                             "pts": index, "time_base_num": 1, "time_base_den": 30,
                             "image_size": [width, height], "bbox_px": [0, 0, width, height]}] for index in range(8)]
                class Decoder:
                    binding = None
                    index = 0
                    released = False
                    def read(self):
                        self.binding = {"pts": self.index, "time_base_num": 1, "time_base_den": 30}
                        self.index += 1
                        # Broadcast view avoids allocating 4K pixels in this CPU unit test;
                        # ndarray.nbytes still reports the production image's byte budget.
                        return True, np.broadcast_to(np.zeros(1, dtype=np.uint8), (height, width, 3))
                    def release(self):
                        self.released = True
                decoder = Decoder()
                with patch("scripts.benchmark_sam3d_pipeline.select_pipeline_records", return_value=selected), \
                        patch("scripts.benchmark_sam3d_pipeline.sha256_file", return_value="a" * 64), \
                        patch("scripts.benchmark_sam3d_pipeline.SourcePTSDecoder", return_value=decoder), \
                        patch.dict("os.environ", environment, clear=True):
                    source, images = prepare_source(Path("video"), Path("native"), 8,
                        lambda **kwargs: None, batch_frames=8)
                self.assertEqual(len(images), retained)
                self.assertEqual(source["warmup_expected_subject_shapes"], shapes)
                self.assertLessEqual(source["warmup_image_bytes"], MAX_WARMUP_SOURCE_BYTES)
                self.assertEqual(source["warmup_source_frame_count"], retained)
                self.assertEqual(decoder.index, 8)
                self.assertTrue(decoder.released)
                self.assertEqual([item["bindings"][0]["frame_index"] for item in images], list(range(retained)))

    def test_shape_plan_tracks_microbatch_tail_and_scalar_one_frame_path(self):
        def groups(counts):
            return [[{"image_size": [10, 10]} for _ in range(count)] for count in counts]
        self.assertEqual(candidate_subject_shapes(groups([2] * 8), 8), [8])
        self.assertEqual(candidate_subject_shapes(groups([2] * 5), 8), [2, 8])
        self.assertEqual(candidate_subject_shapes(groups([1] * 4 + [2]), 4), [1, 4])
        self.assertEqual(candidate_subject_shapes(groups([2] * 8), 8, max_source_bytes=300), [1])
        self.assertEqual(candidate_subject_shapes(groups([2] * 8), 8, max_source_bytes=600), [4])

    def test_warmup_records_verified_single_forwards_without_lumping_sixteen_people(self):
        frames = [{"frame": np.zeros((10, 10, 3), dtype=np.uint8),
                   "boxes": [[0, 0, 10, 10]] * 2,
                   "bindings": [{"frame_index": index, "person_track_id": person} for person in (1, 2)]}
                  for index in range(8)]
        class Engine:
            def __init__(self, forward_calls=1):
                self.calls = []
                self.forward_calls = forward_calls
            def infer(self, frame, boxes):
                self.calls.append(("scalar", len(boxes)))
                return [{"status": "ok"} for _ in boxes]
            def infer_batch(self, requests):
                count = sum(len(request["boxes"]) for request in requests)
                self.calls.append(("batch", count))
                return [[{"status": "ok", "batch_execution": {"subjects": count,
                    "forward_calls": self.forward_calls}} for _ in request["boxes"]] for request in requests]
        engine = Engine()
        with patch("scripts.benchmark_sam3d_pipeline.snapshots") as snapshots_mock:
            result = run_pose_warmup(engine, frames, [8], guard=lambda **kwargs: None,
                runtime=Path("runtime"), output=Path("output"), batch_preprocess=False)
        self.assertEqual(engine.calls, [("scalar", 1), ("batch", 8)])
        self.assertEqual([call["subjects"] for call in result["calls"]], [1, 8])
        self.assertEqual(result["calls"][1]["source_frame_indices"], [0, 1, 2, 3])
        self.assertTrue(result["calls"][1]["true_batch_receipt_verified"])
        self.assertEqual(snapshots_mock.call_count, 2)
        selected = warmup_group(frames, 3)
        self.assertIs(selected[0]["frame"], frames[0]["frame"])
        self.assertEqual([len(frame["boxes"]) for frame in selected], [2, 1])
        self.assertEqual(selected[1]["bindings"], frames[1]["bindings"][:1])
        with patch("scripts.benchmark_sam3d_pipeline.snapshots"), self.assertRaisesRegex(ValueError, "exactly one forward"):
            run_pose_warmup(Engine(forward_calls=2), frames, [8], guard=lambda **kwargs: None,
                runtime=Path("runtime"), output=Path("output"), batch_preprocess=False)
        with self.assertRaises(ValueError):
            warmup_group(frames, 16)

    def test_prepare_only_writes_isolated_configuration_without_model_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = root / "service"
            service.mkdir()
            database = service / "jobs.sqlite3"
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE jobs(status TEXT)")
            conn.close()
            runtime = root / "runtime"
            checkpoint = runtime / "checkpoints/sam-3d-body-dinov3/model.ckpt"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.touch()
            checkpoint.with_name("model_config.yaml").touch()
            files = {key: root / key for key in ("video", "native-jsonl", "sam-python", "detect-model")}
            for path in files.values():
                path.touch()
            output = root / "report"
            args = ["--" + key + "=" + str(path) for key, path in files.items()]
            args += ["--runtime-dir=" + str(runtime), "--idle-jobs-db=" + str(database),
                     "--output-root=" + str(output), "--frames=4", "--prepare-only",
                     "--batch-frames=8", "--graph-sizes=8", "--archive-pending-frames=8"]
            with patch("scripts.benchmark_sam3d_pipeline.prepare_source", return_value=({"video_sha256": "a" * 64, "frames": []}, [])), \
                    patch.dict("sys.modules", {"torch": None, "rallymate_vision.inference": None}), \
                    patch.dict("os.environ", {"RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB": "1024",
                                              "RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB": "768"}):
                main(args)
            report = json.loads((output / "probe.json").read_text())
            self.assertEqual(report["status"], "cpu_prepared_no_inference")
            self.assertEqual(report["trials"], [])
            self.assertEqual(report["trial_order"], [1, 8, 8, 1])
            experiment = report["experiment_configuration"]
            self.assertEqual(experiment["graph_sizes"], [8])
            self.assertEqual(experiment["forward_graph_budget_mib"], 1024)
            self.assertEqual(experiment["head_graph_budget_mib"], 768)
            self.assertEqual(experiment["archive_pending_frames"], 8)
            self.assertEqual(experiment["archive_worker_threads"], 1)
            self.assertEqual(experiment["selection_sources"], {"forward_graph_budget_mib": "environment",
                "head_graph_budget_mib": "environment", "archive_pending_frames": "cli"})
            self.assertFalse(report["scoring_accuracy_validated"])
            self.assertTrue((output / "base-request.json").is_file())

    def test_structural_decision_and_feature_changes_are_not_dropped(self):
        difference = recursive_changes([{"grade": "A", "feature": {"value": 1}}],
                                       [{"grade": "B", "feature": {"value": 2}}, {"grade": "C"}])
        self.assertEqual(difference["numeric_changes"], 1)
        self.assertEqual(difference["nonnumeric_changes"], 2)

    def test_complete_geometry_comparison_never_accepts_calibration_as_accuracy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_sha = "1" * 64
            binding = {"video_sha256": source_sha, "frame_index": 0, "processed_index": 0,
                       "pts": 0, "time_base_num": 1, "time_base_den": 30, "timestamp_ms": 0,
                       "person_track_id": 1, "selection_epoch": 0, "camera_reference_epoch": 0,
                       "image_size": [10, 10], "bbox_px": [0, 0, 10, 10]}
            trials = []
            for index, batch in enumerate((1, 4, 4, 1), start=1):
                output = root / str(index)
                output.mkdir()
                np.savez(output / "mesh.npz", vertices=np.zeros((3, 3)), camera=np.zeros(3), focal=np.ones(1))
                np.save(output / "faces.npy", np.array([[0, 1, 2]], dtype=np.int32))
                artifact = {"path": "mesh.npz", "sha256": sha256_file(output / "mesh.npz"),
                            "faces_path": "faces.npy", "faces_sha256": sha256_file(output / "faces.npy"),
                            "vertex_count": 3, "face_count": 1}
                native = {**binding, "status": "ok", "mesh_artifact": artifact,
                          "keypoints_2d": {name: [0., 0.] for name in MHR70_NAMES},
                          "keypoints_3d": {name: [0., 0., 0.] for name in MHR70_NAMES}}
                frame = {"frame": {"index": 0, "processed_index": 0, "pts": 0,
                                  "time_base_num": 1, "time_base_den": 30, "video_sha256": source_sha}}
                score = {"status": "calibration_required", "grade": None, "indicator_id": "test", "feature": {"value": 0}}
                for name, values in (("sam3d_frames.jsonl", [native]), ("frames.jsonl", [frame]),
                                     ("events.jsonl", []), ("scores.jsonl", [score])):
                    (output / name).write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")
                trials.append({"trial": index, "batch_frames": batch, "output_dir": str(output),
                               "caller_wall_seconds": 2, "processing": {"stage_seconds": {"pose_seconds": 1}},
                               "scoring_state": {"status": "calibration_required"}})
            source = {"video_sha256": source_sha, "frames": [[binding]]}
            (root / "probe.json").write_text(json.dumps({"trials": trials, "source_binding": source}), encoding="utf-8")
            report = compare_pipeline(root)
            self.assertTrue(report["routing_and_decisions_equal"])
            self.assertFalse(report["scoring_accuracy"]["validated"])
            self.assertTrue(report["trials"][0]["geometry"]["all_exact"])
            self.assertEqual(report["timing"][0]["median_pipeline_frames_per_second"], .5)
            altered = copy.deepcopy(frame)
            altered["frame"]["pts"] = 1
            with self.assertRaisesRegex(ValueError, "PTS"):
                validate_source_bindings([native], [altered], source)
            incomplete = copy.deepcopy(native)
            incomplete["keypoints_3d"].pop(MHR70_NAMES[-1])
            with self.assertRaisesRegex(ValueError, "70"):
                native_snapshot(root / "4", incomplete, {})
            escaped = copy.deepcopy(native)
            escaped["mesh_artifact"]["path"] = "../outside.npz"
            with self.assertRaisesRegex(ValueError, "escaped"):
                native_snapshot(root / "4", escaped, {})


if __name__ == "__main__":
    unittest.main()
