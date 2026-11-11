"""The MD ranges are registered without claiming reserved fields are measured."""
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from rallymate_features.perception_dictionary import (
    CANONICAL_PERCEPTION_IDS, PERCEPTION_ALIASES, PERCEPTION_DICTIONARY_VERSION,
    PERCEPTION_ID_RANGES, normalize_perception_values, perception_definition, resolve_perception_ids,
)


class PerceptionDictionaryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/perception-measurement.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def test_all_guide_ranges_and_special_ids_exist_exactly_once(self):
        for prefix, (first, last) in PERCEPTION_ID_RANGES.items():
            expected = {f"{prefix}{index:03d}" for index in range(first, last + 1)}
            self.assertLessEqual(expected, CANONICAL_PERCEPTION_IDS)
        for item in ("J168", "J179", "V001", "S013_BASE_WIDTH", "S014_WEIGHT_DISTRIBUTION"):
            self.assertIn(item, CANONICAL_PERCEPTION_IDS)
        enum = self.schema["properties"]["feature_id"]["enum"]
        self.assertEqual(len(enum), len(set(enum)))
        self.assertEqual(set(enum), CANONICAL_PERCEPTION_IDS - {"S013"})
        self.assertEqual(len(CANONICAL_PERCEPTION_IDS), 551)

    def test_alias_targets_are_canonical_and_unknown_ids_fail(self):
        for alias, targets in PERCEPTION_ALIASES.items():
            self.assertEqual(resolve_perception_ids(alias), targets)
            self.assertLessEqual(set(targets), CANONICAL_PERCEPTION_IDS)
        for unknown in ("J180", "BALL-099", "RK024", "TIME021"):
            with self.assertRaises(ValueError):
                resolve_perception_ids(unknown)

    def test_reserved_is_not_an_implemented_measurement(self):
        definitions = [perception_definition(item) for item in CANONICAL_PERCEPTION_IDS]
        reserved = [item for item in definitions if item["semantic_status"] == "reserved"]
        self.assertGreater(len(reserved), 500)
        self.assertTrue(all(item["measurement_available"] is False for item in reserved))
        self.assertTrue(all(item["name"] is None for item in reserved))
        self.assertEqual(perception_definition("V001")["semantic_status"], "defined_by_guide")
        self.assertFalse(perception_definition("BALL-030")["measurement_available"])

    def test_measured_requires_value_and_source_frame_and_missing_cannot_carry_value(self):
        record = {"feature_id": "L030", "status": "observed", "value": .25, "unit": "frame_long_edge",
                  "source_frames": [12], "dictionary_version": PERCEPTION_DICTIONARY_VERSION}
        self.validator.validate(record)
        for changes in ({"source_frames": []}, {"value": None}, {"value": True},
                        {"status": "unavailable"}, {"feature_id": "S013"}):
            self.assertTrue(list(self.validator.iter_errors({**record, **changes})), changes)
        self.validator.validate({**record, "status": "unavailable", "value": None, "source_frames": []})

    def test_overloaded_s013_never_copies_one_value_to_both_semantics(self):
        self.assertEqual(normalize_perception_values({"S013": .5}), {"S013": .5})
        self.assertEqual(normalize_perception_values({"S013": {"base_width": .5}}),
                         {"S013": {"base_width": .5}, "S013_BASE_WIDTH": .5})


if __name__ == "__main__":
    unittest.main()
