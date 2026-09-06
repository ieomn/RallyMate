from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAMES = (
    "build_minimum_scoring_loop",
    "build_scoring_loop_report",
    "evaluate_pose_features_on_common_events",
    "run_pose_scoring_ab",
)


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_historical_registry(work: Path) -> None:
    shutil.copyfile(ROOT / "metric-feasibility.json", work / "metric-feasibility.json")


class HistoricalRegistryScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.modules = {name: _load_script(name) for name in SCRIPT_NAMES}

    def test_main_requires_explicit_historical_replay_before_workspace_access(self) -> None:
        for name, module in self.modules.items():
            with self.subTest(script=name), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                stderr = io.StringIO()
                with (
                    mock.patch.object(module.Path, "cwd", return_value=work),
                    mock.patch.object(
                        module,
                        "_load_historical_registry",
                        side_effect=AssertionError("registry touched before replay gate"),
                    ),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as raised,
                ):
                    module.main([])
                self.assertEqual(2, raised.exception.code)
                self.assertIn("--historical-replay is required", stderr.getvalue())
                self.assertEqual([], list(work.iterdir()))

    def test_explicit_flag_is_accepted_by_every_parser(self) -> None:
        for name, module in self.modules.items():
            with self.subTest(script=name):
                args = module._parse_args(["--historical-replay"])
                self.assertTrue(args.historical_replay)

    def test_current_registry_replacement_fails_in_main_before_outputs(self) -> None:
        for name, module in self.modules.items():
            with self.subTest(script=name), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                registry_path = work / "metric-feasibility.json"
                shutil.copyfile(
                    ROOT / "metric-feasibility-pose-wave-v2.json", registry_path
                )
                with (
                    mock.patch.object(module.Path, "cwd", return_value=work),
                    self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"),
                ):
                    module.main(["--historical-replay"])
                self.assertEqual([registry_path], list(work.iterdir()))

    def test_every_script_pins_the_same_exact_historical_registry_bytes(self) -> None:
        expected_ids = (
            "FS01-M02",
            "FS01-M05",
            "FS02-M02",
            "FS09-M03",
            "FS09-M04",
            "FS09-M05",
        )
        for name, module in self.modules.items():
            with self.subTest(script=name):
                registry = module._load_historical_registry(ROOT)
                self.assertEqual(
                    "minimum-scoring-loop-2026-08-13.1",
                    registry["registry_version"],
                )
                self.assertEqual(
                    expected_ids,
                    tuple(item["indicator_id"] for item in registry["indicators"]),
                )

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            raw = (ROOT / "metric-feasibility.json").read_bytes()
            (work / "metric-feasibility.json").write_bytes(raw + b"\n")
            for name, module in self.modules.items():
                with self.subTest(script=f"{name}:modified_registry"):
                    with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                        module._load_historical_registry(work)

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            shutil.copyfile(
                ROOT / "metric-feasibility-pose-wave-v2.json",
                work / "metric-feasibility.json",
            )
            for name, module in self.modules.items():
                with self.subTest(script=f"{name}:current_13_indicator_registry"):
                    with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                        module._load_historical_registry(work)

    def test_minimum_loop_outputs_are_labeled_historical(self) -> None:
        module = self.modules["build_minimum_scoring_loop"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            _copy_historical_registry(work)
            reports = work / "reports"
            (reports / "pose-model-baseline.json").parent.mkdir(parents=True)
            (reports / "pose-model-baseline.json").write_text(
                json.dumps({"videos": []}), encoding="utf-8"
            )
            video_dir = reports / "scoring-loop" / "synthetic-video"
            video_dir.mkdir(parents=True)
            for filename in (
                "events.jsonl",
                "features.jsonl",
                "indicator-features.jsonl",
            ):
                (video_dir / filename).write_text("", encoding="utf-8")
            (video_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "video_id": "synthetic-video",
                        "status": "historical_fixture",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(module.Path, "cwd", return_value=work):
                module.main(
                    [
                        "--historical-replay",
                        "--video-id",
                        "synthetic-video",
                        "--reuse-complete",
                    ]
                )
            report = json.loads(
                (reports / "minimum-scoring-loop-baseline.json").read_text(
                    encoding="utf-8"
                )
            )
            manifest = json.loads(
                (work / "data" / "annotations" / "event-labeling-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                "historical_replay", report["historical_replay"]["execution_mode"]
            )
            self.assertEqual(
                "historical_replay", manifest["historical_replay"]["execution_mode"]
            )

    def test_report_builder_labels_html_and_embedded_summaries(self) -> None:
        module = self.modules["build_scoring_loop_report"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            _copy_historical_registry(work)
            for video_id in module.VIDEO_IDS:
                video_dir = work / "reports" / "scoring-loop" / video_id
                video_dir.mkdir(parents=True)
                for filename in ("events.jsonl", "indicator-features.jsonl", "scores.jsonl"):
                    (video_dir / filename).write_text("", encoding="utf-8")
                (video_dir / "summary.json").write_text(
                    json.dumps(
                        {
                            "video_id": video_id,
                            "status": "historical_fixture",
                            "event_evaluation": {"status": "ground_truth_required"},
                        }
                    ),
                    encoding="utf-8",
                )
            embedded_modes: list[str | None] = []

            def fake_writer(result: dict, path: Path) -> None:
                embedded_modes.append(result["summary"].get("execution_mode"))
                self.assertEqual(
                    "historical_replay",
                    result["summary"]["model_versions"]["historical_replay"][
                        "execution_mode"
                    ],
                )
                path.write_text("historical child report", encoding="utf-8")

            with (
                mock.patch.object(module.Path, "cwd", return_value=work),
                mock.patch.object(module, "write_scoring_loop_report", side_effect=fake_writer),
            ):
                module.main(["--historical-replay"])
            rendered = (work / "reports" / "minimum-scoring-loop-report.html").read_text(
                encoding="utf-8"
            )
            self.assertEqual(["historical_replay"] * 3, embedded_modes)
            self.assertIn("历史回放（historical replay）", rendered)
            self.assertIn("不代表当前生产注册表", rendered)

    def test_pose_ab_json_producers_label_historical_scope(self) -> None:
        evaluator = self.modules["evaluate_pose_features_on_common_events"]
        runner = self.modules["run_pose_scoring_ab"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            _copy_historical_registry(work)
            output_root = work / "reports" / "pose-scoring-ab"
            output_root.mkdir(parents=True)
            report_path = output_root / "pose-scoring-ab.json"
            report_path.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(evaluator.Path, "cwd", return_value=work),
                mock.patch.object(evaluator, "_load", return_value=[]),
                mock.patch.object(evaluator, "pose_sequence_from_records", return_value=object()),
                mock.patch.object(evaluator, "clear_feature_cache"),
            ):
                evaluator.main(["--historical-replay"])
            evaluated = json.loads(report_path.read_text(encoding="utf-8"))
            comparison = evaluated["common_event_feature_comparison"]
            self.assertEqual("historical_replay", comparison["execution_mode"])
            self.assertEqual(6, len(comparison["historical_indicator_ids"]))

            empty_result = {
                "events": [],
                "scores": [],
                "indicator_records": [],
                "summary": {
                    "event_counts": {},
                    "indicator_feature_validity": {},
                },
            }
            with (
                mock.patch.object(runner.Path, "cwd", return_value=work),
                mock.patch.object(runner, "build_primary_player_artifacts"),
                mock.patch.object(runner, "_pose_metadata", return_value={}),
                mock.patch.object(
                    runner, "run_minimum_scoring_loop", return_value=empty_result
                ) as run_loop,
                mock.patch.object(runner, "_load", return_value=[]),
                mock.patch.object(runner, "pose_sequence_from_records", return_value=object()),
            ):
                runner.main(
                    ["--historical-replay", "--video-id", "synthetic-video"]
                )
            replay = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "historical_replay", replay["historical_replay"]["execution_mode"]
            )
            self.assertEqual(
                list(runner.HISTORICAL_INDICATOR_IDS),
                replay["protocol"]["indicators"],
            )
            self.assertEqual(len(runner.BACKENDS), run_loop.call_count)
            for call in run_loop.call_args_list:
                self.assertEqual(
                    runner.HISTORICAL_REGISTRY_VERSION,
                    call.kwargs["feasibility_registry"]["registry_version"],
                )
                self.assertEqual(
                    "historical_replay",
                    call.kwargs["source_provenance"]["historical_replay"][
                        "execution_mode"
                    ],
                )

    def test_minimum_loop_reuse_rejects_current_registry_records(self) -> None:
        module = self.modules["build_minimum_scoring_loop"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            _copy_historical_registry(work)
            reports = work / "reports"
            (reports / "pose-model-baseline.json").parent.mkdir(parents=True)
            (reports / "pose-model-baseline.json").write_text(
                json.dumps({"videos": []}), encoding="utf-8"
            )
            video_id = "synthetic-video"
            output_dir = reports / "scoring-loop" / video_id
            output_dir.mkdir(parents=True)
            event = {
                "event_id": "fs01-current",
                "event_code": "FS01",
                "provenance": {"source_id": video_id},
            }
            (output_dir / "events.jsonl").write_text(
                json.dumps(event) + "\n", encoding="utf-8"
            )
            (output_dir / "features.jsonl").write_text("", encoding="utf-8")
            record = {
                "video_id": video_id,
                "event_id": event["event_id"],
                "event_code": "FS01",
                "indicator_id": "FS01-M03",
                "provenance": {
                    "feasibility_registry_version": "pose-wave-2026-08-22.17"
                },
            }
            (output_dir / "indicator-features.jsonl").write_text(
                json.dumps(record) + "\n", encoding="utf-8"
            )
            (output_dir / "summary.json").write_text(
                json.dumps({"video_id": video_id, "status": "current_fixture"}),
                encoding="utf-8",
            )
            with (
                mock.patch.object(module.Path, "cwd", return_value=work),
                self.assertRaisesRegex(RuntimeError, "frozen registry bundle"),
            ):
                module.main(
                    ["--historical-replay", "--video-id", video_id, "--reuse-complete"]
                )
            self.assertFalse(
                (reports / "minimum-scoring-loop-baseline.json").exists()
            )

    def test_report_builder_rejects_current_registry_records_and_scores(self) -> None:
        module = self.modules["build_scoring_loop_report"]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            _copy_historical_registry(work)
            video_id = module.VIDEO_IDS[0]
            output_dir = work / "reports" / "scoring-loop" / video_id
            output_dir.mkdir(parents=True)
            event = {
                "event_id": "fs01-current",
                "event_code": "FS01",
                "provenance": {"source_id": video_id},
            }
            (output_dir / "events.jsonl").write_text(
                json.dumps(event) + "\n", encoding="utf-8"
            )
            record = {
                "video_id": video_id,
                "event_id": event["event_id"],
                "event_code": "FS01",
                "indicator_id": "FS01-M03",
                "provenance": {
                    "feasibility_registry_version": "pose-wave-2026-08-22.17"
                },
            }
            score = {
                **record,
                "status": "calibration_required",
                "grade": None,
                "threshold_version": None,
                "model_versions": {
                    "feasibility_registry": "pose-wave-2026-08-22.17"
                },
            }
            (output_dir / "indicator-features.jsonl").write_text(
                json.dumps(record) + "\n", encoding="utf-8"
            )
            (output_dir / "scores.jsonl").write_text(
                json.dumps(score) + "\n", encoding="utf-8"
            )
            (output_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "video_id": video_id,
                        "status": "current_fixture",
                        "event_evaluation": {"status": "ground_truth_required"},
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(module.Path, "cwd", return_value=work),
                mock.patch.object(module, "write_scoring_loop_report") as writer,
                self.assertRaisesRegex(RuntimeError, "frozen registry bundle"),
            ):
                module.main(["--historical-replay"])
            writer.assert_not_called()
            self.assertFalse(
                (work / "reports" / "minimum-scoring-loop-report.html").exists()
            )

    def test_checked_in_historical_bundles_pass_content_validation(self) -> None:
        builder = self.modules["build_minimum_scoring_loop"]
        reporter = self.modules["build_scoring_loop_report"]
        registry = builder._load_historical_registry(ROOT)
        for video_id in builder.VIDEO_IDS:
            with self.subTest(video_id=video_id):
                directory = ROOT / "reports" / "scoring-loop" / video_id
                events = builder._load_jsonl(directory / "events.jsonl")
                features = builder._load_jsonl(directory / "features.jsonl")
                records = builder._load_jsonl(
                    directory / "indicator-features.jsonl"
                )
                scores = reporter._load_jsonl(directory / "scores.jsonl")
                summary = json.loads(
                    (directory / "summary.json").read_text(encoding="utf-8")
                )
                builder._require_reused_historical_bundle(
                    video_id=video_id,
                    registry=registry,
                    events=events,
                    features=features,
                    records=records,
                    summary=summary,
                )
                reporter._require_historical_bundle(
                    video_id=video_id,
                    registry=registry,
                    events=events,
                    records=records,
                    scores=scores,
                    summary=summary,
                )


if __name__ == "__main__":
    unittest.main()
