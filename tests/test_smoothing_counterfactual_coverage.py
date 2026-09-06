from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from rallymate_evaluation.smoothing_coverage import (
    build_smoothing_counterfactual_coverage,
    validate_smoothing_counterfactual_coverage,
    validate_smoothing_counterfactual_coverage_sources,
)


ROOT = Path(__file__).resolve().parents[1]


def _series(values: list[float]) -> list[dict[str, float | int]]:
    return [
        {"timestamp_ms": index * 40, "source_frame": index, "value": value}
        for index, value in enumerate(values)
    ]


def _row(
    *,
    feature_name: str,
    aggregation: str,
    value: float,
    raw: object,
    smoothed: object,
    valid: bool = True,
    unit: str = "unit",
) -> dict:
    return {
        "schema_version": "1.0.0",
        "video_id": "video-1",
        "event_id": "event-1",
        "event_code": "FS09",
        "person_track_id": 1,
        "feature_name": feature_name,
        "feature_version": "test-feature-v1",
        "value": value,
        "unit": unit,
        "confidence": 1.0,
        "valid": valid,
        "reason": "valid" if valid else "insufficient_samples",
        "source_frames": [0, 3],
        "raw_value": raw,
        "smoothed_value": smoothed,
        "provenance": {
            "aggregation": aggregation,
            "model_versions": {"feasibility_registry": "test-registry-v1"},
        },
    }


