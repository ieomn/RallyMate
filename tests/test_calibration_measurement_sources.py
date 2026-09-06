from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.calibration_measurement_sources import (
    build_calibration_measurement_sources,
    load_latest_calibration_measurement_sources,
    validate_calibration_measurement_sources,
)


ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "reports" / "scoring-truth-measurement-sources" / "latest.json"
TRUTH_MANIFEST = ROOT / "data" / "annotations" / "scoring-truth-pack-v1" / "manifest.json"
REGISTRY = ROOT / "metric-feasibility-pose-wave-v2.json"


class CalibrationMeasurementSourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not LATEST.is_file():
            raise unittest.SkipTest("published M49 measurement sources are missing")
        cls.manifest = load_latest_calibration_measurement_sources(LATEST)

    def test_published_sources_cover_all_truth_videos_with_one_current_pose_contract(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["video_count"], 3)
        self.assertEqual(manifest["total_frame_count"], 15868)
        self.assertEqual(
            [item["frame_count"] for item in manifest["sources"]],
            [1441, 2911, 11516],
        )
        self.assertTrue(
            all(
                item["primary_player_version"] == "primary-player-v0.3.0"
                for item in manifest["sources"]
            )
        )
        self.assertEqual(
            manifest["shared_pose_contract"]["model_name"],
            "rtmpose-m_halpe26_256x192",
        )
        self.assertEqual(
            manifest["shared_pose_contract"]["native_keypoint_format"], "halpe26"
        )
        self.assertFalse(manifest["safety"]["gpu_inference_executed"])
        self.assertTrue(manifest["safety"]["existing_pose_frames_reused"])

    def test_source_specs_must_exactly_cover_truth_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "exactly cover"):
                build_calibration_measurement_sources(
                    truth_manifest_path=TRUTH_MANIFEST,
                    feasibility_registry_path=REGISTRY,
                    source_specs=[],
                    output_root=Path(temporary),
                    source_set_id="missing-sources",
                )

    def test_stale_primary_version_and_unsafe_claim_fail_closed(self) -> None:
        stale = copy.deepcopy(self.manifest)
        stale["required_primary_player_version"] = "primary-player-v0.1.0"
        with self.assertRaisesRegex(ValueError, "version is stale"):
            validate_calibration_measurement_sources(stale)
        unsafe = copy.deepcopy(self.manifest)
        unsafe["safety"]["grades_generated"] = True
        with self.assertRaisesRegex(ValueError, "unsafe"):
            validate_calibration_measurement_sources(unsafe)

    def test_machine_schema_accepts_published_manifest(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (
                ROOT / "contracts" / "calibration-measurement-sources.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(self.manifest)


if __name__ == "__main__":
    unittest.main()
