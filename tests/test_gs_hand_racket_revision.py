"""New-producer regression using the same frozen MIT observations, not new labels."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from rallymate_service.gs_card_execution import compute_gs_card_execution
from scripts.replay_gs_follow_through_validation import build_annotations
from scripts.replay_gs_hand_racket_revision import (
    CHANGED, RUNTIME_COMMIT, compare_revision, instances, replay, sha256,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/gs-hand-racket-revision-results-20261024.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class GSHandRacketRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = read(FIXTURE)
        cls.manifest = read(ROOT / "tests/fixtures/gs-follow-through-windows-20261024.json")
        cls.historical = read(ROOT / "tests/fixtures/gs-follow-through-results-20261024.json")
        recorded = read(ROOT / "tests/fixtures/gs-card-real-mit-preparation-v1.json")
        raw = next(row for row in recorded["cases"] if row["case_id"] == cls.manifest["case"]["case_id"])
        cls.report = compute_gs_card_execution(raw["frame_records"], raw["primary_timeline"], raw["decoder_timeline"],
            build_annotations(cls.manifest), source_job_id=raw["source_job_id"])

    def test_original_recorded_observations_reproduce_all_twenty_instances(self):
        self.assertEqual(instances(self.report), self.expected["instances"])
        self.assertEqual(self.report["stage_windows"], self.expected["stage_windows"])
        self.assertEqual(compare_revision(self.report, self.historical), self.expected["comparisons"])
        self.assertEqual([row["source_frames"] for row in self.report["stage_windows"]],
            [list(range(40, 46)), list(range(103, 108)), list(range(162, 167)), list(range(224, 230))])
        self.assertEqual(self.expected["runtime_commit"], RUNTIME_COMMIT)

    def test_real_auxiliary_values_do_not_claim_hand_racket_observations(self):
        for card_id in sorted(CHANGED):
            rows = [row for row in instances(self.report) if row["indicator_id"] == card_id]
            measurements = [row["auxiliary_observations"]["measurements"]["wrist_separation_torso"] for row in rows]
            self.assertAlmostEqual(measurements[0]["statistics"]["delta"], -0.0846946610547554)
            self.assertEqual([row["statistics"].get("delta") for row in measurements[1:]], [None, None, None])
            self.assertEqual([row["total_frame_count"] for row in measurements], [6, 5, 5, 6])
            self.assertEqual([row["score"] for row in rows], [None] * 4)
            self.assertTrue(all(row["missing_required_observables"] for row in rows))

    def test_restoring_old_scores_or_conditions_is_rejected(self):
        for field, value in (("score", 20), ("grade", "E"), ("conditions", [{"passed": False}])):
            mutated = deepcopy(self.report)
            target = next(row for row in instances(mutated) if row["indicator_id"] in CHANGED)
            target[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "must_not_score"):
                compare_revision(mutated, self.historical)

    def test_changed_auxiliary_measurement_is_rejected(self):
        mutated = deepcopy(self.report)
        target = next(row for row in instances(mutated) if row["indicator_id"] in CHANGED)
        target["auxiliary_observations"]["measurements"]["wrist_separation_torso"]["statistics"]["delta"] = 0
        with self.assertRaisesRegex(ValueError, "wrist_measurement_changed"):
            compare_revision(mutated, self.historical)

    def test_unaffected_condition_or_result_changes_are_rejected(self):
        mutated = deepcopy(self.report)
        target = next(row for row in instances(mutated) if row["indicator_id"] not in CHANGED)
        target["conditions"][0]["value"] += 1
        with self.assertRaisesRegex(ValueError, "unaffected_card_instance_changed"):
            compare_revision(mutated, self.historical)

    def test_changed_source_frame_ledger_is_rejected(self):
        mutated = deepcopy(self.report)
        target = instances(mutated)[0]
        target["source_stage_evidence"]["source_frames"].append(46)
        with self.assertRaisesRegex(ValueError, "source_window_changed"):
            compare_revision(mutated, self.historical)

    def test_duplicate_instance_is_rejected(self):
        mutated = deepcopy(self.report)
        card = next(card for card in mutated["cards"] if card["instances"])
        card["instances"].append(deepcopy(card["instances"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate_card_event_instance"):
            compare_revision(mutated, self.historical)

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "preserve_existing_replay_output"):
                replay(ROOT, ROOT, ROOT, Path(directory))

    def test_original_media_new_producer_and_unified_cache_when_available(self):
        artifact = Path(os.environ.get("CARD_ARTIFACT_ROOT", str(ROOT)))
        repository = Path(os.environ.get("CARD_REPOSITORY_ROOT", str(artifact)))
        if not (artifact / self.manifest["case"]["local_video"]).is_file():
            self.skipTest("original_MIT_video_and_review_artifacts_unavailable")
        original_fixture = sha256(ROOT / "tests/fixtures/gs-follow-through-results-20261024.json")
        with tempfile.TemporaryDirectory() as directory:
            result = replay(ROOT, artifact, repository, Path(directory) / "new-replay")
            for field in ("instances", "stage_windows", "comparisons", "summary", "unified_summary", "catalogue_cache_invalidation"):
                self.assertEqual(result[field], self.expected[field])
            self.assertTrue(result["runtime_source_digests"])
            self.assertEqual(result["unified_summary"]["summary"]["status_counts"], {"scored": 3, "unavailable": 72})
            cache = result["catalogue_cache_invalidation"]
            self.assertEqual(cache["after_restored_current_catalogue"]["misses"], 2)
            self.assertEqual(cache["after_unchanged_repeated_call"]["hits"], 1)
        self.assertEqual(sha256(ROOT / "tests/fixtures/gs-follow-through-results-20261024.json"), original_fixture)

    def test_relocated_same_media_rebinds_only_copied_annotation_summary_hash(self):
        artifact = Path(os.environ.get("CARD_ARTIFACT_ROOT", str(ROOT)))
        repository = Path(os.environ.get("CARD_REPOSITORY_ROOT", str(artifact)))
        case = self.manifest["case"]
        if not (artifact / case["local_video"]).is_file():
            self.skipTest("original_MIT_video_and_review_artifacts_unavailable")
        paths = set(self.expected["review_source_sha256"])
        paths.add(case["local_video"])
        paths.update(case["run_dir"] + "/" + name for name in self.expected["source_input_sha256"])
        with tempfile.TemporaryDirectory() as directory:
            relocated = Path(directory) / "relocated-input"
            for name in paths:
                destination = relocated / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifact / name, destination)
            output = Path(directory) / "new-replay"
            result = replay(ROOT, relocated, repository, output)
            migration = result["source_path_migration"]
            self.assertTrue(migration["summary_video_path_changed"])
            self.assertNotEqual(migration["source_summary_sha256"], migration["copied_summary_sha256"])
            self.assertNotEqual(migration["source_annotations_sha256"], migration["copied_annotations_sha256"])
            original_document = read(output / "gs-stage-annotations.json")
            copied_document = read(output / "unified-gs-stage-annotations.json")
            self.assertEqual(copied_document["input_artifact_sha256"]["summary.json"], migration["copied_summary_sha256"])
            copied_document["input_artifact_sha256"]["summary.json"] = migration["source_summary_sha256"]
            self.assertEqual(copied_document, original_document)
            self.assertEqual(result["instances"], self.expected["instances"])
            self.assertEqual(result["unified_summary"], self.expected["unified_summary"])
            for name, digest in self.expected["source_input_sha256"].items():
                self.assertEqual(sha256(relocated / case["run_dir"] / name), digest)


if __name__ == "__main__":
    unittest.main()