class SmoothingCounterfactualCoverageTests(unittest.TestCase):
    def test_m57_evidence_migration_preserves_production_feature_outputs(self) -> None:
        changed_versions: list[tuple[str, str]] = []
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        ):
            by_milestone: dict[str, dict[tuple[str, str], dict]] = {}
            for milestone in ("m53", "m57"):
                path = (
                    ROOT
                    / "reports"
                    / f"scoring-candidate-multivideo-{milestone}"
                    / "runs"
                    / video_id
                    / "features.jsonl"
                )
                rows = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                by_milestone[milestone] = {
                    (row["event_id"], row["feature_name"]): row for row in rows
                }
                self.assertEqual(len(by_milestone[milestone]), len(rows))

            self.assertEqual(
                set(by_milestone["m53"]), set(by_milestone["m57"])
            )
            for key, before in by_milestone["m53"].items():
                after = by_milestone["m57"][key]
                for field in (
                    "event_code",
                    "person_track_id",
                    "value",
                    "unit",
                    "valid",
                    "reason",
                    "source_frames",
                ):
                    self.assertEqual(before[field], after[field], (key, field))
                if before["feature_version"] != after["feature_version"]:
                    changed_versions.append(key)
                    self.assertEqual(key[1], "stability_duration_ms")
                    self.assertEqual(
                        after["feature_version"],
                        "0.2.0-provisional-envelope-evidence",
                    )

        self.assertEqual(len(changed_versions), 182)

    def test_m58_acceleration_evidence_preserves_production_feature_outputs(self) -> None:
        raw_payload_changes: list[tuple[str, str]] = []
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        ):
            by_milestone: dict[str, dict[tuple[str, str], dict]] = {}
            for milestone in ("m57", "m58"):
                path = (
                    ROOT
                    / "reports"
                    / f"scoring-candidate-multivideo-{milestone}"
                    / "runs"
                    / video_id
                    / "features.jsonl"
                )
                rows = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                by_milestone[milestone] = {
                    (row["event_id"], row["feature_name"]): row for row in rows
                }

            self.assertEqual(
                set(by_milestone["m57"]), set(by_milestone["m58"])
            )
            for key, before in by_milestone["m57"].items():
                after = by_milestone["m58"][key]
                for field in (
                    "event_code",
                    "person_track_id",
                    "value",
                    "unit",
                    "valid",
                    "reason",
                    "source_frames",
                    "feature_version",
                ):
                    self.assertEqual(before[field], after[field], (key, field))
                if before["raw_value"] != after["raw_value"]:
                    raw_payload_changes.append(key)
                    self.assertEqual(
                        key[1],
                        "hip_acceleration_along_launch_direction_body_s2",
                    )

        self.assertEqual(len(raw_payload_changes), 182)

    def test_m59_fs09_timing_evidence_preserves_production_feature_outputs(self) -> None:
        smoothed_payload_changes: list[tuple[str, str]] = []
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        ):
            by_milestone: dict[str, dict[tuple[str, str], dict]] = {}
            for milestone in ("m58", "m59"):
                path = (
                    ROOT
                    / "reports"
                    / f"scoring-candidate-multivideo-{milestone}"
                    / "runs"
                    / video_id
                    / "features.jsonl"
                )
                rows = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                by_milestone[milestone] = {
                    (row["event_id"], row["feature_name"]): row for row in rows
                }

            self.assertEqual(
                set(by_milestone["m58"]), set(by_milestone["m59"])
            )
            for key, before in by_milestone["m58"].items():
                after = by_milestone["m59"][key]
                for field in (
                    "event_code",
                    "person_track_id",
                    "value",
                    "unit",
                    "valid",
                    "reason",
                    "source_frames",
                    "feature_version",
                    "raw_value",
                ):
                    self.assertEqual(before[field], after[field], (key, field))
                if before["smoothed_value"] != after["smoothed_value"]:
                    smoothed_payload_changes.append(key)
                    self.assertIn(
                        key[1],
                        {
                            "braking_ankle_slowdown_to_hip_deceleration_ms",
                            "hip_deceleration_to_double_support_proxy_ms",
                        },
                    )

        self.assertEqual(len(smoothed_payload_changes), 364)

    def test_m60_fs01_m03_evidence_preserves_production_feature_outputs(self) -> None:
        raw_payload_changes: list[tuple[str, str]] = []
        smoothed_payload_changes: list[tuple[str, str]] = []
        target_names = {
            "bilateral_foot_rise_min_body",
            "bilateral_foot_rise_synchrony_ms",
            "bilateral_foot_rise_proxy_duration_ms",
            "hip_center_vertical_velocity_body_s",
        }
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        ):
            by_milestone: dict[str, dict[tuple[str, str], dict]] = {}
            for milestone in ("m59", "m60"):
                path = (
                    ROOT
                    / "reports"
                    / f"scoring-candidate-multivideo-{milestone}"
                    / "runs"
                    / video_id
                    / "features.jsonl"
                )
                rows = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                by_milestone[milestone] = {
                    (row["event_id"], row["feature_name"]): row for row in rows
                }

            self.assertEqual(set(by_milestone["m59"]), set(by_milestone["m60"]))
            for key, before in by_milestone["m59"].items():
                after = by_milestone["m60"][key]
                for field in (
                    "event_code",
                    "person_track_id",
                    "value",
                    "unit",
                    "valid",
                    "reason",
                    "source_frames",
                    "feature_version",
                ):
                    self.assertEqual(before[field], after[field], (key, field))
                if before["raw_value"] != after["raw_value"]:
                    raw_payload_changes.append(key)
                    self.assertEqual(
                        key[1], "bilateral_foot_rise_proxy_duration_ms"
                    )
                if before["smoothed_value"] != after["smoothed_value"]:
                    smoothed_payload_changes.append(key)
                    self.assertIn(key[1], target_names)
                if key[1] in target_names:
                    self.assertEqual(
                        after["provenance"]["evidence_contract"],
                        "fs01_m03_raw_and_prepared_kinematic_series_v1",
                    )

        self.assertEqual(len(raw_payload_changes), 182)
        self.assertEqual(len(smoothed_payload_changes), 728)

    def test_m61_fs01_m04_evidence_preserves_production_feature_outputs(self) -> None:
        raw_payload_changes: list[tuple[str, str]] = []
        smoothed_payload_changes: list[tuple[str, str]] = []
        target_names = {
            "bilateral_foot_vertical_slowdown_time_offset_ms",
            "post_slowdown_stance_width_body",
            "hip_center_lateral_variability_body",
        }
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        ):
            by_milestone: dict[str, dict[tuple[str, str], dict]] = {}
            for milestone in ("m60", "m61"):
                path = (
                    ROOT
                    / "reports"
                    / f"scoring-candidate-multivideo-{milestone}"
                    / "runs"
                    / video_id
                    / "features.jsonl"
                )
                rows = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                by_milestone[milestone] = {
                    (row["event_id"], row["feature_name"]): row for row in rows
                }

            self.assertEqual(set(by_milestone["m60"]), set(by_milestone["m61"]))
            for key, before in by_milestone["m60"].items():
                after = by_milestone["m61"][key]
                for field in (
                    "event_code",
                    "person_track_id",
                    "value",
                    "unit",
                    "valid",
                    "reason",
                    "source_frames",
                    "feature_version",
                ):
                    self.assertEqual(before[field], after[field], (key, field))
                if before["raw_value"] != after["raw_value"]:
                    raw_payload_changes.append(key)
                    self.assertIn(
                        key[1],
                        {
                            "post_slowdown_stance_width_body",
                            "hip_center_lateral_variability_body",
                        },
                    )
                if before["smoothed_value"] != after["smoothed_value"]:
                    smoothed_payload_changes.append(key)
                    self.assertIn(key[1], target_names)
                if key[1] in target_names:
                    self.assertEqual(
                        after["provenance"]["evidence_contract"],
                        "fs01_m04_raw_and_prepared_slowdown_series_v1",
                    )

        self.assertEqual(len(raw_payload_changes), 364)
        self.assertEqual(len(smoothed_payload_changes), 546)

    def test_m62_fs02_m04_evidence_preserves_production_feature_outputs(self) -> None:
        raw_payload_changes: list[tuple[str, str]] = []
        smoothed_payload_changes: list[tuple[str, str]] = []
        target_names = {
            "launch_side_code",
            "launch_foot_speed_peak_body_s",
            "launch_foot_relative_displacement_body",
            "launch_foot_motion_duration_ms",
        }
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        ):
            before_path = (
                ROOT
                / "reports"
                / "scoring-candidate-multivideo-m61"
                / "runs"
                / video_id
                / "features.jsonl"
            )
            after_path = (
                ROOT
                / "reports"
                / "scoring-candidate-multivideo-m62-v2"
                / "runs"
                / video_id
                / "features.jsonl"
            )
            before_rows = [
                json.loads(line)
                for line in before_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            after_rows = [
                json.loads(line)
                for line in after_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            before = {
                (row["event_id"], row["feature_name"]): row for row in before_rows
            }
            after = {
                (row["event_id"], row["feature_name"]): row for row in after_rows
            }
            self.assertEqual(set(before), set(after))
            for key, old in before.items():
                new = after[key]
                for field in (
                    "event_code",
                    "person_track_id",
                    "value",
                    "unit",
                    "valid",
                    "reason",
                    "source_frames",
                    "feature_version",
                ):
                    self.assertEqual(old[field], new[field], (key, field))
                if old["raw_value"] != new["raw_value"]:
                    raw_payload_changes.append(key)
                    self.assertIn(key[1], target_names)
                if old["smoothed_value"] != new["smoothed_value"]:
                    smoothed_payload_changes.append(key)
                    self.assertIn(key[1], target_names)
                if key[1] in target_names:
                    self.assertEqual(
                        new["provenance"]["evidence_contract"],
                        "fs02_m04_raw_and_prepared_launch_kinematics_v1",
                    )

        self.assertEqual(len(raw_payload_changes), 728)
        self.assertEqual(len(smoothed_payload_changes), 728)

    def test_m63_fs02_m05_evidence_preserves_production_feature_outputs(self) -> None:
        raw_payload_changes: list[tuple[str, str]] = []
        smoothed_payload_changes: list[tuple[str, str]] = []
        target_names = {
            "launch_foot_speed_drop_body_s",
            "first_step_displacement_body",
            "post_step_hip_direction_consistency",
            "launch_foot_slowdown_to_post_hip_direction_ms",
            "post_step_stance_width_body",
        }
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        ):
            before_path = (
                ROOT
                / "reports"
                / "scoring-candidate-multivideo-m62-v2"
                / "runs"
                / video_id
                / "features.jsonl"
            )
            after_path = (
                ROOT
                / "reports"
                / "scoring-candidate-multivideo-m63"
                / "runs"
                / video_id
                / "features.jsonl"
            )
            before_rows = [
                json.loads(line)
                for line in before_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            after_rows = [
                json.loads(line)
                for line in after_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            before = {
                (row["event_id"], row["feature_name"]): row
                for row in before_rows
            }
            after = {
                (row["event_id"], row["feature_name"]): row
                for row in after_rows
            }
            self.assertEqual(set(before), set(after))
            for key, old in before.items():
                new = after[key]
                for field in (
                    "event_code",
                    "person_track_id",
                    "value",
                    "unit",
                    "valid",
                    "reason",
                    "source_frames",
                    "feature_version",
                ):
                    self.assertEqual(old[field], new[field], (key, field))
                if old["raw_value"] != new["raw_value"]:
                    raw_payload_changes.append(key)
                    self.assertIn(key[1], target_names)
                if old["smoothed_value"] != new["smoothed_value"]:
                    smoothed_payload_changes.append(key)
                    self.assertIn(key[1], target_names)
                if key[1] in target_names:
                    self.assertEqual(
                        new["provenance"]["evidence_contract"],
                        "fs02_m05_raw_and_prepared_first_step_phase_kinematics_v1",
                    )

        self.assertEqual(len(raw_payload_changes), 910)
        self.assertEqual(len(smoothed_payload_changes), 910)

    def test_launch_direction_impact_uses_wrapped_circular_difference(self) -> None:
        registry = {
            "registry_version": "test-registry-v1",
            "indicators": [
                {
                    "indicator_id": "FS02-M02",
                    "required_events": ["FS02"],
                    "required_features": ["launch_direction_deg"],
                }
            ],
        }
        radians = math.radians(179.0)
        direction = [math.cos(radians), -math.sin(radians)]
        hip_position = [
            {
                "timestamp_ms": timestamp,
                "source_frame": index,
                "value": [direction[0] * fraction, direction[1] * fraction],
            }
            for index, (timestamp, fraction) in enumerate(
                ((0, 0.0), (50, 0.2), (150, 0.6), (300, 1.0))
            )
        ]
        row = _row(
            feature_name="launch_direction_deg",
            aggregation="event_hip_displacement_velocity_composite_direction",
            value=-179.0,
            unit="deg",
            raw={"hip_position": hip_position},
            smoothed={"series": {"hip_position": hip_position}},
        )
        row["event_code"] = "FS02"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "registry.json"
            features_path = root / "features.jsonl"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            features_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            report = build_smoothing_counterfactual_coverage(
                registry_path=registry_path,
                feature_paths=[features_path],
            )

        impact = report["feature_metrics"][0]["counterfactual_impact"]
        self.assertEqual(impact["comparison_kind"], "circular_difference_deg")
        self.assertAlmostEqual(impact["mean_absolute_difference"], 2.0, places=6)
        self.assertAlmostEqual(impact["signed_mean_difference"], 2.0, places=6)

    def test_real_m59_report_replays_all_bound_sources(self) -> None:
        report = json.loads(
            (ROOT / "reports" / "smoothing-counterfactual-coverage-m59.json")
            .read_text(encoding="utf-8")
        )
        # M59 is an immutable historical snapshot.  The current evaluator can
        # reconstruct additional FS01-M04 evidence, so only the current M61
        # report is expected to replay byte-for-byte with current semantics.
        validate_smoothing_counterfactual_coverage(report)
        self.assertEqual(report["registry"]["required_pose_feature_count"], 51)
        self.assertEqual(report["counts"]["computed_counterfactual_count"], 7127)
        self.assertEqual(
            report["counts"]["features_fully_reconstructable_count"], 35
        )
        by_name = {
            item["feature_name"]: item for item in report["feature_metrics"]
        }
        for feature_name in (
            "drive_side_code",
            "support_knee_extension_velocity_deg_s",
            "support_drive_to_moving_foot_rise_proxy_ms",
        ):
            self.assertEqual(
                by_name[feature_name]["computed_counterfactual_count"], 179
            )
        self.assertEqual(
            by_name["drive_side_code"]["counterfactual_impact"]["changed_count"],
            35,
        )
        self.assertAlmostEqual(
            by_name["support_knee_extension_velocity_deg_s"][
                "counterfactual_impact"
            ]["p95_absolute_difference"],
            321.77548943,
        )
        self.assertEqual(
            by_name["launch_direction_deg"]["computed_counterfactual_count"],
            182,
        )
        self.assertEqual(
            by_name["launch_direction_deg"]["counterfactual_impact"][
                "comparison_kind"
            ],
            "circular_difference_deg",
        )
        self.assertAlmostEqual(
            by_name["launch_direction_deg"]["counterfactual_impact"][
                "p95_absolute_difference"
            ],
            10.74166982,
        )
        self.assertEqual(
            by_name["stability_duration_ms"]["computed_counterfactual_count"],
            177,
        )
        self.assertEqual(
            by_name["stability_duration_ms"]["feature_versions"],
            ["0.2.0-provisional-envelope-evidence"],
        )
        self.assertAlmostEqual(
            by_name["stability_duration_ms"]["counterfactual_impact"][
                "p95_absolute_difference"
            ],
            124.2,
        )
        self.assertEqual(
            by_name["hip_acceleration_along_launch_direction_body_s2"][
                "computed_counterfactual_count"
            ],
            182,
        )
        self.assertAlmostEqual(
            by_name["hip_acceleration_along_launch_direction_body_s2"][
                "counterfactual_impact"
            ]["maximum_absolute_difference"],
            9311.43901824,
        )
        self.assertEqual(
            by_name["braking_ankle_slowdown_to_hip_deceleration_ms"][
                "computed_counterfactual_count"
            ],
            178,
        )
        self.assertEqual(
            by_name["hip_deceleration_to_double_support_proxy_ms"][
                "computed_counterfactual_count"
            ],
            176,
        )
        self.assertAlmostEqual(
            by_name["braking_ankle_slowdown_to_hip_deceleration_ms"][
                "counterfactual_impact"
            ]["p95_absolute_difference"],
            1137.45,
        )
        self.assertAlmostEqual(
            by_name["hip_deceleration_to_double_support_proxy_ms"][
                "counterfactual_impact"
            ]["p95_absolute_difference"],
            916.25,
        )

    def test_real_m60_report_completes_fs01_m03_family(self) -> None:
        report = json.loads(
            (ROOT / "reports" / "smoothing-counterfactual-coverage-m60.json")
            .read_text(encoding="utf-8")
        )
        # Preserve the published M60 counts as history without pretending its
        # pre-M61 evaluator semantics are the current source replay.
        validate_smoothing_counterfactual_coverage(report)
        self.assertEqual(report["counts"]["valid_numeric_record_count"], 9971)
        self.assertEqual(report["counts"]["computed_counterfactual_count"], 7827)
        self.assertEqual(report["counts"]["counterfactual_coverage"], 0.78497643)
        self.assertEqual(
            report["counts"]["features_fully_reconstructable_count"], 39
        )
        self.assertEqual(
            report["counts"]["features_not_reconstructable_count"], 12
        )
        by_name = {
            item["feature_name"]: item for item in report["feature_metrics"]
        }
        expected = {
            "bilateral_foot_rise_min_body": (176, 0.45801831),
            "bilateral_foot_rise_synchrony_ms": (176, 625.0),
            "bilateral_foot_rise_proxy_duration_ms": (173, 84.0),
            "hip_center_vertical_velocity_body_s": (175, 6.21942596),
        }
        for feature_name, (count, p95) in expected.items():
            metric = by_name[feature_name]
            self.assertEqual(metric["computed_counterfactual_count"], count)
            self.assertEqual(metric["unavailable_counterfactual_count"], 0)
            self.assertAlmostEqual(
                metric["counterfactual_impact"]["p95_absolute_difference"],
                p95,
            )

    def test_real_m61_report_covers_fs01_m04_family_without_relaxing_failures(self) -> None:
        report = json.loads(
            (ROOT / "reports" / "smoothing-counterfactual-coverage-m61.json")
            .read_text(encoding="utf-8")
        )
        # M61 is now historical: M62 can reconstruct four additional FS02-M04
        # aggregations from the same sources.  Preserve its published counts,
        # while reserving strict current-semantics source replay for M62.
        validate_smoothing_counterfactual_coverage(report)
        self.assertEqual(report["counts"]["valid_numeric_record_count"], 9971)
        self.assertEqual(report["counts"]["computed_counterfactual_count"], 8353)
        self.assertEqual(report["counts"]["counterfactual_coverage"], 0.83772942)
        self.assertEqual(
            report["counts"]["features_fully_reconstructable_count"], 41
        )
        self.assertEqual(
            report["counts"]["features_partially_reconstructable_count"], 1
        )
        self.assertEqual(
            report["counts"]["features_not_reconstructable_count"], 9
        )
        by_name = {
            item["feature_name"]: item for item in report["feature_metrics"]
        }
        expected = {
            "bilateral_foot_vertical_slowdown_time_offset_ms": (176, 0, 440.0),
            "post_slowdown_stance_width_body": (175, 1, 1.10408142),
            "hip_center_lateral_variability_body": (175, 0, 0.20364359),
        }
        for feature_name, (computed, unavailable, p95) in expected.items():
            metric = by_name[feature_name]
            self.assertEqual(metric["computed_counterfactual_count"], computed)
            self.assertEqual(
                metric["unavailable_counterfactual_count"], unavailable
            )
            self.assertAlmostEqual(
                metric["counterfactual_impact"]["p95_absolute_difference"],
                p95,
            )
        self.assertEqual(
            by_name["post_slowdown_stance_width_body"]["status"],
            "partially_reconstructable",
        )
        self.assertEqual(
            by_name["post_slowdown_stance_width_body"][
                "counterfactual_unavailable_reason_counts"
            ],
            {"required_fs01_m04_series_counterfactual_unavailable": 1},
        )

    def test_real_m62_report_covers_fs02_m04_without_inventing_raw_side(self) -> None:
        report = json.loads(
            (ROOT / "reports" / "smoothing-counterfactual-coverage-m62.json")
            .read_text(encoding="utf-8")
        )
        # M62 is an immutable historical snapshot.  The current evaluator can
        # reconstruct the FS02-M05 family from evidence already present in the
        # M62 sources, so source replay is intentionally reserved for M63.
        validate_smoothing_counterfactual_coverage(report)
        self.assertEqual(report["counts"]["valid_numeric_record_count"], 9971)
        self.assertEqual(report["counts"]["computed_counterfactual_count"], 9068)
        self.assertEqual(report["counts"]["counterfactual_coverage"], 0.90943737)
        self.assertEqual(
            report["counts"]["features_fully_reconstructable_count"], 42
        )
        self.assertEqual(
            report["counts"]["features_partially_reconstructable_count"], 4
        )
        self.assertEqual(
            report["counts"]["features_not_reconstructable_count"], 5
        )
        by_name = {
            item["feature_name"]: item for item in report["feature_metrics"]
        }
        expected = {
            "launch_side_code": (179, 0, "fully_reconstructable"),
            "launch_foot_speed_peak_body_s": (179, 1, "partially_reconstructable"),
            "launch_foot_relative_displacement_body": (
                178,
                1,
                "partially_reconstructable",
            ),
            "launch_foot_motion_duration_ms": (
                179,
                1,
                "partially_reconstructable",
            ),
        }
        for feature_name, (computed, unavailable, status) in expected.items():
            metric = by_name[feature_name]
            self.assertEqual(metric["computed_counterfactual_count"], computed)
            self.assertEqual(
                metric["unavailable_counterfactual_count"], unavailable
            )
            self.assertEqual(metric["status"], status)
            if unavailable:
                self.assertEqual(
                    metric["counterfactual_unavailable_reason_counts"],
                    {"required_fs02_m04_series_counterfactual_unavailable": 1},
                )
        self.assertEqual(
            by_name["launch_side_code"]["counterfactual_impact"][
                "comparison_kind"
            ],
            "categorical_code_agreement",
        )
        self.assertEqual(
            by_name["launch_side_code"]["counterfactual_impact"][
                "changed_count"
            ],
            7,
        )

    def test_real_m63_report_covers_all_pose_features_without_phase_drift(self) -> None:
        report = json.loads(
            (ROOT / "reports" / "smoothing-counterfactual-coverage-m63.json")
            .read_text(encoding="utf-8")
        )
        validate_smoothing_counterfactual_coverage_sources(report)
        self.assertEqual(report["counts"]["valid_numeric_record_count"], 9971)
        self.assertEqual(report["counts"]["computed_counterfactual_count"], 9965)
        self.assertEqual(report["counts"]["counterfactual_coverage"], 0.99939825)
        self.assertEqual(
            report["counts"]["features_fully_reconstructable_count"], 45
        )
        self.assertEqual(
            report["counts"]["features_partially_reconstructable_count"], 6
        )
        self.assertEqual(
            report["counts"]["features_not_reconstructable_count"], 0
        )
        by_name = {
            item["feature_name"]: item for item in report["feature_metrics"]
        }
        expected = {
            "launch_foot_speed_drop_body_s": (179, 1, 50.75936567),
            "first_step_displacement_body": (179, 1, 0.52313353),
            "post_step_hip_direction_consistency": (180, 0, 0.22365749),
            "launch_foot_slowdown_to_post_hip_direction_ms": (180, 0, 84.75),
            "post_step_stance_width_body": (179, 0, 0.41392841),
        }
        for feature_name, (computed, unavailable, p95) in expected.items():
            metric = by_name[feature_name]
            self.assertEqual(metric["computed_counterfactual_count"], computed)
            self.assertEqual(
                metric["unavailable_counterfactual_count"], unavailable
            )
            self.assertAlmostEqual(
                metric["counterfactual_impact"]["p95_absolute_difference"],
                p95,
            )
            if unavailable:
                self.assertEqual(
                    metric["counterfactual_unavailable_reason_counts"],
                    {
                        "required_fs02_m05_series_or_phase_counterfactual_unavailable": 1
                    },
                )
        self.assertEqual(
            {
                item["feature_name"]
                for item in report["feature_metrics"]
                if item["status"] == "partially_reconstructable"
            },
            {
                "first_step_displacement_body",
                "launch_foot_motion_duration_ms",
                "launch_foot_relative_displacement_body",
                "launch_foot_speed_drop_body_s",
                "launch_foot_speed_peak_body_s",
                "post_slowdown_stance_width_body",
            },
        )

    def test_builds_exact_safe_decomposition(self) -> None:
        registry = {
            "registry_version": "test-registry-v1",
            "indicators": [
                {
                    "indicator_id": "FS09-M02",
                    "required_events": ["FS09"],
                    "required_features": [
                        "hip_center_speed_drop_body_s",
                        "braking_side_code",
                    ],
                }
            ],
        }
        speed = _series([4.0, 3.0, 2.0, 1.0])
        rows = [
            _row(
                feature_name="hip_center_speed_drop_body_s",
                aggregation="event_early_median_minus_late_median",
                value=2.0,
                raw={"hip_speed": speed},
                smoothed={"series": {"hip_speed": speed}},
            ),
            _row(
                feature_name="braking_side_code",
                aggregation="positive_net_drop_then_positive_local_slowdown",
                value=-1,
                unit="code",
                raw={
                    "left_ankle_speed": _series([5.0, 4.0, 2.0, 1.0]),
                    "right_ankle_speed": _series([3.0, 3.0, 2.5, 2.0]),
                },
                smoothed={
                    "series": {
                        "left_ankle_speed": _series([5.0, 4.0, 2.0, 1.0]),
                        "right_ankle_speed": _series([3.0, 3.0, 2.5, 2.0]),
                    }
                },
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "registry.json"
            features_path = root / "features.jsonl"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            features_path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            report = build_smoothing_counterfactual_coverage(
                registry_path=registry_path,
                feature_paths=[features_path],
            )

        validate_smoothing_counterfactual_coverage(report)
        self.assertEqual(report["registry"]["required_pose_feature_count"], 2)
        self.assertEqual(report["counts"]["valid_numeric_record_count"], 2)
        self.assertEqual(report["counts"]["computed_counterfactual_count"], 2)
        self.assertEqual(report["counts"]["counterfactual_coverage"], 1.0)
        by_name = {
            item["feature_name"]: item for item in report["feature_metrics"]
        }
        self.assertEqual(
            by_name["braking_side_code"]["counterfactual_impact"]["agreement_rate"],
            1.0,
        )
        self.assertEqual(
            by_name["hip_center_speed_drop_body_s"]["counterfactual_impact"][
                "mean_absolute_difference"
            ],
            0.0,
        )
        self.assertFalse(report["semantics"]["accuracy_claim"])
        self.assertFalse(report["semantics"]["grade_generated"])

    def test_registry_lineage_mismatch_is_rejected(self) -> None:
        registry = {
            "registry_version": "wrong-registry",
            "indicators": [
                {
                    "indicator_id": "FS09-M03",
                    "required_events": ["FS09"],
                    "required_features": ["hip_center_speed_drop_body_s"],
                }
            ],
        }
        row = _row(
            feature_name="hip_center_speed_drop_body_s",
            aggregation="event_early_median_minus_late_median",
            value=2.0,
            raw={"hip_speed": _series([4.0, 3.0, 2.0, 1.0])},
            smoothed={
                "series": {"hip_speed": _series([4.0, 3.0, 2.0, 1.0])}
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "registry.json"
            features_path = root / "features.jsonl"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            features_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "registry version mismatch"):
                build_smoothing_counterfactual_coverage(
                    registry_path=registry_path,
                    feature_paths=[features_path],
                )

    def test_validator_rejects_accuracy_claim(self) -> None:
        unsafe = {
            "schema_version": "1.2.0",
            "report_version": "smoothing-counterfactual-coverage-v1.2.0",
            "status": "audited_counterfactual_coverage_no_ground_truth",
            "registry": {"required_pose_feature_count": 0},
            "feature_metrics": [],
            "counts": {},
            "semantics": {
                "method": "one_factor_counterfactual_differences_not_additive_shapley_decomposition",
                "ground_truth_provided": False,
                "accuracy_claim": False,
                "counterfactual_coverage_is_feature_accuracy": False,
                "quality_gate_modified": False,
                "grade_generated": False,
                "threshold_generated": False,
                "maturity_promoted": False,
            },
        }
        forged = copy.deepcopy(unsafe)
        forged["semantics"]["accuracy_claim"] = True
        with self.assertRaisesRegex(ValueError, "accuracy_claim"):
            validate_smoothing_counterfactual_coverage(forged)

    def test_validator_rejects_forged_feature_status(self) -> None:
        registry = {
            "registry_version": "test-registry-v1",
            "indicators": [
                {
                    "indicator_id": "FS09-M03",
                    "required_events": ["FS09"],
                    "required_features": ["hip_center_speed_drop_body_s"],
                }
            ],
        }
        row = _row(
            feature_name="hip_center_speed_drop_body_s",
            aggregation="event_early_median_minus_late_median",
            value=2.0,
            raw={"hip_speed": _series([4.0, 3.0, 2.0, 1.0])},
            smoothed={
                "series": {"hip_speed": _series([4.0, 3.0, 2.0, 1.0])}
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "registry.json"
            features_path = root / "features.jsonl"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            features_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            report = build_smoothing_counterfactual_coverage(
                registry_path=registry_path,
                feature_paths=[features_path],
            )
        report["feature_metrics"][0]["status"] = "not_reconstructable"
        with self.assertRaisesRegex(ValueError, "status is inconsistent"):
            validate_smoothing_counterfactual_coverage(report)


if __name__ == "__main__":
    unittest.main()
