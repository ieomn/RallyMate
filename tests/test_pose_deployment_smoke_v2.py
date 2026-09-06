from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

from rallymate_scoring.feasibility import load_feasibility_registry


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "smoke_pose_deployment_profile.py"
    spec = importlib.util.spec_from_file_location("smoke_pose_deployment_profile", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PoseDeploymentSmokeV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = _load_script()
        cls.registry = load_feasibility_registry(
            ROOT / "metric-feasibility-pose-wave-v2.json"
        )
        cls.indicator_ids = sorted(
            indicator["indicator_id"]
            for indicator in cls.registry["indicators"]
        )

    def _artifacts(self):
        events = [
            {"event_code": code}
            for code in sorted({"FS01", "FS02", "FS09"})
        ]
        indicators = [
            {"indicator_id": indicator_id} for indicator_id in self.indicator_ids
        ]
        scores = [
            {
                "indicator_id": indicator_id,
                "status": "calibration_required",
                "grade": None,
                "threshold_version": None,
            }
            for indicator_id in self.indicator_ids
        ]
        summary = {
            "result_state": {"target_indicator_count": len(self.indicator_ids)},
            "model_versions": {
                "feasibility_registry": self.registry["registry_version"]
            },
            "safety_assertions": {
                "any_non_null_grade_without_calibration": False,
                "fake_thresholds_generated": False,
            },
        }
        return events, indicators, scores, summary

    def test_contract_uses_all_registry_indicators_without_literal_six(self) -> None:
        events, indicators, scores, summary = self._artifacts()
        result = self.script._validate_scoring_contract(
            registry=self.registry,
            events=events,
            indicators=indicators,
            scores=scores,
            loop_summary=summary,
        )
        self.assertEqual(self.indicator_ids, result["indicator_ids"])
        self.assertEqual(13, len(result["indicator_ids"]))
        self.assertEqual(0, result["non_null_grade_count"])
        self.assertEqual(0, result["non_null_threshold_version_count"])

    def test_contract_rejects_missing_registry_indicator(self) -> None:
        events, indicators, scores, summary = self._artifacts()
        indicators.pop()
        with self.assertRaisesRegex(RuntimeError, "indicator contract mismatch"):
            self.script._validate_scoring_contract(
                registry=self.registry,
                events=events,
                indicators=indicators,
                scores=scores,
                loop_summary=summary,
            )

    def test_contract_rejects_uncalibrated_grade_or_threshold(self) -> None:
        events, indicators, scores, summary = self._artifacts()
        grade_scores = copy.deepcopy(scores)
        grade_scores[0]["grade"] = "A"
        with self.assertRaisesRegex(RuntimeError, "grade was emitted"):
            self.script._validate_scoring_contract(
                registry=self.registry,
                events=events,
                indicators=indicators,
                scores=grade_scores,
                loop_summary=summary,
            )

        threshold_scores = copy.deepcopy(scores)
        threshold_scores[0]["threshold_version"] = "invented-v1"
        with self.assertRaisesRegex(RuntimeError, "threshold version was emitted"):
            self.script._validate_scoring_contract(
                registry=self.registry,
                events=events,
                indicators=indicators,
                scores=threshold_scores,
                loop_summary=summary,
            )

    def test_launcher_exposes_three_profiles_and_explicit_v2_registry(self) -> None:
        source = (ROOT / "scripts" / "run_pose_deployment_smoke.ps1").read_text(
            encoding="utf-8-sig"
        )
        for profile in ("Online", "Analysis", "WholeBody"):
            self.assertIn(f'"{profile}"', source)
        self.assertIn("metric-feasibility-pose-wave-v2.json", source)
        self.assertIn("RALLYMATE_SCORING_FEASIBILITY_REGISTRY", source)
        self.assertIn("--registry", source)

    def test_smoke_report_language_is_dynamic_and_disclaims_accuracy(self) -> None:
        source = (
            ROOT / "scripts" / "smoke_pose_deployment_profile.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("six_indicator", source)
        self.assertNotIn("all_six", source)
        self.assertNotIn("required_indicators = {", source)
        self.assertIn('"accuracy_claim": False', source)
        self.assertIn('"no_threshold_without_calibration": True', source)


if __name__ == "__main__":
    unittest.main()
