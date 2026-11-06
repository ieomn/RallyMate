from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from rallymate_scoring.artifact_evidence import CODEC_VERSION, POOL_FILENAME
from rallymate_scoring.run_bundle_binding import (
    ScoringRunBundleBindingError, scoring_run_bundle_identity,
    scoring_run_bundle_root_sha256,
)
from tests.test_scoring_run_bundle_binding import _summary, _ledger


class ScoringRunBundleEvidenceTests(unittest.TestCase):
    def schema_validator(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/trusted-scoring-run-bundles.schema.json").read_text("utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, format_checker=FormatChecker())

    def summary(self):
        summary = _summary()
        summary["evidence_storage"] = {
            "codec": CODEC_VERSION, "path": POOL_FILENAME,
            "sha256": "b" * 64, "binding_sha256": "c" * 64,
            "video_id": summary["video_id"], "source_id": "source-job",
            "video_sha256": "a" * 64,
        }
        return summary

    def test_v2_rejects_source_artifact_without_matching_hash(self):
        summary = self.summary()
        summary["artifacts"]["source_aligned_measurements_json"] = "source-aligned-measurements.json"
        with self.assertRaisesRegex(ScoringRunBundleBindingError, "same files"):
            scoring_run_bundle_identity(summary, scoring_summary_sha256="f" * 64)

    def test_v2_rejects_source_hash_without_matching_artifact_path(self):
        summary = self.summary()
        summary["artifact_sha256"]["source_aligned_measurements_json"] = "d" * 64
        with self.assertRaisesRegex(ScoringRunBundleBindingError, "same files"):
            scoring_run_bundle_identity(summary, scoring_summary_sha256="f" * 64)

    def test_v2_binds_optional_source_hash_when_both_are_present(self):
        summary = self.summary()
        summary["artifacts"]["source_aligned_measurements_json"] = "source-aligned-measurements.json"
        summary["artifact_sha256"]["source_aligned_measurements_json"] = "d" * 64
        identity = scoring_run_bundle_identity(summary, scoring_summary_sha256="f" * 64)
        before = scoring_run_bundle_root_sha256(identity)
        self.assertEqual(identity["binding_version"], "scoring-run-bundle-v2")
        self.assertEqual(identity["artifact_sha256"]["source_aligned_measurements_json"], "d" * 64)
        summary["artifact_sha256"]["source_aligned_measurements_json"] = "e" * 64
        after = scoring_run_bundle_root_sha256(scoring_run_bundle_identity(summary, scoring_summary_sha256="f" * 64))
        self.assertNotEqual(before, after)

    def test_v2_without_optional_source_artifact_remains_valid(self):
        identity = scoring_run_bundle_identity(self.summary(), scoring_summary_sha256="f" * 64)
        self.assertTrue(scoring_run_bundle_root_sha256(identity))

    def test_published_schema_accepts_both_valid_versions(self):
        validator = self.schema_validator()
        validator.validate(_ledger())
        validator.validate(_ledger(self.summary()))
        summary = self.summary()
        summary["artifacts"]["source_aligned_measurements_json"] = "source-aligned-measurements.json"
        summary["artifact_sha256"]["source_aligned_measurements_json"] = "d" * 64
        validator.validate(_ledger(summary))

    def test_published_schema_keeps_version_specific_storage_requirements_strict(self):
        validator = self.schema_validator()
        v2 = _ledger(self.summary())
        without_storage = copy.deepcopy(v2)
        del without_storage["entries"][0]["bundle_identity"]["evidence_storage"]
        v1_with_storage = copy.deepcopy(v2)
        v1_with_storage["entries"][0]["bundle_identity"]["binding_version"] = "scoring-run-bundle-v1"
        v1_with_new_artifact = _ledger()
        v1_with_new_artifact["entries"][0]["bundle_identity"]["artifact_sha256"]["source_aligned_measurements_json"] = "d" * 64
        unknown_storage_field = copy.deepcopy(v2)
        unknown_storage_field["entries"][0]["bundle_identity"]["evidence_storage"]["extra"] = "unsupported"
        for invalid in (without_storage, v1_with_storage, v1_with_new_artifact, unknown_storage_field):
            self.assertTrue(list(validator.iter_errors(invalid)))


if __name__ == "__main__":
    unittest.main()
