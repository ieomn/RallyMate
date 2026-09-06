from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_scoring.feasibility import (
    POSE_WAVE_V2_INDICATORS,
    TARGET_INDICATORS,
    load_feasibility_registry,
)
from rallymate_scoring.scoring_context import SCORING_CONTEXT_FEATURE_DEFINITIONS


ROOT = Path(__file__).resolve().parents[1]
V1_PATH = ROOT / "metric-feasibility.json"
V2_PATH = ROOT / "metric-feasibility-pose-wave-v2.json"


class FeasibilityRegistryV2Tests(unittest.TestCase):
    def _load_temporary(self, payload: dict) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metric-feasibility.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            return load_feasibility_registry(path)

    def test_v1_contract_remains_exactly_the_original_six(self) -> None:
        registry = load_feasibility_registry(V1_PATH)
        self.assertEqual(registry["schema_version"], "1.0.0")
        self.assertEqual(
            {item["indicator_id"] for item in registry["indicators"]},
            TARGET_INDICATORS,
        )

    def test_pose_wave_v2_contains_current_thirteen_indicator_event_scope(self) -> None:
        registry = load_feasibility_registry(V2_PATH)
        self.assertEqual(registry["schema_version"], "2.0.0")
        self.assertEqual(len(registry["indicators"]), 13)
        self.assertEqual(registry["scope"]["indicator_count"], 13)
        self.assertEqual(
            {item["indicator_id"] for item in registry["indicators"]},
            POSE_WAVE_V2_INDICATORS,
        )
        self.assertTrue(TARGET_INDICATORS < POSE_WAVE_V2_INDICATORS)

    def test_expanded_metrics_match_implemented_feature_contract(self) -> None:
        registry = load_feasibility_registry(V2_PATH)
        by_id = {item["indicator_id"]: item for item in registry["indicators"]}
        implemented = set(FEATURE_DEFINITIONS) | set(
            SCORING_CONTEXT_FEATURE_DEFINITIONS
        )
        for indicator_id in POSE_WAVE_V2_INDICATORS:
            indicator = by_id[indicator_id]
            self.assertEqual(indicator["feasibility_level"], "F2")
            missing = set(indicator["required_features"]) - implemented
            self.assertFalse(missing)
            if "feature_availability" in indicator:
                self.assertEqual(
                    missing,
                    set(indicator["feature_availability"]["missing"]),
                )
                self.assertEqual(
                    set(indicator["feature_availability"]["implemented"]),
                    set(indicator["required_features"]),
                )

        # F2 means the declared kinematic proxies are measurable.  It does not
        # turn them into direct foot-contact or pressure observations.
        self.assertIn(
            "ground_contact_not_directly_observable",
            by_id["FS09-M02"]["current_blockers"],
        )

    def test_v2_accepts_an_arbitrary_non_duplicate_indicator_set(self) -> None:
        registry = load_feasibility_registry(V2_PATH)
        arbitrary = copy.deepcopy(registry)
        arbitrary["indicators"] = [copy.deepcopy(registry["indicators"][0])]
        arbitrary["indicators"][0]["indicator_id"] = "FS10-M05"
        arbitrary["scope"]["indicator_count"] = 1
        loaded = self._load_temporary(arbitrary)
        self.assertEqual(
            [item["indicator_id"] for item in loaded["indicators"]],
            ["FS10-M05"],
        )

    def test_v2_rejects_duplicate_indicator_ids(self) -> None:
        registry = load_feasibility_registry(V2_PATH)
        duplicate = copy.deepcopy(registry)
        duplicate["indicators"] = [
            copy.deepcopy(registry["indicators"][0]),
            copy.deepcopy(registry["indicators"][0]),
        ]
        with self.assertRaisesRegex(ValueError, "indicator_id values must be unique"):
            self._load_temporary(duplicate)

    def test_v2_rejects_stale_scope_count_and_feature_partition(self) -> None:
        registry = load_feasibility_registry(V2_PATH)
        stale_count = copy.deepcopy(registry)
        stale_count["scope"]["indicator_count"] = 12
        with self.assertRaisesRegex(ValueError, "indicator_count"):
            self._load_temporary(stale_count)

        stale_features = copy.deepcopy(registry)
        indicator = next(
            item
            for item in stale_features["indicators"]
            if "feature_availability" in item
        )
        indicator["feature_availability"]["implemented"].pop()
        with self.assertRaisesRegex(ValueError, "partition required_features"):
            self._load_temporary(stale_features)

    def test_v1_still_rejects_expanded_indicator_sets(self) -> None:
        registry = json.loads(V1_PATH.read_text(encoding="utf-8"))
        extra = copy.deepcopy(registry["indicators"][0])
        extra["indicator_id"] = "FS09-M01"
        registry["indicators"].append(extra)
        with self.assertRaisesRegex(ValueError, "six target indicators"):
            self._load_temporary(registry)

    def test_v2_schema_declares_extensible_non_empty_indicator_array(self) -> None:
        schema = json.loads(
            (ROOT / "contracts" / "metric-feasibility-v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], "2.0.0")
        indicators = schema["properties"]["indicators"]
        self.assertEqual(indicators["minItems"], 1)
        self.assertNotIn("maxItems", indicators)


if __name__ == "__main__":
    unittest.main()
