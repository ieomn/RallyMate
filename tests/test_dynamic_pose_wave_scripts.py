from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DynamicPoseWaveScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _load_script("run_fs09_pose_wave_v2")
        cls.comparison = _load_script("compare_pose_wave_same_window")
        cls.report = _load_script("build_pose_scoring_ab_report")

    def test_runner_scope_uses_registry_indicator_and_event_sets(self) -> None:
        registry = {
            "registry_version": "synthetic-v3",
            "scope": {"events": ["FS01.preload", "FS02"]},
            "indicators": [
                {"indicator_id": "FS01-M02"},
                {"indicator_id": "FS01-M03"},
                {"indicator_id": "FS02-M04"},
            ],
        }
        scope = self.runner._registry_indicator_scope(registry)
        self.assertEqual(3, scope["indicator_count"])
        self.assertEqual(
            ["FS01-M02", "FS01-M03", "FS02-M04"],
            scope["indicator_ids"],
        )
        self.assertEqual(["FS01", "FS02"], scope["event_codes"])
        self.assertIn("does not imply", scope["semantics"])

    def test_same_window_comparison_accepts_any_shared_nonempty_set(self) -> None:
        reports = {
            "a": {
                "indicator_results": {key: {} for key in ("I-1", "I-2", "I-3")},
                "indicator_scope": {"indicator_count": 3},
            },
            "b": {
                "indicator_results": {key: {} for key in ("I-3", "I-1", "I-2")},
                "assertions": {"target_indicator_count": 3},
            },
        }
        self.assertEqual(
            ["I-1", "I-2", "I-3"],
            self.comparison._shared_indicator_ids(reports),
        )
        reports["b"]["indicator_results"].pop("I-2")
        with self.assertRaisesRegex(RuntimeError, "same registry indicator set"):
            self.comparison._shared_indicator_ids(reports)

    def test_same_window_comparison_aggregates_typed_score_block_reasons(self) -> None:
        report = {
            "summary": {
                "indicator_feature_validity": {
                    "FS01-M04": {
                        "blocking_reason_code_counts": {
                            "event_phase_proxy_right_censored_unverified": 2,
                            "event_scoring_quality_blocked": 3,
                        }
                    },
                    "FS02-M05": {
                        "blocking_reason_code_counts": {
                            "event_phase_proxy_low_sample_unverified": 1,
                            "event_scoring_quality_blocked": 1,
                        }
                    },
                }
            }
        }
        self.assertEqual(
            self.comparison._score_reason_counts(report),
            {
                "event_phase_proxy_low_sample_unverified": 1,
                "event_phase_proxy_right_censored_unverified": 2,
                "event_scoring_quality_blocked": 4,
            },
        )

    def test_report_summary_renders_actual_count_and_safe_statuses(self) -> None:
        results = {
            "I-1": {
                "instances": 2,
                "feature_status_counts": {"measured": 2},
                "scoring_status_counts": {"calibration_required": 2},
            },
            "I-2": {
                "instances": 2,
                "feature_status_counts": {"measured": 1, "unavailable": 1},
                "scoring_status_counts": {
                    "calibration_required": 1,
                    "unavailable": 1,
                },
            },
            "I-3": {
                "instances": 2,
                "feature_status_counts": {"measured": 1, "unavailable": 1},
                "scoring_status_counts": {
                    "calibration_required": 1,
                    "unavailable": 1,
                },
            },
        }
        summary = self.report._pose_wave_summary(
            {
                "indicator_scope": {"indicator_count": 3},
                "indicator_results": results,
                "summary": {"event_counts": {"FS01": 2, "FS02": 1}},
                "assertions": {"grade_count": 0},
            }
        )
        self.assertEqual(3, summary["indicator_count"])
        self.assertEqual(6, summary["instance_count"])
        body = self.report._pose_wave_intro(summary)
        self.assertIn("3 项指标、6 条指标事件记录", body)
        self.assertIn("4 条 <code>calibration_required</code>", body)
        self.assertIn("2 条 <code>unavailable</code>", body)
        self.assertIn("A～E 数量为 0", body)

    def test_report_scopes_are_derived_from_actual_model_rows(self) -> None:
        model_validity = {key: {} for key in ("I-1", "I-2", "I-3")}
        self.assertEqual(
            ["I-1", "I-2", "I-3"],
            self.report._shared_model_indicator_ids(
                {
                    "models": [
                        {"backend": "a", "indicator_feature_validity": model_validity},
                        {"backend": "b", "indicator_feature_validity": dict(model_validity)},
                    ]
                }
            ),
        )
        scope = self.report._same_window_scope(
            {
                "models": {
                    "a": {"event_counts": {"FS01": 2}},
                    "b": {"event_counts": {"FS01": 3, "FS02": 1}},
                },
                "indicators": {
                    key: {"a": {}, "b": {}} for key in ("I-1", "I-2", "I-3")
                },
            }
        )
        self.assertEqual(3, scope["indicator_count"])
        self.assertEqual(["FS01", "FS02"], scope["event_codes"])

    def test_scripts_do_not_assert_a_literal_eight_indicator_scope(self) -> None:
        for name in (
            "run_fs09_pose_wave_v2.py",
            "compare_pose_wave_same_window.py",
            "build_pose_scoring_ab_report.py",
        ):
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("eight_indicators_in_both", source)
            self.assertNotIn("eight-indicator", source)
            self.assertNotIn("8 指标", source)

    def test_report_does_not_count_ledger_template_as_production_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "trusted-calibration-promotion-ledger.template.json").write_text(
                json.dumps(
                    {
                        "artifact_scope": (
                            "trusted_calibration_promotion_ledger_template_not_loadable"
                        ),
                        "ledger_id": "REPLACE_ME",
                    }
                ),
                encoding="utf-8",
            )
            (root / "candidate.json").write_text(
                json.dumps(
                    {
                        "artifact_scope": "calibration_candidate",
                        "backend": "threshold_rule",
                        "indicator_id": "FS01-M02",
                    }
                ),
                encoding="utf-8",
            )
            production = root / "production.json"
            production.write_text(
                json.dumps(
                    {
                        "artifact_scope": "production",
                        "backend": "threshold_rule",
                        "indicator_id": "FS01-M02",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                self.report._production_calibration_asset_paths(root), [production]
            )

    def test_event_disagreement_summary_rejects_accuracy_claim(self) -> None:
        report = {
            "semantics": {"accuracy_claim": False},
            "inputs": {
                "left": {"label": "a"},
                "right": {"label": "b"},
            },
            "threshold_results": [
                {
                    "minimum_segment_iou": 0.3,
                    "left_event_count": 2,
                    "right_event_count": 3,
                }
            ],
        }
        summary = self.report._event_disagreement_summary(report)
        self.assertEqual(2, summary["left_event_count"])
        self.assertEqual(3, summary["right_event_count"])
        report["semantics"]["accuracy_claim"] = True
        with self.assertRaisesRegex(ValueError, "must not claim accuracy"):
            self.report._event_disagreement_summary(report)

    def test_fixed_boundary_summary_counts_actual_pair_states(self) -> None:
        report = {
            "comparison_semantics": {
                "accuracy_claim": False,
                "grade_generated": False,
            },
            "boundary_source": {
                "event_count": 2,
                "truth_status": "candidate_source_not_truth",
            },
            "registry_source": {"indicator_count": 3},
            "feature_metrics": {"f1": {}, "f2": {}},
            "feature_pairs": [
                {"comparison": {"validity_state": "both_valid"}},
                {"comparison": {"validity_state": "model_a_only"}},
                {"comparison": {"validity_state": "both_valid"}},
            ],
            "model_order": ["a", "b"],
            "models": {"a": {}, "b": {}},
        }
        summary = self.report._fixed_boundary_summary(report)
        self.assertEqual(3, summary["feature_pair_count"])
        self.assertEqual(2, summary["validity_states"]["both_valid"])
        self.assertEqual(1, summary["validity_states"]["model_a_only"])

    def test_pose_profile_routing_summary_rejects_accuracy_and_cherry_picking(self) -> None:
        report = {
            "scope": {"current_primary": "a", "indicator_count": 3},
            "profiles": {"a": {}, "b": {}},
            "comparisons": {"b": {}},
            "routing_decision": {
                "selected_scoring_primary": "a",
                "automatic_model_promotion": False,
                "per_feature_or_per_event_model_cherry_picking_allowed": False,
            },
            "safety": {
                field: False
                for field in (
                    "accuracy_claim",
                    "ground_truth_provided",
                    "grades_generated",
                    "thresholds_generated",
                    "maturity_promoted",
                    "fixed_boundary_coverage_is_accuracy",
                    "self_segmented_counts_used_for_routing",
                )
            },
        }
        summary = self.report._pose_profile_routing_summary(report)
        self.assertEqual("a", summary["current_primary"])
        report["routing_decision"][
            "per_feature_or_per_event_model_cherry_picking_allowed"
        ] = True
        with self.assertRaisesRegex(ValueError, "prohibit cherry-picking"):
            self.report._pose_profile_routing_summary(report)

    def test_scoring_blocker_summary_separates_measurement_from_scoring(self) -> None:
        report = {
            "source": {"quality_policy_version": "quality-v1"},
            "counts": {
                "indicator_record_count": 5,
                "status_counts": {"calibration_required": 1, "unavailable": 4},
                "mutually_exclusive_decomposition": {
                    "calibration_required_complete_features_no_score_block": 1,
                    "unavailable_complete_features_score_only": 2,
                    "unavailable_feature_incomplete_without_score_block": 1,
                    "unavailable_hard_fail": 1,
                },
            },
            "human_review_priority": [],
            "assertions": {
                "typed_reason_mapping_consistent": True,
                "recoverable_means_calibration_required_not_scored": True,
                "quality_gate_modified": False,
                "grade_generated": False,
                "threshold_generated": False,
                "maturity_promoted": False,
                "counts_are_accuracy_metrics": False,
            },
        }
        summary = self.report._scoring_blocker_audit_summary(report)
        self.assertEqual(5, summary["counts"]["indicator_record_count"])
        report["assertions"]["grade_generated"] = True
        with self.assertRaisesRegex(ValueError, "unsafe assertion"):
            self.report._scoring_blocker_audit_summary(report)

    def test_truth_priority_worklist_stays_annotation_only(self) -> None:
        report = {
            "counts": {"work_items": 2, "accepted_annotations": 0},
            "safety": {
                field: False
                for field in (
                    "model_candidates_are_truth",
                    "quality_gate_modified",
                    "grades_generated",
                    "thresholds_generated",
                    "maturity_promoted",
                    "priority_is_accuracy",
                    "clearing_one_item_automatically_changes_score",
                )
            },
        }
        report["safety"]["maximum_post_review_status_without_calibration"] = (
            "calibration_required"
        )
        self.assertEqual(
            2, self.report._truth_priority_worklist_summary(report)["work_items"]
        )
        report["safety"]["grades_generated"] = True
        with self.assertRaisesRegex(ValueError, "unsafe claim"):
            self.report._truth_priority_worklist_summary(report)

    def test_feature_gap_summary_forbids_automatic_profile_fallback(self) -> None:
        report = {
            "counts": {
                "indicator_record_count": 5,
                "feature_measured_indicator_count": 3,
                "feature_unavailable_indicator_count": 2,
            },
            "recovery_priority": [],
            "profile_alternative_metrics": [],
            "assertions": {
                "required_feature_contract_matches_registry": True,
                "compact_features_match_full_features": True,
                "profile_comparisons_use_identical_candidate_boundaries": True,
                "human_ground_truth_still_required": True,
                "measurement_gate_modified": False,
                "scoring_gate_modified": False,
                "automatic_profile_fallback_enabled": False,
                "cross_model_counts_are_accuracy_metrics": False,
                "grade_generated": False,
                "threshold_generated": False,
                "maturity_promoted": False,
            },
        }
        summary = self.report._feature_observation_gap_summary(report)
        self.assertEqual(2, summary["counts"]["feature_unavailable_indicator_count"])
        report["assertions"]["automatic_profile_fallback_enabled"] = True
        with self.assertRaisesRegex(ValueError, "unsafe automatic_profile_fallback_enabled"):
            self.report._feature_observation_gap_summary(report)

    def test_small_roi_summary_is_experimental_and_not_accuracy(self) -> None:
        report = {
            "settings": {
                "baseline_min_roi_size_px": 32,
                "experimental_min_roi_size_px": 8,
            },
            "inference": {
                "counts": {
                    "inference_attempted": 4,
                    "pose_output_recovered": 4,
                }
            },
            "measurement_impact": {
                "status_transitions": {
                    "measured_to_measured": 10,
                    "unavailable_to_measured": 2,
                    "unavailable_to_unavailable": 1,
                },
                "recovered_indicator_instance_count": 2,
                "regressed_indicator_instance_count": 0,
            },
            "safety": {
                "accuracy_claim": False,
                "ground_truth_provided": False,
                "production_enabled": False,
                "current_production_default_unchanged": True,
                "automatic_profile_fallback_enabled": False,
                "measurement_gate_modified": False,
                "scoring_gate_modified": False,
                "grades_generated": False,
                "thresholds_generated": False,
                "maturity_promoted": False,
                "recovered_means_observable_under_experimental_small_roi_not_accurate": True,
            },
        }
        video = {
            "status": "passed",
            "video": {"codec": "h264", "frame_count": 20},
            "safety": {
                "accuracy_claim": False,
                "ground_truth_provided": False,
                "production_enabled": False,
                "grade_generated": False,
                "threshold_generated": False,
            },
        }
        summary = self.report._small_roi_recovery_summary(report, video)
        self.assertEqual(13, summary["indicator_instance_count"])
        self.assertEqual(2, summary["recovered_indicator_instance_count"])
        report["safety"]["production_enabled"] = True
        with self.assertRaisesRegex(ValueError, "unsafe production_enabled"):
            self.report._small_roi_recovery_summary(report, video)

    def test_small_roi_truth_summary_withholds_metrics_until_independent_truth(self) -> None:
        manifest = {
            "scope": {
                "frame_count": 3,
                "joint_task_count": 42,
                "joint_names": [f"joint-{index}" for index in range(14)],
                "required_independent_annotators": 2,
            },
            "safety": {
                "model_keypoints_embedded_in_annotation_ui": False,
                "truth_prefilled_from_model": False,
                "independent_adjudication_required": True,
                "accuracy_claim": False,
                "production_enabled": False,
                "automatic_profile_fallback_enabled": False,
                "grade_generated": False,
                "threshold_generated": False,
                "maturity_promoted": False,
            },
        }
        validation = {
            "status": "annotation_required",
            "counts": {
                "joint_tasks": 42,
                "raw_annotations": 0,
                "accepted_adjudications": 0,
            },
        }
        error_report = {
            "status": "annotation_required",
            "metrics": None,
            "routing_decision": {"switch_allowed": False},
            "safety": {
                "model_values_used_as_truth": False,
                "acceptance_threshold_generated": False,
                "production_enabled": False,
                "automatic_profile_fallback_enabled": False,
                "grade_generated": False,
                "threshold_generated": False,
                "maturity_promoted": False,
            },
        }

        summary = self.report._small_roi_truth_summary(
            manifest, validation, error_report
        )
        self.assertEqual(42, summary["tasks"])
        self.assertIsNone(summary["metrics"])
        error_report["routing_decision"]["switch_allowed"] = True
        with self.assertRaisesRegex(ValueError, "routing switch"):
            self.report._small_roi_truth_summary(manifest, validation, error_report)

    def test_residual_computability_summary_proves_all_indicators_without_zero_fill(self) -> None:
        audit = {
            "status": (
                "all_registry_indicators_have_real_measured_instances_residuals_fail_closed"
            ),
            "counts": {
                "registry_indicator_count": 2,
                "indicator_with_measured_instance_count": 2,
                "indicator_without_measured_instance_count": 0,
                "indicator_event_instance_count": 7,
                "measured_indicator_event_instance_count": 5,
                "unavailable_indicator_event_instance_count": 2,
                "residual_event_count": 2,
            },
            "classification_counts_by_indicator_instance": {
                "primary_bbox_clipped_at_image_boundary": 1,
                "primary_source_track_transition": 1,
            },
            "per_indicator": [
                {
                    "indicator_id": "FS01-M02",
                    "measured_instance_count": 3,
                    "all_measured_instances_contract_complete": True,
                },
                {
                    "indicator_id": "FS02-M02",
                    "measured_instance_count": 2,
                    "all_measured_instances_contract_complete": True,
                },
            ],
            "safety": {
                "accuracy_claim": False,
                "event_ground_truth_provided": False,
                "keypoint_ground_truth_provided": False,
                "feature_quality_gate_modified": False,
                "zero_fill_used": False,
                "cross_model_cherry_picking_used": False,
                "production_route_changed": False,
                "grade_generated": False,
                "threshold_generated": False,
                "maturity_promoted": False,
            },
        }
        video = {
            "status": "passed",
            "video": {
                "codec": "h264",
                "sample_decode": [{"frame_index": 0, "decoded": True}],
            },
            "safety": {
                "accuracy_claim": False,
                "ground_truth_provided": False,
                "production_route_changed": False,
                "feature_quality_gate_modified": False,
                "zero_fill_used": False,
                "grade_generated": False,
                "threshold_generated": False,
            },
        }
        summary = self.report._residual_computability_summary(audit, video)
        self.assertEqual(2, summary["min_measured_per_indicator"])
        self.assertEqual(3, summary["max_measured_per_indicator"])
        audit["safety"]["zero_fill_used"] = True
        with self.assertRaisesRegex(ValueError, "unsafe zero_fill_used"):
            self.report._residual_computability_summary(audit, video)

    def test_deployment_smoke_summary_is_registry_derived_and_safe(self) -> None:
        report = {
            "status": "passed",
            "semantics": {"accuracy_claim": False},
            "preset": "candidate",
            "pose_backend": {
                "native_keypoint_format": "coco_wholebody133",
                "native_keypoint_count": 133,
            },
            "processed_frames": 120,
            "effective_processed_fps": 20.0,
            "feasibility_registry": {
                "indicator_count": 3,
                "indicator_ids": ["I-1", "I-2", "I-3"],
            },
            "indicator_ids": ["I-3", "I-1", "I-2"],
            "event_counts": {"FS01": 2, "FS02": 2},
            "score_status_counts": {
                "calibration_required": 5,
                "unavailable": 1,
            },
            "non_null_grade_count": 0,
            "non_null_threshold_version_count": 0,
            "bundle_validation": {"status": "passed"},
        }
        summary = self.report._deployment_smoke_summary(report)
        self.assertEqual(3, summary["indicator_count"])
        self.assertEqual(6, summary["indicator_record_count"])
        self.assertEqual(133, summary["native_keypoint_count"])
        report["non_null_grade_count"] = 1
        with self.assertRaisesRegex(ValueError, "must not contain grades"):
            self.report._deployment_smoke_summary(report)

    def test_pose_diagnostic_truth_summary_rejects_fake_truth(self) -> None:
        manifest = {
            "status": "annotation_required",
            "diagnostic_types": ["a", "b"],
            "frame_domain": {"frame_count": 600},
            "source_queue": {"sha256": "A" * 64, "candidate_task_count": 39},
            "safety": {"manual_truth_present": False, "quality_gate_modified": False},
        }
        evaluation = {
            "status": "annotation_required",
            "source": {"truth_pack_source_queue_sha256": "A" * 64},
            "annotation_counts": {
                "accepted_coverage_rows": 0,
                "accepted_positive_rows": 0,
            },
            "safety": {
                "candidate_only_review_used_as_recall_truth": False,
                "quality_gate_modified": False,
                "formal_scoring_accuracy_claim": False,
                "grades_generated": False,
                "thresholds_generated": False,
                "maturity_promoted": False,
            },
        }
        summary = self.report._pose_diagnostic_truth_summary(manifest, evaluation)
        self.assertEqual(summary["frame_count"], 600)
        self.assertEqual(summary["candidate_task_count"], 39)
        manifest["safety"]["manual_truth_present"] = True
        with self.assertRaisesRegex(ValueError, "must not claim truth"):
            self.report._pose_diagnostic_truth_summary(manifest, evaluation)

    def test_multivideo_readiness_summary_separates_calculation_and_scoring(self) -> None:
        report = json.loads(
            (
                ROOT
                / "reports"
                / "multivideo-scoring-readiness"
                / "m65-halpe26-three-video-typed-blocker-recovery-v1"
                / "audit.json"
            ).read_text(encoding="utf-8")
        )
        summary = self.report._multivideo_readiness_summary(report)
        self.assertEqual(2291, summary["counts"]["operational_feature_measured"])
        self.assertEqual(834, summary["counts"]["ready_for_calibration_application"])
        self.assertEqual(
            "keypoint_jump_candidates_present",
            summary["scoring_block_recovery_priority"][0]["flag"],
        )
        forged = json.loads(json.dumps(report))
        forged["safety"]["calculation_completeness_is_accuracy"] = True
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.report._multivideo_readiness_summary(forged)

    def test_pose_diagnostic_policy_review_summary_is_fail_closed(self) -> None:
        report = {
            "status": "annotation_and_protocol_required",
            "quality_policy_version_under_review": "indicator-event-quality-v1.6.0",
            "protocol": None,
            "sources": [
                {"video_id": "a"},
                {"video_id": "b"},
                {"video_id": "c"},
            ],
            "blockers": [
                "external_preregistered_acceptance_protocol_missing",
                "full_timeline_diagnostic_truth_missing",
            ],
            "by_diagnostic_type": {
                name: {
                    "status": "acceptance_protocol_required",
                    "precision": None,
                    "recall": None,
                    "f1": None,
                }
                for name in (
                    "keypoint_jump",
                    "left_right_swap",
                    "primary_identity_ambiguity",
                    "source_track_switch",
                )
            },
            "policy_decision": {
                "automatic_policy_change_allowed": False,
                "quality_policy_change_applied": False,
                "next_quality_policy_version": None,
            },
            "safety": {
                "A_to_E_thresholds_generated": False,
                "grades_generated": False,
                "quality_gate_modified": False,
                "maturity_promoted": False,
                "self_declared_protocol_identity_is_trusted": False,
            },
        }
        summary = self.report._pose_diagnostic_policy_review_summary(report)
        self.assertEqual(summary["source_count"], 3)
        self.assertEqual(summary["diagnostic_type_count"], 4)
        report["policy_decision"]["quality_policy_change_applied"] = True
        with self.assertRaisesRegex(ValueError, "must not modify"):
            self.report._pose_diagnostic_policy_review_summary(report)


if __name__ == "__main__":
    unittest.main()
