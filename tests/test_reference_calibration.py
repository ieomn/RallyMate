"""Tests for coach-free reference-population rubric calibration and CLI."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from rallymate_scoring.reference_calibration import (
    CALIBRATION_ORIGIN,
    COHORT_MANIFEST_SCHEMA_VERSION,
    InsufficientCohortError,
    ReferenceCalibrationError,
    calibrate_reference_population,
    evaluate_cohort,
    export_calibration_artifacts,
    mann_whitney_cliffs,
    summarize_distribution,
    verify_rubric_policy_signature,
)
from rallymate_service.tennis_assessment import analyze_tennis_assessment
from tests.test_tennis_assessment import episodes, inputs


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _tiered_cohort_manifest() -> dict:
    # Realistic tiered distributions across pro, advanced, intermediate, beginner.
    tier_profiles = {
        "pro": {
            "videos": 3,
            "knee_load": [40.0, 44.0, 48.0, 52.0, 56.0],
            "support_control": [1.55, 1.75, 1.95, 2.20, 2.45],
            "coil": [40.0, 46.0, 54.0, 60.0, 66.0],
            "arm_space": [0.72, 0.76, 0.80, 0.84, 0.88],
        },
        "advanced": {
            "videos": 3,
            "knee_load": [34.0, 38.0, 42.0, 46.0, 50.0],
            "support_control": [1.42, 1.65, 1.88, 2.15, 2.38],
            "coil": [32.0, 38.0, 45.0, 52.0, 58.0],
            "arm_space": [0.68, 0.74, 0.79, 0.83, 0.87],
        },
        "intermediate": {
            "videos": 2,
            "knee_load": [18.0, 22.0, 25.0, 28.0, 31.0],
            "support_control": [1.05, 1.18, 1.30, 2.85, 3.15],
            "coil": [18.0, 22.0, 26.0, 30.0, 33.0],
            "arm_space": [0.56, 0.62, 0.92, 0.95, 0.97],
        },
        "beginner": {
            "videos": 2,
            "knee_load": [8.0, 10.0, 12.0, 15.0, 18.0],
            "support_control": [0.72, 0.85, 0.96, 3.45, 3.80],
            "coil": [7.0, 10.0, 13.0, 16.0, 19.0],
            "arm_space": [0.42, 0.48, 0.54, 0.98, 1.0],
        },
    }
    entries = []
    for tier, cfg in tier_profiles.items():
        for v_idx in range(cfg["videos"]):
            sample_id = f"{tier}-video-{v_idx}"
            eps = []
            for ep_idx in range(len(cfg["knee_load"])):
                jitter = (v_idx - 1) * 0.4
                arm_val = min(1.0, max(0.2, cfg["arm_space"][ep_idx] + (v_idx - 1) * 0.005))
                eps.append(
                    {
                        "episode_id": f"{sample_id}-ep-{ep_idx}",
                        "metrics": {
                            "knee_load": {"value": cfg["knee_load"][ep_idx] + jitter, "score": 50.0},
                            "support_control": {"value": cfg["support_control"][ep_idx] + jitter * 0.02, "score": 50.0},
                            "coil": {"value": cfg["coil"][ep_idx] + jitter, "score": 50.0},
                            "arm_space": {"value": arm_val, "score": 50.0},
                        },
                    }
                )
            entries.append(
                {
                    "sample_id": sample_id,
                    "tier": tier,
                    "source_video_sha256": _sha(sample_id),
                    "pose_source": "sam3d",
                    "episodes": eps,
                }
            )
    return {"schema_version": COHORT_MANIFEST_SCHEMA_VERSION, "entries": entries}


class ReferenceCalibrationTests(unittest.TestCase):
    def test_distribution_summary_and_exact_mann_whitney_cliffs_delta(self):
        summary = summarize_distribution([10.0, 20.0, 30.0, 40.0, 50.0], n_videos=2)
        self.assertEqual(summary["n"], 5)
        self.assertEqual(summary["n_videos"], 2)
        self.assertEqual(summary["p50"], 30.0)
        self.assertEqual(summary["iqr"], 20.0)

        # Complete separation for n1=3, n2=3 -> U=9, delta=+1.0, exact p=2/comb(6,3)=0.1.
        sep = mann_whitney_cliffs([10.0, 11.0, 12.0], [1.0, 2.0, 3.0])
        self.assertEqual(sep["method"], "exact_midrank_dp")
        self.assertEqual(sep["mann_whitney_u"], 9.0)
        self.assertEqual(sep["cliffs_delta"], 1.0)
        self.assertEqual(sep["effect_size"], "large")
        self.assertAlmostEqual(sep["p_value"], 0.1, places=6)

        # Complete reversal -> delta=-1.0.
        rev = mann_whitney_cliffs([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
        self.assertEqual(rev["cliffs_delta"], -1.0)
        self.assertAlmostEqual(rev["p_value"], 0.1, places=6)

        # Exact ties handling.
        tied = mann_whitney_cliffs([5.0, 5.0], [5.0, 10.0])
        self.assertEqual(tied["mann_whitney_u"], 1.0)
        self.assertEqual(tied["cliffs_delta"], -0.5)

        # Large-N branch uses tie-corrected normal approximation.
        large = mann_whitney_cliffs(np.linspace(50, 70, 20).tolist(), np.linspace(10, 30, 20).tolist())
        self.assertEqual(large["method"], "normal_approx_tie_corrected")
        self.assertEqual(large["cliffs_delta"], 1.0)
        self.assertLess(large["p_value"], 1e-6)

    def test_tiered_calibration_derives_valid_signed_policy_and_round_trips_into_engine(self):
        manifest = _tiered_cohort_manifest()
        calibrated = calibrate_reference_population(manifest, min_separation_delta=0.5)
        policy = calibrated["policy"]
        report = calibrated["report"]

        self.assertFalse(policy["coach_calibrated"])
        self.assertEqual(policy["calibration_origin"], CALIBRATION_ORIGIN)
        self.assertEqual(policy["numeric_rule_origin"], CALIBRATION_ORIGIN)
        self.assertEqual(policy["rubric_version"], "tennis-action-rubric-v1.4.0")
        self.assertTrue(verify_rubric_policy_signature(policy))
        self.assertEqual(set(policy["indicator_links"]), {"knee_load", "support_control", "coil", "arm_space"})
        self.assertIn("FS01-M02", policy["indicator_links"]["knee_load"]["indicator_ids"])

        # Physical bounds & strict monotonicity checks.
        knee_knots = policy["rules"]["knee_load"]["knots"]
        self.assertEqual(len(knee_knots), 2)
        self.assertLess(knee_knots[0], knee_knots[1])

        arm_knots = policy["rules"]["arm_space"]["knots"]
        self.assertEqual(len(arm_knots), 4)
        self.assertLess(0.0, arm_knots[0])
        self.assertLess(arm_knots[0], arm_knots[1])
        self.assertLessEqual(arm_knots[1], arm_knots[2])
        self.assertLess(arm_knots[2], arm_knots[3])
        self.assertLessEqual(arm_knots[3], 1.0)
        self.assertLess(arm_knots[2], 1.0)

        # Verify symmetric trapezoid separation proved even when beginners split cramped vs locked.
        arm_report = report["metrics"]["arm_space"]
        self.assertGreater(arm_report["upper_vs_lower"]["proximity_to_reference_median_separation"]["cliffs_delta"], 0.6)
        self.assertGreater(arm_report["proposed_score_separation"]["cliffs_delta"], 0.6)
        self.assertGreater(report["metrics"]["knee_load"]["upper_vs_lower"]["raw_separation"]["cliffs_delta"], 0.8)

        # Round-trip directly into analyze_tennis_assessment() via exported policy file.
        timeline, records = inputs()
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = Path(tmpdir) / "calibrated_policy.json"
            report_path = Path(tmpdir) / "calibration_report.json"
            export_calibration_artifacts(calibrated, policy_path=policy_path, report_path=report_path)
            assessment = analyze_tennis_assessment(
                timeline,
                records,
                video_sha256="a" * 64,
                source_run_id="calib-roundtrip",
                motion_episodes=episodes(),
                rubric_policy=policy_path,
            )
        self.assertEqual(assessment["status"], "scored")
        self.assertEqual(assessment["rubric"]["policy_sha256"], policy["policy_sha256"])
        self.assertEqual(assessment["rubric"]["calibration_origin"], CALIBRATION_ORIGIN)
        self.assertFalse(assessment["rubric"]["coach_calibrated"])
        self.assertEqual(assessment["rubric"]["rules"]["arm_space"]["knots"], arm_knots)

    def test_calibration_accepts_full_tennis_assessment_outputs_with_sha_provenance(self):
        timeline, records = inputs()
        pro_sha = _sha("pro-real-assessment")
        beg_sha = _sha("beg-real-assessment")
        pro_records = deepcopy(records)
        for row in pro_records:
            row["video_sha256"] = pro_sha
        pro_ep = episodes()
        for ep in pro_ep:
            ep["continuity"]["video_sha256"] = pro_sha
        pro_report = analyze_tennis_assessment(
            timeline, pro_records, video_sha256=pro_sha, source_run_id="pro-run", motion_episodes=pro_ep
        )
        self.assertEqual(pro_report["observation_count"], 3)
        # Modify beginner records to upright knee & narrow stance.
        beg_records = deepcopy(records)
        for row in beg_records:
            for side, sign in (("left", -1), ("right", 1)):
                row["keypoints_3d"][f"{side}_knee"] = [sign * 0.4, -0.625, 0.06]
                row["keypoints_3d"][f"{side}_ankle"] = [sign * 0.3, -1.25, 0.0]
            row["video_sha256"] = beg_sha
        beg_ep = episodes()
        for ep in beg_ep:
            ep["continuity"]["video_sha256"] = beg_sha
        beg_report = analyze_tennis_assessment(
            timeline, beg_records, video_sha256=beg_sha, source_run_id="beg-run", motion_episodes=beg_ep
        )
        self.assertEqual(beg_report["observation_count"], 3)

        # Build multi-video cohort with assessment payloads directly.
        entries = []
        for idx in range(3):
            sha = pro_sha if idx == 0 else _sha(f"pro-extra-{idx}")
            r = deepcopy(pro_report)
            r["source_video_sha256"] = sha
            for j, ep in enumerate(r["episodes"]):
                ep["metrics"]["coil"]["value"] += idx * 1.5 + j * 2.0
                ep["metrics"]["support_control"]["value"] += idx * 0.15 + j * 0.08
                ep["metrics"]["arm_space"]["value"] = 0.74 + idx * 0.03 + j * 0.02
            entries.append({"sample_id": f"pro-{idx}", "tier": "pro", "source_video_sha256": sha, "assessment": r})
        for idx in range(2):
            sha = beg_sha if idx == 0 else _sha(f"beg-extra-{idx}")
            r = deepcopy(beg_report)
            r["source_video_sha256"] = sha
            entries.append({"sample_id": f"beg-{idx}", "tier": "beginner", "source_video_sha256": sha, "assessment": r})

        calibrated = calibrate_reference_population(
            {"schema_version": COHORT_MANIFEST_SCHEMA_VERSION, "entries": entries},
            min_reference_episodes=6,
            min_comparison_episodes=6,
            min_reference_videos=3,
            min_comparison_videos=2,
        )
        self.assertTrue(verify_rubric_policy_signature(calibrated["policy"]))
        self.assertEqual(calibrated["report"]["total_videos"], 5)

    def test_insufficient_sample_and_video_guards_fail_closed(self):
        manifest = _tiered_cohort_manifest()
        # Only 1 pro video (< min_reference_videos=3) and < 10 episodes.
        small = {
            "schema_version": COHORT_MANIFEST_SCHEMA_VERSION,
            "entries": [manifest["entries"][0], manifest["entries"][-1]],
        }
        with self.assertRaises(InsufficientCohortError):
            calibrate_reference_population(small)

        # Missing lower tier fails closed when require_lower_tier=True.
        no_lower = {
            "schema_version": COHORT_MANIFEST_SCHEMA_VERSION,
            "entries": [e for e in manifest["entries"] if e["tier"] in ("pro", "advanced")],
        }
        with self.assertRaises(InsufficientCohortError):
            calibrate_reference_population(no_lower, require_lower_tier=True)

        # Cohort evaluation still succeeds descriptively without emitting a policy.
        summary = evaluate_cohort(small)
        self.assertEqual(summary["total_videos"], 2)

    def test_physical_bound_and_provenance_violations_fail_closed(self):
        # Reference arm_space p90 >= 0.99 (locked straight elbows) must fail closed.
        locked_manifest = _tiered_cohort_manifest()
        for entry in locked_manifest["entries"]:
            if entry["tier"] in ("pro", "advanced"):
                for ep in entry["episodes"]:
                    ep["metrics"]["arm_space"]["value"] = 0.995
        with self.assertRaisesRegex(ReferenceCalibrationError, "arm_space"):
            calibrate_reference_population(locked_manifest)

        # Zero-IQR degenerate reference cohort fails closed.
        zero_iqr = _tiered_cohort_manifest()
        for entry in zero_iqr["entries"]:
            if entry["tier"] in ("pro", "advanced"):
                for ep in entry["episodes"]:
                    ep["metrics"]["coil"]["value"] = 45.0
        with self.assertRaisesRegex(ReferenceCalibrationError, "spread|degenerate"):
            calibrate_reference_population(zero_iqr)

        # Duplicate source_video_sha256 across entries fails closed.
        dup_sha = _tiered_cohort_manifest()
        dup_sha["entries"][1]["source_video_sha256"] = dup_sha["entries"][0]["source_video_sha256"]
        with self.assertRaisesRegex(ReferenceCalibrationError, "duplicate source_video_sha256"):
            calibrate_reference_population(dup_sha)

        # Unseparated metric rejected when min_separation_delta > 0.
        flat_sep = _tiered_cohort_manifest()
        for entry in flat_sep["entries"]:
            for i, ep in enumerate(entry["episodes"]):
                ep["metrics"]["knee_load"]["value"] = 20.0 + i * 5.0
        with self.assertRaises(ReferenceCalibrationError):
            calibrate_reference_population(flat_sep, min_separation_delta=0.3)

    def test_cli_exports_policy_on_success_and_never_writes_policy_on_fail_closed(self):
        manifest = _tiered_cohort_manifest()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "manifest.json"
            policy_path = root / "policy.json"
            report_path = root / "report.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/calibrate_reference_population.py",
                    "--input-manifest",
                    str(manifest_path),
                    "--output-policy",
                    str(policy_path),
                    "--report",
                    str(report_path),
                    "--min-separation-delta",
                    "0.4",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(policy_path.is_file())
            self.assertTrue(report_path.is_file())
            loaded_policy = json.loads(policy_path.read_text(encoding="utf-8"))
            self.assertTrue(verify_rubric_policy_signature(loaded_policy))
            self.assertFalse(loaded_policy["coach_calibrated"])

            # Now test fail-closed behavior with tiny manifest.
            small_manifest = root / "small.json"
            small_manifest.write_text(
                json.dumps({"schema_version": COHORT_MANIFEST_SCHEMA_VERSION, "entries": manifest["entries"][:2]}),
                encoding="utf-8",
            )
            bad_policy = root / "should_not_exist.json"
            bad_report = root / "fail_report.json"
            fail_proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/calibrate_reference_population.py",
                    "--input-manifest",
                    str(small_manifest),
                    "--output-policy",
                    str(bad_policy),
                    "--report",
                    str(bad_report),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(fail_proc.returncode, 1)
            self.assertFalse(bad_policy.exists())
            self.assertTrue(bad_report.is_file())
            fail_report_json = json.loads(bad_report.read_text(encoding="utf-8"))
            self.assertEqual(fail_report_json["status"], "failed_closed")
            self.assertFalse(fail_report_json["coach_calibrated"])


if __name__ == "__main__":
    unittest.main()
