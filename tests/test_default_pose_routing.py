from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from rallymate_evaluation.default_pose_routing import (
    DefaultPoseRoutingError,
    build_default_pose_routing_audit,
    validate_default_pose_routing_audit,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_MODEL = "rtmpose-m-halpe26-256x192"
EXPERIMENTAL_MODEL = "rtmpose-m-halpe26-256x192-small-roi-min8-experimental"


def _read(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _sources() -> dict[str, dict[str, str]]:
    return {
        name: {"path": name, "sha256": "A" * 64}
        for name in (
            "deployment_registry",
            "feasibility_registry",
            "worker_smoke",
            "full_video_comparison",
            "full_video_scoring_summary",
            "residual_computability",
        )
    }


class DefaultPoseRoutingAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = {
            "deployment_registry": _read("models/rtmpose/deployment-presets.json"),
            "feasibility_registry": _read("metric-feasibility-pose-wave-v2.json"),
            "worker_smoke": _read(
                "runs/rtmpose-m-halpe26-default-m37-smoke/deployment-smoke.json"
            ),
            "full_video_comparison": _read(
                "reports/experiments/small-roi-pose-recovery-halpe256-full-v1/fixed-boundary-comparison.json"
            ),
            "full_video_scoring_summary": _read(
                "reports/fs09-pose-wave-v2/850cb0006b406c7176eeda8d711cd065/scoring-loop-summary.json"
            ),
            "residual_computability": _read(
                "reports/residual-indicator-computability-small-roi-v1.json"
            ),
        }

    def _build(self) -> dict:
        return build_default_pose_routing_audit(
            **self.inputs,
            production_model_key=PRODUCTION_MODEL,
            experimental_model_key=EXPERIMENTAL_MODEL,
            sources=_sources(),
        )

    def test_real_default_worker_and_full_video_cover_all_13(self) -> None:
        report = self._build()
        self.assertEqual("deployment_registry_default", report["real_worker_default_smoke"]["preset_source"])
        self.assertEqual("rtmpose-m-halpe26-online", report["deployment_default"]["preset_id"])
        self.assertEqual(13, report["full_video_production_measurement"]["indicator_with_measured_instance_count"])
        self.assertEqual(403, report["full_video_production_measurement"]["measured_indicator_event_instance_count"])
        self.assertEqual(442, report["full_video_production_measurement"]["indicator_event_instance_count"])
        self.assertEqual({}, report["full_video_production_measurement"]["grade_counts"])

    def test_rejects_yolo_default_or_explicit_smoke_override(self) -> None:
        inputs = deepcopy(self.inputs)
        inputs["deployment_registry"]["default_preset"] = "yolo-baseline"
        with self.assertRaisesRegex(DefaultPoseRoutingError, "not the deployment default"):
            build_default_pose_routing_audit(
                **inputs,
                production_model_key=PRODUCTION_MODEL,
                experimental_model_key=EXPERIMENTAL_MODEL,
                sources=_sources(),
            )
        inputs = deepcopy(self.inputs)
        inputs["worker_smoke"]["preset_source"] = "explicit_cli_override"
        with self.assertRaisesRegex(DefaultPoseRoutingError, "registry default"):
            build_default_pose_routing_audit(
                **inputs,
                production_model_key=PRODUCTION_MODEL,
                experimental_model_key=EXPERIMENTAL_MODEL,
                sources=_sources(),
            )

    def test_rejects_weight_drift_or_missing_indicator_coverage(self) -> None:
        inputs = deepcopy(self.inputs)
        inputs["worker_smoke"]["pose_backend"]["model_sha256"] = "B" * 64
        with self.assertRaisesRegex(DefaultPoseRoutingError, "different weights"):
            build_default_pose_routing_audit(
                **inputs,
                production_model_key=PRODUCTION_MODEL,
                experimental_model_key=EXPERIMENTAL_MODEL,
                sources=_sources(),
            )
        report = self._build()
        tampered = deepcopy(report)
        tampered["full_video_production_measurement"]["per_indicator"][0][
            "has_real_measured_instance"
        ] = False
        with self.assertRaisesRegex(DefaultPoseRoutingError, "coverage is incomplete"):
            validate_default_pose_routing_audit(tampered)
        tampered = deepcopy(report)
        tampered["real_worker_default_smoke"]["model_sha256"] = "B" * 64
        with self.assertRaisesRegex(DefaultPoseRoutingError, "provenance differ"):
            validate_default_pose_routing_audit(tampered)

    def test_real_audit_file_validates(self) -> None:
        path = ROOT / "reports" / "default-pose-routing-audit-m37.json"
        if not path.exists():
            self.skipTest("real audit is generated after the builder unit tests")
        report = json.loads(path.read_text(encoding="utf-8"))
        validate_default_pose_routing_audit(report)


if __name__ == "__main__":
    unittest.main()
