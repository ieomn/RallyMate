"""Run the CPU scoring, source-binding and result-contract regression suite.

No inference is launched and no running service is changed. The JSON receipt
records the exact source revision, test counts, failures and skipped tests.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

MODULES = (
    "action_aggregation", "source_level_weights", "scoring_source_binding", "level_route_isolation",
    "scoring_identity_namespaces", "scoring_loop", "sam_footwork",
    "level_grading", "level_assessment_integration", "direct_scoring_rules", "indicator_assessment",
    "card_formal_scoring_inputs", "card_evaluation", "training_dimensions",
    "relative_score", "score_comparison", "scoring_unknown_confidence", "model_measurement_contract",
    "technique_assessment", "technique_assessment_record_scope", "technical_review",
    "perception_dictionary_contract", "scoring_guide_features", "fs01_fs02_features", "fs09_features",
    "events", "source_aligned_features", "features", "fs01_fs02_truth_evaluation",
    "event_phase_candidates", "source_preload_independent", "source_preload_translation",
    "source_preload_consumers", "isotropic_coordinates", "numeric_performance_equivalence",
    "multimodal_scoring", "specialist_motion_assessment", "phase_authority_metadata",
    "tennis_candidate_binding", "tennis_assessment", "tennis_profiles", "tennis_motion_patterns",
    "gs_phase_kinematics", "training_session_analysis", "full_test_scoring_validation",
    "subject_navigation_clock", "subject_lock", "subject_selection_replay", "initial_subject_buffer",
    "primary_player", "secondary_subjects", "tennis_subject_scoring", "source_time_bindings",
    "tennis_assessment_api", "tennis_action_comparison", "tennis_hand_roles", "swing_kinematics",
    "vector_kinematics",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/scoring-refactor-20261112/backend-regression.json")
    args = parser.parse_args()
    # Importing API tests must not resolve a local RTMPose checkpoint. Test
    # fixtures supply their own settings; this selects no inference work.
    os.environ.setdefault("RALLYMATE_POSE_BACKEND", "yolo")
    modules = [f"tests.test_{name}" for name in MODULES]
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    receipt = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(), "git_revision": revision,
        "scope": "cpu_scoring_source_binding_and_result_contracts", "modules": modules,
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "failed_tests": [{"test": test.id(), "traceback": trace} for test, trace in result.failures],
        "errored_tests": [{"test": test.id(), "traceback": trace} for test, trace in result.errors],
        "skipped_tests": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
        "duration_seconds": round(time.perf_counter() - started, 3), "all_passed": result.wasSuccessful(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: receipt[key] for key in ("tests_run", "failures", "errors", "skipped", "all_passed")}), flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
