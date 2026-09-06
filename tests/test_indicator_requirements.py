from __future__ import annotations

import json
import importlib.util
import unittest
from pathlib import Path

from rallymate_annotation.truth_pack import (
    REQUIRED_PHASES_BY_INDICATOR,
    SEMANTIC_REQUIREMENTS_BY_INDICATOR,
    TRUTH_PACK_VERSION,
)
from rallymate_scoring.feasibility import load_feasibility_registry
from rallymate_scoring.indicator_requirements import (
    build_indicator_requirements_snapshot,
    canonical_sha256,
    required_phase_keys,
    validate_indicator_requirements_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]


class IndicatorRequirementsTests(unittest.TestCase):
    def test_real_thirteen_indicator_requirement_matrix_is_closed(self) -> None:
        registry = load_feasibility_registry(
            ROOT / "metric-feasibility-pose-wave-v2.json"
        )
        indicators = {
            item["indicator_id"]: item for item in registry["indicators"]
        }
        self.assertEqual(set(indicators), set(REQUIRED_PHASES_BY_INDICATOR))
        self.assertEqual(set(indicators), set(SEMANTIC_REQUIREMENTS_BY_INDICATOR))
        for indicator_id, indicator in indicators.items():
            self.assertEqual(
                required_phase_keys(indicator),
                sorted(REQUIRED_PHASES_BY_INDICATOR[indicator_id]),
            )

        generated = build_indicator_requirements_snapshot(
            registry=registry,
            semantic_requirements=SEMANTIC_REQUIREMENTS_BY_INDICATOR,
            requirements_version="test-generated-v1",
            semantic_contract_version=TRUTH_PACK_VERSION,
        )
        validated = validate_indicator_requirements_snapshot(generated)
        self.assertEqual(set(validated), set(indicators))
        for indicator_id, item in validated.items():
            self.assertEqual(
                item["required_feature_names"],
                indicators[indicator_id]["required_features"],
            )
            self.assertEqual(
                item["registry_indicator_sha256"],
                canonical_sha256(indicators[indicator_id]),
            )

    def test_checked_in_snapshot_matches_current_registry_and_truth_contract(self) -> None:
        registry = load_feasibility_registry(
            ROOT / "metric-feasibility-pose-wave-v2.json"
        )
        checked_in = json.loads(
            (ROOT / "indicator-scoring-requirements-pose-wave-v1.json").read_text(
                encoding="utf-8"
            )
        )
        validated = validate_indicator_requirements_snapshot(checked_in)
        self.assertEqual(
            checked_in["source"]["registry_content_sha256"],
            canonical_sha256(registry),
        )
        expected_semantics = {
            indicator_id: sorted(set(values))
            for indicator_id, values in SEMANTIC_REQUIREMENTS_BY_INDICATOR.items()
        }
        self.assertEqual(
            checked_in["source"]["semantic_contract_sha256"],
            canonical_sha256(expected_semantics),
        )
        self.assertEqual(len(validated), 13)

    def test_default_builder_exactly_reproduces_checked_in_snapshot(self) -> None:
        script_path = ROOT / "scripts" / "build_indicator_scoring_requirements.py"
        spec = importlib.util.spec_from_file_location(
            "test_build_indicator_scoring_requirements", script_path
        )
        if spec is None or spec.loader is None:
            self.fail(f"cannot load builder: {script_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        registry = load_feasibility_registry(
            ROOT / "metric-feasibility-pose-wave-v2.json"
        )
        generated = build_indicator_requirements_snapshot(
            registry=registry,
            semantic_requirements=SEMANTIC_REQUIREMENTS_BY_INDICATOR,
            requirements_version=module.DEFAULT_REQUIREMENTS_VERSION,
            semantic_contract_version=TRUTH_PACK_VERSION,
        )
        checked_in = json.loads(
            (ROOT / "indicator-scoring-requirements-pose-wave-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            "pose-wave-indicator-requirements-2026-08-22.17",
            module.DEFAULT_REQUIREMENTS_VERSION,
        )
        self.assertEqual(checked_in, generated)

    def test_snapshot_rejects_shrunk_or_duplicate_requirements(self) -> None:
        payload = json.loads(
            (ROOT / "indicator-scoring-requirements-pose-wave-v1.json").read_text(
                encoding="utf-8"
            )
        )
        payload["indicators"][0]["required_feature_names"] = []
        with self.assertRaisesRegex(ValueError, "required_feature_names"):
            validate_indicator_requirements_snapshot(payload)


if __name__ == "__main__":
    unittest.main()
