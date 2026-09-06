from __future__ import annotations

import unittest
from pathlib import Path

from rallymate_service.config import ServiceSettings


ROOT = Path(__file__).resolve().parents[1]


class RegistryLifecycleIntegrationTests(unittest.TestCase):
    def test_deployment_image_contains_current_lifecycle_authority(self) -> None:
        dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
        for artifact in (
            "registry-lifecycle.json",
            "metric-feasibility-pose-wave-v2.json",
            "indicator-scoring-requirements-pose-wave-v1.json",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, dockerfile)
        self.assertNotIn("chown -R rallymate:rallymate /app\n", dockerfile)
        self.assertIn(
            "chown -R rallymate:rallymate /app/service_data /app/models",
            dockerfile,
        )
        compose = (ROOT / "deploy" / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("RALLYMATE_SCORING_REGISTRY_LIFECYCLE_MANIFEST", compose)
        self.assertIn("RALLYMATE_SCORING_FEASIBILITY_REGISTRY", compose)

    def test_service_default_is_manifest_role_not_a_second_hardcoded_registry(self) -> None:
        settings = ServiceSettings(
            data_root=ROOT / "service_data",
            database_path=ROOT / "service_data" / "jobs.sqlite3",
            detect_model=ROOT / "models" / "yolo26n.pt",
            pose_model=ROOT / "models" / "yolo26n-pose.pt",
        )
        authority = settings.resolved_scoring_registry_authority
        self.assertEqual(authority.role, "runtime_feasibility")
        self.assertEqual(authority.lifecycle, "current")
        self.assertEqual(authority.embedded_version, "pose-wave-2026-08-22.17")
        self.assertEqual(
            settings.resolved_scoring_feasibility_registry,
            ROOT / "metric-feasibility-pose-wave-v2.json",
        )

    def test_production_source_does_not_reference_the_historical_registry(self) -> None:
        offenders = []
        for path in (ROOT / "src").rglob("*.py"):
            if "metric-feasibility.json" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
