from __future__ import annotations

import json
from importlib.resources import files
import unittest

from rallymate_scoring.training_dimensions import (
    DIMENSION_NAMES, EXPECTED_CARD_COUNTS, dimension_for_card, source_basis_for_dimension,
)


class TrainingDimensionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads(files("rallymate_scoring").joinpath("data/technical_review_rules.json").read_text(encoding="utf-8"))

    def test_all_298_source_cards_have_exactly_one_display_group(self):
        bases = [source_basis_for_dimension(key) for key in DIMENSION_NAMES]
        ids = [card for basis in bases for card in basis["card_ids"]]
        self.assertEqual(len(ids), 298)
        self.assertEqual(len(set(ids)), 298)
        self.assertEqual(set(ids), {card["indicator_id"] for card in self.catalog["rules"]})
        self.assertEqual({key: source_basis_for_dimension(key)["card_count"] for key in DIMENSION_NAMES}, EXPECTED_CARD_COUNTS)
        self.assertEqual(source_basis_for_dimension("transition")["card_ids"], [])
        self.assertIsNone(dimension_for_card("GS99-M01-01"))

    def test_slice_preparation_stages_are_not_mistaken_for_drive_swing(self):
        for technique in ("GS04", "GS05"):
            for stage in (4, 5, 6):
                self.assertEqual(dimension_for_card(f"{technique}-M{stage:02d}-01"), "preparation")
            self.assertEqual(dimension_for_card(f"{technique}-M07-01"), "swing")
        for technique in ("GS01", "GS02", "GS03"):
            for stage in (4, 5, 6, 7):
                self.assertEqual(dimension_for_card(f"{technique}-M{stage:02d}-01"), "swing")
        self.assertEqual(dimension_for_card("FS10-M05"), "follow_through")
        self.assertEqual(dimension_for_card("GS01-M01-01"), "preparation")

    def test_source_conflicts_and_missing_boundaries_survive_grouping_unchanged(self):
        expected = {card["indicator_id"]: {"indicator_id": card["indicator_id"],
                    "manual_grading_allowed": card["manual_grading_allowed"], "blockers": card["blockers"]}
                    for card in self.catalog["rules"] if card["blockers"]}
        actual = {card["indicator_id"]: card for key in DIMENSION_NAMES
                  for card in source_basis_for_dimension(key)["source_blockers"]}
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 100)
        for identifier in ("GS01-M10-04", "GS02-M01-01", "GS02-M01-02"):
            self.assertFalse(actual[identifier]["manual_grading_allowed"])

    def test_grouping_is_proposed_source_bound_and_never_grades_cards(self):
        for identifier in DIMENSION_NAMES:
            basis = source_basis_for_dimension(identifier)
            self.assertTrue(basis["proposed_not_original"])
            self.assertEqual(basis["grouping_origin"], "proposed_source_stage_grouping")
            self.assertEqual(basis["source_reference_sha256"], self.catalog["source_reference_sha256"])
            self.assertEqual({row["id"] for row in basis["source_documents"]}, {"CARD-FS", "CARD-GS"})
            self.assertTrue(basis["source_anchors"])
            self.assertNotIn("score", basis)
            self.assertNotIn("grade", basis)
        first = source_basis_for_dimension("contact")
        first["card_ids"].clear()
        first["source_documents"][0]["sha256"] = "changed"
        self.assertEqual(source_basis_for_dimension("contact")["card_count"], 25)
        self.assertEqual(len(source_basis_for_dimension("contact")["card_ids"]), 25)
        self.assertNotEqual(source_basis_for_dimension("contact")["source_documents"][0]["sha256"], "changed")


if __name__ == "__main__":
    unittest.main()
