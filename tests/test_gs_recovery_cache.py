"""A finished report must not survive a change to the M10 evidence contract.

The real cached parser path is exercised with recorded source rows. An invalid
GS document is deliberate: this test establishes cache behavior, not a grade.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring import gs_recovery_evidence as recovery
from rallymate_service import card_execution as execution


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ("VERSION", "POLICY_VERSION", "SCORING_REVISION_ID",
            "REFERENCE_VERSION", "AUXILIARY_VERSION")


class GSRecoveryCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = json.loads((ROOT / "tests/fixtures/gs-card-real-mit-preparation-v1.json")
                              .read_text(encoding="utf-8"))["cases"][0]

    def setUp(self):
        execution.clear_card_execution_cache()
        self.addCleanup(execution.clear_card_execution_cache)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / self.case["source_job_id"]
        self.directory.mkdir()
        for name, rows in (("frames.jsonl", self.case["frame_records"]),
                           ("primary-player.jsonl", self.case["primary_timeline"]),
                           ("events.jsonl", [])):
            (self.directory / name).write_text("".join(json.dumps(row) + "\n" for row in rows),
                                              encoding="utf-8")

    def test_each_recovery_version_invalidates_cached_gs_report(self):
        (self.directory / "gs-stage-annotations.json").write_text("{}\n", encoding="utf-8")
        with patch.object(execution, "_read_records", wraps=execution._read_records) as reader:
            baseline = execution.build_card_execution_report(self.directory)
            self.assertEqual(reader.call_count, 3)
            self.assertEqual(execution.build_card_execution_report(self.directory), baseline)
            self.assertEqual(reader.call_count, 3)
            for version in VERSIONS:
                with self.subTest(version=version), patch.object(recovery, version,
                        getattr(recovery, version) + "-changed"):
                    calls = reader.call_count
                    changed = execution.build_card_execution_report(self.directory)
                    self.assertEqual(reader.call_count, calls + 3)
                    self.assertEqual(changed, baseline)
                    preserved = deepcopy(changed)
                    changed["cards"][0]["run_status"] = "caller-mutation"
                    self.assertEqual(execution.build_card_execution_report(self.directory), preserved)
                    self.assertEqual(reader.call_count, calls + 3)

    def test_without_gs_annotations_recovery_versions_do_not_change_fs_cache(self):
        with patch.object(execution, "_read_records", wraps=execution._read_records) as reader:
            baseline = execution.build_card_execution_report(self.directory)
            self.assertEqual(reader.call_count, 3)
            for version in VERSIONS:
                with self.subTest(version=version), patch.object(recovery, version,
                        getattr(recovery, version) + "-changed"):
                    self.assertEqual(execution.build_card_execution_report(self.directory), baseline)
                    self.assertEqual(reader.call_count, 3)


if __name__ == "__main__":
    unittest.main()
