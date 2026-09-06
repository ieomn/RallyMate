from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "audit_feature_observation_gaps.py"
    spec = importlib.util.spec_from_file_location("audit_feature_observation_gaps", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FeatureObservationGapAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script()

    def test_real_audit_is_exact_non_scoring_and_no_profile_cherry_pick(self) -> None:
        bundle = ROOT / "reports" / "scoring-candidate-multivideo-m63" / "runs" / "850cb0006b406c7176eeda8d711cd065"
        output = ROOT / "reports" / "measurement-recovery-m66" / "gap-audits" / "850cb0006b406c7176eeda8d711cd065.json"
        report = json.loads(output.read_text(encoding="utf-8")) if output.exists() else self.module.audit_feature_observation_gaps(
            indicator_features_path=bundle / "indicator-features.jsonl",
            features_path=bundle / "features.jsonl",
            events_path=bundle / "events.jsonl",
            summary_path=bundle / "scoring-loop-summary.json",
            registry_path=ROOT / "metric-feasibility-pose-wave-v2.json",
        )
        self.module.validate_feature_observation_gap_audit(report)
        self.assertEqual(442, report["counts"]["indicator_record_count"])
        self.assertEqual(403, report["counts"]["feature_measured_indicator_count"])
        self.assertEqual(39, report["counts"]["feature_unavailable_indicator_count"])
        self.assertEqual(11, report["counts"]["affected_event_count"])
        self.assertEqual(138, report["counts"]["unique_invalid_event_feature_count"])
        reasons = {item["reason"]: item["unique_event_feature_count"] for item in report["reason_metrics"]}
        self.assertEqual(134, reasons["valid_fraction_below_quality_gate"])
        self.assertEqual(403, report["counts"]["feature_vector_complete_indicator_count"])
        self.assertEqual(39, report["counts"]["feature_vector_incomplete_indicator_count"])
        self.assertEqual(3, report["counts"]["measurement_hard_fail_indicator_count"])
        self.assertEqual(0, report["counts"]["measurement_hard_fail_only_indicator_count"])
        self.assertEqual([], report["profile_alternative_metrics"])
        self.assertFalse(report["assertions"]["automatic_profile_fallback_enabled"])
        self.assertEqual(0, report["counts"]["grade_count"])

    def test_compact_full_feature_tamper_is_rejected(self) -> None:
        bundle = ROOT / "reports" / "scoring-candidate-multivideo-m53" / "runs" / "850cb0006b406c7176eeda8d711cd065"
        rows = [json.loads(line) for line in (bundle / "indicator-features.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        target = next(row for row in rows if row["feature_status"] == "unavailable")
        next(item for item in target["features"] if not item["valid"])["reason"] = "tampered_reason"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "compact/full feature mismatch"):
                self.module.audit_feature_observation_gaps(
                    indicator_features_path=path,
                    features_path=bundle / "features.jsonl",
                    events_path=bundle / "events.jsonl",
                    summary_path=bundle / "scoring-loop-summary.json",
                    registry_path=ROOT / "metric-feasibility-pose-wave-v2.json",
                )

    def test_measurement_hard_fail_gate_cannot_be_removed_from_complete_vector(self) -> None:
        bundle = (
            ROOT
            / "reports"
            / "scoring-candidate-multivideo-m63"
            / "runs"
            / "3ae77ee3271d67de171585a5c39ddd69"
        )
        rows = [
            json.loads(line)
            for line in (bundle / "indicator-features.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        target = next(
            row
            for row in rows
            if row["feature_status"] == "unavailable"
            and all(item["valid"] for item in row["features"])
        )
        target["quality_gate"]["measurement_allowed"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "quality gate"):
                self.module.audit_feature_observation_gaps(
                    indicator_features_path=path,
                    features_path=bundle / "features.jsonl",
                    events_path=bundle / "events.jsonl",
                    summary_path=bundle / "scoring-loop-summary.json",
                    registry_path=ROOT / "metric-feasibility-pose-wave-v2.json",
                )


if __name__ == "__main__":
    unittest.main()
