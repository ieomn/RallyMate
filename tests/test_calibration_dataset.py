from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rallymate_scoring.calibration_dataset as calibration_dataset_module
import rallymate_scoring
from jsonschema import Draft202012Validator
from rallymate_scoring.calibration_dataset import (
    canonical_sha256,
    compile_calibration_dataset,
    recompute_independent_test_seal,
)
from rallymate_scoring.indicator_feature_qualification import (
    verify_indicator_feature_source_metadata,
)
from rallymate_scoring.quality_policy import evaluate_indicator_event_quality
from rallymate_scoring.run_bundle_binding import (
    build_scoring_run_bundle_entry,
    build_trusted_scoring_run_bundle_ledger,
)
from rallymate_scoring.calibration_fitting import (
    CalibrationFitError,
    fit_calibration_candidates,
    validate_prepared_dataset,
)
from rallymate_annotation.scoring_truth_intake import ingest_scoring_truth_exports
from rallymate_scoring.scoring_truth_calibration_authorization import (
    _issue_verified_scoring_truth_calibration_authorization,
    scoring_truth_calibration_authorization_binding_sha256,
)
from tests.test_scoring_truth_intake import _exports, _make_source
from tests.test_scoring_truth_authorization import _make_evidence, _verify
from tests.test_truth_pack import (
    COACH_FIELDS,
    EVENT_FIELDS,
    REVIEW_FIELDS,
    _csv,
    _events,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


class CalibrationDatasetTests(unittest.TestCase):
    def test_indicator_feature_qualification_api_is_public(self) -> None:
        self.assertIs(
            rallymate_scoring.verify_indicator_feature_source_metadata,
            verify_indicator_feature_source_metadata,
        )
        self.assertIn(
            "VerifiedIndicatorFeatureSourceMetadata", rallymate_scoring.__all__
        )
        self.assertEqual(
            rallymate_scoring.INDICATOR_FEATURE_SOURCE_METADATA_VERSION,
            "indicator-feature-source-metadata-v1.0.0",
        )

    def _fixture(
        self,
        directory: Path,
        *,
        conflicting_grade: bool = False,
        feature_event_suffix: str = "",
        same_player: bool = False,
        ranking_only: bool = False,
    ) -> dict[str, Path]:
        registry = directory / "registry.json"
        events = directory / "events.jsonl"
        semantics = directory / "semantics.jsonl"
        labels = directory / "labels.jsonl"
        features = directory / "features.jsonl"
        manifest = directory / "truth-manifest.json"
        validation = directory / "truth-validation.json"
        metadata = directory / "groups.json"
        policy = directory / "split-policy.json"
        output = directory / "output"
        videos = ["video-a", "video-b", "video-c"]
        _write_json(
            registry,
            {
                "schema_version": "2.0.0",
                "registry_version": "synthetic-registry-v1",
                "indicators": [
                    {
                        "indicator_id": "FS01-M02",
                        "feasibility_level": "F2",
                        "required_events": ["FS01.preload"],
                        "required_features": ["feature_one", "feature_two"],
                    }
                ],
            },
        )
        event_records = []
        feature_records = []
        label_records = []
        for index, video_id in enumerate(videos):
            event_id = f"manual-event-{index}"
            event_records.append(
                {
                    "schema_version": "1.0.0",
                    "video_id": video_id,
                    "event_id": event_id,
                    "event_code": "FS01",
                    "person_track_id": 1,
                    "start_ms": index * 1000,
                    "end_ms": index * 1000 + 500,
                    "key_phases_ms": {"preload_ms": index * 1000 + 100},
                    "confidence": 1.0,
                    "boundary_uncertainty_ms": 20,
                    "quality_flags": [],
                    "view_group": f"view-{index % 2}",
                    "annotation_source": "manual",
                    "annotator_id": "event-annotator",
                    "reviewer_id": "event-reviewer",
                    "adjudication_status": "accepted",
                    "provenance": {"fixture": True},
                }
            )
            feature_records.append(
                {
                    "schema_version": "1.0.0",
                    "video_id": video_id,
                    "event_id": event_id + feature_event_suffix,
                    "event_code": "FS01",
                    "indicator_id": "FS01-M02",
                    "person_track_id": 1,
                    "feasibility_level": "F2",
                    "feature_status": "measured",
                    "features": [
                        {
                            "feature_name": "feature_one",
                            "feature_version": "feature-v1",
                            "value": float(index + 1),
                            "unit": "body",
                            "confidence": 0.9,
                            "valid": True,
                            "reason": "valid",
                            "source_frames": [index],
                        },
                        {
                            "feature_name": "feature_two",
                            "feature_version": "feature-v1",
                            "value": float(index + 2),
                            "unit": "deg",
                            "confidence": 0.8,
                            "valid": True,
                            "reason": "valid",
                            "source_frames": [index + 1],
                        },
                    ],
                    "scoring_status": "calibration_required",
                    "grade": None,
                    "reason_codes": ["coach_calibration_missing"],
                    "provenance": {"fixture": True},
                }
            )
            grades = ["E", "C", "A"]
            for coach_index, coach_id in enumerate(("coach-1", "coach-2")):
                grade = grades[index]
                if conflicting_grade and index == 0 and coach_index == 1:
                    grade = "A"
                label = {
                    "schema_version": "1.0.0",
                    "annotation_id": f"label-{video_id}-{coach_id}",
                    "video_id": video_id,
                    "event_id": event_id,
                    "indicator_id": "FS01-M02",
                    "annotator_id": coach_id,
                }
                if ranking_only:
                    label.update(
                        {
                            "label_type": "ranking",
                            "rank_group_id": f"rank-{video_id}",
                            "rank": 1,
                        }
                    )
                else:
                    label.update({"label_type": "grade", "grade": grade})
                label_records.append(label)
        _write_jsonl(events, event_records)
        _write_jsonl(features, feature_records)
        _write_jsonl(labels, label_records)
        _write_jsonl(semantics, [])
        _write_json(
            manifest,
            {
                "pack_version": "synthetic-truth-v1",
                "videos": [{"video_id": video_id} for video_id in videos],
            },
        )
        _write_json(
            validation,
            {
                "readiness": {
                    "semantic_coverage": [
                        {
                            "indicator_id": "FS01-M02",
                            "required_semantic_keys": [],
                        }
                    ]
                }
            },
        )
        _write_json(
            metadata,
            {
                "schema_version": "1.0.0",
                "records": [
                    {
                        "video_id": video_id,
                        "person_track_id": 1,
                        "player_id": "same-player" if same_player and index < 2 else f"player-{index}",
                        "session_id": f"session-{index}",
                        "view_group": f"view-{index % 2}",
                    }
                    for index, video_id in enumerate(videos)
                ]
            },
        )
        _write_json(
            policy,
            {
                "schema_version": "1.0.0",
                "policy_id": "synthetic-group-holdout",
                "policy_version": "1.0.0",
                "assignments": [
                    {"split": "train", "video_ids": ["video-a"]},
                    {"split": "validation", "video_ids": ["video-b"]},
                    {"split": "independent_test", "video_ids": ["video-c"]},
                ],
            },
        )
        return {
            "registry": registry,
            "events": events,
            "semantics": semantics,
            "labels": labels,
            "features": features,
            "manifest": manifest,
            "validation": validation,
            "metadata": metadata,
            "policy": policy,
            "output": output,
        }

    def _compile(self, paths: dict[str, Path]) -> dict:
        return compile_calibration_dataset(
            feasibility_registry_path=paths["registry"],
            indicator_feature_paths=[paths["features"]],
            manual_events_path=paths["events"],
            manual_semantics_path=paths["semantics"],
            coach_labels_path=paths["labels"],
            truth_manifest_path=paths["manifest"],
            truth_validation_report_path=paths["validation"],
            group_metadata_path=paths["metadata"],
            split_policy_path=paths["policy"],
            output_dir=paths["output"],
            generated_at="2026-08-13T00:00:00+00:00",
            synthetic_test_only=True,
        )

    def test_exact_join_unanimous_labels_and_sealed_test(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            manifest = self._compile(paths)
            self.assertEqual(manifest["registry"]["indicator_count"], 1)
            samples = [
                json.loads(line)
                for line in (paths["output"] / "samples.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(samples), 3)
            self.assertTrue(all(sample["schema_version"] == "1.1.0" for sample in samples))
            self.assertTrue(
                all(
                    sample["qualification_snapshot"]["source_status"]
                    == "synthetic_test_only_source"
                    for sample in samples
                )
            )
            self.assertTrue(
                all(
                    sample["qualification_snapshot"]["quality_gate"] is None
                    for sample in samples
                )
            )
            sample_schema = json.loads(
                (
                    Path(__file__).resolve().parents[1]
                    / "contracts/calibration-dataset-sample.schema.json"
                ).read_text(encoding="utf-8")
            )
            for sample in samples:
                Draft202012Validator(sample_schema).validate(sample)
            self.assertTrue(all(sample["feature_vector_complete"] for sample in samples))
            self.assertTrue(
                all(sample["label_summary"]["grade_status"] == "unanimous" for sample in samples)
            )
            self.assertIn("not proof", samples[0]["label_summary"]["resolution_semantics"])
            prepared = json.loads(
                (paths["output"] / "prepared" / "by-indicator" / "FS01-M02.json").read_text()
            )
            self.assertEqual({item["split"] for item in prepared["records"]}, {"train", "validation"})
            self.assertEqual(len(prepared["records"]), 2)
            self.assertFalse(any(item["split"] == "independent_test" for item in prepared["records"]))
            expected = recompute_independent_test_seal(samples, "FS01-M02")
            self.assertEqual(prepared["independent_test_seal"], expected)
            self.assertEqual(expected["record_count"], 1)
            self.assertTrue(expected["labels_withheld"])
            self.assertEqual(len(expected["sample_ids"]), 1)
            tampered = json.loads(json.dumps(samples))
            test_sample = next(item for item in tampered if item["split"] == "independent_test")
            test_sample["labels"]["grades"][0]["grade"] = "B"
            self.assertNotEqual(
                recompute_independent_test_seal(tampered, "FS01-M02")["content_sha256"],
                expected["content_sha256"],
            )
            self.assertFalse(manifest["safety"]["generated_thresholds"])
            self.assertFalse(manifest["safety"]["automatic_F3_or_F4_promotion"])

    def test_conflicting_coaches_are_not_silently_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary), conflicting_grade=True)
            self._compile(paths)
            samples = [
                json.loads(line)
                for line in (paths["output"] / "samples.jsonl").read_text().splitlines()
            ]
            conflicted = next(item for item in samples if item["video_id"] == "video-a")
            self.assertEqual(conflicted["label_summary"]["grade_status"], "conflict")
            self.assertIsNone(conflicted["label_summary"]["resolved_grade"])
            self.assertFalse(conflicted["readiness"]["eligible_for_grade_modeling"])
            prepared = json.loads(
                (paths["output"] / "prepared" / "by-indicator" / "FS01-M02.json").read_text()
            )
            self.assertNotIn(conflicted["sample_id"], {item["record_id"] for item in prepared["records"]})
            self.assertIn(
                "multi_coach_grade_conflict_requires_explicit_adjudication",
                prepared["readiness"]["blockers"],
            )

    def test_ranking_only_never_claims_ordinary_A_E_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary), ranking_only=True)
            manifest = self._compile(paths)
            self.assertEqual(manifest["status"], "insufficient")
            readiness = json.loads(
                (paths["output"] / "readiness-report.json").read_text()
            )
            indicator = readiness["indicators"][0]
            self.assertEqual(indicator["status"], "insufficient")
            self.assertEqual(indicator["counts"]["grade_labels"], 0)
            self.assertEqual(indicator["counts"]["ranking_labels"], 6)
            self.assertIn(
                "absolute_grade_anchor_missing_ranking_only",
                indicator["blockers"],
            )
            prepared = json.loads(
                (
                    paths["output"]
                    / "prepared"
                    / "by-indicator"
                    / "FS01-M02.json"
                ).read_text()
            )
            self.assertEqual(prepared["records"], [])
            self.assertIn(
                "absolute_grade_anchor_missing_ranking_only",
                prepared["readiness"]["blockers"],
            )
            with self.assertRaisesRegex(
                CalibrationFitError, "upstream readiness blockers"
            ):
                validate_prepared_dataset(prepared, require_fit_ready=True)

    def test_candidate_event_id_is_never_fuzzy_joined_to_manual_truth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary), feature_event_suffix="-candidate")
            self._compile(paths)
            samples = [
                json.loads(line)
                for line in (paths["output"] / "samples.jsonl").read_text().splitlines()
            ]
            self.assertTrue(all(not item["feature_vector_complete"] for item in samples))
            self.assertTrue(
                all("indicator_feature_join_missing" in item["readiness"]["reason_codes"] for item in samples)
            )
            self.assertTrue(
                all(item["lineage"]["join_policy"] == "exact_manual_event_id_only_v1" for item in samples)
            )

    def test_connected_player_component_cannot_cross_splits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary), same_player=True)
            self._compile(paths)
            split_manifest = json.loads((paths["output"] / "split-manifest.json").read_text())
            audit = split_manifest["leakage_audit"]
            self.assertTrue(audit["data_leakage_detected"])
            self.assertEqual(audit["component_cross_split_count"], 1)
            samples = [
                json.loads(line)
                for line in (paths["output"] / "samples.jsonl").read_text().splitlines()
            ]
            a = next(item for item in samples if item["video_id"] == "video-a")
            b = next(item for item in samples if item["video_id"] == "video-b")
            self.assertEqual(a["groups"]["leakage_group_id"], b["groups"]["leakage_group_id"])
            self.assertIsNone(a["split"])
            self.assertIsNone(b["split"])

    def test_feature_status_and_quality_hard_fail_block_modeling_even_with_numeric_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            records = [
                json.loads(line) for line in paths["features"].read_text().splitlines()
            ]
            records[0]["feature_status"] = "unavailable"
            records[0]["quality_gate"] = evaluate_indicator_event_quality(
                "FS01-M02", ["primary_track_coverage_low"]
            )
            _write_jsonl(paths["features"], records)
            self._compile(paths)
            samples = [
                json.loads(line)
                for line in (paths["output"] / "samples.jsonl").read_text().splitlines()
            ]
            blocked = next(item for item in samples if item["video_id"] == "video-a")
            self.assertFalse(blocked["feature_vector_complete"])
            self.assertFalse(blocked["readiness"]["eligible_for_grade_modeling"])
            self.assertIn(
                "indicator_feature_status_unavailable",
                blocked["readiness"]["reason_codes"],
            )
            self.assertIn("quality_gate_hard_fail", blocked["readiness"]["reason_codes"])

    def test_canonical_hash_is_key_order_independent_and_value_sensitive(self) -> None:
        self.assertEqual(canonical_sha256({"a": 1, "b": 2}), canonical_sha256({"b": 2, "a": 1}))
        self.assertNotEqual(canonical_sha256({"a": 1}), canonical_sha256({"a": 2}))

    def test_output_is_immutable_and_existing_target_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            manifest = self._compile(paths)
            self.assertTrue((paths["output"] / "manifest.json").exists())
            self.assertTrue(
                manifest["outputs"]["samples"].startswith(str(paths["output"].resolve()))
            )
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                self._compile(paths)
            self.assertFalse(
                list(Path(temporary).glob(".output.staging-*")),
                "staging directories must not remain after a refused compile",
            )

    def test_input_contract_failure_leaves_no_partial_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            policy = json.loads(paths["policy"].read_text())
            policy["assignments"][0]["video_ids"] = []
            _write_json(paths["policy"], policy)
            with self.assertRaisesRegex(ValueError, "non-empty unique string array"):
                self._compile(paths)
            self.assertFalse(paths["output"].exists())
            self.assertFalse(list(Path(temporary).glob(".output.staging-*")))

    def test_contracts_and_templates_match_versioned_python_inputs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        contract_names = (
            "calibration-dataset.schema.json",
            "calibration-dataset-sample.schema.json",
            "calibration-dataset-readiness.schema.json",
            "calibration-split-manifest.schema.json",
            "calibration-group-metadata.schema.json",
            "calibration-split-policy.schema.json",
        )
        for name in contract_names:
            payload = json.loads((root / "contracts" / name).read_text(encoding="utf-8"))
            self.assertEqual(payload["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertTrue(payload["$id"].endswith(name))
        group_template = json.loads(
            (root / "examples/calibration-group-metadata.template.json").read_text()
        )
        policy_template = json.loads(
            (root / "examples/calibration-split-policy.template.json").read_text()
        )
        self.assertEqual(group_template["schema_version"], "1.0.0")
        self.assertEqual(policy_template["schema_version"], "1.0.0")
        self.assertNotIn("minimum_sample_count", json.dumps(policy_template))
        self.assertNotIn("kappa_threshold", json.dumps(policy_template))

    def test_real_blank_pack_emits_dynamic_13_indicator_annotation_required(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            manifest = compile_calibration_dataset(
                feasibility_registry_path=root / "metric-feasibility-pose-wave-v2.json",
                indicator_feature_paths=[
                    root
                    / "reports"
                    / "fs09-pose-wave-v2"
                    / "850cb0006b406c7176eeda8d711cd065"
                    / "indicator-features.jsonl"
                ],
                manual_events_path=root / "data/annotations/scoring-truth-pack-v1/compiled/manual-events.jsonl",
                manual_semantics_path=root / "data/annotations/scoring-truth-pack-v1/compiled/manual-semantics.jsonl",
                coach_labels_path=root / "data/annotations/scoring-truth-pack-v1/compiled/coach-labels.jsonl",
                truth_manifest_path=root / "data/annotations/scoring-truth-pack-v1/manifest.json",
                truth_validation_report_path=root
                / "data/annotations/scoring-truth-pack-v1/compiled/validation-report.json",
                output_dir=Path(temporary) / "compiled",
                generated_at="2026-08-13T00:00:00+00:00",
            )
            self.assertEqual(manifest["status"], "annotation_required")
            self.assertEqual(
                manifest["artifact_scope"], "unverified_truth_diagnostic_input"
            )
            self.assertEqual(
                manifest["truth_authorization"]["status"],
                "unverified_private_truth_diagnostic",
            )
            self.assertEqual(manifest["registry"]["indicator_count"], 13)
            self.assertEqual(manifest["counts"]["samples"], 0)
            self.assertEqual(manifest["counts"]["prepared_indicator_files"], 13)
            readiness = json.loads((Path(temporary) / "compiled/readiness-report.json").read_text())
            self.assertEqual(len(readiness["indicators"]), 13)
            self.assertTrue(all(item["status"] == "insufficient" for item in readiness["indicators"]))
            self.assertFalse(readiness["safety"]["generated_thresholds"])
            self.assertFalse(readiness["safety"]["automatic_F3_or_F4_promotion"])

    def test_valid_private_m89_intake_cannot_mint_real_calibration_input(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = _make_source(work)
            video_ids = [f"video-{index:02d}" for index in range(15)]
            source_manifest = json.loads(
                (source / "manifest.json").read_text(encoding="utf-8")
            )
            source_manifest["videos"] = [
                {"video_id": video_id} for video_id in video_ids
            ]
            _write_json(source / "manifest.json", source_manifest)
            template_event = next(
                row for row in _events() if row["event_code"] == "FS01"
            )
            event_rows = []
            coach_rows = []
            grades = ("E", "D", "C", "B", "A")
            for index, video_id in enumerate(video_ids):
                event = dict(template_event)
                event["video_id"] = video_id
                event["event_id"] = f"fs01-{index:02d}"
                event_rows.append(event)
                for coach_id in ("coach-a", "coach-b"):
                    coach_rows.append(
                        {
                            "annotation_id": f"{event['event_id']}-{coach_id}",
                            "video_id": video_id,
                            "event_id": event["event_id"],
                            "candidate_event_id": "",
                            "blind_clip_id": "",
                            "indicator_id": "FS01-M02",
                            "annotator_id": coach_id,
                            "label_type": "grade",
                            "grade": grades[index % len(grades)],
                            "rank_group_id": "",
                            "rank": "",
                        }
                    )
            _csv(source / "event-annotations.csv", EVENT_FIELDS, event_rows)
            _csv(
                source / "coach-labels.csv",
                COACH_FIELDS,
                coach_rows,
            )
            _csv(
                source / "full-video-review-completion.csv",
                REVIEW_FIELDS,
                [
                    {
                        "video_id": video_id,
                        "annotator_id": reviewer,
                        "full_video_review_completed": "true",
                        "reviewed_at": "2026-08-30T04:00:00Z",
                        "notes": "",
                    }
                    for video_id in video_ids
                    for reviewer in ("event-a", "event-b")
                ],
            )
            session = work / "private-m89-intake"
            intake = ingest_scoring_truth_exports(source, _exports(source), session)
            self.assertEqual(
                intake["status"],
                "private_candidate_containing_intake_not_operator_authorized",
            )
            self.assertFalse(intake["safety"]["operator_authorized"])
            self.assertFalse(intake["safety"]["calibration_eligible"])
            intake_report = json.loads(
                (session / "compiled/validation-report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertGreater(intake_report["counts"]["manual_events"], 0)
            self.assertGreater(intake_report["counts"]["coach_labels"], 0)

            feature_path = work / "indicator-features.jsonl"
            feature_contract = (
                ("hip_center_y_body", "body"),
                ("left_knee_flexion_deg", "deg"),
                ("right_knee_flexion_deg", "deg"),
                ("stance_width_body", "body"),
                ("hip_center_relative_to_ankle_support", "ratio"),
            )
            _write_jsonl(
                feature_path,
                [
                    {
                        "schema_version": "1.0.0",
                        "video_id": event["video_id"],
                        "event_id": event["event_id"],
                        "event_code": "FS01",
                        "indicator_id": "FS01-M02",
                        "person_track_id": 1,
                        "feasibility_level": "F2",
                        "feature_status": "measured",
                        "features": [
                            {
                                "feature_name": name,
                                "feature_version": "1.0.0",
                                "value": float(index + feature_index + 1),
                                "unit": unit,
                                "confidence": 0.95,
                                "valid": True,
                                "reason": "valid",
                                "source_frames": [index],
                            }
                            for feature_index, (name, unit) in enumerate(
                                feature_contract
                            )
                        ],
                        "scoring_status": "calibration_required",
                        "grade": None,
                        "reason_codes": ["coach_calibration_missing"],
                        "provenance": {"fixture": "private-m89-adversarial"},
                    }
                    for index, event in enumerate(event_rows)
                ],
            )
            metadata_path = work / "groups.json"
            _write_json(
                metadata_path,
                {
                    "schema_version": "1.0.0",
                    "records": [
                        {
                            "video_id": video_id,
                            "person_track_id": 1,
                            "player_id": f"player-{video_id}",
                            "session_id": f"session-{video_id}",
                            "view_group": "fixed-rear",
                        }
                        for video_id in video_ids
                    ],
                },
            )
            policy_path = work / "split-policy.json"
            _write_json(
                policy_path,
                {
                    "schema_version": "1.0.0",
                    "policy_id": "private-m89-adversarial-split",
                    "policy_version": "1.0.0",
                    "assignments": [
                        {"split": "train", "video_ids": video_ids[:5]},
                        {"split": "validation", "video_ids": video_ids[5:10]},
                        {
                            "split": "independent_test",
                            "video_ids": video_ids[10:],
                        },
                    ],
                },
            )
            output = work / "diagnostic-calibration"
            manifest = compile_calibration_dataset(
                feasibility_registry_path=(
                    root / "metric-feasibility-pose-wave-v2.json"
                ),
                indicator_feature_paths=[feature_path],
                manual_events_path=session / "compiled/manual-events.jsonl",
                manual_semantics_path=session / "compiled/manual-semantics.jsonl",
                coach_labels_path=session / "compiled/coach-labels.jsonl",
                truth_intake_manifest_path=session / "intake-manifest.json",
                truth_manifest_path=session / "manifest.json",
                truth_validation_report_path=(
                    session / "compiled/validation-report.json"
                ),
                group_metadata_path=metadata_path,
                split_policy_path=policy_path,
                output_dir=output,
                generated_at="2026-08-30T05:00:00+00:00",
            )
            self.assertEqual(
                manifest["artifact_scope"], "unverified_truth_diagnostic_input"
            )
            self.assertEqual(
                manifest["truth_authorization"]["status"],
                "unverified_private_truth_diagnostic",
            )
            self.assertGreater(manifest["counts"]["samples"], 0)
            self.assertFalse(
                manifest["safety"]["verified_authorized_truth_intake"]
            )
            fs01_prepared = json.loads(
                Path(
                    manifest["outputs"]["prepared_by_indicator"]["FS01-M02"]
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                fs01_prepared["readiness"]["blockers"],
                ["verified_authorized_truth_intake_required"],
            )
            for prepared_path in manifest["outputs"]["prepared_by_indicator"].values():
                prepared = json.loads(Path(prepared_path).read_text(encoding="utf-8"))
                self.assertEqual(
                    prepared["artifact_scope"],
                    "unverified_truth_diagnostic_input",
                )
                self.assertEqual(
                    prepared["source"]["kind"], "unverified_private_truth"
                )
                with self.assertRaisesRegex(
                    CalibrationFitError, "verified authorized truth intake"
                ):
                    fit_calibration_candidates(prepared, {})

            copied_output = work / "copied-binding-must-not-pass"
            with self.assertRaisesRegex(
                ValueError, "same-process verified authorized-intake object"
            ):
                compile_calibration_dataset(
                    feasibility_registry_path=(
                        root / "metric-feasibility-pose-wave-v2.json"
                    ),
                    indicator_feature_paths=[feature_path],
                    manual_events_path=session / "compiled/manual-events.jsonl",
                    manual_semantics_path=session / "compiled/manual-semantics.jsonl",
                    coach_labels_path=session / "compiled/coach-labels.jsonl",
                    truth_intake_manifest_path=session / "intake-manifest.json",
                    truth_manifest_path=session / "manifest.json",
                    truth_validation_report_path=(
                        session / "compiled/validation-report.json"
                    ),
                    group_metadata_path=metadata_path,
                    split_policy_path=policy_path,
                    output_dir=copied_output,
                    verified_truth_authorization=manifest["truth_authorization"],
                )
            self.assertFalse(copied_output.exists())

    def test_caller_selected_manual_truth_is_diagnostic_without_live_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            manifest = compile_calibration_dataset(
                feasibility_registry_path=paths["registry"],
                indicator_feature_paths=[paths["features"]],
                manual_events_path=paths["events"],
                manual_semantics_path=paths["semantics"],
                coach_labels_path=paths["labels"],
                truth_manifest_path=paths["manifest"],
                truth_validation_report_path=paths["validation"],
                group_metadata_path=paths["metadata"],
                split_policy_path=paths["policy"],
                output_dir=paths["output"],
                generated_at="2026-08-30T05:00:00+00:00",
            )
            prepared = json.loads(
                (paths["output"] / "prepared/by-indicator/FS01-M02.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                manifest["artifact_scope"], "unverified_truth_diagnostic_input"
            )
            self.assertIn(
                "verified_authorized_truth_intake_required",
                prepared["readiness"]["blockers"],
            )
            with self.assertRaisesRegex(
                CalibrationFitError, "verified authorized truth intake"
            ):
                fit_calibration_candidates(prepared, {})

    def test_authorization_bound_snapshot_change_aborts_before_atomic_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            original_compile = (
                calibration_dataset_module._compile_calibration_dataset_in_place
            )

            def compile_then_change_source(**kwargs):
                manifest = original_compile(**kwargs)
                paths["labels"].write_bytes(paths["labels"].read_bytes() + b"\n")
                return manifest

            with patch.object(
                calibration_dataset_module,
                "_compile_calibration_dataset_in_place",
                side_effect=compile_then_change_source,
            ):
                with self.assertRaisesRegex(
                    ValueError, "changed before atomic commit"
                ):
                    self._compile(paths)
            self.assertFalse(paths["output"].exists())
            self.assertFalse(list(Path(temporary).glob(".output.staging-*")))

    def test_private_test_issuer_exercises_real_scope_content_binding(self) -> None:
        """Mechanics coverage only: no production issuer exists in application code."""

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            paths = self._fixture(work)
            intake_path = work / "test-only-authorized-intake.json"
            intake_payload = {
                "intake_id": "private-test-authorized-intake",
                "intake_version": "private-test-intake-v1",
                "content_root_sha256": "A" * 64,
            }
            _write_json(intake_path, intake_payload)
            auth_work = work / "event-authorization"
            auth_anchor = work / "event-anchor"
            auth_work.mkdir()
            auth_anchor.mkdir()
            event_binding = _verify(_make_evidence(auth_work, auth_anchor))

            def raw_sha256(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest().upper()

            binding = {
                "binding_version": (
                    "scoring-truth-calibration-authorization-binding-v1.0.0"
                ),
                "status": "verified_authorized_intake_for_calibration",
                "canonicalization": "rallymate-canonical-json-v1",
                "authorization_id": "private-test-issuer-compiler-coverage",
                "event_protocol_authorization": event_binding,
                "intake": intake_payload,
                "revision_lineage": {
                    "revision_id": "private-test-final-revision",
                    "annotator_a_id": "private-test-a",
                    "annotator_b_id": "private-test-b",
                    "reviewer_c_id": "private-test-c",
                    "annotator_a_export_root_sha256": "B" * 64,
                    "annotator_b_export_root_sha256": "C" * 64,
                    "reviewer_c_adjudication_root_sha256": "D" * 64,
                    "roles_distinct": True,
                    "revision_finalized": True,
                },
                "input_files": {
                    "intake_manifest": raw_sha256(intake_path),
                    "truth_manifest": raw_sha256(paths["manifest"]),
                    "truth_validation_report": raw_sha256(paths["validation"]),
                    "manual_events": raw_sha256(paths["events"]),
                    "manual_semantics": raw_sha256(paths["semantics"]),
                    "coach_labels": raw_sha256(paths["labels"]),
                },
            }
            binding["binding_sha256"] = (
                scoring_truth_calibration_authorization_binding_sha256(binding)
            )
            live_authorization = (
                _issue_verified_scoring_truth_calibration_authorization(binding)
            )
            with self.assertRaises(TypeError):
                json.dumps(live_authorization)

            feature_records = [
                json.loads(line)
                for line in paths["features"].read_text(encoding="utf-8").splitlines()
            ]
            feature_paths: list[Path] = []
            summary_paths: list[Path] = []
            summaries: list[dict] = []
            summary_hashes: list[str] = []
            for index, record in enumerate(feature_records):
                record["quality_gate"] = evaluate_indicator_event_quality(
                    record["indicator_id"], []
                )
                feature_path = work / f"indicator-features-{record['video_id']}.jsonl"
                _write_jsonl(feature_path, [record])
                feature_sha256 = hashlib.sha256(feature_path.read_bytes()).hexdigest()
                artifacts = {
                    "events_jsonl": str(work / f"events-{index}.jsonl"),
                    "features_jsonl": str(work / f"features-{index}.jsonl"),
                    "indicator_features_jsonl": str(feature_path.resolve()),
                    "scores_jsonl": str(work / f"scores-{index}.jsonl"),
                    "event_feature_errors_json": str(
                        work / f"event-feature-errors-{index}.json"
                    ),
                }
                artifact_sha256 = {
                    "events_jsonl": "1" * 64,
                    "features_jsonl": "2" * 64,
                    "indicator_features_jsonl": feature_sha256,
                    "scores_jsonl": "4" * 64,
                    "event_feature_errors_json": "5" * 64,
                }
                summary = {
                    "schema_version": "1.0.0",
                    "loop_version": "minimum-scoring-loop-v0.6.0",
                    "video_id": record["video_id"],
                    "provenance": {"video_sha256": f"{index + 1:X}" * 64},
                    "artifacts": artifacts,
                    "artifact_sha256": artifact_sha256,
                }
                summary_path = work / f"scoring-summary-{record['video_id']}.json"
                _write_json(summary_path, summary)
                feature_paths.append(feature_path)
                summary_paths.append(summary_path)
                summaries.append(summary)
                summary_hashes.append(
                    hashlib.sha256(summary_path.read_bytes()).hexdigest()
                )
            entries = [
                build_scoring_run_bundle_entry(
                    summary=summary,
                    scoring_summary_sha256=summary_hashes[index],
                    entry_id=f"{summary['video_id']}:run-v1",
                    review_id=f"{summary['video_id']}:review-v1",
                    reviewer_id="private-test-feature-reviewer",
                    review_source_sha256="E" * 64,
                    reviewed_at="2026-08-30T04:30:00Z",
                    registered_at="2026-08-30T04:45:00Z",
                )
                for index, summary in enumerate(summaries)
            ]
            ledger = build_trusted_scoring_run_bundle_ledger(
                entries=entries,
                ledger_id="private-test-feature-ledger",
                ledger_version="private-test-feature-ledger-v1",
                authority_id="private-test-feature-operator",
                registered_at="2026-08-30T04:50:00Z",
            )
            verified_feature_sources = [
                verify_indicator_feature_source_metadata(
                    scoring_summary_path=summary_path,
                    indicator_features_path=feature_path,
                    run_bundle_ledger=ledger,
                )
                for summary_path, feature_path in zip(summary_paths, feature_paths)
            ]
            copied_feature_path = work / "copied-indicator-features.jsonl"
            copied_feature_path.write_bytes(feature_paths[0].read_bytes())
            with self.assertRaisesRegex(
                ValueError, "indicator-features path differs"
            ):
                verify_indicator_feature_source_metadata(
                    scoring_summary_path=summary_paths[0],
                    indicator_features_path=copied_feature_path,
                    run_bundle_ledger=ledger,
                )

            changed_feature_output = work / "changed-feature-output"
            original_feature_bytes = feature_paths[0].read_bytes()
            feature_paths[0].write_bytes(original_feature_bytes + b"\n")
            with self.assertRaisesRegex(
                ValueError, "metadata bytes do not match compiler input"
            ):
                compile_calibration_dataset(
                    feasibility_registry_path=paths["registry"],
                    indicator_feature_paths=feature_paths,
                    manual_events_path=paths["events"],
                    manual_semantics_path=paths["semantics"],
                    coach_labels_path=paths["labels"],
                    truth_intake_manifest_path=intake_path,
                    truth_manifest_path=paths["manifest"],
                    truth_validation_report_path=paths["validation"],
                    group_metadata_path=paths["metadata"],
                    split_policy_path=paths["policy"],
                    output_dir=changed_feature_output,
                    generated_at="2026-08-30T05:00:00+00:00",
                    verified_truth_authorization=live_authorization,
                    verified_indicator_feature_sources=verified_feature_sources,
                )
            feature_paths[0].write_bytes(original_feature_bytes)
            self.assertFalse(changed_feature_output.exists())

            changed_summary_output = work / "changed-summary-output"
            original_summary_bytes = summary_paths[0].read_bytes()
            summary_paths[0].write_bytes(original_summary_bytes + b"\n")
            with self.assertRaisesRegex(
                ValueError, "scoring summary changed before calibration dataset commit"
            ):
                compile_calibration_dataset(
                    feasibility_registry_path=paths["registry"],
                    indicator_feature_paths=feature_paths,
                    manual_events_path=paths["events"],
                    manual_semantics_path=paths["semantics"],
                    coach_labels_path=paths["labels"],
                    truth_intake_manifest_path=intake_path,
                    truth_manifest_path=paths["manifest"],
                    truth_validation_report_path=paths["validation"],
                    group_metadata_path=paths["metadata"],
                    split_policy_path=paths["policy"],
                    output_dir=changed_summary_output,
                    generated_at="2026-08-30T05:00:00+00:00",
                    verified_truth_authorization=live_authorization,
                    verified_indicator_feature_sources=verified_feature_sources,
                )
            summary_paths[0].write_bytes(original_summary_bytes)
            self.assertFalse(changed_summary_output.exists())

            missing_source_output = work / "missing-feature-source-output"
            with self.assertRaisesRegex(
                ValueError, "one same-process verified.*per feature file"
            ):
                compile_calibration_dataset(
                    feasibility_registry_path=paths["registry"],
                    indicator_feature_paths=feature_paths,
                    manual_events_path=paths["events"],
                    manual_semantics_path=paths["semantics"],
                    coach_labels_path=paths["labels"],
                    truth_intake_manifest_path=intake_path,
                    truth_manifest_path=paths["manifest"],
                    truth_validation_report_path=paths["validation"],
                    group_metadata_path=paths["metadata"],
                    split_policy_path=paths["policy"],
                    output_dir=missing_source_output,
                    generated_at="2026-08-30T05:00:00+00:00",
                    verified_truth_authorization=live_authorization,
                )
            self.assertFalse(missing_source_output.exists())

            manifest = compile_calibration_dataset(
                feasibility_registry_path=paths["registry"],
                indicator_feature_paths=feature_paths,
                manual_events_path=paths["events"],
                manual_semantics_path=paths["semantics"],
                coach_labels_path=paths["labels"],
                truth_intake_manifest_path=intake_path,
                truth_manifest_path=paths["manifest"],
                truth_validation_report_path=paths["validation"],
                group_metadata_path=paths["metadata"],
                split_policy_path=paths["policy"],
                output_dir=paths["output"],
                generated_at="2026-08-30T05:00:00+00:00",
                verified_truth_authorization=live_authorization,
                verified_indicator_feature_sources=verified_feature_sources,
            )
            self.assertEqual(manifest["artifact_scope"], "calibration_input")
            self.assertEqual(manifest["truth_authorization"], binding)
            prepared = json.loads(
                (paths["output"] / "prepared/by-indicator/FS01-M02.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(prepared["artifact_scope"], "calibration_input")
            self.assertEqual(prepared["source"]["kind"], "human_coach_ground_truth")
            self.assertEqual(prepared["truth_authorization"], binding)
            samples = [
                json.loads(line)
                for line in (paths["output"] / "samples.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertTrue(all(item["schema_version"] == "1.1.0" for item in samples))
            self.assertTrue(
                all(
                    item["qualification_snapshot"]["source_status"]
                    == "verified_scoring_run_bundle_source"
                    for item in samples
                )
            )
            self.assertTrue(
                all(
                    item["qualification_snapshot"]["source_metadata"]
                    ["run_bundle_ledger_canonical_sha256"]
                    == canonical_sha256(ledger)
                    for item in samples
                )
            )
            self.assertTrue(
                all(
                    item["qualification_snapshot"][
                        "source_feature_canonical_sha256"
                    ]
                    == item["lineage"]["indicator_feature_sha256"]
                    for item in samples
                )
            )
            revised_ledger = build_trusted_scoring_run_bundle_ledger(
                entries=entries,
                ledger_id="private-test-feature-ledger",
                ledger_version="private-test-feature-ledger-v2",
                authority_id="private-test-feature-operator",
                registered_at="2026-08-30T04:55:00Z",
            )
            revised_sources = [
                verify_indicator_feature_source_metadata(
                    scoring_summary_path=summary_path,
                    indicator_features_path=feature_path,
                    run_bundle_ledger=revised_ledger,
                )
                for summary_path, feature_path in zip(summary_paths, feature_paths)
            ]
            revised_output = work / "revised-ledger-output"
            revised_manifest = compile_calibration_dataset(
                feasibility_registry_path=paths["registry"],
                indicator_feature_paths=feature_paths,
                manual_events_path=paths["events"],
                manual_semantics_path=paths["semantics"],
                coach_labels_path=paths["labels"],
                truth_intake_manifest_path=intake_path,
                truth_manifest_path=paths["manifest"],
                truth_validation_report_path=paths["validation"],
                group_metadata_path=paths["metadata"],
                split_policy_path=paths["policy"],
                output_dir=revised_output,
                generated_at="2026-08-30T05:00:00+00:00",
                verified_truth_authorization=live_authorization,
                verified_indicator_feature_sources=revised_sources,
            )
            self.assertNotEqual(
                manifest["source_files_sha256"],
                revised_manifest["source_files_sha256"],
            )
            self.assertNotEqual(
                manifest["dataset_version"], revised_manifest["dataset_version"]
            )
            root = Path(__file__).resolve().parents[1]
            for schema_name, artifact in (
                ("calibration-dataset.schema.json", manifest),
                ("calibration-prepared-dataset.schema.json", prepared),
                (
                    "scoring-truth-calibration-authorization-binding.schema.json",
                    binding,
                ),
            ):
                schema = json.loads(
                    (root / "contracts" / schema_name).read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(artifact)
            sample_schema = json.loads(
                (root / "contracts/calibration-dataset-sample.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator.check_schema(sample_schema)
            for sample in samples:
                Draft202012Validator(sample_schema).validate(sample)


if __name__ == "__main__":
    unittest.main()
