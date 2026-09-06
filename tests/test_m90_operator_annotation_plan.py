from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

import rallymate_annotation.scoring_truth_authorization as authorization
from rallymate_annotation.scoring_truth_event_handoff import (
    validate_scoring_truth_event_handoff,
)


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "data" / "analysis-plans" / "scoring-truth-event-m89-operator-plan-v2.json"
PLAN_SCHEMA_PATH = ROOT / "contracts" / "scoring-truth-event-annotation-plan.schema.json"
HANDOFF_DIR = ROOT / "data" / "annotations" / "scoring-truth-event-handoff-m89-v1"


class M90OperatorAnnotationPlanTest(unittest.TestCase):
    def test_frozen_zero_label_plan_matches_schema_runtime_and_exact_m89_handoff(self) -> None:
        raw = PLAN_PATH.read_bytes()
        plan = json.loads(raw)
        self.assertEqual(
            "BE1A8BDB73DB48982F3737C53EBD5C116A2B413CDA05B5EBD057163B112BDC3F",
            hashlib.sha256(raw).hexdigest().upper(),
        )
        schema = json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(plan)
        created_at, frozen_at = authorization._validate_plan(plan)
        self.assertLessEqual(created_at, frozen_at)

        handoff = validate_scoring_truth_event_handoff(HANDOFF_DIR)
        manifest = handoff["manifest"]
        expected_technical = {
            "manifest_raw_sha256": handoff["manifest_sha256"],
            "bundle_version": manifest["bundle_version"],
            "bundle_id": handoff["bundle_id"],
            "content_root_sha256": handoff["content_root_sha256"],
            "source_projection_sha256": manifest["source_authority"]["source_contract_sha256"],
        }
        self.assertEqual(
            "D6EF62986DEB550F6847073ADF28EC702C35F98CD3EFDA66F573594132E345DB",
            hashlib.sha256((HANDOFF_DIR / "event-handoff-manifest.json").read_bytes()).hexdigest().upper(),
        )
        self.assertEqual(expected_technical, {key: plan["technical_handoff"][key] for key in expected_technical})
        self.assertEqual(authorization.EVENT_CODES, plan["scope"]["event_codes"])
        self.assertEqual(authorization.PHASE_KEYS_BY_EVENT, plan["scope"]["phase_keys_by_event"])
        self.assertEqual(authorization.TASKS, plan["scope"]["tasks"])
        self.assertEqual(authorization.ROLES, plan["roles"])
        self.assertTrue(plan["decision_contract"]["first_write_requires_verified_operator_release"])
        self.assertTrue(plan["decision_contract"]["full_video_review_before_submit"])
        self.assertTrue(plan["safety"]["operator_release_record_required"])
        self.assertFalse(plan["safety"]["grades_or_thresholds_supported"])
        self.assertFalse(plan["safety"]["calibration_authorized"])
        self.assertFalse(plan["safety"]["promotion_authorized"])
        self.assertFalse(plan["safety"]["production_enabled"])
        self.assertNotIn("release_record", plan)
        self.assertNotIn("annotations", plan)
        self.assertNotIn("labels", plan)
        self.assertTrue(raw.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
