from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from rallymate_service.config import ServiceSettings


ROOT = Path(__file__).resolve().parents[1]


class RegistryLifecycleIntegrationTests(unittest.TestCase):
    def test_deployment_image_contains_current_lifecycle_authority(self) -> None:
        dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
        manifest = json.loads((ROOT / "registry-lifecycle.json").read_text(encoding="utf-8"))
        for artifact in (
            "registry-lifecycle.json",
            manifest["roles"]["runtime_feasibility"]["relative_path"],
            manifest["roles"]["current_scoring_requirements"]["relative_path"],
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

    def test_active_deployment_and_smoke_pins_match_current_lifecycle(self) -> None:
        manifest = json.loads((ROOT / "registry-lifecycle.json").read_text(encoding="utf-8"))
        relative = manifest["roles"]["runtime_feasibility"]["relative_path"]
        expected = "/app/" + relative
        variable = "RALLYMATE_SCORING_FEASIBILITY_REGISTRY"
        compose = (ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn(f"{variable}: ${{{variable}:-{expected}}}", compose)
        for filename in ("deploy/.env.example", "deploy/README.md"):
            content = (ROOT / filename).read_text(encoding="utf-8")
            values = re.findall(rf"^{variable}=([^\r\n]+)$", content, re.MULTILINE)
            with self.subTest(filename=filename):
                self.assertTrue(values)
                self.assertEqual(set(values), {expected})
        smoke = (ROOT / "scripts/run_pose_deployment_smoke.ps1").read_text(encoding="utf-8")
        self.assertIn(f'$Registry = Join-Path $Root "{relative}"', smoke)
        requests = re.findall(r'"examples\\([^"\r\n]+\.json)"', smoke)
        self.assertEqual(len(requests), 3)
        for filename in requests:
            path = ROOT / "examples" / filename
            request = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(smoke_request=filename):
                self.assertEqual((path.parent / request["scoring"]["feasibility_registry"]).resolve(),
                                 (ROOT / relative).resolve())

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
        self.assertEqual(authority.embedded_version, "pose-wave-2026-10-28.1")
        self.assertEqual(
            settings.resolved_scoring_feasibility_registry,
            ROOT / "metric-feasibility-pose-wave-v3.json",
        )

    def test_production_source_does_not_reference_the_historical_registry(self) -> None:
        offenders = []
        for path in (ROOT / "src").rglob("*.py"):
            if "metric-feasibility.json" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
