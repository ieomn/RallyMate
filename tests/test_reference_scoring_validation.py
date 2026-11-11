from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import validate_reference_scoring as validation


class ReferenceScoringValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "service_data/runs/source-run"
        self.run.mkdir(parents=True)
        video = self.root / "source.mp4"
        video.write_bytes(b"independent source bytes")
        self.sha = validation.sha256(video)
        self.metadata = {"frames": 3, "fps": 30.0, "width": 320, "height": 240, "duration_s": .1}
        self.native = [{"frame_index": i, "person_track_id": 1, "video_sha256": self.sha,
                        "status": "ok"} for i in range(3)]
        self.frames = [{"frame": {"index": i, "video_sha256": self.sha, "width": 320, "height": 240},
                        "poses": [{"id": 1}]} for i in range(3)]
        self.summary = {"job_id": "source-run", "minimum_scoring_loop": {"provenance": {"video_sha256": self.sha}}}
        (self.run / "summary.json").write_text(json.dumps(self.summary), encoding="utf-8")
        self.write_rows()
        self.sample = {"sample_id": "reference", "split": "reference", "source_group": "independent-source",
                       "video": "source.mp4", "video_sha256": self.sha, "run": "service_data/runs/source-run",
                       "run_id": "source-run", "prior_results_seen": True, **self.metadata,
                       "artifact_sha256": validation.artifact_hashes(self.run)}
        self.manifest = {"version": validation.VERSION, "samples": [self.sample]}
        self.jobs = [{"id": "source-run", "status": "succeeded", "video_sha256": self.sha,
                      "processed_frames": 3, "source_start_frame": 0, "source_end_frame_exclusive": 3,
                      "frame_stride": 1, "pose_backend": "sam3d", "output_dir": "old-machine/path"}]
        self.report = {"source_video_sha256": self.sha, "source_run_id": "source-run", "status": "scored",
                       "overall_score": 75.0, "observation_count": 1,
                       "dimensions": [{"id": "preparation", "metrics": [{"id": "coil"}]},
                                      {"id": "movement", "metrics": [{"id": "knee"}]}],
                       "episodes": [{"episode_id": "first", "family": "baseline", "metrics": {
                           "coil": {"value": 20, "score": 100}, "knee": {"value": 10, "score": 50}}}]}
        self.probe = patch.object(validation, "probe_video", return_value=self.metadata)
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def write_rows(self):
        for name, rows in (("sam3d_frames.jsonl", self.native), ("frames.jsonl", self.frames),
                           ("primary-player.jsonl", [{"source_frame_index": i} for i in range(3)])):
            (self.run / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def refresh_hashes(self):
        self.sample["artifact_sha256"] = validation.artifact_hashes(self.run)

    def test_source_validation_relocates_archive_but_requires_pinned_database_job(self):
        video, job, summary, evidence = validation.verify_source(self.sample, self.root, self.jobs)
        self.assertEqual(video["sha256"], self.sha)
        self.assertEqual(job["output_dir"], str(self.run))
        self.assertEqual(summary, self.summary)
        self.assertTrue(evidence["full_frame_coverage"])
        with self.assertRaisesRegex(ValueError, "Pinned database job"):
            validation.verify_source(self.sample, self.root, [{**self.jobs[0], "id": "another"}])

    def test_missing_native_frame_and_foreign_native_source_fail_with_frozen_hashes(self):
        for rows, message in ((self.native[:2], "every original frame"),
                              ([{**r, "video_sha256": "f" * 64} for r in self.native], "SHA differs")):
            with self.subTest(message=message):
                self.native = rows
                self.write_rows()
                self.refresh_hashes()
                with self.assertRaisesRegex(ValueError, message):
                    validation.verify_source(self.sample, self.root, self.jobs)

    def test_projected_foreign_source_fails_even_with_manifest_matching_the_file(self):
        self.frames[1]["frame"]["video_sha256"] = "f" * 64
        self.write_rows()
        self.refresh_hashes()
        with self.assertRaisesRegex(ValueError, "Projected observation source SHA"):
            validation.verify_source(self.sample, self.root, self.jobs)

    def test_summary_must_bind_same_video(self):
        self.summary["minimum_scoring_loop"]["provenance"]["video_sha256"] = "f" * 64
        (self.run / "summary.json").write_text(json.dumps(self.summary), encoding="utf-8")
        self.refresh_hashes()
        with self.assertRaisesRegex(ValueError, "summary source identity"):
            validation.verify_source(self.sample, self.root, self.jobs)

    def test_artifact_and_optional_sidecar_changes_fail(self):
        (self.run / "action-context.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifact SHA"):
            validation.verify_source(self.sample, self.root, self.jobs)

    def test_source_path_cannot_escape_root(self):
        for path in ("../source.mp4", str(self.root / "source.mp4")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validation.source_path(self.root, path)

    def test_duplicate_content_group_is_not_an_independent_test_sample(self):
        duplicate = {**self.sample, "sample_id": "duplicate", "split": "test", "video_sha256": "f" * 64}
        with self.assertRaisesRegex(ValueError, "source_group"):
            validation.validate_manifest({"version": validation.VERSION, "samples": [self.sample, duplicate]})

    def test_seen_results_cannot_be_called_holdout(self):
        manifest = deepcopy(self.manifest)
        manifest["samples"].append({**self.sample, "sample_id": "test", "source_group": "other",
                                    "video_sha256": "f" * 64, "split": "holdout"})
        with self.assertRaisesRegex(ValueError, "prior_results_seen"):
            validation.validate_manifest(manifest)
        manifest["samples"][1]["prior_results_seen"] = False
        self.assertIs(validation.validate_manifest(manifest), manifest)

    def test_episode_score_uses_dimension_means_and_reports_observed_saturation(self):
        row = validation.episode_scores(self.report)[0]
        self.assertEqual(row["engineering_score"], 75)
        self.assertEqual(row["metric_saturation_fraction"], .5)
        self.assertEqual(row["observed_dimension_count"], 2)
        self.report["episodes"][0]["metrics"]["knee"]["score"] = 0
        self.assertEqual(validation.episode_scores(self.report)[0]["engineering_score"], 50)

    def execute(self, side_effect=None, fingerprints=None):
        fingerprint = {"sha256": "a" * 64, "files": {"test": "b" * 64}}
        with patch.object(validation, "database_jobs", return_value=self.jobs), \
                patch.object(validation, "recompute", side_effect=side_effect, return_value=self.report) as build, \
                patch.object(validation, "code_fingerprint", side_effect=fingerprints or [fingerprint, fingerprint]):
            result = validation.run_validation(self.manifest, self.root, self.root / "database", self.root / "output")
        return result, build

    def test_current_report_is_always_built_and_replaces_old_report(self):
        path = self.root / "output/assessments/reference.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"overall_score": 999}', encoding="utf-8")
        result, build = self.execute()
        self.assertTrue(result["all_passed"], result)
        build.assert_called_once()
        self.assertEqual(json.loads(path.read_text())["overall_score"], 75)
        self.assertEqual(result["results"][0]["assessment_sha256"], validation.sha256(path))

    def test_source_change_during_build_produces_failed_ledger(self):
        def mutate(*args):
            (self.run / "action-context.json").write_text("{}", encoding="utf-8")
            return self.report
        result, _ = self.execute(side_effect=mutate)
        self.assertFalse(result["all_passed"])
        self.assertIn("Source inputs changed", result["error"])

    def test_code_change_during_build_produces_failed_ledger(self):
        result, _ = self.execute(fingerprints=[{"sha256": "before"}, {"sha256": "after"}])
        self.assertFalse(result["all_passed"])
        self.assertFalse(result["source_code_stable_during_evaluation"])
        self.assertIn("Scorer code changed", result["error"])

    def test_report_changed_before_completion_cannot_receive_passed_receipt(self):
        calls = []
        def fingerprint():
            calls.append(1)
            if len(calls) == 2:
                (self.root / "output/assessments/reference.json").write_text("{}", encoding="utf-8")
            return {"sha256": "unchanged"}
        result, _ = self.execute(fingerprints=fingerprint)
        self.assertFalse(result["all_passed"])
        self.assertIn("assessment file changed", result["error"])

    def test_output_cannot_overwrite_source_evidence(self):
        with self.assertRaisesRegex(ValueError, "Output overlaps"):
            validation.run_validation(self.manifest, self.root, self.root / "database", self.run)

    def test_recompute_clears_lru_and_rejects_cached_result(self):
        from rallymate_service import tennis_assessment
        with patch.object(tennis_assessment, "_cached") as cache, \
                patch.object(tennis_assessment, "build_tennis_assessment", return_value=self.report) as build:
            cache.cache_info.return_value.hits = 0
            cache.cache_info.return_value.misses = 1
            self.assertEqual(validation.recompute(self.summary, self.run, "source-run"), self.report)
            cache.cache_clear.assert_called_once()
            build.assert_called_once_with(self.summary, self.run, job_id="source-run")
            cache.cache_info.return_value.hits = 1
            with self.assertRaisesRegex(ValueError, "freshly recomputed"):
                validation.recompute(self.summary, self.run, "source-run")

    def test_missing_source_or_invalid_manifest_has_nonzero_exit(self):
        path = self.root / "invalid.json"
        path.write_text("{}", encoding="utf-8")
        self.assertEqual(validation.main(["--manifest", str(path), "--source-root", str(self.root)]), 1)


if __name__ == "__main__":
    unittest.main()
