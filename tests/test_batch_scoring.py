from __future__ import annotations

import unittest

from rallymate_scoring.batch import score_indicator_records
from rallymate_scoring.quality_policy import evaluate_indicator_event_quality
from tests.test_runtime_profile_binding import _bind, _runtime_fixture


def _record(valid: bool = True, quality_flags: list[str] | None = None) -> dict:
    record = {
        "video_id": "synthetic-video",
        "event_id": "synthetic-event",
        "event_code": "FS01",
        "person_track_id": 1,
        "indicator_id": "FS01-M02",
        "feasibility_level": "F4",
        "features": [
            {
                "feature_name": "synthetic_feature",
                "feature_version": "test-only",
                "value": 2.5 if valid else None,
                "unit": "test_unit",
                "confidence": 0.8,
                "valid": valid,
                "reason": "valid" if valid else "missing",
                "source_frames": [10],
            }
        ],
        "feature_status": "measured" if valid else "unavailable",
        "provenance": {
            "video_sha256": "b" * 64,
            "frames_sha256": "c" * 64,
        },
    }
    record["quality_gate"] = evaluate_indicator_event_quality(
        record["indicator_id"], quality_flags or []
    )
    if not record["quality_gate"]["measurement_allowed"]:
        record["feature_status"] = "unavailable"
    return record


def _calibration(status: str) -> dict:
    passed = status == "passed"
    return {
        "schema_version": "1.2.0",
        "backend": "threshold_rule",
        "threshold_version": "synthetic-thresholds-v1",
        "indicator_id": "FS01-M02",
        "primary_feature": "synthetic_feature",
        "primary_feature_version": "test-only",
        "unit": "test_unit",
        "direction": "higher_is_better",
        "thresholds": [1.0, 2.0, 3.0, 4.0],
        "source": "coach_ground_truth_calibration",
        "ground_truth_dataset_version": "synthetic-coach-truth-v1",
        "artifact_scope": "production",
        "independent_test": {
            "status": status,
            "approved_for_scoring": passed,
            "dataset_version": "synthetic-independent-v1" if passed else None,
            "report_version": "synthetic-report-v1" if passed else None,
            "report_sha256": "a" * 64 if passed else None,
            "acceptance_protocol_version": "synthetic-protocol-v1" if passed else None,
            "evaluated_at": "2026-08-13T00:00:00Z" if passed else None,
        },
    }


def _runtime_bound_record(fixture: dict, quality_flags: list[str]) -> dict:
    return {
        "video_id": fixture["evidence"]["video_id"],
        "event_id": "synthetic-event",
        "event_code": "FS01",
        "person_track_id": 1,
        "indicator_id": "FS01-M02",
        "feasibility_level": "F4",
        "features": [
            {
                "feature_name": "feature_a",
                "feature_version": "feature-a-v1",
                "value": 20.0,
                "unit": "body",
                "confidence": 0.8,
                "valid": True,
                "reason": "valid",
                "source_frames": [10],
            }
        ],
        "feature_status": "measured",
        "quality_gate": evaluate_indicator_event_quality(
            "FS01-M02", quality_flags
        ),
        "provenance": {
            "video_sha256": fixture["evidence"]["video_sha256"],
            "frames_sha256": "c" * 64,
        },
    }


def _authoritative_event(fixture: dict, quality_flags: list[str]) -> dict:
    return {
        "schema_version": "1.0.0",
        "video_id": fixture["evidence"]["video_id"],
        "event_id": "synthetic-event",
        "event_code": "FS01",
        "person_track_id": 1,
        "start_ms": 0,
        "end_ms": 100,
        "key_phases_ms": {"preload_ms": 50},
        "confidence": 0.9,
        "boundary_uncertainty_ms": 10,
        "quality_flags": quality_flags,
        "provenance": {"detector_version": "synthetic-event-v1"},
    }


