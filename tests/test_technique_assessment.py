from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from rallymate_scoring.technique_assessment import build_technique_assessment
from rallymate_scoring.technique_registry import TechniqueRegistryError, technique_catalog
from rallymate_service.api import create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase


class TechniqueAssessmentTests(unittest.TestCase):
    def test_catalog_contains_all_document_families_without_numeric_grade_policy(self) -> None:
        catalog = technique_catalog()
        self.assertEqual(catalog["schema_version"], "1.0.0")
        self.assertEqual(len(catalog["source_documents"]), 5)
        self.assertEqual(len(catalog["techniques"]), 24)
        self.assertEqual(
            {item["family"] for item in catalog["techniques"]},
            {"baseline", "serve", "return", "net_attack", "footwork"},
        )
        self.assertFalse(catalog["semantics"]["formal_coach_score"])
        self.assertTrue(
            all(
                isinstance(source.get("source_sha256"), str)
                and len(source["source_sha256"]) == 64
                for source in catalog["source_documents"]
            )
        )

    def test_invalid_source_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "technique_metrics.json"
            payload = json.loads(
                (Path(__file__).resolve().parents[1] / "src" / "rallymate_scoring" / "data" / "technique_metrics.json").read_text(encoding="utf-8")
            )
            payload["source_documents"][0]["source_sha256"] = "not-a-digest"
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(TechniqueRegistryError):
                technique_catalog(target)

    def test_assessment_matches_versioned_contract(self) -> None:
        result = build_technique_assessment(
            {
                "job_id": "schema-job",
                "coverage": {
                    "pose_frame_fraction": 0.8,
                    "player_frame_fraction": 0.8,
                },
                "minimum_scoring_loop": {"event_counts": {"FS01": 1}},
            }
        )
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "contracts"
                / "technique-assessment.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(result)))

    def test_assessment_maps_legacy_events_and_keeps_contact_proxy_explicit(self) -> None:
        summary = {
            "job_id": "job-1",
            "coverage": {
                "pose_frame_fraction": 0.9,
                "player_frame_fraction": 0.9,
                "ball_frame_fraction": 0.8,
                "racket_frame_fraction": 0.75,
                "court_calibrated_fraction": 0.2,
            },
            "minimum_scoring_loop": {"event_counts": {"FS01": 2}},
        }
        result = build_technique_assessment(summary)
        split_step = next(item for item in result["techniques"] if item["technique_id"] == "split_step")
        self.assertTrue(split_step["observed"])
        self.assertEqual(split_step["status"], "ready")
        self.assertEqual(split_step["evidence"]["contact_status"], "not_applicable")
        self.assertEqual(
            split_step["reference_constraints"], []
        )
        self.assertEqual(
            result["policy"]["contact"]["presence_rule"],
            "coverage_fraction > 0; no confidence threshold and no minimum-frame cutoff",
        )
        self.assertIsNone(split_step["score_0_to_100"])
        self.assertFalse(result["formal_score_available"])

    def test_contact_status_uses_presence_only_and_primary_player_coverage(self) -> None:
        summary = {
            "job_id": "contact-policy",
            "coverage": {
                "pose_frame_fraction": 0.99,
                "player_frame_fraction": 0.99,
                "ball_frame_fraction": 0.0,
                "racket_frame_fraction": 0.25,
            },
            "primary_player": {
                "pose_coverage_fraction": 0.61,
                "track_coverage_fraction": 0.57,
            },
            "minimum_scoring_loop": {"event_counts": {"GS01": 1}},
        }
        result = build_technique_assessment(summary)
        forehand = next(
            item for item in result["techniques"] if item["technique_id"] == "baseline_forehand"
        )
        self.assertEqual(forehand["evidence"]["contact_status"], "impact_window_only")
        self.assertEqual(result["coverage"]["pose"], 61)
        self.assertEqual(result["coverage"]["tracking"], 57)
        self.assertEqual(
            result["coverage_detail"]["pose"]["source"],
            "summary.primary_player.pose_coverage_fraction",
        )
        self.assertEqual(
            result["coverage_detail"]["tracking"]["source"],
            "summary.primary_player.track_coverage_fraction",
        )
        self.assertFalse(result["coverage_detail"]["pose"]["fallback_used"])

    def test_missing_events_are_not_imputed(self) -> None:
        result = build_technique_assessment(
            {
                "job_id": "job-2",
                "coverage": {
                    "pose_frame_fraction": 1.0,
                    "player_frame_fraction": 1.0,
                    "ball_frame_fraction": 1.0,
                    "racket_frame_fraction": 1.0,
                },
            }
        )
        serve = next(item for item in result["techniques"] if item["technique_id"] == "serve")
        self.assertFalse(serve["observed"])
        self.assertEqual(serve["status"], "not_observed")
        self.assertEqual(serve["evidence_score_0_to_100"], 0)
        self.assertIsNone(serve["score_0_to_100"])

    def test_api_discovery_and_assessment_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            detect = root / "detect.pt"
            pose = root / "pose.pt"
            detect.write_bytes(b"test")
            pose.write_bytes(b"test")
            settings = ServiceSettings(
                data_root=root / "service",
                database_path=root / "service" / "jobs.sqlite3",
                detect_model=detect,
                pose_model=pose,
            )
            settings.ensure_directories()
            db = JobDatabase(settings.database_path)
            app = create_app(settings, db)
            db.initialize()
            job_id = "22222222-2222-4222-8222-222222222222"
            output_dir = settings.runs_dir / job_id
            output_dir.mkdir(parents=True)
            db.create_job(
                job_id,
                "clip.mp4",
                settings.uploads_dir / "clip.mp4",
                settings.requests_dir / f"{job_id}.json",
                output_dir,
            )
            claimed = db.claim_next("test", settings.lease_seconds, settings.max_attempts)
            self.assertEqual(claimed["id"], job_id)
            summary = {
                "status": "completed",
                "job_id": job_id,
                "coverage": {"pose_frame_fraction": 0.8, "player_frame_fraction": 0.8},
                "minimum_scoring_loop": {"event_counts": {"FS01": 1}},
            }
            (output_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            (output_dir / "indicator-features.jsonl").write_text("", encoding="utf-8")
            db.mark_succeeded(job_id, summary)
            with TestClient(app) as client:
                self.assertEqual(client.get("/v1/meta").status_code, 200)
                catalog_response = client.get("/v1/techniques")
                self.assertEqual(catalog_response.status_code, 200)
                self.assertEqual(len(catalog_response.json()["techniques"]), 24)
                assessment = client.get(f"/v1/jobs/{job_id}/technique-assessment")
                self.assertEqual(assessment.status_code, 200, assessment.text)
                self.assertEqual(assessment.json()["job_id"], job_id)
                self.assertFalse(assessment.json()["formal_score_available"])


if __name__ == "__main__":
    unittest.main()