class BatchScoringTests(unittest.TestCase):
    def test_non_target_direction_indicator_rejects_scoring_context(self) -> None:
        record = _record()
        record["scoring_context"] = {"status": "available"}
        with self.assertRaisesRegex(
            ValueError, "scoring_context is only valid for FS02-M02"
        ):
            score_indicator_records(
                [record], calibrations={}, model_versions={"pose": "synthetic"}
            )

    def test_pending_asset_stays_calibration_required(self) -> None:
        outputs, report = score_indicator_records(
            [_record()],
            calibrations={"FS01-M02": _calibration("pending")},
            model_versions={"pose": "synthetic-pose"},
        )
        self.assertEqual(outputs[0]["status"], "calibration_required")
        self.assertIsNone(outputs[0]["grade"])
        self.assertEqual(report["status_counts"], {"calibration_required": 1})

    def test_self_declared_passed_asset_cannot_score_without_trusted_loader(self) -> None:
        outputs, report = score_indicator_records(
            [_record()],
            calibrations={"FS01-M02": _calibration("passed")},
            model_versions={"pose": "synthetic-pose"},
        )
        self.assertEqual(outputs[0]["status"], "calibration_required")
        self.assertIsNone(outputs[0]["grade"])
        self.assertIn(
            "trusted_promotion_ledger_verification_required",
            outputs[0]["reason_codes"],
        )
        self.assertEqual(outputs[0]["evidence"][0]["video_sha256"], "b" * 64)
        self.assertEqual(
            report["safety"]["scored_without_independent_test_or_test_override"],
            0,
        )

    def test_invalid_feature_is_unavailable_even_with_approved_asset(self) -> None:
        outputs, _ = score_indicator_records(
            [_record(valid=False)],
            calibrations={"FS01-M02": _calibration("passed")},
            model_versions={"pose": "synthetic-pose"},
        )
        self.assertEqual(outputs[0]["status"], "unavailable")
        self.assertIsNone(outputs[0]["grade"])

    def test_context_scoring_feature_cannot_be_dropped_during_rescoring(self) -> None:
        record = _record()
        record["scoring_features"] = [
            *record["features"],
            {
                "feature_name": "target_direction_alignment_error_deg",
                "feature_version": "target-direction-alignment-v1.0.0",
                "value": None,
                "unit": "deg",
                "confidence": 0.0,
                "valid": False,
                "reason": "manual_target_direction_required",
                "source_frames": [10],
            },
        ]
        record["scoring_feature_status"] = "unavailable"
        outputs, _ = score_indicator_records(
            [record], calibrations={}, model_versions={"pose": "synthetic-pose"}
        )
        self.assertEqual(outputs[0]["status"], "unavailable")
        self.assertIn("required_feature_unavailable", outputs[0]["reason_codes"])
        self.assertEqual(
            [item["feature_name"] for item in outputs[0]["feature"]["items"]],
            ["synthetic_feature", "target_direction_alignment_error_deg"],
        )

    def test_quality_block_is_preserved_during_batch_rescoring(self) -> None:
        for flag in (
            "primary_track_coverage_low",
            "source_track_switch_candidates_present",
            "required_phase_missing:preload_ms",
        ):
            with self.subTest(flag=flag):
                outputs, _ = score_indicator_records(
                    [_record(quality_flags=[flag])],
                    calibrations={"FS01-M02": _calibration("passed")},
                    model_versions={"pose": "synthetic-pose"},
                )
                self.assertEqual(outputs[0]["status"], "unavailable")
                self.assertIsNone(outputs[0]["grade"])

    def test_stale_quality_policy_cannot_be_rescored_as_current(self) -> None:
        stale = _record()
        stale["quality_gate"]["policy_version"] = (
            "indicator-event-quality-v1.1.0"
        )
        with self.assertRaisesRegex(ValueError, "versioned quality policy"):
            score_indicator_records(
                [stale], calibrations={}, model_versions={"pose": "synthetic"}
            )

    def test_tampered_or_missing_quality_gate_is_rejected(self) -> None:
        missing = _record()
        missing.pop("quality_gate")
        with self.assertRaisesRegex(ValueError, "quality_gate is required"):
            score_indicator_records(
                [missing], calibrations={}, model_versions={"pose": "synthetic"}
            )
        tampered = _record()
        tampered["quality_gate"]["scoring_allowed"] = False
        with self.assertRaisesRegex(ValueError, "does not match"):
            score_indicator_records(
                [tampered], calibrations={}, model_versions={"pose": "synthetic"}
            )

    def test_runtime_bound_production_recomputes_gate_from_event(self) -> None:
        fixture = _runtime_fixture()
        record = _runtime_bound_record(fixture, [])
        event = _authoritative_event(
            fixture, ["source_track_switch_candidates_present"]
        )

        with self.assertRaisesRegex(ValueError, "authoritative event"):
            score_indicator_records(
                [record],
                calibrations={"FS01-M02": _bind(fixture)},
                model_versions=fixture["model_versions"],
                authoritative_events=[event],
                authoritative_indicators=fixture["registry"]["indicators"],
            )

    def test_runtime_bound_production_requires_authoritative_sources(self) -> None:
        fixture = _runtime_fixture()
        record = _runtime_bound_record(fixture, [])

        with self.assertRaisesRegex(ValueError, "requires authoritative events"):
            score_indicator_records(
                [record],
                calibrations={"FS01-M02": _bind(fixture)},
                model_versions=fixture["model_versions"],
            )

    def test_runtime_bound_production_exactly_matches_event_identity(self) -> None:
        for field, value in (
            ("video_id", "other-video"),
            ("event_code", "FS09"),
            ("person_track_id", 2),
        ):
            with self.subTest(field=field):
                fixture = _runtime_fixture()
                record = _runtime_bound_record(fixture, [])
                event = _authoritative_event(fixture, [])
                event[field] = value
                with self.assertRaisesRegex(ValueError, field):
                    score_indicator_records(
                        [record],
                        calibrations={"FS01-M02": _bind(fixture)},
                        model_versions=fixture["model_versions"],
                        authoritative_events=[event],
                        authoritative_indicators=fixture["registry"]["indicators"],
                    )


if __name__ == "__main__":
    unittest.main()
